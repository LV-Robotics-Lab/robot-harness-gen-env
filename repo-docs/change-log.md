# Change Log

## 2026-08-18

- 为 Harness MVP PR1 增加正式中文实现报告，逐项记录 14 个公共 schema、Pydantic/JSON Schema 分工、状态机、Text2Env 边界、100% 覆盖率证据、兼容性风险和 PR2 前置清单。
- 增加 reader-facing Harness Schema Tranche 模块页，并同步平台总览、代码地图、术语表、三轮源码证据和质检残余风险；明确 schema tranche 尚不包含 Registry、handler 或 MCP，run `succeeded` 也不等于 validation pass 或 publishable。

## 2026-08-17

- 增加 Harness MVP PR1 schema tranche：14 个严格、不可变公开 schema，Text2Env compile/replay/validate 边界、committed JSON Schema 漂移检测与 100% 语句/分支覆盖门；Registry、handler、MCP 留待后续，RFC 仍为 Proposed。
- 校正回放指南中的 contact window 默认值：独立 `run_scene_runtime.py` CLI 当前默认 60；README、prompt matrix 与已验证配方显式传入 120。未改变源码或既有验收证据。

## 2026-08-14

- 统一仓库本地大数据根目录为 ignored `data/`，移除与其语义重复的旧目录约定；Jingxiang canonical checkout 的 7.1 GB payload 已同盘改名，9,576 条新路径 manifest 全量通过，历史 manifest 与清理 receipt 继续保留执行当时的原始路径作为不可改写的审计证据。
- 固化 Jingxiang 仿真组多人协作布局：canonical `workspace/robot-harness-gen-env` 只承载共享 `main`，Bingsheng、Gujie、Yeyuxuan、HYX 分别在个人名字目录使用 `worktree/<人名>`；个人产物不得越出个人 workspace，共享材料须经过明确提升。
- 将 `huyuxinn/env-gen-dev` 完整历史接入 `worktree/hyx`，迁入遗漏的 legacy 资产工具与 33 份个人 ledger，并在分支推送、manifest 复核后移除第二个本地 checkout；原事故记录中的“RoboTwin 全灭”继续以 canonical cleanup receipt 的现场复核为准。
- 迁入 Yeyuxuan 完整 RoboLab onboarding 分支历史、20 份资产来源记录、迁移 CLI 与运行时语义修复；大文件只保留 SHA-256 清单，未把第三方 payload 放入 Git。
- 调和 Yuxin 当前 `main`、`feat/web-studio-v2` 与未提交的断点续测修改，完整保留各历史 tip，并把资产流水线改为由 `runtime_config.py`/环境变量提供路径。
- 将 6 个旧归档入口共同保存、但不在 `main` 或任何个人 worktree 中的 38 个独有提交以 history-only merge 并入 `worktree/hyx`（`899c649`），前后 tree SHA 均为 `971cb34`；确认全部旧 tip 可从活动分支到达后，退役 15 个 `archive/*` 和 1 个 `integration/*` 远端分支。
- 保存 `301`–`361` 外部资产命名空间的 12,047 文件摘要、选择 manifest 与小型 ledger/model metadata；27,637,543,884 字节本体因 `storage_uri: null` 继续留在本地。
- 把 Bingsheng、Gujie、Yuxin 独有的设计/交接文档作为历史材料纳入 `self_improving/contributor_notes/`，不把旧绝对路径包装成当前命令。
- 补查 `.gitignore` 后保存 Yuxin 被所有分支遗漏的 42 份 `work/` 源码与笔记，包括中断的属性矩阵 driver；作为只读 workbench snapshot，不冒充正式入口。
- 把忽略的实验数据、checkpoints、RoboLab payload 与资产库移动到 canonical checkout；经 checksum-mode rsync 证明重复后删除 Bingsheng/Yuxin 的 16 GB RoboTwin 资产副本，并保存清理 receipt。Gujie 当前训练占用的 RoboTwin 仍明确留待训练退出后移动。
- 处理并发恢复任务：保留其 `ad28866` 与断点续测 dirty state 到组织归档分支，纠正“迁移即 530GB 丢失”的误判，并把 508 模型颜色、538 条原点校准、471 条顶面探针和 runtime revocation 四份小型实测元数据正式纳入 Git。
- 在最终整合 commit 上初始化所需顶层外部子模块后，自包含回归为 543 passed、5 skipped；Jingxiang 的真实 RoboTwin/SAPIEN 回放门也已完成。
- 原训练自然结束于 epoch 599/global step 25799；保存 `600.ckpt` 的 1,549,185,541 字节大小与 SHA-256 后，将 89 GB RoboTwin 树迁入 canonical `external/RoboTwin`，修复 227 个生成链接与六份 Curobo 配置，最终 broken symlink 为 0。
- 在 Jingxiang `robotwin-5090` 真环境完成 `place_a_can_on_the_table_acd20a6814` 的 900 步 SAPIEN 回放：`pass`、`fail_count=0`、`not_run_count=0`、120 帧（100 unique）；结构化 JSON 与 manifest 已进入 `validation_evidence/student_workspace_20260814/`。
- 对并发恢复的 Yuxin RoboTwin 再做 checksum-mode rsync，唯一六行差异正是 canonical 路径修复；日志和 48 份小文件转入现已统一命名为 `data/` 的 ignored 数据根后，删除所有四个同学的重复个人工作目录。`/home/jingxiang/workspace/` 项目层只剩 `lerobot` 与 `robot-harness-gen-env`。

