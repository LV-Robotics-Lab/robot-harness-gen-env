# 1_asset_reuse — 阶段 1 · 资产复用

> 在 env-gen 之上，提升 RoboTwin 资产在场景生成中的**复用**。**当前为脚手架骨架，尚无实现代码**——本阶段的具体机制会在 brainstorm 设计后填入，设计见 `../docs/design.md`。

## 1. 这是什么 / 目标
- **输入**：env-gen 的资产目录 `asset_catalog.json` + RoboTwin 资产库。
- **输出**：待设计（复用索引 / 缓存 / 跨场景共享等，brainstorm 定）。
- **目标**：减少重复落地/生成，提升资产在多场景间的复用效率。

## 2. 里面有什么
| 路径 | 作用 |
|---|---|
| `lib/` | 本阶段模块（待实现） |
| `sN_*.py` | 步骤脚本（按流程顺序命名，待实现） |

## 3. 怎么用
> 待实现后补可复制命令。运行环境同项目根：`conda activate env-gen-yuxin`。

## 4. 来历 / 依赖
- **只读依赖**：`../external/env-gen-github`（上游 scene_gen，引用不改）。
