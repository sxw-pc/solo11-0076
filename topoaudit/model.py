"""Typed internal model shared across loading, rules, reports, and storage."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
SUPPORTED_GEOMETRY_TYPES = (
    "Point",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
)
SUPPORTED_SCHEMA_TYPES = (
    "string",
    "number",
    "integer",
    "boolean",
    "object",
    "array",
)
SEVERITIES = ("error", "warning", "info")
SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}
@dataclass(frozen=True)
class Bounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    def contains(self, point: tuple[float, float]) -> bool:
        x, y = point
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y
    def as_dict(self) -> dict[str, float]:
        return {
            "min_x": self.min_x,
            "min_y": self.min_y,
            "max_x": self.max_x,
            "max_y": self.max_y,
        }
@dataclass(frozen=True)
class CRS:
    name: str
    kind: str
    bounds: Bounds
    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.kind, "bounds": self.bounds.as_dict()}
@dataclass(frozen=True)
class DeclaredCRS:
    name: str
    kind: str
    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "type": self.kind}
@dataclass(frozen=True)
class Tolerance:
    coordinate_epsilon: float = 1e-9
    near_miss_distance: float = 1.0
    sliver_ratio: float = 0.1
    def as_dict(self) -> dict[str, float]:
        return {
            "coordinate_epsilon": self.coordinate_epsilon,
            "near_miss_distance": self.near_miss_distance,
            "sliver_ratio": self.sliver_ratio,
        }
@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str
    required: bool = True
    def as_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "required": self.required}
@dataclass(frozen=True)
class LayerConfig:
    layer_id: str
    path: str
    resolved_path: Path
    source_format: str
    geometry_types: tuple[str, ...]
    id_field: str
    schema: dict[str, FieldSpec]
    rules: dict[str, bool]
    threshold_overrides: dict[str, float]
    declared_crs: Optional[DeclaredCRS]
    def rule_enabled(self, rule_id: str) -> bool:
        return self.rules.get(rule_id, True)
    def effective_tolerance(self, base: Tolerance) -> Tolerance:
        values = base.as_dict()
        values.update(self.threshold_overrides)
        return Tolerance(**values)
    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.layer_id,
            "path": self.path,
            "format": self.source_format,
            "geometry_types": list(self.geometry_types),
            "id_field": self.id_field,
            "schema": {k: v.as_dict() for k, v in sorted(self.schema.items())},
            "rules": {k: self.rules[k] for k in sorted(self.rules)},
            "thresholds": {
                k: self.threshold_overrides[k]
                for k in sorted(self.threshold_overrides)
            },
        }
        if self.declared_crs is not None:
            result["declared_crs"] = self.declared_crs.as_dict()
        return result
@dataclass(frozen=True)
class CoverageRequest:
    """Explicit opt-in coverage-gap audit request.

    The AOI is stored as immutable inline geometry so the request enters the
    public configuration digest without re-reading any file.
    """
    layer_id: str
    mode: str
    aoi_geometry: Geometry
    def as_public_dict(self) -> dict[str, Any]:
        return {
            "layer_id": self.layer_id,
            "mode": self.mode,
            "aoi_geometry": public_geometry(self.aoi_geometry),
        }
def public_geometry(geometry: Geometry) -> dict[str, Any]:
    if geometry.kind == "Polygon":
        coordinates: Any = [list(ring) for ring in geometry.coordinates]
    elif geometry.kind == "MultiPolygon":
        coordinates = [
            [list(ring) for ring in polygon] for polygon in geometry.coordinates
        ]
    else:
        coordinates = []
    return {"type": geometry.kind, "coordinates": coordinates}
@dataclass(frozen=True)
class AuditConfig:
    path: Path
    config_digest: str
    schema_version: str
    run_label: str
    tolerance: Tolerance
    crs: CRS
    layers: tuple[LayerConfig, ...]
    coverage: Optional[CoverageRequest] = None
    def as_public_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "run_label": self.run_label,
            "tolerance": self.tolerance.as_dict(),
            "crs": self.crs.as_dict(),
            "layers": [layer.as_dict() for layer in self.layers],
        }
        if self.coverage is not None:
            result["coverage"] = self.coverage.as_public_dict()
        return result
@dataclass(frozen=True)
class Geometry:
    kind: str
    coordinates: tuple[Any, ...]
    raw_type: Optional[str] = None

@dataclass(frozen=True)
class Feature:
    layer_id: str
    feature_id: str
    geometry: Geometry
    properties: dict[str, Any]
    source_index: int
@dataclass(frozen=True)
class InputFile:
    layer_id: str
    path: str
    source_format: str
    sha256: str
    size_bytes: int
    def as_dict(self) -> dict[str, Any]:
        return {
            "layer_id": self.layer_id,
            "path": self.path,
            "format": self.source_format,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }
@dataclass(frozen=True)
class LoadedLayer:
    config: LayerConfig
    features: tuple[Feature, ...]
    input_file: InputFile

@dataclass(frozen=True)
class Finding:
    finding_id: str
    rule_id: str
    severity: str
    layer_id: str
    feature_id: Optional[str]
    related_feature_ids: tuple[str, ...]
    evidence: dict[str, Any]
    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "layer_id": self.layer_id,
            "feature_id": self.feature_id,
            "related_feature_ids": list(self.related_feature_ids),
            "evidence": self.evidence,
        }

@dataclass
class LayerStats:
    layer_id: str
    feature_count: int
    geometry_type_counts: dict[str, int] = field(default_factory=dict)
    finding_count: int = 0
    severity_counts: dict[str, int] = field(default_factory=dict)
    bbox: Optional[list[float]] = None
    def as_dict(self) -> dict[str, Any]:
        return {
            "layer_id": self.layer_id,
            "feature_count": self.feature_count,
            "geometry_type_counts": {
                k: self.geometry_type_counts[k]
                for k in sorted(self.geometry_type_counts)
            },
            "finding_count": self.finding_count,
            "severity_counts": {
                sev: self.severity_counts.get(sev, 0) for sev in SEVERITIES
            },
            "bbox": self.bbox,
        }

@dataclass
class AuditResult:
    config: AuditConfig
    layers: tuple[LoadedLayer, ...]
    findings: tuple[Finding, ...]
    layer_stats: tuple[LayerStats, ...]
    input_files: tuple[InputFile, ...]
    input_digest: str
    summary: dict[str, Any]
    limitations: tuple[str, ...]
    coverage: Optional[dict[str, Any]] = None

def geometry_type_counts(features: tuple[Feature, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        kind = feature.geometry.kind
        counts[kind] = counts.get(kind, 0) + 1
    return counts
