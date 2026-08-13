# robot-harness-gen-env (`/gen-env`) · 项目全景

> 一句话定位：把一句自然语言场景描述（中/英）**确定性地编译成"物理已验证、可复现"的 RoboTwin 桌面场景包**——不生成机器人任务代码、不训练策略，只负责"造出一个物理上站得住、相机看得见、可哈希溯源的初始场景"。

## 1. 这是什么

本项目是更大的 "Robot Harness" 系统的 **`/gen-env` 子系统**。它把 `"Place a can on top of a plate."` 这样的一句话，经过**解析 → 资产落地 → 支撑/容纳求解 → 打包 → SAPIEN 物理回放 → 多重验证门**，产出一个可被 RoboTwin 下游任务直接加载的场景包。

解决的问题：机器人操作要海量场景/演示数据，但"手写场景脚本"慢、"让大模型自由生成"又不稳定、不可复现。本项目用**确定性规则 + 物理引擎验证**替代大模型生成，保证：同一输入永远同一输出（seed 固定）、每个产物哈希绑定可溯源、且"物理上真的站得住"（丢进 SAPIEN settle 后不倒/不穿模/不掉出）。

**明确不做**：机器人任务策略 `play_once()`、数据采集、训练、评测、sim-to-real 迁移。这些是下游 RoboTwin / 训练层的事。

## 2. 整体 输入 → 输出

- **输入**：
  - 自然语言场景 prompt（中/英，桌面摆放类）
  - RoboTwin 资产库（`external/RoboTwin/assets/objects`，引用不改）
  - 随机种子 `seed`（决定布局，保证可复现）
- **输出**（每个通过的场景包，落在 `data/generated_scenes/<scene_id>/`）：
  - `resolved_scene.json`——完整解析场景（物体身份/位姿/关系/关节）
  - `generated_scene.py`——SAPIEN 回放入口（内嵌 JSON + SHA-256）
  - `package_manifest.json`——逐文件 SHA-256 清单（防篡改）
  - `validation_report.json`——静态验证报告
  - 物理回放产物（`data/runtime/<scene_id>/`）：`runtime_evidence.json` + 预览图 + `observer_runtime.mp4`
- **关键中间产物**（跨阶段流转）：
  `asset_catalog.json`（资产目录）→ `scene_spec.json`（类型化 SceneSpec）→ `resolved_scene.json`（ResolvedSceneSpec）→ `runtime_evidence.json`（物理证据）

## 3. 阶段总览

代码按模块组织（`scene_gen/` 库 + `script/` 驱动 + `demo/` 网页），**功能阶段横跨这些目录**：

| 阶段 | 职责（一句话） | 对应文件 |
|---|---|---|
| 1 · 资产目录 | 扫 RoboTwin 资产库，生成可用资产目录 | `scene_gen/catalog.py`, `asset_overrides.yml` |
| 2 · 场景编译 | 文本→类型规格→资产落地→位姿求解→哈希打包（含静态验证） | `script/generate_scene.py` + `scene_gen/{parser,schema,grounding,asset_generator,solver,support_geometry,builder}.py` |
| 3 · 物理回放 | 把场景包丢进 SAPIEN 物理 settle，出证据+视频 | `script/run_scene_runtime.py` + `scene_gen/envs/generated_scene.py` |
| 4 · 多重验证 | 静态几何 + 运行时物理证据 → 通/不通判定 | `scene_gen/validator.py` |
| 5 · VLM 批判（可选） | 视觉模型看渲染图判语义 | `script/run_rendered_critic.py` + `scene_gen/rendered_critic.py` |
| 6 · 批量验收 & Demo | 矩阵/多 seed 批量跑；浏览器交互 | `script/run_{prompt_matrix,100_seed_acceptance}.py`, `demo/app.py` |

