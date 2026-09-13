# 当前 TODO

更新时间：2026-09-13

## 当前执行焦点（优先于下列历史slice状态）

- wheel-in-checkout来源误判已由真实攻击修复，26项复核通过；固定d5f52d5六组CI进行中，其结果仍绑定旧固定源码不回填。

- 六组/root/merge独立限时CI已接，自身11项通过；固定HEAD后跑真实各组，补全active lint和docslink pending门。

- 唯一Codex transport现显式max；中性真实兼容探针6.323s通过、156项组件测试通过，不声称修复业务timeout。固定下一版本后验证真实case。

- compile公开边界与显式结构值53项通过，118语句/46分支100%；无新仿真通过，整体core仍待六组测量。

- Store+AssetRegistry真实聚焦134项通过、415语句/182分支100%；一次全目录覆盖中断未归因，完整六组/全仓仍待验证。

- C13 孤立旧Store/media/snapshot九模块及专属native已退役，root465项通过；六组CI与独立root测试入口正在实现。

- S01 fixed f35ea18已failed/model_timeout（600.773s），无SceneIR或Genesis；原现场保留，不原样重跑。受管模型诊断与C13/core覆盖并行继续。

- 公共 contracts 239语句/42分支100%，40项相关测试通过；整体core覆盖尚未完成。

- C13 旧资格/catalog/exporter/资源退役，25项主线程安装与边界测试通过；余下孤立Store/media/旧snapshot组继续分类，CI分组随后更新。

- 三Skill执行绑定7项测试、62语句/6分支100%；其余core覆盖继续逐模块补，不扩大外部adapter排除范围。

- C14 安装预览来源身份与拒读分离已实现，25项主线程测试通过；实际安装后GPU执行仍待验，不改变进行中S01固定代码。

- C13 旧资格执行组14个源文件退役，主线程75项通过；资源/schema及孤立深模块继续清理。canonical诊断747项通过但覆盖仍有缺口，不满足最终core100%门。
- S01 在固定 f35ea18 独立树运行：完整来源报告含Box引用，准备先失败后补真实原bytes，未裁报告；终态待收集。

- C14 wheel 补齐 scipy、冻结物理断言与 public schema，10项安装测试通过；installed preview 身份与拒读边界另行验证。

- C11 颜色 child 视觉调用历史审计已补，主线程93项通过；固定源码后启动 S01，不把组件通过算成真实闭环。

- C13 旧 Workbench compile 的 app/static 实际消费者已退役；45 项 demo/import 测试通过，下一步裁剪旧资格深模块与schema/CI，不重签旧资格。
- S01 文本未知资产尺寸的通用设计缺口已补：实际 Registry 几何无需媒体，未知布局仍拒；75 项主线程相关测试通过。真实 S01 仍待新固定源码运行。
- 前置 local color→continuation→compile→完成包历史链已接通组件测试，主线程70项通过；准备固定真实S01前，补text-only资产尺寸派生不应要求图片的通用缺口。
- C13 旧 System 2/GoldenRun/Qwen/未接路由 assessment 独立组已退役；旧 Workbench compile 的 app/static 消费者需先下线，再退旧 qualification/schema/CI。稳定 SAPIEN工具与只读feed保留。
- C13/C14 provider wheel 缺依赖已修复：隔离安装后原 Yuxin 本地检索通过；6 项包装测试通过，不依赖工作区 provider 路径。全 CLI/runtime 安装闭包仍需独立核验。
- C13 G2c qualified replay bootstrap 已撤，旧深模块测试改为直接调用；65 项相关测试通过。旧资格资源/deep consumers 尚待进一步清理。
- local 颜色 Harness→新版本→新视觉→来源续点已接通，主线程 91 项相关测试通过；完成包历史链审计仍在接入。
- S04 fixed d08aaa1 本次 failed/model_timeout（600.57s），未返回提案，未执行分类修复或 web/Genesis；
  原现场保留，继续只读对照超时原因，不追加原样模型调用。
