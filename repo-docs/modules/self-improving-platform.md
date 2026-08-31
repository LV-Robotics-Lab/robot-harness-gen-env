# Self-Improving 平台边界

`scene_gen/` 是稳定信任边界，负责把受限文本编译成可验证、可回放、哈希绑定的场景包。`self_improving/` 是消费者和编排层：它可以选择环境、组织采集与训练、评估失败、写诊断和记忆、决定是否晋升，也可以调用资产与仿真适配器；它不能伪造或跳过 `/gen-env` 的物理门控。

| 层 | 目录 | 写什么 | 不写什么 |
| --- | --- | --- | --- |
| 稳定核心 | `scene_gen/` | schema、parser、grounding、solver、builder、validator | 策略、训练循环、仿真器特定编排 |
| Harness 契约 | `self_improving/harness/` | 严格审计记录、Text2Env Skill 输入输出、权威载荷引用、公开 schema 快照、通用 Registry 与 compile adapter | 复制 `scene_gen` 载荷、在 Registry 核心写 Text2Env 分支、MCP 自有类型或发布决策 |
| 场景编排 | `self_improving/stage5/` | designer/critic/grounding agent、prompt、MCP-lite | 核心物理判定的替代实现 |
| 闭环 | `self_improving/alchedata/` | collect/train/evaluate/diagnose/transfer、失败记忆、promotion gate | 大规模 runs、checkpoint、下载缓存 |
| 资产 | `self_improving/asset_pipeline/` | 发现、ingest、ledger、catalog 对接、迁移 adapter | 第三方 mesh 与渲染产物 |
| 同学交接材料 | `self_improving/contributor_notes/` | 历史设计、运行说明、任务交接及来源哈希 | 当前运行命令或新的能力承诺 |
| 仿真适配 | `self_improving/sim_adapters/` | 薄脚本、schema、可隔离测试 | 完整复制 IsaacLab 或候选仓库 |
| 历史原型 | `self_improving/legacy/robotwin_text2env_alt/` | `text2env.tabletop.v0` 来源快照、修复工具、有限 smoke evidence | 覆盖当前 Stage 5 或成为新功能入口 |
| 被忽略的工作台 | `self_improving/asset_pipeline/workbench_snapshots/` | Yuxin 的 asset-spike、nightwatch、one-off 源码与笔记快照 | 直接作为当前运行入口 |
| 验收归档 | `self_improving/validation_evidence/`、`workspace_archives/` | 小型结构化证据、复现脚本、完整文件哈希及 Release 指针 | 把 cache、嵌套 Git 元数据或第三方 mesh 直接塞进主树 |
| 呈现层 | `apps/pearl_evidence_portal/` | PEARL 门户、浏览器报告子集、构建测试 | 生成验收结论或把页面文案当运行证据 |
| 外部项目 | `external/` | 钉住子模块 commit | vendor copy |
| 历史 | `self_improving/legacy/` | 只读来源快照 | 新功能 |

`python -m self_improving --json` 只检查这些源码是否到位以及子模块是否初始化，不导入 GPU 框架、不启动仿真器。来源工作区、提交、归档分支和排除项在 `self_improving/source_inventory.json`，它是清理旧副本前的审计入口。

下面的 Harness 集成状态固定到 clean commit `ab03859`。之后观察到的并发 commits `c365874`、
`8d9a01c`、`587b49f`、`0e6716a` 未进入本研究复核；“当前/未接线”均只描述这个固定快照，不认证
移动 HEAD。

