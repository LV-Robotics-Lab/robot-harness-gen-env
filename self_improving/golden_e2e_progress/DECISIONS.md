# 稳定决策

## 2026-09-13 动态支撑先证明表面再放开图

- 依批准计划C08/C09补单支撑DAG，而非删除rigid-on-structural门即声称通用支持。
- 实际加载拓扑、保孔洞的视觉/碰撞共同支撑面及逐帧目标坐标是前置；整体凸包不能冒充凹盘底。
- 多实体生成布局采用显式新授权和v2证据；旧v1不自动追认。增量契约见CANONICAL_MULTI_ENTITY_SUPPORT_SPEC.md。
- v2首片字段现已固定：显式generated_layout模式、部署数值范围仅约束新设计值、1..8实体的直接结构支撑。
  深层动态目标和未知world-Z仍拒绝；这些限制不由模型或新receipt自行解除，旧缺mode配置继续v1。

## 2026-09-13 用户原媒体和派生重建发布授权

- 用户明确允许“鼠标和笔.jpg”“鼠标视频.mp4”的原媒体及派生重建资产按CC-BY-4.0发布，
  并在后续确认署名为`x2env1.0`；授权来源为本任务用户回复，不推断自然人身份或目录所有者。
- 固定图片SHA-256：`044e68562224788ad4e395d9c7cae49c148eb509a15f9ff11eb4aa39bc467d48`。
- 固定视频SHA-256：`5c7aa9c809aaff9c5f4d3076502d99cba0f72e46754102184b9c5c9db8d88634`。
- 授权适用于以上bytes及其派生；实际来源/模型许可与代码归属分别保留，不由此许可覆盖第三方限制。
  GitHub证据prerelease仍以完整验收门通过为前置，此授权本身不是运行或发布完成证明。

## 2026-09-13 必要设计信息不由模型 critical 标记授予

- Harness按实际未解决的布局/yaw/必要结构尺寸决定补全或澄清；原unknown及critical标记不改写。
- 明确报告的未解决conflict不因critical=false或字段已有数值而获得授权；显式已解决override不在此列。
- 保留原编译合同允许的显式结构默认、Registry实测尺寸与唯一支撑on-Z推导；不能把这些已有依据
  错判成必须调用模型。未知foreground X/Y无此默认授权，compile与completion都须拒绝。
- 模型未列unknown行时，实际缺失字段仍可产生有界设计rules；空indices不等于无设计，原行不伪造。
  新design receipt由完成门独立重算规则；旧无design_plan合同仍要求非空原索引。

## 2026-09-13 唯一受管理 Codex transport 显式请求最大力度

- 现固定传 `-c model_reasoning_effort="max"`；用户/模型prompt不能降级，也不在拒绝后换值。
- invocation记录 requested_reasoning_effort=max、server_effective_effort_verified=false；
  CLI接受并完成调用不证明服务端实际执行力度。旧未显式指定的回执不回填为max。
- 中性最小探针只证明CLI兼容；此改动满足最大力度与可审计约束，不声称解决600秒业务超时。

## 2026-09-13 安装源码身份不是运行权限或资格

- 预览的 Git/installed 身份只记录实际执行源；部署可 pin commit 或原始 RECORD digest，
  不能由模型选择身份模式。未固定的 RECORD 只证明安装成员内部一致，不是发行签名。
- denied_roots 使用独立部署值，不通过 __file__ 层数推算并误拒整个 site-packages。
- 显式 Git 失败不切模式。自动识别只接受与本仓库 canonical 源布局对应的 Git 根；
  无关祖先的 Git（包括宿主 /tmp 既有损坏标记）不能抢走 installed 身份。初版过宽祖先
  检查已由真实 wheel-in-checkout 攻击修正，未删除任何宿主标记。

## 2026-09-13 文本资产尺寸设计不冒充媒体观测

- 在显式开启的 asset-anchored simulation 策略下，foreground 未知尺寸可以采用实际 Registry
  几何尺寸；它是仿真设计选择，不是从文字或图像测得实物尺度，不需要伪造媒体。
- 仅严格匹配 null 尺寸轴的 asset_anchor 可以处理 unspecified；未知 XY/yaw 的 media_layout
  继续要求媒体。已知尺寸轴、关系、冲突与默认禁用策略的边界不变，不改冻结 S01 输入。
- 老固定代码的 design_plan.requires_media 可能与新合同不同；旧回执保留原源码验证，不回填升级。

## 2026-09-13 本地颜色续点与历史包复用边界

- 前置颜色修复保留原 local mismatch 与原 pending 场景；只有已提交的新版本、新预览及原 a6 MATCH
  才能续接。失败/取消仍占共享预算，失联且无结果不自动重做资产写入。