- C13 G2b 已撤历史 compile campaign/独立 Qwen 两脚本，15 项包装与深模块回归通过；其余旧消费者按调用关系继续退役。
- 新 S04 fixed d08aaa1 在隔离工作树运行；一次有界 submit，原输入/seed/web-only 不变，结果待收集。
- 新 S04 fixed427d245 因几何可推导 z 标为 unspecified 再次 blocked（83.07s）；窄分类修复36项通过，
  原失败保留，需固定下一版本验证，不将模型措辞当作成功或手改提案。

- local 颜色 executor 已实现新版本+新预览/原视觉复核（88 项相关测试）；Harness 编排/来源续点/完成门同步中。

- 结构 defaults/on-z 分类与完成门逐轴来源审计均已接通测试；下一步固定新代码执行显式授权的真实案例。

- replay 之后补齐诊断 backend 的续点已接 public resume，保留原回放和时间；52 项相关测试通过，未授真实恢复。

- S04 fixed109b0c5 真实开发运行 blocked/clarification_required（72.51s），未进入web；结构defaults/on-z
  通用分类与精确单轴field处理正在实现，原proposal/失败不变。三次有界transport对照不能确定S02超时根因。

- 本地纯颜色 classifier/managed proposal 和路由 pending 已接通测试；controller 新版本执行及剩余来源续行仍在接线。

- Store/Harness/完成门的修订预留已接通测试；前置 local asset.revise 的独立新版本链仍在实施。

- 显式 uniform_replace 资产颜色修订已通过 51 项测试；local 颜色失配 continuation 与新视觉复核接线中。

- S02 新固定 109b0c5 开发运行 failed/model_timeout（600.514s），尚未进入 grounding/资产/Genesis。
  失败包已保存；当前分流最小 transport 诊断与预算/颜色修订接线，不原样重试。

- partial pose 的 null yaw 已修复；ground+layout 完成链已实现，ground+asset 修订仍拒绝，待独立补齐。

- 持久修订预算在现有 Store 原子操作中实现中；失败/取消必须消费预算，不能只累计成功回执。

- C13 G2a 已撤两个旧 console 安装映射；旧脚本及仍被消费的深模块继续按实际消费者退役。

- 资产锚定生成设计已接 Harness pending→resolve→ground→compile；63 项定向测试通过。
  completion 绑定单独核验中；之后固定新代码、新 key 执行 S02，原失败保持不变。

- 单次 managed 检索词建议已接 deployment 与原 Yuxin 引擎；下一步固定代码执行 S04，不预设搜索成功。

- C13 G1 已完成父入口轻量化及显式消费者迁移；继续 G2–G5，不把这一步写作旧路径全部退役。

- 实施authority保持canonical worktree，用户bingsheng仍未push；持续执行至固定验收/push条件。
- S02真实开发probe已blocked/clarification_required（54.08s），原样证据保留；继续受控生成设计尺度grounding
  与S04web准备，不重试原失败、不宣称4/4或>=8/12已经达到。
- 统一CLI、原Yuxin本地检索、完成门与成功包导出已接通组件；固定新HEAD后执行原样S02真实开发probe。
- 成功包仍development_review/未copy-run；冻结12case、3隔离copy-run、全覆盖/旧active清理、正式资格和push均未完成。
- 真实managed Codex解释已通过；当前controller接线到local resolve→compile→replay→observe→diagnosis→validate；
  下一步有界revise→包与CLI。真实单workflow带资产probe在固定1a0aefa运行，不计后续接线真实通过。
- 资产规范化/Registry v2、Yuxin search/fetch、Gujie外部adapter、Genesis运行内核、属性修订均有组件，
  但不等于完整workflow/矩阵通过；真实证据与每项缺口见RESULTS最新条目。
- 子agent独占：统一deployment/CLI；成功包物化completion模块。主线程管controller/时限/合同/集成。
- 三来源resolver已提交；失败包和请求绑定已接公共Harness，成功包、严格总时限及真实矩阵仍待验收。
- 媒体派生发布授权已异步确认请求；等待期间不停止其他实施，不擅自赋予CC0/CC-BY。

