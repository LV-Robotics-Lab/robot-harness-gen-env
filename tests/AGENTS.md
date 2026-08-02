<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# tests

## Purpose（用途）
pytest 套件根。通过 `pyproject.toml` 配置（`testpaths = ["tests"]`，`pythonpath = ["."]`）。按模块组织单元与攻击测试，加上自包含 JSON fixture，使套件无需 RoboTwin checkout 即可运行。

## Subdirectories（子目录）
| Directory | Purpose |
|-----------|---------|
| `scene_gen/` | 每个 `scene_gen` 模块的单元 + 攻击测试（见 `scene_gen/AGENTS.md`） |
| `demo/` | Flask demo API 的测试（见 `demo/AGENTS.md`） |
| `fixtures/` | 自包含 committed fixture：资产目录、golden prompt、prompt 矩阵（见 `fixtures/AGENTS.md`） |

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- 套件必须保持基于 fixture 且无需 RoboTwin checkout；新增 committed fixture 放在 `fixtures/` 下，而非去取实时 checkout。
- 为每个已发现的误报模式（contact、support、containment、visibility、video）保留攻击测试。

### Testing Requirements（测试要求）
- 从仓库根运行 `pytest -q`。
- 对 support、containment、loader 或 validator 契约的改动，还须在支持 RoboTwin/SAPIEN 的机器上做真实回放——适用时在 PR 中注明。

### Common Patterns（常见模式）
- 每个 `scene_gen` 模块一个 `test_<module>.py`；攻击用例与该模块的正向测试并列。
- fixture 为 JSON，结构稳定且有文档（见 `fixtures/AGENTS.md`）。

## Dependencies（依赖）

### Internal（内部）
- `scene_gen/` —— 被测模块
- `demo/` —— 被测 app（通过 `tests/demo/`）

### External（外部）
- pytest >=8.3,<9（extra `dev`）
- ruff >=0.9,<1（extra `dev`）—— lint

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->