- 续点与最终包共用原提案/批准/子版本的历史审计；该审计不提供新的执行权限，不要求当前模型路径存在。
- 历史 review index 已包含 representation 描述，不等于包含描述所引用的必要资产；新 Registry
  导入必须核实际传递闭包与许可。S01 使用许可明确的 PolyPizza 来源，不借旧 mouse 描述回填缺失证据。

## 2026-09-13 结构设计权威与视觉估计分开

- 结构厚度/表面高度来自部署 StructuralPolicy；默认世界 XY/yaw 需要显式 SceneDesignPolicy 授权和值。
- 原场景局部 on-z 可由已登记资产实测高度推导；与已知 z 冲突即拒绝，不借默认覆盖用户数值。
- classify_design_unknowns 只解释已知实体的精确 null 字段，critical 原样保留。模型无图时不得做媒体估计。

## 2026-09-13 失败修订也消费共享预算

- 修订成本必须在执行前持久预留，不能仅累计成功回执；场景与资产共用两次成本。
- 失败/取消不退还，重复失败指纹拒绝。预算绑定 workflow head，而不是混用 SceneIR revision。
- 旧历史记录保持可读，不回填虚构批准；canonical controller 新动作显式携带预留。

## 2026-09-13 图像尺度缺失与生成设计分离

- S02 原失败证明图像不提供绝对米制尺度；不改冻结输入，不将 critical 改为 false。
- 显式部署 SceneDesignPolicy 可允许结构化 scale_unobservable/pose_unobservable 进入待补全意图。
  conflict、unsupported 和未分类关键未知仍要求澄清；待补全只查资产，不是 accepted SceneIR。
- 已选不可变资产的实测 AABB 是生成尺度锚点，受管 Codex 只能补未知尺寸/坐标/朝向；保留已知轴、
  实体、关系、frame 与原始未知记录。该片限定一个刚体与一个 on 结构支撑，超范围明确拒绝。
- 补全结果记录 simulation design-choice 与 real_world_scale_recovered=false，经检查后才进入 compile；
  不授物理通过，不修改成功阈值，不允许用户 prompt 自行开启部署策略。

## 2026-09-13 用户补充：替代后端与持续执行

- 用户已批准先做替代重建后端，保留 Gujie 原接口方便后续联调协商；它不是默认第二工作流。
- 单个许可/资源阻断进入待解决账本，主线并行推进；仅在确实不能达到 push 硬门时停止并准备失败证据。
- 未授权伪造成功或放宽矩阵/物理阈值；固定来源、不可变资产、真实 Gujie seam、三个 copy-run 和
  qualification/prerelease 门仍成立。main PR 仍需另行批准。

## 2026-09-13 C01/C07 安全停止

- 批准有效，矩阵/物理阈值不变。C00 完成，C01 先保护而不清理：用户普通文件与子模块 dirty
  bytes 已在本地 Git refs 和实际恢复目录核验，其他分支处置与 mixed hunks 尚未完成。
- C07 自定义模型/输出许可与新后端资源属于计划 §17.3 的用户方向点，不能由 agent 接受新条款、
  放宽资产许可、无许可依据发布或以 primitive 代替重建。暂停等待方向，保留所有现有字节。

## 2026-09-13 canonical x2env 合并实施计划

- 用户已逐项确认 Q01–Q64，权威检查点为
  [`CANONICAL_X2ENV_MERGE_DECISIONS.md`](CANONICAL_X2ENV_MERGE_DECISIONS.md)。它固定三个 public
  Skills、统一多模态入口、Harness/Codex 控制关系、分支与 dirty-byte 保护、Registry/存储边界、
  4+4+4 验收矩阵、30 分钟核验上限、GitHub Release 制品路径、push 后等待 main PR 授权等计划输入。
- 澄清已经结束，逐文件/逐提交/TDD/真实资格/branch-push 计划已生成在
  [`CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md`](CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md)。
  用户于 2026-09-13 明确批准开始实现，Qualification Matrix v1 已冻结。不把批准写成已实现、
  qualification 或 push。

## 2026-09-13 本轮实验审阅停止点

- 三模态、三来源、A/B修复与复制包运行已有小型真实证据；暂停供用户审阅，不扩大为正式release。
- 最终限定回归保留旧qualification哈希拒绝，不改资格使其变绿；限定综合覆盖率72.98%如实报告。
- 新实验MCP投影、父终态、通用重建/cousin/多物体/数值修订与机器人接口仍未授予。
- 最后再次读取dashboard三入口均404，没有可更新task id；不伪造同步。

## 2026-09-13 小型真实覆盖的验收口径

- 模态text/image/video与source local/web/generated分别记账，允许案例交叉覆盖；centered成功不
  撤销offset视频预算耗尽或旧image目录失配。不得用单一成功百分比掩盖布局/视觉能力差异。
