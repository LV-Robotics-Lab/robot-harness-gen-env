# Canonical x2env 合并计划：澄清决策检查点

- 日期：2026-09-13
- 状态：`accepted_matrix_v1_frozen`
- 目标分支：`worktree/bingsheng`
- 当前规划来源：`codex/golden-diagnosis-20260911`
- 计数口径：Q01–Q64 是从本轮对话逐项确认的“澄清结论单元”，用于压缩上下文后稳定引用；
  它们不是对聊天问题的逐字转录。
- 执行边界：本文只固定计划输入，不表示对应代码、测试、资格或真实运行已经完成。

## 已确认决策（Q01–Q64）

### 架构、信任与能力边界

1. **Q01 — 校验只保留在权威边界。** 用户输入、Codex proposal、外部资产导入、跨进程或
   CAS/SQLite 持久化、最终 package/release 是校验边界；一旦数据成为已校验的类型化对象，内部模块
   彼此信任，不重复做 schema、hash 或 qualification 深验。证据完整性、fresh observation 和发布门
   继续保留。
2. **Q02 — 三个 canonical public Skills。** 活跃默认路径只公开
   `x2env.compile`、`x2env.replay`、`x2env.validate`。text、image、video 及其组合先汇入共享
   SceneIntent/SceneIR，再走同一组 Skills，不建立按模态复制的九套路径。
3. **Q03 — supporting Tools 数量不硬编码。** 初始内部工具为 `asset.resolve`、`observe`、`revise`；
   只有出现独立能力和清晰责任边界时才增加 Tool，不为凑“3×3”数量创建抽象。
4. **Q04 — 资产解析顺序由 Harness 固定。** 顺序为 Yuxin local exact reuse → verified digital cousin →
   licensed web acquisition → Gujie reconstruction/new generation。Codex只描述语义、几何要求和是否允许
   近似，不任意选择 provider；用户可禁用 cousin/web/reconstruction，但不能绕过许可和完整性检查。
5. **Q05 — 柜子案例必须入矩阵且不能硬编码。** “在桌上生成一个打开的柜子，柜子上放着粉红色的
   鼠标”固定属于四个中等案例之一；通用模型应保留两个实体、粉红色、打开状态和 mouse-on-cabinet
   关系，不允许用该 prompt 的专门分支或固定最终 proposal 实现。它允许按 Q33/Q35 的总体规则失败，
   不是独立 push 硬门。
6. **Q06 — `open` 是真实 articulation。** 打开的柜子必须在 Genesis 中使用有关节、限位和已验证
   初始开角的可动结构；本阶段不要求机器人实际执行开门动作。
7. **Q07 — 案例必须覆盖属性与空间变化。** 固定案例包含 mouse、cabinet、shopping basket、can、
   plate 等类别，并改变 seed、支撑面、颜色、材质/物理属性以及对象间相对或绝对位置；不能依赖单一
   固定桌面居中模板。
8. **Q08 — 活跃命名空间做 breaking migration。** canonical 路径允许不兼容升级；旧
   `experiment.*`、模态专用活跃 Skills、limited-preview execution 和旧 MCP execution 不再出现在活跃
   Registry 或默认路径。
9. **Q09 — 审计所有开发分支，但不机械合并 branch tip。** 每个仍存在的开发分支都要盘点；只吸收
   仍有效且唯一的能力。已 cherry-pick、重复、被替代、仅历史研究或归档用途的分支记录为
   `absorbed`、`superseded` 或 `archived`，不得用 octopus merge 把旧架构重新带回。
10. **Q10 — push 与 main PR 分开授权。** 先清理并验证 `worktree/bingsheng`，然后 push 到
    `origin/worktree/bingsheng`；push 后必须暂停。只有用户再次明确批准，才创建
    `worktree/bingsheng -> main` PR。
11. **Q11 — 100% 覆盖率限定于 canonical core。** unified input、SceneIR、controller、三个主 Skill、
    supporting Tools、CapabilityRegistry 和状态机要求 statement + branch 100%；Gujie、Yuxin、Genesis
    外部 adapter 以集成测试和真实运行证据验收。CI 中纳入的测试必须全绿，不追求整个历史
    `self_improving/harness` 的无意义 100%，也不为已退出范围的 legacy reader 建测试矩阵。
