"""Layer-level topology checks: identities, overlaps, and line endpoints."""
from __future__ import annotations
from typing import Any, Optional
from .endpoint_index import EndpointIndex, EndpointRecord, build_endpoint_index
from .findings import FindingCollector
from .geometry import (
    bbox_intersection,
    distance,
    format_point,
    polygon_bbox,
    polygon_components,
    polygons_overlap,
    valid_polygon,
)
from .model import Feature, LayerConfig, Tolerance
from .normalize import canonical_geometry
from .util import canonical_json, sha256_text, stable_round

__all__ = [
    "EndpointIndex",
    "EndpointRecord",
    "build_endpoint_index",
    "check_layer_topology",
]


def check_layer_topology(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    ordered = tuple(sorted(features, key=lambda item: item.source_index))
    _check_duplicate_ids(ordered, layer, collector)
    _check_duplicate_geometries(ordered, layer, tolerance, collector)
    _check_polygon_overlaps(ordered, layer, tolerance, collector)
    _check_line_endpoints(ordered, layer, tolerance, collector)

def _check_duplicate_ids(
    features: tuple[Feature, ...], layer: LayerConfig, collector: FindingCollector
) -> None:
    if not layer.rule_enabled("DUPLICATE_FEATURE_ID"):
        return
    groups: dict[str, list[Feature]] = {}
    for feature in features:
        value = _id_value(feature.properties.get(layer.id_field))
        if value is not None:
            groups.setdefault(value, []).append(feature)
    for value in sorted(groups):
        group = groups[value]
        if len(group) < 2:
            continue
        first = group[0]
        for duplicate in group[1:]:
            collector.add(
                "DUPLICATE_FEATURE_ID",
                layer.layer_id,
                duplicate.feature_id,
                related_feature_ids=(first.feature_id,),
                evidence={
                    "id_field": layer.id_field,
                    "value": value,
                    "first_source_index": first.source_index,
                    "duplicate_source_index": duplicate.source_index,
                },
            )

def _check_duplicate_geometries(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    if not layer.rule_enabled("DUPLICATE_GEOMETRY"):
        return
    seen: dict[str, Feature] = {}
    for feature in features:
        canonical = canonical_geometry(feature.geometry, tolerance.coordinate_epsilon)
        if canonical is None:
            continue
        signature = canonical_json(canonical)
        if signature not in seen:
            seen[signature] = feature
            continue
        first = seen[signature]
        collector.add(
            "DUPLICATE_GEOMETRY",
            layer.layer_id,
            feature.feature_id,
            related_feature_ids=(first.feature_id,),
            evidence={
                "geometry_digest": sha256_text(signature)[:16],
                "first_source_index": first.source_index,
                "duplicate_source_index": feature.source_index,
                "coordinate_epsilon": tolerance.coordinate_epsilon,
            },
        )

def _check_polygon_overlaps(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    if not layer.rule_enabled("POLYGON_OVERLAP"):
        return
    entries: list[tuple[Feature, str, Any, tuple[float, float, float, float]]] = []
    for feature in features:
        for label, polygon in polygon_components(feature.geometry):
            if not valid_polygon(polygon, tolerance.coordinate_epsilon):
                continue
            bbox = polygon_bbox(polygon)
            if bbox is not None:
                entries.append((feature, label, polygon, bbox))
    for left in range(len(entries)):
        feature_a, label_a, polygon_a, bbox_a = entries[left]
        for right in range(left + 1, len(entries)):
            feature_b, label_b, polygon_b, bbox_b = entries[right]
            if feature_a.source_index == feature_b.source_index:
                continue
            intersection = bbox_intersection(bbox_a, bbox_b)
            if intersection is None:
                continue
            if not polygons_overlap(polygon_a, polygon_b, tolerance.coordinate_epsilon):
                continue
            first, second = sorted(
                (feature_a, feature_b), key=lambda item: item.source_index
            )
            collector.add(
                "POLYGON_OVERLAP",
                layer.layer_id,
                first.feature_id,
                related_feature_ids=(second.feature_id,),
                evidence={
                    "pair": [first.feature_id, second.feature_id],
                    "component_indexes": [label_a, label_b],
                    "bbox_intersection": [stable_round(value) for value in intersection],
                },
            )

def _check_line_endpoints(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    dangling_enabled = layer.rule_enabled("DANGLING_ENDPOINT")
    near_enabled = layer.rule_enabled("NEAR_MISS_ENDPOINT")
    if not dangling_enabled and not near_enabled:
        return
    index = build_endpoint_index(features, layer, tolerance)
    records = index.records
    exact_connected = [False] * len(records)
    near_pairs: list[tuple[int, int, float]] = []
    for left in range(len(records)):
        first = records[left]
        for right in range(left + 1, len(records)):
            second = records[right]
            if first.source_index == second.source_index:
                continue
            gap = index.gap(left, right)
            if gap <= tolerance.coordinate_epsilon:
                exact_connected[left] = True
                exact_connected[right] = True
            elif gap <= tolerance.near_miss_distance:
                near_pairs.append((left, right, gap))
    if near_enabled:
        for left, right, gap in near_pairs:
            first, second = records[left], records[right]
            collector.add(
                "NEAR_MISS_ENDPOINT",
                layer.layer_id,
                first.ref.feature_id,
                related_feature_ids=(second.ref.feature_id,),
                evidence={
                    "endpoint_a": _endpoint_evidence(first),
                    "endpoint_b": _endpoint_evidence(second),
                    "distance": stable_round(gap),
                    "tolerance": tolerance.near_miss_distance,
                },
            )
    if dangling_enabled:
        for record_index, record in enumerate(records):
            if exact_connected[record_index]:
                continue
            nearest: Optional[float] = None
            nearest_feature: Optional[str] = None
            for other_index, other in enumerate(records):
                if other_index == record_index or other.source_index == record.source_index:
                    continue
                gap = index.gap(record_index, other_index)
                if nearest is None or gap < nearest:
                    nearest = gap
                    nearest_feature = other.ref.feature_id
            collector.add(
                "DANGLING_ENDPOINT",
                layer.layer_id,
                record.ref.feature_id,
                related_feature_ids=(nearest_feature,) if nearest_feature else (),
                evidence={
                    "part_index": record.ref.part_index,
                    "endpoint": record.ref.side,
                    "coordinate": format_point(record.point),
                    "nearest_feature_id": nearest_feature,
                    "nearest_distance": (
                        stable_round(nearest) if nearest is not None else None
                    ),
                    "tolerance": tolerance.coordinate_epsilon,
                },
            )

def _endpoint_records(
    features: tuple[Feature, ...], layer: LayerConfig, tolerance: Tolerance
) -> list[dict[str, Any]]:
    """Legacy dict-shaped endpoint records; new code uses EndpointIndex."""
    index = build_endpoint_index(features, layer, tolerance)
    return [record.legacy_dict() for record in index.records]

def _endpoint_evidence(record: EndpointRecord) -> dict[str, Any]:
    return {
        "feature_id": record.ref.feature_id,
        "source_index": record.source_index,
        "part_index": record.ref.part_index,
        "endpoint": record.ref.side,
        "coordinate": format_point(record.point),
    }

def _id_value(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None