- 模型读取有界实际catalog摘要及digest；有可验证不可变receipt才提供尺寸。目录只是能力上下文，
  不给类别造别名、不强迫匹配。缺资源在模型前结构化blocked，不浪费模型修订预算。
- fallback A改变实际场景；B隔离产生新asset版本，本轮scale=3.5不改旧版本。视觉与物理分账，
  有界失败停止保留全部已产出证据。数值配置修订未实现，不通过改成功阈值制造pass。
- 此实验阶段完成门仍包括最后测试、可迁移包及walkthrough审阅；新experiment命名空间未投影MCP，
  不从现有qualified MCP历史能力推导新工具可用；父terminal、晋升、robot/data均不授予。

## 2026-09-13 实验接线落实

- 本阶段新阶段用`experiment.<stage>@1.0.0`开发descriptor，仍通过原SkillRegistry与Golden内核；
  不把未取得qualification的新源码塞进旧qualified namespace。旧MCP路径不改，新开发工具尚未
  投影到qualified MCP。内部Codex.propose不掌握shell/业务工具权限，Harness执行建议。
- 源码/配置身份使用显式declared-files开发authority，旧authority遇源码改变拒绝恢复；结果不发布。
- replay执行完整与物理判定分开：完整双profile轨迹才是执行passed，物理failed原样保留供诊断；
  validate继续独立重算，visual_status独立报告，模型不能覆盖物理失败。
- 资产source仍local/web/generated；generated当前是明确的procedural proxy，不是通用重建服务。
  cousin没有实选实现，返回unsupported_selection而不是只记录cousin标签。
- 有限桌面单刚体是当前实现范围；未执行机器人policy/数据收集；父workflow仍active与阶段序列passed
  分别呈现，不用改SQLite或伪终态制造交付。

## 2026-09-12 安全停止与通用实验管线优先

- 状态：`accepted`，替代此前“先完成 normal+repair，再建立新 release 部署并发布”的执行顺序。
- 当前会话在最近安全边界停止：不再追加 repair 验收、深读、bootstrap、资格重建或 release。normal
  已通过的开发证据、`69aa6c2` 的历史资格、repair 的非终态 SQLite/CAS/日志和所有失败材料原样保留。
- 下一阶段优先把 text/image/video 及其组合、local/web/generated 三种资产来源实际接入 Harness
  → CodexBackend → Skills → Genesis → observation/diagnosis → validate → portable package。每种模态
  和每种资产来源至少一个小型真实案例；不要求此阶段全组合或高稳定率。
- Harness 继续是唯一用户入口、状态和生命周期权威。Codex 是内部建议者；现有 MCP 只是协议/工具
  边界。复用现有 controller、journal、CAS/SQLite 和 Skills，不增加第二套业务 authority。
- development 可以在未取得新 release qualification 时真实执行 changed implementation，但必须由
  部署配置显式启用、记录源码/环境/省略检查、与正式库隔离。当前 v1 record 的
  `exact_skills_qualified=true`、`changed_exact_skill_closure_allowed=false` 仍是事实；需要时以最小、
  显式升级调整，不能伪造资格、替换 verifier 或让 release 自动降级。
- fallback 分为两类：场景/布局问题生成受控 Prompt/SceneIR revision 后回到 compile；资产问题在
  隔离目录形成并检查新的不可变资产版本或重新获取，再重解依赖和 replay。数值配置另走白名单修订。
  每类均有预算和确定性重复停止条件；视觉建议不替代物理指标。
- 新包必须包含场景和资产、可迁移引用、Genesis loader、依赖说明、来源/修订/验证、图片与连续视频；
  至少一次复制到新目录后真实 load/step。`sim-ready` 只授予实际验证 profile，robot policy/data
  collection 继续后置。
- 正式 qualification、全矩阵、autoresearch、162 份历史 ledger、全面性能优化、robot policy 和无关
  前端保留但暂停。权威交接为
  [`HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md`](../../docs/history/golden-e2e/9460a4b9ac3825e44b6e6641d5fdafe3034a8a6e/self_improving/golden_e2e_progress/HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md)。

## M1 收口与 fresh observe v3

- `1cf8908` 的首条完整 rigid-scene line 已真实通过并集中验收，下一 active 为 M2 Harness 入口、
  受管理 CodexBackend 与 same-workflow exact MCP。M1 只覆盖已知鼠标图像 exact reuse、有限桌面与
  ground；不代表三模态、
  全新资产生成、digital cousins、MCP 或机器人策略完成。
- attempt-1 的 `genesis.observe@2.0.0` 保留原纯读帧与 TTL 语义，过期失败不得补写时间。
  新 `genesis.observe@3.0.0` 使用独立固定 runtime 恢复可信 replay 的最终 pose、归零速度、
  零物理步后实际 render；权威为 `observed_restored_final_pose`，`process_continuation=false`。