Harness 当前公开 15 个以 `$id` 标识的 JSON Schema：PR1 的 14 个入口保持不变，另加
`harness.skill_descriptor.v2`，明确区分内容位级确定性与证据不变量可复验。descriptor v1 保持冻结，
compile 仍用 v1，replay 用 v2；两版不能静默转换。`ArtifactRef.schema_version` 仍指向既有
`robotwin.*` 权威载荷，Harness 不重新定义其内部格式。schema 之外已有本地 digest-checking artifact
resolver、callback-driven `RunRecorder`、SQLite WAL append-only `SQLiteEventJournal`、CAS
`PackageStore`、通用 qualified-version `SkillRegistry` 与 `Text2EnvCompileHandler`。固定 `ab03859`
关于 replay/validate 未接通的描述只是历史快照；当前 replay 已有 candidate handler、固定资格深验证与
production-only registration policy，但尚无 checked-in pass bundle 或真实 900/120 replay。
`python script/export_harness_schemas.py --check` 锁住 committed snapshot；核心资格模块强制 100%
语句与分支覆盖。compile 的 asset admission 仍发生在 solve 前且失败不回滚；跨 run promotion、
identity-frozen resumable EvaluationRun 与 MCP adapter 仍未闭合，契约保持 `Status: Proposed`。

字段边界、状态机、快照与未实现范围见 [Harness Schema Tranche](harness-schema-tranche.md)；
逐项实现和验证证据见 [PR1 实现报告](../../docs/contracts/HARNESS_MVP_PR1_IMPLEMENTATION_REPORT.zh-CN.md)。

ASPIRE E0–E2 快照 `1180aef` 的离线、自包含回归是 595 passed、6 skipped；这不是后续 Registry/compiler/asset/Stage 5 follow-on 的当前绿灯。该快照的 skip 只对应未纳入 Git 的 Isaac/SceneAgent/媒体/报告原始包或本机未安装的 SAPIEN 物理运行时。完整命令与时间边界见 `self_improving/README.md`。

后续只读诊断快照 `28333de` 的默认 pytest 因两个同名 `test_registry.py` collection error；
`--import-mode=importlib` 为 155 passed / 1 failed，剩余 failure 是 compiler 的 `parse` stage 与历史
`scene_spec_validation` 期望不一致。Harness 自身在该快照已回到 100% statement/branch coverage，
但这不抵消全仓 collection/行为回归。

E1e 收尾快照 `910ccb1` 另验证 Harness 专项 74 passed、1291/1291 statements 与 312/312 branches；
后续 `51447da` 的独立 clean-archive 复核是 76 passed，但 coverage 为 99.88% 并未过 100% 门；
默认 pytest 与完成 submodule 初始化后的平台脚本也都在两个同名 `test_registry.py` 上 collection
error。这些是分时快照证据，不等于重新跑通默认全仓/平台矩阵。

`148001d` clean archive 上 importlib 根测试为 201 passed / 0 failed，说明 CLI failure-stage 漂移已
闭合；默认 collection error 与 Harness 99.88% coverage gate 仍未闭合，所以该快照仍非全绿。

post-close `ab03859` clean archive 上 Harness 为 133 passed / 99.91% coverage；默认 collection 与
同一 coverage branch 仍失败。qualification/packaging/RunStore 是独立 prerequisites，不执行
development/clean qualification，不具原子 promotion/rollback，也不闭合 replay media identity。

2026-08-31 的后续切片已把 compile qualification/production CLI、独立 validate handler、严格 replay
event、runtime capability、subprocess executor 和 CAS runtime-asset snapshot 分别落地。runtime asset
层会闭合 selected tree 与 URDF/OBJ/MTL/glTF/GLB/COLLADA loader 引用，worker 在首事件前与 close 后复验；真实
can-on-plate 2-step smoke 已取得 acquisition pass，但物理 validation 因短 horizon 失败。A039 新增
媒体 consumer：在 delegated cgroup/Landlock/seccomp 边界中由静态 FFmpeg 从 held FD 完整解码，
真实历史录像观测为 120 帧 / 114 个 decoded unique。A040 再把 replay handler/resolver 接成候选竖切：
Invocation 必须精确带 capability、executor、handler config、media verifier、input-specific runtime
assets 五项依赖，worker 输出先作为 untrusted bytes 保存，只有 evidence 与媒体（含固定
MP4/H.264/8bit-420/SAR 语义）复核后才晋升 typed artifacts。两组专项为 185 passed 且两个模块
statement/branch 100%。descriptor 已用 v2 诚实声明 `evidence_invariant_repeatable`；固定 loader、
generator 与 production application 会重读完整 CAS evidence closure，但仍没有生成 pass bundle，
也没有新的真实 900/120 handler receipt。VLM fallback、LLM orchestration 和工作台不能从这些
候选底座推断为完成。

