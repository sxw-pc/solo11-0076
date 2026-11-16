"""Turn per-slice tallies into a line-coverage summary.

Pure functions shared by the coverage audit: boundary-mode accounting,
CRS-driven undetermined relabelling, and final status selection.
"""
from __future__ import annotations
from typing import Any
from .coverage_slices import (
    SLICE_BOUNDARY_HOLE,
    SLICE_BOUNDARY_OUTER,
    SLICE_EXTERIOR,
    SLICE_INTERIOR,
    SLICE_KINDS,
    SLICE_TOLERANCE_EXTERIOR,
    SLICE_UNDETERMINED,
    coverage_status,
    mark_all_undetermined,
    uncovered_slices,
)

def summarize(
    tally: Any,
    segments: list[dict[str, Any]],
    boundary_mode: str,
    crs_reason: str | None,
    reasons: set[str],
) -> dict[str, Any]:
    total = sum(tally.lengths.values())
    accepted = tally.lengths[SLICE_INTERIOR]
    if boundary_mode == "boundary_allowed":
        accepted += tally.lengths[SLICE_BOUNDARY_OUTER]
        accepted += tally.lengths[SLICE_BOUNDARY_HOLE]
    uncovered = tally.lengths[SLICE_EXTERIOR] + tally.lengths[SLICE_TOLERANCE_EXTERIOR]
    if boundary_mode == "strict_interior":
        uncovered += tally.lengths[SLICE_BOUNDARY_OUTER]
        uncovered += tally.lengths[SLICE_BOUNDARY_HOLE]
    undetermined = tally.lengths[SLICE_UNDETERMINED]
    if crs_reason is not None:
        # One side of the comparison declares a conflicting CRS, so neither
        # "covered" nor "uncovered" is justified.  Relabel every slice so the
        # summary and the coordinate evidence tell the same story.
        mark_all_undetermined(segments, crs_reason)
        for kind in SLICE_KINDS:
            tally.lengths[kind] = 0.0
        tally.lengths[SLICE_UNDETERMINED] = total
        undetermined = total
        accepted = 0.0
        uncovered = 0.0
        reasons.add(crs_reason)
    status = coverage_status(
        total,
        accepted,
        uncovered,
        undetermined,
        segments,
        tally.counts[SLICE_UNDETERMINED],
    )
    return {
        "status": status,
        "total_length": total,
        "covered_length": accepted,
        "uncovered_length": uncovered,
        "undetermined_length": undetermined,
        "lengths": dict(tally.lengths),
        "uncovered_slices": uncovered_slices(segments, boundary_mode),
    }
