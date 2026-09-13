# Bingsheng 工作与来源档案

## 角色与归属口径

Bingsheng 是当前 Harness 主责人与全仓整合责任人，负责把 Text2Env、资产检索复用、x2env、仿真
adapter 和证据门组织成统一、可审计的运行路径。该角色包含公共合同、运行框架、资格/门禁、跨模块
接线、回归测试和最终交付，但不会把 Gujie、Yuxin、Genesis、SimFoundry、RoboTwin 或仓库早期基础
代码改记为 Bingsheng 原创。

以下矩阵把“Bingsheng 具体完成的工作”和“由 Bingsheng 负责接入的来源”分开。`主要实现` 表示
当前 git 历史中由 Bingsheng 主导落地；`整合/加固` 表示基础能力已有来源，Bingsheng 完成正式接线
或可信性修复；`设计/实施中` 不等于运行时已经完成。

## 已完成的模块与功能

| 模块 | 具体工作 | 关键代码/API | 归属类型与当前证据 |
|---|---|---|---|
| 确定性 compile 入口 | 将既有 Text2Env Stage 0–5 收束为可调用编译器；发出真实阶段边界；校验 effective catalog 中的资产文件；保持 CLI 失败报告合同 | `scene_gen/compiler.py` 的 `CompileRequest`、`CompileEvent`、`CompileOutcome`、`compile_scene()`；`script/generate_scene.py` | `整合/加固`；代表提交 `d5aa185`、`9af9db5`、`28333de`、`148001d` |
| 运行时物理证据 | 把 runtime evidence 与 resolved scene 哈希绑定；修复视频尾端与 adaptive endpoint 绑定和调用者进程状态恢复；冻结回放所需资产快照 | `scene_gen/validator.py`、`scene_gen/envs/generated_scene.py`、`script/run_scene_runtime.py`，以及 Harness `runtime_assets.py` | `整合/加固`；代表提交 `795273f`、`e6ed0ff`、`7766fee`、`41016c7`；`runtime_sampling.py` 的早期实现不归入本行个人工作 |
| Harness 公共类型 | 在 LV-Robotics Lab 提交 `9b72090` 的初始 schema tranche 上，继续扩展 evidence-invariant SkillDescriptor v2、qualification report、validate v2、workflow、registry snapshot 和 asset-repair 类型，并维护 committed schema catalog | `self_improving/harness/schemas/common.py`、`schemas/text2env_validate_v2.py`、`schemas/qualification_report.py`、`schemas/workflow.py`、`schemas/registry_snapshot.py`、`schemas/asset_repair.py`、`schema_catalog.py`、`json_schemas/` | `整合/扩展`；明确不把初始公共 schema 记为 Bingsheng 单独原创 |
| CAS 与包存储 | 以内容哈希写入、解析并复核 ArtifactRef；原子快照字节；把环境包从临时运行目录物化为可复核包 | `artifacts.py` 的 `LocalArtifactStore`；`package_store.py`；`runtime_assets.py` | `主要实现`；拒绝错 digest、路径逃逸、读写竞态和不可复核引用 |
| 资产准入 | 将新生成资产写成 v3 ledger，核对生成报告、provenance、representation closure 和目录身份，再进入资产库；后续增加 locator-free provenance | `assets.py` 的 `GeneratedAssetAdmitter` | `主要实现/对接 Yuxin 合同`；代表提交 `567969e`、`db24ea9`、`f4542a5` |
| Skill Registry | 注册精确版本 Skill，解析 invocation dependencies，执行 qualified handler，阻止未资格、身份漂移或重复发布的结果 | `registry.py` 的 `SkillRegistry`、`QualificationCandidate`、`RunContext`；`qualification.py`；`qualified_skills/` | `主要实现`；包括 descriptor、implementation manifest、qualification 与 report 闭包 |
| 持久审计 | 记录真实执行事件、按序提交事件链、把 invocation/run state 持久化到 SQLite，并在恢复时重验绑定 | `events.py` 的 `RunRecorder`；`event_journal.py` 的 `SQLiteEventJournal`；`run_store.py` 的 `SQLiteRunStore` | `主要实现`；代表提交 `5ccf779`、`315524f`、`ab03859`、`c365874` |
| qualified compile | 把 typed compile input、固定 catalog/config/asset dependencies、资产准入、包存储和 Registry invocation 接成正式应用 | `handlers/text2env_compile.py` 的 `Text2EnvCompileHandler`；`text2env_compile_dependencies.py` 的 `Text2EnvCompileDependencyResolver`；`application.py` 的 `CompileApplication`；`compile_cli.py` | `主要实现`；真实资格包位于 `qualified_skills/text2env.compile/1.0.0/` |
| portable replay | 固定运行时能力、RoboTwin/任务/资产 closure、执行器策略和事件协议；在受限进程中运行回放，并把输出、物理报告、诊断和媒体重新入 CAS | `replay_application.py`、`replay_dependencies.py`、`runtime_capability.py`、`runtime_executor.py`、`runtime_events.py`、`handlers/text2env_replay.py` | `主要实现`；已有生产资格历史，但当前 source identity 已漂移，必须重签或撤销旧资格 |
| 媒体完整性 | 在 native cgroup/Landlock seam 下调用固定媒体工具链，校验 PNG、MP4 box、逐帧 framemd5、总帧与唯一帧，并记录工具/沙箱身份和资源指标 | `media_verifier.py` 的 `SubprocessReplayMediaVerifier`；`media_sandbox.py` 的 `NativeCgroupSandbox`；`native/media_sandbox.c` | `主要实现`；媒体可见性仍不替代物理通过 |
| validate 与 publishability | v1 validate 保持 non-publishable；v2 从 CAS snapshot 重新读原始证据，不信任调用方摘要；promotion evidence 缺失时 fail closed | `handlers/text2env_validate.py` 的 `Text2EnvValidateHandler`、`RequirePromotionEvidence`；`validate_v2.py` 的 `StrictValidateV2CasReader`；`schemas/text2env_validate_v2.py` | `主要实现`；现有 validate 组件不等于新 Golden run 的最终 promotion 已闭环 |
| 历史 System 2 基础 | 建立可信世界状态、证据、新鲜度、CAS 状态增量、ToolResult、bounded planner context、planner receipt、history authority 和 dispatcher 骨架 | `system2/domain.py` 的 `TrustedWorldState`、`StateDelta`、`System2ToolResult`；`context.py`、`planner.py`、`history.py`、`dispatcher.py` | `主要实现/历史架构`；真实 Qwen provider 也已实现，但 D001 后认知中枢改为外部 Codex，这些可信数据合同继续复用 |
| Golden workflow S1 | 从 CAS user-input evidence 与严格 RegistrySnapshot 启动 actor-neutral workflow；持久化 idempotency；恢复时重验 request/state/registry/receipt 全闭包 | `golden_run.py` 的 `GoldenRunHarness.start()`；`schemas/workflow.py` 的 `RunStartRequest`、`WorkflowStartReceipt`、`RunSnapshot`；`schemas/registry_snapshot.py` | `主要实现`；提交 `3d7aa02`、`e5d3d86`、`7b23ad2`，当前仅 start 闭包，不含完整 submit/read/dispatch |
| 资产债务修复入口 | 把历史 ledger inventory 作为显式受信 CAS 输入，分类 exact-byte recovery、quarantine 与人工处理，不在探测阶段改历史账本 | `asset_repair.py` 的 `AssetRepairApplication`；`schemas/asset_repair.py` 的 `AssetDebtInventory`、`AssetRepairPlan` | `主要实现/对接 Yuxin 历史`；提交 `0f787d0`，当前只是 2/162 的 E0 tracer |
| 合同、验收与过程审计 | 冻结 Codex 角色、exact Skill MCP 外观、x2env Skill 合同、E0–E4、Genesis 最终能力门、TDD seam、实验预算和逐切片进度记录 | `self_improving/golden_e2e_progress/`、`docs/contracts/`、`repo-docs/` | `主要负责`；合同完成不冒充真实 MCP、Genesis 或 Golden E2E 通过 |

