"""HTML rendering for the responsibility-zone assignment section."""
from __future__ import annotations
import html
from typing import Any

def render_assignment_html(assignment: dict[str, Any]) -> list[str]:
    config = assignment.get("config", {})
    overview = assignment.get("input_summary", {})
    lines = ["<h2>责任区分配预检</h2>"]
    lines.append('<div class="card">')
    lines.append(
        "<p>状态 <b>{}</b> · 点层 {} · 区层 {} · 边界策略 {} · 每区容量 {}</p>".format(
            _e(assignment.get("status", "")),
            _e(config.get("point_layer", "")),
            _e(config.get("zone_layer", "")),
            _e(config.get("boundary_policy", "")),
            _e(config.get("max_facilities_per_zone", "")),
        )
    )
    lines.append(
        "<p class=\"muted\">可分配点 {} / 排除 {} · 有效区 {} / 排除 {} · 候选对 {} · 边界待核实 {}</p>".format(
            _e(overview.get("eligible_points", 0)),
            _e(overview.get("excluded_points", 0)),
            _e(overview.get("valid_zones", 0)),
            _e(overview.get("excluded_zones", 0)),
            _e(overview.get("candidate_pairs", 0)),
            _e(overview.get("review_pairs", 0)),
        )
    )
    lines.append("</div>")
    issues = assignment.get("issues", [])
    if issues:
        lines.append("<h3>阻断问题</h3><ul>")
        for issue in issues:
            lines.append(
                '<li><span class="error">{}</span> {}</li>'.format(
                    _e(issue.get("code", "")), _e(issue.get("message", ""))
                )
            )
        lines.append("</ul>")
    lines.extend(_assignment_exclusions_html(assignment))
    lines.extend(_assignment_candidates_html(assignment))
    lines.extend(_assignment_plans_html(assignment))
    lines.extend(_assignment_blocking_html(assignment))
    return lines

def _assignment_exclusions_html(assignment: dict[str, Any]) -> list[str]:
    excluded_zones = [z for z in assignment.get("zones", []) if z.get("status") == "excluded"]
    excluded_points = [p for p in assignment.get("points", []) if p.get("status") == "excluded"]
    if not excluded_zones and not excluded_points:
        return []
    lines = ["<h3>未进入分配的要素（显式排除）</h3>",
             "<table><thead><tr><th>类别</th><th>要素</th><th>原因</th></tr></thead><tbody>"]
    for zone in excluded_zones:
        label = zone.get("zone_id") or zone.get("feature_id") or "—"
        lines.append(
            "<tr><td>责任区</td><td>{}</td><td class=\"mono\">{}</td></tr>".format(
                _e(label), _e(zone.get("exclusion_reason", ""))
            )
        )
    for point in excluded_points:
        lines.append(
            "<tr><td>点设施</td><td>{}</td><td class=\"mono\">{}</td></tr>".format(
                _e(point.get("point_id", "")), _e(point.get("exclusion_reason", ""))
            )
        )
    lines.append("</tbody></table>")
    return lines

def _assignment_candidates_html(assignment: dict[str, Any]) -> list[str]:
    candidates = assignment.get("candidates", [])
    review_pairs = assignment.get("review_pairs", [])
    if not candidates and not review_pairs:
        return []
    lines = ["<h3>空间候选归属</h3>"]
    def rows(items: list[dict[str, Any]]) -> list[str]:
        result = ["<table><thead><tr><th>点设施</th><th>责任区</th><th>关系</th>",
                  "<th>组件</th><th>锚点距离</th></tr></thead><tbody>"]
        for item in items:
            result.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td class=\"mono\">{}</td>"
                "<td>{}</td></tr>".format(
                    _e(item.get("point_id", "")),
                    _e(item.get("zone_id", "")),
                    _e(item.get("relation", "")),
                    _e(item.get("component", "")),
                    _e(item.get("distance", "")),
                )
            )
        result.append("</tbody></table>")
        return result
    if candidates:
        lines.extend(rows(candidates))
    if review_pairs:
        lines.append('<p class="muted">边界待核实（按配置不计入候选）：</p>')
        lines.extend(rows(review_pairs))
    return lines

