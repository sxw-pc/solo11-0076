"""Offline loaders for local GeoJSON FeatureCollections and CSV point tables."""
from __future__ import annotations
import csv
import io
import json
import re
from typing import Any
from .errors import InputError
from .model import AuditConfig, Feature, Geometry, InputFile, LayerConfig, LoadedLayer
from .util import canonical_json, sha256_bytes, sha256_text
_INTEGER_RE = re.compile(r"^[+-]?\d+$")

def load_layers(config: AuditConfig) -> tuple[LoadedLayer, ...]:
    loaded: list[LoadedLayer] = []
    for index, layer in enumerate(config.layers):
        locator = f"$.layers[{index}]"
        if layer.source_format == "geojson":
            loaded.append(_load_geojson(layer, locator))
        elif layer.source_format == "csv":
            loaded.append(_load_csv(layer, locator))
        else:  # pragma: no cover - config parser prevents this
            raise InputError("TOPO-INP-004", f"不支持的输入格式: {layer.source_format}", locator)
    return tuple(loaded)

def compute_input_digest(config_digest: str, files: tuple[InputFile, ...]) -> str:
    payload = {
        "config_digest": config_digest,
        "files": [
            {
                "layer_id": item.layer_id,
                "format": item.source_format,
                "sha256": item.sha256,
            }
            for item in sorted(files, key=lambda value: value.layer_id)
        ],
    }
    return sha256_text(canonical_json(payload))
def _read_source(layer: LayerConfig, locator: str) -> tuple[str, InputFile]:
    path = layer.resolved_path
    if not path.is_file():
        raise InputError("TOPO-INP-001", "输入文件不存在或不是普通文件", f"{locator}.path")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError("TOPO-INP-002", f"无法读取输入文件: {exc}", f"{locator}.path") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError("TOPO-INP-003", "输入文件不是有效 UTF-8 文本", f"{locator}.path") from exc
    digest = sha256_bytes(raw)
    return text, InputFile(
        layer_id=layer.layer_id,
        path=layer.path,
        source_format=layer.source_format,
        sha256=digest,
        size_bytes=len(raw),
    )
def _load_geojson(layer: LayerConfig, locator: str) -> LoadedLayer:
    text, input_file = _read_source(layer, locator)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        where = f"line {exc.lineno}, column {exc.colno}"
        raise InputError("TOPO-INP-005", f"GeoJSON 解析失败 ({where})", f"{locator}.path") from exc
    if not isinstance(raw, dict) or raw.get("type") != "FeatureCollection":
        raise InputError("TOPO-INP-005", "GeoJSON 根节点必须是 FeatureCollection", f"{locator}.path")
    features_raw = raw.get("features")
    if not isinstance(features_raw, list):
        raise InputError("TOPO-INP-005", "FeatureCollection.features 必须是数组", f"{locator}.path")
    features: list[Feature] = []
    for index, item in enumerate(features_raw):
        feature = _parse_feature(item, layer, index, locator)
        features.append(feature)
    return LoadedLayer(config=layer, features=tuple(features), input_file=input_file)
def _parse_feature(item: Any, layer: LayerConfig, index: int, locator: str) -> Feature:
    where = f"{locator}.features[{index}]"
    if not isinstance(item, dict) or item.get("type") != "Feature":
        raise InputError("TOPO-INP-005", "GeoJSON 要素必须是 type=Feature 的对象", where)
    properties = item.get("properties", {})
    if properties is None:
        properties = {}
    if not isinstance(properties, dict):
        raise InputError("TOPO-INP-005", "feature.properties 必须是对象或 null", f"{where}.properties")
    geometry_raw = item.get("geometry")
    if geometry_raw is None:
        geometry = Geometry(kind="Unsupported", coordinates=(), raw_type=None)
    elif not isinstance(geometry_raw, dict):
        raise InputError("TOPO-INP-006", "geometry 必须是对象或 null", f"{where}.geometry")
    else:
        type_raw = geometry_raw.get("type")
        if not isinstance(type_raw, str):
            geometry = Geometry(kind="Unsupported", coordinates=(), raw_type=None)
        elif type_raw not in {
            "Point",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
        }:
            geometry = Geometry(kind="Unsupported", coordinates=(), raw_type=type_raw)
        else:
            coords = _parse_coordinates(type_raw, geometry_raw.get("coordinates"), f"{where}.geometry.coordinates")
            geometry = Geometry(kind=type_raw, coordinates=coords, raw_type=type_raw)
    return Feature(
        layer_id=layer.layer_id,
        feature_id=_feature_id(properties, layer.id_field, index),
        geometry=geometry,
        properties=properties,
        source_index=index,
    )
