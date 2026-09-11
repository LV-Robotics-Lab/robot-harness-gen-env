# 当前 TODO

更新时间：2026-09-11

## Doing

- X2 exact-reuse image compile：controlled candidate handler 与 v2 input-bound receipt 已完成；当前进入
  production qualification 设计。必须取得真实 Genesis asset issuer/runtime evidence，且资格生成器要复用
  同一 preflight/postflight，不得把 controlled catalog fixture 当生产资产资格。
- P10 fresh observation：strict schema、controlled candidate application/evidence policy 与 test-controlled
  Golden execution binding 已在同一 revision 2→3 中闭合 receipt/state/restart；下一切片是 production
  runtime binding。当前受控路径固定 `simulator_executed=false`，真实 Genesis capture、qualification 与
  MCP exposure 尚未开始。
- P10 diagnosis/prompt revision：durable resource-access receipt、两个 exact MCP control tools、受控
  external Codex 回合和下一次受控 exact image compile 已闭合。image/video compile v2 input 强制消费
  prompt revision、effective prompt、失败 receipt、predecessor package 与选定 fallback frame；下一切片
  实现 production image/video handler/qualification。不得静默选择 latest/fallback，也不得把 server 已交付
  bytes 夸成模型理解。

## Next

- X2：为 exact-reuse image compile 实现 production application/qualification；先生成真实 Genesis asset
  issuer、runtime lock 与 qualification bundle，再允许 Registry/MCP 曝光。controlled handler 继续保持
  未注册、`simulator_ready=false`。
- P10：实现 production execution binding/runtime；保持过期、缺 key、时间或 payload 漂移在 parent
  commit 前 fail closed。
- P10：production policy 稳定后，用真实 Genesis sealed checkpoint 做 capture/qualification；只有该门
  通过后才把 `genesis_observe_v1_0_0` 加入 exact MCP catalog 与 workflow-scoped resource。
- P5：把已完成的受控 PromptRevision consumption 接到 production exact compile，并补 operation
  polling/cancel；当前 rev4→5 只调用受控 image compile application，没有 image runtime 或 Genesis。
- P6：在已合入的 exact-byte staging 后，单独实现 collision provenance、settle 与 Genesis
  qualification；stage result 仍不得直接触发历史 ledger promotion，也不得把 headless ground-contact
  probe 写成 can-on-plate、媒体或 Genesis 证据。

## Blocked

- ClawCross/dashboard 的 portfolio、tasks、project 三个状态 URL 均返回 HTTP 404；无法同步 TODO
  控制面，也没有可用 task id 写回进度。
- 2026-09-10 一名 P6 fixed-diff 只读审查 agent 被 OpenAI 安全系统标记为潜在网络安全风险。按仓库
  规则已立即停止该审查，未重试、改写或重新委派；用户随后允许继续其余工作。该具体审查永久保留
  为 blocked，不作为通过结论，也不妨碍修复主窗口已经独立复现的功能阻断。
- Autoresearch 的 benchmark/Genesis 前置尚未实现；设置已确认但尚不能建立诚实 baseline。
- 当前没有 qualified Genesis observe runtime 或 sealed checkpoint capture 证据；历史 replay 图片不能作为
  fresh observation。production qualification 与 `genesis_observe_v1_0_0` MCP exposure 因此前置未完成。
- MCP 尚无 image/video media ingest 或公开 operation polling/cancel seam；当前 text-only 三 Skill
  已通过真实 MCP simulator 闭合，external Codex 已完成受控读取/诊断/prompt revision，但尚未从该
  revision 继续调用 compile/replay/validate。

## Done

- X2 controlled exact-reuse image compile 完成：image/video compile descriptor 输出切到 v2，compile receipt
  v2 以 CAS `compile_input` 绑定 seed、attempt、acquisition policy 和完整输入字节，v1 继续 parse-only 且
  snapshot 不变。`Image2EnvExactReuseCompileHandler` 仅走 Registry candidate：纯读 preflight 完整消费
  image/proposal/runtime/catalog 及所有 available qualification/member/check evidence，首个切片严格限制为
  单一 dynamic object + `on_table` + case-sensitive exact/alias 唯一匹配；随后确定性发布 8 份派生文档，
  postflight deep verifier 通过后才 emit/return。重复 candidate 输出相同且 CAS 零增长；missing/ambiguous
  category 与证据缺失在派生前 fail closed。公共 schema 84→87；新 handler+verifier 375 statements/92
  branches 100%。仍无 production qualification、真实 Genesis/GPU、replay、MCP 或 publishability。

