"""The immutable catalog of audit rules and their fixed severity levels."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class RuleMeta:
    rule_id: str
    severity: str
    title: str
    description: str
    evidence_fields: tuple[str, ...]
    def as_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "evidence_fields": list(self.evidence_fields),
        }
_RULE_ROWS = (
    ("SCHEMA_MISSING_FIELD", "error", "必填属性缺失", "必填字段不存在，或值为 null/空字符串。", "field,expected_type,source_index"),
    ("SCHEMA_TYPE_MISMATCH", "error", "属性类型不匹配", "属性值类型与 schema 声明不一致；CSV 转换失败时保留原文。", "field,expected_type,actual_type,value,source_index"),
    ("COORD_NON_FINITE", "error", "坐标非有限值", "坐标出现 NaN 或 Infinity，无法可靠参与几何计算。", "point_count,locations"),
    ("COORD_OUT_OF_BOUNDS", "error", "坐标越界", "坐标超出配置 crs.bounds。", "bounds,point_count,locations"),
    ("GEOM_UNSUPPORTED_TYPE", "error", "几何类型不受支持", "几何为空、未知类型或不在图层白名单内。", "geometry_type,allowed_types,reason"),
    ("RING_NOT_CLOSED", "error", "环未闭合", "多边形环首尾坐标在容差内不相等。", "locations,coordinate_epsilon"),
    ("RING_TOO_FEW_POINTS", "error", "环点数不足", "多边形环少于 4 个坐标（含闭合点）。", "locations,minimum_points"),
    ("LINE_TOO_FEW_POINTS", "error", "线点数不足", "线组成部分少于 2 个坐标。", "locations,minimum_points"),
    ("DUPLICATE_CONSECUTIVE_VERTEX", "warning", "连续重复顶点", "坐标序列中相邻顶点在容差内重复。", "locations,duplicate_count,coordinate_epsilon"),
    ("ZERO_LENGTH_SEGMENT", "warning", "零长度线段", "线段两端坐标在容差内相等。", "locations,segment_count,coordinate_epsilon"),
    ("SELF_INTERSECTION", "error", "自相交", "按图层 line_usage 合同裁定线/环的段对：proper crossing、正长度共线重走、不可判定接触在 boundary_curve 与 route_walk 下均上报；非相邻点触与顶点复访仅在 boundary_curve 下上报。", "usage,coordinate_epsilon,violation_count,pair_count,allowed_shared_endpoint_count,pairs"),
    ("DUPLICATE_FEATURE_ID", "error", "要素 ID 重复", "图层内多个要素使用相同 ID 字段值。", "id_field,value,first_source_index,duplicate_source_index"),
    ("DUPLICATE_GEOMETRY", "warning", "几何重复", "规范坐标序列完全一致。", "geometry_digest,first_source_index,duplicate_source_index,coordinate_epsilon"),
    ("POLYGON_OVERLAP", "error", "多边形压盖", "两个有效多边形内部重叠；仅共边或共点不算。", "pair,component_indexes,bbox_intersection"),
    ("SLIVER_POLYGON", "warning", "狭长多边形", "面积周长比低于配置阈值。", "part_index,area,perimeter,ratio,threshold"),
    ("DANGLING_ENDPOINT", "warning", "悬挂端点", "线端点附近没有其他要素的线端点。", "part_index,endpoint,coordinate,nearest_feature_id,nearest_distance,tolerance"),
    ("NEAR_MISS_ENDPOINT", "info", "端点近接", "不同线的端点距离超过坐标容差但不超过近接容差。", "endpoint_a,endpoint_b,distance,tolerance"),
    ("LAYER_CRS_MISMATCH", "error", "图层 CRS 声明不一致", "图层 declared_crs 与全局 crs 名称或类型不一致。", "expected_crs,declared_crs"),
    ("EMPTY_LAYER", "warning", "空图层", "图层加载后不包含任何要素。", "feature_count"),
)
RULE_CATALOG: dict[str, RuleMeta] = {
    row[0]: RuleMeta(row[0], row[1], row[2], row[3], tuple(row[4].split(",")))
    for row in _RULE_ROWS
}
RULE_IDS: tuple[str, ...] = tuple(row[0] for row in _RULE_ROWS)

def rule_meta(rule_id: str) -> RuleMeta:
    return RULE_CATALOG[rule_id]

def catalog_rows() -> list[dict[str, object]]:
    return [RULE_CATALOG[key].as_dict() for key in sorted(RULE_CATALOG)]
