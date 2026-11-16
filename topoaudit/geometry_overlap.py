"""Certified three-valued polygon-overlap verdicts with evidence.

The pair conclusion is never a silent boolean:

- ``certified_overlap``: a verified common interior point or a provable
  open overlap region exists (proper boundary crossing, strict vertex
  containment, shared centroid, or a same-side coincident boundary stretch
  confirmed by budgeted refinement).
- ``certified_disjoint``: bounding boxes are stably separated, or the
  boundaries only touch (shared edges/points) with no common interior.
- ``uncertain``: ill-defined ring topology, sub-tolerance contacts, or an
  exhausted refinement budget.  Undecidable locations, budget consumption
  and raw evidence are recorded for manual review.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence
from .geometry import (
    Point,
    Polygon,
    point_in_polygon,
    polygon_bbox,
    polygon_centroid,
    valid_polygon,
)
from .geometry_core import format_point, segments_cross_properly
from .geometry_topology import polygon_topology_issues
from .geometry_witness import (
    boundary_contacts,
    crossing_witness,
    iter_ring_edges,
    ladder_witness,
    refine_stretches,
    segment_intersection_point,
    vertex_bisector,
)

VERDICT_CERTIFIED_OVERLAP = "certified_overlap"
VERDICT_CERTIFIED_DISJOINT = "certified_disjoint"
VERDICT_UNCERTAIN = "uncertain"
OVERLAP_VERDICTS = (
    VERDICT_CERTIFIED_OVERLAP,
    VERDICT_CERTIFIED_DISJOINT,
    VERDICT_UNCERTAIN,
)
# Default number of boundary-refinement witness evaluations allowed per pair.
DEFAULT_OVERLAP_BUDGET = 64

@dataclass(frozen=True)
class OverlapVerdict:
    """Three-valued, evidence-carrying conclusion for a polygon pair."""
    verdict: str
    reason: str
    witness: Optional[Point] = None
    budget_limit: int = 0
    budget_consumed: int = 0
    details: dict[str, Any] = field(default_factory=dict)
    @property
    def is_overlap(self) -> bool:
        return self.verdict == VERDICT_CERTIFIED_OVERLAP

def bboxes_overlap_margin(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    margin: float,
) -> bool:
    return not (
        a[2] < b[0] - margin
        or b[2] < a[0] - margin
        or a[3] < b[1] - margin
        or b[3] < a[1] - margin
    )

def bbox_intersection_margin(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    margin: float,
) -> Optional[list[float]]:
    """Intersection box, or ``None`` when boxes are separated by > ``margin``.

    When boxes are separated by at most ``margin`` the returned box may be
    inverted (min > max); callers treat that as a proximity signal.
    """
    if not bboxes_overlap_margin(a, b, margin):
        return None
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]

def polygon_overlap_verdict(
    a: Polygon,
    b: Polygon,
    epsilon: float = 0.0,
    budget: int = DEFAULT_OVERLAP_BUDGET,
    issues_a: Optional[Sequence[dict[str, Any]]] = None,
    issues_b: Optional[Sequence[dict[str, Any]]] = None,
) -> OverlapVerdict:
    """Three-valued interior-overlap conclusion with locatable evidence."""
    budget = max(0, int(budget))
    if not valid_polygon(a, epsilon) or not valid_polygon(b, epsilon):
        return OverlapVerdict(VERDICT_UNCERTAIN, "invalid_polygon", budget_limit=budget)
    box_a = polygon_bbox(a)
    box_b = polygon_bbox(b)
    if box_a is None or box_b is None:
        return OverlapVerdict(VERDICT_UNCERTAIN, "invalid_polygon", budget_limit=budget)
    if not bboxes_overlap_margin(box_a, box_b, epsilon):
        return OverlapVerdict(VERDICT_CERTIFIED_DISJOINT, "bbox_disjoint", budget_limit=budget)
    if issues_a is None:
        issues_a = polygon_topology_issues(a, epsilon)
    if issues_b is None:
        issues_b = polygon_topology_issues(b, epsilon)
    if issues_a or issues_b:
        return OverlapVerdict(
            VERDICT_UNCERTAIN,
            "topology_undefined",
            budget_limit=budget,
            details={
                "locations": _issue_locations(issues_a, issues_b),
                "issues_a": list(issues_a),
                "issues_b": list(issues_b),
            },
        )
    crossing = _first_proper_crossing(a, b, epsilon)
    if crossing is not None:
        return OverlapVerdict(
            VERDICT_CERTIFIED_OVERLAP,
            "proper_crossing",
            witness=crossing_witness(a, b, crossing, epsilon),
            budget_limit=budget,
            details={
                "crossing": format_point(crossing["point"]),
                "segments": {
                    "ring_a": crossing["ring_a"],
                    "segment_a": crossing["segment_a"],
                    "ring_b": crossing["ring_b"],
                    "segment_b": crossing["segment_b"],
                },
            },
        )
    contained = _first_contained_vertex(a, b, epsilon)
    if contained is not None:
        owner, ring_index, vertex_index, vertex = contained
        ring = (a if owner == "a" else b)[ring_index]
        direction = vertex_bisector(ring, ring_index > 0, vertex_index)
        return OverlapVerdict(
            VERDICT_CERTIFIED_OVERLAP,
            "vertex_containment",
            witness=ladder_witness(vertex, direction, a, b, epsilon),
            budget_limit=budget,
            details={
                "vertex": format_point(vertex),
                "owner": owner,
                "ring_index": ring_index,
            },
        )
    for owner, polygon in (("a", a), ("b", b)):
        centroid = polygon_centroid(polygon)
        if centroid is None:
            continue
        if (
            point_in_polygon(a, centroid, epsilon) == "inside"
            and point_in_polygon(b, centroid, epsilon) == "inside"
        ):
            return OverlapVerdict(
                VERDICT_CERTIFIED_OVERLAP,
                "centroid",
                witness=centroid,
                budget_limit=budget,
                details={"centroid_of": owner},
            )
    stretches, exact_contacts, fuzzy_contacts, unstable_crossings = boundary_contacts(
        a, b, epsilon
    )
    if stretches:
        outcome, consumed, info = refine_stretches(stretches, a, b, epsilon, budget)
        if outcome == "overlap":
            return OverlapVerdict(
                VERDICT_CERTIFIED_OVERLAP,
                "boundary_refinement",
                witness=info["witness"],
                budget_limit=budget,
                budget_consumed=consumed,
                details={"stretch": info["stretch"]},
            )
        if outcome == "budget_exhausted":
            return OverlapVerdict(
                VERDICT_UNCERTAIN,
                "budget_exhausted",
                budget_limit=budget,
                budget_consumed=consumed,
                details={
                    "locations": [format_point(info["location"])],
                    "pending_segments": info["pending"],
                },
            )
        if outcome == "tolerance_unstable":
            return OverlapVerdict(
                VERDICT_UNCERTAIN,
                "tolerance_unstable",
                budget_limit=budget,
                budget_consumed=consumed,
                details={
                    "locations": [format_point(point) for point in info["locations"][:20]],
                },
            )
    if unstable_crossings:
        return OverlapVerdict(
            VERDICT_UNCERTAIN,
            "tolerance_unstable_crossing",
            budget_limit=budget,
            details={
                "locations": [format_point(point) for point in unstable_crossings[:20]],
            },
        )
    if fuzzy_contacts:
        return OverlapVerdict(
            VERDICT_UNCERTAIN,
            "tolerance_unstable_contact",
            budget_limit=budget,
            details={
                "locations": [format_point(point) for point, _ in fuzzy_contacts[:20]],
                "min_distance": min(gap for _, gap in fuzzy_contacts),
            },
        )
    if exact_contacts:
        return OverlapVerdict(
            VERDICT_CERTIFIED_DISJOINT,
            "boundary_touch_only",
            budget_limit=budget,
            details={"contacts": [format_point(point) for point in exact_contacts[:20]]},
        )
    return OverlapVerdict(VERDICT_CERTIFIED_DISJOINT, "no_common_interior", budget_limit=budget)

def _issue_locations(
    issues_a: Sequence[dict[str, Any]], issues_b: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []
    for owner, issues in (("a", issues_a), ("b", issues_b)):
        for issue in issues:
            locations.append(
                {
                    "owner": owner,
                    "issue": issue["issue"],
                    "ring_index": issue.get("ring_index", issue.get("ring_indexes")),
                }
            )
    return locations[:20]

def _first_proper_crossing(a: Polygon, b: Polygon, epsilon: float) -> Optional[dict[str, Any]]:
    for ring_a_index, ring_a in enumerate(a):
        for ring_b_index, ring_b in enumerate(b):
            for i, p1, p2 in iter_ring_edges(ring_a):
                for j, q1, q2 in iter_ring_edges(ring_b):
                    if segments_cross_properly(p1, p2, q1, q2, epsilon):
                        return {
                            "ring_a": ring_a_index,
                            "segment_a": i,
                            "ring_b": ring_b_index,
                            "segment_b": j,
                            "p1": p1,
                            "p2": p2,
                            "q1": q1,
                            "q2": q2,
                            "point": segment_intersection_point(p1, p2, q1, q2),
                        }
    return None

def _first_contained_vertex(
    a: Polygon, b: Polygon, epsilon: float
) -> Optional[tuple[str, int, int, Point]]:
    for owner, source, target in (("a", a, b), ("b", b, a)):
        for ring_index, ring in enumerate(source):
            for vertex_index, vertex in enumerate(ring[:-1]):
                if point_in_polygon(target, vertex, epsilon) == "inside":
                    return (owner, ring_index, vertex_index, vertex)
    return None
