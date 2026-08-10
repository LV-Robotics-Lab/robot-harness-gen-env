# configs/ — 配置与清单

| 文件 | 谁产 | 谁消费 |
|---|---|---|
| `external_manifest.json` | 人工手写 | `scripts/2_convert/import_fetch_convert.py`：批量导入清单——组（服务器 prefix）+ 逐条 usd/asset/model/category/aliases/colors/footprint，可选 `collision: coacd`、`size_policy` |
| `providers.json` | 人工手写 | `scripts/1_search/` + `lib/a1`：检索 provider 开关/层级/白名单 + 全局门禁（`top_k`/`max_fallback`/`max_size_bytes`/`license_gate`） |
| `smoke_manifest.json` | 人工手写 | `scripts/run_smoke.sh`：冒烟回归的输入——单件 YCB 025_mug → 301_cup（带 coacd），与 `external_manifest.json` 同名条目保持一致 |
| `acquire_categories.json` | 人工手写 | `scripts/1_search/acquire_batch.py`：类目需求清单（category + aliases） |
| `acquired_manifest.json` | **acquire_batch 自动追加** | 已引进资产台账，与 `external_manifest` 同构；**勿手改** |
