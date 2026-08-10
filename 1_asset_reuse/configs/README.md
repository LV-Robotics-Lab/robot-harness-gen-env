# configs/ — 配置与清单

| 文件 | 谁产 | 谁消费 |
|---|---|---|
| `external_manifest.json` | 人工手写 | `scripts/2_convert/import_fetch_convert.py`：批量导入清单——组（服务器 prefix）+ 逐条 usd/asset/model/category/aliases/colors/footprint，可选 `collision: coacd`、`size_policy` |
| `providers.json` | 人工手写 | `scripts/1_search/` + `lib/a1`：检索 provider 开关/层级/白名单 + 全局门禁（`top_k`/`max_fallback`/`max_size_bytes`/`license_gate`） |
| `acquire_categories.json` | 人工手写 | `scripts/1_search/acquire_batch.py`：类目需求清单（category + aliases） |
| `acquired_manifest.json` | **acquire_batch 自动追加** | 已引进资产台账，与 `external_manifest` 同构；**勿手改** |
