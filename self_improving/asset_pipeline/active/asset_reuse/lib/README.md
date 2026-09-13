# lib/ — 共用库

纯 Python 模块，被 `scripts/` 各线与 `tests/` 复用。逐文件详解见 `../OVERVIEW.md` 4.6/4.7。

| 文件 | 角色 |
|---|---|
| `a1_providers.py` | 检索层：四级信任梯度 provider + `tiered_search`（命中即停） |
| `a2_selection.py` | 检索层：候选门禁/淘汰码、3XX 资产号分配、清单追加与证据簿记 |
| `a3_webfetch.py` | 检索层：web 候选下载 → GLB 转换（trimesh，纯 Python）→ staging 记录合成 |
| `a4_coverage.py` | 检索层：prompt 需求抽取 + catalog 覆盖判定（只读 import 上游 grounding） |
| `conventions.py` | 横切防护：惯例继承（类目语义可继承）+ `resolve_size` 尺寸策略；朝向绝不继承 |

命名：`aN` 前缀 = 检索层模块序；`conventions` 无前缀 = 横切层。

依赖注意：`a4` 需上游 `scene_gen` 在 sys.path（消费方脚本负责注入）；`a1/a3` 的候选
数据结构与下载器来自 `shared/openxsim`（`agenticsim.openxsim.assets`）；`conventions`
纯 stdlib，双 conda 环境（isaac-smoke / env-gen-yuxin）皆可 import。
