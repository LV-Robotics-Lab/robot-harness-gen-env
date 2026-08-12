<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# evidence

## Purpose（用途）
已计算、已验证的验收证据：带日期的物理验收笔记与 prompt-matrix 运行时报告。这些产物支撑 `README.md` 中陈述的强制阈值与通过率主张。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `physics-acceptance-20260717.md` | 2026-07-17 20-seed RTX 5090 物理验收笔记 |
| `prompt-matrix-20260719.md` | 2026-07-19 prompt-matrix 验收笔记 |
| `prompt-matrix-runtime-20260719.json` | 结构化 prompt-matrix 运行时报告 |

## Subdirectories（子目录）
无。

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- 这些是不可变的历史证据笔记；不要回溯编辑。新运行另起一份带日期的笔记/报告。
- 报告哈希须与引用的运行时产物匹配。若重跑产生新哈希，另起笔记而非覆盖。

### Testing Requirements（测试要求）
- 这里不跑测试。若某 README 断言引用本目录中的事实，编辑时保持该事实不变。

### Common Patterns（常见模式）
- 文件名编码证据类型 + ISO 日期（`YYYYMMDD`）。
- 每份笔记记录命令、阈值、报告哈希与逐用例结果。

## Dependencies（依赖）

### Internal（内部）
- 由 `script/run_prompt_matrix.py`、`script/run_100_seed_acceptance.py`、`script/run_scene_runtime.py` 产出；被 `README.md` 引用。

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->