- X2 typed compile evidence 与 deep verifier 完成：12 个原 opaque artifact label 已成为严格、self-hashed
  public models，公共 schema 72→84。`ExactReuseCompileClosureVerifier` 纯读解引用 source/proposal/
  SceneIR/TaskIR/runtime/catalog/representation/qualification/selection/scene closure/binding/static/package/
  receipt；所有 available catalog entry（包括未选项）的 members 与 qualification check evidence 都须在
  CAS 中存在、匹配 trusted issuer/runtime/profile/subject，并且每份 qualification 恰有绑定自身
  representation 与 runtime lock 的 `backend_load`/`collision_probe`。static validation 恰含四项 exact
  evidence set。永久 RED 先暴露“未选坏 ref”“noop static”“noop qualification”三类假绿，再逐项修复。
  最终 verifier 219 statements/52 branches 100%，两轴 fresh-CAS 验收均 PASS；仍无 handler、production
  qualification、Genesis/GPU 或 publishability。

- X1 complete-sequence video ingest 完成：受控 `VideoContainerDecoder` 必须报告完整解码；ingestor 发布
  `VideoDecodedFrameSequenceV1`、`VideoMediaProbeV1` 与 `VideoSourceMediaV2`，逐帧绑定 index/DTS/PTS/
  duration/raw bytes hash，并闭合 FPS fraction、timebase、duration、total/unique 与 sequence digest。
  consumer 全量重解码且纯读；18-frame/6-unique H.264 样例通过，15-frame sample、乱序、时序/计数/
  MIME/上限/CAS 漂移均拒绝。公共 schema 69→72，新模块 343 statements/104 branches 100%，根套件
  4080 passed/20 skipped。existing `harness.video_frame_sequence.v1` fallback schema 与 v1 source media
  未改写；system FFmpeg 只用于独立验收，尚无 production decoder/factory/qualification/Genesis/GPU。

- X1 deterministic image ingest 完成：真实 JPEG/PNG/WEBP source 由 exact CAS 完整解码，输出只由
  decoded pixels/size/mode 决定的 canonical PNG、strict `ImageMediaProbeV1` 与 `ImageSourceMedia`。
  consumer 纯读重验 source/canonical/probe 实际 bytes、canonical JSON、Pillow identity 与所有镜像字段；
  伪 MIME、坏/缺 CAS、动画、source/pixel 超限、损坏、静态不支持格式、字段换靶及 ICC/text/EXIF
  identity 漂移全部 fail closed。公共 schema 68→69；新模块 statement/branch 100%。仍无 image compile
  handler、qualification、asset lane、Genesis/GPU 或 MCP exposure。

- PromptRevision compile consumption 受控纵切完成：新增 initial/revised 两种严格 attempt schema 与
  image/video compile v2 input；两个尚未资格化的 x2env `@1.0.0` exact contracts 在首次资格前切到 v2，
  v1 保留 parse-only。GoldenRun 在任何 command CAS/child reservation 前重验 current head、target Skill、
  effective prompt、失败 receipts、predecessor package 与 video→image selected frame。官方 MCP 正向让
  exact image compile application 恰调用一次并推进 revision 4→5；target/revision/prompt/failure/package/
  frame 漂移与旧 v1 均零副作用拒绝。公共 snapshot 64→68，新 guard 37 statements/26 branches 100%；
  本切片仍无 image handler、qualification、真实 Genesis runtime 或 publishability。