12. **Q12 — 最终冻结后签发一次 qualification。** qualification 绑定最终 Git HEAD、canonical
    Skills、Registry、schema digest 和部署身份；默认入口使用该 snapshot。源码/schema 漂移必须重新
    qualification，内部调用不重复资格深验。
13. **Q13 — Gujie 真实重建是 push 硬门。** 必须调用固定 committed Gujie/SimFoundry 实现，生成
    本地库中原本不存在的新资产或场景，并完成真实 Genesis replay/validate。缺模型、凭据或运行依赖
    时只能标记 blocker，不能用 adapter-only 结果冒充；secret 只走环境，不进入 Git、日志或 package。
14. **Q14 — 三种资产来源必须真实通过。** local exact、licensed web、Gujie reconstruction 三条来源
    均须至少一个真实成功案例，并验证至少一次来源 fallback。digital cousin 保留在选择顺序和合同中，
    但本轮允许未验证，不作为 push 阻断。
15. **Q15 — SceneIR 的本阶段边界。** 最多 8 个实体；实体字段包含 category、color、dimensions、
    material、pose、articulation_state；关系包含 on、inside、left_of、right_of、front_of、behind、near；
    坐标可为 world 或相对命名 support/container。未知关键属性不得静默丢弃，必须 unsupported 或请求
    clarification。本阶段不引入任意自然语言属性或 Robot Task IR。
16. **Q16 — canonical runtime 只面向 Genesis。** 新 x2env 默认只生成 Genesis 环境；稳定 `/gen-env`
    的 RoboTwin/SAPIEN 能力作为独立兼容模块保留，不再作为默认 workflow。未来 simulator 只能接在
    SceneIR → simulator adapter seam。
17. **Q17 — 一个 Python API 和一个薄 CLI。** canonical 接口为
    `Harness.submit(X2EnvRequest) -> WorkflowHandle`；request 支持 text、最多 8 张 images、可选 video、
    seed、allowed sources 和 constraints。CLI 只包装同一 Python 接口；本阶段不新增 REST、HTTP 或前端。
18. **Q18 — 前台默认运行但先持久化。** `submit` 默认阻塞到终态，但必须在调用 Codex 或 Genesis 前
    持久化 handle；同一 Harness 提供 `status(handle)`、`resume(handle, ...)`、`package(handle)`，从最后
    committed operation 恢复，不引入后台队列。
19. **Q19 — workflow 只有五种状态。** `active`、`succeeded`、`failed`、`blocked`、`cancelled`；
    `succeeded` 表示最新 revision 已 replay/validate 通过且 package 已物化并可读。qualification/publish
    是 package/Registry metadata，不再作为并行 workflow 状态。
20. **Q20 — 只有 canonical capability 可注册为 active。** 活跃 Registry 只含当前 x2env Skills/Tools；
    不保留 canonical runtime 可调用的 legacy reader/writer，旧 runtime 不进入默认路径。历史 schema、
    receipt 和 package 只通过原始证据文件与 Git history 供人工追溯。
21. **Q21 — 用干净 consolidation history 代替 355 个历史提交的机械合并。** 可以基于清理后的
    `bingsheng` 建 canonical consolidation 分支，将有效代码重整为小型逐功能提交；provenance ledger
    逐项记录来源 branch/SHA/author、adopt/rewrite 决定。验证和远端 push 前不删除旧分支。
22. **Q22 — 先逐字保护 dirty bingsheng。** 普通文件先进入保护分支/提交；OpenReal2Sim 子模块内部
    dirty 状态单独固定。相关有效改动纳入 canonical commits；有价值但无关的改动成为独立提交；只有
    在证据证明重复/过时且用户明确确认后才排除，不做 blind WIP add、reset 或丢弃。
23. **Q23 — 文档以中文为主并与类型源同步。** 交付 quickstart/真实案例、Python API/CLI、SceneIR、
    Skills/Tools 生命周期、Gujie/Yuxin 接入、测试/qualification、package load/recovery、migration、
    roadmap 和完整 walkthrough。标识符、schema、命令保持英文；API 字段表从类型模型生成，不手抄
    第二份 schema；不做独立文档网站。
24. **Q24 — 两层验证门。** 普通 CI 跑全部 unit/contract/migration、canonical core 100% 覆盖、
    schema/docs/diff/lint；pre-push 在受控主机跑 12 个固定案例、三种必要资产来源、fallback/recovery、
    copy-run 和最终 HEAD qualification。远端 CI/PR 只核对已签 report 的 HEAD/schema digest，不重跑
    私有昂贵模型；未来可迁到 self-hosted runner。
