# 规则目录与语义

`topoaudit` 固定提供 19 条规则。规则 ID、严重级别、标题、说明和证据字段由 `topoaudit/rules_catalog.py` 中的不可变目录定义；报告的 `rules` 数组和 SQLite 的 `rule_catalog` 表都来自同一目录。

## 1. 通用语义

- 每条规则有固定严重级别：`error`、`warning`、`info`，配置不能修改。
- 规则可按图层关闭：`"rules": {"RULE_ID": {"enabled": false}}`；未写出的规则默认启用。
- 关闭一条规则不会改变输入，也不会自动触发补偿检查；其他规则仍独立运行。
- 同一规则对同一要素的多个同类异常通常聚合为一条 finding，证据中给出最多 20 个位置（具体见各规则说明）。
- 位置路径使用零基索引，例如 `MultiLineString[0]`、`Polygon.rings[1]`。
- 每条 finding 都包含 `finding_id`、`rule_id`、`severity`、`layer_id`、`feature_id`、`related_feature_ids` 和 `evidence`。
- `feature_id` 来自配置的 `id_field`；无法取得时使用 `<missing:行索引>`。
- 端点类规则只在**同一图层内、不同要素的线端点**之间建立连接语义，不跨图层。

## 2. 规则总表

| # | 规则 ID | 级别 | 触发条件 | 主要证据 |
| ---: | --- | --- | --- | --- |
| 1 | `SCHEMA_MISSING_FIELD` | error | 必填字段缺失，或值为 `null`/空白字符串 | `field`、`expected_type`、`source_index` |
| 2 | `SCHEMA_TYPE_MISMATCH` | error | 字段存在且非缺失，但值与声明类型不符 | `field`、`expected_type`、`actual_type`、`value`、`source_index` |
| 3 | `COORD_NON_FINITE` | error | 任一坐标分量为 `NaN` 或 `Infinity` | `point_count`、`locations`（路径与坐标） |
| 4 | `COORD_OUT_OF_BOUNDS` | error | 任一有限坐标超出全局 `crs.bounds` | `bounds`、`point_count`、`locations` |
| 5 | `GEOM_UNSUPPORTED_TYPE` | error | 几何为 `null`、类型未知，或不在图层白名单 | `geometry_type`、`allowed_types`、`reason` |
| 6 | `RING_NOT_CLOSED` | error | 非空环首尾坐标在 `coordinate_epsilon` 内不相等 | `locations`、`coordinate_epsilon` |
| 7 | `RING_TOO_FEW_POINTS` | error | 任一环坐标数少于 4（含闭合点） | `locations`、`minimum_points` |
| 8 | `LINE_TOO_FEW_POINTS` | error | 任一线组成部分坐标数少于 2 | `locations`、`minimum_points` |
| 9 | `DUPLICATE_CONSECUTIVE_VERTEX` | warning | 相邻顶点在 `coordinate_epsilon` 内重复 | `locations`、`duplicate_count`、`coordinate_epsilon` |
| 10 | `ZERO_LENGTH_SEGMENT` | warning | 线段两端在 `coordinate_epsilon` 内相等 | `locations`、`segment_count`、`coordinate_epsilon` |
| 11 | `SELF_INTERSECTION` | error | 线或环的非相邻线段相交、接触或共线重叠 | `locations`、`segment_pairs` |
| 12 | `DUPLICATE_FEATURE_ID` | error | 同一图层多个要素的 ID 字段字符串值相同 | `id_field`、`value`、首末 `source_index` |
| 13 | `DUPLICATE_GEOMETRY` | warning | 规范几何签名完全相同 | `geometry_digest`、首末 `source_index`、`coordinate_epsilon` |
| 14 | `POLYGON_OVERLAP` | error | 两个不同要素的有效多边形内部重叠 | `pair`、`component_indexes`、`bbox_intersection` |
| 15 | `SLIVER_POLYGON` | warning | 有效多边形面积/周长比低于 `sliver_ratio` | `part_index`、`area`、`perimeter`、`ratio`、`threshold` |
| 16 | `DANGLING_ENDPOINT` | warning | 线端点附近没有其他要素的线端点 | `part_index`、`endpoint`、`coordinate`、`nearest_feature_id`、`nearest_distance`、`tolerance` |
| 17 | `NEAR_MISS_ENDPOINT` | info | 不同要素端点距离大于坐标容差且不超过近接容差 | `endpoint_a`、`endpoint_b`、`distance`、`tolerance` |
| 18 | `LAYER_CRS_MISMATCH` | error | 图层 `declared_crs` 的名称或类型与全局 `crs` 不一致 | `expected_crs`、`declared_crs` |
| 19 | `EMPTY_LAYER` | warning | 图层加载后要素数为 0 | `feature_count` |

