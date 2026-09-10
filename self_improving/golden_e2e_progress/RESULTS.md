# 进度与结果

## 2026-09-10 Golden portable replay

- 固定起点 `2bdeafd836d641b4f14aef316a21fbcd736f55e1`。首个公共 RED 把同一 qualification
  bundle 与 exact 源码 tree 复制到不同绝对路径；旧 loader/factory 因绝对 distribution root 拒绝。
  `0ef1eb17fd9e66126764436077400419c1d79266` 将资格源码改为逻辑 `replay_distribution` root，runner
  纳入 50-file implementation closure，module/runner 与 external operator 文件分账；当前根 mapping 上
  仍逐成员、逐目录链做三轮 nofollow 重走与最终 root 重开。字节漂移、缺/多成员、目录/链接异常、
  不同 tree、schema drift 与错误 external dependency 的负例继续拒绝。
- 第一次真实 relocated Golden 在 production factory 与 compile rev1 后精确暴露 capability 仍哈希绝对
  `PYTHONPATH`；没有复用该失败目录。`487bcfc189e31784461ae0dc43fae5955dbf0d19` 只在
  `PYTHONPATH == local mapped source root` 时把它归一到逻辑根，其他值仍按原字节哈希。capability +
  worker 契约 `197 passed`，`runtime_capability.py` 602 statements / 246 branches 为 100%；replay/P3
  focused `233 passed`。
- 在全新 bundle/scratch/CAS 与 `portable-replay-53vbg3.scope` 中重新探测 capability 并运行官方
  generator。direct 与 production kernel 各一次真实完成 0/900/120/120/12，物理均 pass，23-member
  runtime asset closure、完整事件流与 120/100/114 媒体通过。新 bundle 三文档为
  `9553bf10…041a` / `9315394c…cf03` / `feb70567…c681`，implementation 为
  `990b9dbf…5779`，62-ref closure 为 `92457468…9110`。
- 将同一 bundle 原样复制，并把资格 CAS 逐 digest 写入 68-object fresh CAS；A/B 两个绝对源码路径的
  strict verifier/loader 均 pass，source manifest 不含任一本地 root。随后从 B 路径构造 production
  `ReplayApplication`，在新的 Golden evidence root 真实执行 compile rev1→replay rev2；runtime
  evidence `ddb1cf26…673d` 为 pass。MP4 `a41d0536…12a4e` 独立解码为 120 帧、114 个 decoded
  unique、320×240、12 fps。无 live application 的新进程恢复 revision 2 并幂等返回同一 terminal。
- 正式证据：`docs/evidence/golden-portable-replay-20260910.{md,json}`。该范围仍不包含 validate Skill、
  Genesis native、`genesis.robot_policy@1`、promotion 或跨机器 provisioning；dashboard 三个状态 URL
  仍为 HTTP 404，未伪报同步。最终 focused `431 passed`、Harness `2671 passed, 20 skipped`、root
  `3684 passed, 20 skipped`、39-schema、变更 Python Ruff 与 diff check 通过；仓库级 Ruff 仍有
  2,463 个既有、范围外问题，未在本功能提交批量改写。

## 2026-09-10 P3 GoldenRun qualified compile → replay

- 公共 RED `17e535d` 先固定同一 actor-neutral workflow 的第二个 exact operation：rev0 经真实 qualified
  compile 到 rev1，再从该受信状态中的 EnvironmentPackage/asset catalog 和同一 compile receipt 关联
  构造 replay input，最后经 `GoldenRunHarness.submit` 到 rev2。P3a durable application 与两轮真实 replay
  重签完成后，P3b 功能提交为 `b51bf5783f9ea5cd8e0e4559bef020d934d15d30`。
- 新增 replay-owned `golden_replay_policy.py` 与 `golden_replay_execution.py`；generic aggregate/catalog
  没有新增 replay 分支。policy 只读 parameters 与 base trusted state，只从 terminal typed output 重建
  `replay.runtime_evidence` 一项 claim；媒体留作 supporting artifacts，`fresh_observations=()`。production
  adapter 在构造和每次访问前重验 factory seal，并把 application pending 映射到 actor-neutral pending。
- 用例覆盖 succeeded/blocked/failed、reservation/intent/invocation/terminal 四个恢复窗口、cross-workflow
  与 stale package 在 child intent 前拒绝、typed output/supporting CAS 关联、policy/application authority
  漂移、两个 binding 的顺序/重复、跨 exact Skill 共用 idempotency key，以及 replay 加入后 compile policy
  digest 与既有 compile operation 不变。focused `23 passed`，两个新增模块 125 statements / 34 branches
  均为 100%；compile/replay/qualification 邻接回归 `448 passed`。
- 真实运行第 1 次在任何 child 前因 objective 的 UTC 文本不是模型规范 JSON 而拒绝；第 2 次由类型化
  evidence 生成字节后，同一 `GoldenRunHarness` 真正提交 production compile 与 production
  RoboTwin/SAPIEN replay，revision 为 `0→1→2`。runtime evidence
  `ddb1cf26bb3d9ad0d904519dbe4f0b506918131b13ab1678854e537e68b7673d` 为 pass，900 steps、120-step
  contact window；MP4 `a41d05366050ba0c2af635e69299a12a4b2a0e78ed27490517d27b122fa12a4e`
  为 120/100/114 frames、12 fps。无 live application 的新 Harness 实例重读并重提交原 command，返回
  同一 terminal operation，没有再次执行运行时。
- 正式证据：`docs/evidence/golden-compile-replay-p3-20260910.{md,json}`；外部可观看媒体目录在该页记录。
  本切片不调用 validate Skill，不证明 Genesis native 环境、`genesis.robot_policy@1` 最终门、promotion
  或真机。
- 最终标准 root 为 `3676 passed, 20 skipped in 172.25s`；39 份 Harness schema snapshot、P3 Python
  Ruff/format 与 diff check 通过。全 root 带 coverage 的诊断运行中，两个既有 0.1 秒 capability 大输出
  用例在 coverage 开销下被分类为 timeout，完整结束为 `3674 passed, 20 skipped, 2 failed`；同一 4-case
  节点脱离 coverage 立即 `4 passed`，随后标准 root 全绿。P3 两个新增模块的独立 statement/branch
  coverage 均为 100%。统一平台脚本还在测试前因此独立 worktree 的 OpenReal2Sim 与 digital-cousins
  未初始化而返回 `ready=false`；没有把该环境前置写成 P3 功能通过。

## 2026-09-10 P3a durable production ReplayApplication

- 从固定起点 `7fe5c7e76fd9300014195c3af836f2d08bcd9c92` 建立独立 worktree。公共 Golden replay
  tracer 先以 `17e535d` 固定 compile revision 1 → replay revision 2 目标，但评审发现可直接构造的
  `ReplayApplication` 尚不能充当受信 child authority，因此保持该 tracer 为 RED，先拆出 P3a。
- P3a 的独立 RED `3d26088` 要求 production factory 才能签发 private seal；应用在每次公开读取与
  `invoke_typed` 前重验 exact Local CAS、同一 `harness.sqlite3` 的 journal/run store、Registry authority、
  evidence-invariant registration policy、唯一 replay v2 descriptor、同源 handler/resolver/PackageStore、
  operator roots 与 dependency/config identities。直接构造的 facade 不能通过受信断言。
- `invoke_typed(Text2EnvReplayInput, run_id=UUID4)` 复用 Registry 的 controlled intent → Invocation →
  terminal/event lifecycle；同输入、同 dependency 与同 descriptor identity 返回原 terminal，不再次调用
  handler。UUID 版本错误、ordinary-run 占用、不同输入、依赖漂移、pending lifecycle、异 CAS 或组装漂移
  都映射为稳定 application 错误。
