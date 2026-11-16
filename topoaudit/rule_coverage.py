"""Whole-line coverage audit against a declared polygon union.

The audit splits each line segment at every polygon-ring intersection and
classifies each resulting slice against the union of *valid* polygons.  An
invalid ring (unclosed, degenerate, self-intersecting, or declared under a
conflicting CRS) never contributes an inside result, and undetermined
length is never silently counted as covered.  Input geometry is never
modified: every slice keeps its original start/end coordinates.
"""
from __future__ import annotations
from typing import Any, Optional
from .findings import FindingCollector
from .geometry import line_components
from .coverage_slices import zero_lengths
from .model import (
    COVERAGE_STATUSES,
    LINE_GEOMETRY_TYPES,
    CoverageRuleConfig,
    CRS,
    Feature,
    LoadedLayer,
    Tolerance,
)
from .util import stable_round
COVERAGE_RULE_ID = "LINE_COVERAGE"

from .coverage_segments import coverage_segment
from .coverage_sources import (
    CoverageTally,
    boxes_near,
    declared_crs_reason,
    invalid_source_evidence,
    line_bbox,
    polygon_sources,
)

from .coverage_summary import summarize
_DEFAULT_TOLERANCE = Tolerance()

def check_line_coverage(
    rule: CoverageRuleConfig,
    line_layer: LoadedLayer,
    polygon_layer: LoadedLayer,
    global_crs: CRS,
    collector: FindingCollector,
) -> dict[str, Any]:
    """Audit every line component of ``line_layer`` against ``polygon_layer``.

    The whole line is split at every polygon-ring intersection, and each
    resulting slice is classified against the union of valid polygons.
    Length is accumulated once per slice, so overlapping or edge-sharing
    polygons never double-count.  Each slice keeps the original line
    coordinates and its part/segment index as reviewable evidence.

    Returns a rule-level coverage section.  A ``LINE_COVERAGE`` finding is
    emitted for every line whose status is ``partial``, ``uncovered`` or
    ``undetermined``; ``covered`` and ``empty`` lines are recorded but not
    reported as defects.
    """
    line_crs_reason = declared_crs_reason(line_layer.config, global_crs)
    polygon_crs_reason = declared_crs_reason(polygon_layer.config, global_crs)
    # Geometry validity and on-ring predicates use the layer's coordinate
    # epsilon, not the coverage tolerance band: a real 0.4-unit gap must
    # never be treated as an exact boundary hit.
    coordinate_epsilon = polygon_layer.config.effective_tolerance(
        _DEFAULT_TOLERANCE
    ).coordinate_epsilon
    sources = polygon_sources(
        polygon_layer,
        coordinate_epsilon,
        extra_issue=polygon_crs_reason,
    )
    valid_polygons = tuple(source.polygon for source in sources if source.valid)
    rings = tuple(ring for polygon in valid_polygons for ring in polygon)
    invalid_sources = tuple(source for source in sources if not source.valid)

    records: list[dict[str, Any]] = []
    for feature in sorted(line_layer.features, key=lambda item: item.source_index):
        record = _coverage_record(
            rule,
            feature,
            valid_polygons,
            rings,
            invalid_sources,
            line_crs_reason,
            polygon_crs_reason,
            coordinate_epsilon,
        )
        records.append(record)
        if record["status"] in ("partial", "uncovered", "undetermined"):
            _emit_coverage_finding(
                rule,
                feature,
                record,
                polygon_crs_reason,
                collector,
            )
    status_counts = {status: 0 for status in COVERAGE_STATUSES}
    for record in records:
        status_counts[record["status"]] += 1
    return {
        "rule_id": rule.rule_id,
        "line_layer": line_layer.config.layer_id,
        "polygon_layer": polygon_layer.config.layer_id,
        "boundary_mode": rule.boundary_mode,
        "tolerance": rule.tolerance,
        "status_counts": status_counts,
        "coordinate_epsilon": coordinate_epsilon,
        "invalid_polygon_sources": invalid_source_evidence(invalid_sources),
        "line_layer_crs_reason": line_crs_reason,
        "polygon_layer_crs_reason": polygon_crs_reason,
        "lines": records,
    }

