# openxsim（Open-X-Sim） · 项目全景

> 一句话定位：跨仿真器环境迁移引擎——把文本 / 视觉证据 / 已有仿真环境统一编译成**后端中立 IR**（`EnvironmentPackage`），再编译到 IsaacSim / MuJoCo / SAPIEN / MetaSim / RoboTwin 五个后端，并做 L0-L4 一致性评估。

## 1. 这是什么

机器人仿真环境通常绑死在单一模拟器的资产格式与 API 上（SAPIEN 场景没法直接在 Isaac 跑）。openxsim 用一层 **typed、模拟器中立的中间表示（IR）**把「任务语义」与「后端资产格式」分离：USD / MJCF / URDF / SAPIEN 文件只是资产的多种 representation，环境的正典模型是 IR。任何来源（一句话指令、一段视频、一个现成 MJCF 环境、env-gen 场景包）先进 IR，再由各后端 compiler 编译成可运行环境，最后用分级一致性测试验证「迁过去还是不是同一个环境」。它是 env-gen-dev 仓的跨阶段共享引擎，位于 `shared/openxsim/`。

## 2. 整体 输入 → 输出

- **输入**（四类，任一即可）：
  1. 自然语言任务指令（如 "place a can on the plate"）；
  2. 指令 + 图像/视频证据（可附用户或 VLM 给的 JSON 约束）；
  3. 已有仿真环境文件——MJCF `.xml` / Isaac `.usda` / SAPIEN scene JSON / MetaSim scenario JSON / compile manifest / env-gen `resolved_scene.json`；
  4. 公开资产源——JSON catalog、GitHub 仓库（asset-scout 用）。
- **输出**：`--output` 目录（默认 `<项目根>/artifacts/openxsim/`）下的各后端编译产物 + workflow manifest；transfer 时另有 L0-L4 conformance 报告；asset-scout 时是注册好的 AssetBundle（多格式 representation + provenance）。CLI 同时在 stdout 打 JSON 摘要（package_id / digest（内容哈希）/ 各后端结果）。
- **关键中间产物**：`EnvironmentPackage`（IR 的 JSON 序列化）——所有工作流的枢纽，可落盘、可 digest、可回读。

## 3. 阶段总览

下文 `openxsim/…` 均指包目录 `source/agenticsim/agenticsim/openxsim/…`。

| 阶段 | 职责（一句话） | 对应位置 |
|---|---|---|
| 1 · 中立 IR | 环境的正典数据模型：类型化、可校验、可序列化 | `openxsim/ir.py` |
| 2 · 编译进 IR（三条入口） | 文本 / 文本+视觉证据 / 已有环境导入（含 env-gen） | `openxsim/text2env.py` `anchors.py` `importers.py` `env_gen.py`、`generation/` |
| 3 · 多后端编译 | IR → 5 个后端的可运行产物 | `openxsim/backends.py` |
| 4 · 一致性评估 | L0-L4 分级验证迁移前后是同一环境 | `openxsim/conformance.py` `robotwin.py` |
| 5 · 资产获取 | AssetScout 搜 / 下 / 转 / 注册公开资产 | `openxsim/assets.py` |
| 6 · 编排与 CLI | 工作流串成 5 个子命令 + 测试锁定 | `openxsim/pipeline.py`、`scripts/` `tests/` `configs/` |

```
① 文本指令 ──────────────▶ text2env ──┐
② 指令+图像/视频证据 ────▶ anchor2env ─┼─▶ IR(EnvironmentPackage) ─▶ 多后端编译 ─▶ L0-L4 一致性
③ 已有环境(MJCF/USDA/…) ─▶ importers ──┤        ▲
④ 公开资产(catalog/GitHub)▶ AssetScout ─┴─▶ AssetBundle
```

**运行前置**（所有命令共用）：

```bash
conda activate env-gen-yuxin
OX=/home/jingxiang/yuxin/env-gen-dev/shared/openxsim
export PYTHONPATH="$OX/source/agenticsim:$OX/deps/metasim_core:$OX/third_party/MetaSim:$PYTHONPATH"
```

## 4. 分阶段详解

### 阶段 1 · 中立 IR

