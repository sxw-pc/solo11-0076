"""Rule-engine entry point and layer-level checks."""
from __future__ import annotations
from .findings import FindingCollector, sort_findings
from .model import AuditConfig, Finding, LoadedLayer
from .rule_geometry import check_geometry
from .rule_schema import check_schema
from .rule_topology import audit_line_network, check_layer_topology

def run_rules(config: AuditConfig, layers: tuple[LoadedLayer, ...]) -> tuple[Finding, ...]:
    findings, _ = run_rules_with_network(config, layers)
    return tuple(sort_findings(findings))

def run_rules_with_network(
    config: AuditConfig, layers: tuple[LoadedLayer, ...]
) -> tuple[tuple[Finding, ...], tuple[dict, ...]]:
    """Run every enabled rule plus the config-driven line-network audits."""
    collector = FindingCollector()
    network_reports: list[dict] = []
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
        report = audit_line_network(
            layer.features, layer.config, tolerance, collector
        )
        if report is not None:
            network_reports.append(report)
    return tuple(sort_findings(collector.findings())), tuple(network_reports)

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
