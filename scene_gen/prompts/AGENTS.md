<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# prompts

## Purpose（用途）
双语 `scene_gen/parser.py` 用的 LLM prompt 模板，从受限文本引导出结构化 `SceneSpec`。通过 `[tool.setuptools.package-data]` 作为数据打包，而非代码。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `parse_scene.md` | 结构化场景解析的 LLM prompt 模板（无代码/路径/pose） |

## Subdirectories（子目录）
无。

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- prompt 必须保持解析器受限：指示模型绝不产出代码、路径、id 或 pose。
- 这里改动会改变 LLM 行为；对照 `tests/fixtures/golden_prompts.json` 与 `tests/scene_gen/test_parser.py` 的攻击测试验证。

### Testing Requirements（测试要求）
- `pytest -q tests/scene_gen/test_parser.py` 覆盖由该 prompt 驱动的解析器行为。

### Common Patterns（常见模式）
- Markdown prompt 作为 package data 打包；由 `parser.py` 在解析期加载。

## Dependencies（依赖）

### Internal（内部）
- `scene_gen/parser.py` 加载 `parse_scene.md`

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->