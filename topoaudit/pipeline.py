"""Shared run pipeline used by the CLI and the offline demo."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Callable, Optional
from .config import load_config
from .engine import run_audit
from .errors import ReportError
from .publish import (
    CURRENT_LINK,
    LEDGER_DB_NAME,
    MANIFEST_NAME,
    REPORT_FILE_NAMES,
    RecoveryAction,
    publish_run,
    recover_pending,
    staging_dir,
    version_dir,
)
from .report_html import render_html
from .report_json import build_report
from .report_md import render_markdown
from .util import pretty_json
REPORT_JSON = "report.json"
REPORT_MD = "report.md"
REPORT_HTML = "report.html"
LEDGER_DB = LEDGER_DB_NAME
INPUTS_JSON = "inputs.json"
SUMMARY_TXT = "summary.txt"

def execute(
    config_path: str | Path,
    out_dir: str | Path,
    *,
    recovery_policy: Optional[str] = None,
    on_recovery: Optional[Callable[[RecoveryAction], None]] = None,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Run an audit and atomically publish it as the output's current result.

    Any interrupted earlier publish session in ``out_dir`` is settled first
    (see :mod:`topoaudit.publish`); the previously published version stays
    fully readable until the new one is completely staged, recorded in the
    ledger and verified.
    """
    config = load_config(config_path)
    result = run_audit(config)
    report = build_report(result)
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": destination / REPORT_JSON,
        "markdown": destination / REPORT_MD,
        "html": destination / REPORT_HTML,
        "ledger": destination / LEDGER_DB,
        "inputs": destination / INPUTS_JSON,
        "summary": destination / SUMMARY_TXT,
    }
    _refuse_input_overwrite(config, destination, report["result_digest"])
    actions = recover_pending(destination, policy=recovery_policy)
    if on_recovery is not None:
        for action in actions:
            on_recovery(action)
    file_contents = {
        REPORT_JSON: pretty_json(report),
        REPORT_MD: render_markdown(report),
        REPORT_HTML: render_html(report),
        INPUTS_JSON: pretty_json(
            {
                "config_digest": report["config_digest"],
                "input_digest": report["input_digest"],
                "result_digest": report["result_digest"],
                "input_files": report["input_files"],
            }
        ),
        SUMMARY_TXT: render_terminal_summary(report, paths),
    }
    publish_run(destination, report, file_contents)
    return report, paths

def _refuse_input_overwrite(config: Any, destination: Path, digest: str) -> None:
    protected_resolved = {config.path.resolve()} | {
        layer.resolved_path.resolve() for layer in config.layers
    }
    protected_absolute = {
        Path(os.path.abspath(str(config.path))),
        *[Path(os.path.abspath(str(layer.resolved_path))) for layer in config.layers],
    }
    targets = [destination / name for name in REPORT_FILE_NAMES]
    targets.append(destination / LEDGER_DB)
    targets.append(destination / CURRENT_LINK)
    for directory in (staging_dir(destination, digest), version_dir(destination, digest)):
        targets.extend(directory / name for name in REPORT_FILE_NAMES)
        targets.append(directory / MANIFEST_NAME)
    for target in targets:
        if target.resolve() in protected_resolved or Path(
            os.path.abspath(str(target))
        ) in protected_absolute:
            raise ReportError(
                "TOPO-RPT-004",
                "输出文件与输入或配置文件路径冲突，拒绝覆盖",
                str(target),
            )

def render_terminal_summary(report: dict[str, Any], paths: dict[str, Path]) -> str:
    summary = report.get("summary", {})
    severity = summary.get("severity_counts", {})
    lines = [
        "矢量拓扑验台 - 审计完成",
        f"run_label       : {report.get('run_label', '')}",
        f"input_digest    : {report.get('input_digest', '')}",
        f"result_digest   : {report.get('result_digest', '')}",
        (
            "layers / features: "
            f"{summary.get('total_layers', 0)} / {summary.get('total_features', 0)}"
        ),
        (
            "findings        : "
            f"{summary.get('total_findings', 0)} "
            f"(error={severity.get('error', 0)}, "
            f"warning={severity.get('warning', 0)}, "
            f"info={severity.get('info', 0)})"
        ),
        "",
        "输出：",
    ]
    for key in ("json", "markdown", "html", "ledger", "inputs", "summary"):
        lines.append(f"  {key:8s}: {paths[key]}")
    return "\n".join(lines) + "\n"
