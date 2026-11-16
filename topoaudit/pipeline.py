"""Shared run pipeline used by the CLI and the offline demo.

输出发布采用分阶段协议（见 ``topoaudit.publish``）：

1. 全部报告文件先写入版本库 ``.topoaudit/versions/<result_digest>/`` 并
   记录各文件 SHA-256 清单；
2. 台账在单个 SQLite 事务内提交（按 ``result_digest`` 幂等）；
3. 交叉校验暂存产物与台账；
4. 校验通过后才把可见文件投影到输出目录顶层，并原子切换当前版本指针。

任一阶段中断都不会让半套报告成为当前可见结果；下一次运行会先执行恢
复，再决定是幂等跳过还是继续发布。
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Optional
from .config import load_config
from .engine import run_audit
from .errors import ReportError
from .publish import RECOVER_ROLL_FORWARD, Publisher
from .report_html import render_html
from .report_json import build_report
from .report_md import render_markdown
from .util import pretty_json
REPORT_JSON = "report.json"
REPORT_MD = "report.md"
REPORT_HTML = "report.html"
LEDGER_DB = "audit.sqlite"
INPUTS_JSON = "inputs.json"
SUMMARY_TXT = "summary.txt"

def execute(
    config_path: str | Path,
    out_dir: str | Path,
    *,
    recover_policy: str = RECOVER_ROLL_FORWARD,
    fail_at: Optional[str] = None,
    recovery_actions: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], dict[str, Path]]:
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
    publisher = Publisher(destination, fail_at=fail_at)
    _refuse_input_overwrite(config, paths, publisher)
    actions = publisher.recover(policy=recover_policy)
    if recovery_actions is not None:
        recovery_actions.extend(actions)
    digest = str(report["result_digest"])
    if publisher.is_current(digest):
        # 同一输入重复运行：可见版本、台账与指针均已一致，幂等跳过。
        publisher.note_noop(digest)
        return report, paths
    files = {
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
    publisher.stage(
        digest,
        files,
        meta={
            "run_label": report.get("run_label", ""),
            "input_digest": report.get("input_digest", ""),
            "config_digest": report.get("config_digest", ""),
        },
    )
    publisher.commit_ledger(report, digest)
    publisher.verify_staged(digest)
    pointer = publisher.current()
    publisher.publish(digest, previous=str(pointer["result_digest"]) if pointer else None)
    return report, paths

def _refuse_input_overwrite(
    config: Any,
    paths: dict[str, Path],
    publisher: Optional[Publisher] = None,
) -> None:
    protected = {config.path.resolve()} | {
        layer.resolved_path.resolve() for layer in config.layers
    }
    targets = list(paths.values())
    if publisher is not None:
        targets.extend([publisher.current_path, publisher.journal_path])
    for target in targets:
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
