"""Pre-publication verification of a candidate result version.

A candidate (staged or already promoted) may only become the current
result when its artifact set, its manifest, the committed ledger intent
and the ledger rows all agree on the same ``result_digest`` identity.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .errors import AuditError
from .ledger_verify import load_report, verify_report_and_ledger
from .publish_layout import (
    INPUTS_JSON_NAME,
    REPORT_FILE_NAMES,
    REPORT_HTML_NAME,
    REPORT_JSON_NAME,
    REPORT_MD_NAME,
    SUMMARY_TXT_NAME,
    verify_files_against_manifest,
)


def verify_candidate(
    directory: Path,
    manifest: Optional[dict[str, Any]],
    ledger_path: Path,
    digest: str,
    committed_files: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Full pre-publication verification of one candidate version.

    Checks the artifact set against the manifest, the manifest against the
    committed ledger intent, the report against the ledger rows, and that
    every artifact carries the same ``result_digest`` identity.
    """
    issues: list[str] = []
    if manifest is None:
        return ["发布清单缺失或无法解析"]
    if str(manifest.get("result_digest", "")) != digest:
        issues.append("清单 result_digest 与发布会话不一致")
    files_meta = manifest.get("files", {})
    for name in REPORT_FILE_NAMES:
        if name not in files_meta:
            issues.append(f"清单缺少产物 {name}")
    issues.extend(verify_files_against_manifest(directory, manifest))
    if committed_files is not None:
        for name in REPORT_FILE_NAMES:
            want = (committed_files.get(name) or {}).get("sha256")
            have = (files_meta.get(name) or {}).get("sha256")
            if want is not None and want != have:
                issues.append(f"产物 {name} 与台账中的提交意向不一致")
    report_file = directory / REPORT_JSON_NAME
    report: Optional[dict[str, Any]] = None
    if report_file.is_file():
        try:
            report = load_report(report_file)
        except AuditError as exc:
            issues.append(f"报告无法解析: {exc}")
    if report is None:
        issues.append("缺少报告文件 report.json")
        return issues
    if str(report.get("result_digest", "")) != digest:
        issues.append("报告 result_digest 与发布会话不一致")
    try:
        result = verify_report_and_ledger(report_file, ledger_path)
        issues.extend(result.issues)
    except AuditError as exc:
        issues.append(f"台账交叉校验失败: {exc}")
    for name in (REPORT_MD_NAME, REPORT_HTML_NAME, SUMMARY_TXT_NAME):
        path = directory / name
        if not path.is_file():
            continue
        if digest not in path.read_text(encoding="utf-8", errors="replace"):
            issues.append(f"{name} 未包含当前 result_digest，疑似混杂版本")
    inputs_file = directory / INPUTS_JSON_NAME
    if inputs_file.is_file():
        try:
            inputs = json.loads(inputs_file.read_text(encoding="utf-8"))
        except ValueError:
            inputs = None
        if not isinstance(inputs, dict):
            issues.append("inputs.json 无法解析")
        elif str(inputs.get("result_digest", "")) != digest:
            issues.append("inputs.json 的 result_digest 与发布会话不一致")
    return issues
