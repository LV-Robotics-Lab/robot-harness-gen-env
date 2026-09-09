# 当前 TODO

更新时间：2026-09-10

## Doing

- P0：把 `text2env.replay@1.0.0` qualification 从过宽的整棵 Harness source snapshot 收窄为显式、
  完备、fail-closed 的 replay 实现闭包；候选实现已固定在 `d664b04`，真实装配/资格生成 trace 仅加载
  common/text2env schema 家族，旧资格包仍按预期 fail-closed。当前等待 fixed-diff 双轴审查；最终资格刷新
  必须等 P2 的 Registry/Application 固定后重新执行真实固定案例，不能只改 expected hash。
- P2：以单条 RED→GREEN tracer 实现 actor-neutral `submit`，让同一 S1 run 调用真实 qualified compile
  并原子提交 ToolResult、StateDelta、operation receipt、revision/state head。actor-neutral trusted-state
  kernel 已独立提交为 `6570c82`；签名前审查已复现不同输入产生相同 preflight blocker 时错误复用保留
  child identity 的 RED，正在以 canonical ControlledRunIntent、原子 reserve 与并发攻击补齐。operation
  evidence correlation 和 child-terminal/live-unavailable 恢复矩阵未绿前不得重签或提交 aggregate。
- P6：14 个 GLB + 7 个 `model_data*.json` 已组成 21-member、57,290,434-byte CAS closure，且真实
  RoboTwin `create_actor` 已从 staged-only bytes 对 7 个模型各执行 900 个 SAPIEN steps。候选链
  `9760a03` → `292d09b` → `bd61a9e` → `8c1c94a` 现已补 strict typed authority、成员字节 closure、
  sidecar、从 exact CAS loader bytes 重建 document dependency closure、nofollow materialization，以及
  对成功/失败 report publication 都适用的稳定 parent-dirfd containment/cleanup。最后两个主窗口 P1
  反例已 RED→GREEN；固定 `8c1c94a` 后的真实重跑仍为 21 members、7×900 steps，全部 finite/contact/
  nonzero impulse。当前等待新增 fixed diff 的合入审阅、来源/证据文档同步和主分支回归；完成前不合入或
  升级为 qualification/Genesis 证据。

## Next

- P3-R：P2 actor-neutral aggregate 固定后，新增真实 `Text2EnvReplayApplicationAdapter` 与 replay
  evidence policy；用 `Place a can on top of a plate.`、seed 7、`0/900/120/120/12` 让同一 workflow
  从 compile revision 1 推进到 replay revision 2。replay input package 必须来自当前可信 state，完整
  child/receipt/CAS chain 必须连续；该 tracer 只证明 legacy RoboTwin/SAPIEN 编排，不是 Genesis。
- P2：在 actor-neutral v2 receipt/state-delta 上接入 exact qualified compile → replay 两步调度；不得复用
  planner-specific v1 receipt 字段冒充外部 Codex/MCP 调用。
- P4：S1 的 `submit/read`、OperationSnapshot 和真实 Registry handler 绑定完成后，接入 MCP 2.2 的
  low-level Server/Client；在同一切片冻结 dependency/runtime lock，不在实验循环中安装依赖。
- P6：sidecar closure + staged-only SAPIEN replay 通过并完成 fixed-commit 双轴复审后才可合入；仍不得
  就地修改历史 ledger，也不得把 headless physics probe 写成媒体或 Genesis qualification。

## Blocked

- ClawCross/dashboard 的 portfolio、tasks、project 三个状态 URL 均返回 HTTP 404；无法同步 TODO
  控制面，也没有可用 task id 写回进度。
- 2026-09-10 一名 P6 fixed-diff 只读审查 agent 被 OpenAI 安全系统标记为潜在网络安全风险。按仓库
  规则已立即停止该审查，未重试、改写或重新委派；用户随后允许继续其余工作。该具体审查永久保留
  为 blocked，不作为通过结论，也不妨碍修复主窗口已经独立复现的功能阻断。
- Autoresearch 的 benchmark/真实 MCP/Genesis 前置尚未实现；设置已确认但尚不能建立诚实 baseline。
- exact MCP adapter 仍缺 `submit/read` 和真实 Registry handler 交集；caller 提供的内部一致
  `RegistrySnapshot` 目前不能单独作为生产 trust root。
- 三份 reader-facing repo-docs 仍把 Harness schema 数写成 17；本 feature checkout 当前实际为 31。文件已有用户修改，
  本切片不覆盖或暂存，P14 必须在可安全协调时同步。
- clean root suite 的旧 replay qualification source identity 仍有 1 个失败；原始真实 qualification
  settings/CAS 尚在，但旧 delegated cgroup 已不存在，且后续 Harness 实现还会继续改变源码身份。
- P6 第一版 sidecar fixed commit `292d09b7775f75576063f40969c2de078d1c45ab` 的 7×900-step
  headless 物理探针是真实运行，但其 attestation/portable-path/closure identity 尚未通过双轴审查；该
  report 只能作为被拒绝迭代的运行记录，修复并复审前不得升级为最终 stage 证据或合入主分支。
- 2026-09-10 的完整相机 replay 第一次因本机 GPU 被无关训练进程占满，在 observer camera 创建 buffer
  时失败；第二次强制 Lavapipe 因 Vulkan 扩展缺失失败。两次都不是 runtime pass，且不得终止不属于
  本任务的 GPU 进程；当前用无渲染真实 SAPIEN probe 验证 loader/physics，媒体门留待资源可用时补跑。

## Done

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
- P6 第二个 S5 exact-stage 纵切：以 trusted inventory + rebuilt canonical plan + path-free source
  manifest + deployment-only local-root binding，从本机未版本化 RoboTwin 来源逐 member 重算 hash/bytes，
  把 `003_plate`/`071_can` 的 14 个 GLB（57,226,572 bytes）写入新 CAS，并枚举每个 representation 的
  loader closure。结果 hash-bind 三个输入 ref，且明确 simulator/runtime qualification/promotion/
  authoritative-ledger-write 均为 false；未改历史 ledger 或来源资产。