25. **Q25 — 每个成功例有完整包，三个成功代表例做隔离 copy-run。** 成功案例生成 environment
    package 并检查成员 hash 与相对路径；失败案例按 Q53 生成 failure evidence bundle。copy-run 固定覆盖
    Yuxin local reuse、Gujie reconstruction 以及最丰富的另一个成功多对象/多模态案例；若柜子案例成功，
    优先选择它，但柜子失败不会额外新增一条独立门。在拒绝读取原 workspace/CAS/assets 的条件下重新
    load/step 并产生新图片和连续视频。
26. **Q26 — 单次核验硬上限 30 分钟。** 每个 case、copy-run、test group 或 qualification shard 都在
    30 分钟内；超时安全中断，保留 partial evidence 并标记 `timed_out`，在出现大幅代码变更、明确性能
    优化或用户另行指令前不重跑。整个 12-case suite 可由多个 shard 构成，总 wall 可超过 30 分钟。
27. **Q27 — 歧义输入进入可恢复 blocked。** 信息冲突或关键字段可能丢失时，workflow 返回结构化
    `clarification_questions` 和 agent 生成的人类友好错误；用户以 immutable input revision 在同一
    handle 上 `resume(..., clarification=...)`。确定性错误为 failed，外部资源缺失为 blocked。
28. **Q28 — CodexBackend 是必需依赖。** 不回退到旧 parser/planner/VLM 或人工预填 proposal；Codex
    不可用时 workflow 为 blocked 并可恢复。单元测试可替换接口，正式 qualification 必须调用真实 Codex。
29. **Q29 — 类型模型是 active schema 唯一编辑源。** JSON Schema、Registry descriptor 和 API 文档从
    Python typed models 生成；active schema 目录只保留 canonical x2env，旧版本不复制到新的 legacy
    schema 区，只留在 Git history/既有证据中。breaking/additive/fix 分别升级 SemVer
    major/minor/patch，Registry 固定精确版本和 schema digest。
30. **Q30 — 外部实现保持原所有权。** Gujie/SimFoundry 继续是 pinned submodule 或可安装 package，
    本仓库只拥有薄 adapter/type transform，不复制源码；Yuxin 资产复用抽成唯一 `asset.resolve` provider，
    保留作者、上游、集成者和 runtime evidence 的独立 provenance。
31. **Q31 — 活跃代码以用途和 call graph 决定去留。** 被 canonical 路径实际调用或拥有唯一职责的实现
    留下；完全被替代、重复或没有消费者的实现退出活跃树。三个历史 heavyweight CLI 必须变薄、拆入
    模块或退役，不能保留重复 orchestration。

### Golden matrix、修订与恢复

32. **Q32 — 固定 4+4+4 matrix。** qualification 前冻结 4 个简单、4 个中等、4 个困难案例及其
    prompt、seed、输入媒体和 assertions；冻结后不得通过调整输入来洗掉失败。
33. **Q33 — 简单案例必须 4/4。** 任一简单案例失败即未达到 push 门，不能由更难案例的成功抵消。
34. **Q34 — 柜子＋粉红鼠标不是独立硬门。** 它固定留在中等组并如实计分；只要简单 4/4、总分
    `>=8/12` 以及其他跨维度硬门均满足，该案例失败仍可验收和 push，但必须保留完整 failure bundle。
35. **Q35 — 总体阈值为 `>= 8/12`。** 其余中等和困难案例允许失败，但失败必须有终态、结构化原因、
    日志和已完成产物；不得用单一百分比遮蔽分支差异。
36. **Q36 — 难度和固定 matrix 不可事后改写。** 探索性随机 seed 案例与正式 matrix 分账且非阻断；
    同一 idempotency key 返回同一 workflow/result。seed 固定布局、重建和 simulator 随机源；fresh
    workflow 只要求语义/物理可复现，不要求 Codex、mesh 或视频逐字节一致。每次运行记录模型/provider
    版本、seed、SceneIR、资产和输出 hash；最终冻结 HEAD 的 run 禁止人工中途修改。
