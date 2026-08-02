# env-gen-dev — 在 `robot-harness-gen-env` 上做资产复用 + 仿真环境迁移

> 本项目**不改上游**，只在其上加新层：把 LV-Robotics-Lab 的 `robot-harness-gen-env`（`/gen-env`，文本→物理已验证的 RoboTwin 场景包）当**外部只读依赖**，在它之上做两件事——**资产复用**与**仿真环境迁移**。

## 1. 这是什么 / 目标
- **输入**：env-gen 产出的场景包 / 资产目录 + RoboTwin 资产库。
- **输出**：两个新能力的代码与产物（见下两个阶段）。
- **目标**：在不修改 upstream 一行的前提下，扩展 env-gen —— 复用资产、把场景迁到 SAPIEN 之外的仿真后端。

## 2. 为什么存在
env-gen 是一个已发布、结构固定的上游子系统；直接改它会破坏「随时 `git pull` 同步上游」。本项目用**外部只读依赖 + 加新层**的架构，两全其美：上游可同步，新开发独立可控。

## 3. 里面有什么
| 路径 | 作用 |
|---|---|
| `external/env-gen-github/` | **软链**到上游 pristine 克隆（`/home/jingxiang/yuxin/env-gen-github`）；只读、可 `git pull` 同步。**不入 git** |
| `1_asset_reuse/` | 阶段 1 · 资产复用（`lib/` 放本阶段模块） |
| `2_sim_migration/` | 阶段 2 · 仿真环境迁移（SAPIEN→其他后端 adapter） |
| `shared/` | 跨阶段共享（adapter 基类 / schema / tests） |
| `data/` | 输入 / 资产（大文件用软链，**不入 git**） |
| `results/` | 每次运行结果 `results/<YYYYMMDD>_<目的>/`（**不入 git**） |
| `docs/design.md` | 设计文案（架构 + 两任务目标） |
| `docs/plan.md` | 分步实现计划 + 全量前预检门埋点 |
| `docs/env-setup/` | 运行环境搭建参考（RUNBOOK / lock 文件，若归位到此） |

## 4. 怎么用
**运行环境**：复用已建好的 conda 环境 `env-gen-yuxin`（含 torch cu128 / sapien 3.0.3 / scene_gen 可编辑安装）。
```bash
conda activate env-gen-yuxin          # scene_gen 已 editable 安装，import 即用
python -c "import scene_gen; print(scene_gen.__file__)"   # 应指向 external 那份上游
```
**同步上游**（env-gen 有新提交时）：
```bash
cd external/env-gen-github && git pull   # 该目录正是 conda 环境 editable 指向的源，pull 即生效
```

## 5. 关键概念 / 术语
- **env-gen / `robot-harness-gen-env`**（上游子系统：文本→物理已验证的 RoboTwin 场景包；本项目的只读依赖）。
- **外部只读依赖（引用不改）**（上游代码保持 pristine，本项目只 import/adapter 引用它，绝不修改）。
- **资产复用**（阶段 1；提升 RoboTwin 资产在场景生成中的复用，具体机制见 `docs/design.md`）。
- **仿真环境迁移**（阶段 2；让场景包能在 SAPIEN 之外的仿真后端运行，做成 adapter 层）。

## 6. 坑与注意
- **别改 `external/` 里的上游代码** —— 一改就破坏 `git pull` 同步；要改逻辑走 fork/PR，不在这里。
- `external/` 是**软链、不入 git**：别人 clone 本仓后需自建（`git clone <env-gen> external/env-gen-github`，见 `docs/env-setup/`）。
- 运行产物一律落 `results/<YYYYMMDD>_<目的>/`，别塞进 `external/` 或项目根。

## 7. 来历 / 依赖
- **上游只读依赖（引用不改）**：`robot-harness-gen-env`（GitHub: https://github.com/LV-Robotics-Lab/robot-harness-gen-env ）。
- **运行时依赖**：SAPIEN / RoboTwin `envs`（含 curobo）/ torch cu128 —— 详见 `docs/env-setup/RUNBOOK.md`。
- **本项目 GitHub 仓库**：`<建库后填 URL>`。
