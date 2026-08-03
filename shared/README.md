# shared — 跨阶段共享代码 / 资源

> 两个阶段（资产复用 / 仿真迁移）**共用**的代码放这里：adapter 基类、共享 schema、通用工具、跨阶段测试。**openxsim 已入驻（2026-08-03 自 `2_sim_migration/` 移入），其余按需填入。**

## 1. 这是什么
- 供 `1_asset_reuse/` 与 `2_sim_migration/` 共同 import 的模块，避免各阶段重复造轮子。
- 只放**真正被 ≥2 处消费**的东西；单阶段专用的留在各自 `lib/`。

## 2. 里面有什么
| 路径 | 作用 |
|---|---|
| `openxsim/` | 跨仿真器迁移引擎（后端中立 IR `EnvironmentPackage` + 多后端编译 + L0-L4 一致性；`2_sim_migration` 用其迁移工作流、`1_asset_reuse` 用其 AssetBundle IR。用法/坑见 `../2_sim_migration/README.md`，vendored 依赖恢复见 `openxsim/UPSTREAM.md`） |
| `lib/` | 共享模块（待实现） |
| `schemas/` | 共享数据结构（待实现，如需） |
| `tests/` | 跨阶段测试（待实现，如需） |

## 3. 来历 / 依赖
- **只读依赖**：`../external/env-gen-github`（上游 scene_gen，引用不改）。
