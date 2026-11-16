"""Human-review Markdown report renderer."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from .util import canonical_json

def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    label = report.get("run_label", "")
    lines.append(f"# 矢量拓扑验台审计报告：{label}")
    lines.append("")
    lines.append("## 运行摘要")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| schema_version | {_cell(report.get('schema_version', ''))} |")
    lines.append(f"| run_label | {_cell(label)} |")
    lines.append(f"| input_digest | `{report.get('input_digest', '')}` |")
    lines.append(f"| config_digest | `{report.get('config_digest', '')}` |")
    lines.append(f"| result_digest | `{report.get('result_digest', '')}` |")
    generation = report.get("source_generation", {})
    lines.append(f"| 来源代状态 | {_generation_label(generation)} |")
    lines.append(f"| generation_id | {_cell(generation.get('generation_id') or '—')} |")
    lines.append(f"| 清单 SHA-256 | {_cell(generation.get('manifest_sha256') or '—')} |")
    lines.append(f"| 清单规范摘要 | {_cell(generation.get('manifest_digest') or '—')} |")
    lines.append(f"| 质量放行 | {_cell(generation.get('quality_gate', 'blocked'))} |")
    summary = report.get("summary", {})
    lines.append(f"| 图层数 | {summary.get('total_layers', 0)} |")
    lines.append(f"| 要素数 | {summary.get('total_features', 0)} |")
    lines.append(f"| finding 总数 | {summary.get('total_findings', 0)} |")
    severity_counts = summary.get("severity_counts", {})
    for severity in ("error", "warning", "info"):
        lines.append(f"| {severity} | {severity_counts.get(severity, 0)} |")
    lines.append("")
    if generation.get("status") == "failed":
        lines.append(
            "> **同代核验失败、不可作同代质量放行。** 所提供清单与本次实际读入的"
            "图层字节不一致；本报告中的跨图层结论不能用于同代质量放行。"
        )
        lines.append("")
    elif generation.get("status") != "verified":
        lines.append(
            "> **来源代未证、不可作同代质量放行。** 未提供上游发布清单；"
            "本报告中的跨图层结论仅供参考，不能用于同代质量放行。"
        )
        lines.append("")
    lines.extend(_render_generation(generation))
    lines.extend(_render_layers(report.get("layers", [])))
    lines.extend(_render_rules(report.get("rules", [])))
    lines.extend(_render_findings(report.get("findings", []), generation))
    lines.extend(_render_inputs(report.get("input_files", [])))
    lines.append("## 限制说明")
    lines.append("")
    for item in report.get("limitations", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append(
        "本报告由本地确定性审计流程生成；重复运行且输入、配置不变时，"
        "核心内容与 `result_digest` 一致。"
    )
    if generation.get("status") != "verified":
        lines.append(
            "来源代未证：本报告的跨图层审计结论不可作为同代质量放行依据。"
            if generation.get("status") == "unverified"
            else "同代核验失败：本报告的跨图层审计结论不可作为同代质量放行依据。"
        )
    lines.append("")
    return "\n".join(lines)

def _generation_label(generation: dict[str, Any]) -> str:
    status = generation.get("status", "unverified")
    return {
        "verified": "同代已证（verified）",
        "unverified": "来源代未证（unverified）",
        "failed": "同代核验失败（failed）",
    }.get(status, status)

def _render_generation(generation: dict[str, Any]) -> list[str]:
    lines = ["## 来源代核验", ""]
    lines.append(f"- 策略：`{_cell(generation.get('policy', 'observe'))}`")
    lines.append(f"- 状态：{_generation_label(generation)}")
    proven = generation.get("same_generation_proven") is True
    lines.append(f"- 同代已证：{'是' if proven else '否'}")
    lines.append(f"- 发布代：{_cell(generation.get('generation_id') or '—')}")
    lines.append(f"- 清单路径：{_cell(generation.get('manifest_path') or '—')}")
    lines.append(f"- 清单二次读取一致：{_tri_state(generation.get('manifest_read_consistent'))}")
    lines.append(f"- 质量放行：`{_cell(generation.get('quality_gate', 'blocked'))}`")
    missing = generation.get("missing_layer_ids") or []
    extra = generation.get("extra_layer_ids") or []
    if missing:
        lines.append(f"- 清单缺失图层：{_cell(', '.join(missing))}")
    if extra:
        lines.append(f"- 清单多余成员：{_cell(', '.join(extra))}")
    issues = generation.get("issues") or []
    if issues:
        lines.append("")
        lines.append("核验问题：")
        for issue in issues:
            lines.append(f"- {_cell(issue)}")
    lines.append("")
    checks = generation.get("layer_checks") or []
    if checks:
        lines.append("| 图层 | 清单路径 | 预期 SHA-256 | 实际 SHA-256 | 二次读取 | 状态 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for check in checks:
            lines.append(
                "| {layer} | {path} | `{expected}` | `{actual}` | {reread} | {status} |".format(
                    layer=_cell(check.get("layer_id", "")),
                    path=_cell(check.get("manifest_path") or "—"),
                    expected=_cell(check.get("expected_sha256") or "—"),
                    actual=_cell(check.get("actual_sha256") or "—"),
                    reread=_tri_state(check.get("reread_consistent")),
                    status=_cell(check.get("status", "")),
                )
            )
        lines.append("")
    return lines

def _tri_state(value: Any) -> str:
    if value is True:
        return "一致"
    if value is False:
        return "不一致"
    return "—"

def _render_layers(layers: list[dict[str, Any]]) -> list[str]:
    lines = ["## 图层统计", ""]
    lines.append("| 图层 | 要素数 | 几何类型 | finding | error | warning | info | bbox |")
    lines.append("| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |")
    for layer in layers:
        kinds = ", ".join(
            f"{key}:{value}" for key, value in layer.get("geometry_type_counts", {}).items()
        ) or "—"
        severity = layer.get("severity_counts", {})
        bbox = layer.get("bbox")
        bbox_text = ", ".join(str(value) for value in bbox) if bbox else "—"
        lines.append(
            "| {layer} | {features} | {kinds} | {findings} | {error} | {warning} | {info} | {bbox} |".format(
                layer=_cell(layer.get("layer_id", "")),
                features=layer.get("feature_count", 0),
                kinds=_cell(kinds),
                findings=layer.get("finding_count", 0),
                error=severity.get("error", 0),
                warning=severity.get("warning", 0),
                info=severity.get("info", 0),
                bbox=_cell(bbox_text),
            )
        )
    lines.append("")
    return lines

def _render_rules(rules: list[dict[str, Any]]) -> list[str]:
    lines = ["## 规则统计", ""]
    lines.append("| 规则 ID | 严重级别 | 名称 | 数量 |")
    lines.append("| --- | --- | --- | ---: |")
    for rule in rules:
        lines.append(
            f"| {_cell(rule.get('rule_id', ''))} | {_cell(rule.get('severity', ''))} | "
            f"{_cell(rule.get('title', ''))} | {rule.get('count', 0)} |"
        )
    lines.append("")
    return lines

def _render_findings(
    findings: list[dict[str, Any]], generation: dict[str, Any]
) -> list[str]:
    lines = [f"## Findings（{len(findings)}）", ""]
    if not findings:
        proven = generation.get("same_generation_proven") is True
        if proven:
            lines.append("未发现任何问题，且来源代已证，可进入质量放行评审。")
        else:
            lines.append(
                "未发现任何问题，但来源代未证，不能据此给出可执行的质量通过结论。"
            )
        lines.append("")
        return lines
    lines.append("| Finding ID | 严重级别 | 规则 | 图层 | 要素 | 关联要素 | 证据 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for finding in findings:
        related = ", ".join(finding.get("related_feature_ids", [])) or "—"
        evidence = canonical_json(finding.get("evidence", {}))
        lines.append(
            "| `{fid}` | {severity} | {rule} | {layer} | {feature} | {related} | `{evidence}` |".format(
                fid=_cell(finding.get("finding_id", "")),
                severity=_cell(finding.get("severity", "")),
                rule=_cell(finding.get("rule_id", "")),
                layer=_cell(finding.get("layer_id", "")),
                feature=_cell(finding.get("feature_id") or "—"),
                related=_cell(related),
                evidence=_cell(evidence),
            )
        )
    lines.append("")
    return lines

def _render_inputs(inputs: list[dict[str, Any]]) -> list[str]:
    lines = ["## 输入文件摘要", ""]
    lines.append("| 图层 | 路径（配置） | 格式 | 字节 | SHA-256 |")
    lines.append("| --- | --- | --- | ---: | --- |")
    for item in inputs:
        lines.append(
            "| {layer} | {path} | {fmt} | {size} | `{digest}` |".format(
                layer=_cell(item.get("layer_id", "")),
                path=_cell(item.get("path", "")),
                fmt=_cell(item.get("format", "")),
                size=item.get("size_bytes", 0),
                digest=item.get("sha256", ""),
            )
        )
    lines.append("")
    lines.append(
        "`input_digest` 与上表只表示实际读入内容的内容身份；"
        "它不能单独证明各图层来自同一上游发布代。"
    )
    lines.append("")
    return lines

def _cell(value: Any) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")

def write_report_markdown(path: str | Path, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_markdown(report), encoding="utf-8")
