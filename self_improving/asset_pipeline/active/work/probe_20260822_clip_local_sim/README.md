# probe_20260822_clip_local_sim — 本地资产池 CLIP 相似度可分性

**结论：路线成立，已并入正式管线（a5.local_asset_scores + match_local_all）。**

- 语料：catalog 179 资产（缺缩略图 13 个不参与）；金标 = catalog category（免费标注）。
- 分布：同类 pos p5/p50/p95 = 0.171/0.234/0.288；异类 neg = 0.103/0.152/0.204；**AUC 0.949**。
- 双向指标（阈值表节选）：0.18 → 同类召回 91.1% / 异类误入 17.3%；0.20 → 81.6%/6.4%；0.22 → 65.9%/1.9%。
- **默认门限取 0.18**：生效场景是"已同类过滤的池内筛相似"，宁可多保留；条目级 `similar_min_visual` 可覆盖。
- 颜色子探针：`a photo of a {color} ball` 3/3 把同色资产排到首位（blue→366、brown→330、purple→366）——支持"全 query 文本（含颜色/材质）打视觉分"。
- 设计取舍：视觉分**尽力而为**（模型/缓存不可用→退化纯属性分）；缺缩略图资产 visual=None 不误杀；exact（属性全符）豁免门限。
- 明细：out/sims.jsonl（29,893 对逐条可复核）、out/s2.log（完整阈值表）。

探针可删（结论已随代码注释与本 README 留档）；s1 逻辑已生产化为
`a5.build_or_load_local_asset_embeddings`（缓存 data/asset_index/local_asset_clip.npz，按资产集合指纹自动重建）。
