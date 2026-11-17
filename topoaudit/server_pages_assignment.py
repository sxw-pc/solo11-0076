"""Assignment-precheck section rendering for the read-only review server."""
from __future__ import annotations
import html
from typing import Any

def render_assignment_page(assignment: dict[str, Any]) -> list[str]:
    lines = ["<h2>责任区分配预检</h2>"]
    config = assignment.get("config", {})
    overview = assignment.get("input_summary", {})
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
        '<p class="muted">可分配点 {} / 排除 {} · 有效区 {} / 排除 {} · 候选对 {} · 待核实 {}</p>'.format(
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
        lines.append("<ul>")
        for issue in issues:
            lines.append(
                '<li><span class="error">{}</span> {}</li>'.format(
                    _e(issue.get("code", "")), _e(issue.get("message", ""))
                )
            )
        lines.append("</ul>")
    candidates = assignment.get("candidates", [])
    if candidates:
        lines.append("<h3>空间候选归属</h3>")
        lines.append("<table><thead><tr><th>点设施</th><th>责任区</th><th>关系</th>"
                     "<th>组件</th><th>锚点距离</th></tr></thead><tbody>")
        for item in candidates:
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
    review_pairs = assignment.get("review_pairs", [])
    if review_pairs:
        lines.append('<p class="muted">边界待核实（按配置不计入候选）：{}</p>'.format(
            _e(", ".join(
                "{}→{}".format(item.get("point_id", ""), item.get("zone_id", ""))
                for item in review_pairs
            ))
        ))
    plans = assignment.get("plans", [])
    if plans:
        lines.append("<h3>分配方案（两种运营取舍）</h3>")
        lines.append("<table><thead><tr><th>方案</th><th>已分配</th><th>未分配</th>"
                     "<th>锚点总距离</th><th>最大区负载</th><th>负载极差</th><th>非支配</th>"
                     "</tr></thead><tbody>")
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
                '<details><summary>方案 <span class="mono">{}</span>（区负载 {}）</summary>'.format(
                    _e(plan.get("objective", "")), _e(load_text)
                )
            )
            assignments = plan.get("assignments", [])
            if assignments:
                lines.append("<table><thead><tr><th>点设施</th><th>责任区</th><th>距离</th>"
                             "</tr></thead><tbody>")
                for item in assignments:
                    lines.append(
                        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                            _e(item.get("point_id", "")),
                            _e(item.get("zone_id", "")),
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
    blocking = assignment.get("blocking")
    if blocking:
        lines.append("<h3>无法全量分配的阻断证据</h3>")
        deficient = ", ".join(blocking.get("deficient_points", [])) or "—"
        zones = ", ".join(
            "{}(容量 {})".format(zone.get("zone_id", ""), zone.get("capacity", 0))
            for zone in blocking.get("candidate_zones", [])
        ) or "—"
        lines.append(
            '<div class="card"><p>受限点集合需求 <b>{}</b>，候选区总容量 <b>{}</b>，'
            '缺口 <b class="error">{}</b>。</p>'
            "<p>受限点：{}</p><p>候选责任区：{}</p></div>".format(
                _e(blocking.get("demand", 0)),
                _e(blocking.get("total_capacity", 0)),
                _e(blocking.get("deficit", 0)),
                _e(deficient),
                _e(zones),
            )
        )
    return lines


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)
