# 配置、输入与错误码

本文档描述 `topoaudit` 的配置 schema、默认值、GeoJSON/CSV 读取语义和稳定错误码。实现遵循严格模式：未知字段和非法值不会被静默忽略。

## 1. 顶层结构

配置文件必须是 UTF-8 JSON 对象，且只允许以下六个顶层键：

| 键 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | 是 | 字符串 | 当前只接受精确值 `"1.0"` |
| `run_label` | 是 | 非空字符串 | 运行标签，进入报告与台账 |
| `tolerance` | 是 | 对象 | 全局容差；对象本身必填，字段可省略取默认值 |
| `crs` | 是 | 对象 | 全局坐标参考系声明和坐标边界 |
| `layers` | 是 | 非空数组 | 至少一个图层，图层 ID 不得重复 |
| `source_generation` | 否 | 对象 | 上游原子发布清单策略；见第 2 节 |

任何未知顶层键都会产生 `TOPO-CFG-003`。重复键由 Python JSON 解析器按后者覆盖，不另行检测。

## 2. 上游发布清单 `source_generation`

跨图层拓扑审计只有在每个图层来自**同一上游发布代**时才有业务意义。`input_digest` 只证明“本次实际读到的内容”，不能证明同代：若上游在两次读取之间切换版本，可能得到一层 v1、一层 v2，而组合摘要仍然完整。

`source_generation` 允许上游提供一个原子发布的输入集清单，供本地离线核验：