```
prompt(中/英) ─┐
RoboTwin 资产 ─┴▶ 1资产目录 ─▶ asset_catalog.json
                                    │
prompt + seed ──▶ 2编译(解析→落地→求解→打包) ─▶ 场景包(resolved_scene.json + 哈希)
                                    │
                          3物理回放(SAPIEN) ─▶ runtime_evidence.json + 视频
                                    │
                          4多重验证门 ─▶ validation_report(pass/fail)
                                    │(可选)
                          5VLM 批判 ─▶ rendered_critic.json
```

## 4. 分阶段详解

### 阶段 1 · 资产目录构建
- **目标**：扫描 RoboTwin 资产库，抽取每个物体的尺寸、稳定朝向、支撑面、容器内腔、关节限位，生成一份"哪些资产可用"的目录。缺元数据（如缺 stable_pose）的标为不可用。
- **对应文件**：`scene_gen/catalog.py`（+ `scene_gen/asset_overrides.yml` 手工补丁）
- **输入 → 输出**：`RoboTwin/assets/objects/` → `data/scene_gen/asset_catalog.json` + `missing_assets.json`

**文件清单**：

| 文件 | 功能 |
|---|---|
| `scene_gen/catalog.py` | 扫资产、解析元数据、判可用性，产出 catalog + missing 诊断，`python -m` 入口 |
| `scene_gen/asset_overrides.yml` | 对个别资产的人工覆盖/修正（补 stable_pose、替换不稳资产等）|

**运行参数配置**：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--robotwin-root` | RoboTwin 仓库根 | 必填 |
| `--overrides` | 覆盖规则 yml | `scene_gen/asset_overrides.yml` |
| `--source-commit` | 记录资产来源 commit（溯源） | 建议填 `git -C <RT> rev-parse HEAD` |
| `--out` / `--missing-out` | 目录 / 缺失诊断 输出路径 | 必填 |

**一键运行**：
```bash
python -m scene_gen.catalog --robotwin-root "$RT" \
  --overrides scene_gen/asset_overrides.yml \
  --source-commit "$(git -C "$RT" rev-parse HEAD)" \
  --out data/scene_gen/asset_catalog.json \
  --missing-out data/scene_gen/missing_assets.json
```

### 阶段 2 · 场景编译（解析 → 落地 → 求解 → 打包）
- **目标**：一句话 → 一个哈希绑定的场景包。一个命令内跑完解析、资产落地、位姿求解、打包、静态验证。
- **对应文件**：`script/generate_scene.py`（驱动）+ 下方 `scene_gen/` 模块
- **输入 → 输出**：prompt + seed + `asset_catalog.json` → `data/generated_scenes/<scene_id>/`（含 `resolved_scene.json`）

**文件清单**（按流程顺序）：

| 文件 | 功能 |
|---|---|
| `scene_gen/prompts/parse_scene.md` | 解析提示词（供 LLM 解析变体用；发布版默认走规则解析） |
| `scene_gen/parser.py` | 规则解析：文本 → 类型化 SceneSpec（`parse_rule_based`，不依赖 LLM） |
| `scene_gen/schema.py` | 全部数据结构定义：SceneSpec / ResolvedSceneSpec / 关系 / 位姿（pydantic，含哈希 `digest()`） |
| `scene_gen/grounding.py` | 资产落地：把物体词映射到目录里的真实资产（打分选型） |
| `scene_gen/asset_generator.py` | 目录缺失时生成"几何代理 proxy"（程序生成 / 缩放派生），带来源血缘 |
| `scene_gen/colors.py` | 颜色名 → RGB 映射 |
| `scene_gen/solver.py` | 支撑/容纳求解：拒绝采样 + 回溯，算出每个物体的位姿；也有独立 `main()` 入口 |
| `scene_gen/support_geometry.py` | 求解与验证共用的几何：脚印、支撑边距、容纳采样 |
| `scene_gen/builder.py` | 打包 ResolvedSceneSpec → 落盘 + 逐文件 SHA-256 清单 + 回放入口脚本 |
| `scene_gen/envs/generated_scene.py` | 回放入口：按图纸在 SAPIEN 里 spawn 物体（阶段 3 调用） |

**运行参数配置**（`script/generate_scene.py`）：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--prompt` | 场景描述（中/英） | 必填 |
| `--seed` | 随机种子（决定布局，可复现关键开关） | `0` |
| `--asset-catalog` | 阶段 1 产出的目录 | 必填 |
| `--out-root` | 场景包输出根 | `data/generated_scenes` |
| `--generate-missing-assets` | 开则对目录缺失的物体生成 proxy | 关（`store_true`） |
| `--generated-objects-root` | proxy 落盘根（配合上一项） | 无 |

