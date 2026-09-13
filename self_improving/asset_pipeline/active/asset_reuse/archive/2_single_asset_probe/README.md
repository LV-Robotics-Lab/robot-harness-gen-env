# archive/2_single_asset_probe/ — 线 B 单件打样（s8a / s8b）· 已归档

**归档日期**：2026-08-10 ｜ **状态**：只留档，不再维护、不再被任何入口调用。

## 这是什么

线 B 最早的单件打样通路：把一个 NVIDIA 服务器上的 USD 物体反向引入 RoboTwin 布局，
证明「USD 生态资产能进 RoboTwin 并过 SAPIEN 静置验证」。打样样本 YCB `025_mug` →
`301_cup`（红色马克杯）。

| 文件 | 功能 | 运行环境 |
|---|---|---|
| `s8a_fetch_convert_usd.py` | S3 精确拉单 prop（源镜像 + 哈希清单 + upAxis 元数据），asset_converter 反向导出 GLB | isaac-smoke |
| `s8b_materialize_validate.py` | 规范化物化（upAxis 驱动 Z-up→Y-up、原点=底部中心、实测包围盒写 model_data）+ SAPIEN settle + 账本 | env-gen-yuxin |

## 为什么归档

**两条理由，第二条是决定性的：**

1. **能力被完全覆盖**。B批量管线（`2_convert/import_fetch_convert.py` +
   `3_materialize/import_materialize.py`）是它的超集：同样的拉取 + 转换 + 规范化 + SAPIEN
   硬门，另外还支持 `collision: coacd` 离线凸分解、写 v1 权威账本、产出 catalog 需要的
   overrides fragment、单次 Kit 会话批量转换。

2. **`s8b` 会破坏资产**。它早于 v1 账本，物化时**整目录重建**资产：
   - 覆盖 `collision/base0.glb` —— 把 coacd 分解出的碰撞体换成视觉网格的副本
   - **删除 `ledger.json`** —— 它只写 pre-v1 的 bundle，不认识权威账本

   2026-08-10 首次真跑 `run_smoke.sh` 时实际发生过：301_cup 的 510KB coacd 碰撞体被
   7MB 的视觉网格副本覆盖，已提交的 `ledger.json` 被删。恢复过程见该日提交
   `6c52f8f`。

## 取代它的是什么

`run_smoke.sh` 已改为走批量管线，且**全程隔离**——源镜像、资产库、影子根、扩展 catalog、
场景包全部落 `results/_test/<run>/`，生产 `data/` 只读不写。输入是
`configs/smoke_manifest.json`（就是 301_cup 那一条，带 coacd）。

## 如果要恢复

两个脚本原本在 `scripts/2_convert/`（s8a）与 `scripts/3_materialize/`（s8b）。恢复前先解决
`s8b` 的账本问题——否则它仍会删掉目标资产的 `ledger.json`。原始产物目录：
`results/_test/20260803_smoke_usd2envgen/`。