- asset、scene replay、fresh capture 三个 runtime 身份分账；原 replay 资格不因新 observer 重签。
  完整链交付仍需 captured ≤ checked ≤ delivered ≤ expires，TTL 上限 300s。

## M1 场景执行与资产资格分账

- 资产资格仅证明它绑定的 asset runtime；新增 renderer 场景 runtime 单独固定、实际执行并核对。
  两个 lock 可以不同，不能把旧 asset qualification 当成新 scene runtime 的证明，也不要求无关的
  重复资产签发。只有 consumer 显式要求相同运行身份时才补 fresh qualification。
- 场景生产执行先由固定 producer 建立不可任意构造的 live capability 与完整 execution receipt；
  持久调用复用 `SQLiteRunStore`/`SkillRegistry`，不另建第二套通用状态库。
- 内部 execution receipt 不等于 Skill qualification。真实 candidate 执行、固定 descriptor/资格、
  production durable invoke 是分开的必要步骤；新增 native I/O 显式版本化，不静默改旧合同。

## 本阶段优先级调整 — sim-ready服务先交付，policy后置

- 状态：用户已批准详细计划并要求实际整合Gujie工作；M1首条完整线已验收，当前M2。
- 顺序：完整Genesis环境线→Codex同workflow闭环→三模态/三资产路径→入库/历史债务→系统验收、
  仓库整理与开放服务→暂停；P8/E3机器人联调留待其他项目组参与后重新启动。
- 第一阶段不再以前述`genesis.robot_policy@1`作为交付阻断；旧E3/E4与历史证据保持原义，不把E2
  环境服务冒称旧E4。正式新验收/benchmark与promotion profile合同将在计划批准后同步。
- 详细计划：[SIM_READY_RELEASE_PLAN.md](../../docs/history/golden-e2e/7c739e36b76c3812cac959546fb7f9e52e4e4340/self_improving/golden_e2e_progress/SIM_READY_RELEASE_PLAN.md)。

## D001 — Codex 是认知中枢与视觉判读者

- 日期：2026-09-09
- 状态：accepted
- 决策：Codex 取代原计划中的独立 System 2 LLM 中枢和独立 VLM。Harness 保留确定性、类型化、
  fail-closed 的执行与证据权威；Codex 只能提出下一步、诊断和视觉判断，不能自报执行成功、物理通过
  或 publishable。
- 影响：现有 Qwen planner/VLM 代码作为历史实验或可选 adapter 评估，不再决定目标架构。新接口优先
  表达 `Harness context → managed Codex proposal → Harness validation/execution → trusted result → next turn`。

## D002 — Golden output 是 sim-ready Genesis 环境

- 日期：2026-09-09
- 状态：accepted
- 决策：文本、图像、视频或组合 prompt 的最终机器产物必须能被 Genesis 原生加载，供机器人策略
  测试或数据采集；同时输出哈希绑定的资产、场景、运行报告、图像和连续视频。
- 边界：是否复用 `worktree/gujie` 的 Genesis 原生输出不影响其接入 Harness，但非 Genesis 原生输出
  必须经过明确 adapter 和真实加载门，不能仅凭文件扩展名或 render 判为 sim-ready。

## D003 — 证据权威与晋升

- 日期：2026-09-09
- 状态：accepted
- 决策：compile success、Codex/VLM judgement、媒体可见和 replay process success 均不能单独给出
  publishability。只有哈希闭包完整、真实 Genesis replay/validate 通过且 promotion gate 接受的候选
  才能晋升。

## D004 — Deep aggregate 与 exact Skill MCP

- 日期：2026-09-09
- 状态：accepted
- 决策：workflow 业务通过 S1 `GoldenRunHarness.start/submit/read` 聚合；用户、frontend、API 与 CLI
  只进入 Harness。Harness 内部管理 CodexBackend，并可给它分配经 S2 MCP adapter 暴露的当前任务
  exact tools。MCP 不提供通用 `skill.invoke`；耗时执行以 durable operation/resource 呈现。
- 边界：MCP 只做协议转换，Codex 的 route、视觉判断和诊断是 advisory。Harness 对生命周期、状态、
  执行、重试、恢复、validate 和 promotion 保持唯一权威。

## D005 — Genesis 最终能力门

- 日期：2026-09-09
- 状态：accepted
- 决策：最终 golden case 必须满足 `genesis.robot_policy@1`，包括版本化 robot profile、reset、typed
  action/observation/termination、bounded nonzero action probe 与 trajectory；仅 load/build/step 的
  `genesis.rigid_scene@1` 是中间门。
- 边界：该能力不声明训练 policy 成功或真机可用。

## D006 — Autoresearch 冻结设置