- 最新C05：真实Codex strict transport已通过；第二次SceneIR因非法frame/category拒绝，未计S01成功。
  正在修复部分轴未知的表达和可审计校验错误；下一步固定新代码重跑解释，再接资产解析。

## Doing：C03 类型化能力接线；C07 替代后端并行推进

- 当前 authority：`codex/canonical-x2env-consolidation-20260913` 新 worktree；基于已吸收独立用户文档
  的 clean `worktree/bingsheng@46a2718`。C01保护和source intake完成；实际功能吸收尚待C03–C14。
- C02 已冻结机读矩阵、全部物理阈值与新合同，4项契约测试通过；不是runtime资格。
- C03 request/SceneIR/CapabilityRegistry 已逐seam RED→GREEN；当前合并快测29 passed，尚未接运行时。
- C03a已提交`a1c130f`：39tests、新包语句/分支100%；stage类型/descriptor随真实接线继续补齐。
- C04单SQLite handle/幂等与CAS已开始TDD；C05完整媒体ingest并行实现。
- C06a单次Yuxin包搬迁与活跃消费者修复完成，107项相关测试通过；尚未接canonical资产解析器。
- C07替代TRELLIS首次原图新geometry真实成功（45.453s），排除笔/M04/物理仍not_run；继续接Gujie接口。
- C04/C05首个接通slice：真实ingest→同workflow journal→managed Codex advisory adapter；71小测试通过。
  当前新增包综合覆盖率约91%，未达最终100%门。真实模型调用随后从Harness执行，不预填建议。
  clarification提交、全pipeline成功原子门、孤立模型child恢复和全部stage descriptors仍待补齐。
- Gujie 替代后端 TRELLIS 在外部隔离环境准备，保留原Hunyuan接口非默认；不是新geometry成功。
- 下列 C01 暂停/待清理描述仅为历史过程，不再作为执行指令。

- 用户补充批准先做替代、保留 Gujie 原接口供联调，并持续执行计划；可解决的单项阻断不再暂停全局。
- 主会话负责用户 dirty hunk 归属、clean canonical 基线和 C02；Nietzsche 负责剩余 archive/ref disposition，
  Bacon 使用 research 核对替代后端/版本/完整许可/资源，文件独占。不改冻结矩阵或降低 push 门。
- 以下前次停止记录保留历史含义，已由本补充恢复执行。

- Q01–Q64 已写入
  [`CANONICAL_X2ENV_MERGE_DECISIONS.md`](CANONICAL_X2ENV_MERGE_DECISIONS.md)，不再只依赖聊天上下文。
- 完整计划已生成：
  [`CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md`](CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md)。
  它固定一个 Harness/CLI、三个 Skills、Tools、C00–C15 提交序列和已批准的 4+4+4 matrix。
- 用户于 2026-09-13 批准开始实现，matrix v1 已冻结；当前登记批准并执行 fetch 后全分支盘点、
  dirty-byte 保护。之后才进入 TDD consolidation、真实 qualification 和
  `origin/worktree/bingsheng` push；push 后等待用户批准 main PR。
- Dashboard 的 portfolio/tasks/project 三个入口本次仍返回 HTTP 404，没有 task id，不伪造同步。
- 文件所有权：主会话负责 C00/C01 记录和 Git 保护；Mill 只读分类 dirty 改动，Bacon 只读核对 Gujie
  reconstruction seam/资源。两名子 agent 不修改任何工作树，不启动昂贵运行。
- C00 已提交 `6e88985`。92 refs/87 worktrees 已冻结，bingsheng 普通 16 文件与 OpenReal2Sim 的
  1 文件已保存 local Git archive 并在新 worktree 恢复逐字核验；原 HEAD/index/status 不变。
- C01 尚欠其他 dirty 工作树 archive、逐 ref 最终 disposition、混合文档 hunk 拆分和 clean 基线；
  没有清理用户主目录或创建 canonical 分支。详见
  [C01 记录](CANONICAL_C01_INTAKE_20260913.md)。
