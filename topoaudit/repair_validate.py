"""Preflight checks and request-vs-index validation for a rehearsal."""
from __future__ import annotations
from typing import Any, Sequence

from .errors import RepairError
from .model import EndpointRef, Finding, LoadedLayer, RepairConflict
from .repair_request import RepairRequest, endpoints_of_finding
from .util import stable_round

# Baseline findings that make automatic repair unsafe: the geometry is
# already invalid or the CRS declaration contradicts the audit CRS.
BLOCKING_RULE_IDS = frozenset(
    {
        "LAYER_CRS_MISMATCH",
        "COORD_NON_FINITE",
        "COORD_OUT_OF_BOUNDS",
        "GEOM_UNSUPPORTED_TYPE",
        "RING_NOT_CLOSED",
        "RING_TOO_FEW_POINTS",
        "LINE_TOO_FEW_POINTS",
        "SELF_INTERSECTION",
    }
)

TARGET_NOT_NEAR_MISS = "TARGET_NOT_NEAR_MISS"


def select_layer(
    layers: Sequence[LoadedLayer], layer_id: str
) -> LoadedLayer:
    for layer in layers:
        if layer.config.layer_id == layer_id:
            return layer
    known = sorted(item.config.layer_id for item in layers)
    raise RepairError(
        "TOPO-FIX-002",
        f"预演目标图层不存在: {layer_id}；可用图层: {', '.join(known)}",
        f"layers[{layer_id}]",
    )


def preflight_conflicts(
    baseline_findings: Sequence[Finding],
) -> list[RepairConflict]:
    """Reject rehearsal when the baseline already has unfixable defects."""
    conflicts: list[RepairConflict] = []
    for finding in baseline_findings:
        if finding.rule_id not in BLOCKING_RULE_IDS:
            continue
        conflicts.append(
            RepairConflict(
                code=f"PREFLIGHT_{finding.rule_id}",
                message=(
                    "基线审计存在不可自动修复的缺陷，端点预演已拒绝："
                    f"{finding.rule_id}"
                ),
                endpoints=endpoints_of_finding(finding),
                details={
                    "rule_id": finding.rule_id,
                    "finding_id": finding.finding_id,
                    "feature_id": finding.feature_id,
                },
            )
        )
    return conflicts


def validate_targets(
    requested: Sequence[tuple[EndpointRef, EndpointRef]], index: Any
) -> tuple[
    tuple[tuple[EndpointRef, EndpointRef], ...], list[RepairConflict]
]:
    """Split requested pairs into valid near-miss edges and structured rejects."""
    valid: list[tuple[EndpointRef, EndpointRef]] = []
    conflicts: list[RepairConflict] = []
    for first_ref, second_ref in requested:
        try:
            left = index.resolve(first_ref)
            right = index.resolve(second_ref)
        except KeyError:
            raise RepairError(
                "TOPO-FIX-003",
                "目标端点不存在或不是线端点",
                "targets",
            ) from None
        record_left = index.record_at(left)
        record_right = index.record_at(right)
        if record_left.source_index == record_right.source_index:
            raise RepairError(
                "TOPO-FIX-003",
                "吸附目标必须是不同要素的两个端点",
                "targets",
            )
        gap = index.gap(left, right)
        if not (
            index.tolerance.coordinate_epsilon
            < gap
            <= index.tolerance.near_miss_distance
        ):
            state = (
                "已在 coordinate_epsilon 内连通"
                if gap <= index.tolerance.coordinate_epsilon
                else "距离超过 near_miss_distance，不属于近失配关系"
            )
            conflicts.append(
                RepairConflict(
                    code=TARGET_NOT_NEAR_MISS,
                    message=f"指定端点对不是近失配关系：{state}。",
                    endpoints=(first_ref, second_ref),
                    details={
                        "distance": stable_round(gap),
                        "coordinate_epsilon": index.tolerance.coordinate_epsilon,
                        "near_miss_distance": index.tolerance.near_miss_distance,
                    },
                )
            )
            continue
        valid.append((first_ref, second_ref))
    return tuple(valid), conflicts


def pinned_groups(
    request: RepairRequest, index: Any
) -> frozenset[frozenset[int]]:
    """Exact-connection groups frozen by the caller's protection list."""
    pinned: set[frozenset[int]] = set()
    for first_ref, second_ref in request.protected_connections:
        try:
            left = index.resolve(first_ref)
            right = index.resolve(second_ref)
        except KeyError:
            raise RepairError(
                "TOPO-FIX-003",
                "保护连接引用了未知端点",
                "protected_connections",
            ) from None
        record_left = index.record_at(left)
        record_right = index.record_at(right)
        if record_left.source_index == record_right.source_index:
            raise RepairError(
                "TOPO-FIX-003",
                "保护连接必须是不同要素端点之间的既有连接",
                "protected_connections",
            )
        if not index.are_exact_connected(left, right):
            raise RepairError(
                "TOPO-FIX-003",
                "保护连接必须引用 coordinate_epsilon 内既有的精确连接",
                "protected_connections",
            )
        pinned.add(index.group_of(left))
        pinned.add(index.group_of(right))
    return frozenset(pinned)


def record_limit_map(request: RepairRequest, index: Any) -> dict[int, float]:
    """Effective per-record displacement cap (global cap + per-endpoint)."""
    limits: dict[int, float] = {}
    for record_index in range(len(index)):
        limits[record_index] = request.effective_limit(
            index.record_at(record_index).ref
        )
    for ref, limit in (request.endpoint_limits or {}).items():
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit < 0:
            raise RepairError(
                "TOPO-FIX-001",
                "端点位移上限必须是非负有限数",
                "endpoint_limits",
            )
        try:
            record_index = index.resolve(ref)
        except KeyError:
            raise RepairError(
                "TOPO-FIX-003", "端点引用不存在", "endpoint_limits"
            ) from None
        limits[record_index] = min(request.max_displacement, float(limit))
    return limits
