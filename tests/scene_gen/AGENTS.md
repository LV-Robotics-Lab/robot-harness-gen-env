<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# scene_gen

## Purpose（用途）
`scene_gen/` 的单元与攻击测试：每个模块一个 `test_<module>.py`，外加 acceptance/matrix runner。为每个已发现的误报模式（contact、support、containment、visibility、video）保留攻击测试。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `test_schema.py` | pydantic `SceneSpec` / `ResolvedSceneSpec` 契约测试 |
| `test_parser.py` | 受限中/英解析的 parser + golden/攻击测试 |
| `test_catalog.py` | 资产目录构建 + override 行为 |
| `test_grounding.py` | RoboTwin 资产 grounding 测试 |
| `test_solver.py` | 目标局部 support/containment 求解器测试 |
| `test_builder_validator.py` | builder + 静态 validator（哈希绑定包）测试 |
| `test_asset_generator.py` | 确定性几何代理生成测试 |
| `test_generated_scene.py` | 回放入口（`scene_gen/envs`）可导入表面测试 |
| `test_acceptance.py` | 验收门阈值测试 |
| `test_acceptance_runner.py` | 验收 runner 测试 |
| `test_prompt_matrix.py` | committed prompt 矩阵 runner 测试 |
| `test_rendered_critic.py` | 可选 VLM 渲染评判测试 |

## Subdirectories（子目录）
无。

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- 为每个已发现的误报模式保留攻击测试（通过 `is_static` 堆叠、无接触 pose、外层 AABB 重叠、仅起止截图验收等）。
- 测试必须基于 fixture 且无需 RoboTwin checkout；用 `tests/fixtures/`。需要真实 SAPIEN 的明确标注。

### Testing Requirements（测试要求）
- `pytest -q tests/scene_gen`（或从根跑 `pytest -q`）。
- 契约改动（support、containment、loader、validator）还须在支持 RoboTwin/SAPIEN 的机器上做真实回放。

### Common Patterns（常见模式）
- 每个 `scene_gen` 模块一个测试模块；正向 + 攻击用例并列。
- fixture 从 `tests/fixtures/*.json` 加载。

## Dependencies（依赖）

### Internal（内部）
- `scene_gen/*` —— 被测模块
- `tests/fixtures/*` —— committed fixture

### External（外部）
- pytest >=8.3,<9（extra `dev`）

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->