```json
"source_generation": {
  "policy": "require",
  "manifest": "inputs.manifest.json"
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `policy` | 否 | `require` 或 `observe`；省略时：有 `manifest` 即 `require`，否则 `observe` |
| `manifest` | 否 | 清单相对路径（以配置文件目录为基准）；`policy=require` 时必填 |

- `require`：要求同代证明。任何缺层、额外成员、路径/格式/摘要不符、清单或图层在读取期间被替换、二次读取不一致，都会以 `TOPO-INP-012` 拒绝整个审计，不产生可放行结果。
- `observe`：继续执行观察式审计；即使提供清单且核验失败，也不会静默降级，而是记录 `status=failed` 且 `quality_gate=blocked`。
- 不提供 `source_generation`：旧式用法，`status=unverified`、`quality_gate=blocked`。结果只供观察，报告与台账明确标注“来源代未证、不可作同代质量放行”。

### 2.1 清单格式

```json
{
  "schema_version": "1.0",
  "generation_id": "demo-release-v1",
  "layers": [
    {"id": "alpha", "path": "alpha.geojson", "format": "geojson",
     "sha256": "…64 位小写十六进制…"}
  ]
}
```

- 清单自身必须是一个完整的 JSON 对象，`schema_version` 目前只接受 `"1.0"`。
- `generation_id` 是非空字符串，进入报告和台账。
- `layers` 是非空数组，每个成员必须提供 `id`、**相对路径** `path`、`format`（`geojson` 或 `csv`）和原始字节 `sha256`。
- 清单声明的图层集合必须与配置 `layers` 的 ID 集合完全一致；缺层或多出成员都判为失败。
- 图层路径以**清单文件所在目录**为基准解析；与配置解析后的路径不同即判为路径错配。

### 2.2 加载与核验顺序

1. 读取清单一次，对其**同一份字节**做 UTF-8 解码和 JSON 解析，同时计算原始字节 SHA-256 与规范化清单摘要；
2. 逐层读取输入字节并计算 SHA-256，随后用**这批同一字节**解码、解析；
3. 逐层比对：配置图层 ID 是否在清单中、路径、格式、原始字节 SHA-256；
4. 全部读取完成后，再次读取各输入文件和清单，只用于发现“解析后被原子替换”或“清单在读前读后变更”；二次读取结果不替代第 2 步的摘要；
5. 只有“清单读取一致 + 成员集合一致 + 每层路径/格式/摘要一致 + 每层二次读取一致”同时成立，才记为 `verified` 并允许质量放行。

仅比较读前/读后的文件 stat，或只比较每个文件自身前后哈希是否稳定，**不足以**证明同代：两个文件可以各自稳定地分别属于 v1 和 v2。

### 2.3 结论状态

| `status` | `quality_gate` | 含义 |
| --- | --- | --- |
| `verified` | `allowed` | 各图层字节与清单逐一匹配，同代已证 |
| `failed` | `blocked` | 提供了清单但核验失败；不得作同代结论 |
| `unverified` | `blocked` | 未提供清单；来源代未证，结果只供观察 |

报告 JSON、Markdown、HTML、`inputs.json`、终端摘要、SQLite 台账和 `verify` 输出都携带同一组状态、发布代和每层核对结果。

## 3. 容差 `tolerance`

| 字段 | 默认值 | 约束 | 含义 |
| --- | ---: | --- | --- |
| `coordinate_epsilon` | `1e-9` | 有限数字，`> 0` | 坐标相等、顶点重复、线段零长度、环闭合、规范几何量化的容差 |
| `near_miss_distance` | `1.0` | 有限数字，`>= 0` | 不同线端点之间“近接”提示的距离上限，坐标单位 |
| `sliver_ratio` | `0.1` | 有限数字，`> 0` | 面积/周长比低于该值的有效多边形报告为狭长多边形 |

`tolerance` 对象内不允许未知键。数字不能是布尔值、`NaN` 或 `Infinity`。

各图层可在 `thresholds` 中覆盖这三个字段：

```json
"thresholds": {
  "coordinate_epsilon": 0.000001,
  "near_miss_distance": 2.0,
  "sliver_ratio": 0.12
}
```

距离类阈值始终以**输入坐标单位**解释。`crs.type=geographic` 不会自动把度换算为米。

## 4. 坐标参考系 `crs`

```json
{
  "name": "LOCAL_XY",
  "type": "planar",
  "bounds": {"min_x": -100, "min_y": -100, "max_x": 1000, "max_y": 1000}
}
```

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `name` | 是 | 非空字符串 |
| `type` | 是 | 只能是 `planar` 或 `geographic` |
| `bounds.min_x/min_y/max_x/max_y` | 是 | 四个有限数字，且 `min_x < max_x`、`min_y < max_y` |

`type=geographic` 时还要求：

```text
-180 <= min_x <= max_x <= 180
-90  <= min_y <= max_y <= 90
```

该工具**不做投影、重投影或基准面转换**。`geographic` 仅用于边界合理性校验；全部几何运算仍按二维平面坐标处理。

## 5. 图层 `layers`

每个图层只允许以下键：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 图层唯一 ID，不能重复，不能为空 |
| `path` | 是 | 本地输入文件路径 |
| `format` | 是 | `geojson` 或 `csv` |
| `geometry_types` | 是 | 非空、无重复的几何类型白名单 |
| `id_field` | 是 | 要素 ID 所在属性字段 |
| `schema` | 是 | 属性字段规格对象；可为空对象 `{}` |
| `rules` | 否 | 规则开关对象；省略时全部启用 |
| `thresholds` | 否 | 本图层容差覆盖 |
| `declared_crs` | 否 | 图层声明的 CRS，只含 `name` 和 `type` |

### 5.1 路径解析

- 相对路径以**配置文件所在目录**为基准。
- 绝对路径直接使用。
- 路径中的 `..` 和符号链接按本地文件系统语义解析；工具不提供文件系统沙箱。
- 输入必须是可读取的普通文件，且只以只读方式打开。
- 不识别、不接受远程 URL、数据库连接串或对象存储 URI。

### 5.2 几何类型

`geometry_types` 支持以下值：

```text
Point
LineString
MultiLineString
Polygon
MultiPolygon
```

`format=csv` 时，`geometry_types` 必须精确为 `["Point"]`。要素实际类型不在白名单、几何为空或类型未知时，不会中止加载，而会由 `GEOM_UNSUPPORTED_TYPE` 规则报告。

### 5.3 属性 schema

```json
"schema": {
  "parcel_id": {"type": "string", "required": true},
  "zone": {"type": "string", "required": true},
  "area_hint": {"type": "number", "required": false}
}
```

字段规格只允许 `type` 和 `required`：

- `type` 必填，取值：`string`、`number`、`integer`、`boolean`、`object`、`array`。
- `required` 可选，默认 `true`，必须是布尔值。
- `number` 接受 JSON 整数和浮点数，但拒绝布尔值。
- `integer` 只接受 JSON 整数，拒绝布尔值和 `1.0` 这类浮点值。
- 字段缺失、值为 `null`、值为纯空白字符串，都视为“缺失”。
- 必填字段缺失产生 `SCHEMA_MISSING_FIELD`；已存在但类型不符产生 `SCHEMA_TYPE_MISMATCH`，包括可选字段。
- 同一要素的多个异常字段会分别产生 finding。
- finding 证据中的属性值预览最多 80 个字符，超出部分以 `…` 截断。

### 5.4 规则开关

```json
"rules": {
  "SLIVER_POLYGON": {"enabled": false},
  "NEAR_MISS_ENDPOINT": {"enabled": false}
}
```

- 键必须是 19 条已知规则之一，否则 `TOPO-CFG-007`。
- 值必须是只含 `enabled` 布尔字段的对象。
- 未出现的规则默认启用。
- 只能关闭规则，不能修改规则严重级别，也不能添加新规则。

### 5.5 图层声明的 CRS

```json
"declared_crs": {"name": "LOCAL_XY", "type": "planar"}
```

该对象只允许 `name` 和 `type`。当 `name` 或 `type` 与全局 `crs` 不完全一致时，产生图层级 `LAYER_CRS_MISMATCH`。该规则不执行坐标变换。

## 6. GeoJSON 输入

支持根节点为 `FeatureCollection` 的标准二维子集：

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"parcel_id": "P-001", "zone": "A"},
      "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
    }
  ]
}
```

