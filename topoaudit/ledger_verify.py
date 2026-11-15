"""Cross-check a JSON report against the SQLite audit ledger."""
from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from .errors import LedgerError, ReportError
from .ledger import connect_ledger
from .report_json import compute_result_digest
from .util import canonical_json
@dataclass
class VerificationResult:
    ok: bool
    report_digest: str
    issues: list[str] = field(default_factory=list)
    finding_count: int = 0
    layer_count: int = 0

def verify_report_and_ledger(report_path: str | Path, ledger_path: str | Path) -> VerificationResult:
    report = _load_report(Path(report_path))
    digest = str(report.get("result_digest", ""))
    issues: list[str] = []
    if not digest:
        issues.append("报告缺少 result_digest")
    elif compute_result_digest(report) != digest:
        issues.append("报告 result_digest 与报告内容不一致")
    try:
        connection = connect_ledger(ledger_path, read_only=True)
    except LedgerError as exc:
        raise LedgerError("TOPO-LED-001", str(exc), str(ledger_path)) from exc
    try:
        row = connection.execute(
            "SELECT * FROM runs WHERE result_digest = ?", (digest,)
        ).fetchone()
        if row is None:
            issues.append("台账中不存在该 result_digest 的运行记录")
            return VerificationResult(False, digest, issues)
        issues.extend(_compare_run(report, row))
        layer_count = _compare_layers(connection, digest, report, issues)
        finding_count = _compare_findings(connection, digest, report, issues)
        _compare_inputs(connection, digest, report, issues)
        _compare_rule_catalog(connection, report, issues)
        _compare_summary(report, issues)
    except sqlite3.Error as exc:
        raise LedgerError("TOPO-LED-005", f"读取台账失败: {exc}", str(ledger_path)) from exc
    finally:
        connection.close()
    return VerificationResult(
        ok=not issues,
        report_digest=digest,
        issues=issues,
        finding_count=finding_count,
        layer_count=layer_count,
    )
def _load_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReportError("TOPO-RPT-001", "报告文件不存在", str(path))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReportError("TOPO-RPT-002", f"无法解析报告 JSON: {exc}", str(path)) from exc
    if not isinstance(raw, dict):
        raise ReportError("TOPO-RPT-003", "报告根节点必须是 JSON 对象", str(path))
    return raw
def _compare_run(report: dict[str, Any], row: sqlite3.Row) -> list[str]:
    issues: list[str] = []
    fields = {
        "schema_version": "schema_version",
        "run_label": "run_label",
        "input_digest": "input_digest",
        "config_digest": "config_digest",
    }
    for report_key, row_key in fields.items():
        if str(report.get(report_key, "")) != str(row[row_key]):
            issues.append(f"runs.{row_key} 与报告不一致")
    try:
        summary = json.loads(row["summary_json"])
    except json.JSONDecodeError:
        issues.append("runs.summary_json 不是有效 JSON")
        return issues
    if canonical_json(summary) != canonical_json(report.get("summary", {})):
        issues.append("runs.summary_json 与报告 summary 不一致")
    return issues
def _compare_layers(
    connection: sqlite3.Connection,
    digest: str,
    report: dict[str, Any],
    issues: list[str],
) -> int:
    rows = connection.execute(
        "SELECT layer_id, stats_json FROM layers WHERE result_digest = ?", (digest,)
    ).fetchall()
    ledger = {row["layer_id"]: json.loads(row["stats_json"]) for row in rows}
    reported = {str(item.get("layer_id")): item for item in report.get("layers", [])}
    if set(ledger) != set(reported):
        issues.append("台账 layers 与报告图层集合不一致")
    for layer_id in sorted(set(ledger) & set(reported)):
        if canonical_json(ledger[layer_id]) != canonical_json(reported[layer_id]):
            issues.append(f"图层 {layer_id} 的统计与台账不一致")
    return len(rows)