## 当前由 Bingsheng 负责、尚未完成的整合

| 工作流 | Bingsheng 的具体责任 | 完成判据 |
|---|---|---|
| Codex 中枢 | 让外部 Codex 只通过 exact MCP Skill 获取可信 observation、提交下一步/诊断，不允许 Codex 自报执行成功 | 同一 run 的 Codex tool-call、ToolResult、receipt、状态版本和新鲜 observation 可重放核对 |
| MCP adapter | 把 `GoldenRunHarness.start/submit/read`、Registry 的 qualified handler 与 MCP 2.2 low-level Server/Client 做薄映射 | schema discovery、exact tool invocation、durable operation/resource、错误映射和 negotiated protocol 证据齐全 |
| 统一 promotion | 把 compile、replay、validate、diagnosis、受限修复与状态推进串为一个唯一晋升授权路径 | 只有完整 CAS/receipt closure 和 `genesis.robot_policy@1` 通过才能标记 publishable |
| Gujie x2env 接入 | 设计 anti-corruption adapter，将 `genenv.*`、URDF/SimFoundry/Genesis 输出映射为 Harness schema、CAS 与 receipts | text/image/video 每条 Golden line 都在当前 Harness run 内独立通过 |
| Yuxin 资产能力接入 | 复用检索、选择、转换和物化能力，修复/隔离历史债务，并为 fresh/exact/digital-cousin 三种来源出具新证据 | 新 ledger schema-clean、来源/许可/closure 完整、真实 runtime qualification 通过 |
| 前端工作台与文档 | 将 dashboard/工作台绑定真实代码事件、operation snapshot、资产媒体、依赖板和源码链接 | UI 展示的每个状态都可追到真实 event/receipt，不使用静态假数据冒充完成 |

## 固定锚点与核对方法

- 仓库：当前 `robot-harness-gen-env`
- 分支：`worktree/bingsheng`
- 本台账首次建立提交：`bf9f24a`
- Golden E2E 合同/边界提交：`ece6c25`、`55f9ff2`、`2bd5e71`
- 当前 workflow/asset tracer：`3d7aa02`、`e5d3d86`、`7b23ad2`、`0f787d0`
- 历史职责说明：`self_improving/contributor_notes/bingsheng-phase2-guidance.md`

最终核对以模块 git history、对应测试、资格包和运行 receipt 为准。共享 dirty worktree 不是稳定
来源；从 Gujie、Yuxin 或第三方移植的每一项能力必须保留独立 `INT-*` 条目。
