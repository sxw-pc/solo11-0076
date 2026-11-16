"""Offline, read-only endpoint repair rehearsal (orchestration layer).

The rehearsal never mutates or writes the loaded GeoJSON/CSV sources.  Every
candidate edit is applied to an in-memory copy of the affected features and
re-checked with the same geometry and topology rules as the baseline audit.

Design guarantees:

* requested near-miss edges are clustered explicitly — adjacency is never
  transitive, so "A near B, B near C" cannot pull A and C into one snap group
  unless both edges were requested and a shared snap point is genuinely
  feasible;
* every endpoint already sharing an exact connection (distance <=
  coordinate_epsilon) moves as one group, so existing connections cannot be
  broken;
* candidates are enumerated and ordered from content-only keys, making the
  result independent of input feature ordering.
"""
from __future__ import annotations
from dataclasses import replace
from typing import Any, Sequence

from .errors import RepairError
from .model import (
    AuditConfig,
    EndpointRef,
    LoadedLayer,
    RepairConflict,
    RepairRehearsalResult,
)
from .repair_anchors import cluster_snap_options
from .repair_candidates import (
    build_clusters,
    enumerate_plans,
    pareto_front,
)
from .repair_request import (
    DEFAULT_CANDIDATE_BUDGET,
    RepairRequest,
    parse_repair_request,
    sorted_pair,
    targets_from_near_miss_findings,
)
from .repair_validate import (
    TARGET_NOT_NEAR_MISS,
    pinned_groups,
    preflight_conflicts,
    record_limit_map,
    select_layer,
    validate_targets,
)
from .repair_verify import (
    CANDIDATE_BUDGET_EXCEEDED,
    DISPLACEMENT_LIMIT,
    NEW_FINDING_AFTER_REPAIR,
    NO_SNAP_POINT_WITHIN_LIMITS,
    POST_ACCIDENTAL_CONNECTION,
    POST_EXISTING_CONNECTION_BROKEN,
    POST_TARGET_NOT_CONNECTED,
    PROTECTED_ENDPOINT_PINNED,
    verify_plan,
)
from .rule_topology import build_endpoint_index
from .rules import run_layer_rules
from .util import stable_round

__all__ = [
    "DEFAULT_CANDIDATE_BUDGET",
    "RepairRequest",
    "parse_repair_request",
    "targets_from_near_miss_findings",
    "rehearse_endpoint_repair",
    "TARGET_NOT_NEAR_MISS",
    "DISPLACEMENT_LIMIT",
    "PROTECTED_ENDPOINT_PINNED",
    "POST_TARGET_NOT_CONNECTED",
    "POST_EXISTING_CONNECTION_BROKEN",
    "POST_ACCIDENTAL_CONNECTION",
    "NEW_FINDING_AFTER_REPAIR",
    "CANDIDATE_BUDGET_EXCEEDED",
    "NO_SNAP_POINT_WITHIN_LIMITS",
]