- Gujie 固定 Hunyuan 后端的自定义条款涉及输出用途/地域，缺少本项目使用/发布的明确依据；
  替代后端所需完整依赖/权重未在已查目录找到。按计划 §17.3 停止新增实施并请求用户方向，
  不安装、不重建、不 qualification、不 push。详见 [C07 阻断](CANONICAL_C07_RESOURCE_BLOCKER_20260913.md)。
- 三名子 agent 的只读核对已结束，没有后台实现继续推进。

## 用户验收完成：开放柜子与粉红鼠标

- 原样提交用户 prompt“在桌上生成一个打开的柜子，柜子上放着粉红色的鼠标”，使用新的
  development workflow 和真实内部 Codex；没有改写为单物体 prompt。
- workflow `14fb808f-da9c-4354-8390-00b9d18dafb1`在acquire失败：模型把请求压成单cabinet，
  遗漏鼠标/粉红色/开放状态/层级支撑；web没有cabinet。未进入Genesis，正确地没有图片或视频。
- 完整结果和哈希见`docs/evidence/user-open-cabinet-pink-mouse-20260913.md`；后续是否扩展多对象
  SceneIntent与通用重建由用户审阅后决定，本轮不自动改变实现。
- Dashboard 三入口本次仍为 HTTP 404，无 task id，不伪造状态同步。

## Review（本轮实验版本暂停供审阅）

- 当前收口：固定536496e的image/local、centered video/generated与multimodal资产fallback B真实通过；
  加上既有text/generated、text/web、fallback A，模态与来源小案例均有成功证据。详见RESULTS顶部。
- 旧image类别失配与offset视频repair_budget_exhausted原样保留；不以新简单案例洗掉失败。
- 最终限定测试205 passed/4 skipped/1旧资格hash拒绝；161 schema通过，限定综合覆盖率72.98%。
- 最新完整视频包额外隔离copy-run通过：1000步、41帧、66.166572秒；walkthrough已整合。
- 本轮实验验收完成，暂停供用户审阅；不是长期目标全部完成，不进入正式release。

### 历史推进快照（以下在途描述由顶部结果覆盖，不再作为待运行指令）

- 已整合通用输入、三来源资产、共享意图编译、portable loader、开发execution身份、结构化Codex建议、
  stage handlers和evidence policy；当前主会话接同一Golden service/controller，尚无新三模态E2E成功。
- 资产并发登记、Y-up规范化、cousin明确拒绝、颜色不匹配拒绝及qualified receipt实例重验已修复。
- 首文本7stage、34/34和新包隔离copy-run已真实通过，证据见RESULTS顶部；父snapshot仍active。
- 当前固定e2391bd并行运行image-only/local和真实support margin失败→场景修订A候选。
  下一验证video-only、web完整线、资产修订B；旧normal/repair不恢复，release和大矩阵继续后置。

- 2026-09-12 新接手已核对：集成 HEAD `9460a4b` clean，主目录用户改动保留，未发现旧 preview
  执行进程；dashboard 三入口仍 404，未同步。当前进入通用实验管线实现。
- 文件所有权：主会话负责统一输入/共享意图、现有 controller/service 接线、整合与验收；独立
  `generic-development-execution-20260912` 负责同一 Golden 的显式开发执行身份；
  `generic-assets-20260912` 负责来源 adapter/不可变版本；`generic-portable-loader` 负责包入口与 copy-run。
- 先完成小型公共 seam 测试，再记录各分支真实调用。旧 normal/repair、release 和 runtime 大闭包不重跑。

- 历史交接会话只执行安全停止与交接；当前继续通用实验实现，不启动旧 repair 验收、深验、bootstrap、资格重建或 release 发布。
  接手 prompt 是
  [`HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md`](HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md)。
- 下一会话 active：通用实验管线实际接线。顺序为三模态统一输入/共享场景意图 → local/web/generated
  资产来源 → 规范化与不可变版本登记 → 复用 Genesis compile/replay/observe/validate → 场景/布局与
  资产版本两类有界 fallback → 可迁移包 → 少量真实案例和 Harness 实验入口。
