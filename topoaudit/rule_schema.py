"""Attribute schema checks for individual features."""
from __future__ import annotations
from typing import Any
from .findings import FindingCollector
from .model import Feature, LayerConfig
from .util import truncate_text

def check_schema(feature: Feature, layer: LayerConfig, collector: FindingCollector) -> None:
    for name in sorted(layer.schema):
        spec = layer.schema[name]
        value = feature.properties.get(name)
        present = name in feature.properties and not _is_missing(value)
        if not present:
            if spec.required and layer.rule_enabled("SCHEMA_MISSING_FIELD"):
                collector.add(
                    "SCHEMA_MISSING_FIELD",
                    layer.layer_id,
                    feature.feature_id,
                    evidence={
                        "field": name,
                        "expected_type": spec.kind,
                        "source_index": feature.source_index,
                    },
                )
            continue
        if _matches_type(value, spec.kind):
            continue
        if layer.rule_enabled("SCHEMA_TYPE_MISMATCH"):
            collector.add(
                "SCHEMA_TYPE_MISMATCH",
                layer.layer_id,
                feature.feature_id,
                evidence={
                    "field": name,
                    "expected_type": spec.kind,
                    "actual_type": _json_type(value),
                    "value": truncate_text(_preview(value), 80),
                    "source_index": feature.source_index,
                },
            )

def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")

def _matches_type(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    return False

def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__

def _preview(value: Any) -> str:
    if isinstance(value, str):
        return value
    return repr(value)
