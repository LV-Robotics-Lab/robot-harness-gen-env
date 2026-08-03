# 2_sim_migration — 阶段 2 · 仿真环境迁移

> 让场景能在 **SAPIEN 之外的仿真后端**运行。本阶段的**迁移引擎 = openxsim**（Open-X-Sim）：一套**后端中立 IR**（`EnvironmentPackage`）+ 多后端编译 + 跨模拟器一致性测试，代码在 `../shared/openxsim/`（2026-08-03 自本目录移入 `shared/`，因阶段 1 也消费它）并跑通自带测试。**与 env-gen 的桥接（把 env-gen 的 `resolved_scene` 喂进 openxsim IR）是后续开发**，见 `../docs/design.md`。

## 1. 这是什么 / 目标
- **输入**：文本 / 已有仿真环境 / 资产 →（编译进）后端中立 IR。
- **输出**：多个仿真后端的可运行环境 + L0-L4 一致性评估。
- **目标**：一份中立图纸、多后端加载；env-gen 的场景最终经此迁到 Isaac 等后端。

## 2. openxsim 提供的 5 个工作流（`../shared/openxsim/scripts/openxsim.py`）
| 子命令 | 作用 |
|---|---|
| `text2env` | 纯文本编译进 IR |
| `anchor2env` | 文本 + 图像/视频证据编译 |
| `asset-scout` | 搜索/下载/转换/注册公开资产 |
| **`transfer`** | **导入一个已有环境，编译到另一个后端**（迁移核心） |
| `robotwin-evidence` | 校验 RoboTwin rollout 与其包/任务程序 |

## 3. 里面有什么
| 路径 | 作用 |
|---|---|
| `../shared/openxsim/source/agenticsim/agenticsim/openxsim/` | 引擎源码：`ir.py`(中立 IR) `backends.py`(多后端编译) `robotwin.py`(RoboTwin 适配) `conformance.py`(L0-L4 一致性) `importers.py` `text2env.py` `pipeline.py` `assets.py` |
| `../shared/openxsim/scripts/openxsim.py` | CLI 入口（自定位 `source/agenticsim`） |
| `../shared/openxsim/configs/` `../shared/openxsim/tests/` | 配置 / 测试（36 passed） |
| `../shared/openxsim/third_party/MetaSim/` | vendored 多后端仿真框架（含 IsaacSim 工具）。**不入 git**，见 `../shared/openxsim/UPSTREAM.md` |
| `../shared/openxsim/deps/metasim_core/` | vendored 依赖。**不入 git** |
| `lib/` | 预留：env-gen ↔ openxsim 的桥接 adapter（待开发） |

## 4. 怎么用
```bash
conda activate env-gen-yuxin
OX=/home/jingxiang/yuxin/env-gen-dev/shared/openxsim
export PYTHONPATH="$OX/source/agenticsim:$OX/deps/metasim_core:$OX/third_party/MetaSim:$PYTHONPATH"
python $OX/scripts/openxsim.py --help              # 5 个工作流
python -m pytest $OX/tests -q                       # 预期 36 passed
```

## 5. 关键概念 / 术语
- **openxsim / Open-X-Sim**（跨仿真器迁移引擎；核心是后端中立 IR + 多后端编译 + 一致性测试）。
- **EnvironmentPackage（IR）**（typed、simulator-neutral 的中间表示；把任务语义与后端资产格式 USD/MJCF/URDF/SAPIEN 分离）。
- **MetaSim**（vendored 多后端仿真框架，openxsim 靠它对接 Isaac 等；见 `../shared/openxsim/UPSTREAM.md`）。
- **conformance L0-L4**（跨模拟器一致性的分级评估）。

## 6. 坑与注意
- 跑 openxsim 需把 `source/agenticsim`、`deps/metasim_core`、`third_party/MetaSim` 加进 `PYTHONPATH`（见上）。
- `third_party/` 与 `deps/` **不入 git**（本地已存在、可跑）；**fresh clone 需按 `../shared/openxsim/UPSTREAM.md` 重新填充**才能跑。
- **openxsim 目前独立于 env-gen**（有自己的 text2env/IR，不读 env-gen 的 `resolved_scene.json`）；打通两者是本阶段待开发的桥接。

## 7. 来历 / 依赖
- **引擎来历**：从 `/home/jingxiang/workspace/openxsim-validation` 搬入（2026-08-02），详见 `../shared/openxsim/UPSTREAM.md`。
- **只读依赖**：`../external/env-gen-github`（上游 env-gen；桥接时作源，引用不改）。
