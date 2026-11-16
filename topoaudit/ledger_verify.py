"""Cross-check a JSON report against the SQLite audit ledger."""
from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from .errors import LedgerError, ReportError
from .generation import source_generation_issues
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
    generation_status: str = ""
    generation_id: str | None = None
    quality_gate: str = ""
    same_generation_proven: bool = False

def verify_report_and_ledger(report_path: str | Path, ledger_path: str | Path) -> VerificationResult:
    report = _load_report(Path(report_path))
    digest = str(report.get("result_digest", ""))
    issues: list[str] = []
    generation = report.get("source_generation")
    if not digest:
        issues.append("报告缺少 result_digest")
    elif compute_result_digest(report) != digest:
        issues.append("报告 result_digest 与报告内容不一致")
    issues.extend(source_generation_issues(generation))
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
        issues.extend(_compare_generation(report, row))
        layer_count = _compare_layers(connection, digest, report, issues)
        finding_count = _compare_findings(connection, digest, report, issues)
        _compare_inputs(connection, digest, report, issues)
        _compare_rule_catalog(connection, report, issues)
        _compare_summary(report, issues)
        _compare_quality_gate(report, issues)
    except sqlite3.Error as exc:
        raise LedgerError("TOPO-LED-005", f"读取台账失败: {exc}", str(ledger_path)) from exc
    finally:
        connection.close()
    evidence = generation if isinstance(generation, dict) else {}
    return VerificationResult(
        ok=not issues,
        report_digest=digest,
        issues=issues,
        finding_count=finding_count,
        layer_count=layer_count,
        generation_status=str(evidence.get("status", "")),
        generation_id=evidence.get("generation_id"),
        quality_gate=str(evidence.get("quality_gate", "")),
        same_generation_proven=evidence.get("same_generation_proven") is True,
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

def _compare_generation(report: dict[str, Any], row: sqlite3.Row) -> list[str]:
    """Ensure the ledger's source-generation columns match the report block."""
    issues: list[str] = []
    generation = report.get("source_generation")
    if not isinstance(generation, dict):
        issues.append("报告缺少 source_generation 证据")
        return issues
    try:
        ledger_generation = json.loads(row["source_generation_json"])
    except (json.JSONDecodeError, TypeError):
        issues.append("runs.source_generation_json 不是有效 JSON")
        return issues
    if canonical_json(ledger_generation) != canonical_json(generation):
        issues.append("runs.source_generation_json 与报告不一致")
    summary = report.get("summary", {})
    expected = {
        "generation_status": str(generation.get("status", "")),
        "quality_gate": str(generation.get("quality_gate", "")),
    }
    for key, value in expected.items():
        observed = row[key]
        if (str(observed) if observed is not None else None) != (
            str(value) if value is not None else None
        ):
            issues.append(f"runs.{key} 与报告来源代证据不一致")
    for key in ("manifest_sha256", "manifest_digest"):
        observed, value = row[key], generation.get(key)
        if (observed if observed is not None else None) != value:
            issues.append(f"runs.{key} 与报告来源代证据不一致")
    if str(row["generation_id"]) != str(generation.get("generation_id")):
        issues.append("runs.generation_id 与报告来源代证据不一致")
    if summary.get("source_generation_status") != generation.get("status"):
        issues.append("summary.source_generation_status 与 source_generation 不一致")
    if summary.get("source_generation_id") != generation.get("generation_id"):
        issues.append("summary.source_generation_id 与 source_generation 不一致")
    if summary.get("quality_gate") != generation.get("quality_gate"):
        issues.append("summary.quality_gate 与 source_generation 不一致")
    return issues

def _compare_quality_gate(report: dict[str, Any], issues: list[str]) -> None:
    """A blocked gate must not be accompanied by an executable pass verdict."""
    generation = report.get("source_generation")
    if not isinstance(generation, dict):
        return
    summary = report.get("summary", {})
    status = generation.get("status")
    gate = generation.get("quality_gate")
    if gate == "allowed" and status != "verified":
        issues.append("质量放行标记与来源代状态矛盾")
    if status == "unverified":
        if generation.get("same_generation_proven") is not False:
            issues.append("来源代未证却标记为已证")
        if gate != "blocked":
            issues.append("来源代未证却未阻断质量放行")
    if summary.get("quality_gate") == "allowed" and status != "verified":
        issues.append("summary.quality_gate 在来源代未证时错误放行")
    expected_verdict = "releasable" if gate == "allowed" else "not_releasable"
    if summary.get("quality_verdict") != expected_verdict:
        issues.append("summary.quality_verdict 与质量放行状态不一致")
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
        "SELECT layer_id, path, format, sha256, size_bytes, manifest_path, "
        "expected_sha256, generation_check_status, reread_consistent "
        "FROM input_files WHERE result_digest = ?",
        (digest,),
    ).fetchall()
    ledger = {row["layer_id"]: dict(row) for row in rows}
    reported = {str(item.get("layer_id")): item for item in report.get("input_files", [])}
    generation = report.get("source_generation", {})
    checks = {
        str(item.get("layer_id")): item
        for item in generation.get("layer_checks", [])
        if isinstance(item, dict)
    }
    if set(ledger) != set(reported):
        issues.append("输入文件摘要集合与台账不一致")
        return
    for layer_id in sorted(ledger):
        entry = ledger[layer_id]
        for key in ("layer_id", "path", "format", "sha256", "size_bytes"):
            expected = reported[layer_id].get(key)
            if key == "size_bytes":
                expected = int(expected or 0)
            if entry[key] != expected:
                issues.append(f"图层 {layer_id} 的输入文件摘要不一致")
                break
        check = checks.get(layer_id, {})
        if str(entry["generation_check_status"]) != str(check.get("status", "not_applicable")):
            issues.append(f"图层 {layer_id} 的来源代核对状态与台账不一致")
        if entry["manifest_path"] != check.get("manifest_path"):
            issues.append(f"图层 {layer_id} 的清单路径与台账不一致")
        if entry["expected_sha256"] != check.get("expected_sha256"):
            issues.append(f"图层 {layer_id} 的清单摘要与台账不一致")
        reread = entry["reread_consistent"]
        if reread is not None:
            reread = bool(reread)
        if reread != check.get("reread_consistent"):
            issues.append(f"图层 {layer_id} 的二次读取一致性与台账不一致")
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
