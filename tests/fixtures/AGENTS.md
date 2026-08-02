<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# fixtures

## Purpose（用途）
自包含 committed fixture，使 pytest 套件无需 RoboTwin checkout 即可运行：资产目录、golden prompt、双语 prompt 矩阵。稳定的、有文档的 JSON，保证测试可复现。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `asset_catalog.json` | committed 资产目录 fixture（无需 RoboTwin checkout） |
| `golden_prompts.json` | golden prompt fixture |
| `prompt_matrix.json` | 11 例中/英 prompt 矩阵，3 个 seed，含一例不可行区域预期拒绝 |

## Subdirectories（子目录）
无。

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- fixture 是 committed 的，被测试与 CI 引用；保持结构稳定。扩展而非重塑。
- 不要嵌入密钥或 RoboTwin 资产；本目录是公开测试数据。

### Testing Requirements（测试要求）
- 被 `tests/scene_gen/*` 与 `script/run_prompt_matrix.py` 消费；任何改动后跑 `pytest -q`。

### Common Patterns（常见模式）
- `prompt_matrix.json` 混合正向用例与一例预期 solver 拒绝用例；两者都计入聚合 pass/fail 结果。

## Dependencies（依赖）

### Internal（内部）
- 与 `scene_gen/catalog.py`、`scene_gen/parser.py`、`tests/scene_gen/` 的 golden/攻击测试配套产出。

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->