> 静态验证结果 `incomplete`（`fail=0, not_run=1`）是**正常**——`not_run` 只表示"物理那一项还没跑"。只要 `fail=0` 即静态全过。

**一键运行**：
```bash
python script/generate_scene.py --prompt "Place a can on top of a plate." \
  --seed 42 --asset-catalog data/scene_gen/asset_catalog.json \
  --out-root data/generated_scenes
```

### 阶段 3 · 物理回放
- **目标**：把场景包丢进 SAPIEN，让物体自由下落 settle，采集"真实发生了什么"（接触/穿模/位姿/可见性）+ 渲染预览图与视频。这是"物理已验证"这句话的底气。
- **对应文件**：`script/run_scene_runtime.py`（驱动）+ `scene_gen/envs/generated_scene.py`（spawn）+ `scene_gen/runtime_sampling.py`（采样）
- **输入 → 输出**：`resolved_scene.json` + `asset_catalog.json` → `data/runtime/<scene_id>/`（`runtime_evidence.json` + 预览图 + `observer_runtime.mp4`）

**文件清单**：

| 文件 | 功能 |
|---|---|
| `script/run_scene_runtime.py` | 建一个继承 RoboTwin `Base_Task` 的运行时，加载场景、settle、读接触/位姿、渲染分割图与视频、写证据 |
| `scene_gen/envs/generated_scene.py` | `load_resolved_scene`：按 resolved 图纸调 RoboTwin `create_actor` spawn 物体、上色、驱动关节 |
| `scene_gen/runtime_sampling.py` | 视频抽帧步序等运行时采样小工具 |

**运行参数配置**：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--robotwin-root` | RoboTwin 根（脚本会 `chdir` 进去，故其余路径要**绝对**） | 必填 |
| `--resolved-scene` | 阶段 2 的 `resolved_scene.json`（绝对路径） | 必填 |
| `--out-dir` | 物理产物输出（绝对路径） | 必填 |
| `--settle-steps` | 物理稳定步数（复杂场景需大） | `900` |
| `--contact-window-steps` | 末尾统计"支撑接触占比"的窗口 | `60` |
| `--video-frames` / `--fps` | 视频帧数 / 帧率 | `120` / `12` |
| `--min-visible-pixels` | 判"可见"的最小分割像素数 | `64` |
| `--precheck-steps` | spawn 后预检步数（0=从释放起采） | `0` |

**一键运行**：
```bash
python script/run_scene_runtime.py --robotwin-root "$RT" \
  --resolved-scene "$REPO/data/generated_scenes/$SCENE/resolved_scene.json" \
  --asset-catalog  "$REPO/data/scene_gen/asset_catalog.json" \
  --out-dir "$REPO/data/runtime/$SCENE" \
  --settle-steps 900 --contact-window-steps 120 --video-frames 120 --fps 12