- factory 现在只接受空 root，或只含 exact SQLite authority 与三个 replay-owned work directory 的合法
  非空 root；未知成员、符号链接与成员类型错误均拒绝。失败清理只删除本次新建成员，绝不清空既有
  authority。同 state root 的 factory 组装由 canonical sibling lock 线性化；确定性并发用例证明第二个
  factory 在 publication 失败时不会删除首个成功 factory 的数据库。损坏 SQLite 的底层异常统一封装为
  `ReplayApplicationConfigurationError` 并保留原 cause。测试在同实例、新 factory 实例和独立 Python
  进程中恢复同一 terminal，受控外部
  runtime/media fixture 总计只执行一次；这不冒充真实 RoboTwin/SAPIEN、Genesis 或真机运行。
- ReplayApplication 与资格组装专项为 `103 passed`；该模块 429 statements / 130 branches 均为 100%。
  compile application、Golden compile 与 compile qualification 回归为 `236 passed`。checked compile
  qualification 通过；checked replay qualification 唯一按预期以
  `implementation_file_mismatch: self_improving/harness/replay_application.py` 拒绝。P3a 未改 qualification
  三份文档或 expected hash。
- 基于固定实现 `8e45bd9d755ca68001f3372286cbaff265bbc461`，官方 generator 随后在全新外部
  bundle/scratch/CAS 与临时 delegated scope 中真实执行 direct/kernel 两条 0/900/120/120/12：两者
  均单 attempt succeeded，物理 `pass`、120/100/114 顺序媒体、23-member runtime assets。P3b tracer
  随后暴露并修复 typed output 的 CAS 身份重复，最终重新资格化 closure 为 63 refs；把 67 个 CAS 对象
  逐一按摘要复制到另一个全新 CAS 后，strict verifier/loader 均为 `pass`。
  三份 generator 原始文档及 49-file implementation identity 已独立安装；详见
  `docs/evidence/replay-qualification-refresh-p3a-20260910.md`。本结论仍不覆盖 validate、Genesis、
  promotion 或真机。

## 2026-09-10 P2 GoldenRun.submit compile-only 纵切

- 从固定绿色起点 `e67b656ea97044da5b09ee8323c48e752cd88c76` 建立独立 worktree，先以公共
  `GoldenRunHarness.submit` tracer 冻结 revision 0→1：真实调用 checked qualified
  `text2env.compile@1.0.0` 的 production `CompileApplication`，返回 terminal OperationSnapshot，随后
  从 CAS 重建 ToolResult、唯一 StateDelta、workflow receipt 与 resulting trusted state，并在重启后
  不重执行已提交 child。
- actor-neutral catalog kernel 与 compile-owned binding/adapter/policy 已分离。generic factory 不导入或
  硬编码 `CompileApplication`；compile evidence-policy digest 使用独立稳定源码闭包，所以后续新增
  replay binding 不会只因修改中央 catalog 而改变既有 compile operation 的 frozen policy identity。
  public binding 只暴露只读 Protocol，factory 返回非 dataclass 的 opaque concrete；catalog 消费时重验
  skill/policy/application/input model/live descriptor/child authority。policy 只规范化 parameters，完整
  `RunCommandRequest` 的 command/CAS 身份由 aggregate 原样保留。
- operation SQLite authority 显式持久化 `skill_ref`，lookup/唯一域改为
  `(workflow_run_id, skill_ref, idempotency_key)`，decode 再与 OperationSnapshot 交叉对账；同一 workflow
  的不同 exact Skill 可复用同一 key，同一 Skill/key 的不同 command 仍拒绝。
- `ToolResultV2.state_delta` 只保存 `ArtifactRef | None`，与 workflow receipt 指向同一 CAS delta；读路径
  从 ToolResult 的 ref 解析一次，重算 delta digest，再校验 base state、trusted receipt、有效时间、
  mutations 与 resulting state。发布顺序保持无环：command/qualified execution → child receipt → delta →
  resulting state → ToolResult → workflow receipt → SQLite 原子 terminal/head。
- 四个公开恢复窗口已固定：parent reservation 后、child intent 前可安全继续；intent-only 和
  invocation-only 因无法证明 handler 外部副作用边界而保守保持 running；child terminal 后、parent
  commit 前可只重建 CAS closure 并提交父 head，不再次调用 handler。这里只声明持久边界已证明的恢复
  语义，不扩大为跨任意外部副作用的 exactly-once。
- focused：`test_golden_run.py` + `test_golden_submit.py` + `test_schema_catalog.py` 为 `194 passed`；
  7 个相关模块共 1,386 statements / 402 branches，语句与分支均为 100%。从 repo root 以
  `PYTHONPATH=. python -m script.export_harness_schemas --check` 验证 39 份 Harness schema snapshot；
  direct-script 回归另行证明入口不会解析到其他 checkout。compile
  checked qualification、replay checked qualification 与 production compile application 三个固定身份
  节点为 `3 passed`。Harness 全套为 `2609 passed, 20 skipped`，完整 root 为
  `3622 passed, 20 skipped`。Dashboard 三个约定入口仍返回 HTTP 404，无法同步控制面。
- 明确边界：本切片没有接 replay/observe/validate dispatch、MCP、System 2 model loop、Genesis、
  promotion、VLM fallback 或真机；compile 的 succeeded 也不等于物理 validation 或 publishable。

## 2026-09-10 root demo 兼容失败修复

- 固定起点 `461b05d9efea11803ce5f35c3d7f2a089653d54f` 的完整 root 首轮稳定得到
  `3516 passed, 20 skipped, 2 failed`。失败节点为
  `test_submit_fails_closed_when_the_terminal_run_state_cannot_be_reloaded` 与
  `test_submit_fails_closed_when_committed_history_does_not_match_run_state`；秒级复现为
  `2 failed in 0.35s`。
- 两项测试在调用 `WorkbenchCompile.submit` 之前向 `harness_run_states` 安装 trigger。P2 的
  `SQLiteRunStore` exact-layout/zero-trigger 门会在写入 Invocation 和调用 handler 之前拒绝该布局，
  所以 observed exception 已提前为 `RunPersistenceError(RunStoreCorruptionError)`，demo 只会投影为
  unavailable；旧测试不再到达其名称声明的 terminal missing/history mismatch seam。
- 修复选择更新过时测试，不改生产代码：每个测试都先穿过真实 `CompileApplication.compile`，再在其
  返回 terminal state 后、Workbench 二次读取前删除该 run 的 terminal row 或最后一个 committed
  event。第一纵切形成 `1 passed, 1 failed`，第二纵切完成后为 `2 passed`；两项后置完整性语义仍由
  公共 submit seam 直接验证，P2 的 pre-Invocation layout 拒绝保持不变，既有 Registry/RunStore
  负向用例继续保护该边界。
- focused：`tests/demo/test_harness_compile.py` 为 `192 passed`；
  `demo/harness_compile.py` 为 531/531 statements、214/214 branches，均为 100%。Harness 全套为
  `2505 passed, 20 skipped`；完整 root 不排除节点为 `3518 passed, 20 skipped in 151.50s`。
- 本切片没有稳定生产行为变化，因此 reader-facing repo-docs 无需修改；compile/replay 资格文档、
  hash、签名、settings 和 evidence 均未触碰。

## 2026-09-10 当前集成树 replay qualification 独立重签

- 固定起点 `13f654e72e0204993355fa098e240698e1d8b085`。旧 replay bundle 先以
  `implementation_file_mismatch: self_improving/harness/__init__.py` RED；同次 preflight 中
  production import tracer 已随 compile 重签恢复。
- Python 3.10.20 在全新外部 bundle/scratch/CAS 与临时 delegated scope 中执行官方 fixed
  can-on-plate。candidate-direct 和 production-kernel candidate 均单 attempt succeeded，各有 14 个
  完整 runtime events，完成 `0/900/120/120/12`。
- 两次物理 validation 都为 pass、`fail=0/not_run=0`；视频均为 120 sequential frames、100 个
  source-unique frames、114 个 decoded-unique frames。23 个 runtime asset members 全部复核；
  evidence closure manifest 含 62 refs。
