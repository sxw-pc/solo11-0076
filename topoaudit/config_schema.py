"""Nested configuration parsers and schema validation helpers."""
from __future__ import annotations
import math
from pathlib import Path
from typing import Any
from .errors import ConfigError
from .model import (
    BOUNDARY_POLICIES,
    SUPPORTED_GEOMETRY_TYPES,
    SUPPORTED_SCHEMA_TYPES,
    AssignmentConfig,
    Bounds,
    CRS,
    DeclaredCRS,
    FieldSpec,
    LayerConfig,
    Tolerance,
)
from .rules_catalog import RULE_IDS
THRESHOLD_KEYS = ("coordinate_epsilon", "near_miss_distance", "sliver_ratio")

def parse_tolerance(raw: Any, path: str) -> Tolerance:
    obj = _object(raw, path, set(THRESHOLD_KEYS))
    values = Tolerance().as_dict()
    for key in THRESHOLD_KEYS:
        if key in obj:
            values[key] = _finite_number(obj[key], f"{path}.{key}")
    if values["coordinate_epsilon"] <= 0:
        raise ConfigError("TOPO-CFG-006", "coordinate_epsilon 必须大于 0", f"{path}.coordinate_epsilon")
    if values["near_miss_distance"] < 0:
        raise ConfigError("TOPO-CFG-006", "near_miss_distance 不能为负数", f"{path}.near_miss_distance")
    if values["sliver_ratio"] <= 0:
        raise ConfigError("TOPO-CFG-006", "sliver_ratio 必须大于 0", f"{path}.sliver_ratio")
    return Tolerance(**values)

def parse_crs(raw: Any, path: str) -> CRS:
    obj = _object(raw, path, {"name", "type", "bounds"})
    name = _nonempty_string(_require(obj, "name", path), f"{path}.name")
    kind = _string(_require(obj, "type", path), f"{path}.type")
    if kind not in ("planar", "geographic"):
        raise ConfigError("TOPO-CFG-006", "crs.type 只能是 planar 或 geographic", f"{path}.type")
    bounds = _parse_bounds(_require(obj, "bounds", path), f"{path}.bounds")
    if kind == "geographic" and not (
        -180 <= bounds.min_x <= bounds.max_x <= 180
        and -90 <= bounds.min_y <= bounds.max_y <= 90
    ):
        raise ConfigError(
            "TOPO-CFG-006",
            "geographic CRS 边界必须位于经纬度范围内",
            f"{path}.bounds",
        )
    return CRS(name=name, kind=kind, bounds=bounds)
def _parse_bounds(raw: Any, path: str) -> Bounds:
    keys = {"min_x", "min_y", "max_x", "max_y"}
    obj = _object(raw, path, keys)
    values = {key: _finite_number(_require(obj, key, path), f"{path}.{key}") for key in keys}
    if values["min_x"] >= values["max_x"] or values["min_y"] >= values["max_y"]:
        raise ConfigError(
            "TOPO-CFG-006",
            "坐标边界必须满足 min_x < max_x 且 min_y < max_y",
            path,
        )
    return Bounds(**values)

def parse_layer(raw: Any, path: str, base_dir: Path) -> LayerConfig:
    allowed = {
        "id",
        "path",
        "format",
        "geometry_types",
        "id_field",
        "schema",
        "rules",
        "thresholds",
        "declared_crs",
    }
    obj = _object(raw, path, allowed)
    layer_id = _nonempty_string(_require(obj, "id", path), f"{path}.id")
    source = _nonempty_string(_require(obj, "path", path), f"{path}.path")
    source_format = _string(_require(obj, "format", path), f"{path}.format")
    if source_format not in ("geojson", "csv"):
        raise ConfigError("TOPO-CFG-006", "format 只能是 geojson 或 csv", f"{path}.format")
    geometry_types = _string_list(
        _require(obj, "geometry_types", path), f"{path}.geometry_types"
    )
    if not geometry_types:
        raise ConfigError("TOPO-CFG-006", "geometry_types 不能为空", f"{path}.geometry_types")
    for index, item in enumerate(geometry_types):
        if item not in SUPPORTED_GEOMETRY_TYPES:
            raise ConfigError(
                "TOPO-CFG-006",
                f"不支持的几何类型: {item}",
                f"{path}.geometry_types[{index}]",
            )
    if source_format == "csv" and geometry_types != ("Point",):
        raise ConfigError(
            "TOPO-CFG-006",
            "CSV 图层的 geometry_types 必须是 ['Point']",
            f"{path}.geometry_types",
        )
    id_field = _nonempty_string(_require(obj, "id_field", path), f"{path}.id_field")
    schema = _parse_schema(_require(obj, "schema", path), f"{path}.schema")
    rules = _parse_rules(obj.get("rules", {}), f"{path}.rules")
    thresholds = _parse_layer_thresholds(obj.get("thresholds", {}), f"{path}.thresholds")
    declared = _parse_declared_crs(obj.get("declared_crs"), f"{path}.declared_crs")
    return LayerConfig(
        layer_id=layer_id,
        path=source,
        resolved_path=(base_dir / source).resolve(),
        source_format=source_format,
        geometry_types=geometry_types,
        id_field=id_field,
        schema=schema,
        rules=rules,
        threshold_overrides=thresholds,
        declared_crs=declared,
    )
