"""Layer-level topology checks: identities, overlaps, and line endpoints."""
from __future__ import annotations
from typing import Any, Optional
from .findings import FindingCollector
from .geometry import (
    distance,
    format_point,
    is_finite_point,
    line_components,
    points_close,
)
from .model import Feature, LayerConfig, Tolerance
from .rule_overlap import check_polygon_overlaps
from .normalize import canonical_geometry
from .util import canonical_json, sha256_text, stable_round

def check_layer_topology(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    ordered = tuple(sorted(features, key=lambda item: item.source_index))
    _check_duplicate_ids(ordered, layer, collector)
    _check_duplicate_geometries(ordered, layer, tolerance, collector)
    check_polygon_overlaps(ordered, layer, tolerance, collector)
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
    records = _endpoint_records(features, layer, tolerance)
    exact_connected = [False] * len(records)
    near_pairs: list[tuple[int, int, float]] = []
    for left in range(len(records)):
        first = records[left]
        for right in range(left + 1, len(records)):
            second = records[right]
            if first["source_index"] == second["source_index"]:
                continue
            gap = distance(first["point"], second["point"])
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
                first["feature_id"],
                related_feature_ids=(second["feature_id"],),
                evidence={
                    "endpoint_a": _endpoint_evidence(first),
                    "endpoint_b": _endpoint_evidence(second),
                    "distance": stable_round(gap),
                    "tolerance": tolerance.near_miss_distance,
                },
            )
    if dangling_enabled:
        for index, record in enumerate(records):
            if exact_connected[index]:
                continue
            nearest: Optional[float] = None
            nearest_feature: Optional[str] = None
            for other_index, other in enumerate(records):
                if other_index == index or other["source_index"] == record["source_index"]:
                    continue
                gap = distance(record["point"], other["point"])
                if nearest is None or gap < nearest:
                    nearest = gap
                    nearest_feature = other["feature_id"]
            collector.add(
                "DANGLING_ENDPOINT",
                layer.layer_id,
                record["feature_id"],
                related_feature_ids=(nearest_feature,) if nearest_feature else (),
                evidence={
                    "part_index": record["part"],
                    "endpoint": record["side"],
                    "coordinate": format_point(record["point"]),
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
    records: list[dict[str, Any]] = []
    for feature in features:
        if feature.geometry.kind not in layer.geometry_types:
            continue
        for label, line in line_components(feature.geometry):
            if len(line) < 2 or not all(is_finite_point(point) for point in line):
                continue
            if points_close(line[0], line[-1], tolerance.coordinate_epsilon):
                continue
            for side, point in (("start", line[0]), ("end", line[-1])):
                records.append(
                    {
                        "feature_id": feature.feature_id,
                        "source_index": feature.source_index,
                        "part": label,
                        "side": side,
                        "point": point,
                    }
                )
    return records

def _endpoint_evidence(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature_id": record["feature_id"],
        "source_index": record["source_index"],
        "part_index": record["part"],
        "endpoint": record["side"],
        "coordinate": format_point(record["point"]),
    }

def _id_value(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None