- durable MCP resource delivery 与受控 external Codex diagnosis 完成：`ResourceAccessReceipt` 在独立
  append-only journal 中绑定 principal/workflow/full observed snapshot/resource/bytes/representation，读取
  不推进 revision/state/head；MCP 返回原资源和第二份 receipt，专用 receipt URI 不递归记账。advisory
  现在无条件要求 source prompt、每个 failure、observation receipt 与 diagnostic 的读取目标精确覆盖，
  zero/partial/unrelated 均拒绝且不推进 workflow。Codex CLI `0.153.4` 经 `2025-06-18` stdio 协议实际
  读取四项证据；首次乱序 receipt 负向用例被拒后，提交 advisory revision 3 与 prompt revision 4。
  无 live application 的第二进程重提保持 starts/operations/accesses/controls/turns=`1/2/4/2/4`，CAS
  45 files/75,985 bytes 零增长。64 schema snapshots、focused 113 tests 通过；使用系统构建工具并复用
  项目 MCP 2.2 依赖的完整 root 套件为 3,957 passed/20 skipped。证据见
  `docs/evidence/golden-external-codex-diagnosis-20260911.{md,json}`。该切片固定
  `simulator_executed=false`、`transport_acknowledged=false`、`model_understanding_claimed=false`，无
  Genesis/GPU/physical/publishability/promotion 主张。

- diagnosis/prompt revision 的 S1 纵切完成：五份 strict public schema、共享 Skill/control turn ledger、
  `GoldenRunHarness.submit_control` 与 mixed receipt reconstruction 已实现。受控链真实完成 fresh
  observation → blocked Skill → advisory → prompt revision，并可在 prompt control 后继续 exact Skill；
  control 每次只让 revision/turn/head +1，state ref/hash 与 child runs 不变。advisory 绑定 blocked/failed
  receipts、diagnostics 与仍新鲜的 Genesis observation citation；prompt 绑定 source prompt、advisory、
  failure/package lineage 与显式 video-frame→image fallback。固定 `e9d9fcb` 的三轴验收核对 41 个 CAS
  对象、共享 turn/detail 双射、frame manifest、TTL 闭区间、SQLite integrity、CAS 篡改与无 live app
  restart；174/243/331 组测试均通过。新增 schema 272 statements/72 branches、新 store diff
  210 statements/62 branches均为100%，完整根套件为3929 pass/20 skip。该结果固定
  `external_agent_executed=false`，尚无资源读取回执、MCP control tools 或真实 external Codex 回合。

- fresh observation 受控纵切完成：exact Skill 为 `genesis.observe@1.0.0`；三份 strict public schema
  把 replay lineage、requested keys、`1..300` 秒 TTL、typed values 和 payload CAS 闭合；controlled
  candidate 使用 Registry + SQLiteEventJournal/SQLiteRunStore，显式值、可解码 PNG 和
  `simulator_executed=false`/0 steps。显式 test-controlled execution binding 让同一 workflow 的真实
  controlled child receipt 进入 fresh observations、trusted claims、唯一
  StateDelta、resulting state 和 restart recovery 绑定；controlled evidence policy 还拒绝跨 workflow/
  principal/revision/state/head 的 lineage 混用。专项 57 passed，新 binding/application 为
  258 statements / 74 branches 100%，根套件 3830 pass/20 skip，公共 snapshot 55→58；没有运行 GPU，
  也没有 production
  qualification、真实 Genesis capture 或 MCP exposure。

- 最终 revision-3 integration 已完成：固定实现 `eec1976bdd71578e43b606ef3b88f3bd7d5975e9`
  的 compile/replay/validate 资格报告分别 7/7、8/8、6/6 通过；同一 bundle 在另一绝对
  代码路径中真实执行 exact qualified revision `0→1→2→3`。独立验收核对 159 个 CAS
  对象、3,407 个递归 ArtifactRef、23 个 runtime asset members、重算物理报告、SQLite/回执/
  状态链与 120 帧/114 decoded-unique MP4，全部闭合。无 live application 重提 validate
  返回同一 terminal，未产生第四个 child。该证据仅表达 legacy RoboTwin/SAPIEN
  `publishable=true`；Genesis profile、promotion、robot-policy 均为 false。详见
  `docs/evidence/golden-revision3-integration-20260910.{md,json}`。
- replay 源码 identity 已跨代码安装路径验证，但 qualified operator 仍包含本机部署绑定。
  exact delegated cgroup scope 消失后，deep verifier 会按合同 fail-closed；运行前需显式重新
  provision 该 scope，未证明跨机器自动 provisioning。