def _compare_findings(
    connection: sqlite3.Connection,
    digest: str,
    report: dict[str, Any],
    issues: list[str],
) -> int:
    rows = connection.execute(
        """
        SELECT finding_id, rule_id, severity, layer_id, feature_id,
               related_json, evidence_json
        FROM findings WHERE result_digest = ?
        """,
        (digest,),
    ).fetchall()
    ledger: dict[str, dict[str, Any]] = {}
    for row in rows:
        ledger[row["finding_id"]] = {
            "finding_id": row["finding_id"],
            "rule_id": row["rule_id"],
            "severity": row["severity"],
            "layer_id": row["layer_id"],
            "feature_id": row["feature_id"],
            "related_feature_ids": json.loads(row["related_json"]),
            "evidence": json.loads(row["evidence_json"]),
        }
    reported: dict[str, dict[str, Any]] = {}
    for item in report.get("findings", []):
        finding_id = str(item.get("finding_id", ""))
        if finding_id in reported:
            issues.append(f"报告中 finding_id 重复: {finding_id}")
        reported[finding_id] = item
    if set(ledger) != set(reported):
        missing = sorted(set(ledger) - set(reported))
        extra = sorted(set(reported) - set(ledger))
        if missing:
            issues.append(f"报告缺少 {len(missing)} 条台账 finding")
        if extra:
            issues.append(f"报告多出 {len(extra)} 条台账中不存在的 finding")
    for finding_id in sorted(set(ledger) & set(reported)):
        if canonical_json(ledger[finding_id]) != canonical_json(reported[finding_id]):
            issues.append(f"finding {finding_id} 内容与台账不一致")
    return len(rows)
def _compare_inputs(
    connection: sqlite3.Connection,
    digest: str,
    report: dict[str, Any],
    issues: list[str],
) -> None:
    rows = connection.execute(
        "SELECT layer_id, path, format, sha256, size_bytes FROM input_files "
        "WHERE result_digest = ?",
        (digest,),
    ).fetchall()
    ledger = {row["layer_id"]: dict(row) for row in rows}
    reported = {str(item.get("layer_id")): item for item in report.get("input_files", [])}
    if set(ledger) != set(reported):
        issues.append("输入文件摘要集合与台账不一致")
        return
    for layer_id in sorted(ledger):
        if canonical_json(ledger[layer_id]) != canonical_json(reported[layer_id]):
            issues.append(f"图层 {layer_id} 的输入文件摘要不一致")
def _compare_rule_catalog(
    connection: sqlite3.Connection, report: dict[str, Any], issues: list[str]
) -> None:
    rows = connection.execute(
        "SELECT rule_id, severity, title FROM rule_catalog ORDER BY rule_id"
    ).fetchall()
    ledger = {row["rule_id"]: {"severity": row["severity"], "title": row["title"]} for row in rows}
    reported = {
        str(item.get("rule_id")): {
            "severity": item.get("severity"),
            "title": item.get("title"),
        }
        for item in report.get("rules", [])
    }
    if set(ledger) != set(reported):
        issues.append("规则目录快照与报告规则集合不一致")
        return
    for rule_id in sorted(ledger):
        if canonical_json(ledger[rule_id]) != canonical_json(reported[rule_id]):
            issues.append(f"规则 {rule_id} 的目录快照不一致")
def _compare_summary(report: dict[str, Any], issues: list[str]) -> None:
    summary = report.get("summary", {})
    findings = report.get("findings", [])
    if summary.get("total_findings") != len(findings):
        issues.append("summary.total_findings 与报告 findings 数量不一致")
    severity = {key: 0 for key in ("error", "warning", "info")}
    for finding in findings:
        name = finding.get("severity")
        if name in severity:
            severity[name] += 1
    if summary.get("severity_counts") != severity:
        issues.append("summary.severity_counts 与报告 findings 不一致")
    if summary.get("total_layers") != len(report.get("layers", [])):
        issues.append("summary.total_layers 与报告 layers 数量不一致")