37. **Q37 — 资产修订生成不可变 child version。** local exact geometry 可复用；颜色、材质或允许的
    物理变化由 `revise` 创建新版本，绝不修改原版本，并分别记录 `parent_version`、geometry provenance
    和 attribute provenance。
38. **Q38 — `revise` 使用白名单。** visual 仅允许 base_color、roughness、metallic；physical 仅允许
    uniform_scale、mass/density、friction、restitution。位置、yaw、joint state 属于 SceneRevision；mesh
    topology、collision、URDF 结构或 joint 修改必须进入 Gujie reconstruction/normalization。
39. **Q39 — 每个 workflow 最多两次自动修订。** scene 与 asset 共用总预算 2，每个 revision 只允许一次
    recompile/replay；相同 error code + SceneIR/asset version + evidence summary 重复即停止。每个 provider
    source 最多尝试一次，provider 尝试不占修订预算。
40. **Q40 — Codex 故障时错误信息不递归调用 Codex。** 正常失败由 agent 生成面向人的解释并同时返回
    machine code/evidence；若 CodexBackend 本身不可用，Harness 使用确定性本地化模板，避免为解释失败
    再次依赖已失败的模型。
41. **Q41 — 归档不复制废弃源码。** stable `scene_gen` 中仍被复用的 parser/solver/builder/validator 和
    `/gen-env` 保留；旧 planner、独立 VLM、limited-preview orchestration、旧 MCP/rule-based intelligent
    fallback 若完全被替代，则从 active tree 删除。Git history、固定 SHA 和 provenance ledger 就是归档；
    active tree 不保留 legacy reader，也不另建大体积 `archive/` 副本。

### Registry、持久化、输入合并与交付

42. **Q42 — 两个深模块 Registry。** `CapabilityRegistry` 管 Skills/Tools/version/schema/adapter；
    `AssetRegistry` 管不可变资产版本/source/license/parent/Genesis representation。workflow state 属于
    journal，package 独立；不做一个无边界的 universal registry。
43. **Q43 — 每 deployment 一套 SQLite + CAS。** 使用 `state/harness.sqlite` 和 `state/cas/`；SQLite
    分表保存 workflow、operation、capability snapshot、asset index，大型 artifact/schema/receipt/
    SceneIR/assets/media 存同一 CAS。停止向 `golden.sqlite`、`children.sqlite`、`runs.sqlite` 新写数据，
    旧库只读。
44. **Q44 — 新 Harness 从 clean DB 开始。** 只导入重新验证的 active assets、需要长期保留的最终
    package index、provenance/qualification summary；不把旧 workflow 或被中断 repair 改写进新库。
    legacy DB 文件保持 immutable 供人工/历史工具取证，但 canonical Harness 不承诺读取或恢复。
45. **Q45 — 多模态按字段记录来源，不设笼统优先级。** 每个 SceneIR 关键字段记录 text/image/video
    provenance；只有文本明确要求忽略某项参考媒体信息时才覆盖。未声明的冲突返回 blocked clarification，
    不静默选择 text 或 media。
46. **Q46 — 本次 `sim-ready` 的精确定义。** 必须真实证明 Genesis load、reset、step、observation、
    contact、stability 和连续媒体；包含 articulation 的成功场景还必须验证 joint/limits/state。预留
    robot/task adapter seam，但明确
    `robot_policy_evaluated=false`、`data_collection_evaluated=false`，实际策略与采集列入 roadmap。
47. **Q47 — 默认 Harness 不依赖 MCP runtime。** Codex 只返回结构化 SceneIntent/诊断建议，controller
    自己选择并执行 Skills/Tools；CapabilityRegistry + ToolResult 提供版本和审计。旧 MCP 归档；只有未来
    出现真实外部 consumer 时才增加 canonical MCP projection。
48. **Q48 — 四类输入形态都须至少一个真实成功。** 固定 matrix 中 text-only、image-only、video-only、
    multimodal 各至少一个成功，不能用八个 text-only 成功满足总体分；它们仍共享同一 canonical 路径。
49. **Q49 — 大型证据放持久制品存储。** Git 只提交小型报告、索引、hash 和精选图片；完整 Genesis
    package、MP4 和完整运行日志进入持久 artifact store，并由文档链接。push 前必须验证远端制品可下载
    且与 committed digest 一致；不得提交 bulk runtime output。
