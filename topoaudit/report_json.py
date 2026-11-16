"""Deterministic JSON report construction and digest computation."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from . import SCHEMA_VERSION
from .model import AuditResult
from .rules_catalog import RULE_CATALOG
from .util import canonical_json, pretty_json, sha256_text

def build_report(result: AuditResult) -> dict[str, Any]:
    counts: dict[str, int] = {rule_id: 0 for rule_id in RULE_CATALOG}
    for finding in result.findings:
        counts[finding.rule_id] = counts.get(finding.rule_id, 0) + 1
    rules = [
        {
            "rule_id": meta.rule_id,
            "severity": meta.severity,
            "title": meta.title,
            "count": counts[meta.rule_id],
        }
        for meta in (RULE_CATALOG[key] for key in sorted(RULE_CATALOG))
    ]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_label": result.config.run_label,
        "config_digest": result.config.config_digest,
        "input_digest": result.input_digest,
        "summary": result.summary,
        "layers": [item.as_dict() for item in result.layer_stats],
        "rules": rules,
        "findings": [item.as_dict() for item in result.findings],
        "input_files": [item.as_dict() for item in result.input_files],
        "limitations": list(result.limitations),
    }
    if result.coverage:
        payload["coverage"] = list(result.coverage)
    digest = compute_result_digest(payload)
    report: dict[str, Any] = dict(payload)
    report["result_digest"] = digest
    return report

def compute_result_digest(report: dict[str, Any]) -> str:
    payload = {key: value for key, value in report.items() if key != "result_digest"}
    return sha256_text(canonical_json(payload))

def write_report_json(path: str | Path, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(pretty_json(report), encoding="utf-8")
