"""
PBIP TMDL Injector - ThoughtSpot -> Power BI
===========================================
This file writes Power BI-friendly TMDL table files into:
    <ProjectName>.SemanticModel/definition/tables/

Important fixes:
- Never creates table named "Measures" because Power BI rejects it in some PBIP schemas.
- Uses safe measure table name: "DAX Measures".
- Removes old invalid Measures.tmdl if it exists.
- Deduplicates tables, columns, and measures.
- Generates DATATABLE calculated partitions so tables can open inside Power BI Desktop.
- Adds table references to definition/model.tmdl idempotently.
- Adds relationship definitions when relationships are provided.
- Keeps names readable but safe for TMDL/DAX.
- Fixes raw ThoughtSpot formula fields like revenue/cost into valid Power BI DAX:
      revenue - cost
  becomes:
      SUM('Table'[revenue]) - SUM('Table'[cost])
"""

from __future__ import annotations

import math
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from loguru import logger

try:
    import pandas as pd
    _PANDAS_AVAILABLE = True
except ImportError:  # pragma: no cover
    pd = None
    _PANDAS_AVAILABLE = False


MEASURE_TABLE_NAME = "DAX Measures"
INVALID_MEASURE_TABLE_NAMES = {"Measures", "measures"}


# -----------------------------------------------------------------------------
# Name helpers
# -----------------------------------------------------------------------------

