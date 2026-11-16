"""Parsing for the optional ``coverage_rules`` configuration block.

Kept separate from :mod:`topoaudit.config_schema` so each module stays
small.  Rules reference existing layers and enforce line/polygon geometry
whitelists plus an explicit boundary interpretation.
"""
from __future__ import annotations
from typing import Any
from .config_schema import _finite_number, _nonempty_string, _object, _require, _string
from .errors import ConfigError
from .model import (
    COVERAGE_BOUNDARY_MODES,
    LINE_GEOMETRY_TYPES,
    POLYGON_GEOMETRY_TYPES,
    CoverageRuleConfig,
    LayerConfig,
)

def parse_coverage_rules(
    raw: Any, path: str, layers: tuple[LayerConfig, ...]
) -> tuple[CoverageRuleConfig, ...]:
    """Parse the optional ``coverage_rules`` array.

    Each rule names one line layer and one polygon layer.  Layers must
    exist, the line layer must allow line geometry and the polygon layer
    must allow polygon geometry.  ``boundary_mode`` is mandatory so that
    the business interpretation of boundary-length is always declared,
    never inferred.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise ConfigError("TOPO-CFG-006", "coverage_rules 必须是非空数组", path)
    by_id = {layer.layer_id: layer for layer in layers}
    rules: list[CoverageRuleConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        item_path = f"{path}[{index}]"
        obj = _object(
            item,
            item_path,
            {
                "id",
                "line_layer",
                "polygon_layer",
                "boundary_mode",
                "tolerance",
                "enabled",
            },
        )
        rule_id = _nonempty_string(_require(obj, "id", item_path), f"{item_path}.id")
        if rule_id in seen:
            raise ConfigError(
                "TOPO-CFG-008", f"coverage rule id 重复: {rule_id}", f"{item_path}.id"
            )
        seen.add(rule_id)
        line_id = _nonempty_string(
            _require(obj, "line_layer", item_path), f"{item_path}.line_layer"
        )
        polygon_id = _nonempty_string(
            _require(obj, "polygon_layer", item_path), f"{item_path}.polygon_layer"
        )
        line_layer = by_id.get(line_id)
        if line_layer is None:
            raise ConfigError(
                "TOPO-CFG-006",
                f"coverage rule 引用了不存在的线图层: {line_id}",
                f"{item_path}.line_layer",
            )
        polygon_layer = by_id.get(polygon_id)
        if polygon_layer is None:
            raise ConfigError(
                "TOPO-CFG-006",
                f"coverage rule 引用了不存在的面图层: {polygon_id}",
                f"{item_path}.polygon_layer",
            )
        if not set(line_layer.geometry_types).issubset(set(LINE_GEOMETRY_TYPES)):
            raise ConfigError(
                "TOPO-CFG-006",
                "coverage rule 的线图层只能包含 LineString/MultiLineString",
                f"{item_path}.line_layer",
            )
        if not set(polygon_layer.geometry_types).issubset(set(POLYGON_GEOMETRY_TYPES)):
            raise ConfigError(
                "TOPO-CFG-006",
                "coverage rule 的面图层只能包含 Polygon/MultiPolygon",
                f"{item_path}.polygon_layer",
            )
        if line_id == polygon_id:
            raise ConfigError(
                "TOPO-CFG-006",
                "coverage rule 的线图层与面图层不能相同",
                f"{item_path}.polygon_layer",
            )
        boundary_mode = _string(
            _require(obj, "boundary_mode", item_path), f"{item_path}.boundary_mode"
        )
        if boundary_mode not in COVERAGE_BOUNDARY_MODES:
            raise ConfigError(
                "TOPO-CFG-006",
                "boundary_mode 只能是 strict_interior 或 boundary_allowed",
                f"{item_path}.boundary_mode",
            )
        tolerance = _finite_number(
            _require(obj, "tolerance", item_path), f"{item_path}.tolerance"
        )
        if tolerance < 0:
            raise ConfigError(
                "TOPO-CFG-006", "coverage tolerance 不能为负数", f"{item_path}.tolerance"
            )
        enabled = obj.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(
                "TOPO-CFG-004", "enabled 必须是布尔值", f"{item_path}.enabled"
            )
        rules.append(
            CoverageRuleConfig(
                rule_id=rule_id,
                line_layer_id=line_id,
                polygon_layer_id=polygon_id,
                boundary_mode=boundary_mode,
                tolerance=tolerance,
                enabled=enabled,
            )
        )
    return tuple(rules)