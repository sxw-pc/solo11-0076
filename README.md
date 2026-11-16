# 矢量拓扑验台（topoaudit）

一个**完全本地、离线、确定性**的二维矢量数据几何与拓扑质量审计工具。它读取本地 GeoJSON / CSV，按严格配置执行 19 条核心规则，并可按需启用正交覆盖空缺审计，输出 JSON、Markdown、自包含 HTML 报告和 SQLite 审计台账，并提供只监听回环地址的只读复核页面。

- 项目 slug：`topoaudit`
- Python：3.10+
- 依赖：仅 Python 标准库
- 数据坐标：合成 fixture 使用虚构的 `LOCAL_XY` 平面坐标
- 默认本地服务：`127.0.0.1:8765`

## 1. 定位与边界

**它适合：**

- 在数据交付、入库或制图前，对本地矢量数据做离线质量卡点。
- 用固定配置、固定输入得到可比较的 finding 集合和摘要。
- 通过 SHA-256 摘要、报告和 SQLite 台账留痕，复核一次审计是否可重复。
- 在完全断网的机器上运行，不依赖 CDN、云 API、远程数据库或第三方 Python 包。

**它不是：**

- 不是地图渲染器、切片服务或 GIS 桌面软件。
- 不做坐标参考系转换、投影计算或基准面变换。
- 不自动修复、合并、删除或写回源数据。
- 不连接 OGC/WFS、远程数据库、对象存储或云服务。
- 不处理栅格、三维或分布式调度。
- 不是多用户、带认证的联网服务，也不提供“数据必然合规”的结论。

## 2. 快速开始

在仓库根目录直接运行，无需安装第三方依赖。

```bash
# 1) 运行内置合成演示
python -m topoaudit demo --out build/demo

# 2) 对任意本地配置执行审计
python -m topoaudit run --config fixtures/demo/config.json --out build/run

# 3) 交叉核对 report.json 与 audit.sqlite
python -m topoaudit verify \
  --report build/demo/report.json \
  --ledger build/demo/audit.sqlite

# 4) 查询台账
python -m topoaudit query --ledger build/demo/audit.sqlite
python -m topoaudit query --ledger build/demo/audit.sqlite \
  --digest <result_digest> --severity error --json

# 5) 启动只读本地复核服务
python -m topoaudit serve --ledger build/demo/audit.sqlite \
  --host 127.0.0.1 --port 8765

# 6) 运行全部测试
python -m unittest discover -s tests -v
```

演示会核对 `fixtures/demo/expected_summary.json`，并确保演示输出不写入 fixture 目录。当前 fixture 的参考值：

```text
layers / features : 4 / 33
findings          : 24（error=15, warning=8, info=1）
覆盖规则           : 19 / 19（核心规则；覆盖空缺为 opt-in）
input_digest      : e3ea9103ea9b01639897200ce167a64d17822b1ac6c796733cd5e49524246b25
result_digest     : a6571ddbafc802a88215fd64281cf43f9a70120535e3366e5862e16a38509ce1
```

`result_digest` 是 fixture、配置和规则实现的联合指纹。修改任何一项后，应以新生成的报告和 `verify` 结果为准。

## 3. 输出物

| 文件 | 用途 |
| --- | --- |
| `report.json` | 确定性机器报告：摘要、图层统计、规则统计、findings、输入摘要、限制说明 |
| `report.md` | 人工评审用 Markdown 报告 |
| `report.html` | 完全自包含、无脚本、无外部资源的 HTML 报告 |
| `audit.sqlite` | 运行、图层、findings、输入文件、规则目录快照台账 |
| `inputs.json` | 本次运行的 config/input/result 摘要与文件级 SHA-256 |
| `summary.txt` | 终端摘要的文本副本 |

台账以 `result_digest` 为运行主键。相同报告重复写入不会产生重复的 run、layer 或 finding；不同摘要会成为新运行。报告中的 `result_digest` 在写台账前会被重新计算，不一致时拒绝写入。

一条 finding 的形态（摘自演示报告）：

```json
{
  "finding_id": "7347d5cbd43cce7d",
  "rule_id": "POLYGON_OVERLAP",
  "severity": "error",
  "layer_id": "parcels",
  "feature_id": "P-007",
  "related_feature_ids": ["P-008"],
  "evidence": {
    "pair": ["P-007", "P-008"],
    "component_indexes": ["polygons[0]", "polygons[0]"],
    "bbox_intersection": [220.0, 220.0, 240.0, 240.0]
  }
}
```

