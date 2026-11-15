"""Stable finding construction, deduplication, and deterministic ordering."""
from __future__ import annotations
from typing import Any, Iterable, Optional
from .model import SEVERITIES, SEVERITY_RANK, Finding
from .rules_catalog import RULE_CATALOG, rule_meta
from .util import canonical_json, sha256_prefix

def make_finding(
    rule_id: str,
    layer_id: str,
    feature_id: Optional[str],
    related_feature_ids: Iterable[str],
    evidence: dict[str, Any],
) -> Finding:
    meta = rule_meta(rule_id)
    related = tuple(sorted({str(value) for value in related_feature_ids}))
    payload = {
        "rule_id": rule_id,
        "severity": meta.severity,
        "layer_id": layer_id,
        "feature_id": feature_id,
        "related_feature_ids": list(related),
        "evidence": evidence,
    }
    finding_id = sha256_prefix(canonical_json(payload), 16)
    return Finding(
        finding_id=finding_id,
        rule_id=rule_id,
        severity=meta.severity,
        layer_id=layer_id,
        feature_id=feature_id,
        related_feature_ids=related,
        evidence=evidence,
    )

class FindingCollector:
    """Collect findings while suppressing exact duplicates by finding ID."""
    def __init__(self) -> None:
        self._items: list[Finding] = []
        self._seen: set[str] = set()
    def add(
        self,
        rule_id: str,
        layer_id: str,
        feature_id: Optional[str] = None,
        related_feature_ids: Iterable[str] = (),
        evidence: Optional[dict[str, Any]] = None,
    ) -> Finding:
        if rule_id not in RULE_CATALOG:
            raise KeyError(f"unknown rule: {rule_id}")
        finding = make_finding(
            rule_id=rule_id,
            layer_id=layer_id,
            feature_id=feature_id,
            related_feature_ids=related_feature_ids,
            evidence={} if evidence is None else evidence,
        )
        if finding.finding_id not in self._seen:
            self._seen.add(finding.finding_id)
            self._items.append(finding)
        return finding
    def findings(self) -> tuple[Finding, ...]:
        return tuple(sort_findings(self._items))
    def __len__(self) -> int:
        return len(self._items)

def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda item: (
            SEVERITY_RANK.get(item.severity, 99),
            item.rule_id,
            item.layer_id,
            item.feature_id or "",
            item.related_feature_ids,
            item.finding_id,
        ),
    )

def severity_counts(findings: Iterable[Finding]) -> dict[str, int]:
    counts = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts

def rule_counts(findings: Iterable[Finding]) -> dict[str, int]:
    counts = {rule_id: 0 for rule_id in RULE_CATALOG}
    for finding in findings:
        counts[finding.rule_id] = counts.get(finding.rule_id, 0) + 1
    return counts

def layer_counts(findings: Iterable[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.layer_id] = counts.get(finding.layer_id, 0) + 1
    return counts