- replay qualification 与 production replay 已跨代码安装绝对路径：源码身份改为逻辑
  `replay_distribution` 根 + 50 个 relative exact-byte members + tree hash，loader 在调用方映射本地根后
  仍保留 P0 的三轮 nofollow closure；runner 与 module root 不再混入 deployment file list。只有精确等于
  映射源码根的 worker `PYTHONPATH` 会逻辑归一，其他环境与 RoboTwin/解释器/media/cgroup 仍按部署身份
  拒绝漂移。全新 direct/kernel 资格均真实完成 0/900/120/120/12；同一 bundle 在 A/B 路径 strict
  verify/load 后，由 B 路径 production factory 在全新证据根完成 Golden 0→1→2，MP4 独立解码
  120/114 帧且无 live application 重启恢复同一 terminal。证据见
  `docs/evidence/golden-portable-replay-20260910.{md,json}`；不包含 validate、Genesis 或 promotion。

- Image2Env/Video2Env 正式合同切片完成：新增 12 个 exact Skill I/O、Image/Video source media、asset
  decision 与 x2env environment package 共 16 个严格公共 schema，Harness snapshot 总数 39→55；六项
  descriptor metadata 保持 contract-only/unqualified/unregistered。Gujie 固定来源已逐真实入口映射，
  两份 raw media manifest fixture 明确 sample-only 缺口；逐 namespace 的 compile/replay/validate 三 lane、
  adapter/qualification/Genesis runtime/System2/Golden catalog 条件已有实施计划。本切片没有 handler、
  Registry entry、MCP tool、真实 Genesis run 或 publishable 结果。
- 两个独立验收轴已完成：固定 `eb0b710…` 来源逐入口与现存 output 核对通过，确认 fresh-image output
  的 manifest closure 失效、只有 existing-reconstruction image output 的 legacy Genesis baseline 可验；
  video/dual-dt/Harness/robot-policy 仍无证据。schema 执行验收先复现 11/16 standalone snapshot 失败，
  随后统一移除根 `$defs` 以下的嵌套资源标识；现在新增 schema 的 standalone/catalog 验证均为 16/16，
  六 Skill I/O 为 12/12，55-schema snapshot gate 通过，相关 438 statements/110 branches 为 100%。

- P4 legacy validate v2 候选已真实闭合：同一 actor-neutral Golden workflow、同一 Harness、同一
  RegistrySnapshot 依次执行 exact qualified compile/replay/validate，revision `0→1→2→3`；validate
  输入来自 revision 2 的受信 receipt/state，重新计算物理报告并只用 `environment.validation` 推进状态，
  `fresh_observations=[]`。真实运行输出 120 帧 MP4（114 decoded-unique），187 个 CAS 文件与 3,735 个
  递归 ArtifactRef occurrence 全闭合；无 live application 重启重复提交返回同一 terminal operation。
  证据见 `docs/evidence/golden-compile-replay-validate-p4-20260910.{md,json}`。该候选尚未整合 portable
  replay；最终整合必须重签并重跑，且此处 publishable 不表示 promotion/Genesis/robot-policy。

- P3 exact qualified replay operation 已完成：replay-owned policy/adapter 没有向 generic catalog 增加
  Skill 分支；同一 actor-neutral workflow 真实提交 compile rev1→replay rev2。replay 只接受同一 compile
  trusted receipt 的 package/catalog，只晋升 `replay.runtime_evidence`，fresh observations 为空。四个恢复
  窗口、blocked/failed/succeeded、关联/漂移/顺序/幂等边界全有用例；新增两模块 statement/branch 100%，
  邻接回归 448 pass，完整 root 3676 pass/20 skip。真实 RoboTwin/SAPIEN 运行完成
  0/900/120/120/12 与无 live application 重启恢复；没有调用 validate，也不声称 Genesis/promotion。

- P2 首个真实 submit 纵切：公开 `GoldenRunHarness.submit` 在同一父 workflow 中调用 production
  `CompileApplication`，以 exact RegistrySnapshot/live descriptor/qualification/policy 交集冻结执行，
  把 command、qualified execution、controlled child、ToolResult v2、StateDelta v2、workflow receipt
  与 revision 0→1 head 全部绑定到同一 CAS/SQLite authority。generic catalog 与 compile binding/policy
  分模块，compile policy digest 只覆盖自身稳定闭包；operation 幂等域为
  `(workflow_run_id, exact skill_ref, idempotency_key)`；ToolResult 与 workflow receipt 指向同一份
  StateDelta CAS。四个公开重启窗口均已锁定：reservation-before-intent 可安全继续；intent-only 与
  invocation-only 保守保持 running、不重复 handler；child terminal 可只提交父 head。binding 是
  opaque factory product，catalog 消费时重验完整交叉绑定；evidence policy 只能规范化 parameters，
  aggregate 保有 command identity。focused 194 tests，1,386 statements / 402 branches 均为 100%；
  当前 checkout 绑定的 39 份 schema snapshot 和 compile/replay/application
  三个资格身份节点通过。该结果不声称 exactly-once 外部副作用，也不证明 replay、MCP、Genesis、
  promotion 或真机能力。

