"""Rule-engine entry point and layer-level checks."""
from __future__ import annotations
from .findings import FindingCollector, sort_findings
from .model import (
    COVERAGE_STATUSES,
    AuditConfig,
    CoverageRuleConfig,
    Finding,
    LoadedLayer,
)
from .rule_geometry import check_geometry
from .rule_schema import check_schema
from .rule_topology import check_layer_topology, check_line_coverage

def run_rules(config: AuditConfig, layers: tuple[LoadedLayer, ...]) -> tuple[Finding, ...]:
    findings, _ = run_rules_with_coverage(config, layers)
    return findings

def run_rules_with_coverage(
    config: AuditConfig, layers: tuple[LoadedLayer, ...]
) -> tuple[tuple[Finding, ...], tuple[dict[str, object], ...]]:
    """Run all rules and return findings plus per-coverage-rule sections.

    Coverage sections carry the per-line status, lengths and raw slice
    coordinates that the JSON report exposes as audit evidence.
    """
    collector = FindingCollector()
    for layer in layers:
        tolerance = layer.config.effective_tolerance(config.tolerance)
        _check_layer_crs(layer, config, collector)
        if not layer.features:
            if layer.config.rule_enabled("EMPTY_LAYER"):
                collector.add(
                    "EMPTY_LAYER",
                    layer.config.layer_id,
                    evidence={"feature_count": 0},
                )
        for feature in layer.features:
            check_schema(feature, layer.config, collector)
            check_geometry(feature, layer.config, config, tolerance, collector)
        check_layer_topology(layer.features, layer.config, tolerance, collector)
    sections = _run_coverage_rules(config, layers, collector)
    return tuple(sort_findings(collector.findings())), sections

def _run_coverage_rules(
    config: AuditConfig,
    layers: tuple[LoadedLayer, ...],
    collector: FindingCollector,
) -> tuple[dict[str, object], ...]:
    by_id = {layer.config.layer_id: layer for layer in layers}
    sections: list[dict[str, object]] = []
    for rule in config.coverage_rules:
        line_layer = by_id.get(rule.line_layer_id)
        polygon_layer = by_id.get(rule.polygon_layer_id)
        if line_layer is None or polygon_layer is None:
            # Config parsing guarantees both layers exist; this is a
            # defensive skip for programmatically constructed configs.
            continue
        enabled = rule.enabled and line_layer.config.rule_enabled("LINE_COVERAGE")
        if not enabled:
            sections.append(_disabled_section(rule))
            continue
        section = check_line_coverage(
            rule, line_layer, polygon_layer, config.crs, collector
        )
        section["enabled"] = True
        sections.append(section)
    return tuple(sections)

def _disabled_section(rule: CoverageRuleConfig) -> dict[str, object]:
    return {
        "rule_id": rule.rule_id,
        "line_layer": rule.line_layer_id,
        "polygon_layer": rule.polygon_layer_id,
        "boundary_mode": rule.boundary_mode,
        "tolerance": rule.tolerance,
        "enabled": False,
        "status_counts": {status: 0 for status in COVERAGE_STATUSES},
        "invalid_polygon_sources": [],
        "line_layer_crs_reason": None,
        "polygon_layer_crs_reason": None,
        "lines": [],
    }

def _check_layer_crs(
    layer: LoadedLayer, config: AuditConfig, collector: FindingCollector
) -> None:
    declared = layer.config.declared_crs
    if declared is None or not layer.config.rule_enabled("LAYER_CRS_MISMATCH"):
        return
    expected = {"name": config.crs.name, "type": config.crs.kind}
    actual = {"name": declared.name, "type": declared.kind}
    if expected != actual:
        collector.add(
            "LAYER_CRS_MISMATCH",
            layer.config.layer_id,
            evidence={"expected_crs": expected, "declared_crs": actual},
        )
