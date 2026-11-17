"""Plan construction, unassigned reasons, and the infeasibility witness."""
from __future__ import annotations
from typing import Any
from .assignment_flow import FlowNetwork, reachable_from
from .util import stable_round

# Unassigned reason codes (part of the public result).
NO_CANDIDATE = "no_candidate_zone"
BOUNDARY_REVIEW = "boundary_only_requires_review"
CAPACITY_BLOCKED = "capacity_blocked"


def build_plan(
    objective: str,
    network: FlowNetwork,
    eligible_points: list[dict[str, Any]],
    valid_zones: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    all_points: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    review_pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Read one solved network into a plan with assignments and metrics."""
    point_by_node = {i + 1: record for i, record in enumerate(eligible_points)}
    zone_by_node = {
        len(eligible_points) + 1 + i: record for i, record in enumerate(valid_zones)
    }
    candidate_lookup = {
        (item["point_id"], item["zone_id"]): item for item in candidates
    }
    assignments: list[dict[str, Any]] = []
    assigned_ids: set[str] = set()
    loads: dict[str, int] = {record["zone_id"]: 0 for record in valid_zones}
    total_distance = 0.0
    for edge in edges:
        ref = edge.get("ref")
        if ref is None or ref[2] != 0:  # residual cap 0 == the unit is used
            continue
        point = point_by_node[edge["point_node"]]
        zone = zone_by_node[edge["zone_node"]]
        candidate = candidate_lookup[(point["point_id"], zone["zone_id"])]
        assignments.append(
            {
                "point_id": point["point_id"],
                "zone_id": zone["zone_id"],
                "relation": candidate["relation"],
                "component": candidate["component"],
                "distance": candidate["distance"],
            }
        )
        assigned_ids.add(point["point_id"])
        loads[zone["zone_id"]] += 1
        total_distance += candidate["distance"]
    assignments.sort(key=lambda item: item["point_id"])
    unassigned = unassigned_points(all_points, assigned_ids, candidates, review_pairs)
    load_values = list(loads.values())
    metrics = {
        "assigned": len(assignments),
        "unassigned": len(unassigned),
        "total_distance": stable_round(total_distance),
        "zone_loads": {key: loads[key] for key in sorted(loads)},
        "max_load": max(load_values) if load_values else 0,
        "load_spread": (max(load_values) - min(load_values)) if load_values else 0,
        "load_sum_squares": sum(value * value for value in load_values),
    }
    return {
        "objective": objective,
        "assignments": assignments,
        "unassigned": unassigned,
        "metrics": metrics,
        "non_dominated": True,
    }


def unassigned_points(
    all_points: list[dict[str, Any]],
    assigned_ids: set[str],
    candidates: list[dict[str, Any]],
    review_pairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every point that has no zone in this plan, with a reviewable reason."""
    candidate_ids = {item["point_id"] for item in candidates}
    review_ids = {item["point_id"] for item in review_pairs}
    unassigned: list[dict[str, Any]] = []
    for record in all_points:
        point_id = record["point_id"]
        if point_id in assigned_ids:
            continue
        if record["status"] == "excluded":
            reason = record["exclusion_reason"]
        elif point_id in candidate_ids:
            reason = CAPACITY_BLOCKED
        elif point_id in review_ids:
            reason = BOUNDARY_REVIEW
        else:
            reason = NO_CANDIDATE
        unassigned.append({"point_id": point_id, "reason": reason})
    unassigned.sort(key=lambda item: item["point_id"])
    return unassigned


def build_blocking(
    feasibility: FlowNetwork,
    source: int,
    eligible_points: list[dict[str, Any]],
    valid_zones: list[dict[str, Any]],
    unassigned: list[dict[str, Any]],
) -> dict[str, Any]:
    """Capacitated Hall witness from the residual min-cut.

    The source-reachable set of any max flow is the unique minimal
    min-cut, so this witness does not depend on the particular flow:
    the deficient points together with their candidate zones satisfy
    demand > total capacity, which is exactly why a full assignment is
    impossible.
    """
    seen = reachable_from(feasibility.graph, source)
    deficient_points = [
        record["point_id"]
        for i, record in enumerate(eligible_points)
        if seen[i + 1]
    ]
    offset = len(eligible_points) + 1
    witness_zones = [
        {"zone_id": record["zone_id"], "capacity": record["capacity"]}
        for i, record in enumerate(valid_zones)
        if seen[offset + i]
    ]
    total_capacity = sum(zone["capacity"] for zone in witness_zones)
    return {
        "reason": "insufficient_capacity_or_coverage",
        "deficient_points": deficient_points,
        "candidate_zones": witness_zones,
        "demand": len(deficient_points),
        "total_capacity": total_capacity,
        "deficit": len(deficient_points) - total_capacity,
        "unassigned_points": unassigned,
    }


def mark_non_dominated(plans: list[dict[str, Any]]) -> None:
    """Flag plans that are not dominated on (total_distance, load squares)."""
    keys = [
        (plan["metrics"]["total_distance"], plan["metrics"]["load_sum_squares"])
        for plan in plans
    ]
    for index, plan in enumerate(plans):
        dominated = False
        for other_index, other_key in enumerate(keys):
            if other_index == index:
                continue
            if (
                other_key[0] <= keys[index][0]
                and other_key[1] <= keys[index][1]
                and other_key != keys[index]
            ):
                dominated = True
                break
        plan["non_dominated"] = not dominated
