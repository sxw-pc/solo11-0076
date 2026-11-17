"""Command line interface for the offline topology audit workbench."""
from __future__ import annotations
import argparse
import json
import sys
from typing import Optional, Sequence
from . import __version__
from .demo import run_demo
from .errors import AuditError, LedgerError
from .ledger_query import get_findings, get_run, list_runs
from .pipeline import execute, render_terminal_summary
from .server import create_server
from .util import pretty_json

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m topoaudit",
        description="本地离线的矢量几何与拓扑质量审计引擎",
    )
    parser.add_argument("--version", action="version", version=f"topoaudit {__version__}")
    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="执行完整审计")
    run_parser.add_argument("--config", required=True, help="JSON 审计配置路径")
    run_parser.add_argument("--out", required=True, help="输出目录")
    demo_parser = sub.add_parser("demo", help="运行内置离线演示")
    demo_parser.add_argument("--out", required=True, help="演示输出目录")
    verify_parser = sub.add_parser("verify", help="核对报告与 SQLite 台账")
    verify_parser.add_argument("--report", required=True, help="report.json 路径")
    verify_parser.add_argument("--ledger", required=True, help="audit.sqlite 路径")
    serve_parser = sub.add_parser("serve", help="启动只读本地复核服务")
    serve_parser.add_argument("--ledger", required=True, help="audit.sqlite 路径")
    serve_parser.add_argument("--host", default="127.0.0.1", help="仅允许 127.0.0.1 或 localhost")
    serve_parser.add_argument("--port", type=int, default=8765, help="默认 8765")
    query_parser = sub.add_parser("query", help="查询台账运行或 findings")
    query_parser.add_argument("--ledger", required=True, help="audit.sqlite 路径")
    query_parser.add_argument("--digest", help="结果摘要；省略时列出全部运行")
    query_parser.add_argument("--severity", choices=("error", "warning", "info"))
    query_parser.add_argument("--layer")
    query_parser.add_argument("--rule")
    query_parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        if args.command == "run":
            return _run(args)
        if args.command == "demo":
            return _demo(args)
        if args.command == "verify":
            return _verify(args)
        if args.command == "serve":
            return _serve(args)
        if args.command == "query":
            return _query(args)
    except AuditError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[TOPO-IO-001] {exc}", file=sys.stderr)
        return 2
    parser.print_help()
    return 2
def _run(args: argparse.Namespace) -> int:
    report, paths = execute(args.config, args.out)
    print(render_terminal_summary(report, paths))
    return 0
def _demo(args: argparse.Namespace) -> int:
    report, paths, expected = run_demo(args.out)
    print(render_terminal_summary(report, paths))
    expected_label = expected.get("label", "expected_summary")
    print(f"fixture 预期计数核对通过: {expected_label}")
    return 0
def _verify(args: argparse.Namespace) -> int:
    from .ledger_verify import verify_report_and_ledger
    result = verify_report_and_ledger(args.report, args.ledger)
    if result.ok:
        print(
            "校验通过: "
            f"result_digest={result.report_digest} "
            f"layers={result.layer_count} findings={result.finding_count}"
        )
        return 0
    print("校验失败:", file=sys.stderr)
    for issue in result.issues:
        print(f"  - {issue}", file=sys.stderr)
    return 1
def _serve(args: argparse.Namespace) -> int:
    if not 1 <= args.port <= 65535:
        raise LedgerError("TOPO-SRV-002", "端口必须是 1-65535 的整数", str(args.port))
    server = create_server(args.ledger, host=args.host, port=args.port)
    host = args.host.strip().lower()
    print(f"topoaudit 只读复核服务: http://{host}:{server.server_address[1]}/")
    print("仅监听本机回环地址；按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
def _query(args: argparse.Namespace) -> int:
    runs = list_runs(args.ledger)
    if not args.digest:
        if args.json:
            print(pretty_json(runs), end="")
            return 0
        if not runs:
            print("台账中暂无运行记录。")
            return 0
        print(f"{'result_digest':64s}  {'layers':>6s}  {'findings':>8s}  run_label")
        for run in runs:
            print(
                f"{run['result_digest']:64s}  {run['layer_count']:>6d}  "
                f"{run['finding_count']:>8d}  {run['run_label']}"
            )
        return 0
    run = get_run(args.ledger, args.digest)
    if run is None:
        raise LedgerError("TOPO-LED-006", "台账中不存在该 result_digest", args.digest)
    findings = get_findings(
        args.ledger,
        args.digest,
        severity=args.severity,
        layer_id=args.layer,
        rule_id=args.rule,
    )
    if args.json:
        payload = {
            "run": {
                "result_digest": run["result_digest"],
                "run_label": run["run_label"],
                "summary": run["summary"],
            },
            "findings": findings,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(
        f"run_label={run['run_label']} digest={run['result_digest']} "
        f"findings={len(findings)}"
    )
    for finding in findings:
        print(
            f"{finding['severity']:7s} {finding['rule_id']:28s} "
            f"{finding['layer_id']:12s} {finding['feature_id'] or '-':16s} "
            f"{finding['finding_id']}"
        )
    return 0
if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
