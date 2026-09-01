# Change Log

## 2026-08-31

- 落地 Workbench Event Timeline v1：`demo/` 通过注入式只读 feed 和
  `GET /api/harness/events` 消费 SQLite journal 的 committed `RunEvent`，支持 global cursor、run
  filter、分页与 reload 恢复；浏览器严格校验事件页，明确区分未配置、历史损坏和响应不自洽。
  轮询只读取事件、不伪造阶段，artifact 只显示 metadata。缓存事件须从当前 journal 重新确认，在途
  请求以代际隔离，64 位 cursor 以十进制字符串传输；SQLite 文件损坏稳定返回 503。`tests/demo`
  36 passed（含 12 项真 Flask + headless Chrome），feed statement/branch 100%；本切片不含 Skill
  submit、System 2、artifact viewer
  或拖拽编排，完整四页面工作台仍在进行中。
- 接通 System 2 → production compile 的可信调度边界：同一 CAS 内重读 planner、base state/context、
  qualification、Invocation/RunState、effective catalog 与 package，重跑 solver 和 static validator；
  完整 history authority 绑定每次状态迁移，成功才产生 receipt-backed delta，blocked/failed 无 delta。
  compile request 还必须等于受信 `task.objective`，完整协调替换 state/context/planner closure 也不能
  把旧结果重绑给新任务；无 Invocation 的 blocked preflight receipt 也走同一门。独立复审发现的
  四类 P1 与该审计缺口均已转成攻击测试；264 项通过，
  application/dispatcher/domain/history 的 statement/branch 均为 100%。这只证明本地
  compile 编排和两个非物理事实，不声明 SAPIEN/真机或已刷新 production qualification。
- 实现 `text2env.validate@2.0.0` 的第一切片：新增 12 项 typed CAS evidence 输入、决定输出与
  `StrictValidateV2CasReader`。reader 在读文件前重验完整输入，固定单一 CAS、逐 raw ref 验证后按
  digest 去重，并拒绝 symlink/FIFO、内容漂移与资源越界；JSON/NDJSON 留给后续权威 typed parser。
  这避免在 byte-authority 层展开泛型树。两模块 statement/branch 100%。当前只建立 byte
  authority，尚未交叉绑定证据语义、
  生成物理决定、注册 Skill 或声明 publishable。
- 新增 `harness.skill_descriptor.v2`，用 `content_bitwise_deterministic` 与
  `evidence_invariant_repeatable` 取代含糊 boolean；v1 snapshot 字节完全不变且仍只允许
  `deterministic=true`。compile 保持 v1，replay 以 v2 声明 evidence-invariant repeatability；
  replay 的固定资格加载器/生成器与 production registration policy 会重读完整 CAS evidence closure，
  不把可构造 inspection 对象当发布权限。核心五模块 2,306 statements / 510 branches 全覆盖；这没有
  生成 checked-in pass bundle，也没有运行真实 900/120 replay。
- 将 production compile 的可复用生成资产从一次运行的 `state_root/asset-library` 分离：
  `CompileApplicationSettings.asset_library_root` 与 CLI `--asset-library-root` 现在显式指定持久资产库，
  默认落到项目内 `self_improving/asset_pipeline/active/data/asset_library`；qualification、测试与实验
  必须传自己的 scratch 库。45 项干净检出专项通过；这只改变资产所有权边界，不把 generation QC
  写成 SAPIEN settle 或物理资格。
- 接出 replay handler/resolver 候选竖切：Invocation 只接受 capability、executor、handler config、
  media verifier、input-specific runtime assets 五项精确依赖，handler 独立重物化/快照并在执行前后
  对账；失败制品保留为 untrusted diagnostics，JSON/PNG/MP4 经事件、evidence、validation 与固定
  MP4/H.264/8bit-420/SAR 复核后才 typed promotion。专项 185 passed、两个模块 statement/branch
  100%；尚无固定 replay qualification/application 或新的 900/120 handler 真跑，descriptor 确定性
  语义仍 open，因此状态只记 candidate vertical slice。
- 增加 replay 媒体的 consumer-side 真解码边界：PNG/MP4 先作为不受信任 bytes 入 CAS，再由
  delegated cgroup + Landlock/seccomp/rlimit 中的最小静态 FFmpeg 通过 held FD 完整解码；帧数、
  帧率、尺寸、SAR 与解码后互异帧不再信 worker JSON 或扩展名。真实历史 120 帧录像解出 114 个
  互异帧；Python 3.11/3.13 delegated 各 211 tests 通过、两模块 statement/branch 100%。这只
  qualification media consumer，不把尚未完成的 900/120 handler/validate/promotion 写成已发布。
- Registry 的 handler context 现在携带 Invocation 已冻结的同一组只读 dependencies，供 replay
  在真实执行时核对资产/运行时身份；这一实现字节变化也触发并完成了 compile 三轮资格刷新。
- A035 改变 scene-gen 执行字节后，旧 compile qualification 被 source-tree 门禁拒绝；在干净
  `7766fee` 上重新执行三轮真实 generator，仍得到 admitted→reused→reused，并以新 source/report
  摘要原子刷新 packaged bundle。static validation 仍为 incomplete，未扩大物理声明。