```

### 阶段 4 · 多重验证门
- **目标**：把静态几何检查与运行时物理证据合起来，给出通/不通判定。两层：静态（不用仿真器，永远跑）+ 运行时（有物理证据才跑）。
- **对应文件**：`scene_gen/validator.py`
- **输入 → 输出**：`resolved_scene.json` (+ `runtime_evidence.json`) → `validation_full.json`（`status=pass/incomplete/fail`）

**文件清单**：

| 文件 | 功能 |
|---|---|
| `scene_gen/validator.py` | 静态门（工作区边界、支撑高度、无重叠、关系成立、包哈希）+ 运行时门（穿模/静止/接触占比/无意外接触/可见/漂移/关节），`python -m` 入口 |

**运行参数配置**：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--resolved-scene` | 待验证场景 | 必填 |
| `--runtime-evidence` | 阶段 3 的物理证据 | 无（不给则运行时门 `not_run`） |
| `--require-runtime` | 强制要求物理证据（缺则判 fail） | 关（`store_true`） |
| `--package-root` | 场景包目录（校验清单哈希） | 无 |
| `--out` | 报告输出 | 必填 |

**一键运行**：
```bash
python -m scene_gen.validator \
  --resolved-scene "data/generated_scenes/$SCENE/resolved_scene.json" \
  --asset-catalog  "data/scene_gen/asset_catalog.json" \
  --package-root   "data/generated_scenes/$SCENE" \
  --runtime-evidence "data/runtime/$SCENE/runtime_evidence.json" \
  --require-runtime --out "data/runtime/$SCENE/validation_full.json"
```

### 阶段 5 · VLM 渲染批判（可选）
- **目标**：让一个本地视觉语言模型看渲染图，做几何/物理数字看不出来的**语义**判断（物体在不在、支撑对不对、有没有悬浮/穿模、关节状态、整体是否符合 prompt）。
- **对应文件**：`script/run_rendered_critic.py`（驱动）+ `scene_gen/rendered_critic.py`（逻辑）
- **输入 → 输出**：`resolved_scene.json` + 渲染 PNG → `rendered_critic.json`

**文件清单**：

| 文件 | 功能 |
|---|---|
| `scene_gen/rendered_critic.py` | 构造批判契约、调本地 Qwen2.5-VL 推理、规整输出（5 项必检 + 契约修复） |
| `script/run_rendered_critic.py` | CLI 驱动，喂多张预览图跑批判 |

**运行参数配置**：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--resolved-scene` | 场景 | 必填 |
| `--image` | 渲染图（可多次传） | 必填 |
| `--provider` | 推理后端 | `qwen_local`（唯一内置） |
| `--model` | VLM 模型 | `Qwen/Qwen2.5-VL-3B-Instruct` |
| `--out` | 输出 | 必填 |

> 需先装 `pip install -e '.[vlm]'`（transformers / qwen-vl-utils / accelerate）。

### 阶段 6 · 批量验收 & 浏览器 Demo
- **目标**：把单场景流程放大——按 prompt 矩阵或多 seed 批量跑做统计验收（对齐官方证据），或用网页交互式生成。
- **对应文件**：`script/run_prompt_matrix.py`, `script/run_100_seed_acceptance.py`, `scene_gen/acceptance.py`, `script/build_stage5_report.py`, `demo/`
- **输入 → 输出**：prompt 矩阵 / 单 prompt+多 seed → 批量场景包 + `report.json`（含通过率）

**文件清单**：

| 文件 | 功能 |
|---|---|
| `script/run_prompt_matrix.py` | 跑一组 prompt（可加 `--runtime` 含物理），汇总报告；对齐 README 的 33/33 |
| `script/run_100_seed_acceptance.py` | 同一 prompt 跑 N 个 seed 做稳健性验收，按最低通过率判定 |
| `scene_gen/acceptance.py` | 验收统计的公共逻辑 |
| `script/build_stage5_report.py` | 从跑完的 bundle 汇总生成阶段报告 |
| `demo/app.py` | Flask 网页：输 prompt+seed，后台跑 pipeline、管 GPU 作业队列、展示产物 |
| `demo/static/{index.html,app.js,styles.css}` | 前端页面/逻辑/样式 |

**运行参数配置**（`run_prompt_matrix.py` 关键项）：

| 参数 | 含义 | 默认 / 取值 |
|---|---|---|
| `--matrix` | prompt 矩阵 json | 如 `tests/fixtures/prompt_matrix.json`（11 prompt × seeds） |
| `--runtime` | 是否含物理回放 | 关（`store_true`）；开则需 `--robotwin-root` |
| `--generated-objects-root` | proxy 落盘根 | 必填 |
| `--out-root` / `--report` | 输出根 / 报告 | 必填 |

**Demo 环境变量**（`demo/app.py`）：`ROBOTWIN_ROOT` / `ROBOTWIN_PYTHON` / `SCENE_ASSET_CATALOG` / `SCENE_DEMO_JOBS_ROOT` / `SCENE_VLM_MODEL`；`--host`（默认 `0.0.0.0`）`--port`（默认 `8765`）。

**一键运行**：
```bash
python script/run_prompt_matrix.py --matrix tests/fixtures/prompt_matrix.json \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --generated-objects-root "$RT/assets/objects" \
  --out-root data/prompt_matrix --report data/prompt_matrix/report.json \
  --runtime --robotwin-root "$RT"