- 49-file implementation SHA-256 为
  `f6bc59e628bd72ff3b5195915a6bea53c7656154d6c71bd135eb6dd39347c604`，三阶段 source snapshot
  无变化；14 个 replay-bound model↔schema exact gate 通过。66 个新 CAS 对象逐摘要复制到另一个
  fresh CAS 后，严格 verifier/load 均 pass。
- 首次 scope 包装在生成器前因换行转义失败；首次 scope 外 reload 又按设计因 operator locator
  不存在而拒绝。两次均未改资格文档，最终在活动的同名 delegated scope 完成 strict reload。
- 三个原 deselect 节点不排除为 `3 passed`；P0 replay/loader/schema 加 checked bundle 为
  `172 passed`；完整 Harness 为 `2505 passed, 20 skipped`。32-schema、Ruff、replay 相关文件
  format 与 `git diff --check` 均通过；扩大到既有 `test_golden_run.py` 整文件的 format check 在
  HEAD 与工作树都返回 1，本切片未机械重排两个 expected digest 之外的基线代码。
- 完整 root 为 `3516 passed, 20 skipped, 2 failed`。两项失败是既有 demo 负向用例先安装 SQLite
  trigger，而 P2 RunStore 当前会在 Invocation 前拒绝 trigger-bearing authority；旧断言期待后续
  terminal reload/history mismatch。本 replay 切片不改实现、不弱化测试，按实际结果保留。
- 证据：
  `docs/evidence/replay-qualification-refresh-20260910.{md,json}`。该结果不扩大为 validate v2、
  MCP、Genesis、promotion、portable install 或真机资格。

## 2026-09-10 当前集成树 compile qualification 独立重签

- 固定 distribution 为干净
  `2841d0a17c9df12c434178969544dc7ed8e87fc9`。旧 checked-in gate 先真实 RED：
  `implementation_file_mismatch: self_improving/harness/qualification.py`。
- 用 `/home/jingxiang/miniconda3/bin/python`（Python 3.13.12）在全新外部
  bundle/scratch/CAS 运行官方 generator；固定 purple pedestal、seed 77、admission date
  2026-08-31 的三轮处置为 `admitted -> reused -> reused`。同一 asset id、package、ledger；
  第二/三轮 Invocation 与 typed output 相同，第一到第二轮只改变 `asset-library-state` dependency。
- 生成器报告绑定当前 11 个实现文件，implementation SHA-256 为
  `0394575ff26f8da601507416573656b3fb04d7ca06fc45ff3f8195005f470b7f`；执行前后 source
  snapshot 相同。只读 verifier 与全新外部 CAS strict loader 均 pass，生成的三件套逐字安装后
  checked-in loader gate 为 `1 passed`。
- compile qualification 两个专项文件为 `93 passed`；32 份 Harness schema snapshot、相关四个
  Python 文件的 Ruff/format 和 `git diff --check` 均通过。正式证据见
  `docs/evidence/compile-qualification-refresh-20260910.{md,json}`。
- ClawCross 与 dashboard 三个必读状态地址均返回 HTTP 404，无法同步控制面。本切片没有修改实现
  源码，没有启动 replay；static validation 仍 incomplete、资产仍 pending_settle，replay
  qualification 仍为下一独立工作。

## 2026-09-10 P0+P2 隔离集成

- 从主仓库固定 `ea26524cae7b4a18b5576150376eb6ec1ddddf7b` 新建隔离 worktree，依次合入
  `6570c82`、`b24c827`、`2ac283f`。主工作树的用户修改未被暂存、覆盖或提交；
  `docs/evidence/replay-production-qualification-20260902.md` 保持基线字节。
- 公共 root/schemas facade 保留 P0 的 lazy seam，并在 closure 外的全局映射中合并 P6 的
  `AssetSourceSnapshotManifest` / `AssetStageRequest` / `AssetStageResult` 三份 schema 与 stage 公开符号；
  P2 的 Registry/Application/RunStore/EventJournal/actor-neutral domain 实现保持不变。schema gate 为
  `verified 32 Harness schema snapshots`。
- P0 focused 在精确排除依赖 stale compile bundle 的 production import tracer 后为
  `170 passed, 1 deselected`；P2 focused 四文件为 `197 passed`。compile checked gate 按预期以
  `implementation_file_mismatch: self_improving/harness/qualification.py` 拒绝；production import
  tracer 在进入 trace 前因同一 compile qualification 原因拒绝；replay checked gate 按预期以
  `implementation_file_mismatch: self_improving/harness/__init__.py` 拒绝。
- Harness 在精确排除上述 3 个节点后为 `2502 passed, 20 skipped, 3 deselected`；
  `application.py`、`event_journal.py`、`registry.py`、`run_store.py`、`system2/domain.py`
  合计 1,447 statements / 478 branches，语句与分支覆盖均为 100%。fixed-range Ruff、format 与
  integration diff 完整性门通过。
- 无边界 `ruff check .` 仍报 2,463 个基线问题，主要位于本 fixed range 之外的历史
  `validation_evidence/`、`demo/` 与 `scene_gen/`；本集成的 28 个变更 Python 文件逐个通过
  `ruff check` 与 `ruff format --check`，未扩展修改基线文件。
- 本切片不重签、不执行真实 runtime。下一阶段必须在当前固定树上真实重新生成 compile
  与 900/120 replay 资格、严格 reload，然后不排除地跑通三个节点。当前结果不证明新的
  RoboTwin/SAPIEN、Genesis 或真机能力。

## 2026-09-10 P6 staged-loader 合入与未完成边界

- 早期固定候选 `bd61a9e1504b1462c1bb24799a38e76f5cdf26e4` 在独立 worktree 中完成 166 个 focused
  cases；三个相关生产模块合计 744 statements/260 branches，均为 100%。根套件显式排除既有 stale
  replay qualification 单项后为 `3293 passed, 20 skipped, 1 deselected`。该候选尚未合入。
- 从固定提交重建的 source/CAS stage 为 21 members、57,290,434 bytes、14 representation
  closures；真实 RoboTwin `create_actor`/SAPIEN 无渲染 probe 对 7 个模型各运行 900 steps，共 6,300
  steps，均记录 finite pose、contact 和 nonzero impulse。外部 report SHA-256 为
  `6e9ec1c6b294ab389c5579a4f9695c966dd2019ac2754130280b8ffd788cfb9e`；它明确没有执行 Genesis、
  runtime qualification、promotion 或 can-on-plate golden case。
- 主窗口在固定提交后独立复现两个 P1 阻断：`write_new_report` 的失败清理在 parent directory 被替换时
  会删除替换路径并遗留原 partial；deep verifier 可接受重新计算全部自洽 hash、但从 representation
  closure 中删去一个 GLB 引用 member 的伪造结果。两项均已冻结为下一轮 RED；修复并复审前，真实
  SAPIEN 运行只证明候选 loader/physics path 曾执行，不能升级为可信 stage gate。
- 一名 fixed-diff 只读审查 agent 被 OpenAI 安全系统拒绝。该具体审查已按仓库规则永久保留为
  blocked，没有重试、改写或重新委派；用户随后允许继续其他工作。此事件不是通过结论，也不改变
  上述两个主窗口阻断。
- 两个反例的独立 follow-up 固定提交为
  `8c1c94afae8292dc94ee1d2b08fb7fb08a94bc06`。focused 为 `183 passed, 1 skipped`，相关三个
  生产模块合计 780 statements/278 branches 均为 100%；根套件显式排除同一个 stale replay
  qualification 项后为 `3310 passed, 20 skipped, 1 deselected`。Ruff、31 份分支基线 schema snapshot 与
  diff check 通过。
