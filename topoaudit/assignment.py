"""Offline, read-only point-facility to responsibility-zone assignment precheck.

The precheck turns spatial containment candidates into a capacitated
responsibility assignment.  It never assigns greedily: feasibility and
plans come from a deterministic max-flow / min-cost-flow core, so a point
that can only be served by one zone is never displaced by a flexible
point that merely happens to be closer to that zone.  When a full
assignment is impossible the precheck emits a reviewable capacitated
Hall witness (deficient point set, candidate zones, capacities) instead
of a bare "capacity insufficient" message.

Two plans are always computed and kept when non-dominated:

* ``min_total_distance`` — minimize the summed distance to zone anchors;
* ``balance_loads`` — minimize the sum of squared zone loads (then
  distance) so workloads spread more evenly across zones.

Both plans assign every assignable point to exactly one zone and never
exceed a zone capacity.  Everything is derived from sorted,
input-order-independent keys, so permuting input features cannot change
candidates or results.
"""
from __future__ import annotations
from typing import Any
from .assignment_flow import (
    FEASIBILITY,
    OBJECTIVES,
    FlowNetwork,
    add_assignment_arcs,
    flow_edges,
    min_cost_max_flow,
)
from .assignment_inventory import (
    ISSUE_DUPLICATE_POINT_ID,
    ISSUE_DUPLICATE_ZONE_ID,
    build_candidates,
    build_point_inventory,
    build_zone_inventory,
    crs_issues,
    duplicate_issue,
    layer_by_id,
    unknown_reference_issues,
)
from .assignment_plans import build_blocking, build_plan, mark_non_dominated
from .model import AuditConfig, LoadedLayer


def run_assignment_precheck(
    config: AuditConfig, layers: tuple[LoadedLayer, ...]
) -> dict[str, Any]:
    """Execute the assignment precheck and return a JSON-ready result."""
    spec = config.assignment
    if spec is None:  # pragma: no cover - engine guards this
        raise ValueError("assignment config is required")
    point_layer = layer_by_id(layers, spec.point_layer)
    zone_layer = layer_by_id(layers, spec.zone_layer)
    epsilon = zone_layer.config.effective_tolerance(config.tolerance).coordinate_epsilon

    issues: list[dict[str, Any]] = []
    issues.extend(crs_issues(config, point_layer, zone_layer))

    zones = build_zone_inventory(zone_layer, spec, epsilon)
    points = build_point_inventory(point_layer)
    issues.extend(duplicate_issue(points, "point_id", ISSUE_DUPLICATE_POINT_ID, "点设施"))
    issues.extend(duplicate_issue(zones, "zone_id", ISSUE_DUPLICATE_ZONE_ID, "责任区"))
    issues.extend(unknown_reference_issues(zones, spec))

    if issues:
        return {
            "status": "blocked",
            "config": spec.as_dict(),
            "issues": issues,
            "input_summary": _input_summary(point_layer, zone_layer, points, zones, 0, 0),
            "zones": [_public_zone(record) for record in zones],
            "points": [_public_point(record) for record in points],
            "candidates": [],
            "review_pairs": [],
            "plans": [],
            "blocking": None,
        }

    valid_zones = [record for record in zones if record["status"] == "valid"]
    eligible_points = [record for record in points if record["status"] == "eligible"]
    candidates, review_pairs = build_candidates(valid_zones, eligible_points, spec, epsilon)

    network_size = len(eligible_points) + len(valid_zones) + 2
    source, sink = 0, network_size - 1
    edges = flow_edges(candidates, eligible_points, valid_zones)

    # 1) Plan-independent feasibility check (the residual min-cut of this
    #    flow yields the capacitated Hall witness used below).
    feasibility = FlowNetwork(network_size)
    add_assignment_arcs(feasibility, eligible_points, valid_zones, edges, source, sink, FEASIBILITY)
    max_flow, _ = min_cost_max_flow(feasibility, source, sink)
    feasible = max_flow == len(eligible_points)

    # 2) One min-cost plan per objective, both maximum-flow (non-greedy).
    plans = []
    for objective in OBJECTIVES:
        network = FlowNetwork(network_size)
        add_assignment_arcs(network, eligible_points, valid_zones, edges, source, sink, objective)
        min_cost_max_flow(network, source, sink)
        plans.append(
            build_plan(
                objective, network, eligible_points, valid_zones, edges,
                points, candidates, review_pairs,
            )
        )
    mark_non_dominated(plans)

    # 3) When full assignment is impossible, explain why with a reviewable
    #    witness; the unassigned list mirrors the primary (distance) plan.
    blocking = None
    if not feasible:
        blocking = build_blocking(
            feasibility, source, eligible_points, valid_zones,
            plans[0]["unassigned"],
        )

    return {
        "status": "ok",
        "config": spec.as_dict(),
        "issues": [],
        "input_summary": _input_summary(
            point_layer, zone_layer, points, zones, len(candidates), len(review_pairs)
        ),
        "zones": [_public_zone(record) for record in zones],
        "points": [_public_point(record) for record in points],
        "candidates": candidates,
        "review_pairs": review_pairs,
        "plans": plans,
        "blocking": blocking,
    }


def _input_summary(
    point_layer: LoadedLayer,
    zone_layer: LoadedLayer,
    points: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    candidate_count: int,
    review_count: int,
) -> dict[str, Any]:
    return {
        "point_layer": {
            "layer_id": point_layer.config.layer_id,
            "sha256": point_layer.input_file.sha256,
            "feature_count": len(point_layer.features),
        },
        "zone_layer": {
            "layer_id": zone_layer.config.layer_id,
            "sha256": zone_layer.input_file.sha256,
            "feature_count": len(zone_layer.features),
        },
        "eligible_points": sum(1 for record in points if record["status"] == "eligible"),
        "excluded_points": sum(1 for record in points if record["status"] == "excluded"),
        "valid_zones": sum(1 for record in zones if record["status"] == "valid"),
        "excluded_zones": sum(1 for record in zones if record["status"] == "excluded"),
        "candidate_pairs": candidate_count,
        "review_pairs": review_count,
    }


def _public_zone(record: dict[str, Any]) -> dict[str, Any]:
    anchor = record["anchor"]
    return {
        "zone_id": record["zone_id"],
        "feature_id": record["feature_id"],
        "source_index": record["source_index"],
        "capacity": record["capacity"],
        "anchor": [anchor[0], anchor[1]] if anchor is not None else None,
        "components": record["components"],
        "status": record["status"],
        "exclusion_reason": record["exclusion_reason"],
    }


def _public_point(record: dict[str, Any]) -> dict[str, Any]:
    coordinate = record["coordinate"]
    return {
        "point_id": record["point_id"],
        "source_index": record["source_index"],
        "coordinate": [coordinate[0], coordinate[1]] if coordinate is not None else None,
        "status": record["status"],
        "exclusion_reason": record["exclusion_reason"],
    }
