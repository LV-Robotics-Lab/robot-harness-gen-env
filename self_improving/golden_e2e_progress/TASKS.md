# Golden E2E 任务分解

## 2026-09-13 已批准 canonical 执行清单

- [x] C00：用户批准 Q01–Q64、计划和 Qualification Matrix v1；登记不可降低的 push 门。
- [ ] C01：fetch 后冻结 refs/worktrees，保护并复原验证 dirty bytes，分类形成 clean bingsheng 基线。
- [x] C02：权威 contracts、seams、acceptance、机读矩阵冻结；不代表矩阵执行通过。
- [ ] C03：typed models、生成 schema、CapabilityRegistry。
- [ ] C04：单 Store、workflow 生命周期、Harness public interface。
- [ ] C05：多模态 ingest 与受管理 Codex SceneIR/视觉建议。
- [ ] C06：唯一 Yuxin local/web provider 与 AssetRegistry。
- [ ] C07：固定 Gujie reconstruction-only seam 和真实新 geometry。
- [ ] C08：多实体/支撑图/articulation Genesis compile。
- [ ] C09：真实 replay/fresh observation/独立物理与视觉 validate。
- [ ] C10：共享两次修订预算、A/B/numerics 和恢复。
- [ ] C11：可迁移包、failure bundle、制品 publisher。
- [ ] C12：唯一 x2env CLI。
- [ ] C13：删除被替代 active graph，canonical core 100% statement/branch 与 active CI。
- [ ] C14：完整中文接入文档、walkthrough、provenance。
- [ ] C15：冻结 HEAD 后 12 cases、补充门、三个 copy-run、qualification/prerelease、普通 push。
- [ ] 确认远端 bingsheng SHA 后暂停；main PR 仍需用户另行授权。

细节以已批准实施计划为准；下列实验/旧计划清单只表示其历史版本。

当前子步骤：C03 三 Skill 实际 typed 调用、C05 pending 设计补全、C06 单次搜索建议、C13 G1 父入口
与 G2a console 安装映射均已有独立提交及定向测试。上方阶段保持未勾选的部分仍有完整验收缺口。

## 2026-09-13 通用实验验收清单

- [x] Harness唯一CLI入口与原Golden journal、版本化开发Skill执行（真实文本revision0→7）。
- [x] 文本完整线、真实生成资产、34项物理检查、新鲜Codex视觉与validate。
- [x] 新文本包复制后在禁止原资源访问条件下真实load/1000step/新媒体。
- [x] image-only/local完整线：首试失配保留，修复实际目录上下文后centered案例7阶段通过。
- [x] video-only完整线：旧offset失败保留；536496e centered案例7阶段通过。
- [x] web来源完整线：固定来源Box实际搜索下载、Genesis及validate共7阶段通过。
- [x] fallback A完整线：实际余量失败、Codex修改y和scene bytes，第二轮34/34及validate通过。
- [x] fallback B完整线：536496e真实模型scale=3.5新版本，12阶段/34项检查通过。
- [x] 有限重试停止真实证据：offset视频两次修订后repair_budget_exhausted；完整失败journal保留。
- [x] 完整journal新包输出（image/local 231-member等）；原文本隔离copy-run已通过。
- [x] 最新包额外copy-run、最终测试覆盖说明与walkthrough收口；旧资格测试失败明确记录。
- [x] 最终逐功能提交，暂停供用户审阅；不继续release/历史债务/autoresearch/robot。

## 2026-09-12 active 任务替换

下列原 M2 和 G0–G4 清单保留为历史分解，不再是当前执行队列。当前会话已安全停止单鼠标 repair；
下一 active 改为 text/image/video 统一输入、local/web/generated 资产来源、现有 Genesis 链、两类
有界 fallback 和可迁移包的实际接线。直接执行清单以
[`HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md`](HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md)
和 `TODO.md` 顶部为准；正式 release、历史债务、autoresearch 与 robot policy 继续后置。

## 历史：M1 已完成首条完整线；原 M2 受限预览

- [x] 实际适配 Gujie 单刚体场景、完整几何与有限桌面。
- [x] 真实 baseline/half_dt 场景回放及完整新媒体（研究运行，无生产场景 authority）。
- [x] `1cf8908`：registered image compile → 固定双 dt replay → fresh observe v3 → validate，四 Skill 资格与终态闭合。
- [x] M1 集中功能验收；第二路径输出闭包纯读通过（不包含 runtime member 部署或应用 SQLite 迁移）。
- [ ] M2：用户从 Harness API/CLI 提交原始图像与文本；Harness 先创建可恢复 workflow，再调用内部
  真实 CodexBackend，并驱动 exact compile → Genesis replay → observe → validate。用户不操作 Codex
  CLI/MCP；不得用程序客户端或历史应用关联替代端到端验收。
- [ ] M2：内部真实 Codex 读取本次实际观测并返回有证据的受限布局修改；Harness 校验并消费该决定，
  下一次 compile 必须改变实际位置、方向或允许字段，随后真实 replay / validate。