- **目标**：给环境一个与模拟器无关的正典模型；任务语义（success / termination / reset / observation）与资产格式解耦。
- **位置**：`openxsim/ir.py`
- **输入 → 输出**：dict / JSON ↔ 类型化对象（双向），带校验与内容 digest。

**文件清单**：

| 文件 | 功能 |
|---|---|
| `openxsim/ir.py` | 全部 IR 数据类：`EnvironmentPackage`（顶层包）、`EnvSpec`（场景）、`SceneObject`、`Pose`、`AssetBundle`（一资产多 representation）、`AssetRepresentation`、`AnchorSpec`（视觉证据锚点）、`TaskSpec`；校验（`IRValidationError`）、digest、JSON 读写 |

**运行参数配置**：无独立运行入口——纯数据模型，被其余所有阶段 import 消费；行为由 `tests/test_openxsim_ir_backends.py` 锁定。

### 阶段 2 · 编译进 IR（三条入口）

- **目标**：把任意来源变成 IR 包。生成式两条（纯文本、文本+视觉证据），导入式一条（已有环境文件，六种格式自动分发）。
- **位置**：`openxsim/text2env.py` `anchors.py` `importers.py` `env_gen.py`、`generation/placement_agent.py`
- **输入 → 输出**：指令 / 媒体文件 / 环境文件 → `EnvironmentPackage`

**文件清单**：

| 文件 | 功能 |
|---|---|
| `openxsim/text2env.py` | 纯文本编译入口：拿 placement_agent 的布局结果落成 IR 包 |
| `generation/placement_agent.py` | placement-only 规划器：自然语言指令 → RoboTwin 风格桌面任务布局（Text2Env v0 schema）；缺资产按 blocker 报告，不做资产生成 |
| `openxsim/anchors.py` | 图像/视频证据抽取与 `AnchorSpec` 融合：视频均匀抽帧、证据 hash 留档；内置 `ColorLayoutAnchorProvider`（颜色布局启发式视觉 provider） |
| `openxsim/importers.py` | 已有环境导入：compile manifest / MJCF / SAPIEN scene / MetaSim scenario / Isaac USDA 五路 importer；`import_environment()` 按 manifest、文件后缀、JSON schema、`source_backend` 提示自动分发 |
| `openxsim/env_gen.py` | env-gen `resolved_scene.json` → IR 的一等 importer（`import_env_gen()`）：保真搬运物体位姿/物理/来源信息，非 env-gen 文件与缺资产走 `EnvironmentImportError` |

**运行参数配置**（`text2env` / `anchor2env` 子命令）：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--instruction` | 自然语言任务指令 | 必填 |
| `--media` | 图像/视频证据文件（anchor2env） | anchor2env 必填 |
| `--annotations` | 用户或 VLM（视觉-语言模型）产出的 JSON 约束，作补充证据 | 无 |
| `--vision-provider` | 视觉证据提取器 | `none`（`color-layout` 启用内置启发式） |
| `--sample-count` | 视频均匀抽帧数 | `8`（最少 3） |
| `--repo-root` | 布局规划查资产的仓库根 | 项目根 |

**一键运行**：

```bash
python $OX/scripts/openxsim.py text2env --instruction "place a can on the plate"
# env-gen 场景导入（Python API）：
python -c "from agenticsim.openxsim.env_gen import import_env_gen; print(import_env_gen('<resolved_scene.json>').package_id)"
```

### 阶段 3 · 多后端编译

- **目标**：把一份 IR 编译成各模拟器的可运行产物。
- **位置**：`openxsim/backends.py`
- **输入 → 输出**：`EnvironmentPackage` → 每后端一份编译产物 + `CompileResult`（产物路径、blocker 清单）。

**文件清单**：

| 文件 | 功能 |
|---|---|
| `openxsim/backends.py` | 5 个后端 compiler：`IsaacSimCompiler`（USD）、`MuJoCoCompiler`（MJCF）、`SapienCompiler`、`MetaSimCompiler`、`RoboTwinCompiler`；`compile_package()` 统一入口按名分发 |

**运行参数配置**（各工作流共用）：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--backends` | 目标后端集合，逗号分隔 | `sapien`（可选 `isaacsim`/`mujoco`/`sapien`/`metasim`/`robotwin`；transfer 必填） |
| `--strict` | 严格编译：产物有 blocker（后端表达不了包内容）即失败，不降级通过 | 关 |

