# scripts/ — 步骤脚本（按流程顺序分夹）

总入口 `run_smoke.sh`（4 步打样回归，横跨 B/C 线）——**全程隔离**：源镜像 / 资产库 / 影子根 / 扩展 catalog / 场景全部落 `results/_test/<run>/`，生产 `data/` 只读不写。输入清单 `configs/smoke_manifest.json`。逐文件功能与参数详解见
`../OVERVIEW.md` 第 4 节，此处只做地图。

**编号夹 = 外部资产引入的流程顺序**，从一句 prompt 到场景包依次经过：

| 夹 | 阶段 | 内容 |
|---|---|---|
| `1_search/` | 检索选定 | `acquire_batch`（类目批量引擎）、`scene_acquire`（场景自适应总入口） |
| `2_convert/` | 格式转换 | `import_fetch_convert`（批量 USD→GLB，Kit 单会话）、`s13a_usd2urdf`（关节体 USD→URDF） |
| `3_materialize/` | 规范化物化 | `import_materialize`（批量物化+硬门+落账）、`dev_harness/`（多模型回归夹具） |
| `4_validate/` | 验证 | `s3_validate_sapien`（SAPIEN 静置）、`s13b_validate_articulated`（关节扫掠）、`s11_runtime_load_sweep`（运行时加载） |
| `5_catalog/` | 注册与验收 | `s9_build_shadow_root`（影子根+扩展 catalog）、`s14_catalog_admission`（目录准入）、`s10`/`s12`（e2e 与 external-only 验收） |

**非编号夹 = 横切工具**，不在流程序列上：

| 夹 | 角色 | 内容 |
|---|---|---|
| `ledger/` | 账本工具链 | `backfill_ledger_v1`、`gen_fragment`（fragment 生成）、`ledger_audit`（全库巡检）、`retire_asset`（退役）、`s5_check_ir`（IR 校验） |
| `probe/` | 一次性探测/自检 | `s0_verify_isaac`（Isaac 环境自检）、`s7_probe_reverse`（反向可行性探测） |

`sN` 序号是历史打样步序（**保留不改**，可与既往 `results/` 报告和 docs 里的称呼对照）；
**阶段归属以文件夹为准**，与 `sN` 数字无关（如 `s8a` 在 `2_convert/`、`s8b` 在 `3_materialize/`）。

Kit 类脚本跑 conda `isaac-smoke`，SAPIEN/上游类跑 `env-gen-yuxin`（各文件 docstring 已标注）。
两类脚本之间是 subprocess 边界，不能互相 import。

已归档（`../archive/`）：`1_forward_convert/` = 线 A 正向转换；`2_single_asset_probe/` = 线 B 单件打样 `s8a`/`s8b`（2026-08-10，被批量管线取代）。