- 从 `8c1c94a` 新建外部 evidence root 后重跑，stage result SHA-256 仍为
  `a2e786131bb33a3eacd7afd0403dca45a6353a2f6d54aebc100ecb19fda09f23`，stage binding 为
  `fcf73ec7851e42654a2264d818bfcc60d091ad3669429ecee7f5b7ccd7e0eb89`；真实 7×900-step report
  SHA-256 为 `c7a0ac85e1425b12041ab0d91833a0bfb15d57ba04f2b5549ff2fe9d1ecafc5d`。报告无已知本机
  absolute-path 泄漏，source/CAS/ledger 未变化。主窗口逐项复核新增 fixed diff 后未发现新的同级
  blocker，并按顺序合入为 `8bd0d33`、`5b5ae78`、`d9df27b`、`16eb232`、`52a21ac`；先前被安全
  系统拒绝的那一份审查仍不作为通过证据。
- 合并后的主分支 schema gate 为 32 snapshots；focused 为 `256 passed, 1 skipped`，四个核心模块
  845 statements/296 branches 全部 100%，Ruff 通过。完整根套件为
  `3360 passed, 20 skipped, 1 failed in 145.88s`；唯一失败仍是已登记的旧 replay qualification source
  identity，首个报错文件是用户修改的 `IMPLEMENTATION_LOG.md`，且 P0 最终仍需在新实现树上真实重签。
- 该切片仍明确不证明 can-on-plate、runtime qualification、Genesis 或 promotion；下一资产纵切是
  collision provenance/settle，不能由 exact bytes 与 ground contact 直接推断。

## 2026-09-10 P0 replay source-closure 候选

- 候选 `d664b046f7868ecce5e0ecd0fc6574d8dcc911da` 把 replay qualification 从整个 Harness 树改为
  显式实现闭包，并用 fresh-process 的真实 application/replay 装配与 qualification-generation 路径
  检查 import trace；旧 checked-in bundle 继续 fail closed，没有刷新 expected hash 或资格文档。
- 候选基于 `660ae49`，而 P1/P2 后续会继续修改 Registry/Application、schema catalog 与公共 façade，
  因此当前只作为待重放设计，不直接合入；最终必须在 P2 固定实现之后重新审查闭包并运行真实固定
  replay 后一次性重签。

## 2026-09-10 P3 同-run 链只读审计

- 第一条后续 tracer 已收敛为 `start → text2env.compile@1.0.0 → text2env.replay@1.0.0 → read`，同一
  workflow 依次推进 revision 0/1/2。replay 的 package 必须逐字来自 revision 1 的可信 state；两个
  child run 使用不同 identity，但 command、qualified execution、Invocation、RunState、ToolResult、
  StateDelta 与 operation receipt 必须形成一条连续 predecessor/CAS 链。
- P2 aggregate 内核必须以 `QualifiedApplicationAdapter` + per-Skill evidence policy 泛化；P2 可以只
  注册 compile，但不能在 submit/恢复/验证内硬编码 Text2Env 类型。P3 第一笔只增加真实 replay
  adapter，不复制旧 System2 dispatcher 分支，也不提前实现 MCP。
- fresh observation 可以与同 key 的 state upsert 共存，但只有 observation receipt、
  `ToolResult.fresh_observations` 与 StateDelta 中 `source_kind=fresh_observation` 的 key/value/source/time/
  TTL 全等时才能推进状态；仅移除“不允许重叠”规则不构成信任证明。
- 现有 portable run receipt 是证据可携带闭包，不是新机器可重新执行的完整环境包。最终 portability
  仍须屏蔽原资产根、从新随机根/新进程只用 CAS materialize 并重新执行。当前 replay v1 的 backend
  是 RoboTwin/SAPIEN，不能计作 Genesis 或 `genesis.robot_policy@1`。

## 2026-09-09 初始化

- Dashboard/ClawCross 项目状态读取：失败，三个公开状态 URL 和 Harness dashboard 均返回 HTTP 404。
- 当前分支：`worktree/bingsheng`。
- 当前 HEAD：`d8b3787`（`chore(portal): remove PEARL evidence portal`）。
- 当前工作树已有用户修改和未跟踪文件；本任务不会批量暂存、还原或覆盖这些内容。
- 本仓库的本地 `worktree/gujie` ref 指向旧祖先 `a25bc58`，不能代表 Gujie 当前实现。
- Gujie 的真实独立克隆位于
  `/home/jingxiang/gujie/gen-env`：当前 HEAD `a8ced27`，其 `origin/worktree/gujie` 同样指向
  `a8ced27`；该提交比已合并 bingsheng 的 `4f6ef23` 多 52 个提交。该工作目录另有大量未提交
  Genesis adapter、测试和文档修改，审计必须把 committed ref 与 dirty workspace 分开，且保持只读。
- 历史 `gujie-x2env-design.md` 明确自称“架构设计阶段”，不能作为已实现 x2env 的证据；真实实现
  资格必须从上述独立克隆的代码、测试和运行产物重新判断。
- 功能基线、Genesis 可用性和路由成功率：待调研后测量，历史测试数字不冒充当前 HEAD 基线。

## 2026-09-09 当前工作树测试基线

- 命令：`pytest -q`
- 结果：`3015 passed, 19 skipped, 1 failed in 142.21s`。
- 唯一失败：`test_checked_in_replay_qualification_matches_current_source_identity`。
- 原因：当前用户修改的 `self_improving/harness/IMPLEMENTATION_LOG.md` 与
  `text2env.replay@1.0.0` qualification manifest 固定的 bytes/SHA-256 不一致；fail-closed 行为符合
  设计，但当前工作树不是可晋升基线。
- 该数字只是当前共享工作树观察值；不能与后续切片测试相加，也不能代表 clean HEAD 或 Genesis
  真实运行通过。
- `python -m self_improving --json`：`ready=false`，因为 HEAD 已删除
  `apps/pearl_evidence_portal`，但 registry 仍把它列为 required；统一平台门当前在测试前失败。
- 当时的 20 份 Harness schema snapshot gate 全部通过；当前从 repo root 使用
  `PYTHONPATH=. python -m script.export_harness_schemas --check` 复核当前 checkout。
- 当前默认 Python 不含 Genesis。Gujie 独立克隆已有隔离环境
  `/home/jingxiang/gujie/gen-env/venv/genesis`，可导入 Genesis `1.3.3`，源码绑定到该克隆的
  `external/genesis-world`；这只证明依赖可导入，不证明任何 golden case 已运行。
- 在 Gujie 当前 dirty workspace 上运行默认离线 suite：
  `venv/genesis/bin/python -m pytest -q self_improving/sim_adapters/genesis/tests`，结果
  `770 passed, 55 skipped in 230.98s`。跳过项包含显式 opt-in 的真实 Genesis/模型/媒体用例；该结果
  证明大量确定性合同测试可用，但不计作真实物理或 sim-ready golden-line 证据。

## 2026-09-09 Gujie 现存运行产物初检

- 文本任务“场景中有一张桌子。上面放着一个黄色杯子。整体摆放简洁。”的 `TaskOutput.verify()`
  成功复核 1,925 个 manifest 文件，终态为 `physics_passed`。
- 其 Genesis 物理报告记录 `simulation_executed=true`、1,500 steps、桌/杯完整指标通过；最终 orbit
  媒体为 180/180 个唯一解码帧、30 FPS、6 秒。主线程已目视检查 overview：内容为桌面、黄色杯子
  和简洁背景。
- 该产物来自 Gujie dirty workspace，不在当前 Harness run/Registry/ToolResult/portable receipt 中，且
  报告含部署绝对 locator；目前只能作为可接入的真实 Genesis 竖切，不是本任务最终 golden case。
- 图片重建任务“从鼠标原图重新重建并保留 SimFoundry 原始支撑平面”仅到 `scene_built`；preview
  可见重建鼠标，但 physics/final_render 均为 `not_run`，遗留失败为 native-service capability preflight。

## 2026-09-09 并行审计复核

