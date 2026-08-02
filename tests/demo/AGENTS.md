<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# demo

## Purpose（用途）
Flask demo API/浏览器界面（`demo/app.py`）的测试。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `test_app.py` | 测试 Flask demo app 端点与注册资产服务 |

## Subdirectories（子目录）
无。

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- 保持测试基于 fixture；不要在此启用真实 GPU/RoboTwin 运行时。

### Testing Requirements（测试要求）
- `pytest -q tests/demo`

### Common Patterns（常见模式）
- 验证端点与任务存储；断言仅服务已注册产物。

## Dependencies（依赖）

### Internal（内部）
- `demo/app.py`

### External（外部）
- pytest（extra `dev`）；Flask test client

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->