报告顶层的 `summary` 提供总数、级别、按规则和按图层计数；`layers` 提供每层要素/几何类型/finding/bbox；`rules` 给出全部规则及计数；`findings` 是已排序明细；`limitations` 固定记录能力边界。

## 4. 配置和输入

完整字段、默认值和错误码见 [`docs/CONFIG.md`](docs/CONFIG.md)。最小结构如下：

```json
{
  "schema_version": "1.0",
  "run_label": "local-check-001",
  "tolerance": {
    "coordinate_epsilon": 0.000001,
    "near_miss_distance": 2.0,
    "sliver_ratio": 0.12
  },
  "crs": {
    "name": "LOCAL_XY",
    "type": "planar",
    "bounds": {"min_x": -100, "min_y": -100, "max_x": 1000, "max_y": 1000}
  },
  "layers": [
    {
      "id": "parcels",
      "path": "parcels.geojson",
      "format": "geojson",
      "geometry_types": ["Polygon", "MultiPolygon"],
      "id_field": "parcel_id",
      "schema": {
        "parcel_id": {"type": "string", "required": true},
        "zone": {"type": "string", "required": true}
      }
    }
  ]
}
```

要点：

- 配置采用**严格模式**：未知字段、未知规则、重复图层 ID、非法默认值都会直接报错，不会静默忽略。
- 图层 `path` 相对配置文件所在目录解析；`..`、符号链接和绝对路径按本地文件系统语义处理，不提供文件系统沙箱。
- GeoJSON 必须是 `FeatureCollection`；支持 Point、LineString、MultiLineString、Polygon、MultiPolygon。
- CSV 只用于 Point 图层，必须有 `id`、`x`、`y` 及配置中的 `id_field` 列。
- 每条规则可在图层级用 `{"enabled": false}` 关闭；规则严重级别固定，配置不能修改。
- 容差可在全局设置，也可在图层 `thresholds` 中覆盖。

## 5. 规则与严重级别

19 条核心规则及 3 条 opt-in 覆盖规则的触发条件、证据字段和关闭方式见 [`docs/RULES.md`](docs/RULES.md)。

| 级别 | 含义 |
| --- | --- |
| `error` | 数据无法可靠使用或身份/结构明显错误，例如非有限坐标、环未闭合、无效几何、重复 ID、自相交、多边形压盖、CRS 声明冲突 |
| `warning` | 可疑质量信号，例如重复顶点、零长度线段、狭长多边形、重复几何、悬挂端点、空图层 |
| `info` | 提示性观察，例如端点近接 |

规则组：

- 属性与身份：`SCHEMA_MISSING_FIELD`、`SCHEMA_TYPE_MISMATCH`、`DUPLICATE_FEATURE_ID`。
- 坐标与几何结构：`COORD_NON_FINITE`、`COORD_OUT_OF_BOUNDS`、`GEOM_UNSUPPORTED_TYPE`、`RING_NOT_CLOSED`、`RING_TOO_FEW_POINTS`、`LINE_TOO_FEW_POINTS`、`DUPLICATE_CONSECUTIVE_VERTEX`、`ZERO_LENGTH_SEGMENT`、`SELF_INTERSECTION`。
- 拓扑与形态：`DUPLICATE_GEOMETRY`、`POLYGON_OVERLAP`、`SLIVER_POLYGON`、`DANGLING_ENDPOINT`、`NEAR_MISS_ENDPOINT`、`LAYER_CRS_MISMATCH`、`EMPTY_LAYER`。

## 5.1 覆盖空缺审计（opt-in）

在配置顶层加入 `coverage` 块后，工具会额外计算：AOI 内未被被审计地块**并集**覆盖的连通区域、面积与边界证据。它使用与其余规则相同的平面坐标语义，只读源要素，绝不写回。

```json
"coverage": {
  "layer_id": "parcels",
  "mode": "internal_only",
  "aoi": {"type": "Polygon", "coordinates": [[[0,0],[10,0],[10,10],[0,10],[0,0]]]}
}
```