def _coverage_record(
    rule: CoverageRuleConfig,
    feature: Feature,
    valid_polygons: tuple[Polygon, ...],
    rings: tuple[Ring, ...],
    invalid_sources: tuple[PolygonSource, ...],
    line_crs_reason: Optional[str],
    polygon_crs_reason: Optional[str],
    coordinate_epsilon: float,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "feature_id": feature.feature_id,
        "source_index": feature.source_index,
        "geometry_type": feature.geometry.kind,
    }
    if feature.geometry.kind not in LINE_GEOMETRY_TYPES:
        base.update(
            {
                "status": "undetermined",
                "total_length": 0.0,
                "covered_length": 0.0,
                "uncovered_length": 0.0,
                "undetermined_length": 0.0,
                "lengths": zero_lengths(),
                "uncovered_slices": [],
                "segments": [],
                "reasons": ["unsupported_line_geometry"],
                "invalid_polygon_sources": [],
            }
        )
        return base

    feature_bbox = line_bbox(feature)
    relevant_invalid = tuple(
        source
        for source in invalid_sources
        if source.bbox is not None
        and feature_bbox is not None
        and boxes_near(source.bbox, feature_bbox, rule.tolerance)
    )
    invalid_boxes = tuple(
        source.bbox for source in relevant_invalid if source.bbox is not None
    )
    if polygon_crs_reason is not None:
        # The CRS conflict is reported once for the whole rule; do not
        # also attribute slices to a geometric-invalidity reason.
        invalid_boxes = ()
    tally = CoverageTally()
    segments: list[dict[str, Any]] = []
    reasons: set[str] = set()
    for part_label, line in line_components(feature.geometry):
        for index in range(len(line) - 1):
            segment = coverage_segment(
                rule,
                part_label,
                index,
                line[index],
                line[index + 1],
                valid_polygons,
                rings,
                invalid_boxes,
                tally,
                reasons,
                coordinate_epsilon,
            )
            if segment is not None:
                segments.append(segment)
    crs_reason = line_crs_reason or polygon_crs_reason
    summary = summarize(
        tally, segments, rule.boundary_mode, crs_reason, reasons
    )
    base.update(
        {
            "status": summary["status"],
            "total_length": stable_round(summary["total_length"]),
            "covered_length": stable_round(summary["covered_length"]),
            "uncovered_length": stable_round(summary["uncovered_length"]),
            "undetermined_length": stable_round(summary["undetermined_length"]),
            "lengths": {
                name: stable_round(value)
                for name, value in summary["lengths"].items()
            },
            "uncovered_slices": summary["uncovered_slices"],
            "segments": segments,
            "reasons": sorted(reasons),
            "invalid_polygon_sources": invalid_source_evidence(relevant_invalid),
        }
    )
    return base

def _emit_coverage_finding(
    rule: CoverageRuleConfig,
    feature: Feature,
    record: dict[str, Any],
    polygon_crs_reason: Optional[str],
    collector: FindingCollector,
) -> None:
    slices = record["uncovered_slices"]
    evidence: dict[str, Any] = {
        "rule_id": rule.rule_id,
        "line_layer": feature.layer_id,
        "polygon_layer": rule.polygon_layer_id,
        "boundary_mode": rule.boundary_mode,
        "status": record["status"],
        "source_index": feature.source_index,
        "total_length": record["total_length"],
        "covered_length": record["covered_length"],
        "uncovered_length": record["uncovered_length"],
        "undetermined_length": record["undetermined_length"],
        "lengths": record["lengths"],
        "uncovered_slices": slices[:20],
        "uncovered_slice_count": len(slices),
        "reasons": record["reasons"],
        "tolerance": rule.tolerance,
    }
    if record["invalid_polygon_sources"]:
        evidence["invalid_polygon_sources"] = record["invalid_polygon_sources"]
    if polygon_crs_reason is not None:
        evidence["polygon_layer_crs_reason"] = polygon_crs_reason
    collector.add(
        "LINE_COVERAGE",
        feature.layer_id,
        feature.feature_id,
        evidence=evidence,
    )