### 阶段 4 · 一致性评估

- **目标**：分级回答「迁移前后还是不是同一个环境」。
- **位置**：`openxsim/conformance.py` `robotwin.py`
- **输入 → 输出**：源/目标两侧的编译产物与运行证据 → 各级 PASS / FAIL / NOT_EVALUATED 的分级报告。

**文件清单**：

| 文件 | 功能 |
|---|---|
| `openxsim/conformance.py` | L0-L4 五级检查（见术语表），产出 `ConformanceCheck` 报告 |
| `openxsim/robotwin.py` | IR → RoboTwin `selection2env`（其场景放置/任务接口）adapter + `runtime_evidence_from_rollout()`：校验 RoboTwin rollout 与其包/任务程序一致并落 `runtime_evidence.json` |

**运行参数配置**（`robotwin-evidence` 子命令）：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--package` | IR 包 JSON | 必填 |
| `--task-program` / `--rollout-report` | RoboTwin 任务程序 / rollout 报告 | 必填 |
| `--evidence-output` | 证据输出路径 | rollout 报告同目录 `runtime_evidence.json` |
| `--minimum-video-frames` | rollout 视频最少帧数门槛 | `3` |

**一键运行**（transfer = 导入 + 编译 + 一致性，一条命令走完）：

```bash
python $OX/scripts/openxsim.py transfer --source $OX/configs/openxsim/existing_settle.xml \
  --backends sapien,metasim --strict
```

### 阶段 5 · 资产获取

- **目标**：按查询词从公开源找资产，下载、转多格式、带 provenance（来源与许可证记录）注册成 `AssetBundle`。
- **位置**：`openxsim/assets.py`
- **输入 → 输出**：查询词 + 资产源 → 注册好的 `AssetBundle`（多 representation）+ `search_evidence.json`（检索留痕）。

**文件清单**：

| 文件 | 功能 |
|---|---|
| `openxsim/assets.py` | `AssetScout` + 3 个搜索 provider（`CatalogSearchProvider` JSON 目录 / `GitHubTreeSearchProvider` 单仓树 / `GitHubRepositoryDiscoveryProvider` 仓库发现）、`_relevance` 相关度打分、下载与 license/provenance 记录、`compile_downloaded_asset` 格式转换、注册落盘 |

**运行参数配置**（`asset-scout` 子命令；三类源至少给一个）：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--query` / `--asset-id` | 查询词 / 注册用资产 id | 必填 |
| `--catalog` | JSON catalog 路径，可多次 | 无 |
| `--github` | GitHub 仓库（`owner/repo`），可多次；配 `--github-branch`/`--github-token`/`--license` | 无 / `main` / 无 / `unknown` |
| `--github-discovery` | 开启仓库发现；配 `--github-repository-query`/`--github-repository-limit` | 关 / 无 / `5` |
| `--candidate-index` | 从候选列表选第几个 | `0` |
| `--formats` | 转换目标格式集合 | `usda,mjcf,urdf,sapien_manifest,metasim_object` |
| `--smoke-backends` | 注册前用这些后端做冒烟编译 | 空（不冒烟） |

**一键运行**：

```bash
python $OX/scripts/openxsim.py asset-scout --query "mug" --asset-id mug_01 --catalog <catalog.json>
```

### 阶段 6 · 编排与 CLI

- **目标**：把上述能力串成 5 个可运行工作流，并用测试锁定行为。
- **位置**：`openxsim/pipeline.py`、`scripts/` `tests/` `configs/`
- **输入 → 输出**：CLI 参数 → `--output`（默认 `<项目根>/artifacts/openxsim/`）下的产物 + workflow manifest。

**文件清单**：