## 3. 逐条说明

### 3.1 属性规则

**`SCHEMA_MISSING_FIELD`**

对 `schema` 中每个 `required: true` 字段检查。字段不存在、值为 `null`、值为纯空白字符串均视为缺失。同一要素多个必填字段缺失会产生多条 finding。可选字段缺失不报告。

**`SCHEMA_TYPE_MISMATCH`**

只在字段“存在且非缺失”时检查，包括可选字段。JSON 的布尔值不算数字；整数与浮点数区分 `integer`/`number`。CSV 无法转换的文本保留原文并在此规则中报告。证据中的 `value` 是截断到 80 字符的预览。

### 3.2 坐标和几何结构规则

**`COORD_NON_FINITE`**

遍历要素的所有坐标分量；任一 `NaN`/`Infinity` 产生一条要素级 finding，列出前 20 个坐标位置。出现非有限坐标后，该要素的环/线/重复/自相交/狭长规则会跳过，以避免派生噪声；属性规则仍执行。

**`COORD_OUT_OF_BOUNDS`**

仅在有限坐标上检查，使用全局 `crs.bounds` 的闭区间。越界点按要素聚合，最多列 20 个位置。该规则不使用容差扩展边界。

**`GEOM_UNSUPPORTED_TYPE`**

以下情况均报告：

- `geometry` 为 `null`；
- 几何对象没有字符串 `type`；
- 类型不是支持的五种二维几何；
- 类型合法但不在当前图层的 `geometry_types` 白名单。

报告后跳过该要素的几何结构检查，属性规则仍执行。`reason` 取 `null_or_missing_geometry_type`、`unsupported_geometry_type` 或 `outside_layer_whitelist` 之一。

**`RING_NOT_CLOSED`**

对多边形每个环检查首尾坐标是否在有效的 `coordinate_epsilon` 内相等。位置按要素聚合。

**`RING_TOO_FEW_POINTS`**

环坐标数少于 4 即报告。闭合环最少需要 3 个不同顶点加 1 个重复的闭合点。空环也会报告该规则，但不报告“未闭合”。

**`LINE_TOO_FEW_POINTS`**

LineString 或 MultiLineString 的任一组组成部分少于 2 个坐标。位置路径和实际点数进入证据。

**`DUPLICATE_CONSECUTIVE_VERTEX`**

若当前顶点与前一个顶点距离 `<= coordinate_epsilon`，记录当前顶点索引。该检查对线序列和多边形环分别进行，按要素聚合。

**`ZERO_LENGTH_SEGMENT`**

若线段起点与终点距离 `<= coordinate_epsilon`，记录线段起始索引。与重复顶点规则不同，它检查的是线段而不仅是顶点。

**`SELF_INTERSECTION`**

对有效长度线段两两检查：

- 跳过同一序列中的相邻线段；
- 对闭合线/环，额外跳过首尾相邻的一对线段；
- 相交、端点在另一线段上、共线重叠均算自相交；
- 零长度线段先被排除，避免虚假的相邻关系。

一条 finding 聚合该要素的多个位置和线段对。

### 3.3 身份和全局几何规则

**`DUPLICATE_FEATURE_ID`**

按图层内 ID 字段的字符串值分组；`null`、布尔、对象、数组不参与。每组第一个要素为基准，后续每个重复要素产生一条 finding，`related_feature_ids` 指向基准要素。比较前会去除 ID 两端空白。

**`DUPLICATE_GEOMETRY`**

将几何量化到 `coordinate_epsilon` 整数网格后构造规范签名：

- 连续重复点先去除；
- 线正向与反向视为相同；
- 环旋转起点和正反方向视为相同；
- MultiLineString 的成员、MultiPolygon 的成员、多边形孔的顺序被排序；
- 几何类型必须相同；
- 非有限或无法规范化的几何跳过。

每条重复几何 finding 的基准是源索引较小的要素，后续重复要素被报告。源坐标不会被修改。该规则不区分属性是否相同。

**`POLYGON_OVERLAP`**

只比较不同要素的有效多边形部件。有效多边形要求：非空、所有环至少 4 点且闭合、外环面积大于 `epsilon²`。流程：

1. 计算部件 bbox；
2. bbox 不相交则跳过；
3. 检查边界“真正穿越”；
4. 检查顶点严格包含于对方；
5. 检查质心严格包含。

仅共边、仅共点、边界接触不算内部重叠。bbox 相交但内部不重叠不会报告。每条 finding 关联两个要素，证据包含双方部件标签和 bbox 交。算法不计算压盖面积，也不检测跨图层压盖。

