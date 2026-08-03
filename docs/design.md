# env-gen-dev — 设计文案 (design.md)

> 本文是 env-gen-dev 的主设计文案。目标：在 `robot-harness-gen-env`（env-gen）之上做**资产复用**与**仿真环境迁移**，不改上游。
> - 根路径：`/home/jingxiang/yuxin/env-gen-dev`
> - 状态：**设计中**（两任务的具体机制待 brainstorm 细化，未定处标 ⏳）
> - 名词：**env-gen**＝上游子系统（文本→物理已验证 RoboTwin 场景包）；**adapter**＝把后端中立图纸翻译成具体仿真后端调用的层。

## 1. 这是什么 & 目标
在**不修改上游一行**的前提下扩展 env-gen：
- **任务 1 · 资产复用**：提升 RoboTwin 资产在场景生成中的复用效率。⏳ 具体机制（复用索引 / 缓存 / 跨场景共享 / proxy 复用…）待 brainstorm。
- **任务 2 · 仿真环境迁移**：让 env-gen 的场景包能在 SAPIEN 之外的仿真后端运行。**迁移引擎已就位 = openxsim**（后端中立 IR + 多后端编译 + 一致性测试，搬入 `shared/openxsim/`，36 测试通过）。⏳ 剩余：把 env-gen 的 `resolved_scene` 桥接进 openxsim IR + 选定首个目标后端。

## 2. 设计原则 & 关键约束
- **上游只读、加新层**：env-gen 作为外部只读依赖（`external/env-gen-github`，pristine 克隆，`git pull` 同步）；新代码只 import/adapter 引用，绝不改上游。
- **后端中立复用**：优先复用上游已有的后端中立产物（`resolved_scene.json` 纯数据图纸）作为迁移与复用的接口。
- **可复现 & 可同步**：新项目独立成 git 仓；上游随时可 pull 而不冲突。

## 3. 数据源 / 输入
- env-gen 的 `asset_catalog.json` / `resolved_scene.json` / RoboTwin 资产库（`external/env-gen-github` + `env-gen-yuxin` conda 环境提供）。

## 4. 方案详解（分步 / 组件）
⏳ 待 brainstorm 后填：
- 任务 1 资产复用：组件划分、复用键设计、与上游 grounding/catalog 的接口。
- 任务 2 仿真迁移：**引擎 openxsim 已搬入**（`shared/openxsim/`，详见其 README/UPSTREAM）；待设计的是 **env-gen→openxsim 桥接**（`resolved_scene` → openxsim IR）、首个目标后端、迁移一致性验证口径。

## 5. 交付规范 / 输出
- 代码落各阶段 `lib/` + `sN_` 步骤；运行产物落 `results/<YYYYMMDD>_<目的>/`。

## 6. 坑与注意
- 迁移的"物理验证"**跨后端不等价**（不同引擎接触/求解不同）——每个后端的验证只对该后端成立（见此前 SAPIEN vs Isaac 讨论）。
- 别把上游 pristine 克隆污染成工作目录。

## 7. 来历 / 依赖 + ⏳ 待办
- 依赖：`robot-harness-gen-env`（引用不改）、SAPIEN/RoboTwin、torch cu128。
- ⏳ 待办：对两任务分别 brainstorm → 填第 4 节 → 写 `plan.md` 的 Task 步骤 → 实现。