- 每种模态至少一个真实成功案例，每种资产来源至少一个真实成功案例，允许交叉覆盖。外部资源缺失
  时要标为 `受阻` 并列资源；不得以 adapter、mock 或固定回执冒充真实通过。
- 实现基线为 `cc6afa236579f2360d808be68ae85e94fcfd81d8`；新会话应读取包含交接文档的实际 branch
  `HEAD`。当前集成 worktree 在本次文档提交前只有交接文档改动。

## Paused（保留，当前不启动）

- 单鼠标 normal+repair 的正式收口：normal 已真实通过开发线；repair 在首次 validate 中断，保留现场，
  不自动恢复；不创建新 release deployment，不把开发产物写入正式库。
- `69aa6c2` 的旧资格与历史证据继续有效且只属于该固定版本，不作为新实现的资格。
- 正式 release qualification、全矩阵稳定率、162 份历史 ledger 清理、autoresearch、大规模性能优化、
  robot policy/data collection、通用插件化和无关前端。

## 最新交接事实

- normal workflow `2eaecc85-f73c-4c34-9646-8761d8744ba6`：真实 Codex/MCP/Genesis，父 workflow
  `validated_unreleased`，75-member 包、1 图、2 段连续视频、双时间步 34/34；总 wall 约 3107 秒，
  正式库未改。
- repair workflow `b57b603e-bf60-4f23-a9b6-1023c5fcbf38`：compile/replay 已提交 revision 1/2；
  validate child 仅有 running preflight，父仍 active revision 2。所有所属进程已退出，未手改 SQLite。
- development record 仍为 `exact_skills_qualified=true`、
  `changed_exact_skill_closure_allowed=false`。下一会话若要调试改动后的 core Skill，应做最小开发契约
  调整，继续禁止开发结果冒充 release。

## M1 收口结果

- `1cf8908` 的 attempt-2 完整真实线 1 passed / 1610.72s，summary `46f21368…`；四个
  registered 终态、34 项物理/媒体检查和集中验收通过。当前 126 schema，focused 162 passed、
  4 skipped、8 deselected。新 core 覆盖率仍有缺口，不称 100%。
- attempt-1 旧 observe v2 在 314.40631s 拒绝过期帧，失败终态不改；v3 独立恢复末态、零速度、
  零物理步的新采集修复交付路径。attempt-2 交付时 TTL 剩余 34.50027s。
- 第二 CAS 新进程只复验 4,115 个输出证据对象 / 1,970,263,053 bytes；不含 runtime member
  payload、部署根或应用 SQLite，不称完整 runtime relocation。详细证据见 `RESULTS.md` 顶部。

## 历史切片待办快照（已由上述 M1 收口覆盖的状态不再作为 active）

- M1：Gujie 场景适配与软件双 dt 实跑已形成切片；继续 image native compile、固定场景执行
  authority/持久应用和新 renderer runtime lock。证据见 `golden-gujie-scene-software-20260911.md`。

- 用户已批准M1–M5，并指定Gujie接入审计为重要整合依据。当前主目标M1：image exact-reuse完整
  Genesis环境线；三agent分别实现Gujie场景/布局适配、环境replay/validate证据、真实CPU场景与
  媒体。先table+ground+mouse tracer再接生产compile，不能以手工scene独立运行替代最终M1。
  P8/E3后置，M5服务开放后暂停。新计划见`SIM_READY_RELEASE_PLAN.md`。

- 当前功能目标收口：`fd50576`固定CPU execution已在两部署真实通过，`5ceead3`记录其集中验收和
  失败材料；`5f78e62`durable单资产qualification已真实签发、executed恢复、第二CAS/SQLite无Genesis
  纯读通过。资格CAS坏字节与真实executed并发恢复也通过；新core覆盖尚有缺口，不能称全部100%。
  固定归档全套4,297pass/26skip/2旧计数fail；两处已修，相关文件14pass，原全套日志保留。
  后续production image compile/Registry/MCP仍须独立Skill资格门，不从资产资格直接推导。

