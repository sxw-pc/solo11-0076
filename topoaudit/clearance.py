"""Exact, continuous-plane orthogonal clearance connectivity audit.

The audit erodes a provably valid axis-aligned polygon by half of the
declared ``clearance_width`` and inspects the positive-area connected
components of the surviving region.  It never modifies the source face and
never uses fixed-grid raster sampling: the arrangement is built from the
polygon's own edge coordinates and grid edges/vertices are tested exactly so
that a thin bridge cannot be missed.

Two usage contracts share one evidence trail:

* ``land_compactness`` keeps the global area/perimeter ratio as the only
  delivery gate; a neck that splits cores after erosion is reported as an
  explicit observation, never as a block.
* ``access_clearance`` additionally requires every surviving component whose
  area is at least ``min_core_area`` to stay mutually connected; otherwise the
  face is blocked.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional, Sequence
from .geometry import (
    Point,
    clearance_cells,
    format_point,
    polygon_area,
    polygon_perimeter,
    provably_valid_orthogonal_polygon,
)
from .util import stable_round

STATUS_PASS = "pass"
STATUS_BLOCKED = "blocking_narrow_neck"
STATUS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Constriction:
    """Boundary evidence of a narrow passage."""
    width: float
    witness: Point
    segment_a: tuple[Point, Point]
    segment_b: tuple[Point, Point]
    ring_indexes: tuple[int, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "clear_width": stable_round(self.width),
            "witness_point": format_point(self.witness),
            "boundary_segment_a": [format_point(self.segment_a[0]), format_point(self.segment_a[1])],
            "boundary_segment_b": [format_point(self.segment_b[0]), format_point(self.segment_b[1])],
            "ring_indexes": list(self.ring_indexes),
        }


@dataclass(frozen=True)
class CoreEvidence:
    component_index: int
    area: float
    bbox: tuple[float, float, float, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_index": self.component_index,
            "area": stable_round(self.area),
            "bbox": [stable_round(value) for value in self.bbox],
        }


@dataclass(frozen=True)
class PartClearanceResult:
    part_index: str
    status: str
    reason: Optional[str]
    area: float
    perimeter: float
    area_perimeter_ratio: float
    originally_connected: bool
    clear_components: tuple[CoreEvidence, ...]
    cores: tuple[CoreEvidence, ...]
    fragments: tuple[CoreEvidence, ...]
    disconnected_core_pairs: tuple[tuple[int, int], ...]
    candidate_neck: Optional[dict[str, Any]]
    constrictions: tuple[Constriction, ...]
    half_width: float

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "part_index": self.part_index,
            "status": self.status,
            "area": stable_round(self.area),
            "perimeter": stable_round(self.perimeter),
            "area_perimeter_ratio": stable_round(self.area_perimeter_ratio),
            "erosion_half_width": stable_round(self.half_width),
            "originally_connected_inside_face": self.originally_connected,
            "clear_component_count": len(self.clear_components),
            "clear_components": [item.as_dict() for item in self.clear_components],
            "core_count": len(self.cores),
            "cores": [item.as_dict() for item in self.cores],
            "sub_core_fragments": [item.as_dict() for item in self.fragments],
            "disconnected_core_pairs": [list(pair) for pair in self.disconnected_core_pairs],
            "constriction_evidence": [item.as_dict() for item in self.constrictions],
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.candidate_neck is not None:
            payload["candidate_neck"] = self.candidate_neck
        return payload


@dataclass(frozen=True)
class ClearanceAudit:
    status: str
    reason: Optional[str]
    clearance_width: float
    min_core_area: float
    usage_contract: str
    parts: tuple[PartClearanceResult, ...]
    input_summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "clearance_width": stable_round(self.clearance_width),
            "min_core_area": stable_round(self.min_core_area),
            "usage_contract": self.usage_contract,
            "parts": [part.as_dict() for part in self.parts],
            "input_summary": self.input_summary,
        }


def audit_polygon_part(
    part_index: str,
    polygon: Sequence[Sequence[Point]],
    clearance_width: float,
    min_core_area: float,
    epsilon: float,
) -> PartClearanceResult:
    """Run the exact clearance decision for one polygon part (read-only)."""
    area = polygon_area(polygon)
    perimeter = polygon_perimeter(polygon)
    ratio = area / perimeter if perimeter > 0 else 0.0
    half_width = clearance_width / 2.0
    valid, reason = provably_valid_orthogonal_polygon(polygon, epsilon)
    if not valid:
        return PartClearanceResult(
            part_index=part_index,
            status=STATUS_UNKNOWN,
            reason=reason,
            area=area,
            perimeter=perimeter,
            area_perimeter_ratio=ratio,
            originally_connected=False,
            clear_components=(),
            cores=(),
            fragments=(),
            disconnected_core_pairs=(),
            candidate_neck=None,
            constrictions=(),
            half_width=half_width,
        )
    arrangement = clearance_cells(polygon, half_width, epsilon)
    if arrangement is None:  # pragma: no cover - validity gate already proved
        return PartClearanceResult(
            part_index=part_index,
            status=STATUS_UNKNOWN,
            reason="arrangement_construction_failed",
            area=area,
            perimeter=perimeter,
            area_perimeter_ratio=ratio,
            originally_connected=False,
            clear_components=(),
            cores=(),
            fragments=(),
            disconnected_core_pairs=(),
            candidate_neck=None,
            constrictions=(),
            half_width=half_width,
        )
    raw_components = arrangement.connected_components()
    components = tuple(
        CoreEvidence(
            component_index=index,
            area=component.area,
            bbox=component.bbox,
        )
        for index, component in enumerate(raw_components)
    )
    cores = tuple(item for item in components if item.area + epsilon >= min_core_area)
    fragments = tuple(item for item in components if item.area + epsilon < min_core_area)
    disconnected_pairs: list[tuple[int, int]] = []
    for left in range(len(cores)):
        for right in range(left + 1, len(cores)):
            disconnected_pairs.append(
                (cores[left].component_index, cores[right].component_index)
            )
    constrictions = _find_constrictions(polygon, half_width, epsilon)
    candidate_neck = _candidate_neck(cores, constrictions, epsilon)
    # A simple valid single polygon has one connected interior by
    # construction; the gate above rejects self-touching rings.
    originally_connected = True
    status = STATUS_BLOCKED if disconnected_pairs else STATUS_PASS
    return PartClearanceResult(
        part_index=part_index,
        status=status,
        reason=None,
        area=area,
        perimeter=perimeter,
        area_perimeter_ratio=ratio,
        originally_connected=originally_connected,
        clear_components=components,
        cores=cores,
        fragments=fragments,
        disconnected_core_pairs=tuple(disconnected_pairs),
        candidate_neck=candidate_neck,
        constrictions=constrictions,
        half_width=half_width,
    )


def audit_feature_clearance(
    polygons: Sequence[tuple[str, Sequence[Sequence[Point]]]],
    clearance_width: float,
    min_core_area: float,
    usage_contract: str,
    epsilon: float,
) -> ClearanceAudit:
    """Audit every polygon part independently.

    Distinct ``MultiPolygon`` parts are never unioned: a separation that
    pre-exists in the source face is not attributed to clearance erosion.
    """
    if clearance_width <= epsilon:
        return ClearanceAudit(
            status=STATUS_UNKNOWN,
            reason="clearance_width_not_above_coordinate_epsilon",
            clearance_width=clearance_width,
            min_core_area=min_core_area,
            usage_contract=usage_contract,
            parts=(),
            input_summary={
                "part_count": len(polygons),
                "coordinate_epsilon": stable_round(epsilon),
            },
        )
    parts = tuple(
        audit_polygon_part(
            part_index, polygon, clearance_width, min_core_area, epsilon
        )
        for part_index, polygon in polygons
    )
    if any(part.status == STATUS_UNKNOWN for part in parts):
        status = STATUS_UNKNOWN
        reason = "one_or_more_parts_unsupported"
    elif any(part.status == STATUS_BLOCKED for part in parts):
        status = STATUS_BLOCKED
        reason = None
    else:
        status = STATUS_PASS
        reason = None
    input_summary = {
        "part_count": len(polygons),
        "coordinate_epsilon": stable_round(epsilon),
        "ring_counts": [len(polygon) for _, polygon in polygons],
        "global_area": stable_round(sum(part.area for part in parts)),
        "global_perimeter": stable_round(sum(part.perimeter for part in parts)),
    }
    return ClearanceAudit(
        status=status,
        reason=reason,
        clearance_width=clearance_width,
        min_core_area=min_core_area,
        usage_contract=usage_contract,
        parts=parts,
        input_summary=input_summary,
    )


def _find_constrictions(
    polygon: Sequence[Sequence[Point]],
    half_width: float,
    epsilon: float,
    limit: int = 20,
) -> tuple[Constriction, ...]:
    """Find inward-facing parallel boundary edge pairs closer than 2*h.

    Hole edges and outer edges participate on equal footing because both
    constrain the clearance.  A midpoint witness strictly inside the face
    proves the pair bounds a real passage rather than unrelated edges.
    """
    edges: list[tuple[int, Point, Point]] = []
    for ring_index, ring in enumerate(polygon):
        for index in range(len(ring) - 1):
            edges.append((ring_index, ring[index], ring[index + 1]))
    found: list[Constriction] = []
    for left in range(len(edges)):
        ring_a, a1, a2 = edges[left]
        for right in range(left + 1, len(edges)):
            ring_b, b1, b2 = edges[right]
            constriction = _constriction_between(
                (ring_a, a1, a2), (ring_b, b1, b2), polygon, half_width, epsilon
            )
            if constriction is not None:
                found.append(constriction)
    found.sort(key=lambda item: (item.width, item.ring_indexes, item.witness))
    return tuple(found[:limit])


def _constriction_between(
    edge_a: tuple[int, Point, Point],
    edge_b: tuple[int, Point, Point],
    polygon: Sequence[Sequence[Point]],
    half_width: float,
    epsilon: float,
) -> Optional[Constriction]:
    ring_a, a1, a2 = edge_a
    ring_b, b1, b2 = edge_b
    horizontal_a = abs(float(a1[1]) - float(a2[1])) <= epsilon
    horizontal_b = abs(float(b1[1]) - float(b2[1])) <= epsilon
    if horizontal_a != horizontal_b:
        return None
    if horizontal_a:
        gap = abs(float(a1[1]) - float(b1[1]))
        if gap <= epsilon or gap >= 2.0 * half_width - epsilon:
            return None
        overlap_min = max(min(a1[0], a2[0]), min(b1[0], b2[0]))
        overlap_max = min(max(a1[0], a2[0]), max(b1[0], b2[0]))
        if overlap_max - overlap_min <= epsilon:
            return None
        witness = ((overlap_min + overlap_max) / 2.0, (float(a1[1]) + float(b1[1])) / 2.0)
    else:
        gap = abs(float(a1[0]) - float(b1[0]))
        if gap <= epsilon or gap >= 2.0 * half_width - epsilon:
            return None
        overlap_min = max(min(a1[1], a2[1]), min(b1[1], b2[1]))
        overlap_max = min(max(a1[1], a2[1]), max(b1[1], b2[1]))
        if overlap_max - overlap_min <= epsilon:
            return None
        witness = ((float(a1[0]) + float(b1[0])) / 2.0, (overlap_min + overlap_max) / 2.0)
    from .geometry import point_in_polygon
    if point_in_polygon(polygon, witness, epsilon) != "inside":
        return None
    return Constriction(
        width=gap,
        witness=witness,
        segment_a=(a1, a2),
        segment_b=(b1, b2),
        ring_indexes=tuple(sorted((ring_a, ring_b))),
    )


def _candidate_neck(
    cores: Sequence[CoreEvidence],
    constrictions: Sequence[Constriction],
    epsilon: float,
) -> Optional[dict[str, Any]]:
    if len(cores) < 2:
        return None
    nearest: Optional[tuple[float, Point, tuple[int, int]]] = None
    for left in range(len(cores)):
        for right in range(left + 1, len(cores)):
            gap, witness = _bbox_gap(cores[left].bbox, cores[right].bbox)
            pair = (cores[left].component_index, cores[right].component_index)
            if nearest is None or gap < nearest[0]:
                nearest = (gap, witness, pair)
    if nearest is None:  # pragma: no cover - len(cores) >= 2 guarantees entries
        return None
    payload: dict[str, Any] = {
        "core_pair": list(nearest[2]),
        "gap_between_core_bboxes": stable_round(nearest[0]),
        "gap_witness_point": format_point(nearest[1]),
    }
    if constrictions:
        narrowest = min(constrictions, key=lambda item: item.width)
        payload["narrowest_constriction"] = narrowest.as_dict()
    return payload


def _bbox_gap(
    box_a: tuple[float, float, float, float],
    box_b: tuple[float, float, float, float],
) -> tuple[float, Point]:
    ax0, ay0, ax1, ay1 = box_a
    bx0, by0, bx1, by1 = box_b
    gap_x = max(0.0, max(bx0 - ax1, ax0 - bx1))
    gap_y = max(0.0, max(by0 - ay1, ay0 - by1))
    if gap_x >= gap_y and gap_x > 0.0:
        if bx0 > ax1:
            witness = (ax1, (max(ay0, by0) + min(ay1, by1)) / 2.0)
        else:
            witness = (bx1, (max(ay0, by0) + min(ay1, by1)) / 2.0)
        return gap_x, witness
    if gap_y > 0.0:
        if by0 > ay1:
            witness = ((max(ax0, bx0) + min(ax1, bx1)) / 2.0, ay1)
        else:
            witness = ((max(ax0, bx0) + min(ax1, bx1)) / 2.0, by1)
        return gap_y, witness
    return 0.0, ((ax1 + bx0) / 2.0, (ay1 + by0) / 2.0)