def _assignment_plans_html(assignment: dict[str, Any]) -> list[str]:
    plans = assignment.get("plans", [])
    if not plans:
        return []
    lines = ["<h3>分配方案（两种运营取舍）</h3>",
             "<table><thead><tr><th>方案</th><th>已分配</th><th>未分配</th>",
             "<th>锚点总距离</th><th>最大区负载</th><th>负载极差</th><th>非支配</th>",
             "</tr></thead><tbody>"]
    for plan in plans:
        metrics = plan.get("metrics", {})
        lines.append(
            "<tr><td class=\"mono\">{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _e(plan.get("objective", "")),
                _e(metrics.get("assigned", 0)),
                _e(metrics.get("unassigned", 0)),
                _e(metrics.get("total_distance", 0)),
                _e(metrics.get("max_load", 0)),
                _e(metrics.get("load_spread", 0)),
                _e("是" if plan.get("non_dominated") else "否"),
            )
        )
    lines.append("</tbody></table>")
    for plan in plans:
        metrics = plan.get("metrics", {})
        loads = metrics.get("zone_loads", {})
        load_text = ", ".join(f"{key}:{loads[key]}" for key in sorted(loads)) or "—"
        lines.append(
            '<details><summary>方案 <span class="mono">{}</span>（区负载 {}，'
            "负载平方和 {}）</summary>".format(
                _e(plan.get("objective", "")),
                _e(load_text),
                _e(metrics.get("load_sum_squares", 0)),
            )
        )
        assignments = plan.get("assignments", [])
        if assignments:
            lines.append("<table><thead><tr><th>点设施</th><th>责任区</th><th>关系</th>"
                         "<th>组件</th><th>距离</th></tr></thead><tbody>")
            for item in assignments:
                lines.append(
                    "<tr><td>{}</td><td>{}</td><td>{}</td><td class=\"mono\">{}</td>"
                    "<td>{}</td></tr>".format(
                        _e(item.get("point_id", "")),
                        _e(item.get("zone_id", "")),
                        _e(item.get("relation", "")),
                        _e(item.get("component", "")),
                        _e(item.get("distance", "")),
                    )
                )
            lines.append("</tbody></table>")
        unassigned = plan.get("unassigned", [])
        if unassigned:
            lines.append("<table><thead><tr><th>未分配点</th><th>原因</th></tr></thead><tbody>")
            for item in unassigned:
                lines.append(
                    "<tr><td>{}</td><td class=\"mono\">{}</td></tr>".format(
                        _e(item.get("point_id", "")), _e(item.get("reason", ""))
                    )
                )
            lines.append("</tbody></table>")
        lines.append("</details>")
    return lines

def _assignment_blocking_html(assignment: dict[str, Any]) -> list[str]:
    blocking = assignment.get("blocking")
    if not blocking:
        return []
    lines = ["<h3>无法全量分配的阻断证据</h3>"]
    lines.append(
        '<div class="card"><p>受限点集合需求 <b>{}</b>，候选区总容量 <b>{}</b>，'
        '缺口 <b class="error">{}</b>。</p>'.format(
            _e(blocking.get("demand", 0)),
            _e(blocking.get("total_capacity", 0)),
            _e(blocking.get("deficit", 0)),
        )
    )
    deficient = ", ".join(blocking.get("deficient_points", [])) or "—"
    zones = ", ".join(
        "{}(容量 {})".format(zone.get("zone_id", ""), zone.get("capacity", 0))
        for zone in blocking.get("candidate_zones", [])
    ) or "—"
    lines.append(f"<p>受限点：{_e(deficient)}</p>")
    lines.append(f"<p>候选责任区：{_e(zones)}</p>")
    lines.append("</div>")
    return lines


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)
