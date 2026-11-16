# 端点修复预演（只读）

本文档说明 `rehearse` 子系统的语义、请求/报告结构和确定性保证。预演只在
**内存副本**上比较可行的端点吸附方案，绝不写回 GeoJSON/CSV 源文件。

## 1. 边界与安全承诺

- 输入源（GeoJSON、CSV、配置、修复请求）只被读取；预演报告路径若与任何
  输入文件重合，直接以 `TOPO-RPT-004` 拒绝。
- 所有候选编辑通过 `geometry_edits.apply_endpoint_moves` 构造新的
  `Feature`/`Geometry` 对象，加载得到的源对象不被修改。
- 预演不产生可执行的写回指令；报告固定携带 `"read_only": true` 与
  `"write_back": "never"`。
- 基线审计若已存在不可自动修复的缺陷（无效几何、CRS 声明冲突等），预演以
  `status="rejected"` 整体拒绝，不产生任何候选。

## 2. 修复请求

请求是一个 JSON 对象，由 `repair_request.parse_repair_request` 严格校验，
未知字段、负数位移、非法 `side` 都会抛出 `TOPO-FIX-001/003`。

```json
{
  "layer_id": "roads",
  "max_displacement": 0.9,
  "candidate_budget": 200,
  "targets": [
    [
      {"feature_id": "RD-008", "part_index": "parts[0]", "side": "end"},
      {"feature_id": "RD-009", "part_index": "parts[0]", "side": "start"}
    ]
  ],
  "endpoint_limits": [
    {
      "endpoint": {"feature_id": "RD-008", "part_index": "parts[0]", "side": "end"},
      "max_displacement": 0.4
    }
  ],
  "protected_connections": [
    [
      {"feature_id": "RD-002", "part_index": "parts[0]", "side": "end"},
      {"feature_id": "RD-003", "part_index": "parts[0]", "side": "start"}
    ]
  ]
}
```

字段语义：

| 字段 | 含义 |
| --- | --- |
| `layer_id` | 预演所在的线图层；端点语义不跨图层。 |
| `targets` | 要修复的近失配端点对，每对必须满足 `coordinate_epsilon < 距离 <= near_miss_distance`。 |
| `max_displacement` | 每个端点允许的最大位移（正数），可被 `endpoint_limits` 进一步收紧。 |
| `endpoint_limits` | 可选的逐端点位移上限。 |
| `protected_connections` | 禁止改变的既有精确连接；引用的两端必须已在 `coordinate_epsilon` 内连通，所属连接分组整体被钉住。 |
| `candidate_budget` | 候选组合枚举上限；超出时只返回稳定排序前缀并置 `review_required=true`，不宣称无解或最优。 |

## 3. 聚簇规则：近邻不传递

- 端点先按既有精确连接（距离 `<= coordinate_epsilon`）做并查集分组；同组
  端点永远一起移动，因此既有连接不会被拉断。
- 仅对 `targets` 中**显式给出的边**合并连接组。`A 近 B、B 近 C` 不会因为
  传递性把 `A、C` 并入同一簇：只请求 A-B 时，C 完全不参与；只有同时请求
  两条边，三者才进入同一簇并共同寻找可行锚点。
- 每个簇的候选锚点是确定性的：各连接组当前点，以及多组时的几何质心。

## 4. 候选复审（在内存副本上复用现有规则）

每个候选都会：

1. 按锚点生成逐端点 `EndpointMove`，校验位移上限与保护钉住约束；
2. 在内存要素副本上应用编辑并重建 `EndpointIndex`；
3. 用 `rules.run_layer_rules` 跑完整几何与拓扑规则链；
4. 通过 `rules.diff_findings` 以**业务键**对照基线，输出
   `added/removed/retained` 三类 findings 及受影响 feature/part/side；
5. 额外校验：
   - 每条目标边修后必须在 `coordinate_epsilon` 内真实连通
     （`POST_TARGET_NOT_CONNECTED`）；
   - 基线既有跨要素精确连接不得消失
     （`POST_EXISTING_CONNECTION_BROKEN`）；
   - 不得把未指定的邻近端点误接入精确连接
     （`POST_ACCIDENTAL_CONNECTION`，同一显式簇内部的互连属于预期）；
   - 修后复审新增任何缺陷（零长度段、自交、重复顶点等）记
     `NEW_FINDING_AFTER_REPAIR`。

只要存在任一冲突，候选 `feasible=false`，并保留完整不可行原因；不存在
“伪可执行”建议。

## 5. 非支配取舍（帕累托前沿）

可行方案按两个目标比较：`moved_endpoint_count`（少改端点）与
`max_displacement`（低最大位移）。例如相距 0.8 的一对端点、上限 0.9：

- 只移动一端 0.8：编辑量 1、最大位移 0.8；
- 两端各移动 0.4 到中点：编辑量 2、最大位移 0.4。

两者互不支配，都会出现在 `pareto_front` 中，并分别说明影响的
feature/part/side 与 findings 变化。小型候选集会完整列出前沿；超预算时
标记 `review_required` 与 `CANDIDATE_BUDGET_EXCEEDED`，仅给待复核前缀。

## 6. 状态与冲突词汇

| 状态/代码 | 含义 |
| --- | --- |
| `status=feasible` | 至少一个候选通过全部复审。 |
| `status=infeasible` | 枚举到的候选全部不满足约束，附带结构化冲突。 |
| `status=rejected` | 基线存在阻断性缺陷或目标不是近失配关系，未生成候选。 |
| `TARGET_NOT_NEAR_MISS` | 目标对已连通或超出近接容差。 |
| `DISPLACEMENT_LIMIT` / `NO_SNAP_POINT_WITHIN_LIMITS` | 锚点违反位移上限，簇内无可行吸附点。 |
| `PROTECTED_ENDPOINT_PINNED` | 锚点要求移动受保护连接的端点。 |
| `POST_*` / `NEW_FINDING_AFTER_REPAIR` | 修后连接完整性或规则复审失败。 |
| `CANDIDATE_BUDGET_EXCEEDED` | 组合数超预算，结果为待复核前缀而非全局结论。 |

## 7. 确定性

- 端点记录按 `(source_index, part, side)` 排序；簇、锚点、候选与帕累托前沿
  全部以坐标内容键排序，与输入要素排列无关。
- 候选 ID 与 `rehearsal_digest` 由 `util.canonical_json` 序列化后 SHA-256
  截取，相同输入字节与配置下逐位复现。
- findings 差分时，端点类规则（`NEAR_MISS_ENDPOINT`、`DANGLING_ENDPOINT`）
  使用端点业务身份而非包含坐标的 finding ID，避免坐标变化造成假性增减。

## 8. CLI 与报告

```bash
python -m topoaudit rehearse \
  --config <audit-config.json> \
  --request <repair-request.json> \
  --out <rehearsal-report.json>
```

报告顶层包含 `rehearsal_digest`、输入文件指纹列表、只读标记，以及
`rehearsal` 对象（状态、容差、目标、冲突、帕累托 ID 与全部候选明细）。