- System 2、portable receipt、validate-v2 和 replay bridge 专项：`756 passed in 24.01s`。
- Asset ledger audit/migration/settle trust 专项：`87 passed`；asset admission：`104 passed`。
- 动态 ledger audit：历史 asset library `audited=66 clean=1 violating=65`；upstream
  `audited=97 clean=0 violating=97`。历史 162 份合计恰好 4,082 条违规。
- `migrate_v3 --dry-run`：预期 exit 1，`migrated=0 failed=163`，确认 migration 与 runtime
  qualification 之间存在 bootstrap deadlock。
- RoboTwin 827 个 representation primary 中 825 个可按 SHA-256 从本机源恢复，2 个 USD 缺失；
  第三方旧副本只有 27 个资产 cohort 可进入 exact-byte staging。
- 详细 claim/evidence/caveat 见 `AUDIT.md`。本次审计未修改 Gujie workspace、ledger 或资产字节。

## 2026-09-09 Codex 自动化可用性预检

- 本机：`codex-cli 0.153.4`，提供 `codex mcp`、`codex exec --json` 和 `--output-schema`。
- 只读、无工具、`--ephemeral` 最小真实 Codex 调用 exit 0，在 6.22 秒内返回精确 JSON
  `{"codex_preflight":true}`；JSONL 包含 start/turn/agent-message/completion 事件。
- 此预检只证明当前身份可启动真实 Codex 非交互回合和取得 usage，不证明 MCP 工具调用、视觉理解、
  路由、Genesis 或 golden E2E。会话 identity 不写入公开进度或 dashboard。
- 官方 Codex MCP 文档确认 STDIO/Streamable HTTP、server instructions、项目级 config、tool timeout 和
  per-tool approval 配置；正式证据仍须由当前仓库 MCP server 的 negotiated clientInfo 与 tool-call
  receipts 对账。

## 2026-09-09 契约与实验设置确认

- 用户确认 S1–S6 公共 TDD seam、exact qualified Skill MCP 和 `genesis.robot_policy@1` 最终门。
- 用户允许新增 optional dependency `mcp>=2.2,<3`，但依赖只能在 P4 的正常 feature 切片中冻结；
  autoresearch 循环本身不安装或升级依赖。
- Autoresearch 已确认 36 个 routing cases、12 个 execution cases、primary
  `/metrics/closed_loop_success_rate`（higher is better），以及最多 30 次或 12 小时的首轮预算。
- 这表示允许开始 TDD，实现完成前仍不能声称 MCP、Genesis robot-policy 或 golden E2E 已通过。

## 2026-09-09 P6 只读资产修复计划 tracer

- public seam：S5 `AssetRepairApplication.plan(AssetRepairPlanRequest) -> AssetRepairPlan`；本切片不实现
  `stage` 或 `promote`。
- 独立双轴 review 先拒绝原实现：任意 caller CAS 可自报 exact、duplicate-key/non-canonical JSON 可通过、
  public plan 的 totals/disposition/next-step/followup 不自洽、Windows drive 与 `.` 可通过、missing 被错误
  导向 reacquire，且 v1 不能表达未来 full baseline。修正后的 focused RED 曾为 import error，随后在第一轮
  GREEN 后以 31 个失败暴露 canonical fixture 和旧错误预期，均保留在本次过程记录中。
- 冻结 canonical fixture：`tests/fixtures/asset_repair_tracer_inventory.json`，5,859 bytes，SHA-256
  `d19dc71323a7c94de129f5217efc158a9076fd70c762dece6b7c7395825d4afa`。两份 ledger 的 hash/bytes 与
  9/49 violation 已独立复算；主线程也对本机先前 replay materialization 的 14 份 visual/collision GLB
  重算 SHA-256/bytes，全部与 fixture 的 observed fields 一致。
- 证据限定：这些 GLB 没有 committed dataset revision，也尚未由本切片复制进 CAS；inventory 因而明确
  `byte_probe_source=local_unversioned_robotwin_assets`、`byte_probe_revision=null`。Application 只接受
  组装时显式给定的 trusted inventory ArtifactRef，并重验 exact `LocalArtifactStore`、CAS 与 canonical
  bytes；输出固定 `probe_bytes_in_cas=false`，不把本机观测冒充 portable recovery closure。
- tracer 结果：`003_plate` 为 9 条结构违规、2/2 observed digest matches；`071_can` 为 49 条结构违规、
  12/12 observed digest matches。合计 `planned_ledger_count=2`、`planned_violation_count=58`、
  `planned_representation_count=14`，下一步均为 `stage_and_rehash_observed_primary`。
- hash 相同但旧 size 错时进入 stage/rehash + remeasure；hash mismatch 或 missing 都只能
  `build_new_identity_or_retire_asset`。inventory 显式冻结来源总体 `162/4,082/1,101`，public plan 同时
  记录 source population、inventory sample、planned selection 三层 totals；只有 scope 为
  `full_baseline`、inventory totals 等于来源总体、且本次完整选择 inventory 时，才允许
  `full_baseline_evaluated=true`。把 2-entry tracer 改标签冒充全量、选择部分 inventory 或单字段篡改
  totals 都 fail closed。Pydantic document 只验证语法和本地可重算不变量；协调篡改整份 plan 仍可能
  形成内部一致文档，跨文档真实性必须由 exact trusted `AssetRepairApplication` 解析 `inventory_ref`
  重建，后续再由 S1 receipt 绑定，不能把裸 plan 当成独立证明。
- 其他攻击覆盖：untrusted/missing/non-canonical/duplicate-key inventory，非 CAS ref、错误 media/schema、
  未知/重复/乱序 asset、重复/乱序 debt detail、availability/observed identity 冲突、绝对/逃逸/反斜杠/
  Windows-drive/`.`/重复分隔符/尾随分隔符/C0/DEL/U+2028/U+2029 path，以及 line-separator 绕过
  dot-segment 检查；portable path runtime 与 JSON Schema 已用同一攻击样本对齐。另用 Node
  ECMA-262 `RegExp(..., "u")` 读取 committed schema，确认四个 Unicode line-separator 攻击均被拒绝。
- focused gate：
  `pytest -q tests/self_improving/harness/test_asset_repair.py tests/self_improving/harness/test_schema_catalog.py --cov=self_improving.harness.asset_repair --cov=self_improving.harness.schemas.asset_repair --cov-branch --cov-report=term-missing --cov-fail-under=100`
  为 `79 passed in 0.85s`，两个新增模块 statement/branch 均为 `100%`；28 份 schema snapshot、Ruff
  和 `git diff --check` 通过。Harness 全域排除已单独记录的 stale replay identity 门后为
  `2118 passed, 19 skipped in 56.89s`；根套件以同样的单项显式排除运行，为
  `3131 passed, 19 skipped in 155.31s`。本切片没有改 expected hash 或伪造重签。
- 主张边界：fixture 只覆盖 2/162 ledgers、58/4,082 violations、14/1,101 primary probes；它没有
  重放全量 scanner，也没有把资产字节入 CAS、写 ledger、枚举 loader closure、执行 settle/Genesis 或
  产生 qualification/promotion receipt。`162/4,082/1,101` 也只是 trusted inventory producer 声明，尚无
  scanner/manifest attestation，因此本切片只能算 E0 contract-tested + local-observation tracer。

## 2026-09-09 P0 readiness 修复与 clean baseline

- RED：删除 PEARL portal 后，公共 `python -m self_improving --json` 仍把它当 required module，退出 1。
- GREEN：保留历史 module identity 供旧消费者发现，但设为 `required=false`；其他 required modules
  不变。focused registry tests `3 passed`，当前共享 checkout 的真实 audit 为
  `ready=true/required_failures=[]`，portal 明确为 `missing`。
- Feature commit：`5ac9108 fix(platform): align readiness after portal removal`。
- Detached clean worktree 根套件：`3016 passed, 19 skipped, 1 failed in 143.26s`。唯一失败仍是
  `text2env.replay@1.0.0` 的 checked-in qualification source identity；clean tree 首个漂移文件是
  `self_improving/harness/__init__.py`，证明问题早于共享工作树的未提交 IMPLEMENTATION_LOG 变化。
