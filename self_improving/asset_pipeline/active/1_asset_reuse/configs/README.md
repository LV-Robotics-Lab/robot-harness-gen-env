# configs/ — 配置与清单

| 文件 | 谁产 | 谁消费 |
|---|---|---|
| `external_manifest.json` | 人工手写 | `scripts/2_convert/import_fetch_convert.py`：批量导入清单——组（服务器 prefix）+ 逐条 usd/asset/model/category/aliases/colors/footprint，可选 `collision: coacd`、`size_policy` |
| `providers.json` | 人工手写 | `scripts/1_search/` + `lib/a1`：检索 provider 开关/层级/白名单 + 全局门禁（`top_k`/`max_fallback`/`max_size_bytes`/`license_gate`） |
| `smoke_manifest.json` | 人工手写 | `scripts/run_smoke.sh`：冒烟回归的输入——单件 YCB 025_mug → 301_cup（带 coacd），与 `external_manifest.json` 同名条目保持一致 |
| `acquire_categories.json` | 人工手写 | `scripts/1_search/acquire_batch.py`：类目需求清单（category + aliases） |
| `acquired_manifest.json` | **acquire_batch 自动追加** | 已引进资产台账，与 `external_manifest` 同构；**勿手改** |

## acquire_categories 清单模板（searched/pinned/local 三形态）

模板：`acquire_categories.template.json`（复制改名后使用，四个条目分别演示各形态）。

| 字段 | 必填 | 作用 |
|---|---|---|
| `category` | ✅ | 要什么类别（唯一必填；库里已有同类会直接复用不重复引进） |
| `aliases` | | 同义词，扩大检索命中面 + VLM 名称核对 |
| `colors` / `materials` | | 进检索词 + VLM 看图验货（属性不符淘汰候选） |
| `size_policy` | | `match_category`(默认) / `absolute:<米>` / `none` |
| `collision` / `reorient` / `flat` | | 物化旋钮：凸分解 / 静置定姿 / 放宽直立门 |
| `pinned: {prefix, usd}` | | 钉死 NVIDIA 服务器具体文件，跳过检索 |
| `local: {path}` | | 验收本地文件入库，跳过检索和下载 |
| `comment` | | 仅注释，代码忽略 |

执行方式见 `1_asset_reuse/README.md`；每条最多引进 1 个资产，条目数不限，单条失败不影响其余。