- 增加 replay 的 CAS runtime-asset snapshot：绑定 resolved/catalog、selected model、空目录与全文件
  identity，解析 URDF/OBJ/MTL/glTF/GLB/COLLADA 引用闭包，运行前后逐树复验；修复 URDF model
  目录错选、CAS 子目录 symlink/rename race、损坏 CAS 无界读取和资产漂移失败类型。专项 100%
  statement/branch，真实 2-step
  RoboTwin/SAPIEN acquisition pass；短 horizon validation fail，未冒充 900/120 发布通过。
- 固定官方 ASPIRE 源码为 `external/ASPIRE@7ba73d3` 子模块，并在
  `self_improving/studies/ASPIRE/` 保存论文/官方资料、源码审计、宿主 harness 审计、复现边界、
  预注册实验、逐步日志和机读结果；明确本机只复现无服务机制测试，未复现论文尺度基准。
- 为核心 runtime validator 增加自声明 `scene_id` 与 `resolved_scene_sha256` equality check 及四个
  缺失/错配攻击用例；runner 产生的一次真实 SAPIEN evidence 通过。该门不签名完整 run/media，
  不能防 producer relabel，也不称完整 provenance。
- 修复 Stage 5 main/legacy/critic/batch 的状态错误：视觉前只写 candidate，visual pass 才 per-file
  atomic rename 为 final；pending/review aggregate 退出 2，默认 static-only 明确写 not_run/nonfinal。
- 同步并发 follow-on：Harness 已有本地 artifact resolver、callback `RunRecorder`、SQLite append-only
  event journal、通用 qualified-version `SkillRegistry` 与 deterministic compiler module，但
  `28333de` 仍无 Text2Env handlers/MCP。
  resolver 的 `file://` 无 allowed-root 且返回可变原路径，因此不是 sandbox 或不可变 CAS
  capability；生成资产 ledger 把未跑 settle 的 QC 标成 `sapien/pass`，不得作物理资格证据。
- follow-on 源码锚包括 `315524f` 的 SQLite journal、`9af9db5` 的 compiler started/completed 事件、
  `28333de` 的 effective-catalog 文件校验。`28333de` archive 的 Harness coverage 为 100%，但默认
  pytest 仍有同名 test collection error，importlib 诊断为 155 passed / 1 behavior-contract failure。
- `284ffb` 提交可显式组装的 `Text2EnvCompileHandler`，`1a1f3d8` 增加 CAS PackageStore，
  `e98e8c1`/`5915315` 接通 parameter-aware compile dependencies。攻击审计证明旧 handler 的 input
  catalog CAS copy 未成为实际 compiler 输入；`ef5e29e` 先冻结 mutation test，`910ccb1` 再切到
  snapshot-only execution，攻击转绿且 Harness 74 passed/100% statement+branch coverage。生成 asset
  admission 仍先于 solve 且 blocked 不回滚，所以不称原子晋升或完整 Text2Env/物理发布闭环。
- `50e8f18` 进一步把 CAS capture 改成单次流式 hash+copy/fsync/原子 rename，并拒绝已污染的同摘要
  对象；`51447da` 要求 qualification `report_sha256` 的 CAS bytes 存在且匹配。后者仍不解释报告
  领域内容或把实际 handler/source manifest 与 descriptor 对账，不能称完整 attestation。
- 在 clean `51447da` archive/checkout 独立复核实现日志：Harness 76 tests 通过但 coverage 99.88%，
  100% 门失败；默认 pytest 与 canonical 平台脚本仍有同名 `test_registry.py` collection error，
  importlib 诊断为 184 passed / 1 CLI behavior-contract failure。因此不把该提交记为绿色快照。
- 后续 `e6ed0ff` 绑定 adaptive replay 的视频尾帧与真实延长终点，`148001d` 恢复旧 CLI failure-stage
  投影契约。固定 `148001d` archive 的 importlib 根测试为 201 passed / 0 failed；默认 collection 与
  Harness 99.88% coverage 仍未闭合，所以仍不记为完整绿色快照，也不把 replay follow-on 算作
  ASPIRE matched repair 结果。
- 封存后固定审计 `4836ebf` qualification loader、`38518e5` wheel packaging 与 `ab03859` RunStore：
  它们是预制 bundle verifier、packaging prerequisite 和 immutable terminal-record adapter，固定树无
  Registry 接线，也未闭合 qualification transaction、resumable EvaluationRun 或 package→run→media。
  clean Harness 为 133 passed / 99.91% coverage，默认 collection 仍失败。
- 审计完成后共享分支继续出现 `c365874`、`8d9a01c`、`587b49f`、`0e6716a`；本轮不追逐移动 HEAD，
  所有 post-close “open/未接线”表述明确固定到 `ab03859`，后续提交须另做 clean-archive 复核。
- 增加六类、开发/held-out 隔离的 ASPIRE 风格 harness 机制基准。固定合成故障集上 validated
  trigger memory 的 mHRC 为 1.00，reactive 为 0.50；该结果不支持物理鲁棒性、LLM 学习或论文
  尺度主张。把 595 passed、6 skipped 明确钉到 E0–E2 快照 `1180aef`，不冒充后续提交的当前
  绿灯；并纠正文档中 rotation drift 3° / resolved rotation error 5° 的混写。

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

Synced through behavior commit 1180aef33936fcf1955923ed2a270e5a775029fb.