## 2026-08-13

- 把仓库文档范围扩展为稳定 `/gen-env` 核心与 `self_improving/` 平台两层。
- 登记 Alchedata、stage-05、asset pipeline、sim adapter、onboarding、stage-04 历史和两个外部子模块的所有权边界。
- 明确 AgenticSim 历史仓库与后续 runtime adapter 不是同一个组件。
- 以完整祖先链迁入 PEARL evidence portal 与 alternate RoboTwin Text2Env，并把后者固定为只读 legacy；补回 can/basket video anchor 标注与精确 SHA-256 来源记录。
- 收口 Jingxiang workspace 的七个 sibling：OpenXSim 验收 JSON/日志/复现脚本进入 Git，六个 cache-filtered 完整工作区进入同仓库 Release，MetaSim 固定为子模块，重复 prompt-matrix clone 由既有历史合并覆盖。

| Timestamp | Request | Actions | Verification | Result |
| --- | --- | --- | --- | --- |
| 2026-07-29 10:55 +08:00 | 用 `repo-docs-zh` 为 `robot-harness-gen-env` 首次构建中文 repo-docs 包 | 读完父 `repo-docs` 三个核心规则文件（SKILL/REFERENCE/PAGE_RULES/WRITING/QUALITY_RULES/EXAMPLES）。在 `repo-docs/` 下新建：`README.md`（中文开场 + 阅读路径表）、`walkthroughs/one-real-run.md`（一条 prompt → resolved 包 → SAPIEN 回放 → 运行时门控，8 步）、`code-map.md`（`scene_gen/`/`script/`/`demo/`/`tests/` 四区目录职责 + 关键符号 + 主路径关系 + 排除项）、`modules/` 下 7 页（bounded-parser、scene-contract、target-local-geometry、solver、derived-proxy、replay-package、runtime-gates）、`references/source-evidence.md`（两轮 traversal + claim/evidence/confidence/caveat/used-by 表）、`references/quality-review.md`（Reader Simulation + 可理解性 review + 残余风险）、`glossary.md`（17 行术语）、本 change-log。在仓库根 `AGENTS.md` 末尾追加 `Repo docs` 路由句与中文 overlay 指明。 | `$env:PYTHONIOENCODING = "utf-8"; python "C:\Users\SatelluS\.agents\skills\repo-docs\scripts\validate_repo_docs.py" repo-docs --repo-root .` → 初轮 0 errors / 32 warnings（含 walkthrough 难点触发句、code-map 目录/Header/Coverage 形态、source locator 前缀、证伪检查、5 个高频术语缺 glossary 行），按 warning 逐项修了 5 轮后到 0 errors / 0 warnings。`pytest -q` 在本机未能跑——当前 Python 3.12 解释器没装 pytest、本地无 `.venv`，仓库要求 Python 3.11 + 装了 `dev` extra 才有 pytest；但本次改动只动了 `AGENTS.md` 文范畴路由段与新建 `repo-docs/*.md`，没动任何 Python 源码或 `tests/fixtures/`，故测试集状态不受影响。真机 SAPIEN/RoboTwin 回放另按根 `AGENTS.md` 在支持机器上验证。`git rev-parse HEAD` = `60a25971738e0cd4c64615e4455cc2b4098aaa43`，与 sync anchor 一致。 | build：通过；validator 0 errors / 0 warnings；测试集本机未跑（环境缺 pytest），仅改文档不影响契约层。 |

Synced through 9b720900ff1c3c1b5a6587f7bc5d78359d3af81b.