- [ ] M2：Codex timeout、进程退出或无效决定必须写入同一任务记录，执行有限重试并可从当前 head
  恢复；不能依赖原 Codex 会话保存业务进度。
- [ ] M2：把通过门禁的环境 manifest 发布到隔离预览库，并验证查询、获取、复用、失败留痕与恢复。
- [ ] M2：记录输入/资产、compile、runtime 完整性、仿真/渲染、CAS 深读、Codex/MCP、
  validate/publish 的实测耗时和观测 TTL 余量；先测量再处理重复开销。
- [ ] M2：固定干净集成提交和最小服务入口，完成一次从服务入口发起的真实冒烟后暂停供审阅。
- [ ] 后续三模态、全新资产/digital cousins、历史债务与服务验收；M1 不授予这些能力，P8/E3 后置。

## G0：基线与审计

- [x] 记录 Codex 取代独立 System 2 中枢和独立 VLM 的架构决定。
- [x] 创建持久进度账本并从根 `AGENTS.md` 建立入口。
- [x] 审计 `worktree/gujie` 的 x2env 输入、输出、Genesis 资产/场景格式和可运行证据。
- [x] 审计历史资产债务、可迁移数据、真实 settle 缺口和现有修复工具。
- [x] 审计当前 compile/replay/validate、System 2、MCP、observation、diagnosis 和 promotion seam。
- [x] 建立 Bingsheng、Gujie、Yuxin 模块/功能级工作归属、整合来源与验证状态台账。

## G1：契约与测试 seam

- [x] 形成至少三种 x2env Skill interface 设计并比较 depth、locality 和 seam placement。
- [x] 起草 text/image/video/multimodal 输入归一化合同，等待确认冻结。
- [x] 起草 image2env 与 video2env 的三个 Skill 命名、输入、输出、资格和失败合同，等待确认冻结。
- [x] 起草 Codex external-agent/advisory 与 MCP adapter 合同，等待确认冻结。
- [x] 用户确认 `SEAMS.md` 中的公共测试 seam。
- [x] 用户确认 `AUTORESEARCH_SETUP.md` 的指标、范围、约束和实验预算。
- [x] 落实 image2env/video2env 六个 exact Skill 的严格 Pydantic/JSON Schema、contract-only
  descriptor namespace、Gujie adapter mapping fixtures 和逐链路实施计划；不注册虚假 runtime。
- [x] 冻结 `genesis.observe@1.0.0` 受控纵切合同：TTL `1..300` 闭区间，以及
  receipt → fresh ToolResult → trusted claim → StateDelta 的逐项绑定；不把合同冻结算作 runtime。

## G2：Codex 中枢与 MCP 证据链

- [ ] qualified compile 的 Codex 调用与可信 ToolResult。
- [ ] qualified replay/validate 的同 run 调度。
  - [x] qualified compile → replay 已在同 run 调度，并验证同一 replay bundle 跨代码安装绝对路径复用。
  - [x] replay → validate 已在同一 Golden run 接入；MCP 受控链也已推进到 revision 3。
- [ ] fresh observation 获取及回执绑定。
  - [x] exact Skill、受控证据链、TTL 和历史截图失效模式已冻结。
  - [x] 通过 S1 TDD 实现受控 application/policy/receipt/state 纵切，结果标注
    `simulator_executed=false`。
  - [x] 实现 controlled evidence policy，并把 Invocation 与 exact workflow authorization context 纳入
    首次提交和重启恢复验证；相同 state hash 不允许跨 workflow 混用 receipt。
  - [x] 用显式 test-controlled qualification 把真实 controlled application 接入同一 Golden
    revision 2→3，并验证 CAS/SQLite/no-live restart；该 binding 不进入 production assembly。
  - [ ] 实现 production execution binding/runtime；再使用真实 Genesis sealed checkpoint 完成
    production qualification，之后才加入 production MCP。
- [ ] Codex 诊断、受限修复、状态增量与可续跑 history。
  - [x] 冻结并实现 advisory/prompt revision 两类不可变 workflow control receipt；统一 shared-turn
    authority、mixed receipt reconstruction、CAS reachability 与重启幂等已通过独立验收。
  - [x] control 只推进 revision/turn/head，state 与 child runs 不变；advisory 固定为
    `advisory_only`，不授予 physics/publishability。
  - [x] 增加 durable resource-access receipt 与两个 exact MCP control tools；真实 external Codex 已
    请求 source prompt、失败 receipt、controlled observation 与 diagnostic，并提交 advisory/prompt
    revision。回执只证明 server delivery，且仍保留 `external_agent_executed=false`。
  - [x] 为 image/video compile 冻结 v2 attempt 合同，并让受控 exact image compile 明确消费当前
    prompt revision、effective prompt、失败 receipt、predecessor package 与 video fallback frame；旧 v1
    和六类漂移在 child/CAS/SQLite reservation 前拒绝。
  - [ ] 把同一消费合同接到 production image/video handler、资格和真实 Genesis observation；不得静默
    取 latest 或 fallback。
