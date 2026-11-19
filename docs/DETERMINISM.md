# 确定性与可复现性

本文档说明 `topoaudit` 如何构造摘要、排序 findings、规范化几何，以及 SQLite 台账为何能按运行幂等。

## 1. 复现承诺的范围

在**相同 Python 运行环境、相同配置内容、相同输入文件字节**下：

- 同一份 JSON 报告内容（含 `result_digest`）应完全一致；
- Markdown 和 HTML 由该报告纯函数式渲染，也应完全一致；
- 相同 `result_digest` 重复写入同一台账不会产生重复行；
- `verify` 应返回成功。

不把输出目录、运行时间、主机名、用户名、进程号或随机数写入报告。报告不包含时间戳。

该承诺不覆盖：

- 输入文件或配置内容本身发生变化；
- 图层顺序、要素源顺序或容差发生变化；
- 代码/规则实现变化；
- 在不同浮点实现或极端数值环境下产生的末位差异。

如果需要在异构机器间做逐位审计，建议固定 Python 版本与运行平台，并把 `result_digest` 作为该环境的金标准。

## 2. 规范 JSON

所有摘要使用同一个规范化 JSON 函数：

```text
ensure_ascii=False
sort_keys=True
separators=(",", ":")
allow_nan=False
```

此外：

- 对象键按字符串排序；
- 列表/元组保持原有顺序；
- 负零 `-0.0` 归一为 `0.0`；
- 非有限浮点数不能进入摘要，违规会抛错，而不是写入不稳定的 `NaN`；
- 路径对象在序列化时使用 POSIX 分隔符。

JSON 报告文件使用 `sort_keys=True`、`indent=2` 的稳定美化格式，并带末尾换行。键顺序和缩进变化不进入摘要；摘要基于解析后的值。

## 3. 摘要链

### 3.1 `config_digest`

```text
config_digest = SHA-256(canonical_json(public_config))
```

`public_config` 包含：

- `schema_version`、`run_label`；
- 全局容差（含默认值）；
- CRS 名称、类型、边界；
- 按配置顺序排列的图层列表，每层包括：
  - 图层 ID、**配置中的原始路径**、格式；
  - 几何类型白名单、ID 字段；
  - 排序后的 schema；
  - 排序后的规则开关；
  - 排序后的阈值覆盖；
  - 可选的 `declared_crs`。

它**不**包含配置文件绝对路径、绝对解析路径、输入内容或输出目录。因此：

- 更改 JSON 缩进、键顺序或换行，不会改变 `config_digest`；
- 更改配置值、图层顺序、规则开关或原始路径字符串，会改变 `config_digest`；
- 把同一路径写成不同字符串（如 `a.geojson` 与 `./a.geojson`），通常会产生不同摘要，即使解析到同一文件。

### 3.2 `input_digest`

```text
input_digest = SHA-256(canonical_json({
  "config_digest": config_digest,
  "files": [
    {"layer_id": ..., "format": ..., "sha256": ...},
    ...
  ]  # 按 layer_id 排序
}))
```

每个文件的 `sha256` 是输入文件**原始字节**的 SHA-256。注意：

- 即使改动只影响格式化或空白，文件字节变化也会改变 `input_digest`；
- CSV 重新编码（即使文本语义相同）也会改变摘要；
- `input_digest` 同时绑定配置摘要和所有输入文件摘要。

### 3.3 `result_digest`

```text
result_digest = SHA-256(canonical_json(report_without_result_digest))
```

报告载荷包括：

- `schema_version`、`run_label`、`config_digest`、`input_digest`；
- `summary`（图层/要素/finding 计数、级别计数、按规则/图层计数、输入字节数等）；
- 图层统计（要素数、几何类型计数、finding 数、级别计数、稳定 bbox）；
- 规则快照（规则 ID、级别、标题、计数）；
- 完整 findings 列表（含证据）；
- 输入文件摘要；
- 限制说明。

因此，改变任何 finding 内容、计数、图层统计、容差证据或输入摘要，都会改变 `result_digest`。`result_digest` 不包含自身，避免递归。

### 3.4 `finding_id`

```text
finding_id = first_16_hex(SHA-256(canonical_json({
  "rule_id": ...,
  "severity": ...,
  "layer_id": ...,
  "feature_id": ...,
  "related_feature_ids": [排序去重后的列表],
  "evidence": {...}
})))
```

性质：

- 同一语义 finding 在同一图层/要素/证据下得到相同 ID；
- 只取前 16 个十六进制字符，展示与索引方便，但不作为密码学唯一标识；
- 不包含输入摘要或运行标签，因此不同运行中相同 finding 的 ID 可相同；
- 如果证据包含源索引，要素源顺序变化会间接改变 ID。

finding 收集器按 `finding_id` 去重，避免同一检查重复添加完全相同的结果。

## 4. 数值规范化

- 图层 bbox 和部分几何证据使用 `stable_round(value, 9)`，固定 9 位小数并把结果负零归一。
- bbox 计算忽略非有限坐标；没有任何有限坐标时 `bbox=null`。
- 非有限坐标不参与 `stable_round`，而是在 `COORD_NON_FINITE` 证据中格式化为显式文本。
- 重复几何的规范签名把坐标除以 `coordinate_epsilon` 后量化到整数网格，再构造序列。
- 规范几何只用于比较，不回写 `Feature`；报告和台账展示的仍是原始属性的文本/结构化证据。