def _parse_schema(raw: Any, path: str) -> dict[str, FieldSpec]:
    if not isinstance(raw, dict):
        raise ConfigError("TOPO-CFG-004", "schema 必须是 JSON 对象", path)
    result: dict[str, FieldSpec] = {}
    for name in sorted(raw):
        if not isinstance(name, str) or not name:
            raise ConfigError("TOPO-CFG-006", "schema 字段名必须是非空字符串", path)
        spec_path = f"{path}.{name}"
        spec = _object(raw[name], spec_path, {"type", "required"})
        kind = _string(_require(spec, "type", spec_path), f"{spec_path}.type")
        if kind not in SUPPORTED_SCHEMA_TYPES:
            raise ConfigError("TOPO-CFG-006", f"不支持的字段类型: {kind}", f"{spec_path}.type")
        required = spec.get("required", True)
        if not isinstance(required, bool):
            raise ConfigError("TOPO-CFG-004", "required 必须是布尔值", f"{spec_path}.required")
        result[name] = FieldSpec(name=name, kind=kind, required=required)
    return result
def _parse_rules(raw: Any, path: str) -> dict[str, bool]:
    if not isinstance(raw, dict):
        raise ConfigError("TOPO-CFG-004", "rules 必须是 JSON 对象", path)
    result: dict[str, bool] = {}
    for name in sorted(raw):
        if name not in RULE_IDS:
            raise ConfigError("TOPO-CFG-007", f"未知规则 ID: {name}", f"{path}.{name}")
        spec = _object(raw[name], f"{path}.{name}", {"enabled"})
        enabled = _require(spec, "enabled", f"{path}.{name}")
        if not isinstance(enabled, bool):
            raise ConfigError("TOPO-CFG-004", "enabled 必须是布尔值", f"{path}.{name}.enabled")
        result[name] = enabled
    return result
def _parse_layer_thresholds(raw: Any, path: str) -> dict[str, float]:
    obj = _object(raw, path, set(THRESHOLD_KEYS))
    result: dict[str, float] = {}
    for key in THRESHOLD_KEYS:
        if key in obj:
            value = _finite_number(obj[key], f"{path}.{key}")
            if key == "coordinate_epsilon" and value <= 0:
                raise ConfigError("TOPO-CFG-006", "coordinate_epsilon 必须大于 0", f"{path}.{key}")
            if key == "near_miss_distance" and value < 0:
                raise ConfigError("TOPO-CFG-006", "near_miss_distance 不能为负数", f"{path}.{key}")
            if key == "sliver_ratio" and value <= 0:
                raise ConfigError("TOPO-CFG-006", "sliver_ratio 必须大于 0", f"{path}.{key}")
            result[key] = value
    return result
def _parse_declared_crs(raw: Any, path: str) -> DeclaredCRS | None:
    if raw is None:
        return None
    obj = _object(raw, path, {"name", "type"})
    name = _nonempty_string(_require(obj, "name", path), f"{path}.name")
    kind = _string(_require(obj, "type", path), f"{path}.type")
    if kind not in ("planar", "geographic"):
        raise ConfigError("TOPO-CFG-006", "declared_crs.type 必须是 planar 或 geographic", f"{path}.type")
    return DeclaredCRS(name=name, kind=kind)