- **只接受正交面**：AOI 与地块的环必须闭合、有限、边与坐标轴平行，洞合法且不互相嵌套。斜边、自交、无效洞会被阻断为 `indeterminate`，绝不用 bbox 冒充面积。
- **并集补集**：先取所有地块的并集，再与 AOI 求差；重叠地块不重复计覆盖，也不能用压盖面积抵消另一处缺口。相切共边、共点和零面积线缝不产生正面积缺口。
- **洞语义**：AOI 自身的洞不算缺口；地块的洞若无人覆盖，则是缺口。
- **两种验收范围**：`internal_only` 只把完全被包围的内部空洞列为必须整改，边界条带单列为待补项；`all_uncovered` 把两者都列为必须整改。两种模式的未覆盖总面积和组件几何相同，但必须整改的组件数与面积不同。
- **可追溯**：每个组件带编号、bbox、网格单元、相邻地块 ID；文档带 AOI 几何摘要、覆盖输入摘要和参与计算的地块清单（来源索引与部件）。
- **报告与台账**：`coverage` 区块写入 `report.json`；Markdown/HTML 有独立章节；SQLite 新增 `coverage` 表；`verify` 会核对报告与台账的覆盖文档是否一致。
- **规则**：`COVERAGE_GAP_INTERNAL`（error）、`COVERAGE_GAP_BOUNDARY`（warning）、`COVERAGE_INDETERMINATE`（error）仅在配置了 `coverage` 时参与；原有 `POLYGON_OVERLAP` 继续独立存在。

## 6. 退出码和 findings 语义

| 退出码 | 场景 |
| --- | --- |
| `0` | 命令成功完成，或 `verify` 核对一致 |
| `1` | `verify` 发现报告与台账不一致 |
| `2` | 参数用法错误、配置/输入/报告/台账/服务启动等结构化错误 |

`run` 返回 `0` 只表示审计流程成功执行，**不表示数据没有质量问题**。质量结论由 findings 和严重级别表达。所有预期错误输出稳定错误码和 JSON 路径定位，例如：

```text
[TOPO-CFG-003] 未知配置字段: surprise @ $.surprise
```

## 7. 确定性、摘要和可审计性

详见 [`docs/DETERMINISM.md`](docs/DETERMINISM.md)。

- `config_digest`：规范化配置内容的 SHA-256。
- `input_digest`：配置摘要加各输入文件原始字节 SHA-256 的规范摘要，按图层 ID 排序。
- `result_digest`：规范化报告内容（不含 `result_digest` 字段自身）的 SHA-256。
- `finding_id`：finding 规范载荷 SHA-256 的前 16 位十六进制字符。
- findings 采用固定的严重级别、规则 ID、图层、要素 ID 排序，不依赖集合遍历顺序。
- 同一 Python 运行环境、同一配置内容和同一输入文件字节应得到相同的 `result_digest` 和台账主键。
- SQLite 写入按 `result_digest` 幂等；`verify` 会重新计算摘要并逐项核对 run、图层、findings、输入文件和规则目录快照。

摘要用于完整性比对，不是数字签名，也不提供可信时间戳或来源认证。

## 8. 离线与安全边界

详见 [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md)。

- 运行期不发起外部网络请求，不使用 CDN、云 API、远程数据库或遥测。
- HTTP 服务只允许绑定 `127.0.0.1` 或 `localhost`，默认端口 `8765`，拒绝 `0.0.0.0`。
- 服务只提供 `GET`/`HEAD`，路由只接受固定页面和 64 位十六进制 `result_digest`，不提供任意文件读取或上传。
- 页面和报告内联样式，无脚本、无外部字体/图片；响应带有严格 CSP、`nosniff`、`no-referrer`、`no-store`。
- SQLite 复核查询使用只读连接和 `PRAGMA query_only`。
- 输入文件只以只读方式读取。`run`/`demo` 在写报告前拒绝与配置或输入文件路径冲突的输出目标；`demo` 额外拒绝把输出写入 `fixtures/demo`。
- 输出目录由调用者显式指定。已有同名报告会被更新；需要保留旧结果时请使用新的 `--out` 目录。

复核服务没有认证、用户隔离或公网加固，只应在可信本机、受控网络和授权数据上使用。

## 9. 项目结构

