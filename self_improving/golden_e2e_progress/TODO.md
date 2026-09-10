# 当前 TODO

更新时间：2026-09-10

## Doing

- P2 `GoldenRunHarness.submit` 首个 compile-only 候选已完成实现与本地门禁，等待固定差异双轴审阅和
  主线集成。该候选只把 exact qualified `text2env.compile@1.0.0` 从 workflow revision 0 推进到 1；
  不实现 replay/validate workflow dispatch、MCP、Genesis 或 promotion。

## Next

- P3-R：P2 actor-neutral aggregate 固定后，新增真实 `Text2EnvReplayApplicationAdapter` 与 replay
  evidence policy；用 `Place a can on top of a plate.`、seed 7、`0/900/120/120/12` 让同一 workflow
  从 compile revision 1 推进到 replay revision 2。replay input package 必须来自当前可信 state，完整
  child/receipt/CAS chain 必须连续；该 tracer 只证明 legacy RoboTwin/SAPIEN 编排，不是 Genesis。
- P3：在 P2 actor-neutral v2 receipt/state-delta 上新增 replay-owned binding，完成 exact qualified
  compile → replay 两步调度；不得修改 compile evidence-policy identity，也不得复用 planner-specific
  v1 receipt 字段冒充外部 Codex/MCP 调用。
- P4：S1 的 `submit/read`、OperationSnapshot 和真实 Registry handler 绑定完成后，接入 MCP 2.2 的
  low-level Server/Client；在同一切片冻结 dependency/runtime lock，不在实验循环中安装依赖。
- P6：在已合入的 exact-byte staging 后，单独实现 collision provenance、settle 与 Genesis
  qualification；stage result 仍不得直接触发历史 ledger promotion，也不得把 headless ground-contact
  probe 写成 can-on-plate、媒体或 Genesis 证据。

## Blocked

- ClawCross/dashboard 的 portfolio、tasks、project 三个状态 URL 均返回 HTTP 404；无法同步 TODO
  控制面，也没有可用 task id 写回进度。
- 2026-09-10 一名 P6 fixed-diff 只读审查 agent 被 OpenAI 安全系统标记为潜在网络安全风险。按仓库
  规则已立即停止该审查，未重试、改写或重新委派；用户随后允许继续其余工作。该具体审查永久保留
  为 blocked，不作为通过结论，也不妨碍修复主窗口已经独立复现的功能阻断。
- Autoresearch 的 benchmark/真实 MCP/Genesis 前置尚未实现；设置已确认但尚不能建立诚实 baseline。
- exact MCP adapter 仍缺 `submit/read` 和真实 Registry handler 交集；caller 提供的内部一致
  `RegistrySnapshot` 目前不能单独作为生产 trust root。

## Done

- P2 首个真实 submit 纵切：公开 `GoldenRunHarness.submit` 在同一父 workflow 中调用 production
  `CompileApplication`，以 exact RegistrySnapshot/live descriptor/qualification/policy 交集冻结执行，
  把 command、qualified execution、controlled child、ToolResult v2、StateDelta v2、workflow receipt
  与 revision 0→1 head 全部绑定到同一 CAS/SQLite authority。generic catalog 与 compile binding/policy
  分模块，compile policy digest 只覆盖自身稳定闭包；operation 幂等域为
  `(workflow_run_id, exact skill_ref, idempotency_key)`；ToolResult 与 workflow receipt 指向同一份
  StateDelta CAS。四个公开重启窗口均已锁定：reservation-before-intent 可安全继续；intent-only 与
  invocation-only 保守保持 running、不重复 handler；child terminal 可只提交父 head。focused 179
  tests，1,354 statements / 392 branches 均为 100%；39 份 schema snapshot 和 compile/replay/application
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
