"""Rule-engine entry point and layer-level checks."""
from __future__ import annotations
from typing import Any, Iterable, Optional
from .findings import FindingCollector, sort_findings
from .model import AuditConfig, Finding, LoadedLayer
from .rule_geometry import check_geometry
from .rule_schema import check_schema
from .rule_topology import check_layer_topology
from .util import canonical_json

def run_rules(config: AuditConfig, layers: tuple[LoadedLayer, ...]) -> tuple[Finding, ...]:
    collector = FindingCollector()
    for layer in layers:
        run_layer_rules(config, layer, collector)
    return tuple(sort_findings(collector.findings()))

def run_layer_rules(
    config: AuditConfig, layer: LoadedLayer, collector: Optional[FindingCollector] = None
) -> tuple[Finding, ...]:
    """Run the complete rule chain for one layer on an in-memory layer copy."""
    own_collector = FindingCollector() if collector is None else collector
    tolerance = layer.config.effective_tolerance(config.tolerance)
    _check_layer_crs(layer, config, own_collector)
    if not layer.features:
        if layer.config.rule_enabled("EMPTY_LAYER"):
            own_collector.add(
                "EMPTY_LAYER",
                layer.config.layer_id,
                evidence={"feature_count": 0},
            )
    for feature in layer.features:
        check_schema(feature, layer.config, own_collector)
        check_geometry(feature, layer.config, config, tolerance, own_collector)
    check_layer_topology(layer.features, layer.config, tolerance, own_collector)
    if collector is None:
        return tuple(sort_findings(own_collector.findings()))
    return ()

def _check_layer_crs(
    layer: LoadedLayer, config: AuditConfig, collector: FindingCollector
) -> None:
    declared = layer.config.declared_crs
    if declared is None or not layer.config.rule_enabled("LAYER_CRS_MISMATCH"):
        return
    expected = {"name": config.crs.name, "type": config.crs.kind}
    actual = {"name": declared.name, "type": declared.kind}
    if expected != actual:
        collector.add(
            "LAYER_CRS_MISMATCH",
            layer.config.layer_id,
            evidence={"expected_crs": expected, "declared_crs": actual},
        )

# Endpoint findings carry coordinates/distances that change during a repair
# preview even when the underlying business defect is the same.  Diffing on
# full finding IDs would therefore report spurious add/remove churn, so these
# rule IDs use stable business keys instead.
_VOLATILE_EVIDENCE_RULES = frozenset({"DANGLING_ENDPOINT", "NEAR_MISS_ENDPOINT"})

def finding_business_key(finding: Finding) -> tuple[Any, ...]:
    """Stable identity of a finding across an edit and its post-edit re-audit."""
    if finding.rule_id == "DANGLING_ENDPOINT":
        evidence = finding.evidence
        return (
            finding.rule_id,
            finding.layer_id,
            finding.feature_id,
            evidence.get("part_index"),
            evidence.get("endpoint"),
        )
    if finding.rule_id == "NEAR_MISS_ENDPOINT":
        evidence = finding.evidence
        endpoints = sorted(
            (
                _endpoint_identity(evidence.get("endpoint_a", {})),
                _endpoint_identity(evidence.get("endpoint_b", {})),
            )
        )
        return (finding.rule_id, finding.layer_id, tuple(endpoints))
    return (
        finding.rule_id,
        finding.layer_id,
        finding.feature_id,
        finding.related_feature_ids,
        canonical_json(finding.evidence),
    )


def _endpoint_identity(evidence: Any) -> tuple[Any, ...]:
    if not isinstance(evidence, dict):
        return ()
    return (
        evidence.get("feature_id"),
        evidence.get("part_index"),
        evidence.get("endpoint"),
    )

def diff_findings(
    baseline: Iterable[Finding], repaired: Iterable[Finding]
) -> tuple[tuple[Finding, ...], tuple[Finding, ...], tuple[Finding, ...]]:
    """Match two audit runs on business keys; return (added, removed, retained)."""
    baseline_rows = tuple(baseline)
    repaired_rows = tuple(repaired)
    baseline_by_key: dict[tuple[Any, ...], list[Finding]] = {}
    for finding in baseline_rows:
        baseline_by_key.setdefault(finding_business_key(finding), []).append(finding)
    repaired_by_key: dict[tuple[Any, ...], list[Finding]] = {}
    for finding in repaired_rows:
        repaired_by_key.setdefault(finding_business_key(finding), []).append(finding)
    added: list[Finding] = []
    retained: list[Finding] = []
    for key, rows in repaired_by_key.items():
        old_rows = baseline_by_key.get(key, [])
        for index, finding in enumerate(rows):
            if index < len(old_rows):
                retained.append(finding)
            else:
                added.append(finding)
    removed: list[Finding] = []
    for key, rows in baseline_by_key.items():
        new_rows = repaired_by_key.get(key, [])
        for index, finding in enumerate(rows):
            if index >= len(new_rows):
                removed.append(finding)
    return (
        tuple(sort_findings(added)),
        tuple(sort_findings(removed)),
        tuple(sort_findings(retained)),
    )