50. **Q50 — 制品后端和路径约定。** 使用 S3-compatible object store，以不可变 Git commit SHA 和
    workflow ID 作为路径身份；每个 run 的 manifest 必须同时记录 canonical object URI、package-relative
    path、media type、byte size 和 SHA-256。建议根布局为
    `s3://<bucket>/robot-harness-gen-env/<git-sha>/<workflow-id>/`，其下明确分为 `inputs/`、
    `assets/<asset-id>/<version>/`、`scene/`、`media/images/`、`media/videos/`、`logs/`、`reports/`、
    `package/` 和根 `manifest.json`。文档引用稳定 object URI；临时 signed URL 只用于下载，不作为身份。
    endpoint/bucket 等非 secret 配置进入 deployment；凭据仅由环境或 secret manager 提供。
51. **Q51 — 当前没有已确认的 S3 部署信息。** 用户不知道是否已有可用 endpoint/bucket，因此计划、
    代码和验收不得假设隐藏的 S3 基础设施或凭据存在。Q50 的路径与 manifest 合同继续作为可移植
    object-store adapter 目标；本轮必须另选一个当前可获得且能持久下载的默认后端，或把缺失资源明确
    设为 push blocker。
52. **Q52 — 本阶段默认使用 GitHub Release assets。** 完整资产、图片、视频、日志和环境包上传到当前
    GitHub 仓库的 Release assets，并绑定不可变 qualification tag/commit；manifest 保存固定下载路径、
    byte size 和 SHA-256。实现继续以 artifact-store port 隔离后端并保留 S3-compatible adapter seam，
    不使用 Git LFS 或有短期过期语义的 Actions artifacts 代替持久证据。
53. **Q53 — 成功包与失败证据包分流。** 成功案例必须产生可加载、可重放、已验证的 sim-ready
    environment package；允许失败的中等/困难案例必须产生 immutable failure evidence bundle，至少包含
    原始输入及 digest、最后可信 SceneIR/修订、已取得或生成的部分资产、阶段日志、workflow 终态、
    machine-readable error、agent 生成的人类说明和完整 manifest/hash。失败不得创建或命名一个假的
    sim-ready environment package。
54. **Q54 — 以冻结 branch manifest 定义“所有当前开发分支”。** 实施开始时先同步远端，再生成带
    时间戳、ref、commit、worktree path 和 dirty-state digest 的不可变 manifest；范围覆盖本仓库当时的
    所有本地分支、关联 worktree 和 `origin/*` 开发分支。冻结后新出现的分支不自动扩张本计划，除非
    用户明确加入。Gujie/Yuxin 等外部仓库只审计 provenance 中固定的来源提交，不扫描其全部分支。
55. **Q55 — GitHub 制品使用证据专用 prerelease。** tag/release 名使用
    `x2env-evidence-<commit>` 一类不可变标识，继承仓库现有访问权限；release notes 和 manifest 必须
    明确写明“qualification evidence only”，不表示 main 已合并、产品已正式发布或未验证能力已授予。
56. **Q56 — 不投入时间修复旧 qualification 红测。** 已漂移的历史 snapshot/hash 不更新、不重签、
    不为其新增兼容性测试，也不继续阻断 canonical CI；对应 fixture/test/qualification path 退出 active
    suite 并按 Q31/Q41 归档或删除。新路径只对最终冻结 canonical HEAD 签发新的 qualification。
57. **Q57 — 撤销 canonical legacy-reader 要求。** 为缩短实现、迁移和测试时间，新 Harness 不读取旧
    SQLite、schema、receipt 或 package，也不提供旧 workflow resume。旧 bytes、固定 SHA、运行证据和
    Git history 原样保留供人工审计；若未来确有业务需求，再作为独立迁移项目重新授权，不预先建设。
58. **Q58 — Yuxin provider 管线是唯一 canonical 联网检索内核。** 复用
    `1_asset_reuse/configs/providers.json`、`lib/a1_providers.py`、selection、webfetch、Objaverse 和 ledger
    的有效职责，由新的薄 typed adapter 接入 `asset.resolve`；不新增重复 Poly Haven implementation，
    不通过 heavyweight script/subprocess 调用，也不复制粘贴 provider 代码。canonical 路径强制 license
    gate；候选许可不明确或验证失败时继续现有 tier walk，全部耗尽后才调用 Gujie reconstruction。当前
    `experiment_assets._web()` 的 Khronos-only exact-filename 路径退出 active tree。
