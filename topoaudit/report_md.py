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
    summary = report.get("summary", {})
    lines.append(f"| 图层数 | {summary.get('total_layers', 0)} |")
    lines.append(f"| 要素数 | {summary.get('total_features', 0)} |")
    lines.append(f"| finding 总数 | {summary.get('total_findings', 0)} |")
    severity_counts = summary.get("severity_counts", {})
    for severity in ("error", "warning", "info"):
        lines.append(f"| {severity} | {severity_counts.get(severity, 0)} |")
    lines.append("")
    lines.extend(_render_layers(report.get("layers", [])))
    lines.extend(_render_rules(report.get("rules", [])))
    lines.extend(_render_coverage(report.get("coverage")))
    lines.extend(_render_findings(report.get("findings", [])))
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
    lines.append("")
    return "\n".join(lines)

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

def _render_coverage(coverage: Any) -> list[str]:
    lines = ["## 覆盖空缺审计", ""]
    if coverage is None:
        lines.append("本次运行未启用覆盖空缺审计（配置中无 `coverage` 块）。")
        lines.append("")
        return lines
    summary = coverage.get("coverage", {})
    aoi = coverage.get("aoi", {})
    lines.append("| 项目 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 状态 | {_cell(coverage.get('status', ''))} |")
    lines.append(f"| 验收模式 | {_cell(coverage.get('mode', ''))} |")
    lines.append(f"| 被审计图层 | {_cell(coverage.get('layer_id', ''))} |")
    lines.append(f"| AOI 类型 / 面积 | {_cell(aoi.get('geometry_type', ''))} / {_fmt(summary.get('aoi_area'))} |")
    lines.append(f"| AOI 洞数 | {aoi.get('hole_count', 0)} |")
    lines.append(f"| 已覆盖面积 | {_fmt(summary.get('covered_area'))} |")
    lines.append(f"| 未覆盖面积 | {_fmt(summary.get('gap_area'))} |")
    lines.append(f"| 必须整改面积 | {_fmt(summary.get('required_area'))} |")
    lines.append(f"| 空缺组件数 / 必须整改 | {_fmt(summary.get('gap_component_count'))} / {_fmt(summary.get('required_component_count'))} |")
    lines.append(f"| 边界待补面积 | {_fmt(summary.get('boundary_area'))} |")
    lines.append(f"| 未覆盖率 | {_fmt(summary.get('uncovered_percent'))}% |")
    lines.append(f"| 覆盖输入摘要 | `{coverage.get('input_digest', '')}` |")
    lines.append("")
    blocked = coverage.get("blocked")
    if blocked:
        lines.append(f"**不可判定原因**：`{_cell(blocked.get('reason', ''))}`")
        lines.append("")
        issues = blocked.get("feature_issues") or []
        if issues:
            lines.append("| 要素 | 源索引 | 部件 | 几何类型 | 原因 |")
            lines.append("| --- | ---: | --- | --- | --- |")
            for issue in issues:
                lines.append(
                    "| {feature} | {index} | {component} | {kind} | {reason} |".format(
                        feature=_cell(issue.get("feature_id", "")),
                        index=issue.get("source_index", ""),
                        component=_cell(issue.get("component_index", "—")),
                        kind=_cell(issue.get("geometry_type", "")),
                        reason=_cell(issue.get("reason", "")),
                    )
                )
            lines.append("")
        return lines
    components = coverage.get("components", [])
    if not components:
        lines.append("AOI 已被地块并集完整覆盖，无空缺组件。")
        lines.append("")
        return lines
    lines.append("| 组件 | 分类 | 必须整改 | 面积 | bbox | 相邻地块 |")
    lines.append("| --- | --- | --- | ---: | --- | --- |")
    for component in components:
        lines.append(
            "| {cid} | {cls} | {required} | {area} | {bbox} | {adjacent} |".format(
                cid=_cell(component.get("component_id", "")),
                cls=_cell(component.get("classification", "")),
                required="是" if component.get("required") else "否",
                area=_fmt(component.get("area")),
                bbox=_cell(", ".join(str(v) for v in component.get("bbox", []))),
                adjacent=_cell(", ".join(component.get("adjacent_feature_ids", [])) or "—"),
            )
        )
    lines.append("")
    return lines

def _fmt(value: Any) -> str:
    return "—" if value is None else str(value)

def _render_findings(findings: list[dict[str, Any]]) -> list[str]:
    lines = [f"## Findings（{len(findings)}）", ""]
    if not findings:
        lines.append("未发现任何问题。")
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
    return lines

def _cell(value: Any) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")

def write_report_markdown(path: str | Path, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_markdown(report), encoding="utf-8")
