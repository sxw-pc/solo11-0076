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
    lines.extend(_render_coverage(report.get("coverage", [])))
    lines.extend(_render_rules(report.get("rules", [])))
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

def _render_coverage(sections: list[dict[str, Any]]) -> list[str]:
    if not sections:
        return []
    lines = ["## 线覆盖审计", ""]
    for section in sections:
        lines.append(
            "### 规则 `{rule}`：`{line}` → `{poly}`".format(
                rule=_cell(section.get("rule_id", "")),
                line=_cell(section.get("line_layer", "")),
                poly=_cell(section.get("polygon_layer", "")),
            )
        )
        lines.append("")
        lines.append(
            "| 边界口径 | 容差 | 启用 | 已覆盖 | 部分 | 未覆盖 | 不可判定 | 空线 |"
        )
        lines.append("| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |")
        counts = section.get("status_counts", {})
        lines.append(
            "| {mode} | {tol} | {enabled} | {covered} | {partial} | {uncovered} | "
            "{undetermined} | {empty} |".format(
                mode=_cell(section.get("boundary_mode", "")),
                tol=section.get("tolerance", 0),
                enabled="是" if section.get("enabled", True) else "否",
                covered=counts.get("covered", 0),
                partial=counts.get("partial", 0),
                uncovered=counts.get("uncovered", 0),
                undetermined=counts.get("undetermined", 0),
                empty=counts.get("empty", 0),
            )
        )
        lines.append("")
        lines.append(
            "| 线要素 | 状态 | 总长 | 已覆盖 | 未覆盖 | 不可判定 | 未覆盖区段 |"
        )
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
        for record in section.get("lines", []):
            slices = record.get("uncovered_slices", [])
            preview = "; ".join(
                "{part} seg{seg} {kind} {length} [{start}→{end}]".format(
                    part=item.get("part", ""),
                    seg=item.get("segment", ""),
                    kind=item.get("kind", ""),
                    length=item.get("length", 0),
                    start=item.get("start"),
                    end=item.get("end"),
                )
                for item in slices[:5]
            ) or "—"
            lines.append(
                "| {feature} | {status} | {total} | {covered} | {uncovered} | "
                "{undetermined} | {slices} |".format(
                    feature=_cell(record.get("feature_id", "")),
                    status=_cell(record.get("status", "")),
                    total=record.get("total_length", 0),
                    covered=record.get("covered_length", 0),
                    uncovered=record.get("uncovered_length", 0),
                    undetermined=record.get("undetermined_length", 0),
                    slices=_cell(preview),
                )
            )
        invalid = section.get("invalid_polygon_sources", [])
        if invalid:
            lines.append("")
            lines.append(
                "不可判定面来源："
                + "; ".join(
                    "{feature} {component} {reasons}".format(
                        feature=_cell(item.get("feature_id", "")),
                        component=_cell(item.get("component", "")),
                        reasons=_cell(", ".join(item.get("reasons", []))),
                    )
                    for item in invalid
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
