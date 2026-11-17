"""Rule-engine entry point and layer-level checks."""
from __future__ import annotations
from typing import Any, Optional
from .findings import FindingCollector, sort_findings
from .model import AuditConfig, Finding, LoadedLayer
from .rule_geometry import check_geometry
from .rule_schema import check_schema
from .rule_topology import check_layer_topology

def run_rules(config: AuditConfig, layers: tuple[LoadedLayer, ...]) -> tuple[Finding, ...]:
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
    return tuple(sort_findings(collector.findings()))

def run_cross_layer_checks(
    config: AuditConfig, layers: tuple[LoadedLayer, ...]
) -> Optional[dict[str, Any]]:
    """Cross-layer prechecks that relate features across layers.

    Currently this is the capacitated point-facility to responsibility-zone
    assignment precheck; it runs only when the config carries an
    ``assignment`` block and never alters the per-layer findings above.
    """
    if config.assignment is None:
        return None
    from .assignment import run_assignment_precheck
    return run_assignment_precheck(config, layers)

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
