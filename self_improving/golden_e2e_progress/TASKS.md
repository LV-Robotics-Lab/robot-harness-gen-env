# Golden E2E 任务分解

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