- 原 2026-09-02 qualification settings、CAS、解释器、资产和媒体工具仍在本机，但其 delegated cgroup
  已不存在。不能复写三份资格文档或更新期望 hash；最终在实现树稳定后用新隔离 scope 跑真实固定案例
  并重签，或者显式撤销旧 Skill 资格。

## 2026-09-09 三方整合来源台账

- 新建 `docs/integration-provenance/`，以功能级 `integration_id` 分别记录 Bingsheng、Gujie、Yuxin
  的原始来源、第三方上游、Harness 整合责任、固定 commit、dirty snapshot、目标路径和验证状态。
- 初始台账只把当前 Harness/合同和 Yuxin 历史 active tree 标为 `integrated`；Gujie x2env/Genesis
  以及 Yuxin 面向新 Skills 的复用能力仍为 `candidate`。
- 当前没有任何新条目标为 `runtime_pass`：资产 ledger 债务、Codex/MCP 同-run 闭环和当前 Harness
  内的真实 Genesis 最终能力门均尚未完成。
- 根 `AGENTS.md` 已要求后续每个来源整合切片与台账更新同一提交，防止最终集中补写造成来源漂移。
- 三份人员档案已扩展为模块/功能矩阵：Bingsheng 细分 Text2Env 加固、Harness schema/CAS/Registry、
  compile/replay/validate、可信状态、Golden workflow、资产修复、Codex/MCP 与总体整合；Gujie 细分
  x2env/URDF/SimFoundry/Genesis adapter；Yuxin 细分检索、筛选、转换、ledger、catalog 与跨仿真验证。

## 2026-09-09 P1 S1 workflow-start 纵切

- 首个 RED：公共测试导入 `self_improving.harness.golden_run.GoldenRunHarness` 时得到
  `ModuleNotFoundError`；随后只实现 S1 的 `start()`，没有预先实现 submit/read/dispatch。
- 安全边界：调用方提交的是按 SHA-256 绑定的 `harness.world_fact_evidence.v1` 与 registry snapshot，
  不能直接提交或自称 `TrustedWorldState`；Harness 解析 canonical bytes、校验 `user_input` provenance
  和 `request.*` namespace 后自行构建可信状态。
- 输出同时保留逻辑 `state_sha256` 与状态 JSON 的 CAS `state_ref`，并产生不含 planner/provider/model
  身份字段的 `harness.workflow_start_receipt.v1` 及 revision 0 父快照。
- schema catalog RED：新增测试要求 23 个精确 schema 时为 `5 failed, 1 passed`；接线并导出 3 份新
  schema snapshot 后变绿。
- focused 验证：`19 passed in 0.53s`；`golden_run.py` 52/52 statements、8/8 branches，
  `schemas/workflow.py` 100/100 statements、14/14 branches，均为 100%。
- 攻击测试覆盖非 canonical JSON、伪装 fresh observation、错误 namespace、非 UTC clock、非 CAS/错
  digest URI、错误 media/schema、重复或乱序 initial evidence，以及非法 snapshot 时间序。
- 本切片只证明可信父 workflow 的启动证据链；尚不证明幂等恢复、compile/replay/validate 调度、MCP
  transport、Genesis 或最终晋升。

## 2026-09-09 P1 durable start/idempotency 纵切

- RED：跨 Harness 实例重复 `start()` 时原实现没有 durable idempotency authority，且公共
  `GoldenRunConflictError` 不存在；随后以 `(principal_id, idempotency_key)` 为唯一键，在 SQLite
  `BEGIN IMMEDIATE` 事务内执行 query-or-create。
- 相同 canonical request digest 返回原 snapshot，测试用会主动失败的 clock/UUID factory 证明 retry
  没有重新取时钟或分配 identity；同 key 异 request 在新 CAS/identity 前以
  `HARN_IDEMPOTENCY_CONFLICT` 拒绝。
- 恢复路径重验 canonical SQLite snapshot、registry/state/receipt/input CAS closure、逻辑 state digest
  与 start receipt/request binding；非 canonical DB bytes、跨记录 swap、CAS bytes 漂移和不一致 revision
  均 fail closed。workflow/operation identities 同步收紧为 UUID4。
- `GoldenRunHarness` 只接受 exact `LocalArtifactStore`，并已从 `self_improving.harness` 公共 façade 导出；
  protocol lookalike 不能绕过 CAS authority。
- focused 验证：workflow + schema catalog `29 passed in 0.68s`；`golden_run.py` 112/112 statements、
  24/24 branches，`schemas/workflow.py` 99/99 statements、14/14 branches，均为 100%；23 份 schema
  snapshot check 与 Ruff 通过。
- 审阅仍发现两个下一切片 blocker：RegistrySnapshot 目前只被 resolve、未 strict parse/资格闭包验证；
  start receipt 还没有 canonical request ArtifactRef，且 RunSnapshot receipt-head 仍锁死为 start receipt。
  因此本切片不声称完整可重放 workflow 或 exact qualified dispatch。

## 2026-09-09 P1 Registry/request 审计闭包纵切

- RED：把内容为非 JSON 的 CAS 对象伪标为 `harness.registry_snapshot.v1` 时，旧 `start()` 只做
  artifact resolve 便成功；同时 start receipt 只保存 request digest 而无 request ArtifactRef，且
  `RunSnapshot.receipt_head` 永久限定为 start receipt schema。后续契约复核又以 5 个 schema-catalog
  失败锁定 qualification report 已被 Registry 引用、却没有公共 schema identity/snapshot 的遗漏。
- GREEN：新增正式 `RegistrySnapshot`，只含按 MCP tool name 排序且 skill/tool 唯一的 qualified entries；
  每项 strict-canonical 解析 descriptor、qualification、qualification report，并交叉绑定 exact skill、
  MCP tool、implementation、case、regression command、status 与 schema catalog。缺 CAS、坏 canonical
  bytes、伪造版本绑定和未知 schema 分别给出稳定的 unavailable/input-invalid/unqualified/drift 错误。
- 当前真实 fixture 只列 `text2env.compile@1.0.0`；旧 replay qualification 有 source drift，目标
  text2env v2、image/video 和 Genesis Skills 尚未 qualification，因此没有被提前列入可用目录。
- canonical `RunStartRequest` 现在先入 CAS；start receipt 和 parent snapshot 都保存其 ArtifactRef，恢复时
  重算并核对 request、state、registry 与 receipt 全闭包。receipt head 接受精确
  `harness.workflow_<name>_receipt.v1` family，为后续 operation/transition 留出版本化演进空间。
- 验证：`48 passed in 0.94s`；`golden_run.py` 147/147 statements、28/28 branches，
  `schemas/registry_snapshot.py` 45/45 statements、12/12 branches，`schemas/workflow.py` 112/112
  statements、20/20 branches，公共 qualification-report schema projection 7/7 statements，均为
  100%；25 份 Harness schema snapshot、Ruff 与 `git diff --check` 全部通过。projection 没有修改
  已合格 compile 所绑定的 `qualification.py` 源码，compile qualification loader 仍通过。Harness 全域
  排除已单独记录的 stale replay qualification 身份门后为
  `2045 passed, 19 skipped, 1 deselected in 51.35s`。
- 限定：该闭包能拒绝不一致或损坏的调用方快照，但还没有与运行中 Registry/handler 的安装身份取交集；
  P2/P4 必须补这层生产 trust root 后，才可把 snapshot entry 暴露成 exact callable MCP Skill。

## 2026-09-09 P4 官方 MCP 2.2 隔离 spike

- 项目环境未安装 MCP；在隔离临时环境验证 `mcp==2.2.0`，没有修改项目依赖或 lock。官方 v2
  low-level `Server` 与 `Client` 的进程内和真实 stdio 两条最小路径均协商协议 `2026-07-28`，并完成
  exact tool list/call；这只证明 SDK seam，不证明项目 Harness MCP 已实现。
