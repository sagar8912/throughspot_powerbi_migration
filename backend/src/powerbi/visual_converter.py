"""
visual_converter.py
Power BI visual/report helper for ThoughtSpot -> Power BI migration.

Use this file as: src/powerbi/visual_converter.py

Important fixes:
- Uses the same measure table name as pbip_tmdl_injector.py: "DAX Measures".
- Does not change table names like "DAX Measures" into "DAX_Measures".
- Does not change column names like order_id into "order id".
- Generates safer legacy Power BI report visualContainers for PBIP/PBIX exports.
- Adds fallback visuals when worksheet metadata is missing.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from loguru import logger
except Exception:  # pragma: no cover
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)


MEASURE_TABLE_NAME = "DAX Measures"


class PowerBIVisualType(str, Enum):
    CARD = "card"
    TABLE = "tableEx"
    MATRIX = "pivotTable"
    CLUSTERED_BAR_CHART = "clusteredBarChart"
    CLUSTERED_COLUMN_CHART = "clusteredColumnChart"
    STACKED_BAR_CHART = "barChart"
    STACKED_COLUMN_CHART = "columnChart"
    LINE_CHART = "lineChart"
    AREA_CHART = "areaChart"
    PIE_CHART = "pieChart"
    DONUT_CHART = "donutChart"
    SCATTER_CHART = "scatterChart"
    MAP = "map"
    SLICER = "slicer"


@dataclass
class VisualLayout:
    x: float
    y: float
    width: float
    height: float
    z_index: int = 0


@dataclass
class PowerBIFieldRef:
    table: str
    column: str
    aggregation: Optional[str] = None
    is_measure: bool = False


@dataclass
class PowerBIVisual:
    visual_type: PowerBIVisualType
    name: str
    title: str
    layout: VisualLayout
    data_roles: Dict[str, List[PowerBIFieldRef]]
    filters: List[Dict[str, Any]] = field(default_factory=list)


class VisualConverter:
    CANVAS_WIDTH = 1280
    CANVAS_HEIGHT = 720
    DEFAULT_VISUAL_WIDTH = 390
    DEFAULT_VISUAL_HEIGHT = 245

    def __init__(
        self,
        tables: Optional[List[Dict[str, Any]]] = None,
        calculated_fields: Optional[List[Dict[str, Any]]] = None,
    ):
        self.tables = tables or []
        self.calculated_fields = calculated_fields or []
        self._field_to_table = self._build_field_to_table_index(self.tables)
        self._measure_table_name = MEASURE_TABLE_NAME

    # ----------------------------- public API -----------------------------

    def convert_worksheets_to_visuals(
        self,
        worksheets: List[Dict[str, Any]],
        auto_layout: bool = True,
    ) -> List[PowerBIVisual]:
        logger.info(f"Converting {len(worksheets or [])} worksheets to Power BI visuals")
        visuals: List[PowerBIVisual] = []
        seen_names: set[str] = set()

        for worksheet in worksheets or []:
            try:
                visual = self._convert_single_worksheet(worksheet or {})
                if not visual:
                    continue
                visual.name = self._unique_name(self._safe_visual_name(visual.name), seen_names)
                if auto_layout:
                    visual.layout = self._calculate_auto_layout(len(visuals), max(len(worksheets), 1))
                visuals.append(visual)
            except Exception as exc:
                logger.warning(f"Failed to convert worksheet {worksheet.get('name', 'Unknown')}: {exc}")

        if not visuals:
            visuals = self.build_default_visuals()
            for i, visual in enumerate(visuals):
                visual.layout = self._calculate_auto_layout(i, len(visuals))

        logger.info(f"Converted {len(visuals)} visuals")
        return visuals

    def build_default_visuals(self) -> List[PowerBIVisual]:
        """
        Creates useful default visuals from the available semantic model.
        This prevents a completely blank report when source worksheet metadata is missing.
        """
        visuals: List[PowerBIVisual] = []

        measure_refs = self._measure_refs_from_calculated_fields()
        dimension_refs = self._dimension_refs_from_tables()
        numeric_refs = self._numeric_refs_from_tables()

        # KPI cards from DAX measures.
        for idx, measure in enumerate(measure_refs[:4]):
            visuals.append(
                PowerBIVisual(
                    visual_type=PowerBIVisualType.CARD,
                    name=f"kpi_{idx + 1}_{measure.column}",
                    title=measure.column,
                    layout=VisualLayout(0, 0, self.DEFAULT_VISUAL_WIDTH, self.DEFAULT_VISUAL_HEIGHT),
                    data_roles={"Values": [measure]},
                )
            )

        # Bar/column chart with first dimension and measure/numeric value.
        if dimension_refs and (measure_refs or numeric_refs):
            visuals.append(
                PowerBIVisual(
                    visual_type=PowerBIVisualType.CLUSTERED_COLUMN_CHART,
                    name="summary_chart",
                    title="Summary",
                    layout=VisualLayout(0, 0, self.DEFAULT_VISUAL_WIDTH, self.DEFAULT_VISUAL_HEIGHT),
                    data_roles={
                        "Category": [dimension_refs[0]],
                        "Y": (measure_refs or numeric_refs)[:3],
                    },
                )
            )

        # Data table visual.
        table_values = (dimension_refs[:4] + (measure_refs or numeric_refs)[:4])[:8]
        if table_values:
            visuals.append(
                PowerBIVisual(
                    visual_type=PowerBIVisualType.TABLE,
                    name="data_preview",
                    title="Data Preview",
                    layout=VisualLayout(0, 0, self.DEFAULT_VISUAL_WIDTH * 2, self.DEFAULT_VISUAL_HEIGHT),
                    data_roles={"Values": table_values},
                )
            )

        return visuals

    def generate_visual_json(self, visual: PowerBIVisual) -> Dict[str, Any]:
        visual_name = self._safe_visual_name(visual.name)[:60] or f"visual_{uuid.uuid4().hex[:8]}"
        prototype_query, projections = self._build_prototype_query(visual.data_roles)

        position = {
            "x": visual.layout.x,
            "y": visual.layout.y,
            "z": visual.layout.z_index,
            "width": visual.layout.width,
            "height": visual.layout.height,
            "tabOrder": visual.layout.z_index,
        }

        single_visual = {
            "visualType": visual.visual_type.value,
            "projections": projections,
            "prototypeQuery": prototype_query,
            "drillFilterOtherVisuals": True,
            "objects": {
                "title": [
                    {
                        "properties": {
                            "show": {"expr": {"Literal": {"Value": "true"}}},
                            "text": {"expr": {"Literal": {"Value": json.dumps(visual.title)}}},
                        },
                        "selector": None,
                    }
                ]
            },
        }

        config = {
            "name": visual_name,
            "layouts": [{"id": 0, "position": position}],
            "singleVisual": single_visual,
        }

        return {
            "name": visual_name,
            "layouts": [{"id": 0, "position": position}],
            "singleVisual": single_visual,
            "filters": "[]",
            "config": json.dumps(config, ensure_ascii=False),
        }

    def generate_page_json(self, page_name: str, visuals: List[PowerBIVisual]) -> Dict[str, Any]:
        page_id = self._safe_visual_name(page_name or "ReportSection")
        containers = [self.generate_visual_json(v) for v in visuals]
        return {
            "name": page_id,
            "displayName": page_name or "Page 1",
            "displayOption": 1,
            "height": self.CANVAS_HEIGHT,
            "width": self.CANVAS_WIDTH,
            "ordinal": 0,
            "filters": "[]",
            "visualContainers": containers,
            "config": json.dumps({"objects": {}}, ensure_ascii=False),
        }

    def generate_report_json(self, pages: List[Dict[str, Any]] | List[PowerBIVisual]) -> Dict[str, Any]:
        if pages and isinstance(pages[0], PowerBIVisual):
            pages = [self.generate_page_json("Page 1", pages)]  # type: ignore[list-item]

        sections = pages or [self.generate_page_json("Page 1", self.build_default_visuals())]
        active = sections[0].get("name", "Page_1") if sections else "Page_1"

        return {
            "version": "5.54",
            "themeCollection": {
                "baseTheme": {
                    "name": "CY23SU10",
                    "version": "5.54",
                    "type": 2,
                }
            },
            "activeSectionName": active,
            "sections": sections,
            "config": json.dumps({"version": "5.54", "settings": {}}, ensure_ascii=False),
            "layoutOptimization": 0,
        }

    def generate_visual_conversion_report(
        self,
        worksheets: List[Dict[str, Any]],
        visuals: List[PowerBIVisual],
    ) -> str:
        lines = [
            "# Visual Conversion Report",
            "",
            f"**Source Worksheets:** {len(worksheets or [])}",
            f"**Power BI Visuals:** {len(visuals or [])}",
            "",
            "| Worksheet/Title | Power BI Visual | Fields |",
            "|---|---|---|",
        ]
        for v in visuals or []:
            fields = []
            for role, refs in v.data_roles.items():
                fields.append(f"{role}: {', '.join([r.column for r in refs])}")
            lines.append(f"| {v.title} | {v.visual_type.value} | {'; '.join(fields)} |")
        return "\n".join(lines)

    # -------------------------- conversion internals ------------------------

    def _convert_single_worksheet(self, worksheet: Dict[str, Any]) -> Optional[PowerBIVisual]:
        title = str(worksheet.get("name") or worksheet.get("title") or "Sheet")
        visual_type = self._map_visual_type(worksheet)

        rows = self._extract_field_names(
            worksheet.get("rows")
            or worksheet.get("row_fields")
            or worksheet.get("dimensions")
            or []
        )
        cols = self._extract_field_names(
            worksheet.get("cols")
            or worksheet.get("columns")
            or worksheet.get("column_fields")
            or []
        )
        marks = self._extract_marks(worksheet)

        roles = self._map_fields_to_data_roles(visual_type, rows, cols, marks)
        if not any(roles.values()):
            fallback = self._fallback_fields()
            roles = {"Values": fallback[:6]} if fallback else {}

        return PowerBIVisual(
            visual_type=visual_type,
            name=title,
            title=title,
            layout=VisualLayout(0, 0, self.DEFAULT_VISUAL_WIDTH, self.DEFAULT_VISUAL_HEIGHT),
            data_roles=roles,
            filters=[],
        )

    def _map_visual_type(self, worksheet: Dict[str, Any]) -> PowerBIVisualType:
        raw = str(
            worksheet.get("visual_type")
            or worksheet.get("chart_type")
            or worksheet.get("type")
            or worksheet.get("mark_type")
            or ""
        ).lower()

        if "slicer" in raw or "filter" in raw:
            return PowerBIVisualType.SLICER
        if "card" in raw or "kpi" in raw:
            return PowerBIVisualType.CARD
        if "matrix" in raw or "pivot" in raw:
            return PowerBIVisualType.MATRIX
        if "table" in raw or "text" in raw:
            return PowerBIVisualType.TABLE
        if "line" in raw:
            return PowerBIVisualType.LINE_CHART
        if "area" in raw:
            return PowerBIVisualType.AREA_CHART
        if "pie" in raw:
            return PowerBIVisualType.PIE_CHART
        if "donut" in raw or "doughnut" in raw:
            return PowerBIVisualType.DONUT_CHART
        if "scatter" in raw or "circle" in raw:
            return PowerBIVisualType.SCATTER_CHART
        if "map" in raw:
            return PowerBIVisualType.MAP
        if "bar" in raw:
            return PowerBIVisualType.CLUSTERED_BAR_CHART
        if "column" in raw:
            return PowerBIVisualType.CLUSTERED_COLUMN_CHART
        return PowerBIVisualType.CLUSTERED_COLUMN_CHART

    def _map_fields_to_data_roles(
        self,
        visual_type: PowerBIVisualType,
        rows: List[str],
        columns: List[str],
        marks: List[str],
    ) -> Dict[str, List[PowerBIFieldRef]]:
        row_refs = self._dedupe_refs([r for r in [self._field_ref(f) for f in rows] if r])
        col_refs = self._dedupe_refs([r for r in [self._field_ref(f) for f in columns] if r])
        mark_refs = self._dedupe_refs([r for r in [self._field_ref(f, prefer_measure=True) for f in marks] if r])

        dims = row_refs + col_refs
        measures = mark_refs or [r for r in dims if r.aggregation or r.is_measure]
        dims = [r for r in dims if not (r.aggregation or r.is_measure)] or row_refs or col_refs

        if visual_type == PowerBIVisualType.CARD:
            return {"Values": (measures or mark_refs or row_refs or col_refs)[:1]}
        if visual_type == PowerBIVisualType.TABLE:
            return {"Values": self._dedupe_refs(row_refs + col_refs + mark_refs)}
        if visual_type == PowerBIVisualType.MATRIX:
            return {"Rows": row_refs[:3], "Columns": col_refs[:2], "Values": measures[:5]}
        if visual_type in {PowerBIVisualType.PIE_CHART, PowerBIVisualType.DONUT_CHART}:
            return {"Category": dims[:1], "Values": measures[:1]}
        if visual_type == PowerBIVisualType.SCATTER_CHART:
            return {"X": measures[:1], "Y": measures[1:2] or measures[:1], "Details": dims[:1]}
        if visual_type == PowerBIVisualType.SLICER:
            return {"Values": dims[:1] or row_refs[:1] or col_refs[:1]}

        return {
            "Category": dims[:1],
            "Y": measures[:3] or mark_refs[:3],
            "Series": col_refs[:1],
        }

    # -------------------------- Power BI query JSON -------------------------

    def _build_prototype_query(
        self,
        roles: Dict[str, List[PowerBIFieldRef]],
    ) -> Tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
        selects = []
        projections: Dict[str, List[Dict[str, Any]]] = {}
        table_aliases: Dict[str, str] = {}

        def alias_for(table: str) -> str:
            if table not in table_aliases:
                table_aliases[table] = f"t{len(table_aliases)}"
            return table_aliases[table]

        for role, refs in roles.items():
            projections[role] = []
            for ref in refs or []:
                table = self._clean_table_name(ref.table)
                col = self._clean_column_name(ref.column)
                alias = alias_for(table)
                query_ref = f"{table}.{col}"

                if ref.is_measure:
                    expr = {
                        "Measure": {
                            "Expression": {"SourceRef": {"Source": alias}},
                            "Property": col,
                        }
                    }
                elif ref.aggregation:
                    expr = {
                        "Aggregation": {
                            "Expression": {
                                "Column": {
                                    "Expression": {"SourceRef": {"Source": alias}},
                                    "Property": col,
                                }
                            },
                            "Function": self._agg_code(ref.aggregation),
                        }
                    }
                else:
                    expr = {
                        "Column": {
                            "Expression": {"SourceRef": {"Source": alias}},
                            "Property": col,
                        }
                    }

                selects.append(
                    {
                        "Name": query_ref,
                        "NativeReferenceName": col,
                        "Expression": expr,
                    }
                )
                projections[role].append({"queryRef": query_ref, "active": True})

        if not selects:
            fallback_table = self._clean_table_name(self.tables[0].get("name", "orders_fact")) if self.tables else "orders_fact"
            alias = alias_for(fallback_table)
            selects.append(
                {
                    "Name": f"{fallback_table}.Dummy",
                    "NativeReferenceName": "Dummy",
                    "Expression": {
                        "Column": {
                            "Expression": {"SourceRef": {"Source": alias}},
                            "Property": "Dummy",
                        }
                    },
                }
            )
            projections = {"Values": [{"queryRef": f"{fallback_table}.Dummy", "active": True}]}

        from_list = [{"Name": alias, "Entity": table, "Type": 0} for table, alias in table_aliases.items()]
        return {"Version": 2, "From": from_list, "Select": selects}, projections

    # ------------------------------- helpers --------------------------------

    def _measure_refs_from_calculated_fields(self) -> List[PowerBIFieldRef]:
        refs: List[PowerBIFieldRef] = []
        for item in self.calculated_fields or []:
            name = (
                item.get("name")
                or item.get("measure")
                or item.get("calculated_field")
                or item.get("field_name")
            )
            if name:
                refs.append(
                    PowerBIFieldRef(
                        table=self._measure_table_name,
                        column=self._clean_column_name(str(name)),
                        is_measure=True,
                    )
                )
        return self._dedupe_refs(refs)

    def _dimension_refs_from_tables(self) -> List[PowerBIFieldRef]:
        refs: List[PowerBIFieldRef] = []
        for table in self.tables or []:
            tname = self._clean_table_name(str(table.get("name") or table.get("table_name") or "Table"))
            for col in table.get("columns") or table.get("fields") or []:
                cname = str(col.get("name") if isinstance(col, dict) else col)
                dtype = str(col.get("dataType") or col.get("data_type") or col.get("type") or "").lower() if isinstance(col, dict) else ""
                if dtype in {"string", "text", "date", "datetime", "boolean"} or not dtype:
                    refs.append(PowerBIFieldRef(tname, self._clean_column_name(cname)))
        return self._dedupe_refs(refs)

    def _numeric_refs_from_tables(self) -> List[PowerBIFieldRef]:
        refs: List[PowerBIFieldRef] = []
        for table in self.tables or []:
            tname = self._clean_table_name(str(table.get("name") or table.get("table_name") or "Table"))
            for col in table.get("columns") or table.get("fields") or []:
                cname = str(col.get("name") if isinstance(col, dict) else col)
                dtype = str(col.get("dataType") or col.get("data_type") or col.get("type") or "").lower() if isinstance(col, dict) else ""
                if dtype in {"int64", "integer", "double", "decimal", "number", "float"}:
                    refs.append(PowerBIFieldRef(tname, self._clean_column_name(cname), aggregation="sum"))
        return self._dedupe_refs(refs)

    def _extract_marks(self, worksheet: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        for key in ("marks", "measures", "values", "metrics"):
            values.extend(self._extract_field_names(worksheet.get(key) or []))
        for pane in worksheet.get("pane_encodings") or []:
            enc = pane.get("encodings") or {}
            if isinstance(enc, dict):
                values.extend(self._extract_field_names(list(enc.values())))
        return self._dedupe_strings(values)

    def _extract_field_names(self, obj: Any) -> List[str]:
        out: List[str] = []
        if obj is None:
            return out
        if isinstance(obj, str):
            return [self._clean_field_token(obj)] if obj.strip() else []
        if isinstance(obj, dict):
            for key in ("name", "field", "field_name", "column", "column_name", "caption"):
                if obj.get(key):
                    return [self._clean_field_token(str(obj[key]))]
            for value in obj.values():
                out.extend(self._extract_field_names(value))
            return out
        if isinstance(obj, Iterable):
            for item in obj:
                out.extend(self._extract_field_names(item))
        return self._dedupe_strings([x for x in out if x])

    def _field_ref(self, field_name: str, prefer_measure: bool = False) -> Optional[PowerBIFieldRef]:
        if not field_name:
            return None

        clean = self._clean_field_token(field_name)
        agg = None

        m = re.match(r"(?i)^\s*(sum|avg|average|count|countd|count_distinct|min|max)\s*\((.*?)\)\s*$", clean)
        if m:
            agg = m.group(1).lower().replace("average", "avg").replace("countd", "count_distinct")
            clean = self._clean_field_token(m.group(2))

        table = None
        mt = re.search(r"'([^']+)'\s*\[([^\]]+)\]", clean)
        if mt:
            table, clean = mt.group(1), mt.group(2)
        else:
            mt = re.search(r"\[([^\]]+)\]", clean)
            if mt:
                clean = mt.group(1)

        table = table or self._field_to_table.get(clean.lower()) or self._guess_default_table(clean, prefer_measure)
        is_measure = prefer_measure and not agg and table == self._measure_table_name

        return PowerBIFieldRef(
            table=self._clean_table_name(table),
            column=self._clean_column_name(clean),
            aggregation=agg,
            is_measure=is_measure,
        )

    def _guess_default_table(self, column: str, prefer_measure: bool = False) -> str:
        if prefer_measure and self.calculated_fields:
            return self._measure_table_name

        if self.tables:
            facts = [
                t for t in self.tables
                if "fact" in str(t.get("name") or t.get("table_name") or "").lower()
                or "sales" in str(t.get("name") or t.get("table_name") or "").lower()
                or "orders" in str(t.get("name") or t.get("table_name") or "").lower()
            ]
            chosen = facts[0] if facts else self.tables[0]
            return str(chosen.get("name") or chosen.get("table_name") or "orders_fact")

        return "orders_fact"

    def _build_field_to_table_index(self, tables: List[Dict[str, Any]]) -> Dict[str, str]:
        index: Dict[str, str] = {}
        for table in tables or []:
            tname = self._clean_table_name(str(table.get("name") or table.get("table_name") or "orders_fact"))
            cols = table.get("columns") or table.get("fields") or []
            for col in cols:
                cname = str(col.get("name") or col.get("column") or col.get("column_name") or "") if isinstance(col, dict) else str(col)
                if cname:
                    index[self._clean_column_name(cname).lower()] = tname
        return index

    def _fallback_fields(self) -> List[PowerBIFieldRef]:
        refs = []
        measure_refs = self._measure_refs_from_calculated_fields()
        if measure_refs:
            return measure_refs

        for table in self.tables[:1]:
            tname = self._clean_table_name(str(table.get("name") or table.get("table_name") or "orders_fact"))
            for col in (table.get("columns") or table.get("fields") or [])[:6]:
                cname = col.get("name") if isinstance(col, dict) else str(col)
                refs.append(PowerBIFieldRef(tname, self._clean_column_name(cname)))
        return refs

    def _calculate_auto_layout(self, index: int, total: int) -> VisualLayout:
        cols = 1 if total <= 1 else 2 if total <= 4 else 3
        padding = 24
        header_gap = 12
        width = (self.CANVAS_WIDTH - padding * (cols + 1)) / cols
        height = self.DEFAULT_VISUAL_HEIGHT
        row, col = divmod(index, cols)
        return VisualLayout(
            x=padding + col * (width + padding),
            y=padding + row * (height + padding + header_gap),
            width=width,
            height=height,
            z_index=index,
        )

    @staticmethod
    def _agg_code(agg: str) -> int:
        return {
            "sum": 0,
            "avg": 1,
            "min": 2,
            "max": 3,
            "count": 4,
            "count_distinct": 5,
        }.get((agg or "sum").lower(), 0)

    @staticmethod
    def _clean_field_token(value: str) -> str:
        value = str(value or "").strip()
        value = re.sub(r"^ATTR\((.*)\)$", r"\1", value, flags=re.I)
        value = value.replace("[", "").replace("]", "")
        return value.strip().strip("'\"")

    @staticmethod
    def _clean_column_name(value: str) -> str:
        # IMPORTANT: Do not replace underscores with spaces. Must match TMDL column names exactly.
        value = str(value or "Field").strip().strip("'\"")
        return re.sub(r"\s+", " ", value).strip() or "Field"

    @staticmethod
    def _clean_table_name(value: str) -> str:
        # IMPORTANT: Do not replace spaces with underscores. Must match TMDL table names exactly.
        value = str(value or "Table").strip().strip("'\"")
        return re.sub(r"\s+", " ", value).strip() or "Table"

    @staticmethod
    def _safe_visual_name(value: str) -> str:
        value = str(value or "visual").strip()
        value = re.sub(r"[^A-Za-z0-9_]+", "_", value)
        value = re.sub(r"_+", "_", value).strip("_")
        return value or f"visual_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _dedupe_strings(values: List[str]) -> List[str]:
        seen, out = set(), []
        for v in values:
            key = str(v).lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(v)
        return out

    @staticmethod
    def _dedupe_refs(values: List[PowerBIFieldRef]) -> List[PowerBIFieldRef]:
        seen, out = set(), []
        for r in values:
            key = (r.table.lower(), r.column.lower(), r.aggregation, r.is_measure)
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    @staticmethod
    def _unique_name(base: str, seen: set[str]) -> str:
        name = base or "visual"
        if name not in seen:
            seen.add(name)
            return name
        i = 2
        while f"{name}_{i}" in seen:
            i += 1
        final = f"{name}_{i}"
        seen.add(final)
        return final


# -------------------------- backward compatible helpers --------------------------

def create_visual_converter(
    tables: Optional[List[Dict[str, Any]]] = None,
    calculated_fields: Optional[List[Dict[str, Any]]] = None,
) -> VisualConverter:
    return VisualConverter(tables=tables, calculated_fields=calculated_fields)


def convert_worksheets_to_visuals(
    worksheets: List[Dict[str, Any]],
    **kwargs: Any,
) -> List[PowerBIVisual]:
    tables = kwargs.pop("tables", None)
    calculated_fields = kwargs.pop("calculated_fields", None)
    return VisualConverter(tables=tables, calculated_fields=calculated_fields).convert_worksheets_to_visuals(
        worksheets,
        **kwargs,
    )


def generate_report_json(visuals: List[PowerBIVisual], page_name: str = "Page 1") -> Dict[str, Any]:
    converter = VisualConverter()
    return converter.generate_report_json([converter.generate_page_json(page_name, visuals)])