量化使用 Python 的浮点除法和 `round`。容差变化会改变量化网格，从而可能改变重复几何判定。

## 5. 排序规则

### 5.1 Findings

报告中的 findings 按以下键升序：

```text
severity_rank(error=0, warning=1, info=2)
rule_id
layer_id
feature_id（空值按空串）
related_feature_ids（元组顺序）
finding_id
```

该排序在收集完成后统一执行，不依赖规则遍历或集合遍历顺序。

SQLite 查询的展示顺序为：

```text
severity_rank, rule_id, layer_id, COALESCE(feature_id, ''), finding_id
```

与报告排序在极端情况下可能有差异（报告还比较关联 ID），但两者都是确定的。

### 5.2 其他集合

- 报告中的图层统计按配置中的图层顺序输出。
- 报告中的规则统计按 `rule_id` 字典序输出。
- 报告和 `input_digest` 中的输入文件按 `layer_id` 排序。
- SQLite 中查询图层时按 `layer_id` 排序。
- `summary` 对象在 JSON 文件中按键排序。

### 5.3 输入源顺序

输入要素的原始顺序通过 `source_index` 保留。部分证据会引用源索引，例如：

- 重复要素 ID 的首个/重复行；
- 重复几何的首个/重复行；
- schema 异常字段所在要素；
- 端点近接的两端要素。

因此，**重排输入文件中的要素通常不是语义无操作**：文件 SHA-256 改变，`input_digest` 改变，某些证据和 finding ID 也可能改变。确定性指“同样的输入字节产生同样的输出”，不是“任意重排都产生同样输出”。

## 6. SQLite 幂等性

台账结构使用：

- `runs.result_digest` 主键；
- `(result_digest, layer_id)` 作为图层主键；
- `(result_digest, finding_id)` 作为 finding 主键；
- `(result_digest, layer_id)` 作为输入文件主键；
- 规则目录以 `rule_id` 为主键的全局快照。

写入流程：

1. 从报告读取 `result_digest`；
2. 重新计算报告摘要；
3. 如果缺失或不一致，抛 `TOPO-LED-003`，不打开写入；
4. 建立 schema（`CREATE TABLE IF NOT EXISTS`）；
5. 在事务内用 `INSERT OR IGNORE` 写入 run、layers、findings、input_files；
6. 规则目录用 `ON CONFLICT(rule_id) DO UPDATE` 刷新快照；
7. 提交。

因此：

- 同一报告写入 1 次或 N 次，run/finding 行数不变；
- 同一 `result_digest` 的不同报告（若通过摘要自校验，仅可能是刻意构造的摘要碰撞/伪造）不会覆盖既有行；
- 新摘要是一组新运行，不会与旧运行混行；
- `rule_catalog` 是全局表，会保持为当前代码目录的快照。

台账另含 `publish_log` 表：发布管线把 `ledger_committed` 事件与上述运行行放在**同一事务**提交，随后的 `verified`、`published`、`recovery_rollback` 事件各自成事务追加。该表是发布决定的可恢复持久证据，使同输入重跑不追加不一致记录、中断恢复可按日志重放；完整协议见 [`PUBLISHING.md`](PUBLISHING.md)。

## 7. `verify` 的核对范围

`python -m topoaudit verify --report ... --ledger ...` 会：

1. 检查报告文件存在、可解析且根为对象；
2. 重新计算 `result_digest` 并与报告字段比较；
3. 查台账中是否存在该摘要的 run；
4. 比较 `schema_version`、`run_label`、`input_digest`、`config_digest`；
5. 比较 `summary_json` 与报告 summary；
6. 比较图层集合和每层统计 JSON；
7. 比较 finding 集合、finding ID、规则、级别、图层、要素、关联要素和证据；
8. 比较输入文件集合与每项路径/格式/SHA-256/大小；
9. 比较规则目录的 `rule_id`、`severity`、`title` 快照；
10. 用报告 findings 重新汇总总数、级别数和图层数，与 summary 比较。

一致返回 `0`；任一不一致打印问题并返回 `1`；报告/台账不可读等结构化错误返回 `2`。

校验可以发现：

- 报告字段被修改但摘要未同步；
- 报告摘要被替换；
- 台账 run/layer/finding/input/catalog 内容被修改；
- summary 与 findings 明细矛盾。

校验**不能**发现：

- 报告和台账被同一攻击者一致地重建，并重新计算所有摘要；
- 有人替换为另一份自洽的运行；
- 时间、来源、授权身份的真实性。

摘要不是 HMAC、数字签名或可信时间戳。需要来源认证时，必须在工具之外使用受控签名和密钥管理。

## 8. 复现操作建议

```bash
# 两次独立输出
python -m topoaudit run --config path/to/config.json --out build/run-a
python -m topoaudit run --config path/to/config.json --out build/run-b

# 分别核对报告与各自台账
python -m topoaudit verify \
  --report build/run-a/report.json --ledger build/run-a/audit.sqlite
python -m topoaudit verify \
  --report build/run-b/report.json --ledger build/run-b/audit.sqlite
```

比较两个 `report.json` 的 `result_digest`。如果相同，而报告内容不同，说明有人修改了内容但没有重算摘要，或摘要实现被篡改——应视为审计异常。

若要验证输入未被本轮审计改写，可在运行前后比较输入文件 SHA-256；内置演示测试已对 fixture 做此检查。`run`/`demo` 还会拒绝把固定输出文件名写到配置文件或任一输入文件上。