- 选择 low-level Server：adapter 只做 SDK type 与 S1 public type 映射，所有 schema/canonical/receipt/
  idempotency 校验仍由 Harness 负责；不提供 generic `skill.invoke`、alias、latest 或版本范围，也不在
  MCP server 内调用 Codex。
- SDK server 只广告 input schema，不替项目执行入参验证；timeout/cancel 后 durable operation 也不能
  假定回滚，caller 必须凭 command/idempotency identity 从 workflow resource 恢复。
- `uv lock --check` 当前已因历史 metadata 漂移失败，CI 的 editable pip 安装也没有消费 lock。P4 加入
  optional `mcp>=2.2,<3` 时必须同时更新 frozen runtime/CI lane，并记录 mcp、mcp-types 与 negotiated
  protocol 的精确身份；在 S1 `submit/read` 和真实 Registry trust root 完成前不写假 adapter。

## 2026-09-09 P1 durable workflow read/store 纵切

- public seam 保持 S1：新增 strict `RunReadRequest(schema_version, principal_id, workflow_run_id UUID4)`，
  `GoldenRunHarness.read(request) -> RunSnapshot`，以及调用方显式构造/注入的
  `SQLiteGoldenRunStore(path, busy_timeout_ms=5000)`。Harness 不再从 CAS root 隐式创建私有数据库；
  当前 `read` 明确只恢复 revision-0 start head；原有 `start`、跨实例幂等和 request/receipt/CAS
  复核语义保持通过，未来 operation head 不在本切片能力声明内。
- TDD RED 依次为：`RunReadRequest` import 不存在；公开 SQLite store module 不存在；Harness 不接受
  `workflow_store`；有效 64 位 SQLite start-request identity 被篡改后 `read` 仍静默成功；旧私有四列表
  无法由新 store 读取；旧 logical request digest 被迁移丢弃；只篡改 idempotency lookup key 能创建第二个
  workflow；两个线程并发 bootstrap 新库偶发 `database is locked`。这些 RED 分别由双 request identity
  binding、单一旧布局事务迁移、lookup metadata 反向绑定和无竞态初始化修复；receipt 中的
  domain-separated logical `request_sha256` 保持原定义。
- SQLite authority 不在并发 bootstrap 中切换 `journal_mode`；新库沿用 SQLite 默认 rollback journal，
  同时使用 `synchronous=FULL`、`BEGIN IMMEDIATE`、snapshot canonical bytes 与独立 payload checksum。
  `workflow_run_id` 为主键，`(principal_id, idempotency_key)` 与 canonical start-request digest 分别唯一。
  已知旧私有四列表可事务迁移并保留 logical digest；未知 layout、坏旧行、principal 不一致、合法 UUID
  碰撞、非 canonical/非 BLOB/不可解析 snapshot、无效或不匹配 checksum、row/snapshot identity swap、
  request/idempotency metadata 篡改和损坏数据库均 fail closed。
- 后续主审以同名七列表攻击发现：原构造器只比列名，会接受缺主键、缺任一 UNIQUE、可空列、错误
  declared type/affinity、默认值、隐藏生成列和附加索引；正确列/索引还可附带 `AFTER INSERT` trigger，
  让首个 `start` 返回内存成功而持久 authority 已被改写。新增 RED 覆盖上述结构，以及 duplicate/
  partial/expression/collation index、foreign key、STRICT/WITHOUT ROWID 和构造后插入/删除 trigger。
- 表级 `PRIMARY KEY/UNIQUE ... ON CONFLICT REPLACE` 在 PRAGMA 中与默认 ABORT 同形，却会静默替换
  authority；三类约束的 RED 均复现原构造器接受。布局检查现在忽略 SQL 引号/注释后拒绝显式
  `ON CONFLICT` policy（包括 `ON/**/CONFLICT`），生产 start 与 legacy migration 的写入同时固定为
  `INSERT OR ABORT`，避免表级 policy 改写冲突语义。
- GREEN 以 `table_xinfo` 精确验证 cid/name/type/notnull/default/pk/hidden，以 `index_list` +
  `index_xinfo` 验证 unique/origin/partial、顺序 key、collation、expression/extra key 与辅助 rowid；比较
  语义而不写死 SQLite autoindex 名。同时验证 `table_list` flags、零 foreign key/trigger；已知四列
  legacy 先验完整旧 layout，迁移后再验完整 current layout。INSERT 后在同一事务重读并对账全部
  authority metadata，因而晚注入 trigger 也不能造成首调假成功。
- `read` 只按 `(principal_id, workflow_run_id)` 返回，因此不存在与其他 principal 使用同 UUID 时的
  可观察差异；missing 与 wrong-principal 都给出同一个
  `GoldenRunNotFoundError(code=HARN_WORKFLOW_NOT_FOUND)`。恢复后重新解析 canonical start request，
  再复核 registry、trusted state、receipt、初始 evidence 与全部 CAS 内容闭包；删除 start-request CAS
  对象会给出 `HARN_PERSISTENCE_CONFLICT`，不会返回仅有 SQLite 的快照。
- 同一测试先用第二个 Harness/ArtifactStore/SQLite store 实例恢复，再启动真实 Python 子进程从相同
  SQLite 与 CAS 调用公开 `read`，两次结果都与起始快照完全相同；显式数据库位于 CAS 外，且确认
  Harness 没有在 CAS root 偷建 `golden-workflows.sqlite3`。另有 40 个 fresh database × 2 线程的有界
  并发 constructor 测试，以及两个 Harness 同时 start 同一 request 只落一个 aggregate 的测试。
- focused gate：
  `pytest -q tests/self_improving/harness/test_golden_run.py tests/self_improving/harness/test_schema_catalog.py --cov=self_improving.harness.golden_run --cov=self_improving.harness.golden_store --cov=self_improving.harness.schemas.workflow --cov-branch --cov-fail-under=100`
  为 `89 passed`；三个模块合计 440 statements、98 branches，均为 `100%`。29 份 Harness JSON
  Schema snapshot check 与 Ruff 通过。
- Harness 全域显式排除已登记的 `text2env.replay@1.0.0` qualification source-identity 单项后为
  `2141 passed, 19 skipped, 1 deselected in 53.37s`；本修复不改 expected hash 或伪造重签。
- 根套件保留同一个显式排除项后为 `3154 passed, 19 skipped, 1 deselected in 148.28s`；没有第二个
  回归失败。本结果不是 Genesis runtime 验证，也不替代最终资格重签。
- 早期 e1cc853 Standards/spec reviewer 曾在 Python 3.11.15 隔离环境复跑 focused `65 passed`、
  405 statements/86 branches 100%，并补做 40 个 fresh database × 2 独立进程 constructor 攻击；
  随后主审仍发现上述 layout/trigger blocker，因此旧 `NO BLOCKER` 不能作为最终审阅结论。当前修复由
  主线程复跑 focused 89 tests 并检查结构/trigger/post-insert 路径，未发现新的同级 blocker。
- 边界：本切片没有定义 `RunCommandRequest`、`OperationSnapshot`、`submit`、handler dispatch、MCP、
  Genesis 或 promotion，也没有把 caller 提供的 RegistrySnapshot 提升为生产 trust root；这些仍属于
  P1 后续与 P2/P4。纯持久化切片不产生真实仿真或真机能力主张。

## 2026-09-10 P2 controlled child-run identity 纵切

- 首轮 RED 固定 caller-reserved UUID4 的身份语义：相同 run id 但不同 canonical input、依赖树、
  descriptor、implementation 或 qualification identity 必须拒绝；只完成 preflight 的 intent 与已验证
  Invocation 使用不同 phase，且输入只 strict normalize 一次，避免可变 Mapping 在摘要与执行之间改变。