读取规则：

- 文件按 UTF-8 解码，允许 UTF-8 BOM。
- 根节点必须是对象且 `type=FeatureCollection`；`features` 必须是数组。
- 每个要素必须是对象且 `type=Feature`；`properties` 必须是对象或 `null`（按空对象处理）。
- 几何为 `null` 时保留为不支持几何，由规则报告。
- 坐标位置必须是恰好两个数字的数组；布尔值不算数字。
- Point、LineString、MultiLineString、Polygon、MultiPolygon 的结构必须符合各自嵌套层级；非法结构是输入错误，不是 finding。
- 未知几何类型、缺失几何类型或不在白名单内的类型会保留用于 `GEOM_UNSUPPORTED_TYPE`，不会丢弃要素。
- Python 标准 JSON 解析器接受 `NaN`/`Infinity` 字面量；这些坐标会进入审计并由 `COORD_NON_FINITE` 报告。它们不是可移植 JSON，建议输入侧避免。

## 7. CSV 输入

CSV 只用于点要素。要求：

- UTF-8 文本，允许 BOM。
- 必须有表头，表头列名不得重复。
- 必须包含 `x`、`y`、`id` 以及配置中的 `id_field` 列；`id_field` 为 `id` 时三者即 `id/x/y`。
- 每个数据行的 `x`、`y` 必须可转换为浮点数；转换失败是输入错误。
- `x`、`y` 本身不进入属性对象；其他列进入属性对象。
- 空单元格保留为空字符串；如果字段必填，会触发 `SCHEMA_MISSING_FIELD`。
- 属性值按 schema 尝试转换：
  - `string`：保留原文。
  - `integer`：仅当文本匹配 `^[+-]?\d+$` 时转整数，否则保留原文。
  - `number`：尝试转浮点数，失败保留原文。
  - `boolean`：仅 `true`/`false`（不区分大小写）转换，其他保留原文。
  - `object` / `array`：尝试解析 JSON 且检查根类型，失败保留原文。
- 转换失败不会丢弃字段，而会由类型规则报告，从而保留证据。
- 如果 `id_field` 值缺失、为空或不是标量，内部 ID 使用 `<missing:行索引>`；这不是一条独立规则，但会影响重复 ID、重复几何和端点证据中的标识。

## 8. 规范化和只读保证

配置加载和输入读取不会修改源文件。运行时会：

1. 计算配置的规范摘要。
2. 若声明了发布清单，读取清单一次并对其同一份字节解析和计算 SHA-256。
3. 逐个读取输入文件原始字节，计算 SHA-256，并用同一批字节解析。
4. 将每层字节摘要与清单逐一绑定，并在读取完成后复核清单与各层文件是否被替换。
5. 在内存中建立冻结的要素模型。
6. 在写任何报告前，拒绝输出路径与配置文件、发布清单或任一输入文件路径冲突的目标。

注意：输出文件名固定。如果 `--out` 指向一个目录，且该目录中存在同名 `report.json` 等文件，旧报告会被更新；这是输出覆盖，不是输入修改。需要保留旧输出时使用新目录。

## 9. 配置摘要

`config_digest` 基于规范化后的公开配置计算，包括：

- schema 版本、运行标签；
- 全局容差及其默认值；
- CRS 和边界；
- 图层顺序、图层 ID、配置中的原始路径、格式、类型白名单、ID 字段、schema、规则开关、阈值覆盖、声明的 CRS。

它不包含：

- 配置文件的绝对路径；
- 输入文件内容；
- 输出目录或当前时间。

`source_generation` 的策略与清单配置路径也进入 `config_digest`（清单内容不进入）；清单内容身份由报告中的 `manifest_sha256` 与 `manifest_digest` 表达。

因此，JSON 排版不同但解析后公开配置相同，会得到相同 `config_digest`。输入内容变化则由 `input_digest` 捕获。

## 10. 稳定错误码

预期错误通过 CLI 打印到 `stderr`，带 `[错误码]` 和可能的路径定位；进程退出码为 `2`（`verify` 不一致为 `1`）。

### 10.1 配置 `TOPO-CFG-*`

