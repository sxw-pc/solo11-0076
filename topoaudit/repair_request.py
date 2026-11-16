"""Repair request model and strict JSON request parsing."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .errors import RepairError
from .model import EndpointRef, Finding

DEFAULT_CANDIDATE_BUDGET = 200


@dataclass(frozen=True)
class RepairRequest:
    """Caller-supplied repair intent for one line layer."""

    layer_id: str
    targets: tuple[tuple[EndpointRef, EndpointRef], ...]
    max_displacement: float
    endpoint_limits: Mapping[EndpointRef, float] = None
    protected_connections: tuple[tuple[EndpointRef, EndpointRef], ...] = ()
    candidate_budget: int = DEFAULT_CANDIDATE_BUDGET

    def effective_limit(self, ref: EndpointRef) -> float:
        override = (self.endpoint_limits or {}).get(ref)
        if override is None:
            return self.max_displacement
        return min(self.max_displacement, float(override))


def targets_from_near_miss_findings(
    findings: Iterable[Finding],
) -> tuple[tuple[EndpointRef, EndpointRef], ...]:
    """Convert NEAR_MISS_ENDPOINT findings into repair target pairs."""
    targets: list[tuple[EndpointRef, EndpointRef]] = []
    for finding in sorted(findings, key=lambda item: item.finding_id):
        if finding.rule_id != "NEAR_MISS_ENDPOINT":
            continue
        first = ref_from_evidence(finding.evidence["endpoint_a"])
        second = ref_from_evidence(finding.evidence["endpoint_b"])
        targets.append(sorted_pair(first, second))
    return tuple(targets)


def ref_from_evidence(evidence: Mapping[str, Any]) -> EndpointRef:
    return EndpointRef(
        feature_id=str(evidence["feature_id"]),
        part_index=str(evidence["part_index"]),
        side=str(evidence["endpoint"]),
    )


def sorted_pair(
    first: EndpointRef, second: EndpointRef
) -> tuple[EndpointRef, EndpointRef]:
    return tuple(sorted((first, second), key=lambda ref: ref.key()))


def endpoints_of_finding(finding: Any) -> tuple[EndpointRef, ...]:
    """Extract every endpoint reference carried by a topology finding."""
    evidence = finding.evidence
    if "endpoint" in evidence and "part_index" in evidence:
        return (
            EndpointRef(
                feature_id=str(finding.feature_id),
                part_index=str(evidence["part_index"]),
                side=str(evidence["endpoint"]),
            ),
        )
    refs: list[EndpointRef] = []
    for key in ("endpoint_a", "endpoint_b"):
        if isinstance(evidence.get(key), dict):
            refs.append(ref_from_evidence(evidence[key]))
    return tuple(refs)


def parse_repair_request(raw: Any) -> RepairRequest:
    """Parse and validate a JSON-shaped repair request document."""
    if not isinstance(raw, dict):
        raise RepairError("TOPO-FIX-001", "修复请求必须是 JSON 对象", "$")
    allowed = {
        "layer_id",
        "targets",
        "max_displacement",
        "endpoint_limits",
        "protected_connections",
        "candidate_budget",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise RepairError(
            "TOPO-FIX-001", f"修复请求包含未知字段: {', '.join(unknown)}", "$"
        )
    layer_id = raw.get("layer_id")
    if not isinstance(layer_id, str) or not layer_id.strip():
        raise RepairError("TOPO-FIX-001", "layer_id 必须是非空字符串", "$.layer_id")
    max_displacement = raw.get("max_displacement")
    if (
        isinstance(max_displacement, bool)
        or not isinstance(max_displacement, (int, float))
        or not max_displacement > 0
    ):
        raise RepairError(
            "TOPO-FIX-001",
            "max_displacement 必须是正数",
            "$.max_displacement",
        )
    targets_raw = raw.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise RepairError(
            "TOPO-FIX-001", "targets 必须是非空数组", "$.targets"
        )
    targets: list[tuple[EndpointRef, EndpointRef]] = []
    for index, item in enumerate(targets_raw):
        where = f"$.targets[{index}]"
        if not isinstance(item, list) or len(item) != 2:
            raise RepairError(
                "TOPO-FIX-001", "每个 target 必须是两个端点引用组成的数组", where
            )
        targets.append(
            sorted_pair(
                _parse_ref(item[0], where),
                _parse_ref(item[1], where),
            )
        )
    endpoint_limits: dict[EndpointRef, float] = {}
    for index, item in enumerate(raw.get("endpoint_limits", [])):
        where = f"$.endpoint_limits[{index}]"
        if not isinstance(item, dict) or "endpoint" not in item:
            raise RepairError("TOPO-FIX-001", "端点上限项必须包含 endpoint", where)
        ref = _parse_ref(item["endpoint"], f"{where}.endpoint")
        limit = item.get("max_displacement")
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit < 0:
            raise RepairError(
                "TOPO-FIX-001",
                "max_displacement 必须是非负有限数",
                f"{where}.max_displacement",
            )
        endpoint_limits[ref] = float(limit)
    protected: list[tuple[EndpointRef, EndpointRef]] = []
    for index, item in enumerate(raw.get("protected_connections", [])):
        where = f"$.protected_connections[{index}]"
        if not isinstance(item, list) or len(item) != 2:
            raise RepairError(
                "TOPO-FIX-001", "保护连接必须是两个端点引用的数组", where
            )
        protected.append(
            sorted_pair(
                _parse_ref(item[0], where),
                _parse_ref(item[1], where),
            )
        )
    budget = raw.get("candidate_budget", DEFAULT_CANDIDATE_BUDGET)
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise RepairError(
            "TOPO-FIX-001",
            "candidate_budget 必须是 >= 1 的整数",
            "$.candidate_budget",
        )
    return RepairRequest(
        layer_id=layer_id,
        targets=tuple(targets),
        max_displacement=float(max_displacement),
        endpoint_limits=endpoint_limits,
        protected_connections=tuple(protected),
        candidate_budget=budget,
    )


def _parse_ref(raw: Any, where: str) -> EndpointRef:
    required = ("feature_id", "part_index", "side")
    if not isinstance(raw, dict) or any(not isinstance(raw.get(key), str) for key in required):
        raise RepairError(
            "TOPO-FIX-001",
            "端点引用必须包含字符串字段 feature_id/part_index/side",
            where,
        )
    if raw["side"] not in ("start", "end"):
        raise RepairError(
            "TOPO-FIX-001", "端点 side 只能是 start 或 end", where
        )
    return EndpointRef.from_dict(raw)
