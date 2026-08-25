# 物理资产检索与重建：独立网页报告

这是一个完全独立于 `apps/pearl_evidence_portal/` 的中文静态报告。它不会进入门户导航、报告卡片、manifest 或门户构建清单。

## 查看方式

直接打开 [`index.html`](index.html) 即可。页面没有构建依赖；若浏览器限制本地文件，也可以在本目录启动任意静态 HTTP server 后访问根路径。

## 报告回答的问题

- text、image、video 如何使用同一套候选 manifest 做资产检索和复用；
- 哪些物理属性可以从视觉给先验，哪些必须通过已知动作或主动交互辨识；
- 是否真的需要重建所有物理属性；
- VLA / WAM 的成功动作能否证明模型恢复了质量、摩擦或力；
- 如何验证 backbone 的物理信息可读性，以及最值得优先复现的工作。

## 研究底稿

- [`physical-property-retrieval-reconstruction-benchmark.md`](research/physical-property-retrieval-reconstruction-benchmark.md)
- [`vla-wam-implicit-physics.md`](research/vla-wam-implicit-physics.md)

`research/` 中的文件是网页的随附快照，因此整个报告文件夹可以单独复制和静态托管。仓库内的工作底稿仍保存在 `self_improving/studies/physical_asset_reconstruction_research/`。

## 证据边界

页面内容来自一手论文、作者代码和本仓库接口审计。所列复现对象目前是建议，不是本仓库已经跑通的实验结果。外部论文结果不能算作 PEARL 自己的结果；3DGS 渲染、VLM 评分和 LLM 给出的物性数字也不能代替连续轨迹、接触、系统辨识与任务回放证据。