- [ ] promotion gate 与 publishability 唯一授权。
- [x] 真正 MCP adapter：exact schema discovery、typed invocation、ToolResult、workflow-scoped
  state/receipt/media resources、错误映射与 stdio restart 已实现；真实三 Skill simulator 已验收。
- [ ] 一个 text-only golden case 完成同 run 证据闭包。
  - [x] 官方 MCP stdio Client 已真实完成 legacy RoboTwin/SAPIEN revision 0→1→2→3 和重启幂等。
  - [ ] external Codex planner 仍需只经 exact MCP 完成同一闭环。

## G3：资产债务与 x2env Skills

- [ ] 历史 ledger 迁移/隔离并逐类消除完整性违规。
- [ ] text2env 的生成、精确复用、digital-cousin 复用分别形成 golden line。
- [ ] image2env 三个 Skill 实现、资格和三类资产路径。
  - [x] X1 image ingest：CAS 原图完整解码、metadata-free canonical PNG、strict probe、
    ImageSourceMedia 与纯读 consumer closure；不计作 compile/qualification。
  - [x] X1 video ingest：完整 ordered decoded-frame manifest、全帧计数/摘要、FPS/duration 与纯读
    consumer；受控 decoder seam 与 system-FFmpeg 独立验收不计作 production decoder/qualification。
  - [x] X2 typed exact-reuse evidence：12 份 self-hashed compile records 与纯读 deep verifier；完整
    catalog、qualification/member/check evidence 和四项 static checks 均 fail closed，但不计作 handler、
    production qualification 或 Genesis runtime。
  - [x] X2 controlled exact-reuse image compile：v2 receipt 绑定完整 compile input；candidate handler 先做
    纯读 preflight，再确定性发布 SceneIR/TaskIR/selection/closure/binding/static/package/receipt，并由
    deep verifier postflight 后才返回。该项不计作 production qualification 或 Genesis runtime。
  - [x] controlled durable image compile application：candidate run、controlled report、caller-owned UUID、
    SQLite terminal 与无 live application restart 闭合；不计作 production qualification 或 MCP exposure。
  - [ ] fresh Genesis asset qualification：legacy 鼠标输出只能迁移 15-member representation candidate；
    当前 issuer/runtime lock 与 asset-scoped load/collision probe 必须重跑。
    - [x] 15-member manifest/bytes 经 stable snapshot 写入 fresh CAS，并发布 unqualified
      `AssetRepresentationV1`；绝对路径、权限和 verify→put source mutation 不改变输出。
    - [x] typed probe input/raw-load/load/contact-row/contact-trace/report schema，93份 public snapshots。
    - [x] Harness-owned 无相机 CPU producer/deep verifier 与 fresh real 1,000-step run；第二绝对 CAS
      路径可在无 Genesis/source root 的进程中严格重验。
    - [x] 封闭 Python/Torch/Quadrants/native 完整 runtime dependency lock，再由 durable production
      issuer 签发 `GenesisAssetQualificationV1`。
      `5f78e62`已真实签发/重启/并发恢复和第二CAS验收；只授单资产两checks，不授Skill/MCP权威。
      - [ ] 新core原始statement/branch 100%门；已记录运行环境失效与公开前置不变量遮蔽的剩余分支。
      - [x] 声明根的 canonical CAS runtime manifest：流式 capture、纯读、独立 deployment mapping、
        drift 拒绝与 fresh-process relocation；该前置不等于完整运行闭包或资格。
        已由166,141成员真实部署、第二CAS/绝对映射和固定`3662ba6`独立功能验收确认。
      - [x] 固定 fresh process 的实际 import/native/RECORD/env closure 与 sealed runner binding。
        固定`fd50576`正式Producer已在两部署完成1,000steps、strict reload及集中功能验收；97 schemas。
        真实运行链闭合；launcher/child全分支覆盖尚未完成，不能把此勾选扩大为覆盖或资格门通过。
- [ ] video2env 三个 Skill 实现、资格和三类资产路径。
- [ ] 输出 sim-ready Genesis 环境，并保留可观看图像/视频和机器可验证报告。

## G4：路由与 autoresearch

- [ ] 建立 text/image/video/multimodal 路由基线。
- [ ] 分别统计路由、每个 Skill、每个细节阶段的成功率与失败分类。
- [ ] 逐实验改进并保留 keep/discard/crash 记录。
- [ ] 验证 fallback、prompt 修复、fresh replay 和最终闭环。

## G5：最终验收与交付

- [ ] 三条路径各用多个冻结与随机输入完成全新生成、精确复用、digital cousin、sim-ready 验证。
- [ ] 统一 System 2/Codex 路由综合测试达到冻结门限。
- [ ] 单元、集成、攻击、浏览器和真实 Genesis 运行门全部通过。
- [ ] 每个功能切片独立提交，提交包含对应测试和证据。
- [ ] 完成 reader-facing walkthrough、repo-docs 同步和根 `AGENTS.md` 文档入口。
- [ ] 明确没有完成的真机或仿真器能力，禁止扩张主张。