- 日期：2026-09-09
- 状态：accepted
- 决策：路由 suite 为 36 cases、执行 suite 为 12 cases；primary metric 是
  `closed_loop_success_rate`，higher is better。首轮独立 worktree 最多 30 次或 12 小时，保持已确认的
  in/out scope、保护门和简单性优先策略。
- 前置：真实 benchmark command、MCP、Codex 与 Genesis 基线全部可执行后才开始实验计数。

## D007 — 功能级来源与整合责任必须分账

- 日期：2026-09-09
- 状态：accepted
- 决策：以 `docs/integration-provenance/LEDGER.md` 作为 Bingsheng、Gujie、Yuxin 工作进入 Golden
  E2E 的核对清单，并以 `sources/*.md` 的逐人模块/功能矩阵回答“谁具体做了什么”。每项能力分别
  记录原始来源、第三方上游、Harness 整合责任、固定 ref、目标路径、合同和验证状态；功能切片与
  台账更新必须同一提交。
- 边界：目录所有者或整合者不自动成为被整合代码的原作者；`integrated` 不等于
  `runtime_pass`；未固定的 dirty workspace 字节不能计入完成来源。

## D008 — 保留 legacy validate v2，Genesis validate 升为 v3

- 日期：2026-09-10
- 状态：accepted_by_existing_upgrade_clause
- 决策：既有 `harness.text2env_validate_input/output.v2` 明确绑定 `EnvironmentPackageV1`、RoboTwin
  receipt 与 `robotwin.scene_validation.v1`，因此只用于 legacy RoboTwin/SAPIEN validator。Genesis
  `EnvironmentPackageV2` 与 `genesis.robot_policy@1` 的 text validation Skill 固定为
  `text2env.validate@3.0.0` / `text2env_validate_v3_0_0`；不得原地改变 v2。
- 依据：D003 的唯一晋升权威，以及 `SKILL_CONTRACTS.md` 已接受的“不兼容则升 v3”条款。
- 边界：本决定只冻结版本语义，不表示 v2 或 v3 已实现、资格化或运行通过。

## D009 — replay 源码资格使用逻辑安装根，部署依赖继续物理绑定

- 日期：2026-09-10
- 状态：accepted
- 决策：`text2env.replay@1.0.0` qualification 的源码 identity 由逻辑根
  `replay_distribution`、相对路径、exact bytes/file hash 与 closure tree hash 构成；绝对源码安装路径
  只作为 load 时的本地 mapping，不进入资格身份。loader 必须在该 mapping 上重新执行完整的三轮
  nofollow closure，不能跳过 source identity。
- 部署分账：RoboTwin 资产、解释器、media launcher、静态 FFmpeg、runtime capability 与 delegated
  cgroup 继续作为 machine-local operator binding 记录和复验。worker `PYTHONPATH` 只有精确等于已声明
  local source root 时才映射成逻辑身份，其他值仍保持部署敏感。
- 边界：这只保证同一源码字节/资格包跨代码安装绝对路径复用，不提供跨机器 provisioning，也不扩大为
  validate、Genesis 或 promotion。

## D010 — Image 与 Video 保持不同身份，legacy 输出先适配再重跑

- 日期：2026-09-10
- 状态：accepted_by_contract_implementation
- 决策：`image2env.*@1.0.0` 与 `video2env.*@1.0.0` 使用不同 source-media schema 和 exact
  compile/replay/validate codec。Video 必须持久化完整 ordered frame manifest、总/唯一帧数、FPS、时长
  与 sequence digest；抽帧不能降格成 Image 输入或完整视频证据。Gujie `genenv.*` 只经
  anti-corruption adapter 进入 `legacy_genenv_adapted` candidate，随后在当前 Harness run 重做真实
  Genesis replay/validate。
- 边界：合同与旧输出中的 `passed`、render、文件格式或 process success 均不授予 simulator-ready、
  physical pass 或 publishable。受管理 CodexBackend 提供 proposal/advisory，不在 exact Skill 内调用
  模型。

## D011 — Fresh observation 先闭合受控证据链，再开放 production tool

- 日期：2026-09-11
- 状态：accepted_by_existing_skill_contract
- 决策：fresh observation 的 exact Skill 固定为 `genesis.observe@1.0.0`。首个受控纵切复用 S1
  `GoldenRunHarness.submit/read`，并要求 FreshObservationReceipt、`ToolResult.fresh_observations`、
  `TrustedToolReceipt.derived_fact_claims`、StateDelta 与 resulting state 对 key/value/capture time/TTL
  逐项绑定。原始 observation 指向 FreshObservationReceipt，可信状态 fact 指向闭合它的外层
  TrustedToolReceipt。TTL 取 `1..300` 秒，使用闭区间 `[captured_at, valid_until]`。
- 新鲜度：capture timestamp 由 runtime 产生，`valid_until == captured_at + TTL`；历史截图、旧 replay
  media 或旧 observation receipt 只能作为历史证据，重新包装不获得 fresh 身份。
