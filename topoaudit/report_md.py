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
    lines.extend(_render_network_audit(report.get("network_audit", [])))
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

def _render_network_audit(networks: list[dict[str, Any]]) -> list[str]:
    lines = [f"## 线网可达性审计（{len(networks)}）", ""]
    if not networks:
        lines.append("未配置线网可达性审计的图层。")
        lines.append("")
        return lines
    for network in networks:
        lines.append(f"### 图层 `{_cell(network.get('layer_id', ''))}`")
        lines.append("")
        mode = (
            f"grade_field={network.get('grade_field')}"
            if network.get("grade_field")
            else f"crossing_policy={network.get('crossing_policy')}"
        )
        lines.append(
            "| 入口要素 | 节点 | 边 | 连通分量 | 有效部件 | 无效部件 | 解释模式 |"
        )
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | --- |")
        lines.append(
            "| {entries} | {nodes} | {edges} | {components} | {valid} | {invalid} | {mode} |".format(
                entries=_cell(", ".join(network.get("entry_feature_ids", []))),
                nodes=network.get("node_count", 0),
                edges=network.get("edge_count", 0),
                components=len(network.get("connected_components", [])),
                valid=network.get("valid_part_count", 0),
                invalid=network.get("invalid_part_count", 0),
                mode=_cell(mode),
            )
        )
        lines.append("")
        lines.append("| 入口 | 可达末端 | 不可达末端 |")
        lines.append("| --- | --- | --- |")
        for entry in network.get("entry_reachability", []):
            lines.append(
                "| {entry} | {reachable} | {unreachable} |".format(
                    entry=_cell(entry.get("entry_feature_id", "")),
                    reachable=_cell(", ".join(entry.get("reachable_terminal_ids", [])) or "—"),
                    unreachable=_cell(
                        ", ".join(entry.get("unreachable_terminal_ids", [])) or "—"
                    ),
                )
            )
        lines.append("")
        blocked = network.get("blocked_contacts", [])
        if blocked:
            lines.append(f"阻断/待核实接点（{len(blocked)}）：")
            lines.append("")
            lines.append("| 类型 | 坐标 | 要素 A | 要素 B | 判定 | 原因 |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for contact in blocked:
                lines.append(
                    "| {kind} | `{coordinate}` | {a} | {b} | {decision} | {reason} |".format(
                        kind=_cell(contact.get("contact_type", "")),
                        coordinate=_cell(canonical_json(contact.get("coordinate", []))),
                        a=_cell(contact.get("feature_a", "")),
                        b=_cell(contact.get("feature_b", "")),
                        decision=_cell(contact.get("decision", "")),
                        reason=_cell(contact.get("reason", "")),
                    )
                )
            lines.append("")
        unreachable = network.get("unreachable_terminals", [])
        if unreachable:
            lines.append(f"不可达末端证据（{len(unreachable)}）：")
            lines.append("")
            lines.append("| 末端 | 坐标 | 最近可达节点 | 距离 | 分量 | 原始线段证据 |")
            lines.append("| --- | --- | --- | ---: | --- | --- |")
            for terminal in unreachable:
                evidence = canonical_json(
                    {
                        "blocked_contacts": terminal.get("blocked_contacts", []),
                        "segment_evidence": terminal.get("segment_evidence", []),
                    }
                )
                lines.append(
                    "| {terminal} | `{coordinate}` | {nearest} | {distance} | {component} | `{evidence}` |".format(
                        terminal=_cell(terminal.get("terminal_id", "")),
                        coordinate=_cell(canonical_json(terminal.get("coordinate", []))),
                        nearest=_cell(terminal.get("nearest_reachable_node") or "—"),
                        distance=terminal.get("distance_to_network"),
                        component=_cell(terminal.get("component_id") or "—"),
                        evidence=_cell(evidence),
                    )
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
