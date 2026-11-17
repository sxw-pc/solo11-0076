"""Strict, path-aware parsing of the JSON audit configuration."""
from __future__ import annotations
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from .config_schema import parse_assignment, parse_crs, parse_layer, parse_tolerance
from .errors import ConfigError
from .model import AssignmentConfig, AuditConfig, LayerConfig
from .util import canonical_json, sha256_text
CONFIG_SCHEMA_VERSION = "1.0"

def load_config(path: str | Path) -> AuditConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError("TOPO-CFG-001", "配置文件不存在或不是普通文件", str(path))
    try:
        raw_text = config_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ConfigError("TOPO-CFG-001", f"无法读取配置文件: {exc}", str(path)) from exc
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        location = f"line {exc.lineno}, column {exc.colno}"
        raise ConfigError("TOPO-CFG-002", f"配置 JSON 解析失败 ({location})", "$") from exc
    if not isinstance(raw, dict):
        raise ConfigError("TOPO-CFG-004", "配置根节点必须是 JSON 对象", "$")
    _reject_unknown(
        raw,
        {"schema_version", "run_label", "tolerance", "crs", "layers", "assignment"},
        "$",
    )
    schema_version = _require(raw, "schema_version", "$")
    if schema_version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            "TOPO-CFG-009",
            f"不支持的 schema_version: {schema_version!r}",
            "$.schema_version",
        )
    run_label = _nonempty_string(_require(raw, "run_label", "$"), "$.run_label")
    tolerance = parse_tolerance(_require(raw, "tolerance", "$"), "$.tolerance")
    crs = parse_crs(_require(raw, "crs", "$"), "$.crs")
    layers_raw = _require(raw, "layers", "$")
    if not isinstance(layers_raw, list) or not layers_raw:
        raise ConfigError("TOPO-CFG-006", "layers 必须是非空数组", "$.layers")
    layers = []
    seen_ids: set[str] = set()
    for index, layer_raw in enumerate(layers_raw):
        layer = parse_layer(layer_raw, f"$.layers[{index}]", config_path.parent)
        if layer.layer_id in seen_ids:
            raise ConfigError(
                "TOPO-CFG-008",
                f"图层 id 重复: {layer.layer_id}",
                f"$.layers[{index}].id",
            )
        seen_ids.add(layer.layer_id)
        layers.append(layer)
    assignment = None
    if "assignment" in raw:
        assignment = _resolve_assignment(
            parse_assignment(raw["assignment"], "$.assignment"), layers
        )
    public = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "run_label": run_label,
        "tolerance": tolerance.as_dict(),
        "crs": crs.as_dict(),
        "layers": [layer.as_dict() for layer in layers],
    }
    if assignment is not None:
        public["assignment"] = assignment.as_dict()
    config_digest = sha256_text(canonical_json(public))
    return AuditConfig(
        path=config_path,
        config_digest=config_digest,
        schema_version=CONFIG_SCHEMA_VERSION,
        run_label=run_label,
        tolerance=tolerance,
        crs=crs,
        layers=tuple(layers),
        assignment=assignment,
    )

def _resolve_assignment(
    assignment: AssignmentConfig, layers: list[LayerConfig]
) -> AssignmentConfig:
    """Validate the assignment block against the parsed layers."""
    by_id = {layer.layer_id: layer for layer in layers}
    point_layer = by_id.get(assignment.point_layer)
    if point_layer is None:
        raise ConfigError(
            "TOPO-CFG-010",
            f"assignment.point_layer 引用了不存在的图层: {assignment.point_layer}",
            "$.assignment.point_layer",
        )
    zone_layer = by_id.get(assignment.zone_layer)
    if zone_layer is None:
        raise ConfigError(
            "TOPO-CFG-010",
            f"assignment.zone_layer 引用了不存在的图层: {assignment.zone_layer}",
            "$.assignment.zone_layer",
        )
    if "Point" not in point_layer.geometry_types:
        raise ConfigError(
            "TOPO-CFG-010",
            "assignment.point_layer 的 geometry_types 必须包含 Point",
            "$.assignment.point_layer",
        )
    if not {"Polygon", "MultiPolygon"} & set(zone_layer.geometry_types):
        raise ConfigError(
            "TOPO-CFG-010",
            "assignment.zone_layer 的 geometry_types 必须包含 Polygon 或 MultiPolygon",
            "$.assignment.zone_layer",
        )
    zone_id_field = assignment.zone_id_field or zone_layer.id_field
    if zone_id_field != assignment.zone_id_field:
        assignment = replace(assignment, zone_id_field=zone_id_field)
    return assignment
def _reject_unknown(raw: dict[str, Any], allowed: set[str], path: str) -> None:
    for key in raw:
        if key not in allowed:
            raise ConfigError("TOPO-CFG-003", f"未知配置字段: {key}", f"{path}.{key}")
def _require(obj: dict[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        raise ConfigError("TOPO-CFG-004", f"缺少必填字段: {key}", f"{path}.{key}")
    return obj[key]
def _nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ConfigError("TOPO-CFG-004", "字段必须是字符串", path)
    if not value.strip():
        raise ConfigError("TOPO-CFG-006", "字段不能为空", path)
    return value
