"""Inventory, exclusion, and spatial-candidate construction for the precheck.

Everything here is explicit: invalid polygons, non-finite points, missing
anchors, duplicate ids, and CRS conflicts are recorded with reasons
instead of silently entering the assignment.  Records are sorted by
input-order-independent keys so permuting features cannot change the
outcome.
"""
from __future__ import annotations
from typing import Any, Optional
from .geometry import (
    is_finite_point,
    iter_numeric_points,
    point_in_zone_components,
    polygon_components,
    valid_polygon,
)
from .geometry_core import distance
from .model import AuditConfig, Feature, LoadedLayer
from .util import stable_round

# Point exclusion reason codes (part of the public result).
NON_FINITE = "non_finite_coordinate"
UNSUPPORTED_GEOMETRY = "unsupported_geometry"
# Zone exclusion reason codes.
INVALID_POLYGON = "invalid_polygon"
MISSING_ANCHOR = "missing_anchor"
MISSING_ZONE_ID = "missing_zone_id"
# Blocking issue codes.
ISSUE_CRS_CONFLICT = "LAYER_CRS_CONFLICT"
ISSUE_DUPLICATE_POINT_ID = "DUPLICATE_POINT_ID"
ISSUE_DUPLICATE_ZONE_ID = "DUPLICATE_ZONE_ID"
ISSUE_UNKNOWN_ZONE_REFERENCE = "UNKNOWN_ZONE_REFERENCE"


def layer_by_id(layers: tuple[LoadedLayer, ...], layer_id: str) -> LoadedLayer:
    for layer in layers:
        if layer.config.layer_id == layer_id:
            return layer
    raise ValueError(f"layer not found: {layer_id}")  # pragma: no cover


def crs_issues(
    config: AuditConfig, point_layer: LoadedLayer, zone_layer: LoadedLayer
) -> list[dict[str, Any]]:
    expected = {"name": config.crs.name, "type": config.crs.kind}
    issues: list[dict[str, Any]] = []
    for layer in (point_layer, zone_layer):
        declared = layer.config.declared_crs
        if declared is None:
            continue
        actual = {"name": declared.name, "type": declared.kind}
        if actual != expected:
            issues.append(
                {
                    "code": ISSUE_CRS_CONFLICT,
                    "message": (
                        f"图层 {layer.config.layer_id} 的 declared_crs 与全局 crs 不一致，"
                        "坐标不可直接比较，分配预检中止"
                    ),
                    "details": {
                        "layer_id": layer.config.layer_id,
                        "expected_crs": expected,
                        "declared_crs": actual,
                    },
                }
            )
    return issues


