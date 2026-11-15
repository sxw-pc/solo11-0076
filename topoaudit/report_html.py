"""Self-contained, script-free HTML report renderer."""
from __future__ import annotations
import html
from pathlib import Path
from typing import Any
from .util import canonical_json
_CSS = """
:root { color-scheme: light; --ink:#17202a; --muted:#5d6d7e; --line:#d5dbdb;
  --bg:#f7f9f9; --card:#ffffff; --error:#a93226; --warning:#9a6400; --info:#1f618d; }
* { box-sizing: border-box; }
body { margin:0; padding:2rem; background:var(--bg); color:var(--ink);
  font: 15px/1.55 "Segoe UI", "Noto Sans SC", system-ui, sans-serif; }
main { max-width: 1180px; margin:0 auto; }
h1,h2 { line-height:1.2; }
h2 { margin-top:2rem; border-bottom:1px solid var(--line); padding-bottom:.35rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:1rem; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:.75rem; }
.metric { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:.75rem 1rem; }
.metric b { display:block; font-size:1.7rem; }
.muted { color:var(--muted); }
.mono { font-family:ui-monospace, SFMono-Regular, Consolas, monospace; font-size:.88em; }
table { width:100%; border-collapse:collapse; background:var(--card); margin:.75rem 0; }
th,td { border:1px solid var(--line); padding:.45rem .55rem; text-align:left; vertical-align:top; }
th { background:#eef2f2; }
.error { color:var(--error); font-weight:600; }
.warning { color:var(--warning); font-weight:600; }
.info { color:var(--info); font-weight:600; }
details { margin:.35rem 0; }
summary { cursor:pointer; }
pre { overflow:auto; white-space:pre-wrap; word-break:break-word; background:#f4f6f6;
  border:1px solid var(--line); border-radius:6px; padding:.6rem; margin:.4rem 0; }
code { word-break:break-all; }
footer { margin-top:2.5rem; color:var(--muted); font-size:.9em; }
""".strip()

def render_html(report: dict[str, Any]) -> str:
    label = report.get("run_label", "")
    summary = report.get("summary", {})
    severity = summary.get("severity_counts", {})
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; base-uri \'none\'; form-action \'none\'">',
        f"<title>矢量拓扑验台审计报告 - {_e(label)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body><main>",
        f"<h1>矢量拓扑验台审计报告</h1><p class=\"muted\">{_e(label)}</p>",
        '<div class="grid">',
        _metric("图层", summary.get("total_layers", 0)),
        _metric("要素", summary.get("total_features", 0)),
        _metric("findings", summary.get("total_findings", 0)),
        _metric("error", severity.get("error", 0), "error"),
        _metric("warning", severity.get("warning", 0), "warning"),
        _metric("info", severity.get("info", 0), "info"),
        "</div>",
        '<div class="card" style="margin-top:1rem">',
        '<div class="mono">input_digest: ' + _e(report.get("input_digest", "")) + "</div>",
        '<div class="mono">config_digest: ' + _e(report.get("config_digest", "")) + "</div>",
        '<div class="mono">result_digest: ' + _e(report.get("result_digest", "")) + "</div>",
        "</div>",
    ]
    parts.extend(_layers_html(report.get("layers", [])))
    parts.extend(_rules_html(report.get("rules", [])))
    parts.extend(_findings_html(report.get("findings", [])))
    parts.extend(_inputs_html(report.get("input_files", [])))
    parts.append("<h2>限制说明</h2><ul>")
    for item in report.get("limitations", []):
        parts.append(f"<li>{_e(item)}</li>")
    parts.append("</ul>")
    parts.append(
        "<footer>本页面完全自包含，不加载外部字体、图片、脚本或样式；"
        "相同输入和配置应产生相同的核心内容与 result_digest。</footer>"
    )
    parts.append("</main></body></html>")
    return "\n".join(parts) + "\n"

