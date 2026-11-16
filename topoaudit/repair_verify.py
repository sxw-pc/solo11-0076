"""In-memory candidate application and full post-repair rule re-audit."""
from __future__ import annotations
import itertools
from typing import Sequence

from .geometry import distance
from .model import (
    AuditConfig,
    EndpointMove,
    EndpointRef,
    Finding,
    FindingChange,
    LoadedLayer,
    RepairCandidate,
    RepairConflict,
)
from .repair_candidates import Plan, build_clusters
from .repair_request import RepairRequest, endpoints_of_finding
from .rule_geometry import apply_endpoint_moves
from .rule_topology import EndpointIndex, build_endpoint_index
from .rules import diff_findings, run_layer_rules
from .util import canonical_json, sha256_text, stable_round

# Structured, stable conflict vocabularies.
TARGET_NOT_NEAR_MISS = "TARGET_NOT_NEAR_MISS"
DISPLACEMENT_LIMIT = "DISPLACEMENT_LIMIT"
PROTECTED_ENDPOINT_PINNED = "PROTECTED_ENDPOINT_PINNED"
POST_TARGET_NOT_CONNECTED = "POST_TARGET_NOT_CONNECTED"
POST_EXISTING_CONNECTION_BROKEN = "POST_EXISTING_CONNECTION_BROKEN"
POST_ACCIDENTAL_CONNECTION = "POST_ACCIDENTAL_CONNECTION"
NEW_FINDING_AFTER_REPAIR = "NEW_FINDING_AFTER_REPAIR"
CANDIDATE_BUDGET_EXCEEDED = "CANDIDATE_BUDGET_EXCEEDED"
NO_SNAP_POINT_WITHIN_LIMITS = "NO_SNAP_POINT_WITHIN_LIMITS"


def verify_plan(
    plan: Plan,
    *,
    request: RepairRequest,
    layer: LoadedLayer,
    config: AuditConfig,
    index: EndpointIndex,
    baseline_findings: Sequence[Finding],
    valid_targets: Sequence[tuple[EndpointRef, EndpointRef]],
) -> RepairCandidate:
    """Apply one plan to an in-memory layer copy and re-run every rule."""
    tolerance = index.tolerance
    epsilon = tolerance.coordinate_epsilon
    conflicts: list[RepairConflict] = []
    moves: list[EndpointMove] = []
    moves_by_feature: dict[
        tuple[str, int], dict[tuple[str, str], tuple[float, float]]
    ] = {}

    cluster_groups = [
        sorted(cluster, key=lambda group: min(group))
        for cluster in build_clusters(valid_targets, index)
    ]
    for option, groups in zip(plan.cluster_options, cluster_groups):
        for position, group in enumerate(groups):
            destination = option.destinations[position]
            for member in group:
                record = index.record_at(member)
                displacement = distance(record.point, destination)
                if displacement <= epsilon:
                    continue
                limit = request.effective_limit(record.ref)
                if displacement > limit + epsilon:
                    conflicts.append(
                        RepairConflict(
                            code=DISPLACEMENT_LIMIT,
                            message=(
                                f"端点 {record.ref.feature_id}/"
                                f"{record.ref.part_index}/{record.ref.side} "
                                f"位移 {stable_round(displacement)} 超过上限 "
                                f"{stable_round(limit)}。"
                            ),
                            endpoints=(record.ref,),
                            details={
                                "displacement": stable_round(displacement),
                                "limit": stable_round(limit),
                            },
                        )
                    )
                moves.append(
                    EndpointMove(
                        endpoint=record.ref,
                        original_coordinate=record.point,
                        proposed_coordinate=(
                            float(destination[0]),
                            float(destination[1]),
                        ),
                        displacement=displacement,
                    )
                )
                feature_key = (record.ref.feature_id, record.source_index)
                moves_by_feature.setdefault(feature_key, {})[
                    (record.ref.part_index, record.ref.side)
                ] = (float(destination[0]), float(destination[1]))

    moves_tuple = tuple(sorted(moves, key=lambda item: item.endpoint.key()))
    affected = tuple(
        sorted((move.endpoint for move in moves_tuple), key=lambda ref: ref.key())
    )

    modified_features = apply_endpoint_moves(layer.features, moves_by_feature)
    modified_layer = LoadedLayer(
        config=layer.config,
        features=modified_features,
        input_file=layer.input_file,
    )
    post_index = build_endpoint_index(modified_features, layer.config, tolerance)
    verify_connections(valid_targets, index, post_index, conflicts)
    post_findings = run_layer_rules(config, modified_layer)
    added, removed, retained = diff_findings(baseline_findings, post_findings)
    for finding in added:
        conflicts.append(
            RepairConflict(
                code=NEW_FINDING_AFTER_REPAIR,
                message=f"修后复审新增 {finding.rule_id} 缺陷，方案不可执行。",
                endpoints=endpoints_of_finding(finding),
                details={
                    "rule_id": finding.rule_id,
                    "finding_id": finding.finding_id,
                    "feature_id": finding.feature_id,
                    "evidence": finding.evidence,
                },
            )
        )
    finding_changes = tuple(
        itertools.chain(
            (FindingChange("added", finding) for finding in added),
            (FindingChange("removed", finding) for finding in removed),
            (FindingChange("retained", finding) for finding in retained),
        )
    )
    move_signature = canonical_json(
        [
            [move.endpoint.key(), list(move.proposed_coordinate)]
            for move in moves_tuple
        ]
    )
    candidate_id = sha256_text(move_signature)[:16]
    return RepairCandidate(
        candidate_id=candidate_id,
        moves=moves_tuple,
        moved_endpoint_count=plan.moved_endpoint_count,
        max_displacement=plan.max_displacement,
        total_displacement=plan.total_displacement,
        feasible=not conflicts,
        is_pareto=False,
        conflicts=tuple(
            sorted(
                conflicts,
                key=lambda conflict: (
                    conflict.code,
                    conflict.endpoints[0].key() if conflict.endpoints else (),
                ),
            )
        ),
        finding_changes=finding_changes,
        affected_endpoints=affected,
    )


