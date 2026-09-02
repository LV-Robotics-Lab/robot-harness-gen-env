<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# script

## Purpose（用途）
`/gen-env` 流水线的独立 CLI 入口：把 prompt 编译为 resolved 场景包、跑真实 RoboTwin/SAPIEN 物理、批量验收运行、committed prompt 矩阵、构建 stage-5 验收报告，以及可选渲染评判。每个脚本可用运行时 Python 解释器直接运行。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `generate_scene.py` | CLI：带 seed 编译 `text -> ResolvedSceneSpec` 包 |
| `run_scene_runtime.py` | CLI：真实 SAPIEN/RoboTwin 物理回放，带 precheck/settle/contact-window/video 参数 |
| `run_qualified_replay.py` | 薄 CLI：在证据绑定的部署上运行已资格化 replay 固定案例；只接受一个 `--settings` 文件 |
| `run_100_seed_acceptance.py` | CLI：100-seed 验收批量运行（可选 `--runtime` 跑 SAPIEN） |
| `run_prompt_matrix.py` | CLI：跨 seed 跑 committed prompt 矩阵，可选 SAPIEN 运行时 |
| `run_rendered_critic.py` | CLI：对 resolved 场景 + 预览图跑可选 VLM 渲染评判 |
| `build_stage5_report.py` | CLI：构建 stage-5 验收报告 |

## Subdirectories（子目录）
无。

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- 脚本是 `scene_gen` 之上的薄入口。流水线逻辑加到 `scene_gen`，不要加在这里；这些文件只做参数解析与编排。
- 真实运行时需要 RoboTwin 环境 Python 与 RoboTwin checkout（`--robotwin-root`）。不要假设测试/CI 环境中有 RoboTwin。
- 保持 CLI flag 名稳定——下游用户及 README/AGENTS 文档引用它们。
- `run_qualified_replay.py` 只做 checkout bootstrap 与委派；设置、资格校验、持久化对账和脱敏摘要属于 `self_improving.qualified_replay_cli` 深模块。

### Testing Requirements（测试要求）
- 场景脚本覆盖来自 `tests/scene_gen/`；固定资格 replay 入口由 `tests/self_improving/test_qualified_replay_cli.py` 的公开 CLI 与攻击测试覆盖。
- 改动后用 `--help` 校验脚本的 CLI 表面。

### Common Patterns（常见模式）
- 生成、验收类 CLI 通常在指定 `--out-root` 下写结构化 JSON 证据 + SHA-256 manifest；
  `run_qualified_replay.py` 则只接受 `--settings`，把持久状态写入设置声明的全新 state root，并在
  stdout 输出一行脱敏终态摘要。
- 运行时视频：请求 120 帧，119 连续释放帧 + 最终 settled 帧；验收要求至少 30 个不同帧。

## Dependencies（依赖）

### Internal（内部）
- `scene_gen/` —— 整套 编译/grounding/solve/validate/replay 流水线
- `self_improving.qualified_replay_cli` / `self_improving.harness` —— 固定资格回放入口、资格校验与持久化运行边界

### External（外部）
- 标准库 `argparse`；无第三方 CLI 框架
- RoboTwin + SAPIEN（仅运行时，CI 中不安装）

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
