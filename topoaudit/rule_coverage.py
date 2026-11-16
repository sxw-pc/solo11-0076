"""Finding projection for the explicitly opted-in coverage audit.

The coverage computation lives in :mod:`topoaudit.coverage`; this module only
turns its deterministic document into ordinary findings so the JSON/Markdown/
HTML reports and the SQLite ledger all carry the same mode, gap components, and
remediation area without special-casing the report writers.
"""
from __future__ import annotations
from typing import Any, Optional
from .findings import FindingCollector
from .model import AuditConfig, LoadedLayer
from .coverage import audit_coverage, request_from_config

_RULE_BY_CLASSIFICATION = {
    "internal": "COVERAGE_GAP_INTERNAL",
    "boundary": "COVERAGE_GAP_BOUNDARY",
}

def check_coverage(
    config: AuditConfig,
    layers: tuple[LoadedLayer, ...],
    collector: FindingCollector,
    document: Optional[dict[str, Any]] = None,
) -> None:
    """Run the opt-in coverage audit and emit its findings.

    This is a no-op unless the configuration contains a ``coverage`` block, so
    the historical finding set is unchanged for existing configurations.
    """
    if config.coverage is None:
        return
    layer = next(
        (item for item in layers if item.config.layer_id == config.coverage.layer_id),
        None,
    )
    tolerance = (
        config.tolerance
        if layer is None
        else layer.config.effective_tolerance(config.tolerance)
    )
    request = request_from_config(config)
    if document is None:
        document = audit_coverage(request, layers, tolerance.coordinate_epsilon)
    enabled = None if layer is None else layer.config.rule_enabled
    if document["status"] == "indeterminate":
        if enabled is not None and not enabled("COVERAGE_INDETERMINATE"):
            return
        blocked = document["blocked"]
        collector.add(
            "COVERAGE_INDETERMINATE",
            request.layer_id,
            evidence={
                "reason": blocked["reason"],
                "feature_issues": blocked["feature_issues"],
                "mode": request.mode,
            },
        )
        return
    for component in document["components"]:
        rule_id = _RULE_BY_CLASSIFICATION[component["classification"]]
        if enabled is not None and not enabled(rule_id):
            continue
        collector.add(
            rule_id,
            request.layer_id,
            related_feature_ids=component["adjacent_feature_ids"],
            evidence={
                "mode": request.mode,
                "component_id": component["component_id"],
                "classification": component["classification"],
                "required": component["required"],
                "area": component["area"],
                "bbox": component["bbox"],
                "cells": component["cells"],
                "adjacent_feature_ids": component["adjacent_feature_ids"],
                "aoi_geometry_digest": document["aoi"]["geometry_digest"],
                "coverage_input_digest": document["input_digest"],
            },
        )
