"""Markdown rendering for the responsibility-zone assignment section."""
from __future__ import annotations
from typing import Any

def render_assignment_md(assignment: dict[str, Any]) -> list[str]:
    lines = ["## 责任区分配预检", ""]
    config = assignment.get("config", {})
    lines.append("| 项目 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 状态 | {_cell(assignment.get('status', ''))} |")
    lines.append(f"| 点设施图层 | {_cell(config.get('point_layer', ''))} |")
    lines.append(f"| 责任区图层 | {_cell(config.get('zone_layer', ''))} |")
    lines.append(f"| 区 ID 字段 | {_cell(config.get('zone_id_field', ''))} |")
    lines.append(f"| 边界策略 | {_cell(config.get('boundary_policy', ''))} |")
    lines.append(f"| 每区容量上限 | {config.get('max_facilities_per_zone', '')} |")
    overview = assignment.get("input_summary", {})
    lines.append(f"| 可分配点 / 被排除点 | {overview.get('eligible_points', 0)} / {overview.get('excluded_points', 0)} |")
    lines.append(f"| 有效区 / 被排除区 | {overview.get('valid_zones', 0)} / {overview.get('excluded_zones', 0)} |")
    lines.append(f"| 空间候选对 | {overview.get('candidate_pairs', 0)} |")
    lines.append(f"| 待核实边界对 | {overview.get('review_pairs', 0)} |")
    lines.append("")
    issues = assignment.get("issues", [])
    if issues:
        lines.append("### 阻断问题")
        lines.append("")
        for issue in issues:
            lines.append(f"- `{_cell(issue.get('code', ''))}` {_cell(issue.get('message', ''))}")
        lines.append("")
    lines.extend(_render_assignment_exclusions(assignment))
    lines.extend(_render_assignment_candidates(assignment))
    lines.extend(_render_assignment_plans(assignment))
    lines.extend(_render_assignment_blocking(assignment))
    return lines

def _render_assignment_exclusions(assignment: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    excluded_zones = [z for z in assignment.get("zones", []) if z.get("status") == "excluded"]
    excluded_points = [p for p in assignment.get("points", []) if p.get("status") == "excluded"]
    if not excluded_zones and not excluded_points:
        return lines
    lines.append("### 未进入分配的要素（显式排除）")
    lines.append("")
    lines.append("| 类别 | 要素 | 原因 |")
    lines.append("| --- | --- | --- |")
    for zone in excluded_zones:
        label = zone.get("zone_id") or zone.get("feature_id") or "—"
        lines.append(f"| 责任区 | {_cell(label)} | {_cell(zone.get('exclusion_reason', ''))} |")
    for point in excluded_points:
        lines.append(f"| 点设施 | {_cell(point.get('point_id', ''))} | {_cell(point.get('exclusion_reason', ''))} |")
    lines.append("")
    return lines

def _render_assignment_candidates(assignment: dict[str, Any]) -> list[str]:
    lines = ["### 空间候选归属", ""]
    candidates = assignment.get("candidates", [])
    review_pairs = assignment.get("review_pairs", [])
    if not candidates and not review_pairs:
        lines.append("无候选归属关系。")
        lines.append("")
        return lines
    if candidates:
        lines.append("| 点设施 | 责任区 | 关系 | 组件 | 锚点距离 |")
        lines.append("| --- | --- | --- | --- | ---: |")
        for item in candidates:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    _cell(item.get("point_id", "")),
                    _cell(item.get("zone_id", "")),
                    _cell(item.get("relation", "")),
                    _cell(item.get("component", "")),
                    item.get("distance", ""),
                )
            )
        lines.append("")
    if review_pairs:
        lines.append("边界待核实（按配置不计入候选）：")
        lines.append("")
        lines.append("| 点设施 | 责任区 | 关系 | 组件 | 锚点距离 |")
        lines.append("| --- | --- | --- | --- | ---: |")
        for item in review_pairs:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    _cell(item.get("point_id", "")),
                    _cell(item.get("zone_id", "")),
                    _cell(item.get("relation", "")),
                    _cell(item.get("component", "")),
                    item.get("distance", ""),
                )
            )
        lines.append("")
    return lines

def _render_assignment_plans(assignment: dict[str, Any]) -> list[str]:
    plans = assignment.get("plans", [])
    if not plans:
        return []
    lines = ["### 分配方案（两种运营取舍）", ""]
    lines.append("| 方案 | 已分配 | 未分配 | 锚点总距离 | 最大区负载 | 负载极差 | 非支配 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for plan in plans:
        metrics = plan.get("metrics", {})
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} |".format(
                _cell(plan.get("objective", "")),
                metrics.get("assigned", 0),
                metrics.get("unassigned", 0),
                metrics.get("total_distance", 0),
                metrics.get("max_load", 0),
                metrics.get("load_spread", 0),
                "是" if plan.get("non_dominated") else "否",
            )
        )
    lines.append("")
    for plan in plans:
        metrics = plan.get("metrics", {})
        lines.append(f"#### 方案 `{_cell(plan.get('objective', ''))}`")
        lines.append("")
        loads = metrics.get("zone_loads", {})
        load_text = ", ".join(f"{key}:{loads[key]}" for key in sorted(loads)) or "—"
        lines.append(f"- 区负载：{_cell(load_text)}")
        lines.append(f"- 锚点总距离：{metrics.get('total_distance', 0)}；负载平方和：{metrics.get('load_sum_squares', 0)}")
        assignments = plan.get("assignments", [])
        if assignments:
            lines.append("")
            lines.append("| 点设施 | 责任区 | 关系 | 组件 | 距离 |")
            lines.append("| --- | --- | --- | --- | ---: |")
            for item in assignments:
                lines.append(
                    "| {} | {} | {} | {} | {} |".format(
                        _cell(item.get("point_id", "")),
                        _cell(item.get("zone_id", "")),
                        _cell(item.get("relation", "")),
                        _cell(item.get("component", "")),
                        item.get("distance", ""),
                    )
                )
        unassigned = plan.get("unassigned", [])
        if unassigned:
            lines.append("")
            lines.append("| 未分配点 | 原因 |")
            lines.append("| --- | --- |")
            for item in unassigned:
                lines.append(f"| {_cell(item.get('point_id', ''))} | {_cell(item.get('reason', ''))} |")
        lines.append("")
    return lines

def _render_assignment_blocking(assignment: dict[str, Any]) -> list[str]:
    blocking = assignment.get("blocking")
    if not blocking:
        return []
    lines = ["### 无法全量分配的阻断证据", ""]
    lines.append(
        f"受限点集合需求 {blocking.get('demand', 0)}，"
        f"候选区总容量 {blocking.get('total_capacity', 0)}，"
        f"缺口 {blocking.get('deficit', 0)}。"
    )
    lines.append("")
    lines.append("| 受限点 |")
    lines.append("| --- |")
    for point_id in blocking.get("deficient_points", []):
        lines.append(f"| {_cell(point_id)} |")
    lines.append("")
    lines.append("| 候选责任区 | 容量 |")
    lines.append("| --- | ---: |")
    for zone in blocking.get("candidate_zones", []):
        lines.append(f"| {_cell(zone.get('zone_id', ''))} | {zone.get('capacity', 0)} |")
    lines.append("")
    return lines


def _cell(value: Any) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