def verify_connections(
    targets: Sequence[tuple[EndpointRef, EndpointRef]],
    baseline_index: EndpointIndex,
    post_index: EndpointIndex,
    conflicts: list[RepairConflict],
) -> None:
    epsilon = baseline_index.tolerance.coordinate_epsilon

    requested_pairs: set[frozenset[int]] = set()
    for first_ref, second_ref in targets:
        try:
            left = post_index.resolve(first_ref)
            right = post_index.resolve(second_ref)
        except KeyError:
            conflicts.append(
                RepairConflict(
                    code=NEW_FINDING_AFTER_REPAIR,
                    message=(
                        "吸附后某条线退化为零长度/闭合线，端点已不再参与"
                        "拓扑连接判定，方案不可执行。"
                    ),
                    endpoints=(first_ref, second_ref),
                    details={"rule_id": "ZERO_LENGTH_SEGMENT"},
                )
            )
            continue
        requested_pairs.add(frozenset((left, right)))
        gap = post_index.gap(left, right)
        if gap > epsilon:
            conflicts.append(
                RepairConflict(
                    code=POST_TARGET_NOT_CONNECTED,
                    message="吸附后指定端点仍未在 coordinate_epsilon 内连通。",
                    endpoints=(first_ref, second_ref),
                    details={
                        "post_distance": stable_round(gap),
                        "coordinate_epsilon": epsilon,
                    },
                )
            )

    baseline_exact: set[frozenset[int]] = set()
    for group in baseline_index.exact_connection_groups():
        for left, right in itertools.combinations(sorted(group), 2):
            if (
                baseline_index.record_at(left).source_index
                == baseline_index.record_at(right).source_index
            ):
                continue
            baseline_exact.add(frozenset((left, right)))
    for pair in baseline_exact:
        left, right = sorted(pair)
        gap = post_index.gap(left, right)
        if gap > epsilon:
            conflicts.append(
                RepairConflict(
                    code=POST_EXISTING_CONNECTION_BROKEN,
                    message="吸附断开了一条既有的精确连接。",
                    endpoints=(
                        post_index.record_at(left).ref,
                        post_index.record_at(right).ref,
                    ),
                    details={
                        "post_distance": stable_round(gap),
                        "coordinate_epsilon": epsilon,
                    },
                )
            )

    requested_records: set[int] = set()
    for first_ref, second_ref in targets:
        requested_records.add(baseline_index.resolve(first_ref))
        requested_records.add(baseline_index.resolve(second_ref))
    requested_groups: set[frozenset[int]] = {
        baseline_index.group_of(record_index)
        for record_index in requested_records
    }
    allowed_records: set[int] = set()
    for group in requested_groups:
        allowed_records.update(group)
    post_records = post_index.records
    for left in range(len(post_records)):
        first = post_records[left]
        for right in range(left + 1, len(post_records)):
            second = post_records[right]
            if first.source_index == second.source_index:
                continue
            if post_index.gap(left, right) > epsilon:
                continue
            if frozenset((left, right)) in baseline_exact:
                continue
            if left in allowed_records and right in allowed_records:
                continue
            conflicts.append(
                RepairConflict(
                    code=POST_ACCIDENTAL_CONNECTION,
                    message=(
                        "吸附把未指定的邻近端点误接入精确连接，"
                        "该连接不在修复请求内。"
                    ),
                    endpoints=(first.ref, second.ref),
                    details={
                        "post_distance": stable_round(post_index.gap(left, right)),
                        "coordinate_epsilon": epsilon,
                    },
                )
            )
