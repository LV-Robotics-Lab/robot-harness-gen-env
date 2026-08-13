<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# Robot Harness Gen-Env and Self-Improving Platform

## Purpose（用途）
仓库包含两层：稳定的 `/gen-env` 确定性编译器，以及围绕它组织选择、生成、采集、评估、诊断、资产复用和跨仿真适配的 `self_improving/` 平台。`scene_gen/` 的信任边界不变；策略或仿真器特定实现只能进入平台适配层或独立子模块。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `README.md` | 项目概览、强制阈值、CLI 配方、已验证证据链接 |
| `pyproject.toml` | Setuptools 清单（`robot-harness-gen-env` v0.1.0，Python >=3.11，pydantic/PyYAML/Pillow；`demo`/`vlm`/`dev` 可选依赖；ruff 配置） |
| `requirements-scene-gen.txt` | 核心场景生成依赖版本钉 |
| `requirements-demo.txt` | Demo（Flask）依赖版本钉 |
| `requirements-vlm.txt` | 可选渲染评判（VLM）依赖版本钉 |
| `LICENSE` | Apache-2.0 全文 |
| `NOTICE` | 归属声明（源自 `yezheng04/robotwin-text2env-demo`） |
| `AGENTS.md` | 本文件——贡献者规则与结构地图 |
| `.gitignore` | 忽略 RoboTwin 资产、生成数据集、检查点、密钥、批量输出 |

## Subdirectories（子目录）
| Directory | Purpose |
|-----------|---------|
| `scene_gen/` | 核心库：schema、catalog、parser、grounding、solver、builder、validators（见 `scene_gen/AGENTS.md`） |
| `script/` | CLI 入口：编译、运行时、批量验收、矩阵、渲染评判（见 `script/AGENTS.md`） |
| `demo/` | Flask API + 队列化 GPU 任务的浏览器界面（见 `demo/AGENTS.md`） |
| `tests/` | 带 committed fixture 与攻击测试的 pytest 套件（见 `tests/AGENTS.md`） |
| `docs/` | 已验证的物理/prompt-matrix 证据笔记（见 `docs/AGENTS.md`） |
| `.github/` | CI 工作流定义（见 `.github/AGENTS.md`） |
| `repo-docs/` | 仓库行为讲解（中文）：一条真实路径、代码地图、概念模块、证据底座（见 `repo-docs/README.md`） |
| `self_improving/` | Self-Improving 平台编排、历史 stage、资产管线、迁移工具和机读来源清单 |
| `external/` | 独立生命周期的 OpenReal2Sim 与 digital-cousins 子模块 |

## Repo docs

生活指南位于 `repo-docs/`。从 `repo-docs/README.md` 开始；当 `repo-docs/walkthroughs/one-real-run.md` 存在时，把它当作主行为踪迹。

Repo-docs sync triggers（在最终回复前先跑 sync gate）：仓库问题；架构 / onboarding / 「这是怎么工作的」类回答；行为相关的代码、配置、数据、脚本、测试改动；用户对稳定行为的不确定或纠正；对话里发现或澄清的稳定项目知识；即将写入 memory 的知识。Trigger 命中时先跑前景 sync gate（用 `repo-docs` skill 在 Sync 模式可用时，否则手动读相关指南页 + 检视当前源码 + 决定 `none` / `answer-only` / `foreground patch` / `background sync`），普通仓库问题在指南仍当前且答案可引用已检视指南/源证据时可 `answer-only`。仅当当前答案或改动会让读者误解、指南说反、或一个小的稳定知识缺口应当现在补时，最后回复前 patch 最小的归属指南页。

若所需 guide 工作更宽且对当前答案不为必需，且平台支持真 tracked handoff，委派给后台 `repo-docs` sync agent；handoff 须含 trigger、稳定事实或变更源区、候选指南页、要跑的验证、以及预期的 `repo-docs/change-log.md` 更新。若无后台 agent，按已检视源回答并视情况提及 pending docs gap。涉及行为的代码/配置/数据/脚本/测试改动时，除非用户明说不碰文档，否则完成前与指南比对。

This repo's `repo-docs/` guide is reader-facing Chinese documentation. When updating reader-facing guide pages, use `repo-docs-zh` when available; keep Chinese reader handles in the prose and preserve exact source identifiers for lookup.

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- `scene_gen/` 仍只负责 `/gen-env`；策略执行、采集、训练、评估和跨仿真器迁移必须放在 `self_improving/` 的明确模块中，不能反向污染核心编译器。
- 权威贡献者契约（作用域、验收规则、测试、数据、git）在下文 `<!-- MANUAL -->` 之下逐字保留——改动前先读。
- Python 3.11 是已测试的本地版本；ruff 行宽 100。

### Testing Requirements（测试要求）
- 本地跑 `pytest -q`（套件用 committed fixture，无需 RoboTwin checkout）。
- 对 support、containment、loader 或 validator 契约的改动，还须在支持 RoboTwin/SAPIEN 的机器上做一次真实回放。
- 为每一个已发现的误报模式保留攻击测试。

### Common Patterns（常见模式）
- `scene_gen/schema.py` 中的类型化 pydantic 契约门控每一个下游阶段。
- 渲染不是物理证据——运行时门控才是权威。
- 运行时证据与 resolved scene 保持哈希绑定。

## Dependencies（依赖）

### External（外部）
- pydantic >=2.9,<3 —— 类型化场景契约
- PyYAML >=6,<7 —— `asset_overrides.yml` 与矩阵解析
- Pillow >=10,<12 —— 渲染评判的图像处理
- Flask >=3.0,<4（extra `demo`）—— 控制面 API
- transformers / accelerate / qwen-vl-utils（extra `vlm`）—— 可选渲染评判
- pytest / ruff（extra `dev`）—— 测试与 lint

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

# Robot Harness /gen-env Contributor Rules

## Scope

The stable core owns `text -> SceneSpec -> ResolvedSceneSpec -> RoboTwin/SAPIEN
evidence`. The `self_improving/` layer may orchestrate collection, training,
evaluation, diagnosis, asset reuse, and simulator adapters without weakening or
bypassing the core contracts.

## Acceptance Rules

- A render is not physical evidence.
- Nested sources must be dynamic and contact the declared target.
- Never accept stacking through `is_static`, contact-free poses, outer AABB
  overlap, or start/end screenshots alone.
- Support and containment calculations must be target-local and account for the
  complete source footprint.
- Runtime evidence must remain hash-bound to the resolved scene.
- A requested video must retain real sequential frames and report its total and
  unique frame counts.
- New asset overrides require measured geometry or a documented simulator probe.

## Tests

Run `pytest -q` locally. Changes to support, containment, loader, or validator
contracts also require a real RoboTwin/SAPIEN replay on a supported machine.
Keep attack tests for every discovered false-positive mode.

## Data And Secrets

Do not commit RoboTwin assets, generated datasets, checkpoints, API keys, SSH
credentials, local configs, or bulk runtime output. Small evidence samples may be
committed only when they are needed to explain an acceptance contract.

## Git

Keep core changes scoped to `/gen-env` and platform changes scoped to named
modules under `self_improving/`. Preserve upstream attribution and avoid
rewriting published history. External projects belong in submodules, not copied
vendor trees.
