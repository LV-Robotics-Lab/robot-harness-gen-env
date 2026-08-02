<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# envs

## Purpose（用途）
生成场景的运行时环境子包。提供回放入口，从 resolved 包构建并驱动一个 RoboTwin/SAPIEN 场景。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `__init__.py` | `envs` 子包的包初始化 |
| `generated_scene.py` | 生成场景运行时环境：RoboTwin/SAPIEN 场景构建 + 回放入口 |

## Subdirectories（子目录）
无。

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- 这是回放时 RoboTwin/SAPIEN 直接接触的唯一路径；保持其接口与 `scene_gen/builder.py` 产出的 `ResolvedSceneSpec` 哈希绑定。
- 不要在此引入策略、数据采集或训练——仅回放。

### Testing Requirements（测试要求）
- 由 `tests/scene_gen/test_generated_scene.py` 覆盖。真实回放需要 RoboTwin checkout；CI 只验证可导入表面。

### Common Patterns（常见模式）
- 加载 resolved 场景 + 资产目录，产出结构化物理证据（contact、settling、视频帧）。

## Dependencies（依赖）

### Internal（内部）
- `scene_gen/schema.py`（`ResolvedSceneSpec`）、`scene_gen/catalog.py`、`scene_gen/builder.py`

### External（外部）
- RoboTwin + SAPIEN（仅运行时）

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->