```

## 5. 关键概念 / 术语

- **RoboTwin**：开源双臂机器人操作仿真平台（建在 SAPIEN 上），既是数据生成器也是 benchmark；本项目产出的场景包供它加载。资产库与运行时（`envs` 包）都来自它。
- **SAPIEN**：UCSD 的物理仿真+渲染引擎（底层 PhysX + Vulkan 渲染），本项目的物理回放与出图都靠它；需 Linux + NVIDIA GPU。
- **SceneSpec / ResolvedSceneSpec**：前者是"解析出的类型化需求"（谁-什么关系-谁），后者是"落地后的完整施工图纸"（含具体资产/位姿/关节），都在 `schema.py`。
- **资产落地（grounding）**：把物体词（can）映射到目录里真实存在的资产（`071_can`）。
- **几何代理（proxy）**：目录里没有对应资产时，程序生成的几何替身（棱柱/长方体），或对现成资产等比缩放的派生版，带来源血缘可溯源。
- **支撑/容纳求解**：给"A 放在 B 上/里"算出真实位姿，用拒绝采样+回溯保证稳、不重叠、不压机器人禁区。
- **哈希绑定（hash-bound）**：resolved 场景与其来源 SceneSpec 用 SHA-256 焊死、包内逐文件盖章，保证可复现、防篡改、可溯源。
- **多重验证门**：静态几何检查（程序算）+ 运行时物理门（读引擎接触/位姿 + 数分割图像素），最终判 pass/incomplete/fail。
- **VLM**：视觉语言模型；本项目用本地 `Qwen2.5-VL-3B-Instruct` 做可选的语义批判（只看图，不参与物理判定）。
- **settle**：物理引擎让物体自由演化直到静止的过程（默认 900 步）。

## 6. 依赖 / 来历

- **资产来源**：RoboTwin 资产库（`external/RoboTwin/assets/objects`，134 物体，**引用不改**）；资产目录由阶段 1 从中扫描生成。
- **外部运行时依赖**：
  - **SAPIEN**（物理+渲染，pip 可装）+ **RoboTwin `envs` 包**（阶段 3 运行时 `import`，含机器人栈/`curobo`，**必须有 RoboTwin 完整仓库**）。
  - **torch（Blackwell/RTX 5090 需 cu128）**、numpy/scipy/trimesh/open3d/imageio/h5py 等（RoboTwin 依赖长尾）。
  - **Qwen2.5-VL-3B**（可选，阶段 5）。
- **上下游**：**上游**——本项目是 "Robot Harness" 的 `/gen-env` 子系统，产物交给其命令循环；**下游**——RoboTwin 及外部策略消费这些场景包做数据生成/评测。
- **License**：Apache-2.0。
- **复现细节与踩坑**：见项目所在机器 `/home/jingxiang/yuxin/RUNBOOK.md`（环境/命令/踩坑速查）。
