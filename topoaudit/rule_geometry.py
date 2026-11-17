"""Per-feature coordinate, structural geometry, and sliver checks."""
from __future__ import annotations
import math
from typing import Any
from .findings import FindingCollector
from .geometry import (
    area_perimeter_ratio,
    classify_segment_pair,
    consecutive_duplicates,
    format_point,
    is_finite_point,
    line_components,
    line_segment_contacts,
    nonzero_segments,
    polygon_area,
    polygon_components,
    polygon_perimeter,
    ring_closed,
    valid_polygon,
    zero_length_segments,
)
from .model import AuditConfig, CRS, Feature, LayerConfig, Tolerance
from .util import stable_round

MAX_SELF_INTERSECTION_PAIRS = 50

def check_geometry(
    feature: Feature,
    layer: LayerConfig,
    config: AuditConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    geometry = feature.geometry
    points = list(_iter_points(geometry.coordinates))
    finite = [(path, point) for path, point in points if is_finite_point(point)]
    non_finite = [(path, point) for path, point in points if not is_finite_point(point)]
    if non_finite and layer.rule_enabled("COORD_NON_FINITE"):
        collector.add(
            "COORD_NON_FINITE",
            layer.layer_id,
            feature.feature_id,
            evidence={
                "point_count": len(non_finite),
                "locations": [
                    {"path": path, "coordinate": format_point(point)}
                    for path, point in non_finite[:20]
                ],
            },
        )
    outside = [(path, point) for path, point in finite if not config.crs.bounds.contains(point)]
    if outside and layer.rule_enabled("COORD_OUT_OF_BOUNDS"):
        collector.add(
            "COORD_OUT_OF_BOUNDS",
            layer.layer_id,
            feature.feature_id,
            evidence={
                "bounds": config.crs.bounds.as_dict(),
                "point_count": len(outside),
                "locations": [
                    {"path": path, "coordinate": format_point(point)}
                    for path, point in outside[:20]
                ],
            },
        )
    unsupported_reason = _unsupported_reason(feature, layer)
    if unsupported_reason is not None:
        if layer.rule_enabled("GEOM_UNSUPPORTED_TYPE"):
            collector.add(
                "GEOM_UNSUPPORTED_TYPE",
                layer.layer_id,
                feature.feature_id,
                evidence={
                    "geometry_type": geometry.raw_type or geometry.kind,
                    "allowed_types": list(layer.geometry_types),
                    "reason": unsupported_reason,
                },
            )
        return
    if non_finite:
        return
    sequences: list[tuple[str, tuple[Any, ...]]] = []
    for label, line in line_components(geometry):
        sequences.append((label, line))
    for part_label, polygon in polygon_components(geometry):
        for ring_index, ring in enumerate(polygon):
            sequences.append((f"{part_label}.rings[{ring_index}]", ring))
    _check_duplicate_vertices(feature, layer, tolerance, sequences, collector)
    _check_zero_segments(feature, layer, tolerance, sequences, collector)
    _check_rings(feature, layer, tolerance, geometry, collector)
    _check_lines(feature, layer, geometry, collector)
    _check_self_intersections(feature, layer, tolerance, geometry, collector)
    _check_slivers(feature, layer, tolerance, geometry, collector)

def _check_duplicate_vertices(
    feature: Feature,
    layer: LayerConfig,
    tolerance: Tolerance,
    sequences: list[tuple[str, tuple[Any, ...]]],
    collector: FindingCollector,
) -> None:
    locations: list[dict[str, Any]] = []
    total = 0
    for label, points in sequences:
        indices = consecutive_duplicates(tuple(points), tolerance.coordinate_epsilon)
        if indices:
            total += len(indices)
            locations.append({"location": label, "indices": indices})
    if total and layer.rule_enabled("DUPLICATE_CONSECUTIVE_VERTEX"):
        collector.add(
            "DUPLICATE_CONSECUTIVE_VERTEX",
            layer.layer_id,
            feature.feature_id,
            evidence={
                "locations": locations[:20],
                "duplicate_count": total,
                "coordinate_epsilon": tolerance.coordinate_epsilon,
            },
        )

def _check_zero_segments(
    feature: Feature,
    layer: LayerConfig,
    tolerance: Tolerance,
    sequences: list[tuple[str, tuple[Any, ...]]],
    collector: FindingCollector,
) -> None:
    locations: list[dict[str, Any]] = []
    total = 0
    for label, points in sequences:
        indices = zero_length_segments(tuple(points), tolerance.coordinate_epsilon)
        if indices:
            total += len(indices)
            locations.append({"location": label, "indices": indices})
    if total and layer.rule_enabled("ZERO_LENGTH_SEGMENT"):
        collector.add(
            "ZERO_LENGTH_SEGMENT",
            layer.layer_id,
            feature.feature_id,
            evidence={
                "locations": locations[:20],
                "segment_count": total,
                "coordinate_epsilon": tolerance.coordinate_epsilon,
            },
        )

def _check_rings(
    feature: Feature,
    layer: LayerConfig,
    tolerance: Tolerance,
    geometry: Any,
    collector: FindingCollector,
) -> None:
    too_few: list[str] = []
    unclosed: list[str] = []
    for part_label, polygon in polygon_components(geometry):
        for ring_index, ring in enumerate(polygon):
            label = f"{part_label}.rings[{ring_index}]"
            if len(ring) < 4:
                too_few.append(label)
            if ring and not ring_closed(ring, tolerance.coordinate_epsilon):
                unclosed.append(label)
    if unclosed and layer.rule_enabled("RING_NOT_CLOSED"):
        collector.add(
            "RING_NOT_CLOSED",
            layer.layer_id,
            feature.feature_id,
            evidence={
                "locations": unclosed[:20],
                "coordinate_epsilon": tolerance.coordinate_epsilon,
            },
        )
    if too_few and layer.rule_enabled("RING_TOO_FEW_POINTS"):
        collector.add(
            "RING_TOO_FEW_POINTS",
            layer.layer_id,
            feature.feature_id,
            evidence={"locations": too_few[:20], "minimum_points": 4},
        )

def _check_lines(
    feature: Feature,
    layer: LayerConfig,
    geometry: Any,
    collector: FindingCollector,
) -> None:
    locations: list[dict[str, Any]] = []
    for label, line in line_components(geometry):
        if len(line) < 2:
            locations.append({"location": label, "point_count": len(line)})
    if locations and layer.rule_enabled("LINE_TOO_FEW_POINTS"):
        collector.add(
            "LINE_TOO_FEW_POINTS",
            layer.layer_id,
            feature.feature_id,
            evidence={"locations": locations[:20], "minimum_points": 2},
        )

def _check_self_intersections(
    feature: Feature,
    layer: LayerConfig,
    tolerance: Tolerance,
    geometry: Any,
    collector: FindingCollector,
) -> None:
    epsilon = tolerance.coordinate_epsilon
    contacts: list[dict[str, Any]] = []
    # 线组成部分：part 内部段对 + 同一要素跨 part 段对。
    line_parts: list[tuple[str, tuple[Any, ...], bool, list[tuple[int, Any, Any]]]] = []
    for label, line in line_components(geometry):
        if len(line) >= 2:
            line_parts.append(
                (label, line, ring_closed(line, epsilon), nonzero_segments(line, epsilon))
            )
    for label, points, closed, _ in line_parts:
        for contact in line_segment_contacts(points, closed, epsilon):
            contacts.append({**contact, "parts": (label, label)})
    for left in range(len(line_parts)):
        label_a, _, _, segments_a = line_parts[left]
        for right in range(left + 1, len(line_parts)):
            label_b, _, _, segments_b = line_parts[right]
            for i, a, b in segments_a:
                for j, c, d in segments_b:
                    category, detail = classify_segment_pair(a, b, c, d, epsilon)
                    if category == "none":
                        continue
                    contacts.append(
                        {
                            "parts": (label_a, label_b),
                            "segment_indexes": (i, j),
                            "relationship": "cross_part",
                            "category": category,
                            "detail": detail,
                        }
                    )
    # 多边形环：逐环检查（含相邻重走与闭合首尾重叠）。
    for part_label, polygon in polygon_components(geometry):
        for ring_index, ring in enumerate(polygon):
            if len(ring) < 4:
                continue
            label = f"{part_label}.rings[{ring_index}]"
            closed = ring_closed(ring, epsilon)
            for contact in line_segment_contacts(ring, closed, epsilon):
                contacts.append({**contact, "parts": (label, label)})
    usage = layer.line_usage
    violations = [
        contact
        for contact in contacts
        if _contact_violates(usage, contact["category"], contact["relationship"])
    ]
    if not violations or not layer.rule_enabled("SELF_INTERSECTION"):
        return
    notable = [
        contact
        for contact in contacts
        if not (
            contact["category"] == "shared_endpoint"
            and contact["relationship"] in ("adjacent", "closure", "degenerate_adjacent")
        )
    ]
    collector.add(
        "SELF_INTERSECTION",
        layer.layer_id,
        feature.feature_id,
        evidence={
            "usage": usage,
            "coordinate_epsilon": epsilon,
            "violation_count": len(violations),
            "pair_count": len(notable),
            "allowed_shared_endpoint_count": len(contacts) - len(notable),
            "pairs": [
                _contact_evidence(usage, contact)
                for contact in notable[:MAX_SELF_INTERSECTION_PAIRS]
            ],
        },
    )

def _contact_violates(usage: str, category: str, relationship: str) -> bool:
    """Adjudicate one segment-pair contact under the layer's usage contract.

    ``boundary_curve`` requires a simple line: every contact beyond the legal
    shared endpoint of adjacent/closure/cross-part connections is a violation.
    ``route_walk`` additionally tolerates point-only revisits (shared endpoints
    and point touches), but never proper crossings, positive-length overlaps,
    or undecidable contacts.  Undecidable pairs are violations under both
    contracts because the feature cannot be certified self-intersection-free.
    """
    if category in ("proper_crossing", "collinear_overlap", "undecidable"):
        return True
    if category == "point_touch":
        return usage == "boundary_curve"
    if category == "shared_endpoint":
        return usage == "boundary_curve" and relationship == "non_adjacent"
    return False

def _contact_evidence(usage: str, contact: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "parts": list(contact["parts"]),
        "segment_indexes": list(contact["segment_indexes"]),
        "relationship": contact["relationship"],
        "category": contact["category"],
        "violation": _contact_violates(
            usage, contact["category"], contact["relationship"]
        ),
    }
    if contact["detail"]:
        entry["detail"] = contact["detail"]
    return entry

def _check_slivers(
    feature: Feature,
    layer: LayerConfig,
    tolerance: Tolerance,
    geometry: Any,
    collector: FindingCollector,
) -> None:
    if not layer.rule_enabled("SLIVER_POLYGON"):
        return
    for part_label, polygon in polygon_components(geometry):
        if not valid_polygon(polygon, tolerance.coordinate_epsilon):
            continue
        area = polygon_area(polygon)
        perimeter = polygon_perimeter(polygon)
        ratio = area_perimeter_ratio(area, perimeter)
        if ratio < tolerance.sliver_ratio:
            collector.add(
                "SLIVER_POLYGON",
                layer.layer_id,
                feature.feature_id,
                evidence={
                    "part_index": part_label,
                    "area": stable_round(area),
                    "perimeter": stable_round(perimeter),
                    "ratio": stable_round(ratio),
                    "threshold": tolerance.sliver_ratio,
                },
            )

def _unsupported_reason(feature: Feature, layer: LayerConfig) -> str | None:
    kind = feature.geometry.kind
    if kind == "Unsupported":
        if feature.geometry.raw_type is None:
            return "null_or_missing_geometry_type"
        return "unsupported_geometry_type"
    if kind not in layer.geometry_types:
        return "outside_layer_whitelist"
    return None

def _iter_points(value: Any):
    from .geometry import iter_numeric_points
    yield from iter_numeric_points(value)