def _parse_coordinates(kind: str, raw: Any, where: str) -> tuple[Any, ...]:
    if kind == "Point":
        return _parse_position(raw, where)
    if not isinstance(raw, list):
        raise InputError("TOPO-INP-006", f"{kind}.coordinates 必须是数组", where)
    if kind == "LineString":
        return tuple(_parse_position(item, f"{where}[{i}]") for i, item in enumerate(raw))
    if kind == "MultiLineString":
        return tuple(_parse_line(item, f"{where}[{i}]") for i, item in enumerate(raw))
    if kind == "Polygon":
        return tuple(_parse_ring(item, f"{where}[{i}]") for i, item in enumerate(raw))
    if kind == "MultiPolygon":
        polygons: list[tuple[Any, ...]] = []
        for i, polygon in enumerate(raw):
            if not isinstance(polygon, list):
                raise InputError("TOPO-INP-006", "MultiPolygon 成员必须是环数组", f"{where}[{i}]")
            polygons.append(
                tuple(
                    _parse_ring(ring, f"{where}[{i}][{j}]")
                    for j, ring in enumerate(polygon)
                )
            )
        return tuple(polygons)
    raise InputError("TOPO-INP-006", f"不支持的几何类型: {kind}", where)
def _parse_line(raw: Any, where: str) -> tuple[Any, ...]:
    if not isinstance(raw, list):
        raise InputError("TOPO-INP-006", "LineString 必须是坐标数组", where)
    return tuple(_parse_position(item, f"{where}[{i}]") for i, item in enumerate(raw))
def _parse_ring(raw: Any, where: str) -> tuple[Any, ...]:
    if not isinstance(raw, list):
        raise InputError("TOPO-INP-006", "Polygon 环必须是坐标数组", where)
    return tuple(_parse_position(item, f"{where}[{i}]") for i, item in enumerate(raw))
def _parse_position(raw: Any, where: str) -> tuple[float, float]:
    if not isinstance(raw, list) or len(raw) != 2:
        raise InputError("TOPO-INP-006", "坐标必须是二元数组（仅支持二维）", where)
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InputError("TOPO-INP-006", "坐标分量必须是数字", where)
    return (float(raw[0]), float(raw[1]))
def _load_csv(layer: LayerConfig, locator: str) -> LoadedLayer:
    text, input_file = _read_source(layer, locator)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = reader.fieldnames
    if not headers:
        raise InputError("TOPO-INP-007", "CSV 缺少表头", f"{locator}.path")
    if len(headers) != len(set(headers)):
        raise InputError("TOPO-INP-007", "CSV 表头存在重复列", f"{locator}.path")
    required = {"id", "x", "y", layer.id_field}
    missing = sorted(required.difference(headers))
    if missing:
        raise InputError(
            "TOPO-INP-007",
            f"CSV 缺少必需列: {', '.join(missing)}",
            f"{locator}.path",
        )
    features: list[Feature] = []
    try:
        for index, row in enumerate(reader):
            features.append(_csv_feature(row, headers, layer, index, locator))
    except csv.Error as exc:
        raise InputError("TOPO-INP-008", f"CSV 解析失败: {exc}", f"{locator}.path") from exc
    return LoadedLayer(config=layer, features=tuple(features), input_file=input_file)
def _csv_feature(row: dict[str, Any], headers: list[str], layer: LayerConfig, index: int, locator: str) -> Feature:
    where = f"{locator}.rows[{index}]"
    try:
        x = float(str(row.get("x", "")).strip())
        y = float(str(row.get("y", "")).strip())
    except (TypeError, ValueError) as exc:
        raise InputError("TOPO-INP-008", "CSV x/y 必须是数值", where) from exc
    properties: dict[str, Any] = {}
    for name in headers:
        if name in ("x", "y"):
            continue
        properties[name] = _coerce_csv_value(row.get(name), layer, name)
    return Feature(
        layer_id=layer.layer_id,
        feature_id=_feature_id(properties, layer.id_field, index),
        geometry=Geometry(kind="Point", coordinates=(x, y), raw_type="Point"),
        properties=properties,
        source_index=index,
    )
def _coerce_csv_value(value: Any, layer: LayerConfig, field_name: str) -> Any:
    spec = layer.schema.get(field_name)
    if spec is None or value is None or value == "":
        return value
    text = str(value)
    if spec.kind == "string":
        return text
    if spec.kind == "integer" and _INTEGER_RE.fullmatch(text):
        try:
            return int(text)
        except ValueError:
            return text
    if spec.kind == "number":
        try:
            return float(text)
        except ValueError:
            return text
    if spec.kind == "boolean":
        lowered = text.strip().lower()
        if lowered in ("true", "false"):
            return lowered == "true"
    if spec.kind in ("object", "array"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        expected = dict if spec.kind == "object" else list
        return parsed if isinstance(parsed, expected) else text
    return text
def _feature_id(properties: dict[str, Any], id_field: str, index: int) -> str:
    if id_field in properties:
        value = properties[id_field]
        if value is not None and not isinstance(value, (dict, list)) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                return text
    return f"<missing:{index}>"