- 边界：受控 test adapter 必须记录 `simulator_executed=false`。真实 Genesis sealed-checkpoint capture、
  production qualification 与 production Registry/MCP exposure 未通过前，不得把受控结果写成 Genesis
  runtime pass，也不得在 production MCP 目录列出 `genesis_observe_v1_0_0`。

## D012 — Advisory 与 prompt revision 是不改可信状态的 workflow controls

- 日期：2026-09-11
- 状态：accepted_by_implementation
- 决策：external-agent advisory 与 prompt revision 不伪装成 qualified Skill、ToolResult 或可信世界事实。
  它们通过 `GoldenRunHarness.submit_control` 形成两类不可变 workflow receipt；与 Skill 共用唯一 turn
  仲裁，推进 revision、turn sequence 和 receipt head，但保持 state ref/hash 与 child runs 不变。
- 权限：advisory 固定 `authority=advisory_only`、`external_agent_executed=false`，且不得授予 physical
  validity 或 publishability。prompt revision 必须显式绑定 source prompt、advisory、失败 receipt、
  predecessor package 和 fallback lineage；不能由 adapter 静默选择 fallback。
- 读取边界：workflow resource 可达不等于 external agent 实际读取。后续必须用 server-owned durable
  resource-access receipt 记录向哪个 principal/workflow 交付了哪些 bytes；它仍只能证明交付，不能证明
  模型理解或隐藏推理。两个 MCP control tools 完成且真实 Codex 回合运行前，所有记录继续保持
  `external_agent_executed=false`。

## D013 — Resource access 只证明 server delivery，且 advisory 必须精确覆盖来源

- 日期：2026-09-11
- 状态：accepted_by_implementation
- 决策：外部 MCP 的 workflow artifact 读取必须在同一 Golden SQLite authority 的独立 append-only
  journal 中产生 `ResourceAccessReceipt`。读取不是 workflow turn，不推进 revision、turn、state、head、
  active operation、child runs 或 updated time。回执绑定 server principal、workflow、完整 observed
  snapshot、resource URI、ArtifactRef、delivered bytes/hash、representation 与 served time。
- 精确覆盖：每个 advisory 必须提供 server-owned receipts，其 resource targets 与 source prompt、可选
  source prompt revision、全部 failure receipts、observation receipts 和 diagnostics 无条件集合相等；
  zero、partial、extra 或 unrelated reads 都不能提交 control。receipt 自身只有在被 control 引用后才进入
  workflow 主闭包。
- 边界：`delivery_scope=server_response_payload` 只证明 server 为该 principal 的读取请求交付了相应
  bytes。`transport_acknowledged=false` 与 `model_understanding_claimed=false` 固定为 false；真实 Codex
  tool-call transcript 可以证明请求发生和响应被送入 client 事件流，仍不能证明隐藏推理或认知。

## D014 — Prompt revision 必须由版本化 compile attempt 显式消费

- 日期：2026-09-11
- 状态：accepted_by_implementation
- 决策：当前 receipt head 是 `harness.prompt_revision.v1` 时，下一次 Skill 只能是 receipt 中指定的 exact
  compile Skill，并必须通过 discriminated `prompt_revision` attempt 显式提交 revision、effective prompt、
  全部 failure receipts 与 predecessor package；video→image fallback 还必须精确使用 receipt 选定的 frame。
  Harness 在发布 command CAS 或分配 child 前重验这些字段，禁止 adapter 选择 latest revision 或静默 fallback。
- 版本：已资格化的 `text2env.compile@1.0.0` 保持冻结，不改 schema/handler/qualification。尚未资格化的
  `image2env.compile@1.0.0` 与 `video2env.compile@1.0.0` 在首次资格前将 descriptor input 切到 v2；旧 v1
  schema 保留 parse-only。未来 text retry 使用新的 exact `text2env.compile@2.0.0`，不能借 v1 暗中改 prompt。
- 边界：当前只完成受控 image compile preflight/blocked 纵切；production handler、qualification、Genesis
  runtime 和成功 environment package 仍待实现。

## D015 — Asset qualification 是跨场景单资产权威，scene closure 是 compile 聚合

- 日期：2026-09-11
- 状态：accepted_by_implementation
- 决策：`GenesisAssetQualificationV1` 只绑定一个 `AssetRepresentationV1`、其 loader closure identity、
  exact Genesis runtime lock、profile、issuer 与 `backend_load`/`collision_probe` evidence；它不引用场景级
  `AssetClosureManifestV1`。因此同一 qualification 可以在多个不同 scene closure 中复用，不会因无关
  共场资产、排序或场景重组而改变身份。
