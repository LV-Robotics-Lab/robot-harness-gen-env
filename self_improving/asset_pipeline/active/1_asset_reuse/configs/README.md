# configs/ — 活配置（代码运行时真读的）

| 文件 | 谁写 | 谁读 | 手改 |
|---|---|---|---|
| `providers.json` | 人 | `acquire_batch`/`scene_acquire`/web（检索源 tier0–4 + top_k/门禁开关） | ✅ 这是主要调参入口 |
| `category_sizes.yml` | 人 | a4 需求解析(size_decision)、物化缩放、rescale_backfill、s9 | ✅ 新类别补一行典型尺寸 |
| `templates/` | 人（照抄起点） | 不被代码读 | ✅ 复制出去改 |

## templates/ — 引进请求清单模板

要资产时照着填的表（喂给 `acquire_batch --categories`；管线内部称 categories 清单，
run 目录里机器写的 `acquire_categories.json` 即同一 schema 的运行痕迹）：

- `acquire_request.template.json` — 全形态参考：searched（自动检索）/ pinned（钉死
  NVIDIA 文件）/ local（验收本地文件）四条带注释
- `acquire_request.example.json` — 最简可跑：2 条 searched，复制改类别即可用

字段说明见本文件末表；执行方式见 `../README.md`。

## 已迁出（2026-08-18 重组）

| 原住户 | 现址 | 原因 |
|---|---|---|
| `acquired_manifest.json` | `../../data/` | 机器写的引进台账（编号+查重依据），须与 asset_library 同区同进退，勿手改 |
| `smoke_manifest.json` | `../tests/fixtures/` | run_smoke.sh 常驻回归的输入夹具 |
| `external_manifest.json` | `../archive/` | 首批 NVIDIA 批量导入的历史记录，只留档 |

## 清单字段速查

| 字段 | 必填 | 作用 |
|---|---|---|
| `category` | ✅ | 要什么类别（库里已有同类会复用不重复引进） |
| `aliases` | | 同义词：扩大检索面 + VLM 名称核对 |
| `colors` / `materials` | | 进检索词 + VLM 看图验货（不符淘汰候选） |
| `size_policy` | | `match_category`(默认) / `absolute:<米>` / `none` |
| `collision` / `reorient` / `flat` | | 物化旋钮：凸分解 / 静置定姿 / 放宽直立门 |
| `pinned: {prefix, usd}` | | 钉死 NVIDIA 服务器具体文件，跳过检索 |
| `local: {path}` | | 验收本地文件入库，跳过检索下载 |
| `allow_similar` | | 默认 `true`：exact 落空时允许返回/引进相似资产（similar 档）；`false`=严格模式，宁可 none |
| `similar_min_visual` | | 相似候选的 CLIP 视觉分门限，默认 `0.18`（探针 2026-08-22 标定：同类召回 91%/异类误入 17%）；exact 豁免 |
| `comment` | | 仅注释，代码忽略 |
