"""
Power BI template/report helper for ThoughtSpot -> Power BI migration.

Use this file as: src/powerbi/template_creator.py

Important fixes:
1. Does NOT create an unsupported table named "Measures".
2. Uses safe measure table name: "DAX Measures".
3. Creates a report folder with actual visualContainers instead of only README files.
4. Keeps PBIP/PBIX helper methods backward-compatible with old imports.
5. Does not rely on Power BI "Upgrade report" to show visuals.

Important:
The semantic model is generated/injected by pbip_tmdl_injector.py.
This file creates report/page/visual scaffolding.
"""

from __future__ import annotations

import json
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from loguru import logger
except Exception:  # pragma: no cover
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)


SAFE_MEASURE_TABLE_NAME = "DAX Measures"


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def _safe_name(value: Any, fallback: str = "Item") -> str:
    text = str(value or fallback).strip()
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"[^A-Za-z0-9_ .%-]+", "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def _safe_file_name(value: Any, fallback: str = "item") -> str:
    text = _safe_name(value, fallback=fallback)
    text = text.replace(" ", "_")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text[:80] or fallback


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _normalise_list(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, dict)]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _extract_tables_from_metadata(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("tables", "model_tables", "semantic_tables", "datasets"):
        tables = _normalise_list(metadata.get(key))
        if tables:
            return tables
    return []


def _extract_worksheets_from_metadata(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("worksheets", "sheets", "visuals", "charts", "dashboard_visuals"):
        worksheets = _normalise_list(metadata.get(key))
        if worksheets:
            return worksheets
    return []


def _normalise_conversions(conversions: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in conversions or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("measure") or item.get("calculated_field") or item.get("field_name")
        dax = item.get("dax") or item.get("expression") or item.get("dax_formula")
        fixed = dict(item)
        if name:
            fixed["name"] = str(name)
            fixed["calculated_field"] = str(name)
        if dax:
            fixed["dax"] = str(dax)
        result.append(fixed)
    return result


# -----------------------------------------------------------------------------
# Report/package template creator
# -----------------------------------------------------------------------------

class PowerBIReportTemplateCreator:
    """Create lightweight report files for the migration export package."""

    def create_report_folder(
        self,
        report_dir: str | Path,
        metadata: Optional[Dict[str, Any]] = None,
        conversions: Optional[List[Dict[str, Any]]] = None,
        relationships: Optional[List[Dict[str, Any]]] = None,
    ) -> Path:
        """
        Create a .Report folder with report.json and visual containers.

        This creates a usable starter report. If worksheet metadata is missing,
        it still creates KPI cards/table visuals from migrated DAX measures.
        """
        report_dir = Path(report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)

        metadata = metadata or {}
        conversions = _normalise_conversions(conversions or [])
        relationships = relationships or []

        report_name = _safe_name(
            metadata.get("report_name")
            or metadata.get("name")
            or "ThoughtSpot Migration Report"
        )

        tables = _extract_tables_from_metadata(metadata)
        worksheets = _extract_worksheets_from_metadata(metadata)

        visuals = []
        report_json: Dict[str, Any]

        try:
            from .visual_converter import VisualConverter
        except Exception:
            try:
                from visual_converter import VisualConverter  # type: ignore
            except Exception as exc:
                VisualConverter = None  # type: ignore
                logger.warning(f"Could not import VisualConverter: {exc}")

        if VisualConverter:
            converter = VisualConverter(tables=tables, calculated_fields=conversions)
            if worksheets:
                visuals = converter.convert_worksheets_to_visuals(worksheets)
            else:
                visuals = converter.build_default_visuals()

            page = converter.generate_page_json("Migration Overview", visuals)
            report_json = converter.generate_report_json([page])
        else:
            report_json = self._fallback_report_json()

        # PBIP report metadata with actual sections/visualContainers.
        _write_json(report_dir / "report.json", report_json)

        # Also write legacy Report/Layout for older packaging code paths.
        _write_json(report_dir / "Layout", self._report_json_to_legacy_layout(report_json))

        # Some PBIP variants expect definition folder; harmless if ignored.
        self._write_definition_files(report_dir, report_json)

        summary_lines = [
            f"# {report_name}",
            "",
            "Generated by ThoughtSpot -> Power BI migration tool.",
            "",
            "## Migration Summary",
            f"- Dashboards: {metadata.get('total_dashboards', metadata.get('dashboard_count', 0))}",
            f"- Worksheets: {metadata.get('total_worksheets', metadata.get('worksheet_count', len(worksheets)))}",
            f"- Tables: {metadata.get('total_tables', metadata.get('table_count', len(tables)))}",
            f"- Calculated fields: {metadata.get('total_calculated_fields', len(conversions))}",
            f"- DAX conversions: {len(conversions)}",
            f"- Relationships: {len(relationships)}",
            f"- Report visuals created: {len(visuals)}",
            "",
            "## Suggested Power BI checks",
            "1. Do not click Upgrade report unless you have saved a backup copy.",
            "2. Expand DAX Measures and confirm measure formulas.",
            "3. Open Model view and confirm relationship lines.",
            "4. If visuals are blank, refresh the semantic model and check field names.",
        ]
        _write_text(report_dir / "README.md", "\n".join(summary_lines))

        visual_suggestions = self.build_visual_suggestions(metadata, conversions)
        visual_suggestions["created_visual_count"] = len(visuals)
        _write_json(report_dir / "visual_suggestions.json", visual_suggestions)

        logger.info(f"Created report folder: {report_dir} with {len(visuals)} visuals")
        return report_dir

    def _fallback_report_json(self) -> Dict[str, Any]:
        return {
            "version": "5.54",
            "themeCollection": {
                "baseTheme": {
                    "name": "CY23SU10",
                    "version": "5.54",
                    "type": 2,
                }
            },
            "activeSectionName": "Migration_Overview",
            "sections": [
                {
                    "name": "Migration_Overview",
                    "displayName": "Migration Overview",
                    "displayOption": 1,
                    "height": 720,
                    "width": 1280,
                    "ordinal": 0,
                    "filters": "[]",
                    "visualContainers": [],
                    "config": json.dumps({"objects": {}}, ensure_ascii=False),
                }
            ],
            "config": json.dumps({"version": "5.54", "settings": {}}, ensure_ascii=False),
            "layoutOptimization": 0,
        }

    def _report_json_to_legacy_layout(self, report_json: Dict[str, Any]) -> Dict[str, Any]:
        sections = report_json.get("sections") or []
        return {
            "id": 0,
            "sections": sections,
            "config": report_json.get("config", "{}"),
            "layoutOptimization": report_json.get("layoutOptimization", 0),
        }

    def _write_definition_files(self, report_dir: Path, report_json: Dict[str, Any]) -> None:
        """
        Writes simple PBIP report definition files for tooling that expects
        definition/pages folders. Power BI may ignore these depending on version,
        but they help preserve report metadata in export packages.
        """
        definition = report_dir / "definition"
        pages_dir = definition / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        _write_json(definition / "report.json", {
            "version": report_json.get("version", "5.54"),
            "themeCollection": report_json.get("themeCollection", {}),
            "activeSectionName": report_json.get("activeSectionName", "Migration_Overview"),
        })

        for index, section in enumerate(report_json.get("sections") or []):
            page_name = section.get("name") or f"Page_{index + 1}"
            page_dir = pages_dir / _safe_file_name(page_name, f"Page_{index + 1}")
            page_dir.mkdir(parents=True, exist_ok=True)

            page_json = dict(section)
            visuals = page_json.pop("visualContainers", [])

            _write_json(page_dir / "page.json", page_json)

            visuals_dir = page_dir / "visuals"
            for v_index, visual in enumerate(visuals or []):
                visual_name = visual.get("name") or f"Visual_{v_index + 1}"
                v_dir = visuals_dir / _safe_file_name(visual_name, f"Visual_{v_index + 1}")
                v_dir.mkdir(parents=True, exist_ok=True)
                _write_json(v_dir / "visual.json", visual)

    def build_visual_suggestions(
        self,
        metadata: Optional[Dict[str, Any]] = None,
        conversions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Return simple visual suggestions based on available measures."""
        metadata = metadata or {}
        conversions = _normalise_conversions(conversions or [])

        measure_names = []
        for item in conversions:
            name = item.get("calculated_field") or item.get("name") or item.get("field_name")
            if name:
                measure_names.append(_safe_name(name))

        charts = []
        for idx, name in enumerate(measure_names[:8]):
            charts.append({
                "title": name,
                "visual_type": "card" if idx < 4 else "clusteredColumnChart",
                "measure": name,
                "table": SAFE_MEASURE_TABLE_NAME,
            })

        return {
            "created_at": datetime.utcnow().isoformat() + "Z",
            "measure_table": SAFE_MEASURE_TABLE_NAME,
            "suggested_visual_count": len(charts),
            "visuals": charts,
            "note": "Starter visuals are generated from available measures and worksheet metadata."
        }


# -----------------------------------------------------------------------------
# Backward compatible starter PBIX creator
# -----------------------------------------------------------------------------

class StarterPBIXCreator:
    """
    Backward-compatible starter template creator.

    Uses "DAX Measures" instead of unsupported "Measures".
    """

    def __init__(self):
        self.template_dir = Path(__file__).parent / "templates"
        self.template_dir.mkdir(exist_ok=True)

    def create_blank_template(
        self,
        output_path: str,
        include_measures_table: bool = True,
        include_date_table: bool = False,
    ) -> Path:
        logger.info(f"Creating blank Power BI template: {output_path}")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self._create_content_types(temp_path)
            self._create_data_model(temp_path, include_measures_table, include_date_table)
            self._create_data_model_schema(temp_path)
            self._create_version(temp_path)
            self._create_report(temp_path)
            self._create_metadata(temp_path)
            self._create_diagram_state(temp_path)

            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in temp_path.rglob("*"):
                    if file_path.is_file():
                        zf.write(file_path, file_path.relative_to(temp_path).as_posix())

        logger.info(f"Created template: {output_path}")
        return output_path

    def _create_content_types(self, temp_path: Path) -> None:
        xml = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json" />
  <Default Extension="xml" ContentType="application/xml" />
  <Override PartName="/DataModel" ContentType="application/x-tmdl-data" />
  <Override PartName="/DataModelSchema" ContentType="application/x-tmdl-metadata" />
  <Override PartName="/DiagramState" ContentType="application/json" />
  <Override PartName="/Report/Layout" ContentType="application/json" />
  <Override PartName="/Metadata" ContentType="application/json" />
  <Override PartName="/Version" ContentType="text/plain" />
</Types>
"""
        _write_text(temp_path / "[Content_Types].xml", xml.strip())

    def _create_data_model(self, temp_path: Path, include_measures_table: bool, include_date_table: bool) -> None:
        tables: List[Dict[str, Any]] = []

        if include_measures_table:
            tables.append({
                "name": SAFE_MEASURE_TABLE_NAME,
                "description": "Table for migrated DAX measures",
                "isHidden": False,
                "columns": [
                    {
                        "name": "Measure Placeholder",
                        "dataType": "string",
                        "isHidden": True,
                        "sourceColumn": "Measure Placeholder",
                    }
                ],
                "partitions": [
                    {
                        "name": f"{SAFE_MEASURE_TABLE_NAME} Partition",
                        "mode": "import",
                        "source": {
                            "type": "m",
                            "expression": "let Source = #table({\"Measure Placeholder\"}, {{\"\"}}) in Source",
                        },
                    }
                ],
                "measures": [],
            })

        if include_date_table:
            tables.append({
                "name": "Calendar",
                "description": "Optional date table",
                "columns": [
                    {"name": "Date", "dataType": "dateTime", "sourceColumn": "Date"},
                    {"name": "Year", "dataType": "int64", "sourceColumn": "Year"},
                    {"name": "Month", "dataType": "string", "sourceColumn": "Month"},
                ],
                "partitions": [
                    {
                        "name": "Calendar Partition",
                        "mode": "import",
                        "source": {
                            "type": "m",
                            "expression": "let Source = #table({\"Date\",\"Year\",\"Month\"}, {{#date(2026,1,1),2026,\"January\"}}) in Source",
                        },
                    }
                ],
            })

        model = {
            "name": "SemanticModel",
            "compatibilityLevel": 1567,
            "model": {
                "culture": "en-US",
                "defaultPowerBIDataSourceVersion": "powerBI_V3",
                "sourceQueryCulture": "en-US",
                "dataSources": [],
                "tables": tables,
                "relationships": [],
                "expressions": [],
                "annotations": [
                    {"name": "PBI_ProTooling", "value": "[\"DevMode\"]"},
                    {"name": "GeneratedBy", "value": "ThoughtSpotPowerBIMigration"},
                ],
            },
        }
        _write_json(temp_path / "DataModel", model)

    def _create_report(self, temp_path: Path) -> None:
        report_dir = temp_path / "Report"
        report_dir.mkdir(exist_ok=True)
        layout = {
            "id": 0,
            "sections": [
                {
                    "name": "ReportSection",
                    "displayName": "Migration Overview",
                    "filters": "[]",
                    "ordinal": 0,
                    "visualContainers": [],
                    "config": "{}",
                    "displayOption": 0,
                    "width": 1280,
                    "height": 720,
                }
            ],
            "config": "{}",
            "layoutOptimization": 0,
        }
        _write_json(report_dir / "Layout", layout)

    def _create_metadata(self, temp_path: Path) -> None:
        _write_json(temp_path / "Metadata", {"version": "3.0", "createdBy": "ThoughtSpotPowerBIMigration"})

    def _create_diagram_state(self, temp_path: Path) -> None:
        _write_json(temp_path / "DiagramState", {"version": "1.0", "diagramViewState": {}})

    def _create_data_model_schema(self, temp_path: Path) -> None:
        _write_json(temp_path / "DataModelSchema", {
            "name": "SemanticModel",
            "compatibilityLevel": 1567,
            "model": {"defaultPowerBIDataSourceVersion": "powerBI_V3"},
        })

    def _create_version(self, temp_path: Path) -> None:
        _write_text(temp_path / "Version", "2.0")


# -----------------------------------------------------------------------------
# Public functions used by other backend files
# -----------------------------------------------------------------------------

def create_report_template(
    report_dir: str | Path,
    metadata: Optional[Dict[str, Any]] = None,
    conversions: Optional[List[Dict[str, Any]]] = None,
    relationships: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    creator = PowerBIReportTemplateCreator()
    return creator.create_report_folder(report_dir, metadata, conversions, relationships)


def create_default_templates() -> None:
    creator = StarterPBIXCreator()
    templates_dir = Path("./templates")
    templates_dir.mkdir(parents=True, exist_ok=True)

    creator.create_blank_template(
        output_path=str(templates_dir / "blank_template.pbix"),
        include_measures_table=False,
        include_date_table=False,
    )
    creator.create_blank_template(
        output_path=str(templates_dir / "standard_template.pbix"),
        include_measures_table=True,
        include_date_table=False,
    )
    logger.info("Default templates created in ./templates/")


if __name__ == "__main__":
    create_default_templates()
