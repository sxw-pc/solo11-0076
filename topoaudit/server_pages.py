"""HTML rendering for the local read-only audit review server."""
from __future__ import annotations
import html
from typing import Any
from .util import canonical_json
_CSS = """
:root { color-scheme: light; --ink:#18232e; --muted:#5c6b78; --line:#d7dddf;
  --bg:#f5f8f8; --card:#fff; --accent:#1f5f6b; --error:#a93226; --warning:#9a6400; --info:#1f618d; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.55 "Segoe UI","Noto Sans SC",system-ui,sans-serif; }
main { max-width:1180px; margin:0 auto; padding:2rem; }
a { color:var(--accent); }
h1,h2 { line-height:1.2; }
h2 { margin-top:2rem; border-bottom:1px solid var(--line); padding-bottom:.3rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:1rem; }
.mono { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:.88em; word-break:break-all; }
.muted { color:var(--muted); }
table { width:100%; border-collapse:collapse; background:var(--card); margin:.75rem 0; }
th,td { border:1px solid var(--line); padding:.45rem .55rem; text-align:left; vertical-align:top; }
th { background:#eef3f3; }
.error { color:var(--error); font-weight:600; }
.warning { color:var(--warning); font-weight:600; }
.info { color:var(--info); font-weight:600; }
pre { white-space:pre-wrap; word-break:break-word; background:#f4f6f6; border:1px solid var(--line);
  border-radius:6px; padding:.55rem; margin:.35rem 0; overflow:auto; }
nav { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1rem; }
.pill { display:inline-block; border:1px solid var(--line); border-radius:999px; padding:.1rem .55rem; margin-right:.3rem; }
footer { margin-top:2rem; color:var(--muted); font-size:.9em; }
""".strip()

def render_index(runs: list[dict[str, Any]]) -> str:
    body = ["<h1>矢量拓扑验台 · 只读复核</h1>"]
    body.append('<p class="muted">仅展示本地 SQLite 台账中已有的运行。无上传、修改或任意文件读取接口。</p>')
    if not runs:
        body.append('<div class="card">台账中暂无运行记录。</div>')
    else:
        body.append("<table><thead><tr><th>run_label</th><th>result_digest</th>"
                    "<th>图层</th><th>findings</th></tr></thead><tbody>")
        for run in runs:
            digest = _e(run.get("result_digest", ""))
            body.append(
                '<tr><td>{}</td><td><a class="mono" href="/runs/{}">{}</a></td>'
                "<td>{}</td><td>{}</td></tr>".format(
                    _e(run.get("run_label", "")),
                    digest,
                    digest,
                    _e(run.get("layer_count", 0)),
                    _e(run.get("finding_count", 0)),
                )
            )
        body.append("</tbody></table>")
    return _page("运行列表", "\n".join(body))

def render_run(
    run: dict[str, Any],
    findings: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
    filters: dict[str, str],
) -> str:
    body = [f"<h1>{_e(run.get('run_label', ''))}</h1>"]
    body.append('<nav><a href="/">← 运行列表</a><a href="/rules">规则目录</a></nav>')
    summary = run.get("summary", {})
    severity = summary.get("severity_counts", {})
    body.append('<div class="card">')
    body.append(f'<div class="mono">result_digest: {_e(run.get("result_digest", ""))}</div>')
    body.append(f'<div class="mono">input_digest: {_e(run.get("input_digest", ""))}</div>')
    body.append(f'<div class="mono">config_digest: {_e(run.get("config_digest", ""))}</div>')
    body.append(
        '<p>图层 {} · 要素 {} · findings {}（<span class="error">error {}</span> / '
        '<span class="warning">warning {}</span> / <span class="info">info {}</span>）</p>'.format(
            _e(summary.get("total_layers", 0)),
            _e(summary.get("total_features", 0)),
            _e(summary.get("total_findings", 0)),
            _e(severity.get("error", 0)),
            _e(severity.get("warning", 0)),
            _e(severity.get("info", 0)),
        )
    )
    body.append("</div>")
    body.extend(_layers(run.get("layers", [])))
    body.extend(_findings(findings, filters))
    body.extend(_catalog(catalog))
    body.append(footer_text())
    return _page("运行复核", "\n".join(body))

def render_rules(catalog: list[dict[str, Any]]) -> str:
    body = ['<h1>规则目录</h1><nav><a href="/">运行列表</a></nav>']
    body.extend(_catalog(catalog))
    body.append(footer_text())
    return _page("规则目录", "\n".join(body))

def render_error(status: int, message: str) -> str:
    body = [
        f"<h1>请求失败（{status}）</h1>",
        f'<div class="card">{_e(message)}</div>',
        '<p><a href="/">返回运行列表</a></p>',
    ]
    return _page("请求失败", "\n".join(body))

def _layers(layers: list[dict[str, Any]]) -> list[str]:
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
                _e(", ".join(str(v) for v in bbox) if bbox else "—"),
            )
        )
    lines.append("</tbody></table>")
    return lines

def _findings(findings: list[dict[str, Any]], filters: dict[str, str]) -> list[str]:
    lines = ["<h2>Findings</h2>"]
    links: list[str] = []
    for severity in ("error", "warning", "info"):
        href = f"?severity={severity}"
        links.append(f'<a class="pill" href="{href}">{severity}</a>')
    if filters:
        links.append('<a class="pill" href="?">清除过滤</a>')
    lines.append("<p>" + " ".join(links) + "</p>")
    if filters:
        lines.append(f'<p class="muted">当前过滤: <span class="mono">{_e(canonical_json(filters))}</span></p>')
    if not findings:
        lines.append('<div class="card">当前过滤条件下没有 finding。</div>')
        return lines
    lines.append("<table><thead><tr><th>Finding</th><th>规则</th><th>图层 / 要素</th>"
                 "<th>关联</th><th>证据</th></tr></thead><tbody>")
    for finding in findings:
        severity = str(finding.get("severity", ""))
        lines.append(
            '<tr><td><span class="mono">{}</span><br><span class="{}">{}</span></td>'
            '<td class="mono">{}</td><td>{} / {}</td><td>{}</td>'
            "<td><pre>{}</pre></td></tr>".format(
                _e(finding.get("finding_id", "")),
                _e(severity),
                _e(severity),
                _e(finding.get("rule_id", "")),
                _e(finding.get("layer_id", "")),
                _e(finding.get("feature_id") or "—"),
                _e(", ".join(finding.get("related_feature_ids", [])) or "—"),
                _e(canonical_json(finding.get("evidence", {}))),
            )
        )
    lines.append("</tbody></table>")
    return lines

def _catalog(catalog: list[dict[str, Any]]) -> list[str]:
    lines = ["<h2>规则说明</h2>", "<table><thead><tr><th>规则 ID</th><th>严重级别</th>",
             "<th>名称</th><th>触发说明</th></tr></thead><tbody>"]
    for rule in catalog:
        severity = str(rule.get("severity", ""))
        lines.append(
            '<tr><td class="mono">{}</td><td class="{}">{}</td><td>{}</td><td>{}</td></tr>'.format(
                _e(rule.get("rule_id", "")),
                _e(severity),
                _e(severity),
                _e(rule.get("title", "")),
                _e(rule.get("description", "")),
            )
        )
    lines.append("</tbody></table>")
    return lines

def footer_text() -> str:
    return (
        "<footer>页面完全自包含，无外部 CDN、字体、图片或脚本。"
        "数据来源仅为本地 SQLite 台账。</footer>"
    )

def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title)}</title>\n<style>{_CSS}</style>\n</head>\n"
        f"<body><main>{body}</main></body>\n</html>\n"
    )

def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)
