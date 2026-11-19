# 发布、恢复与故障演练

本文档说明 `topoaudit` 如何把一次审计结果**原子地**发布到输出目录，以及中断后如何恢复。目标是：复核人员在任一时刻打开输出目录，看到的要么是上一版完整结果，要么是本次完整结果，永远不是半套报告或混杂 A/B 的文件。

## 1. 输出目录布局

```text
<out>/
  audit.sqlite                  累积台账（runs/findings/... 按 result_digest 幂等，含 publish_log 表）
  current -> versions/<digest>/ 唯一的发布边界：原子切换的符号链接
  report.json -> current/report.json   兼容链接：既有命令与脚本路径不变
  report.md  -> current/report.md
  report.html -> current/report.html
  inputs.json -> current/inputs.json
  summary.txt -> current/summary.txt
  versions/<digest>/            已发布版本（含 manifest.json），发布后不再修改
  .staging/<digest>/            发布中的暂存区，复核人员不可见
  .quarantine/                  与内容摘要不一致的产物，隔离保存而非删除
```

- 版本身份是**内容摘要** `result_digest`，不是文件名或运行标签；`versions/` 以 digest 命名。
- `manifest.json` 记录该版本 `result_digest`、`input_digest`、`config_digest` 以及每个产物文件的 SHA-256 与字节数——这是各产物身份的持久证据。
- 顶层 5 个报告名是经 `current/` 的相对符号链接；`python -m topoaudit verify --report <out>/report.json --ledger <out>/audit.sqlite` 的用法不变。移动整个输出目录后链接仍然有效。
- 台账是跨版本累积的：旧版本的台账行永久保留，`verify --report <out>/versions/<旧digest>/report.json --ledger <out>/audit.sqlite` 始终可以复核旧版本。

## 2. 发布阶段（可重放）

每次发布按固定顺序推进，每个阶段都有持久证据：

1. **暂存**：5 个报告文件写入 `.staging/<digest>/`（逐文件临时名 + `os.replace` + fsync），最后写入 `manifest.json`。
2. **台账提交**：runs/layers/findings/input_files/rule_catalog 行与 `publish_log` 的 `ledger_committed` 事件在**同一个 SQLite 事务**中提交；事件 detail 记录 `manifest_sha256` 和各文件 SHA-256（提交意向）。事务要么整体落盘要么整体回滚。
3. **发布前验证**：重新读取暂存文件并对照清单与台账提交意向做 SHA-256 校验；用 `verify_report_and_ledger` 全量交叉核对报告与台账行；检查 report.md/report.html/summary.txt/inputs.json 都携带同一 `result_digest`（防混杂）。全部通过才记录 `verified` 事件；任何一项失败则记录 `verification_failed` 并中止，当前结果不变。
4. **发布**：`.staging/<digest>/` 更名为 `versions/<digest>/`（同名同内容版本已存在则复用），随后用临时符号链接 + `os.replace` 原子切换 `current`，最后追加 `published` 事件（含各文件摘要、上一版 digest、来源 `via`）。

不变量：**`current` 只指向台账行已提交且验证已通过、文件完整的版本**。同输入重跑是幂等空操作：版本目录内容寻址可复用，台账行 `INSERT OR IGNORE`，`publish_log` 不追加不一致记录。

## 3. 故障切点与恢复

故障演练切点（环境变量 `TOPOAUDIT_FAULT_AT`，进程以 `os._exit(99)` 硬中断）：

| 切点 | 含义 |
| --- | --- |
| `staging.partial` | 只写完部分报告文件 |
| `staging.complete` | 报告与清单写完，台账事务尚未提交 |
| `ledger.committed` | 台账行与提交意向已落盘，`current` 尚未切换 |
| `publish.verified` | 验证事件已记录，版本尚未提升/切换 |

恢复在每次 `run`/`demo` 开始前自动执行，也可单独运行：

```bash
python -m topoaudit recover --out <out>                 # 默认 roll-forward
python -m topoaudit recover --out <out> --policy rollback
```

- 切点 1、2（台账未提交）：**必须保留上一版**。恢复删除暂存区，记录 `recovery_rollback`（`reason=staging_incomplete` 或 `ledger_not_committed`），`current` 不动。
- 切点 3（台账已提交、指针未切换）：两条都正确的路线——
  - `roll-forward`（默认）：重新全量校验 B 的文件、清单、台账提交意向与台账行，全部通过后前滚发布 B（`published` 事件 `via=recovery_rollforward`）；校验失败自动转为回滚。
  - `rollback`：保留上一版为当前结果，清理未发布的暂存/版本产物（隔离而非删除），记录 `recovery_rollback`（`reason=policy`）。
- 路线选择、原因、保留的当前 digest 都写入台账 `publish_log` 表，可用 `topoaudit.ledger_query.get_publish_log` 复查；两条路线都不会丢失上一次已发布结果（已发布版本目录永不删除或覆盖）。
- 指针与日志不一致（切换后、`published` 记录前崩溃）时，恢复会先全量验证再补齐 `published` 记录（`via=recovery_heal`），验证失败则切回最近已发布版本。

## 4. 旧布局迁移

对本次改动前生成的扁平输出目录（顶层为真实文件），首次运行会自动把旧结果按 `result_digest` 收编为 `versions/<digest>/`（记录 `published` 事件 `via=legacy_adopted`），再切换为链接布局；无法识别 digest 的旧文件移入 `.quarantine/`，不丢弃任何内容。

## 5. 限制

- 依赖文件系统符号链接与 `os.replace` 的原子性（POSIX 语义）；不支持同一输出目录的并发写入。
- 恢复记录只写入已存在的台账；首次运行即在暂存阶段崩溃且台账尚未创建时，暂存区会被清理但无日志可写（此时也没有任何已发布结果需要保护）。