- 固定基线 `461b05d` 的两项 root demo 失败已修复为测试兼容切片：原测试在 submit 前安装
  `harness_run_states` trigger，P2 exact-layout 门会按设计在 Invocation/handler 前拒绝，因而无法再
  到达测试命名的 terminal missing/history mismatch 场景。测试现在让真实 compile 完整提交后、
  Workbench 二次读取前删除对应 terminal row/event，原有后置完整性语义继续直接受保护；生产
  RunStore、Registry 和 Workbench 均未修改。demo focused 为 `192 passed`，`demo.harness_compile`
  531 statements / 214 branches 100%；Harness 为 `2505 passed, 20 skipped`；完整 root 不排除节点为
  `3518 passed, 20 skipped`。本切片未触碰 compile/replay 资格材料。
- 当前集成树 replay qualification 已独立重签：旧 bundle 先以
  `implementation_file_mismatch: self_improving/harness/__init__.py` RED；全新外部
  bundle/scratch/CAS、重新探测 capability 和临时 delegated scope 中，fixed can-on-plate 的 direct 与
  production-kernel candidate 均真实完成 0/900/120/120/12。物理报告 pass、120 帧顺序媒体完整解码、
  23-member runtime assets 与 62-ref closure 均通过；14-schema exact gate 和 fresh-CAS strict reload
  通过，三个原 deselect 节点现为 `3 passed`，完整 Harness 为 `2505 passed, 20 skipped`。完整 root
  的两项既有 demo trigger/layout 负向用例失败已在本轮 evidence/RESULTS 如实记录，未改实现或弱化测试。
- 当前集成树的 compile qualification 已独立重签：旧 bundle 先以
  `implementation_file_mismatch: self_improving/harness/qualification.py` RED；固定 purple pedestal、
  seed 77、admission date 2026-08-31 的官方 generator 在全新外部 bundle/scratch/CAS 中真实运行三次，
  观测 `admitted -> reused -> reused`。只读 verifier、全新 CAS strict loader 与 checked-in loader
  gate 均为 pass；static validation 仍 incomplete，未启动 replay。
- P0 replay qualification source-closure 修复：fresh-process trace 覆盖整个 `self_improving/` 并纳入
  `self_improving/registry.py`；真实 `_execute_fixed_case` 测试在只替代外部 runtime/media/native 边界时
  执行 production compile、direct replay 与 Registry candidate，并检查执行期第一方 import closure。
  源码 snapshot 对每条目录链和成员做三轮独立 nofollow 重走与 identity/bytes 对账，另做最终 root
  重开；14-schema 路径和模型门由单一冻结表生成并有独立集合一致性校验。旧 checked bundle 继续因
  当前源码 identity 不匹配而拒绝，未刷新 hash、未重签；真实重签等待 P2 后 closure 稳定。2026-09-02
  历史 evidence 保持原字节，当前失效状态写入独立 2026-09-10 页面；repo-docs 的 replay 时态和
  集成树 32-schema 计数已同步。
- P2 controlled child-run identity 纵切已完成并合入本集成候选：canonical input、exact dependency、
  descriptor/implementation/qualification identity、Invocation、terminal 与事件链由同一 SQLite
  authority 绑定；同 intent 可恢复，冲突 intent、ordinary-run identity 占用与损坏 lifecycle
  均 fail closed。该纵切仍不包含 workflow `submit` 或 operation commit。