Stage 5 的视觉评审状态是三态而非布尔值。在 `--run-smoke`/视觉评审路径中，只有 visual pass
才把 candidate 原子晋升为 `final_placement.json` 并退出 0；`pending_visual_review` 只写
`review_candidate_placement.json`，保留 `hold_for_review`/pending 字段并退出 2。batch 只有显式
传 `--allow-pending-visual` 才收集这种候选，aggregate 仍是 `review_required`/exit 2。默认
static-only 写 `static_scene_candidate_placement.json`，smoke/visual 均为 `not_run`；兼容 status
`pass_static_scene_module`/`pass_static_only` 与 exit 0 只表示非物理静态阶段完成，不应读成完整
acceptance。

生成资产 follow-on 目前也不能被当作物理晋升：admission report 明确写
`physical_qualification=pending_settle`，但 ledger 的 generation-QC 项仍标作
`backend=sapien/verdict=pass`，并未运行 SAPIEN settle。读者应以 pending 为准；该矛盾修复前，
ledger 项不是 runtime verification receipt。

生产 compile 的资产所有权已经与一次运行状态分开：默认的可复用生成资产库是项目内
`self_improving/asset_pipeline/active/data/asset_library`，也可由
`CompileApplicationSettings.asset_library_root` / `--asset-library-root` 显式指定；`state_root` 只持有
SQLite/CAS、工作目录与生成 staging。qualification、测试和实验必须提供独立 scratch 库。这一变化
不改变上面的物理边界：generation QC 仍不是 SAPIEN settle。

`self_improving/studies/ASPIRE/` 保存 2026-08-31 的 ASPIRE 一手资料、固定上游子模块、完整
实验日志和 held-out harness benchmark。该研究支持“经开发集验证、按触发条件检索的冻结技能
记忆”作为合成契约层候选机制；它不支持论文尺度复现、策略学习或仿真物理成功率提升主张。

2026-08-14 的同学工作区收口把 Yeyuxuan 的完整 RoboLab 分支历史与 20 份来源记录、Yuxin 当前 main/Web/未提交断点续测状态，以及 Bingsheng/Gujie/Yuxin 的独有说明归入同仓库。Yuxin 的第三方资产本体没有进入 Git；`asset_pipeline/receipts/asset_library_301_361.sha256` 只记录 12,047 个文件、约 27.64 GB 内容的精确摘要，`storage_uri: null` 表示它仍不是远端备份。

Jingxiang 上原先并列的 Stage04/Stage05/OpenXSim/AgenticSim 验证工作区已收口到单仓库：可审阅的 JSON、日志和运行脚本进入 `validation_evidence/openxsim_20260716/`，六个工作区的 cache-filtered 完整包进入同仓库 `workspace-consolidation-20260813` Release，逐文件 SHA-256 清单在 `workspace_archives/20260716/MANIFEST.sha256`。MetaSim 不再保留第二份 checkout，而是固定为 `external/MetaSim` 子模块 commit `6947e35`。

AgenticSim 名称有两种历史含义：旧产品仓库已经证明是 TacHarness 的稀疏历史状态，其唯一文件归档进 TacHarness 后本机副本已删除；`sim_adapters/agenticsim_runtime/` 只保留后来非 Git 工作区里的 Isaac 编排脚本，二者不能再混用。

PEARL portal 与 alternate Text2Env 都通过有双亲的历史合并接到主线，来源 tip 分别仍可沿祖先链追溯；精确 source/merge commit 和被排除的本地缓存登记在 `self_improving/source_inventory.json`。散落的 can/basket video anchor 标注则作为小型结构化证据放在 `self_improving/alchedata/artifacts/openxsim/`。