| 错误码 | 含义 |
| --- | --- |
| `TOPO-CFG-001` | 配置文件不存在、不是普通文件或无法读取 |
| `TOPO-CFG-002` | 配置不是合法 JSON（附 line/column） |
| `TOPO-CFG-003` | 未知配置字段 |
| `TOPO-CFG-004` | 缺少必填字段或 JSON 类型错误 |
| `TOPO-CFG-006` | 字段值非法：空串、空数组、重复元素、枚举不符、非有限数字、范围错误、数值关系错误等 |
| `TOPO-CFG-007` | 未知规则 ID |
| `TOPO-CFG-008` | 图层 ID 重复 |
| `TOPO-CFG-009` | `schema_version` 不受支持 |
| `TOPO-CFG-010` | `source_generation` 对象、策略或清单路径非法 |

路径定位示例：

```text
[TOPO-CFG-004] 缺少必填字段: schema @ $.layers[0].schema
[TOPO-CFG-006] coordinate_epsilon 必须大于 0 @ $.tolerance.coordinate_epsilon
[TOPO-CFG-007] 未知规则 ID: NOT_A_RULE @ $.layers[0].rules.NOT_A_RULE
```

### 10.2 输入 `TOPO-INP-*`

| 错误码 | 含义 |
| --- | --- |
| `TOPO-INP-001` | 输入文件不存在或不是普通文件 |
| `TOPO-INP-002` | 输入文件读取失败 |
| `TOPO-INP-003` | 输入不是合法 UTF-8 文本 |
| `TOPO-INP-004` | 内部不支持的输入格式（配置校验通常会先阻止） |
| `TOPO-INP-005` | GeoJSON 解析失败或 FeatureCollection/Feature 结构错误 |
| `TOPO-INP-006` | 坐标/几何嵌套结构错误，或坐标不是二维数字数组 |
| `TOPO-INP-007` | CSV 表头缺失、重复或必需列缺失 |
| `TOPO-INP-008` | CSV 行解析失败，或 `x`/`y` 不是数值 |
| `TOPO-INP-009` | 发布清单不存在或不是普通文件 |
| `TOPO-INP-010` | 发布清单读取失败 |
| `TOPO-INP-011` | 发布清单不是合法 UTF-8 JSON，或结构/字段非法 |
| `TOPO-INP-012` | `policy=require` 要求同代证明，但核验未通过 |

### 10.3 报告 `TOPO-RPT-*`

| 错误码 | 含义 |
| --- | --- |
| `TOPO-RPT-001` | 报告文件不存在 |
| `TOPO-RPT-002` | 报告无法读取或不是合法 JSON |
| `TOPO-RPT-003` | 报告根节点不是 JSON 对象 |
| `TOPO-RPT-004` | 输出目标与配置/输入文件冲突，拒绝覆盖 |

### 10.4 台账 `TOPO-LED-*`

| 错误码 | 含义 |
| --- | --- |
| `TOPO-LED-001` | 台账文件不存在或无法只读打开 |
| `TOPO-LED-002` | 无法打开 SQLite 台账 |
| `TOPO-LED-003` | 报告缺少有效 `result_digest`，或摘要与内容不一致，拒绝写入 |
| `TOPO-LED-004` | 写入台账失败 |
| `TOPO-LED-005` | 读取台账失败 |
| `TOPO-LED-006` | `query --digest` 指定的运行不存在 |

### 10.5 本地服务 `TOPO-SRV-*`

| 错误码 | 含义 |
| --- | --- |
| `TOPO-SRV-001` | 尝试绑定非 `127.0.0.1`/`localhost` 地址 |
| `TOPO-SRV-002` | 端口不是合法整数或超出范围 |
| `TOPO-SRV-003` | 台账文件不存在 |
| `TOPO-SRV-004` | 台账无法读取或结构不可用 |
| `TOPO-SRV-005` | 地址已被占用或无法绑定 |

### 10.6 演示和其他

| 错误码 | 含义 |
| --- | --- |
| `TOPO-DEMO-001` | 演示输出目录位于 `fixtures/demo` 内，拒绝写入 |
| `TOPO-DEMO-002` | 内置演示配置缺失 |
| `TOPO-DEMO-003` | 预期摘要缺失或结构错误 |
| `TOPO-DEMO-004` | 演示结果与 `expected_summary.json` 的某个计数不一致 |
| `TOPO-ERR-000` | 未细分的基类错误码 |
| `TOPO-IO-001` | CLI 捕获到未结构化的本地 I/O 错误 |

所有 JSON 路径定位均使用 `$` 表示配置根，例如 `$.layers[2].thresholds.sliver_ratio`。