def _metric(label: str, value: Any, css_class: str = "") -> str:
    return (
        f'<div class="metric"><span class="muted">{_e(label)}</span>'
        f'<b class="{css_class}">{_e(value)}</b></div>'
    )

def _layers_html(layers: list[dict[str, Any]]) -> list[str]:
    lines = ["<h2>图层统计</h2>", "<table><thead><tr><th>图层</th><th>要素</th>",
             "<th>几何类型</th><th>findings</th><th>bbox</th></tr></thead><tbody>"]
    for layer in layers:
        kinds = ", ".join(
            f"{key}:{value}" for key, value in layer.get("geometry_type_counts", {}).items()
        ) or "—"
        bbox = layer.get("bbox")
        lines.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td class=\"mono\">{}</td></tr>".format(
                _e(layer.get("layer_id", "")),
                _e(layer.get("feature_count", 0)),
                _e(kinds),
                _e(layer.get("finding_count", 0)),
                _e(", ".join(str(value) for value in bbox) if bbox else "—"),
            )
        )
    lines.append("</tbody></table>")
    return lines

def _rules_html(rules: list[dict[str, Any]]) -> list[str]:
    lines = ["<h2>规则统计</h2>", "<table><thead><tr><th>规则 ID</th>",
             "<th>严重级别</th><th>名称</th><th>数量</th></tr></thead><tbody>"]
    for rule in rules:
        severity = str(rule.get("severity", ""))
        lines.append(
            '<tr><td class="mono">{}</td><td class="{}">{}</td><td>{}</td><td>{}</td></tr>'.format(
                _e(rule.get("rule_id", "")),
                _e(severity),
                _e(severity),
                _e(rule.get("title", "")),
                _e(rule.get("count", 0)),
            )
        )
    lines.append("</tbody></table>")
    return lines

def _findings_html(findings: list[dict[str, Any]]) -> list[str]:
    lines = [f"<h2>Findings（{len(findings)}）</h2>"]
    if not findings:
        return lines + ["<p>未发现任何问题。</p>"]
    lines.append("<table><thead><tr><th>Finding</th><th>规则</th><th>图层/要素</th><th>证据</th></tr></thead><tbody>")
    for finding in findings:
        severity = str(finding.get("severity", ""))
        related = ", ".join(finding.get("related_feature_ids", [])) or "—"
        evidence = canonical_json(finding.get("evidence", {}))
        lines.append(
            '<tr><td><code>{}</code><br><span class="{}">{}</span></td>'
            "<td class=\"mono\">{}</td><td>{}<br><span class=\"muted\">关联: {}</span></td>"
            "<td><details><summary>展开证据</summary><pre>{}</pre></details></td></tr>".format(
                _e(finding.get("finding_id", "")),
                _e(severity),
                _e(severity),
                _e(finding.get("rule_id", "")),
                _e(
                    "{} / {}".format(
                        finding.get("layer_id", ""), finding.get("feature_id") or "—"
                    )
                ),
                _e(related),
                _e(evidence),
            )
        )
    lines.append("</tbody></table>")
    return lines

def _inputs_html(inputs: list[dict[str, Any]]) -> list[str]:
    lines = ["<h2>输入文件摘要</h2>", "<table><thead><tr><th>图层</th><th>路径（配置）</th>",
             "<th>格式</th><th>字节</th><th>SHA-256</th></tr></thead><tbody>"]
    for item in inputs:
        lines.append(
            '<tr><td>{}</td><td class="mono">{}</td><td>{}</td><td>{}</td><td class="mono">{}</td></tr>'.format(
                _e(item.get("layer_id", "")),
                _e(item.get("path", "")),
                _e(item.get("format", "")),
                _e(item.get("size_bytes", 0)),
                _e(item.get("sha256", "")),
            )
        )
    lines.append("</tbody></table>")
    return lines

def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)

def write_report_html(path: str | Path, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_html(report), encoding="utf-8")