```text
.
├── README.md
├── pyproject.toml
├── docs/
│   ├── CONFIG.md
│   ├── RULES.md
│   ├── DETERMINISM.md
│   └── COMPLIANCE.md
├── fixtures/demo/
│   ├── config.json
│   ├── parcels.geojson
│   ├── roads.geojson
│   ├── points.csv
│   ├── empty.geojson
│   └── expected_summary.json
├── topoaudit/
│   ├── __init__.py        # 版本与 schema 版本
│   ├── __main__.py        # python -m topoaudit 入口
│   ├── cli.py             # run/demo/verify/serve/query
│   ├── config.py          # 严格配置加载与 config_digest
│   ├── config_schema.py   # 嵌套配置校验
│   ├── errors.py          # 稳定错误码
│   ├── model.py           # 冻结数据模型
│   ├── loaders.py         # GeoJSON / CSV 只读加载
│   ├── geometry_core.py   # 几何谓词
│   ├── geometry.py        # 面积、环、包含、压盖视图
│   ├── normalize.py       # 规范几何签名
│   ├── rules_catalog.py   # 规则目录和固定级别
│   ├── coverage.py        # opt-in 覆盖空缺审计入口
│   ├── coverage_grid.py   # 正交并集差集与连通组件
│   ├── coverage_document.py # 覆盖结果文档与摘要
│   ├── rule_coverage.py   # 覆盖审计 findings 投影
│   ├── findings.py        # finding 构造、去重、排序
│   ├── rule_schema.py     # 属性规则
│   ├── rule_geometry.py   # 坐标与几何规则
│   ├── rule_topology.py   # 身份、压盖、端点规则
│   ├── rules.py           # 规则编排
│   ├── engine.py          # 端到端审计结果
│   ├── report_json.py     # 确定性 JSON 与 result_digest
│   ├── report_md.py       # Markdown 报告
│   ├── report_html.py     # 自包含 HTML 报告
│   ├── ledger.py          # SQLite 幂等写入
│   ├── ledger_query.py    # 只读查询
│   ├── ledger_verify.py   # 报告/台账交叉核对
│   ├── pipeline.py        # 输出编排与输入覆盖保护
│   ├── demo.py            # 合成演示与预期计数核对
│   ├── server.py          # 回环只读 HTTP 服务
│   └── server_pages.py    # 自包含复核页面
└── tests/                 # 7 个 unittest 文件
```

`fixtures/demo` 全部为虚构合成数据，不包含真实公司、域名、地址、凭据或个人数据。

## 10. 测试与验收

测试覆盖：

- 几何谓词、包含关系（含孔洞）、压盖和规范化。
- GeoJSON/CSV 加载、文件摘要、非法输入、严格配置错误。
- 19 条核心规则及 opt-in 覆盖规则的触发、计数、稳定 ID、顺序无关性、规则开关和严重级别。
- 覆盖空缺审计：并集补集、洞与压盖不混算、两种验收模式、MultiPolygon、相切共边、斜边/自交阻断、报告/台账一致性。
- JSON/Markdown/HTML 报告的确定性、自包含与转义。
- SQLite 表结构、幂等写入、过滤查询、报告/台账篡改检测。
- 本机 HTTP 服务的健康检查、页面、JSON 过滤、路径穿越/未知路由拒绝、方法限制和回环绑定。
- CLI demo 端到端、fixture 哈希不变、重复运行一致、输出冲突保护、篡改后 `verify` 返回 1。

```bash
python -m unittest discover -s tests -v
```

工程约束：全部 `.py` 文件均为标准库实现，单文件不超过 300 行，生产代码与测试合计在 2500–4500 行之间。

## 11. 已知限制

- 仅支持二维欧氏几何；`crs.type=geographic` 只做经纬度边界校验，不做投影或距离换算。
- 拓扑检查以单图层为边界，不建立跨图层的节点—边—面拓扑；覆盖空缺面积仅在显式配置 `coverage` 时按单图层正交几何计算。
- 端点规则只比较线要素端点，不把多边形边界、线内部顶点或另一图层的端点视为连接。
- 压盖检测先用 bbox 预筛，再做确定性布尔拓扑判断；只报告内部重叠，共边或共点不算压盖。
- 自相交和近接判定依赖配置容差；改变容差可能改变 findings 和摘要。
- 大图层两两比较为本地小团队卡点设计，不面向超大数据集或分布式计算。
- 报告和台账是本地文件，摘要不具备签名、访问控制或防篡改硬件保障。
- 不自动修复数据，不对 CRS、精度、法定合规性作最终认定。

## 12. 文档索引

- [`docs/CONFIG.md`](docs/CONFIG.md)：配置 schema、默认值、输入格式、错误码。
- [`docs/RULES.md`](docs/RULES.md)：核心与覆盖规则的语义、证据、示例覆盖和限制。
- [`docs/DETERMINISM.md`](docs/DETERMINISM.md)：摘要链、排序、幂等和复现方法。
- [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md)：离线、只读、数据边界和安全合规说明。