def rehearse_endpoint_repair(
    config: AuditConfig,
    layers: Sequence[LoadedLayer],
    request: RepairRequest,
) -> RepairRehearsalResult:
    """Build and review every bounded repair candidate for ``request``."""
    layer = select_layer(layers, request.layer_id)
    tolerance = layer.config.effective_tolerance(config.tolerance)
    if not request.max_displacement > 0:
        raise RepairError(
            "TOPO-FIX-001",
            "max_displacement 必须是正数",
            f"layers[{request.layer_id}].max_displacement",
        )
    if request.candidate_budget < 1:
        raise RepairError(
            "TOPO-FIX-001", "candidate_budget 必须 >= 1", "candidate_budget"
        )
    index = build_endpoint_index(layer.features, layer.config, tolerance)
    baseline_findings = run_layer_rules(config, layer)

    requested = tuple(
        sorted(
            (sorted_pair(a, b) for a, b in request.targets),
            key=lambda pair: (pair[0].key(), pair[1].key()),
        )
    )
    preflight = preflight_conflicts(baseline_findings)
    if preflight:
        return _rejected(layer, tolerance, requested, request, preflight)

    valid_targets, target_conflicts = validate_targets(requested, index)
    if not valid_targets:
        return _rejected(layer, tolerance, requested, request, target_conflicts)

    pinned = pinned_groups(request, index)
    record_limits = record_limit_map(request, index)
    clusters = build_clusters(valid_targets, index)

    cluster_options: list[tuple] = []
    cluster_blocks: list[RepairConflict] = []
    for cluster in clusters:
        options, rejections = cluster_snap_options(
            cluster, valid_targets, index, record_limits, pinned
        )
        if not options:
            cluster_blocks.extend(rejections)
        cluster_options.append(options)

    plans, review_reasons = enumerate_plans(
        cluster_options, request.candidate_budget
    )
    if cluster_blocks:
        review_reasons.append(
            "至少一个近失配簇在位移或保护约束下不存在可用吸附点，"
            "需人工复核具体约束冲突。"
        )

    candidates = tuple(
        verify_plan(
            plan,
            request=request,
            layer=layer,
            config=config,
            index=index,
            baseline_findings=baseline_findings,
            valid_targets=valid_targets,
        )
        for plan in plans
    )
    pareto_ids = pareto_front(candidates)
    candidates = tuple(
        replace(candidate, is_pareto=candidate.candidate_id in pareto_ids)
        for candidate in candidates
    )

    top_conflicts = list(target_conflicts)
    top_conflicts.extend(cluster_blocks)
    if review_reasons and not any(
        conflict.code == CANDIDATE_BUDGET_EXCEEDED for conflict in top_conflicts
    ) and any("候选组合数超过预算" in reason for reason in review_reasons):
        top_conflicts.append(
            RepairConflict(
                code=CANDIDATE_BUDGET_EXCEEDED,
                message=(
                    "候选组合数超过预算，仅返回稳定排序前缀供人工复核；"
                    "未宣称无解或全局最优。"
                ),
                endpoints=(),
                details={"candidate_budget": request.candidate_budget},
            )
        )
    if not plans and not cluster_blocks:
        top_conflicts.append(
            RepairConflict(
                code=CANDIDATE_BUDGET_EXCEEDED,
                message=(
                    "候选空间在预算内未枚举到任何方案，"
                    "需要人工复核而非宣称无解。"
                ),
                endpoints=(),
                details={"candidate_budget": request.candidate_budget},
            )
        )
    feasible = any(candidate.feasible for candidate in candidates)
    return RepairRehearsalResult(
        layer_id=layer.config.layer_id,
        status="feasible" if feasible else "infeasible",
        coordinate_epsilon=tolerance.coordinate_epsilon,
        near_miss_distance=tolerance.near_miss_distance,
        requested_targets=requested,
        max_displacement_limit=stable_round(request.max_displacement),
        candidates=candidates,
        pareto_front=pareto_ids,
        conflicts=tuple(top_conflicts),
        review_required=bool(review_reasons),
        review_reasons=tuple(review_reasons),
    )


def _rejected(
    layer: LoadedLayer,
    tolerance: Any,
    requested: Sequence[tuple[EndpointRef, EndpointRef]],
    request: RepairRequest,
    conflicts: Sequence[RepairConflict],
) -> RepairRehearsalResult:
    return RepairRehearsalResult(
        layer_id=layer.config.layer_id,
        status="rejected",
        coordinate_epsilon=tolerance.coordinate_epsilon,
        near_miss_distance=tolerance.near_miss_distance,
        requested_targets=tuple(requested),
        max_displacement_limit=stable_round(request.max_displacement),
        candidates=(),
        pareto_front=(),
        conflicts=tuple(conflicts),
        review_required=False,
        review_reasons=(),
    )
