"""Loopback-only, read-only HTTP review service for the audit ledger."""
from __future__ import annotations
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from .errors import LedgerError, ServerError
from .ledger_query import get_findings, get_rule_catalog, get_run, list_runs
from .server_pages import render_error, render_index, render_rules, render_run
from .util import pretty_json
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
_RUN_RE = re.compile(r"^/runs/([0-9a-f]{64})$")
_FINDINGS_RE = re.compile(r"^/runs/([0-9a-f]{64})/findings\.json$")
_ALLOWED_FILTERS = {"severity", "layer", "rule"}
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

class AuditHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address: tuple[str, int], ledger_path: Path) -> None:
        self.ledger_path = ledger_path
        super().__init__(address, AuditRequestHandler)

class AuditRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "topoaudit/1.0"
    sys_version = ""
    @property
    def ledger_path(self) -> Path:
        return self.server.ledger_path  # type: ignore[attr-defined]
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(head=False)
    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(head=True)
    def do_POST(self) -> None:  # noqa: N802
        self._method_not_allowed()
    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()
    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()
    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return
    def _dispatch(self, head: bool) -> None:
        try:
            split = urlsplit(self.path)
            path = unquote(split.path)
            filters = self._parse_filters(split.query)
            if path == "/healthz":
                self._send_json(200, {"status": "ok", "read_only": True}, head)
                return
            if path == "/":
                self._send_html(200, render_index(list_runs(self.ledger_path)), head)
                return
            if path == "/rules":
                self._send_html(200, render_rules(get_rule_catalog(self.ledger_path)), head)
                return
            run_match = _RUN_RE.fullmatch(path)
            if run_match:
                self._serve_run(run_match.group(1), filters, head)
                return
            findings_match = _FINDINGS_RE.fullmatch(path)
            if findings_match:
                self._serve_findings(findings_match.group(1), filters, head)
                return
            self._send_html(404, render_error(404, "未找到请求的资源。"), head)
        except _BadRequest as exc:
            self._send_html(400, render_error(400, str(exc)), head)
        except (LedgerError, sqlite3.Error, OSError):
            self._send_html(500, render_error(500, "台账不可用或读取失败。"), head)
        except Exception:  # pragma: no cover - defensive boundary
            self._send_html(500, render_error(500, "服务处理请求时发生内部错误。"), head)
    def _parse_filters(self, query: str) -> dict[str, str]:
        values = parse_qs(query, keep_blank_values=True)
        unknown = sorted(set(values) - _ALLOWED_FILTERS)
        if unknown:
            raise _BadRequest(f"不支持的查询参数: {', '.join(unknown)}")
        filters: dict[str, str] = {}
        for key in sorted(values):
            items = values[key]
            if len(items) != 1:
                raise _BadRequest(f"查询参数 {key} 只能出现一次")
            filters[key] = items[0]
        severity = filters.get("severity")
        if severity is not None and severity not in ("error", "warning", "info"):
            raise _BadRequest("severity 只能是 error、warning 或 info")
        return filters
    def _serve_run(self, digest: str, filters: dict[str, str], head: bool) -> None:
        run = get_run(self.ledger_path, digest)
        if run is None:
            self._send_html(404, render_error(404, "台账中不存在该运行。"), head)
            return
        findings = get_findings(
            self.ledger_path,
            digest,
            severity=filters.get("severity"),
            layer_id=filters.get("layer"),
            rule_id=filters.get("rule"),
        )
        page = render_run(run, findings, get_rule_catalog(self.ledger_path), filters)
        self._send_html(200, page, head)
    def _serve_findings(self, digest: str, filters: dict[str, str], head: bool) -> None:
        if get_run(self.ledger_path, digest) is None:
            self._send_json(404, {"error": "run_not_found", "result_digest": digest}, head)
            return
        findings = get_findings(
            self.ledger_path,
            digest,
            severity=filters.get("severity"),
            layer_id=filters.get("layer"),
            rule_id=filters.get("rule"),
        )
        self._send_json(
            200,
            {
                "result_digest": digest,
                "filters": filters,
                "count": len(findings),
                "findings": findings,
            },
            head,
        )
    def _send_html(self, status: int, page: str, head: bool) -> None:
        self._send(status, page.encode("utf-8"), "text/html; charset=utf-8", head)
    def _send_json(self, status: int, payload: Any, head: bool) -> None:
        body = pretty_json(payload).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", head)
    def _send(self, status: int, body: bytes, content_type: str, head: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)
    def _method_not_allowed(self) -> None:
        body = json.dumps(
            {"error": "method_not_allowed", "allow": ["GET", "HEAD"]},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(405)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Allow", "GET, HEAD")
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

class _BadRequest(Exception):
    pass

def create_server(
    ledger_path: str | Path, host: str = "127.0.0.1", port: int = 8765
) -> AuditHTTPServer:
    normalized = host.strip().lower()
    if normalized not in ALLOWED_HOSTS:
        raise ServerError(
            "TOPO-SRV-001",
            "复核服务只允许绑定 127.0.0.1 或 localhost",
            host,
        )
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ServerError("TOPO-SRV-002", "端口必须是 0-65535 的整数", str(port))
    ledger = Path(ledger_path)
    if not ledger.is_file():
        raise ServerError("TOPO-SRV-003", "台账文件不存在", str(ledger))
    try:
        list_runs(ledger)
    except (LedgerError, sqlite3.Error) as exc:
        raise ServerError("TOPO-SRV-004", f"台账不可用: {exc}", str(ledger)) from exc
    try:
        return AuditHTTPServer((normalized, port), ledger)
    except OSError as exc:
        raise ServerError("TOPO-SRV-005", f"无法启动本地服务: {exc}", f"{host}:{port}") from exc

def serve_ledger(ledger_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = create_server(ledger_path, host=host, port=port)
    print(f"topoaudit 只读复核服务: http://{host}:{server.server_address[1]}/")
    print("仅监听本机回环地址；按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