- Registry/Application 新增受控 child-run seam。validated intent、Invocation、terminal 和事件链由同一
  SQLite authority 绑定；同 intent 并发只允许一个 handler 执行，精确 terminal 可恢复，只有 invocation
  而没有 terminal 时返回稳定 pending，不把未知外部副作用冒充成可重试成功。普通 run UUID 再用于
  controlled 调用时稳定报 identity 冲突；只有在完整 ordinary lifecycle 已验证后才做该分类，损坏的
  metadata、payload anchor 或事件链仍报 persistence error。
- SQLiteRunStore 对 controlled intent、Invocation、terminal 采用一次只读事务的 lifecycle snapshot，
  并精确校验 current/known-legacy table、index、constraint、trigger、foreign-key、conflict policy、
  metadata 与 payload anchor。SQLiteEventJournal 同样在 publish 前后校验布局、插入后同事务重读 exact
  envelope/metadata，并确认前序链未改变；fresh-path 的 RunStore/EventJournal/双 Application 并发构造
  已有有界用例。
- 固定复审点 `cb3235f` 的 focused 四文件为 `192 passed`；Harness 明确排除 compile/replay 两份待统一
  重签的 qualification identity case 后为 `2294 passed, 19 skipped, 2 deselected`，五个受影响核心模块
  语句/分支为 100%。补齐普通生命周期占用与损坏不重分类用例后的本提交为 focused `197 passed`；
  相同 Harness 门为 `2299 passed, 19 skipped, 2 deselected`，五模块合计 1,592 statements、484 branches，
  均为 100%；Ruff、format 与 diff 完整性门均通过。
- 两份 qualification 尚未在本纵切重签；它们必须等待相关实现树稳定后用既有真实固定案例、settings
  与 CAS 统一生成，不能仅修改 expected hash。本结果也没有实现 `RunCommandRequest`、
  `OperationSnapshot`、`GoldenRunHarness.submit`、workflow operation journal、replay/validate dispatch、
  MCP transport、Genesis runtime 或 promotion，因此不声称 S1 submit 闭环或 golden E2E 已完成。
## 2026-09-10 P0 replay qualification 显式闭包修复

- RED：fresh-process import trace 原先只统计 `harness/`，遗漏 `self_improving/__init__.py` 实际加载的
  `self_improving/registry.py`；扩展到整个 `self_improving/` 后以唯一额外路径精确失败。该顶层 registry
  已进入 replay implementation closure，import-only trace 不再过滤它。
- RED：保持 distribution root 不变，在第二轮最后一个成员已打开时分别把 `self_improving/` 和
  `self_improving/harness/schemas/` 换成字节完全相同的完整副本，两轮实现仍返回旧摘要。GREEN 现在从
  绝对 root 独立打开三次，每次逐组件 nofollow 重走每条目录链并流式重读成员；逐项比较目录与文件
  identity、bytes、SHA-256，并最终再次重开 root。换根、中间目录替换、member rename/symlink、读取中
  原地变化、父级逃逸和非普通文件均 fail closed。
- fresh-process production tracer 不再替换 `_execute_fixed_case`：真实 compile application/handler、
  direct replay handler 与 Registry candidate 都执行，只替代外部 RoboTwin runtime、native sandbox 和
  media decoder 边界。实际加载的 `assets.py`、compile/replay handler、Harness Registry 均在显式
  implementation closure；若 ledger contract 动态 import，则必须落在独立 tree-hash 绑定的 ledger root。
- 14 个 replay 绑定 schema 的 `schema_id + model` 现由 frozen `REPLAY_SCHEMA_BINDINGS` 单表定义；
  implementation snapshot paths 与 exact model/snapshot gate 都从该表派生。测试保留独立 14-ID literal
  并要求模型集合、路径集合和 implementation closure 精确一致；qualification-report projection 仍是
  真实依赖。任一 snapshot 内容变化或缺失会在生成执行或 loader 解析前拒绝。
- package façade 兼容不变：root/handlers/schemas 为稳定惰性 seam；全局公开 export/schema family 目录
  仍在 replay closure 外；`SCHEMA_MODELS.copy()`、左右 `|` 与不可赋值行为保持。
- focused replay/loader/schema：`171 passed`；12 个变更模块合计 1,807 statements、372 branches，
  statement/branch coverage 均为 100%。29 份 Harness schema snapshot、Ruff、`git diff --check` 与
  checked compile qualification 均通过。
- root：`3206 passed, 19 skipped, 1 failed in 153.28s`。唯一失败仍是旧 checked replay bundle 对当前
  `harness/__init__.py` 的 `implementation_file_mismatch`；这是预期的 fail-closed 结果，不是重签。
- 在该独立 P0 候选上，reader-facing repo-docs 统一了时态和当时的 29-schema 计数：
  2026-09-02 bundle/真实 900/120 是固定旧
  源码下的历史证据；当前 loader 会拒绝，新的 current qualification 尚未执行。历史 evidence 页恢复
  原字节，当前失效和未重签状态记录在独立 `replay-qualification-status-20260910.md`。
- 边界：该独立候选未改 qualification 三份文档或 expected hash，未执行真实 replay
  requalification，未声称当前 simulator、Genesis 或真机能力。P2 当时尚会修改 closure 内
  `registry.py` / `application.py`；当前集成状态及后续重签门见本页顶部的 P0+P2 集成记录。
## 2026-09-10 Image2Env / Video2Env contract tranche

- Source audit：仓库内 `worktree/gujie@a25bc58853db1988aafafe88b544e0573eacbb3c` 是旧且
  prunable 的记录；实际多模态/Genesis 固定来源为独立仓库
  `/home/jingxiang/gujie/gen-env@eb0b710581fd7794bc01b447b0f77cb871c8a711`，重点祖先
  `9e8d28c` / `a8ced27`。来源工作树 dirty 项未计入固定 ref。
- Source behavior：已核 `probe_media` 的 input hash、image decode/dimensions、video ffprobe 全计帧、
  ffmpeg framemd5、FPS/duration/total/unique 和 15-frame sample；并核 `TaskOutput`、URDF/scene import、
  position/stabilization、Genesis step/contact/penetration、baseline/half-dt 的入口和来源测试/证据边界。
- RED 1：新 public contract 测试首次收集失败，`self_improving.harness.x2env_contracts` 不存在。
- GREEN 1：新增 Image/Video source identity、asset acquisition decision、candidate environment package、
  两组 compile/replay/validate strict models 和六项 guarded contract descriptor metadata；focused 首轮
  `13 passed`。
- RED 2：schema catalog 尚为 39 份，新增 16 个预期 `$id` 时 `6 failed, 2 passed`。
- GREEN 2：catalog 接入并导出 55 份 committed snapshots；补齐跨字段和跨模态攻击用例后，schema +
  contract focused 为 `24 passed`，新增两个模块 438 statements / 110 branches 均为 100%。
- Contract boundaries：compile 固定 `simulator_executed=false/publishable=false`；replay 要求真实
  collision/nonzero steps/顺序媒体，robot-policy 另要 probe+trajectory；validate 是唯一
  publishability seam。Gujie legacy 输出适配后仍固定 candidate，不导入旧 runtime/pass 状态。
- Artifacts：`docs/contracts/X2ENV_IMAGE_VIDEO_SKILL_CONTRACT_V1.md`、
  `docs/contracts/GUJIE_GENENV_ADAPTER_MAPPING_V1.md`、
  `X2ENV_IMAGE_VIDEO_IMPLEMENTATION_PLAN.md` 与两份 committed raw media mapping fixtures。
- Verification：55 份 committed schema snapshot check 通过；root 为
  `3692 passed, 20 skipped in 173.34s`；该 contract-only tranche 未改写既有资格包。
- 限定：该切片没有 handler/application、qualification bundle、Registry/System2/Golden executable entry、
  MCP tool、真实 Genesis replay/validate 或 promotion；runtime 状态仍为未实现。
