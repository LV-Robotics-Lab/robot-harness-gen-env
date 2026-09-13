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
| `run_100_seed_acceptance.py` | CLI：100-seed 验收批量运行（可选 `--runtime` 跑 SAPIEN） |
| `run_prompt_matrix.py` | CLI：跨 seed 跑 committed prompt 矩阵，可选 SAPIEN 运行时 |
| `run_rendered_critic.py` | CLI：对 resolved 场景 + 预览图跑可选 VLM 渲染评判 |
| `build_stage5_report.py` | CLI：构建 stage-5 验收报告 |
| `run_self_improving_tests.sh` | 限时测试入口：六组、独立 root 与覆盖归并；参数和当前门见 `x2env_test_groups.py --help` |
| `check_reader_docs.py` | 解析真实Markdown链接/锚点并核历史归档bytes；默认远端not_run，第五组显式有界HTTP检查 |

## Subdirectories（子目录）
无。

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- 脚本是 `scene_gen` 之上的薄入口。流水线逻辑加到 `scene_gen`，不要加在这里；这些文件只做参数解析与编排。
- 真实运行时需要 RoboTwin 环境 Python 与 RoboTwin checkout（`--robotwin-root`）。不要假设测试/CI 环境中有 RoboTwin。
- 保持 CLI flag 名稳定——下游用户及 README/AGENTS 文档引用它们。
- 平台新实验从 canonical `x2env` 进入；历史 campaign、Qwen assessment、qualified replay 脚本入口已退役，旧配方使用对应固定 Git 历史。进展见根 AGENTS 指向的 Golden E2E 目录。

### Testing Requirements（测试要求）
- 场景脚本覆盖来自 `tests/scene_gen/`；旧 qualified replay 执行与资格图已退役，固定历史从 Git 恢复。
- 改测试归属或 CI 时先读 Golden E2E 的 `qualification-matrix-v2.json` 最新授权和 `x2env_test_groups.py`。
  本轮每组和归并含退出最多20分钟，使用新证据目录；只有同源完整六组测量可合并，
  canonical业务语句/分支覆盖率实测报告，不以精确100%阻断；测试/lint/源码与测量完整性仍阻断。
  外部执行边界精确列于脚本，pending 非覆盖门不算已通过。
- 第五组在pytest前执行reader docs门；报告区分local/archive/remote，远端未查不得写完整通过。
  历史归档仅核manifest成员，不把原文内部旧相对链接或旧指令当active契约。
- 第六组先跑完整LINT_ROOTS并保ruff.json，再做库存和保留层测试；pending为空只说明均已接线。
- 改动后用 `--help` 校验脚本的 CLI 表面。

### Common Patterns（常见模式）
- 生成、验收类 CLI 通常在指定 `--out-root` 下写结构化 JSON 证据 + SHA-256 manifest。
- 运行时视频：请求 120 帧，119 连续释放帧 + 最终 settled 帧；验收要求至少 30 个不同帧。

## Dependencies（依赖）

### Internal（内部）
- `scene_gen/` —— 整套 编译/grounding/solve/validate/replay 流水线
- `self_improving.harness` —— 平台测试和辅助脚本使用的明确模块

### External（外部）
- 标准库 `argparse`；无第三方 CLI 框架
- RoboTwin + SAPIEN（仅运行时，CI 中不安装）
- markdown-it-py（dev extra，仅文档检查器；不进入仿真运行时要求）

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