- 消费门：compile deep verifier 必须解引用 portable catalog 的每个 available entry，而非只读 selected
  entries；representation members、qualification evidence、trust set、runtime/profile/issuer/subject 均须
  闭合。scene closure、selection、binding、package 与 compile receipt 再在 compile 层聚合。
- static 门：首个 exact-reuse slice 固定四项 evidence-bound check：`asset_closure_complete`、
  `binding_complete`、`runtime_lock_bound`、`scene_task_consistent`。任意 noop、缺项、多项或错 evidence
  均拒绝。该 static 门不声称真实物理、sim-ready 或 publishability。

## D016 — 首个 image compile 是 input-bound controlled candidate，不是生产资格

- 日期：2026-09-11
- 状态：accepted_by_implementation
- 决策：`harness.x2env_compile_receipt.v2` 必须携带同一 CAS 中的 canonical compile input；image/video
  compile output v2 只接受该 receipt。首个 image exact-reuse handler 必须先完成纯读 preflight，再发布
  deterministic derived graph，并在 postflight deep verify 成功后一次 emit。preflight 只做大小写敏感的
  semantic name/alias 唯一匹配，不做视觉相似、尺寸推断或静默 fallback。
- 受控常量：首个 slice 限制为一个 dynamic object 和一个 `on_table` relation；candidate placement、
  observation keys 与 termination steps 是 implementation-bound constants，不声称来自图像或物理求解。
- CAS 边界：preflight 失败不得产生派生 CAS。首次 publish 后若发生本地写入/构造/postflight 故障，当前
  append-only CAS 可能保留不可达 orphan；没有 batch transaction 时不得声称物理零 orphan。任何 orphan
  都不能进入 HandlerResult、workflow state 或 receipt head。
- 发布边界：candidate 始终 `simulator_executed=false`、`simulator_ready=false`、`publishable=false`，不注册
  qualified Skill/MCP。只有真实 Genesis asset qualification 与 production bundle 通过后才能扩大主张。

## D017 — legacy Genesis pass 不可迁移为当前 asset qualification

- 日期：2026-09-11
- 状态：accepted_by_evidence
- 决策：Gujie 历史输出中的 asset bytes 可以作为 staged representation candidate 来源，但旧 scene/task/
  physics status、截图、视频和退出码不能直接产生 `GenesisRuntimeLockV1` 或
  `GenesisAssetQualificationV1`。
- 证据：指定 exact pass 目录已不存在；最接近归档虽有 479/479 manifest 和 15-member mouse URDF closure，
  但 TaskOutput/workflow absolute locator 已失效、两份 physics evidence 不满足当前 threshold contract，且
  记录的 7 个 runtime module SHA 与固定 `eb0b710` 为 0/7 匹配，也不存在可重建该实现集的 Git commit。
- 后续门：当前 implementation/issuer 必须生成 sealed runtime lock，并 fresh 执行 asset-scoped dynamic load、
  collision-enabled contact probe与 typed report；relocated fresh-CAS strict reload 后才能进入 production
  catalog/Skill qualification/MCP。

## D018 — Asset probe pass 与 production qualification 分账

- 日期：2026-09-11
- 状态：accepted_by_evidence
- 决策：asset-level Genesis probe 由 Harness-owned、无相机 CPU backend 产生原始 load observation、
  1,001 行 contact trace 和七项 typed report checks；deep verifier 必须仅从 CAS 重算 source/loaded identity、
  contact/penetration/force/impulse 与 settling。源 collision member 数与 Genesis loaded geom 数是不同事实，
  不允许从 8 个源 OBJ 推断 8 个 runtime shapes。
- 证据：legacy mouse fresh run 实际得到 8 个 source collision members、1 个 merged loaded geom、989 个
  contact steps、4,040 contacts、最大穿透 0.487 mm 和 settled pass；23-object CAS 在第二绝对路径、无
  Genesis/source root 的进程中重验通过。
- 资格边界：probe report 不是 qualification。只有 runtime lock 进一步封住 interpreter、Python、Torch
  CPU、Quadrants、NumPy、native libraries 与 runner implementation，durable issuer 才能从 opaque verified
  capability 最后签发 `GenesisAssetQualificationV1`。当前不得注册 production Skill/MCP。

## D019 — Runtime 内容清单与完整执行闭包分账

- 日期：2026-09-11
- 状态：accepted_by_user_scope
- 决策：先新增版本化 runtime content manifest，固定逻辑根、完整成员的 CAS bytes 与 process identity；
  deployment mapping 单独提供，不进入内容身份。CAS-only 验证不得称为部署验证；部署目录重验也
  不单独证明实际 import/native dependency discovery 完整。
- 后续门：完整 runtime lock 必须另封住固定解释器/ABI、实际 distribution RECORD 与成员、Genesis
  source、backend、系统 native libraries、启动方式、线程和进程环境。现有 v1 schema 语义保留，不能
  用新 manifest 的通过状态暗中授予 production qualification。
