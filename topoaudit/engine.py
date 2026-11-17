"""End-to-end audit execution independent of report and storage formats."""
from __future__ import annotations
from typing import Optional
from .findings import layer_counts, rule_counts, severity_counts
from .loaders import compute_input_digest, load_layers
from .model import (
    AuditConfig,
    AuditResult,
    Finding,
    LayerStats,
    LoadedLayer,
    geometry_type_counts,
)
from .normalize import finite_bbox
from .rules import run_cross_layer_checks, run_rules
from .util import stable_round
LIMITATIONS = (
    "仅处理二维欧氏几何；不进行坐标参考系转换或投影计算。",
    "压盖检测返回确定性布尔判定，不计算多边形重叠面积。",
    "近接与重复坐标判定依赖配置容差；容差变化会改变结果摘要。",
    "工具只读输入文件，不自动修复、合并或写回源数据。",
    "仅支持本地 GeoJSON 与 CSV，不连接远程服务或数据库。",
    "环、线和压盖算法面向小团队离线卡点，不面向超大规模数据集。",
    "责任区分配预检的锚点距离为坐标单位欧氏距离，不代表路网或出勤时间。",
)

def run_audit(config: AuditConfig) -> AuditResult:
    layers = load_layers(config)
    findings = run_rules(config, layers)
    assignment = run_cross_layer_checks(config, layers)
    input_files = tuple(layer.input_file for layer in sorted(layers, key=lambda item: item.config.layer_id))
    input_digest = compute_input_digest(config.config_digest, input_files)
    stats = _build_layer_stats(layers, findings)
    summary = _build_summary(layers, findings, input_files)
    if assignment is not None:
        summary["assignment"] = _assignment_summary(assignment)
    return AuditResult(
        config=config,
        layers=layers,
        findings=findings,
        layer_stats=tuple(stats),
        input_files=input_files,
        input_digest=input_digest,
        summary=summary,
        limitations=LIMITATIONS,
        assignment=assignment,
    )

def _assignment_summary(assignment: dict) -> dict:
    overview = assignment.get("input_summary", {})
    blocking = assignment.get("blocking")
    return {
        "status": assignment.get("status"),
        "eligible_points": overview.get("eligible_points", 0),
        "valid_zones": overview.get("valid_zones", 0),
        "candidate_pairs": overview.get("candidate_pairs", 0),
        "plans": [
            {
                "objective": plan.get("objective"),
                "assigned": plan.get("metrics", {}).get("assigned", 0),
                "unassigned": plan.get("metrics", {}).get("unassigned", 0),
                "total_distance": plan.get("metrics", {}).get("total_distance", 0.0),
                "max_load": plan.get("metrics", {}).get("max_load", 0),
                "non_dominated": plan.get("non_dominated", False),
            }
            for plan in assignment.get("plans", [])
        ],
        "blocking_deficit": blocking.get("deficit", 0) if blocking else 0,
    }
def _build_layer_stats(
    layers: tuple[LoadedLayer, ...], findings: tuple[Finding, ...]
) -> list[LayerStats]:
    by_layer: dict[str, list[Finding]] = {}
    for finding in findings:
        by_layer.setdefault(finding.layer_id, []).append(finding)
    result: list[LayerStats] = []
    for layer in layers:
        layer_findings = by_layer.get(layer.config.layer_id, [])
        severities = severity_counts(layer_findings)
        result.append(
            LayerStats(
                layer_id=layer.config.layer_id,
                feature_count=len(layer.features),
                geometry_type_counts=geometry_type_counts(layer.features),
                finding_count=len(layer_findings),
                severity_counts=severities,
                bbox=_layer_bbox(layer),
            )
        )
    return result
def _layer_bbox(layer: LoadedLayer) -> Optional[list[float]]:
    merged: Optional[tuple[float, float, float, float]] = None
    for feature in layer.features:
        box = finite_bbox(feature.geometry.coordinates)
        if box is None:
            continue
        if merged is None:
            merged = box
        else:
            merged = (
                min(merged[0], box[0]),
                min(merged[1], box[1]),
                max(merged[2], box[2]),
                max(merged[3], box[3]),
            )
    if merged is None:
        return None
    return [stable_round(value) for value in merged]
def _build_summary(
    layers: tuple[LoadedLayer, ...],
    findings: tuple[Finding, ...],
    input_files: tuple,
) -> dict[str, object]:
    codes = rule_counts(findings)
    per_layer = layer_counts(findings)
    return {
        "total_layers": len(layers),
        "total_features": sum(len(layer.features) for layer in layers),
        "total_findings": len(findings),
        "input_file_count": len(input_files),
        "input_bytes": sum(item.size_bytes for item in input_files),
        "severity_counts": severity_counts(findings),
        "findings_by_code": {code: codes[code] for code in sorted(codes)},
        "findings_by_layer": {
            layer.config.layer_id: per_layer.get(layer.config.layer_id, 0)
            for layer in layers
        },
    }