- Runtime dependency lock：早期`3662ba6`的声明清单已由`fd50576`固定归档执行闭合；两套部署实际
  运行、native别名精确校验与受约束child通过。`5f78e62`已完成durable单资产签发。剩余100%覆盖
  缺口逐条记录，不扩张为production Skill资格；dashboard/ClawCross读取HTTP404的外部阻断仍保留。

- X2 exact-reuse image compile：controlled candidate handler 与 v2 input-bound receipt 已完成；当前进入
  production qualification。Gujie 指定 exact pass 输出现已缺失；最接近的 legacy 鼠标归档虽有 15-member
  URDF bytes，却有 stale absolute locators、current threshold fail 和 0/7 runtime source identity mismatch。
  必须由当前 sealed issuer 重跑 asset-scoped Genesis load/collision probe，不得迁移旧 pass 状态。
  15-member bytes 已以 stable snapshot 迁为 unqualified representation；typed raw-load/contact-row/probe
  schema、无相机 CPU producer/deep verifier 和 fresh 1,000-step Genesis run 已完成，并从第二绝对 CAS
  路径在无 Genesis/source root 的进程中复验。之后封闭 runtime dependency lock并签发单资产
  asset qualification；该单资产门现由`5f78e62`真实签发和第二CAS恢复通过，production image Skill
  application/execution binding、Registry descriptor和exact MCP仍是下一步。
- P10 fresh observation：strict schema、controlled candidate application/evidence policy 与 test-controlled
  Golden execution binding 已在同一 revision 2→3 中闭合 receipt/state/restart；下一切片是 production
  runtime binding。当前受控路径固定 `simulator_executed=false`，真实 Genesis capture、qualification 与
  MCP exposure 尚未开始。
- P10 diagnosis/prompt revision：durable resource-access receipt、两个 exact MCP control tools、受控
  external Codex 回合和下一次受控 exact image compile 已闭合。image/video compile v2 input 强制消费
  prompt revision、effective prompt、失败 receipt、predecessor package 与选定 fallback frame；下一切片
  实现 production image/video handler/qualification。不得静默选择 latest/fallback，也不得把 server 已交付
  bytes 夸成模型理解。

## 历史 Next（按 M1→M5 新顺序重新选择，不直接作为执行队列）

- X2：收口新core覆盖与资格最终测试后，为exact-reuse image compile实现production application和
  execution binding，再做冻结Registry descriptor/Skill qualification与exact MCP投影。单资产资格
  已有真实证据，但controlled handler/application仍未注册，`simulator_ready=false`；人审媒体仍须
  单独生成，不能让渲染替代物理资格。
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

- legacy mouse representation staging 完成：严格验证 manifest + 15-member closure 后先冻结 source snapshot，
  再写 16-object fresh CAS；relocated/retry确定性与 verify→put 四类 source mutation均通过。只生成
  `AssetRepresentationV1`，不生成 runtime lock/qualification。

- Genesis asset-probe evidence 与真实 CPU runner 完成：strict self-hashed input/raw-load/load/contact-row/
  contact-trace/report，固定 CPU Newton 0.004s、1,000 steps、10 mm release。fresh mouse run 为 1,001 rows、
  989 contact steps、4,040 contacts、最大穿透 0.487 mm/settled，并通过 relocated CAS deep verify；93 schema
  snapshots。尚无完整 runtime dependency lock、production issuer/qualification。

- controlled durable image compile application 完成：未绑定时只运行 unregistered candidate；真实 succeeded
  candidate 的 Invocation/output/closure 被写入明确 controlled report 后，application 会从同一 CAS/SQLite
  重验并才允许 caller UUID4 invoke。同实例与无 live application 重开均返回同一 terminal；输入 identity
  漂移和 intent-only pending 在 handler 前拒绝；绑定时逐一重验 CAS artifacts 与完整 compile deep closure。
  qualification同时强制 exact CAS URI/name/media/schema；新模块 166 statements/46 branches 100%，不进入
  production Registry/MCP。

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