| 文件 | 功能 |
|---|---|
| `openxsim/pipeline.py` | `OpenXSimPipeline`：text2env / anchor2env / acquire_asset / transfer 四工作流的统一编排面，统一落 workflow manifest |
| `scripts/openxsim.py` | CLI 入口（自定位 `source/agenticsim`，无需预设 sys.path）：5 子命令 `text2env` `anchor2env` `asset-scout` `transfer` `robotwin-evidence` |
| `agenticsim/_third_party.py` | vendored 依赖装载：把 `third_party/` 包根挂上 `sys.path` |
| `agenticsim/__init__.py` `openxsim/__init__.py` `generation/__init__.py` | 包公共 API 导出 |
| `configs/openxsim/existing_settle.xml` | 内嵌 task contract 的 MJCF 样例环境，导入/迁移的现成试料 |
| `tests/test_openxsim_ir_backends.py` | 锁 IR 模型 + 5 后端编译 |
| `tests/test_openxsim_conformance_pipeline.py` | 锁一致性分级 + 工作流编排 |
| `tests/test_openxsim_assets.py` | 锁 AssetScout 搜/下/注册 |
| `tests/test_openxsim_anchors.py` | 锁证据抽取与融合 |
| `tests/test_env_gen_import.py` | 锁 env-gen 导入保真与报错（fixtures 在 `tests/fixtures/env_gen/`） |

**运行参数配置**：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--output` | 所有工作流的产物根目录（子命令前给） | `<项目根>/artifacts/openxsim/` |
| `PYTHONPATH` | 三条：`source/agenticsim` + `deps/metasim_core` + `third_party/MetaSim` | 见「运行前置」 |

**一键运行**（全量测试）：

```bash
python -m pytest $OX/tests -q   # 预期 42 passed
```

## 5. 关键概念 / 术语

- **IR / `EnvironmentPackage`**：本项目的中间表示——typed、模拟器中立的环境包；任务语义在包里，USD/MJCF 等只是资产的 representation。
- **`AssetBundle` / `AssetRepresentation`**：一个资产 + 它的多格式表示（usda/mjcf/urdf/…），各带 uri 与 sha256。
- **`AnchorSpec`**：从图像/视频抽出的布局证据锚点，融合进 IR。
- **MJCF**：MuJoCo 的 XML 场景格式。**USDA**：USD 的文本格式，IsaacSim 用。**SAPIEN**：上游 env-gen 所用的物理仿真器。
- **MetaSim**：RoboVerse 的多后端仿真框架，vendored 在 `third_party/`，openxsim 靠它对接 Isaac 等。
- **L0-L4**：一致性五级——L0 asset_import（产物能导入）/ L1 scene_structure（场景结构一致）/ L2 task_semantics（任务契约与运行证据一致）/ L3 trajectory_replay（轨迹重放一致）/ L4 policy_behavior（策略成功率差在容差内）。
- **task contract**：嵌在环境里的任务契约 JSON（action / observation / reset / success / termination），L2 比对的对象。
- **compile manifest**：每次后端编译落盘的结果清单（产物路径、blocker），也可反向作导入入口。
- **`resolved_scene.json`**：上游 env-gen 场景包的落地文件（物体、位姿、物理、资产引用），经 `env_gen.py` 进 IR。
- **rollout**：一次策略在环境里跑完的完整执行记录（RoboTwin 侧产出报告与视频），robotwin-evidence 的校验对象。
- **vendored**：第三方代码直接放进本仓固定版本使用（不装包）；本项目 vendored 件不入 git，见 `UPSTREAM.md`。
- **blocker**：编译/规划中「后端或资产表达不了」的硬阻碍记录；`--strict` 下有 blocker 即失败。

## 6. 依赖 / 来历

- **来历**：2026-08-02 自 lv-5090 `~/workspace/openxsim-validation`（非 git 目录）整树快照搬入，内部约定原样保留——详见 `UPSTREAM.md`。
- **vendored 依赖（不入 git，本地已在）**：`third_party/MetaSim/`（RoboVerse MetaSim）、`deps/metasim_core/`；fresh clone 后按 `UPSTREAM.md` 恢复。
- **上游数据（引用不改）**：env-gen（`robot-harness-gen-env`）的 `resolved_scene.json`，源仓软链在 `../../external/env-gen-github/`。
- **消费方**：`1_asset_reuse/`（用 `AssetBundle` IR 做资产校验，见其 `scripts/s5_check_ir.py`）与 `2_sim_migration/`（迁移主线）；两阶段用法细节见 `../../2_sim_migration/README.md`。
- **运行环境**：conda `env-gen-yuxin`（lv-5090）。
