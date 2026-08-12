<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# demo

## Purpose（用途）
`/gen-env` 的单机 Flask 控制面与浏览器界面。队列化 GPU 编译/运行时/评审任务，接受文本 prompt 和 seed，仅对外暴露每个任务注册过的截图、视频、manifest 与验证证据。从 `scene_gen` 导入核心流水线。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `__init__.py` | 包标记，使 `demo` 可被导入 |
| `app.py` | Flask app：Text2Env 编译 + 运行时 + VLM 评审端点、任务存储、注册资产服务（约 395 行） |

## Subdirectories（子目录）
| Directory | Purpose |
|-----------|---------|
| `static/` | 浏览器前端资产（见 `static/AGENTS.md`） |

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- demo 只是 `scene_gen` 上的控制面；切勿在此复制流水线逻辑——调用核心库即可。
- 仅暴露已注册的截图/视频/manifest/证据；不要加能服务任意文件系统路径的路由。
- 通过环境变量配置：`ROBOTWIN_ROOT`、`ROBOTWIN_PYTHON`、`SCENE_ASSET_CATALOG`、`SCENE_DEMO_JOBS_ROOT`。

### Testing Requirements（测试要求）
- `tests/demo/test_app.py` 覆盖 Flask API。运行 `pytest -q tests/demo`。
- 单元测试中不要启用真实 GPU 运行时；保持基于 fixture。

### Common Patterns（常见模式）
- 任务入队并按 id 引用；结果仅指向已注册产物。
- Flask 可选依赖（`Flask>=3.0,<4`）声明在 `pyproject.toml`；用 `pip install -e '.[demo]'` 安装。

## Dependencies（依赖）

### Internal（内部）
- `scene_gen/` —— API 调用的编译/grounding/solve/validate 流水线

### External（外部）
- Flask >=3.0,<4（extra `demo`）

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->