59. **Q59 — 许可策略取最小安全且实用集合。** canonical web acquisition 首批自动接受
    `CC0-1.0` 与 `CC-BY-4.0`；CC-BY 自动把作者、来源页和许可声明写入 AssetRegistry、package 与 evidence
    manifest。unknown、NC、ND、SA 和自定义/NVIDIA terms 本阶段不投入额外许可解析，候选被拒绝并继续
    provider/fallback。未来按真实资产需求以独立变更扩充，不预建通用许可引擎。
60. **Q60 — 禁止 cabinet-specific generator。** 即使包装成参数化 API，为该中等案例专门实现柜子
    几何仍属于硬编码，不允许作为 fallback。打开柜子只能由真正通用的 local/web 资产检索或 Gujie
    通用重建得到；若成功，必须通过 articulation 与 Genesis 门；若失败，按正常中等案例计分并诚实
    返回 failure bundle。失败需定位是 intent、检索、重建、articulation、compile、replay 还是 validate
    问题；既定边界内、低风险且能在预算内完成的修复可以继续，需要新增外部依赖、扩张架构、绕过
    门禁或明显超出本阶段的修复则暂停并请求用户批准。只要 Q33/Q35 及其他跨维度硬门满足，柜子失败
    本身不阻止 push。
61. **Q61 — 环境包不内嵌大型 runtime。** package 只携带完整场景/资产、loader、
    `runtime.lock.json`、依赖安装与环境检查命令；不复制既有 8.5 GB runtime，也不为本阶段构建大型
    container。用户提供或安装与 lock 匹配的 Python/Genesis 环境，loader 在启动前验证版本、平台和
    必要依赖；不匹配时 fail closed，并返回可直接执行的人类友好修复指引。
62. **Q62 — dirty-byte 第三类无需人工暂停。** 在触碰 `worktree/bingsheng` 前仍先逐字固定文件、diff、
    submodule state 和 hash，并把每项分类为 canonical、独立用户提交或重复/过时。第三类先写入独立
    Git snapshot/ref 并更新 provenance，使内容可恢复，然后可直接从最终 canonical `bingsheng` 排除，
    不再逐项等待用户批准；不得用未落 Git 的临时副本冒充归档，也不得删除该 snapshot 后声称可恢复。
63. **Q63 — 12-case matrix 由计划给出并随计划批准冻结。** 最终计划必须逐例列出 prompt、input media
    digest、seed、允许/预期来源、难度和机器断言；用户批准计划即批准 matrix。首次 qualification 开始后
    不得因结果不佳修改 prompt、媒体、seed、难度或 assertions。只有输入 bytes 损坏等非业务错误可以
    创建新的 matrix version，且必须重跑整套并重新计分，旧结果继续保留。
64. **Q64 — 唯一用户 CLI 为 `x2env`。** `x2env submit` 接收 text、images、video、seed、source
    constraints 和明确 `--output`，默认前台运行到终态，并同时输出人类可读摘要与 `result.json`；二者
    明确列出 workflow ID、状态、SceneIR、资产、图片、视频、日志、environment/failure bundle 及远端
    evidence URI。查询、恢复和重新物化统一使用 `x2env status|resume|package`；它们与 Python Harness
    API 共用同一 controller/journal，旧 CLI 不再作为用户入口或文档默认路径。

## 澄清状态

- Q01–Q64 全部已确认；用户要求的最后五项 Q60–Q64 已逐轮结束。
- 当前没有会改变总体架构、分支策略、用户入口或验收门的已知未决问题。
- 逐文件、逐提交、逐测试和逐真实案例的计划已生成在
  [`CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md`](CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md)。
  用户于 2026-09-13 批准开始实现；本文 Q01–Q64 和计划内 Qualification Matrix v1 一并冻结。

## 计划生成门

只有在尚未决定事项降到不会改变总体架构、分支处理和验收门后，才生成实施计划。计划至少需要包含：

- 分支/worktree 全量盘点和 dirty-byte 保护顺序；
- canonical 模块图、迁移/删除/保留清单和逐功能提交序列；
- TDD、CI、12-case qualification、30 分钟超时和 evidence storage；
- `worktree/bingsheng` 清理、合并、验证、push 以及等待 main PR 批准的明确停止点；
- 使用文档、API 文档、接入文档、walkthrough 与后续 roadmap。