- 实测原因：只读依赖审计发现 headless CPU import 仍加载系统 EGL/GL/native libraries，且 Torch
  inter-op 线程未被现有七项变量固定；普通 venv 启动还会执行指向其他工作树的 editable `.pth`。
  独立物化目录与 `-S` 的固定 import 路径验证必须先于资格签发。

## D020 — Runtime execution receipt 必须由固定执行产生

- 日期：2026-09-11
- 状态：accepted_by_user_scope
- 决策：在声明内容清单之上增加新execution request/observation/receipt，而不修改已提交probe v1
  语义。既有runtime lock的dependency字段显式绑定manifest，新lock驱动fresh probe；receipt绑定
  manifest、固定runner身份、真实child观测和probe全部证据，最后发布。
- 执行：Genesis从manifest包含的归档源码运行，不借用Git checkout；精确系统native别名是独立部署
  定位，必须对应已声明成员并逐字节重验。缺少运行约束或发生依赖漂移时拒绝，不扩大目录允许范围。
- 资格边界：CAS纯读证明完整性，不自动证明执行来源。后续issuer只接受固定producer的opaque live
  execution capability；重启后必须经durable intent/terminal对账恢复，不能把任意自洽报告转成资格。

## D021 — 首个可用版本收敛为单一 image exact-reuse 预览闭环

- 日期：2026-09-11
- 状态：accepted_by_user_scope；入口关系由 D022 更新。
- 决策：当前唯一 active 交付的调用关系为
  `Harness controller → managed CodexBackend → assigned exact MCP → compile / Genesis replay / validate`。
  这是有限桌面鼠标闭环；观察、诊断、真实布局修改、重编译和隔离发布都进入同一 workflow journal；场景包
  manifest 统一绑定输入、资产、布局、运行、媒体、验证和耗时。
- 复用与简化：复用 M1 applications、修改后 compile application、Genesis execution v3 核心和真实
  Codex/MCP wire 见证。M2 不为 replay、observe、validate 各自新增资格、SQLite authority、terminal
  admission 或 receipt 框架。若旧 replay@2 无法消费 compile@3 receipt v2，只增加一层共享的受限
  执行绑定，并把这个已复现的版本不兼容记录为理由。
- 验收：至少一个正常案例和一个真实诊断修复案例；修复必须改变场景包中的实际位置、方向或其他
  允许布局字段，并再次真实 Genesis replay/validate。最终 Codex 从原始图像与文本开始，不依赖人工
  预填 proposal。超范围输入明确失败，不能替换为固定样例。
- 性能与时效：先记录输入/资产、compile、runtime/完整性、仿真/渲染、CAS 深读、Codex/MCP、
  validate/publish 分段耗时，再针对不可变闭包的重复验证优化；复用必须绑定不可变身份和明确失效条件。
  观测紧邻 Codex 消费产生并实测 TTL 余量，不通过简单增大 TTL 掩盖重复开销。
- 暂缓：三模态、三种资产路径、162 份历史债务、autoresearch、机器人 policy、通用插件与额外
  backend 都保留但不阻塞本版本。预览固定、服务入口冒烟和文档收口后暂停供用户审阅。

## D022 — Harness 是用户入口，Codex 是受管理的内部智能中枢

- 日期：2026-09-11
- 状态：accepted_by_user_scope；覆盖 D021 中“外部 Codex 作为入口”的调用关系，不改变其受限
  image exact-reuse 功能范围与真实证据门。
- 决策：用户、前端或 API 只向 Harness 提交任务。Harness 创建 workflow、构造有界上下文、调用
  `CodexBackend`、校验其解释/动作/引用、执行 exact Skills、保存结果、有限重试、恢复和结束任务。
  Codex 负责文本/图像理解、规划、视觉判断、诊断和修改建议，不授予物理有效性或发布资格。
- 实现边界：本阶段继续封装已有无交互 `codex exec`，不迁移模型 API/SDK，不引入通用模型框架。
  exact MCP、wire recorder、Skills、Genesis、CAS、workflow journal 与发布门原样复用；若 Codex 直接
  使用 MCP，只能访问 Harness 分配的 workflow、动作和预算，所有执行仍经同一服务边界校验和记录。
- 恢复边界：Codex timeout、进程退出或无效输出必须在当前任务留下明确失败和重试状态；下一次调用
  从 Harness 的持久 head 重建上下文，不能依赖 Codex 会话记忆，也不能另建平行 workflow/receipt/
  qualification 权威。
- 验收边界：`codex_mcp_acceptance.py`、`codex_mcp_process.py` 与 scripted MCP client 只保留为历史或
  组件验收，不能替代从 Harness 用户入口发起的产品闭环。正常案例和诊断修复案例仍须从该入口
  真实重跑；本决策不声明它们已经通过。
