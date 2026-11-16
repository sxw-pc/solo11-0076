"""Pairwise polygon-overlap rule driven by certified three-valued verdicts."""
from __future__ import annotations
from typing import Any, Optional
from .findings import FindingCollector
from .geometry import polygon_bbox, polygon_components, valid_polygon
from .geometry_overlap import (
    VERDICT_CERTIFIED_OVERLAP,
    VERDICT_UNCERTAIN,
    bbox_intersection_margin,
    polygon_overlap_verdict,
)
from .geometry_topology import polygon_topology_issues
from .model import Feature, LayerConfig, Tolerance
from .util import stable_round

def check_polygon_overlaps(
    features: tuple[Feature, ...],
    layer: LayerConfig,
    tolerance: Tolerance,
    collector: FindingCollector,
) -> None:
    overlap_enabled = layer.rule_enabled("POLYGON_OVERLAP")
    review_enabled = layer.rule_enabled("POLYGON_OVERLAP_REVIEW")
    if not overlap_enabled and not review_enabled:
        return
    epsilon = tolerance.coordinate_epsilon
    budget = tolerance.overlap_budget
    entries: list[tuple[Feature, str, Any, tuple[float, float, float, float]]] = []
    for feature in features:
        for label, polygon in polygon_components(feature.geometry):
            if not valid_polygon(polygon, epsilon):
                continue
            bbox = polygon_bbox(polygon)
            if bbox is not None:
                entries.append((feature, label, polygon, bbox))
    issues_cache = [polygon_topology_issues(polygon, epsilon) for _, _, polygon, _ in entries]
    for left in range(len(entries)):
        feature_a, label_a, polygon_a, bbox_a = entries[left]
        for right in range(left + 1, len(entries)):
            feature_b, label_b, polygon_b, bbox_b = entries[right]
            if feature_a.source_index == feature_b.source_index:
                continue
            intersection = bbox_intersection_margin(bbox_a, bbox_b, epsilon)
            if intersection is None:
                continue
            # Canonical operand order keeps verdict evidence (and therefore
            # finding IDs) independent of feature iteration order.
            if feature_a.source_index < feature_b.source_index:
                first, second = feature_a, feature_b
                labels = (label_a, label_b)
                verdict = polygon_overlap_verdict(
                    polygon_a,
                    polygon_b,
                    epsilon,
                    budget,
                    issues_a=issues_cache[left],
                    issues_b=issues_cache[right],
                )
            else:
                first, second = feature_b, feature_a
                labels = (label_b, label_a)
                verdict = polygon_overlap_verdict(
                    polygon_b,
                    polygon_a,
                    epsilon,
                    budget,
                    issues_a=issues_cache[right],
                    issues_b=issues_cache[left],
                )
            pair_evidence = {
                "pair": [first.feature_id, second.feature_id],
                "component_indexes": list(labels),
                "bbox_intersection": [stable_round(value) for value in intersection],
                "verdict": verdict.verdict,
                "budget": {
                    "limit": verdict.budget_limit,
                    "consumed": verdict.budget_consumed,
                },
            }
            if verdict.verdict == VERDICT_CERTIFIED_OVERLAP:
                if not overlap_enabled:
                    continue
                collector.add(
                    "POLYGON_OVERLAP",
                    layer.layer_id,
                    first.feature_id,
                    related_feature_ids=(second.feature_id,),
                    evidence={
                        **pair_evidence,
                        "certification": verdict.reason,
                        "witness": _rounded_point(verdict.witness),
                    },
                )
            elif verdict.verdict == VERDICT_UNCERTAIN:
                if not review_enabled:
                    continue
                collector.add(
                    "POLYGON_OVERLAP_REVIEW",
                    layer.layer_id,
                    first.feature_id,
                    related_feature_ids=(second.feature_id,),
                    evidence={
                        **pair_evidence,
                        "reason": verdict.reason,
                        "locations": verdict.details.get("locations", []),
                        "raw_evidence": {
                            key: value
                            for key, value in verdict.details.items()
                            if key != "locations"
                        },
                    },
                )

def _rounded_point(point: Optional[tuple[float, float]]) -> Optional[list[float]]:
    if point is None:
        return None
    return [stable_round(float(point[0])), stable_round(float(point[1]))]
