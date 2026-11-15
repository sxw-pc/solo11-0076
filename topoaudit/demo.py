"""Bundled, fully synthetic offline demonstration."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .errors import ReportError
from .pipeline import execute
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = PROJECT_ROOT / "fixtures" / "demo"
DEMO_CONFIG = DEMO_DIR / "config.json"
EXPECTED_SUMMARY = DEMO_DIR / "expected_summary.json"

def run_demo(out_dir: str | Path) -> tuple[dict[str, Any], dict[str, Path], dict[str, Any]]:
    destination = Path(out_dir).resolve()
    fixtures = DEMO_DIR.resolve()
    if destination == fixtures or fixtures in destination.parents:
        raise ReportError(
            "TOPO-DEMO-001",
            "demo 输出目录不得位于 fixtures/demo 内，输入文件必须保持只读",
            str(destination),
        )
    if not DEMO_CONFIG.is_file():
        raise ReportError("TOPO-DEMO-002", "内置演示配置缺失", str(DEMO_CONFIG))
    report, paths = execute(DEMO_CONFIG, destination)
    expected = load_expected_summary()
    compare_with_expected(report, expected)
    return report, paths, expected

def load_expected_summary() -> dict[str, Any]:
    try:
        raw = json.loads(EXPECTED_SUMMARY.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReportError("TOPO-DEMO-003", f"无法读取演示预期摘要: {exc}", str(EXPECTED_SUMMARY)) from exc
    if not isinstance(raw, dict):
        raise ReportError("TOPO-DEMO-003", "演示预期摘要必须是 JSON 对象", str(EXPECTED_SUMMARY))
    return raw

def compare_with_expected(report: dict[str, Any], expected: dict[str, Any]) -> None:
    summary = report.get("summary", {})
    actual = {
        "total_findings": summary.get("total_findings"),
        "total_features": summary.get("total_features"),
        "severity_counts": summary.get("severity_counts"),
        "findings_by_code": summary.get("findings_by_code"),
        "findings_by_layer": summary.get("findings_by_layer"),
        "layer_feature_counts": {
            item.get("layer_id"): item.get("feature_count")
            for item in report.get("layers", [])
        },
    }
    expected_fields = expected.get("expect", {})
    for key in (
        "total_findings",
        "total_features",
        "severity_counts",
        "findings_by_code",
        "findings_by_layer",
        "layer_feature_counts",
    ):
        if key in expected_fields and actual[key] != expected_fields[key]:
            raise ReportError(
                "TOPO-DEMO-004",
                f"演示结果与 expected_summary.{key} 不一致",
                str(EXPECTED_SUMMARY),
            )
