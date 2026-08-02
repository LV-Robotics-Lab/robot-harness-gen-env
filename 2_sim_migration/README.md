# 2_sim_migration — 阶段 2 · 仿真环境迁移

> 让 env-gen 产出的场景包能在 **SAPIEN 之外的仿真后端**（如 Isaac）运行，做成 **adapter 层**、不改上游。**当前为脚手架骨架，尚无实现代码**——具体后端与适配方案在 brainstorm 设计后填入，设计见 `../docs/design.md`。

## 1. 这是什么 / 目标
- **输入**：env-gen 的 `resolved_scene.json`（后端中立的场景图纸）。
- **输出**：待设计（目标后端的加载/运行适配 + 迁移验证）。
- **目标**：把"一份图纸、多后端加载"落地——上游图纸不变，新增其它后端的 adapter。

## 2. 里面有什么
| 路径 | 作用 |
|---|---|
| `lib/` | 本阶段 adapter 模块（待实现） |
| `sN_*.py` | 步骤脚本（待实现） |

## 3. 怎么用
> 待实现后补可复制命令。

## 4. 关键概念 / 术语
- **adapter（适配层）**（把后端中立的 `resolved_scene.json` 翻译成某个具体仿真后端的加载/运行调用）。
- **后端中立图纸**（`resolved_scene.json` 是纯数据、不含后端代码，天然适合多后端加载）。

## 5. 来历 / 依赖
- **只读依赖**：`../external/env-gen-github`（上游 `scene_gen/envs/generated_scene.py` 是 SAPIEN 加载参照，引用不改）。
