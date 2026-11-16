"""Shared fixtures and helpers for line-coverage tests."""
import json
from pathlib import Path

from topoaudit.config import load_config
from topoaudit.engine import run_audit
from topoaudit.loaders import load_layers

ROOT = Path(__file__).resolve().parent.parent
DEMO_CONFIG = ROOT / "fixtures" / "demo" / "config.json"

# A square with a hole, plus a second square sharing the x=10 edge.  The
# union is a 20x10 strip with a hole at (4..6, 4..6).
HOLED = [
    [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
    [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
]
ADJACENT = [[[10, 0], [20, 0], [20, 10], [10, 10], [10, 0]]]
BOWTIE = [[[30, 0], [40, 10], [30, 10], [40, 0], [30, 0]]]  # self-intersecting
UNCLOSED = [[[50, 0], [60, 0], [60, 10], [50, 10]]]


def _polygon_feature(feature_id, rings, extra=None):
    return {
        "type": "Feature",
        "properties": {"id": feature_id, **(extra or {})},
        "geometry": {"type": "Polygon", "coordinates": rings},
    }


def _line_feature(feature_id, coordinates, kind="LineString"):
    return {
        "type": "Feature",
        "properties": {"id": feature_id},
        "geometry": {"type": kind, "coordinates": coordinates},
    }


def _write(path: Path, features):
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )


def build_case(tmp: Path, lines, polygons, coverage, polygons_crs=None):
    """Write a synthetic case and return (config, layers)."""
    _write(tmp / "lines.geojson", lines)
    _write(tmp / "polys.geojson", polygons)
    layer_specs = [
        {
            "id": "lines",
            "path": "lines.geojson",
            "format": "geojson",
            "geometry_types": ["LineString", "MultiLineString"],
            "id_field": "id",
            "schema": {"id": {"type": "string", "required": True}},
            "rules": {
                "DANGLING_ENDPOINT": {"enabled": False},
                "NEAR_MISS_ENDPOINT": {"enabled": False},
            },
        },
        {
            "id": "polys",
            "path": "polys.geojson",
            "format": "geojson",
            "geometry_types": ["Polygon", "MultiPolygon"],
            "id_field": "id",
            "schema": {"id": {"type": "string", "required": True}},
        },
    ]
    if polygons_crs is not None:
        layer_specs[1]["declared_crs"] = polygons_crs
    data = {
        "schema_version": "1.0",
        "run_label": "coverage-unit",
        "tolerance": {"coordinate_epsilon": 0.000001},
        "crs": {
            "name": "LOCAL_XY",
            "type": "planar",
            "bounds": {"min_x": -100, "min_y": -100, "max_x": 200, "max_y": 100},
        },
        "layers": layer_specs,
        "coverage_rules": coverage,
    }
    path = tmp / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    config = load_config(path)
    return config, load_layers(config)


def run_coverage(tmp: Path, lines, polygons, coverage, polygons_crs=None):
    config, layers = build_case(tmp, lines, polygons, coverage, polygons_crs)
    result = run_audit(config)
    section = result.coverage[0]
    records = {record["feature_id"]: record for record in section["lines"]}
    findings = {
        finding.feature_id: finding
        for finding in result.findings
        if finding.rule_id == "LINE_COVERAGE"
    }
    return section, records, findings


def rule(mode="boundary_allowed", tolerance=0.5):
    return [
        {
            "id": "cov",
            "line_layer": "lines",
            "polygon_layer": "polys",
            "boundary_mode": mode,
            "tolerance": tolerance,
        }
    ]