def build_zone_inventory(
    zone_layer: LoadedLayer, spec: Any, epsilon: float
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for feature in zone_layer.features:
        zone_id = _zone_id_value(feature, spec.zone_id_field)
        components = polygon_components(feature.geometry)
        reason: Optional[str] = None
        anchor: Optional[tuple[float, float]] = None
        if zone_id is None:
            reason = MISSING_ZONE_ID
        else:
            reason = _zone_geometry_problem(feature, components, epsilon)
            if reason is None:
                anchor = spec.anchors.get(zone_id)
                if anchor is None:
                    reason = MISSING_ANCHOR
        records.append(
            {
                "zone_id": zone_id,
                "feature_id": feature.feature_id,
                "source_index": feature.source_index,
                "capacity": spec.capacity_for(zone_id) if zone_id is not None else None,
                "anchor": anchor,
                "components": len(components),
                "status": "excluded" if reason else "valid",
                "exclusion_reason": reason,
                "_feature": feature,
            }
        )
    records.sort(
        key=lambda record: (
            record["zone_id"] is None,
            record["zone_id"] or "",
            record["feature_id"],
            repr(record["_feature"].geometry.coordinates),
        )
    )
    return records


def _zone_geometry_problem(
    feature: Feature, components: list[Any], epsilon: float
) -> Optional[str]:
    if feature.geometry.kind not in ("Polygon", "MultiPolygon"):
        return INVALID_POLYGON
    if not components:
        return INVALID_POLYGON
    for _, point in iter_numeric_points(feature.geometry.coordinates):
        if not is_finite_point(point):
            return INVALID_POLYGON
    for _, polygon in components:
        if not valid_polygon(polygon, epsilon):
            return INVALID_POLYGON
    return None


def _zone_id_value(feature: Feature, field: str) -> Optional[str]:
    value = feature.properties.get(field)
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


def build_point_inventory(point_layer: LoadedLayer) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for feature in point_layer.features:
        reason: Optional[str] = None
        coordinate: Optional[tuple[float, float]] = None
        if feature.geometry.kind != "Point":
            reason = UNSUPPORTED_GEOMETRY
        else:
            raw = feature.geometry.coordinates
            if not is_finite_point(raw):
                reason = NON_FINITE
            else:
                coordinate = (float(raw[0]), float(raw[1]))
        records.append(
            {
                "point_id": feature.feature_id,
                "source_index": feature.source_index,
                "coordinate": coordinate,
                "status": "excluded" if reason else "eligible",
                "exclusion_reason": reason,
                "_feature": feature,
            }
        )
    records.sort(
        key=lambda record: (
            record["point_id"],
            repr(record["_feature"].geometry.coordinates),
        )
    )
    return records


def duplicate_issue(
    records: list[dict[str, Any]], key: str, code: str, label: str
) -> list[dict[str, Any]]:
    seen: dict[str, int] = {}
    for record in records:
        value = record.get(key)
        if value is None:
            continue
        seen[value] = seen.get(value, 0) + 1
    duplicates = sorted(value for value, count in seen.items() if count > 1)
    if not duplicates:
        return []
    return [
        {
            "code": code,
            "message": f"{label} ID 重复，无法建立唯一归属，分配预检中止",
            "details": {"duplicated_ids": duplicates},
        }
    ]


def unknown_reference_issues(
    zones: list[dict[str, Any]], spec: Any
) -> list[dict[str, Any]]:
    known = {record["zone_id"] for record in zones if record["zone_id"] is not None}
    unknown_anchors = sorted(key for key in spec.anchors if key not in known)
    unknown_capacities = sorted(key for key in spec.zone_capacities if key not in known)
    if not unknown_anchors and not unknown_capacities:
        return []
    return [
        {
            "code": ISSUE_UNKNOWN_ZONE_REFERENCE,
            "message": "anchors 或 zone_capacities 引用了责任区图层中不存在的区 ID",
            "details": {
                "unknown_anchor_zones": unknown_anchors,
                "unknown_capacity_zones": unknown_capacities,
            },
        }
    ]


def build_candidates(
    valid_zones: list[dict[str, Any]],
    eligible_points: list[dict[str, Any]],
    spec: Any,
    epsilon: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Provable point-zone containment candidates.

    Points inside a hole are reported as outside by ``point_in_polygon``
    and never become candidates; boundary points become candidates only
    when ``boundary_policy == "candidate"``, otherwise they are listed as
    review pairs; overlapping zones yield one candidate per zone.
    """
    candidates: list[dict[str, Any]] = []
    review_pairs: list[dict[str, Any]] = []
    for point in eligible_points:
        coordinate = point["coordinate"]
        for zone in valid_zones:
            relation, component = point_in_zone_components(
                zone["_feature"].geometry, coordinate, epsilon
            )
            if relation == "outside":
                continue
            anchor = zone["anchor"]
            entry = {
                "point_id": point["point_id"],
                "zone_id": zone["zone_id"],
                "relation": relation,
                "component": component,
                "distance": stable_round(distance(coordinate, anchor)),
            }
            if relation == "inside" or spec.boundary_policy == "candidate":
                candidates.append(entry)
            else:
                review_pairs.append(dict(entry, policy="review"))
    candidates.sort(key=lambda item: (item["point_id"], item["zone_id"]))
    review_pairs.sort(key=lambda item: (item["point_id"], item["zone_id"]))
    return candidates, review_pairs