- 接受 Codex 同时承担中枢 agent 与视觉判读职责的稳定架构决定。
- 建立本进度目录和根 `AGENTS.md` 触发入口。
- 完成 Gujie committed/dirty/runtime 三层 x2env 与 Genesis 审计。
- 完成历史 162 份 ledger、4,082 条违规和可恢复字节审计。
- 完成现有 compile/replay/validate、System 2、Codex/MCP 同-run 断点审计。
- 完成极简 aggregate、capability kernel、exact Skill MCP 三套独立接口设计。
- 推荐 A 内核 + C 外观 + B 最小共享模型，并起草 Skill/验收/实施合同。
- 用户确认 S1–S6、exact Skill MCP、`genesis.robot_policy@1` 最终门、`mcp>=2.2,<3` 与
  36/12 cases、30 次或 12 小时的 autoresearch 设置。
- 修复 portal 删除后的 required-capability 漂移；当前共享 checkout 的
  `python -m self_improving --json` 返回 `ready=true`，portal 为 `required=false/status=missing`。
- 已确认领域语言写入 `CONTEXT.md`，并建立 external Codex/deep aggregate/exact MCP 的 ADR。
- 建立 `docs/integration-provenance/`，以逐人模块/功能矩阵固定 Bingsheng、Gujie、Yuxin 的具体工作、
  来源版本、整合责任、第三方上游与验证状态，并从根 `AGENTS.md` 建立强制更新入口。
- P0 clean baseline 已完成分类：根套件唯一红项来自旧 replay qualification 把整个 Harness 树当作
  source closure；最终在实现树稳定后真实重签或撤销，不能用更新 expected hash 伪修复。
- P1 首个 S1 纵切：从 canonical CAS user-input evidence 构建可信世界状态、actor-neutral start receipt
  与父 workflow snapshot；3 份公共 schema 已导出，focused 19 tests 通过，两个新增模块语句/分支
  覆盖率均为 100%。
- P1 durable start/idempotency 纵切：跨实例 retry、冲突拒绝和 CAS/SQLite 恢复校验已完成；focused
  29 tests 通过，相关新增模块语句/分支覆盖率 100%。
- P1 Registry/request 闭包纵切：正式 `RegistrySnapshot` strict canonical 解析并交叉绑定 descriptor、
  qualification、report；start request 进入 CAS，receipt head 改为可演进的 versioned workflow receipt
  family。focused 48 tests 通过，3 个相关模块语句/分支覆盖率 100%，25 份 schema snapshot 通过。
- P1 durable read/store 纵切：公开 `RunReadRequest`、`SQLiteGoldenRunStore` 与
  `GoldenRunHarness.read`；SQLite 路径由调用方显式选择，revision-0 start 的跨实例/真实子进程恢复、
  principal 隔离、已知旧表迁移、并发 bootstrap/start、SQLite/CAS 损坏拒绝和原有 start/idempotency
  均通过；随后补齐 SQLite table/index/constraint/trigger/`ON CONFLICT` 权威布局和 late mutation 攻击，
  focused 89 tests、440 statements/98 branches 均为 100%。未实现 submit/operation/current operation
  head。
- P6 首个 S5 只读纵切：从显式受信、strict-canonical 的 debt-inventory CAS 对 `003_plate`、
  `071_can` 分类，确认 2 份 ledger 的 58 条结构违规与 14 个本机 observed digest matches；输出明确
  `probe_bytes_in_cas=false`、`writes_performed=false`、`runtime_qualification_executed=false`。这只是
  2/162 的 E0 tracer，来源总体与 inventory/selection totals 已分层绑定，不能改 scope 冒充全量；尚未
  覆盖 4,082 条全量清单或建立 portable byte closure。
- P6 第二个 S5 exact-stage 纵切已合入：以 trusted inventory + rebuilt canonical plan + path-free source
  manifest + deployment-only local-root binding，从本机未版本化 RoboTwin 来源逐 member 重算 hash/bytes，
  把 `003_plate`/`071_can` 的 14 个 GLB 与 7 个 model sidecar（21 members、57,290,434 bytes）写入新
  CAS；从 exact CAS loader bytes 重建 14 个 representation closure，并由 strict typed authority 绑定
  inventory/plan/manifest/result。真实 staged-only RoboTwin/SAPIEN probe 对 7 个模型各执行 900 steps，
  全部 finite/contact/nonzero impulse。主分支 focused 为 256 passed/1 skipped，四个核心模块 845
  statements/296 branches 100%；根套件为 3360 passed/20 skipped/1 个既有 replay qualification identity
  failure。该纵切仍明确没有 can-on-plate、runtime qualification、Genesis、promotion 或历史 ledger 写入。