def _clean_name(name: Any, fallback: str = "Field") -> str:
    value = str(name or "").strip()
    value = re.sub(r'["`]', "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or fallback


def _safe_filename(name: Any) -> str:
    safe = re.sub(r'[<>:"/\\|?*]', "_", _clean_name(name, "Table"))
    safe = safe.strip(" .")
    return safe or "Table"


def _escape_tmdl_quoted(value: Any) -> str:
    return str(value).replace("'", "''")


def _tmdl_identifier(name: Any, fallback: str = "Field") -> str:
    return f"'{_escape_tmdl_quoted(_clean_name(name, fallback))}'"


def _escape_dax_string(value: Any) -> str:
    return str(value).replace('"', '""')


def _escape_dax_single_quoted(value: Any) -> str:
    return str(value).replace("'", "''")


def _dax_column_ref(table: str, column: str) -> str:
    return f"'{_escape_dax_single_quoted(table)}'[{_clean_name(column, 'Column')}]"


def _dax_sum_ref(table: str, column: str) -> str:
    return f"SUM({_dax_column_ref(table, column)})"


def _dedupe_names(names: Iterable[Any], fallback: str = "Column") -> List[str]:
    seen: Dict[str, int] = {}
    result: List[str] = []

    for raw in names:
        base = _clean_name(raw, fallback)
        key = base.lower()
        seen[key] = seen.get(key, 0) + 1
        result.append(base if seen[key] == 1 else f"{base}_{seen[key]}")

    return result


def _clean_table_name(name: Any, index: int = 1) -> str:
    table = _clean_name(name, f"Table_{index}")
    if table in INVALID_MEASURE_TABLE_NAMES:
        table = f"{table} Table"
    return table


def _clean_measure_name(name: Any, fallback: str = "Measure") -> str:
    return _clean_name(name, fallback)


# -----------------------------------------------------------------------------
# Pandas / DAX helpers
# -----------------------------------------------------------------------------

def _clean_dataframe(df: Optional["pd.DataFrame"]) -> Optional["pd.DataFrame"]:
    if df is None or not _PANDAS_AVAILABLE:
        return df

    clean_df = df.copy()
    clean_df.columns = _dedupe_names([str(c) for c in clean_df.columns], "Column")

    # Keep generated PBIP small and stable. The UI can still report real table names.
    max_rows = 50
    if len(clean_df) > max_rows:
        clean_df = clean_df.head(max_rows)

    return clean_df


def _get_tmdl_datatype(dtype: Any) -> str:
    if not _PANDAS_AVAILABLE:
        return "string"
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "int64"
    if pd.api.types.is_float_dtype(dtype):
        return "double"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "dateTime"
    return "string"


def _dax_datatable_type(tmdl_type: str) -> str:
    return {
        "boolean": "BOOLEAN",
        "int64": "INTEGER",
        "double": "DOUBLE",
        "dateTime": "DATETIME",
        "string": "STRING",
    }.get(tmdl_type, "STRING")


def _format_cell_value(value: Any) -> str:
    if value is None:
        return "BLANK()"

    if _PANDAS_AVAILABLE:
        try:
            if pd.isna(value):
                return "BLANK()"
        except Exception:
            pass

    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"

    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "BLANK()"
        return str(int(value)) if value.is_integer() else repr(value)

    if _PANDAS_AVAILABLE and hasattr(value, "strftime"):
        try:
            return f"DATE({value.year}, {value.month}, {value.day})"
        except Exception:
            pass

    return f'"{_escape_dax_string(value)}"'


def _build_datatable_dax(df: Optional["pd.DataFrame"]) -> str:
    if not _PANDAS_AVAILABLE:
        return '\t\t\tDATATABLE ( "_dummy", STRING, { { "" } } )'

    if df is None or len(df.columns) == 0:
        return '\t\t\tDATATABLE ( "_dummy", STRING, { { "" } } )'

    header_parts: List[str] = []
    for col in df.columns:
        tmdl_type = _get_tmdl_datatype(df[col].dtype)
        header_parts.append(f'"{_escape_dax_string(col)}", {_dax_datatable_type(tmdl_type)}')

    header = ",\n\t\t\t\t".join(header_parts)

    row_lines: List[str] = []
    for _, row in df.iterrows():
        values = [_format_cell_value(row[col]) for col in df.columns]
        row_lines.append("\t\t\t\t{ " + ", ".join(values) + " }")

    rows = "{}" if not row_lines else "{\n" + ",\n".join(row_lines) + "\n\t\t\t\t}"

    return (
        "\t\t\tDATATABLE (\n"
        f"\t\t\t\t{header},\n"
        f"\t\t\t\t{rows}\n"
        "\t\t\t)"
    )


def _mask_dax_protected_parts(expr: str) -> Tuple[str, Dict[str, str]]:
    """
    Temporarily masks existing DAX references and strings so raw-column replacement
    does not corrupt:
        'Table'[Column]
        [Measure]
        "Text"
    """
    protected: Dict[str, str] = {}
    masked = str(expr or "")

    patterns = [
        r"'(?:[^']|'')*'\s*\[[^\]]+\]",  # 'Table'[Column]
        r"\[[^\]]+\]",                   # [Measure] or [Column]
        r'"(?:[^"]|"")*"',               # "string"
    ]

    counter = 0
    for pattern in patterns:
        while True:
            match = re.search(pattern, masked)
            if not match:
                break
            key = f"__DAX_PROTECTED_{counter}__"
            protected[key] = match.group(0)
            masked = masked[:match.start()] + key + masked[match.end():]
            counter += 1

    return masked, protected


def _unmask_dax_protected_parts(expr: str, protected: Dict[str, str]) -> str:
    for key, value in protected.items():
        expr = expr.replace(key, value)
    return expr


def _replace_raw_columns_with_sum(
    expr: str,
    default_table: Optional[str],
    default_columns: Optional[List[str]] = None,
) -> str:
    """
    Converts raw field names into valid Power BI DAX.

    Example:
        DIVIDE((revenue - cost), revenue, 0)

    Becomes:
        DIVIDE((SUM('Sales'[revenue]) - SUM('Sales'[cost])), SUM('Sales'[revenue]), 0)
    """
    if not default_table or not default_columns:
        return expr

    table = _clean_name(default_table, "Table")
    masked, protected = _mask_dax_protected_parts(expr)

    # Longest first prevents partial replacements, e.g. sales and sales_amount.
    columns = sorted(
        [_clean_name(c, "Column") for c in default_columns if str(c).strip()],
        key=len,
        reverse=True,
    )

    dax_reserved_words = {
        "SUM", "AVERAGE", "MIN", "MAX", "COUNT", "DISTINCTCOUNT",
        "DIVIDE", "IF", "SWITCH", "CALCULATE", "FILTER", "ALL", "ALLEXCEPT",
        "BLANK", "TRUE", "FALSE", "DATE", "YEAR", "MONTH", "DAY",
        "AND", "OR", "NOT", "ROUND", "ROUNDUP", "ROUNDDOWN", "ABS",
        "VAR", "RETURN", "IN", "VALUE", "FORMAT", "CONTAINSSTRING",
    }

    for col in columns:
        if not col or col.upper() in dax_reserved_words:
            continue

        # Replace exact raw column token only.
        # Handles normal field names like revenue, cost, sales_amount.
        pattern = rf"(?<![\w\]])\b{re.escape(col)}\b(?![\w\[]|\s*\])"
        replacement = _dax_sum_ref(table, col)
        masked = re.sub(pattern, replacement, masked, flags=re.IGNORECASE)

    return _unmask_dax_protected_parts(masked, protected)


def _normalize_dax_table_references(
    dax: str,
    default_table: Optional[str] = None,
    default_columns: Optional[List[str]] = None,
) -> str:
    """Cleanup common generated DAX patterns and fix raw column references."""
    expr = str(dax or "0").strip() or "0"

    if default_table:
        table = _clean_name(default_table, "Table")

        # Convert SUM([Sales]) -> SUM('orders_fact'[Sales])
        expr = re.sub(
            r"\b(SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT)\s*\(\s*\[([^\]]+)\]\s*\)",
            lambda m: f"{m.group(1).upper()}({_dax_column_ref(table, m.group(2))})",
            expr,
            flags=re.IGNORECASE,
        )

        # Convert raw revenue/cost/etc fields into SUM('Table'[field]).
        expr = _replace_raw_columns_with_sum(expr, table, default_columns)

    # Safer division: A / B -> DIVIDE(A, B, 0), only if not already DIVIDE.
    simple_div = re.match(r"^(.+?)\s*/\s*(.+?)$", expr, flags=re.DOTALL)
    if simple_div and "DIVIDE" not in expr.upper():
        left = simple_div.group(1).strip()
        right = simple_div.group(2).strip()
        if left and right:
            expr = f"DIVIDE({left}, {right}, 0)"

    return expr


# -----------------------------------------------------------------------------
# TMDL builders
# -----------------------------------------------------------------------------

def _build_table_tmdl(table_name: str, df: Optional["pd.DataFrame"]) -> str:
    table_name = _clean_name(table_name, "Table")
    df = _clean_dataframe(df)

    lines: List[str] = []
    lines.append(f"table {_tmdl_identifier(table_name, 'Table')}")
    lines.append(f"\tlineageTag: {uuid.uuid4()}")
    lines.append("")

    if df is not None and _PANDAS_AVAILABLE:
        for col in df.columns:
            col_name = _clean_name(col, "Column")
            dtype = _get_tmdl_datatype(df[col].dtype)
            summarize_by = "none" if dtype in {"string", "dateTime", "boolean"} else "sum"
            lines.append(f"\tcolumn {_tmdl_identifier(col_name, 'Column')}")
            lines.append(f"\t\tdataType: {dtype}")
            lines.append(f"\t\tlineageTag: {uuid.uuid4()}")
            lines.append(f"\t\tsummarizeBy: {summarize_by}")
            lines.append(f"\t\tsourceColumn: {_clean_name(col_name, 'Column')}")
            lines.append("")

    lines.append(f"\tpartition {_tmdl_identifier(table_name, 'Table')} = calculated")
    lines.append("\t\tmode: import")
    lines.append("\t\texpression =")
    lines.append(_build_datatable_dax(df))
    lines.append("")
    return "\n".join(lines)


def _build_measure_table_tmdl(
    measures: List[Dict[str, Any]],
    default_table: Optional[str],
    default_columns: Optional[List[str]] = None,
) -> str:
    lines: List[str] = []
    lines.append(f"table {_tmdl_identifier(MEASURE_TABLE_NAME, 'Table')}")
    lines.append(f"\tlineageTag: {uuid.uuid4()}")
    lines.append("")

    lines.append("\tcolumn '_MeasureTableDummy'")
    lines.append("\t\tdataType: string")
    lines.append(f"\t\tlineageTag: {uuid.uuid4()}")
    lines.append("\t\tsummarizeBy: none")
    lines.append("\t\tsourceColumn: _MeasureTableDummy")
    lines.append("\t\tisHidden")
    lines.append("")

    seen = set()
    for raw in measures or []:
        name = _clean_measure_name(
            raw.get("name") or raw.get("measure") or raw.get("calculated_field"),
            "Measure",
        )
        dax = str(raw.get("dax") or raw.get("expression") or raw.get("dax_formula") or "0").strip() or "0"
        dax = _normalize_dax_table_references(dax, default_table, default_columns)
        fmt = str(raw.get("formatString") or raw.get("format_string") or "#,##0.00")

        key = name.lower()
        if key in seen:
            logger.warning(f"Skipping duplicate measure: {name}")
            continue
        seen.add(key)

        expr_lines = dax.splitlines()
        if len(expr_lines) == 1:
            lines.append(f"\tmeasure {_tmdl_identifier(name, 'Measure')} = {expr_lines[0]}")
        else:
            lines.append(f"\tmeasure {_tmdl_identifier(name, 'Measure')} =")
            for expr_line in expr_lines:
                lines.append(f"\t\t\t{expr_line}")
        lines.append(f"\t\tformatString: {fmt}")
        lines.append(f"\t\tlineageTag: {uuid.uuid4()}")
        lines.append("")

    lines.append(f"\tpartition {_tmdl_identifier(MEASURE_TABLE_NAME, 'Table')} = calculated")
    lines.append("\t\tmode: import")
    lines.append("\t\texpression =")
    lines.append('\t\t\tDATATABLE ( "_MeasureTableDummy", STRING, { { "" } } )')
    lines.append("")
    return "\n".join(lines)


def _relationship_name(rel: Dict[str, Any]) -> str:
    return _clean_name(
        rel.get("relationship_id")
        or rel.get("id")
        or f"rel_{rel.get('source_table', 'source')}_{rel.get('target_table', 'target')}",
        "Relationship",
    )


def _build_relationship_tmdl(rel: Dict[str, Any]) -> Optional[str]:
    source_table = _clean_name(rel.get("source_table") or rel.get("from_table"), "")
    target_table = _clean_name(rel.get("target_table") or rel.get("to_table"), "")
    source_column = _clean_name(rel.get("source_column") or rel.get("from_column"), "")
    target_column = _clean_name(rel.get("target_column") or rel.get("to_column"), "")

    if not all([source_table, target_table, source_column, target_column]):
        return None

    name = _relationship_name(rel)
    return (
        f"relationship {_tmdl_identifier(name, 'Relationship')}\n"
        f"\tfromColumn: {_tmdl_identifier(source_table, 'Table')}.{_tmdl_identifier(source_column, 'Column')}\n"
        f"\ttoColumn: {_tmdl_identifier(target_table, 'Table')}.{_tmdl_identifier(target_column, 'Column')}\n"
        "\tcrossFilteringBehavior: oneDirection\n"
        "\tisActive: true\n"
    )


# -----------------------------------------------------------------------------
# Main injector
# -----------------------------------------------------------------------------

class PBIPTmdlInjector:
    """
    Injects tables, measures, and optional relationships into a PBIP SemanticModel.

    Expected folder:
        <ProjectName>.SemanticModel/definition/model.tmdl
        <ProjectName>.SemanticModel/definition/tables/
    """

    def inject(
        self,
        sm_folder: Path,
        tables: Optional[Dict[str, "pd.DataFrame"]] = None,
        measures: Optional[List[Dict[str, Any]]] = None,
        relationships: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        sm_folder = Path(sm_folder)
        definition_dir = sm_folder / "definition"
        tables_dir = definition_dir / "tables"
        tables_dir.mkdir(parents=True, exist_ok=True)
        definition_dir.mkdir(parents=True, exist_ok=True)

        injected: List[str] = []

        self._remove_invalid_old_measure_files(tables_dir)

        clean_tables = self._prepare_tables(tables or {})
        default_table = self._choose_default_fact_table(clean_tables)
        default_columns = self._get_columns_for_table(clean_tables, default_table)

        for table_name, df in clean_tables.items():
            try:
                content = _build_table_tmdl(table_name, df)
                path = tables_dir / f"{_safe_filename(table_name)}.tmdl"
                path.write_text(content, encoding="utf-8")
                injected.append(table_name)
                row_count = len(df) if df is not None and hasattr(df, "__len__") else 0
                logger.info(f"✓ Wrote table TMDL: {path.name} ({row_count} sample rows)")
            except Exception as exc:
                logger.exception(f"✗ Failed to write table TMDL for {table_name}: {exc}")

        valid_measures = self._prepare_measures(measures or [])
        if valid_measures:
            measure_path = tables_dir / f"{_safe_filename(MEASURE_TABLE_NAME)}.tmdl"
            measure_path.write_text(
                _build_measure_table_tmdl(valid_measures, default_table, default_columns),
                encoding="utf-8",
            )
            injected.append(MEASURE_TABLE_NAME)
            logger.info(f"✓ Wrote measure table: {measure_path.name} ({len(valid_measures)} measures)")
        else:
            logger.warning("No valid measures provided; DAX Measures table skipped")

        self._write_relationships(definition_dir, relationships or [])
        self._update_model_tmdl(sm_folder, injected, relationships or [])
        return injected

    def _prepare_tables(self, tables: Dict[str, "pd.DataFrame"]) -> Dict[str, "pd.DataFrame"]:
        result: Dict[str, "pd.DataFrame"] = {}
        used = set()

        for idx, (raw_name, raw_df) in enumerate(tables.items(), start=1):
            base = _clean_table_name(raw_name, idx)
            name = base
            suffix = 2
            while name.lower() in used:
                name = f"{base}_{suffix}"
                suffix += 1
            used.add(name.lower())
            result[name] = _clean_dataframe(raw_df)

        return result

    def _prepare_measures(self, measures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        seen = set()

        for raw in measures or []:
            name = _clean_measure_name(
                raw.get("name") or raw.get("measure") or raw.get("calculated_field"),
                "",
            )
            dax = str(raw.get("dax") or raw.get("expression") or raw.get("dax_formula") or "").strip()

            if not name or not dax:
                continue

            key = name.lower()
            if key in seen:
                continue
            seen.add(key)

            fixed = dict(raw)
            fixed["name"] = name
            fixed["dax"] = dax
            result.append(fixed)

        return result

    def _choose_default_fact_table(self, tables: Dict[str, "pd.DataFrame"]) -> Optional[str]:
        if not tables:
            return None

        for name in tables:
            low = name.lower()
            if any(token in low for token in ["fact", "orders", "sales", "transaction"]):
                return name

        return next(iter(tables.keys()))

    def _get_columns_for_table(
        self,
        tables: Dict[str, "pd.DataFrame"],
        table_name: Optional[str],
    ) -> List[str]:
        if not table_name or table_name not in tables:
            return []

        df = tables.get(table_name)
        if df is None or not hasattr(df, "columns"):
            return []

        return [str(c) for c in df.columns]

    def _remove_invalid_old_measure_files(self, tables_dir: Path) -> None:
        for invalid_name in INVALID_MEASURE_TABLE_NAMES:
            path = tables_dir / f"{invalid_name}.tmdl"
            if path.exists():
                path.unlink()
                logger.info(f"Removed invalid old measure table file: {path.name}")

    def _write_relationships(self, definition_dir: Path, relationships: List[Dict[str, Any]]) -> None:
        rel_blocks: List[str] = []
        seen = set()

        for rel in relationships or []:
            source_table = _clean_name(rel.get("source_table") or rel.get("from_table"), "")
            target_table = _clean_name(rel.get("target_table") or rel.get("to_table"), "")
            source_column = _clean_name(rel.get("source_column") or rel.get("from_column"), "")
            target_column = _clean_name(rel.get("target_column") or rel.get("to_column"), "")
            key = (source_table.lower(), source_column.lower(), target_table.lower(), target_column.lower())
            if not all(key) or key in seen:
                continue
            seen.add(key)

            block = _build_relationship_tmdl(rel)
            if block:
                rel_blocks.append(block)

        if not rel_blocks:
            return

        rel_path = definition_dir / "relationships.tmdl"
        rel_path.write_text("\n".join(rel_blocks).strip() + "\n", encoding="utf-8")
        logger.info(f"✓ Wrote relationships.tmdl ({len(rel_blocks)} relationships)")

    def _update_model_tmdl(
        self,
        sm_folder: Path,
        table_names: List[str],
        relationships: List[Dict[str, Any]],
    ) -> None:
        model_tmdl = Path(sm_folder) / "definition" / "model.tmdl"
        model_tmdl.parent.mkdir(parents=True, exist_ok=True)

        if model_tmdl.exists():
            content = model_tmdl.read_text(encoding="utf-8")
        else:
            content = "model Model\n\tculture: en-US\n"

        refs_to_add: List[str] = []

        for name in table_names:
            ref = f"ref table {_tmdl_identifier(name, 'Table')}"
            if ref not in content and f"ref table {name}" not in content:
                refs_to_add.append(ref)

        if relationships:
            rel_ref = "ref relationships"
            if rel_ref not in content:
                refs_to_add.append(rel_ref)

        if refs_to_add:
            content = content.rstrip() + "\n\n" + "\n".join(refs_to_add) + "\n"
            model_tmdl.write_text(content, encoding="utf-8")
            logger.info(f"✓ Updated model.tmdl with {len(refs_to_add)} references")
        else:
            model_tmdl.write_text(content, encoding="utf-8")
            logger.info("✓ model.tmdl already contains all required references")
