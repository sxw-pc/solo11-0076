"""Per-feature coordinate, structural geometry, and sliver checks."""
from __future__ import annotations
from typing import Any
from .clearance import STATUS_BLOCKED, STATUS_PASS, STATUS_UNKNOWN, audit_feature_clearance
from .findings import FindingCollector
from .geometry import (
    area_perimeter_ratio,
    consecutive_duplicates,
    format_point,
    is_finite_point,
    line_components,
    line_self_intersections,
    polygon_area,
    polygon_components,
    polygon_perimeter,
    ring_closed,
    valid_polygon,
    zero_length_segments,
)
from .model import Feature, LayerConfig, Tolerance
from .util import stable_round

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
    _check_clearance(feature, layer, tolerance, geometry, collector)

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
    locations: list[dict[str, Any]] = []
    for label, line in line_components(geometry):
        if len(line) >= 2:
            closed = ring_closed(line, tolerance.coordinate_epsilon)
            pairs = line_self_intersections(line, closed, tolerance.coordinate_epsilon)
            if pairs:
                locations.append({"location": label, "segment_pairs": pairs[:20]})
    for part_label, polygon in polygon_components(geometry):
        for ring_index, ring in enumerate(polygon):
            if len(ring) >= 4:
                closed = ring_closed(ring, tolerance.coordinate_epsilon)
                pairs = line_self_intersections(ring, closed, tolerance.coordinate_epsilon)
                if pairs:
                    locations.append(
                        {
                            "location": f"{part_label}.rings[{ring_index}]",
                            "segment_pairs": pairs[:20],
                        }
                    )
    if locations and layer.rule_enabled("SELF_INTERSECTION"):
        collector.add(
            "SELF_INTERSECTION",
            layer.layer_id,
            feature.feature_id,
            evidence={"locations": locations[:20]},
        )

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

def _check_clearance(
    feature: Feature,
    layer: LayerConfig,
    tolerance: Tolerance,
    geometry: Any,
    collector: FindingCollector,
) -> None:
    """Contract-scoped, read-only orthogonal clearance connectivity audit.

    The two usage contracts are authorised independently via the layer's
    ``clearance_contract`` configuration and always emit the same full
    evidence; only the delivery gate differs.
    """
    contract = layer.clearance_contract
    if contract is None:
        return
    parts = polygon_components(geometry)
    if not parts:
        return
    audit = audit_feature_clearance(
        polygons=parts,
        clearance_width=contract.clearance_width,
        min_core_area=contract.min_core_area,
        usage_contract=contract.usage_contract,
        epsilon=tolerance.coordinate_epsilon,
    )
    base_evidence = {
        "contract": contract.usage_contract,
        "clearance_width": stable_round(contract.clearance_width),
        "min_core_area": stable_round(contract.min_core_area),
        "status": audit.status,
        "parts": [part.as_dict() for part in audit.parts],
        "input_summary": audit.input_summary,
    }
    if audit.reason is not None:
        base_evidence["reason"] = audit.reason
    if audit.status == STATUS_UNKNOWN:
        if layer.rule_enabled("CLEARANCE_AUDIT_UNKNOWN"):
            collector.add(
                "CLEARANCE_AUDIT_UNKNOWN",
                layer.layer_id,
                feature.feature_id,
                evidence=base_evidence,
            )
        return
    if audit.status == STATUS_BLOCKED:
        rule_id = (
            "BLOCKING_NARROW_NECK"
            if contract.usage_contract == "access_clearance"
            else "NARROW_NECK_OBSERVATION"
        )
        if layer.rule_enabled(rule_id):
            collector.add(
                rule_id,
                layer.layer_id,
                feature.feature_id,
                evidence=base_evidence,
            )
        return
    if layer.rule_enabled("CLEARANCE_CONNECTIVITY_OK"):
        collector.add(
            "CLEARANCE_CONNECTIVITY_OK",
            layer.layer_id,
            feature.feature_id,
            evidence=base_evidence,
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
