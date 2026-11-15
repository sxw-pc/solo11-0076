"""Shared run pipeline used by the CLI and the offline demo."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from .config import load_config
from .engine import run_audit
from .errors import ReportError
from .ledger import write_report
from .report_html import write_report_html
from .report_json import build_report, write_report_json
from .report_md import write_report_markdown
from .util import pretty_json
REPORT_JSON = "report.json"
REPORT_MD = "report.md"
REPORT_HTML = "report.html"
LEDGER_DB = "audit.sqlite"
INPUTS_JSON = "inputs.json"
SUMMARY_TXT = "summary.txt"

def execute(config_path: str | Path, out_dir: str | Path) -> tuple[dict[str, Any], dict[str, Path]]:
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
    _refuse_input_overwrite(config, paths)
    write_report_json(paths["json"], report)
    write_report_markdown(paths["markdown"], report)
    write_report_html(paths["html"], report)
    paths["inputs"].write_text(
        pretty_json(
            {
                "config_digest": report["config_digest"],
                "input_digest": report["input_digest"],
                "result_digest": report["result_digest"],
                "input_files": report["input_files"],
            }
        ),
        encoding="utf-8",
    )
    paths["summary"].write_text(render_terminal_summary(report, paths), encoding="utf-8")
    write_report(paths["ledger"], report)
    return report, paths

def _refuse_input_overwrite(config: Any, paths: dict[str, Path]) -> None:
    protected = {config.path.resolve()} | {
        layer.resolved_path.resolve() for layer in config.layers
    }
    for target in paths.values():
        if target.resolve() in protected:
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
