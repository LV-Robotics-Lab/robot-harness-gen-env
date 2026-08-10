# scripts/ — 步骤脚本（按功能线分夹）

总入口 `run_smoke.sh`（11 步打样回归，横跨 A/B/C 线）。逐文件功能与参数详解见
`../OVERVIEW.md` 第 4 节，此处只做地图。

| 夹 | 功能线 | 内容 |
|---|---|---|
| `a_forward/` | 线 A · 正向转换 | RoboTwin→Isaac USD：`robotwin_asset` + `s0–s6` + `s15` 证据渲染 |
| `b_reverse/` | 线 B · 反向打样+关节 | `s7` 探测、`s8a/s8b` 刚体单件、`s13a/s13b` USD→URDF 关节体 |
| `b_batch/` | 线 B批量 · 清单导入 | `import_fetch_convert`（Kit 转换）+ `import_materialize`（物化+验证入库） |
| `c_catalog/` | 线 C · 接入+验收 | `s9` 影子根/catalog、`s14` 目录准入、`s10/s11/s12` 三层运行时验收 |
| `d_acquire/` | 线 D · 检索选定 | `acquire_batch`（类目批量）、`scene_acquire`（场景自适应总入口） |

`sN` 序号是历史打样步序（保留，可与既往 results/ 报告对照）；**线归属以文件夹为准**。
Kit 类脚本跑 conda `isaac-smoke`，SAPIEN/上游类跑 `env-gen-yuxin`（各文件 docstring 已标注）。
