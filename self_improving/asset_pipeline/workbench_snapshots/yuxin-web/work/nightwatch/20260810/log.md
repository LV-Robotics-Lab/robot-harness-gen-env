# 值夜日志 2026-08-10（新加坡时）

**托管内容**：① s10/s12 端到端实跑（验证 asset_ledger.v2 迁移后可见层零位移）
② 资产检索方法调研 → 产出可执行方案

**分支**：`feat/ledger-v2`（从 `feat/env-gen-ir-bridge` 开出）
**回退**：`git checkout feat/env-gen-ir-bridge`

## 交接假设（用户已入睡，未及回答，按保守默认开工）
- 成功标准：s10 打印 `S10 PASS`；s12 `EXT_ONLY_E2E 5/5`。任一不过 → 保护现场、不改实验语义、写进汇报待拍板。
- 修复边界：只修机械/infra（路径、权限、依赖、目录）。不动 prompt、seed、验证阈值、catalog 内容。
- 交付物：s10/s12 结果 + 资产检索方案（具体到可执行）。
- 预计：s10 分钟级，s12 五个场景。调研整夜。

## 时间线

### 22:54 SGT — 开工
- 分支 `feat/ledger-v2` 建立，快照 commit `bfad517`（含本次 v2 全部改动 + 开始时工作区已有的未提交改动，已在 commit message 里分开说明）。
- **修复①（infra）**：`git add -A` 把 `1_asset_reuse/results/` 的 41MB 测试产物收进了快照。根因：`.gitignore` 写的是 `/results/`，前导斜杠锚定仓库根，阶段目录下的 results/ 漏网。
  处置：`git rm -r --cached` + `.gitignore` 改为无锚定的 `results/`，amend 进同一 commit（未推送，安全）。
  **是否回灌 canonical：是** —— 改的就是仓库根 `.gitignore` 本身，之后任意层级的 results/ 都不会再进 git。

### 22:55–23:05 SGT — s10 / s12 实跑（任务①）完成
- **s10 PASS**（22:55:33 起，约 1 分钟）：编译 → ground 到 301_cup → SAPIEN 回放 fail=0 → 全量验证 fail=0 not_run=0。
- **s12 EXT_ONLY_E2E 5/5**：can/box/bottle/bowl/block 五类 prompt 全部 ground 到 3XX 资产且回放+验证通过。
- ★ **最强证据**：8/3 那次打样与今晚跑出的是同一 scene_id，`resolved_scene.json` **逐字节一致**，
  `asset_catalog_sha256` 相同（5c131900f746cb82d8e8…），grounding_score 108.0 相同。
  → 「v2 迁移对可见层零影响」由推论变成实测。
- 产物：`results/20260810_v2_e2e/`（s10.log / s12.log / scenes / runtime）

### 23:05 SGT — 转入任务②：资产检索方法调研
现状盘点（读代码得出）：四级 provider 全部是 **token 子串匹配**——
NVIDIA 侧 `hits = sum(1 for t in toks if t in base)` 对 USD **文件名**做子串；
RoboTwin 本地对 {category, semantic_name, aliases} 做**精确集合成员**判断；
score = 命中词个数。没有语义相似度、没有视觉验证、命中即入选。

### 23:10–00:05 SGT — 任务②：检索方法实测（不是文献综述，是在自己语料上跑数）

**关键发现①（可行性）**：NVIDIA 资产服务器为每个 prop USD 发布 256×256 预览 PNG，
路径确定（`<dir>/.thumbs/256x256/<name>.usd.png`）。索引里 495 个 prop USD 有 465 个
带缩略图（94%），中位 55 KB，**全量 22.6 MB、51 秒镜像完毕、0 失败**。
而现有代码把 `.thumbs` 当噪音直接丢弃（`thumbs_artifact` 淘汰码）。
→ 等于全语料的免费视觉信号一直摆在那儿没用。

**关键发现②（语料卫生）**：465 个条目里 88 个（19%）根本不是可抓取物体——
材质定义（Plastic_Red_A / MetalPainted_*）、physics 代理、场景脚手架（plane / frame_prim）。
两种检索方法都不该拿这些当候选。

**关键发现③（四方法对比，30 题带标注）**：
| 方法 | top-1 | top-5 | MRR |
|---|---|---|---|
| lexical（现状） | 86.7% | 86.7% | 0.867 |
| clip | 70.0% | 80.0% | 0.753 |
| clip + prompt ensemble | 66.7% | 80.0% | 0.739 |
| **RRF 融合(lexical+clip)** | **93.3%** | **100%** | **0.953** |
→ ★ **CLIP 单独用比现状更差**。若直接建议换成 CLIP就是性能倒退。
→ prompt ensemble 实测反而掉 3 个点，不采用。
→ 词法的短板是纯召回（4 题零结果：coffee cup / shears / 黄色弯曲水果 / cleaning bottle），
   命中时几乎都在第 1 名（86.7% top-1 == 86.7% top-5）。

**关键发现④（安全性 — 最重要）**：30 在库 + 6 不在库查询，
三个信号 `clip_top` / `clip_margin` / `lex_hits` **区间全部重叠，无任何单一阈值可分**。
组合规则「lex_hits==0 且 margin<0.078」零误伤只能判出 4/6。
漏判的两个正是子串匹配的恶果：`trash **bin**`→ 命中 ca-**bin**-et；`teddy **bear**`→ 命中 **bear**ing。
→ **结论：不能靠分数弃权，必须在入库前做正向视觉确认。**

产物：`results/20260810_retrieval_probe/`（thumbs/ + probe.json + eval.json + abstain.json）
脚本：`work/oneoff/{fetch_thumbs,clip_retrieval_probe,retrieval_eval,abstain_probe}.py`
均为只读探测，未改动任何管线代码。

### 00:20–00:40 SGT — 去风险：验证方案第 1 步会不会打坏现有能力
收紧匹配器可能删掉现在能用的命中，所以在写进方案前先量：
| | 子串(现状) | 词边界 |
|---|---|---|
| gold top-1 | 26/30 | 26/30（不变）|
| 打坏 | — | **无** |
| trash bin 误命中 | 3 | **0** |
| teddy bear 误命中 | 2 | **0** |
| cardboard box 候选池 | 33 | **6**（gold 仍第 1）|
→ 零打坏、误命中归零、候选池收缩。**第 1 步无权衡，纯赚。**

模型选型：ViT-L/14 70.0% top-1 vs ViT-B/32 60.0% —— 索引只有 377 条，用 L/14。

### 00:40 SGT — 交付
- 方案：`results/20260810_retrieval_probe/REPORT.md`
- e2e 验收：`results/20260810_v2_e2e/REPORT.md`
- git：本夜**未改动任何管线代码**（全部只读探测），故无新 commit；
  `/work/` 与 `/results/` 均在 .gitignore 内（一次性目录 + 运行产物按约定不入 git）。
  代码回退点仍是开工前的快照 `bfad517`（分支 feat/ledger-v2）。