def parse_assignment(raw: Any, path: str) -> AssignmentConfig:
    """Parse the cross-layer assignment precheck block (shape only).

    Cross-references to layers (point/zone layer existence, geometry
    whitelists, zone id defaulting) are validated by ``config.load_config``
    once all layers are parsed.
    """
    obj = _object(
        raw,
        path,
        {
            "point_layer",
            "zone_layer",
            "zone_id_field",
            "max_facilities_per_zone",
            "zone_capacities",
            "boundary_policy",
            "anchors",
        },
    )
    point_layer = _nonempty_string(_require(obj, "point_layer", path), f"{path}.point_layer")
    zone_layer = _nonempty_string(_require(obj, "zone_layer", path), f"{path}.zone_layer")
    if point_layer == zone_layer:
        raise ConfigError(
            "TOPO-CFG-006", "point_layer 与 zone_layer 不能是同一图层", f"{path}.zone_layer"
        )
    zone_id_field_raw = obj.get("zone_id_field")
    zone_id_field = ""
    if zone_id_field_raw is not None:
        zone_id_field = _nonempty_string(zone_id_field_raw, f"{path}.zone_id_field")
    default_capacity = _positive_int(
        _require(obj, "max_facilities_per_zone", path), f"{path}.max_facilities_per_zone"
    )
    zone_capacities = _parse_zone_capacities(obj.get("zone_capacities", {}), f"{path}.zone_capacities")
    boundary_policy = _string(_require(obj, "boundary_policy", path), f"{path}.boundary_policy")
    if boundary_policy not in BOUNDARY_POLICIES:
        raise ConfigError(
            "TOPO-CFG-006",
            "boundary_policy 只能是 candidate 或 review",
            f"{path}.boundary_policy",
        )
    anchors = _parse_anchors(_require(obj, "anchors", path), f"{path}.anchors")
    return AssignmentConfig(
        point_layer=point_layer,
        zone_layer=zone_layer,
        zone_id_field=zone_id_field,
        max_facilities_per_zone=default_capacity,
        zone_capacities=zone_capacities,
        boundary_policy=boundary_policy,
        anchors=anchors,
    )

def _parse_zone_capacities(raw: Any, path: str) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise ConfigError("TOPO-CFG-004", "zone_capacities 必须是 JSON 对象", path)
    result: dict[str, int] = {}
    for key in sorted(raw):
        if not isinstance(key, str) or not key.strip():
            raise ConfigError("TOPO-CFG-006", "zone_capacities 的键必须是非空字符串", path)
        result[key] = _positive_int(raw[key], f"{path}.{key}")
    return result

def _parse_anchors(raw: Any, path: str) -> dict[str, tuple[float, float]]:
    if not isinstance(raw, dict):
        raise ConfigError("TOPO-CFG-004", "anchors 必须是 JSON 对象", path)
    if not raw:
        raise ConfigError("TOPO-CFG-006", "anchors 不能为空", path)
    result: dict[str, tuple[float, float]] = {}
    for key in sorted(raw):
        if not isinstance(key, str) or not key.strip():
            raise ConfigError("TOPO-CFG-006", "anchors 的键必须是非空字符串", path)
        value = raw[key]
        item_path = f"{path}.{key}"
        if not isinstance(value, list) or len(value) != 2:
            raise ConfigError("TOPO-CFG-004", "锚点必须是 [x, y] 二元数组", item_path)
        result[key] = (
            _finite_number(value[0], f"{item_path}[0]"),
            _finite_number(value[1], f"{item_path}[1]"),
        )
    return result

def _positive_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("TOPO-CFG-004", "字段必须是整数", path)
    if value < 1:
        raise ConfigError("TOPO-CFG-006", "字段必须是 >= 1 的整数", path)
    return value

def _object(raw: Any, path: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError("TOPO-CFG-004", "此处必须是 JSON 对象", path)
    _reject_unknown(raw, allowed, path)
    return raw
def _reject_unknown(raw: dict[str, Any], allowed: set[str], path: str) -> None:
    for key in raw:
        if key not in allowed:
            raise ConfigError("TOPO-CFG-003", f"未知配置字段: {key}", f"{path}.{key}")
def _require(obj: dict[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        raise ConfigError("TOPO-CFG-004", f"缺少必填字段: {key}", f"{path}.{key}")
    return obj[key]
def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ConfigError("TOPO-CFG-004", "字段必须是字符串", path)
    return value
def _nonempty_string(value: Any, path: str) -> str:
    text = _string(value, path)
    if not text.strip():
        raise ConfigError("TOPO-CFG-006", "字段不能为空", path)
    return text
def _string_list(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigError("TOPO-CFG-004", "字段必须是非空字符串数组", path)
    if len(set(value)) != len(value):
        raise ConfigError("TOPO-CFG-006", "数组元素不能重复", path)
    return tuple(value)
def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("TOPO-CFG-004", "字段必须是数字", path)
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError("TOPO-CFG-006", "数字必须是有限值", path)
    return number