**`SLIVER_POLYGON`**

对每个有效多边形部件计算：

```text
area      = abs(外环有向面积) - Σ abs(孔有向面积)，下限 0
perimeter = 外环周长 + Σ 孔周长
ratio     = area / perimeter（周长为 0 时为 0）
```

`ratio < sliver_ratio` 时报告。面积、周长和比率在证据中按 9 位小数稳定舍入。该阈值是形态启发式，不表达法律或制图规范。

### 3.4 线端点规则

两条端点规则只考虑：

- 当前图层内、几何类型在图层白名单中的要素；
- 线组成部分至少 2 点且所有点有限；
- 不把首尾在 `coordinate_epsilon` 内相等的闭合线视为有端点。

**`DANGLING_ENDPOINT`**

对每个开放线端点，若没有**其他要素**的任一端点落在距离 `<= coordinate_epsilon` 内，则报告该端点。一个要素的两个端点可以都悬挂。只有单条开放线的图层，其所有端点都会被视为悬挂。

每条 finding 的 `related_feature_ids` 包含最近的其他要素端点所属 ID（不存在时为 `null`，关联列表为空）；证据中的 `nearest_feature_id` 和 `nearest_distance` 表示该最近端点及其距离，仅用于解释，不改变悬挂判定。该规则不识别端点连接到另一条线的内部顶点、多边形边界或另一图层的线。

**`NEAR_MISS_ENDPOINT`**

对不同要素的两个端点，若：

```text
coordinate_epsilon < distance <= near_miss_distance
```

则报告一条 `info`。它提示“接近但未精确连接”，与 `DANGLING_ENDPOINT` 可同时出现。证据保留两端要素、位置路径、端点和距离。

### 3.5 图层规则

**`LAYER_CRS_MISMATCH`**

仅当图层配置了 `declared_crs` 且规则启用时检查。比较全局 `crs` 的 `name`、`type` 与声明的 `name`、`type`，任一项不同即报告。它只报告声明冲突，不比较坐标数值，也不执行转换。

**`EMPTY_LAYER`**

图层成功加载但要素数为 0 时报告。空的 FeatureCollection 是合法输入，不是加载错误。空 CSV（只有表头）同样产生该 finding。

## 4. 演示 fixture 覆盖

`fixtures/demo` 的预期 finding 计数为 24，覆盖全部 19 条规则：

| 规则 | 计数 | 规则 | 计数 | 规则 | 计数 |
| --- | ---: | --- | ---: | --- | ---: |
| `SCHEMA_MISSING_FIELD` | 3 | `SCHEMA_TYPE_MISMATCH` | 2 | `COORD_NON_FINITE` | 1 |
| `COORD_OUT_OF_BOUNDS` | 1 | `GEOM_UNSUPPORTED_TYPE` | 1 | `RING_NOT_CLOSED` | 1 |
| `RING_TOO_FEW_POINTS` | 1 | `LINE_TOO_FEW_POINTS` | 1 | `DUPLICATE_CONSECUTIVE_VERTEX` | 1 |
| `ZERO_LENGTH_SEGMENT` | 1 | `SELF_INTERSECTION` | 1 | `DUPLICATE_FEATURE_ID` | 1 |
| `DUPLICATE_GEOMETRY` | 1 | `POLYGON_OVERLAP` | 1 | `SLIVER_POLYGON` | 1 |
| `DANGLING_ENDPOINT` | 3 | `NEAR_MISS_ENDPOINT` | 1 | `LAYER_CRS_MISMATCH` | 1 |
| `EMPTY_LAYER` | 1 |  |  |  |  |

按图层：`parcels=7`、`roads=11`、`points=5`、`empty=1`。按级别：`error=15`、`warning=8`、`info=1`。完整预期见 `fixtures/demo/expected_summary.json`。

## 5. 已知判定限制

- 所有检查是二维平面的，不进行大地测量或投影换算。
- 不执行完整平面图构建，不检查节点度数、连通分量、悬挂链或面网闭合环。
- 不检查跨图层拓扑、跨图层重复、跨图层压盖。
- 不检查 Z/M 值、坐标精度来源、拓扑规则库或 OGC 简单要素的全部合法性问题。
- 压盖是保守布尔判定，不计算面积；狭长是面积/周长启发式。
- 重复几何以配置容差量化，容差变化可能改变“重复”的范围和 finding ID。
- 规则禁用后不会隐式改变其他规则的判定；关闭规则应理解为“接受该类风险”，而不是“修复数据”。
