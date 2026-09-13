# archive/1_forward_convert/ — 线 A 正向转换（RoboTwin → Isaac USD）· 已归档

**归档日期**：2026-08-10 ｜ **状态**：只留档，不再维护、不再被任何入口调用。

## 这是什么

把 RoboTwin 私有布局资产（GLB / URDF）正向转成 Isaac Sim 可直接加载的 USD，并用
「Isaac 侧 + SAPIEN 侧」双后端静置验证证明物理不走样，最后把 `isaacsim` 表示注册进账本。

成功标志是 openxsim `representation_for("isaacsim")` 命中 —— 该结论已达成并留档，
是 Phase 2 · 4.7 Transfer 资产侧阻塞消除的依据。

打样样本：`001_bottle`（刚体）、`036_cabinet`（关节体）。

## 为什么归档

线 A 与项目当前主线**方向相反**。主线（线 B/C/D）是「外部资产 → RoboTwin 布局」的
线性引入管线，可按 `1_search → 2_convert → 3_materialize → 4_validate → 5_catalog`
编号；线 A 是反方向的独立能力，塞不进这个序列，留在 `scripts/` 里会让目录出现两个
互相打架的组织维度。其阶段性目标（Transfer 阻塞消除）已完成，无后续需求。

## 逐文件

| 文件 | 功能 | 运行环境 |
|---|---|---|
| `robotwin_asset.py` | 读 RoboTwin 私有布局 → AssetBundle JSON（sapien 侧表示） | env-gen-yuxin |
| `s1_convert_rigid.py` | 刚体 GLB→USD + 物理装配（凸分解碰撞、烘焙缩放 0.05 与 Y-up→Z-up 旋转） | isaac-smoke |
| `s2_convert_articulated.py` | URDF→USD 关节体导入 + USD 关节 prim 与 URDF 声明核对 | isaac-smoke |
| `s4_validate_isaac.py` | Isaac 侧转换产物静置验证（位移/倾角/穿模/关节有限性） | isaac-smoke |
| `s6_verdict.py` | 按产物内容总判定（Kit 吞退出码，不能看返回值） | env-gen-yuxin |
| `s15_evidence_shots.py` | 证据渲染：bottle 定机位正面照 + cabinet 抽屉 DriveAPI 实际驱动三帧 | isaac-smoke |

## 没有一起归档的三个（重要）

原先同在 `scripts/a_forward/` 但**本身不属于「正向转换」**、且仍在生产链路上的三个脚本
已按语义归位，**不在本夹**：

| 脚本 | 现位置 | 为什么留下 |
|---|---|---|
| `s3_validate_sapien.py` | `scripts/4_validate/` | SAPIEN 侧物理验证，与转换方向无关；`docs/2026-08-08-asset-ingest-metadata-contract-plan.md` Task 5 要复用它的渲染写法 |
| `s5_check_ir.py` | `scripts/ledger/` | 账本 IR 校验工具；同一份计划 Task 7 要改它经 `to_ir_bundles` 消费权威账本 |
| `s0_verify_isaac.py` | `scripts/probe/` | Isaac 环境自检，任何 Kit 步骤前都可能用到 |

## 如果要恢复

`run_smoke.sh` 原本的 11 步中，步 1–7 是本夹脚本。恢复时需要：把 6 个文件移回
`scripts/` 下的新阶段夹、在 `run_smoke.sh` 里补回对应步骤并重编号、检查各脚本的
`sys.path` 层级（`parents[N]`）是否与新位置匹配。原始产物目录：
`results/_test/20260802_smoke_bottle_cabinet_glb2usd/`。
