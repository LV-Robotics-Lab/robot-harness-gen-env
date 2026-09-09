# Golden E2E 任务分解

## G0：基线与审计

- [x] 记录 Codex 取代独立 System 2 中枢和独立 VLM 的架构决定。
- [x] 创建持久进度账本并从根 `AGENTS.md` 建立入口。
- [x] 审计 `worktree/gujie` 的 x2env 输入、输出、Genesis 资产/场景格式和可运行证据。
- [x] 审计历史资产债务、可迁移数据、真实 settle 缺口和现有修复工具。
- [x] 审计当前 compile/replay/validate、System 2、MCP、observation、diagnosis 和 promotion seam。

## G1：契约与测试 seam

- [x] 形成至少三种 x2env Skill interface 设计并比较 depth、locality 和 seam placement。
- [x] 起草 text/image/video/multimodal 输入归一化合同，等待确认冻结。
- [x] 起草 image2env 与 video2env 的三个 Skill 命名、输入、输出、资格和失败合同，等待确认冻结。
- [x] 起草 Codex external-agent/advisory 与 MCP adapter 合同，等待确认冻结。
- [x] 用户确认 `SEAMS.md` 中的公共测试 seam。
- [x] 用户确认 `AUTORESEARCH_SETUP.md` 的指标、范围、约束和实验预算。

## G2：Codex 中枢与 MCP 证据链

- [ ] qualified compile 的 Codex 调用与可信 ToolResult。
- [ ] qualified replay/validate 的同 run 调度。
- [ ] fresh observation 获取及回执绑定。
- [ ] Codex 诊断、受限修复、状态增量与可续跑 history。
- [ ] promotion gate 与 publishability 唯一授权。
- [ ] 真正 MCP adapter：schema discovery、typed invocation、ToolResult 返回及错误映射。
- [ ] 一个 text-only golden case 完成同 run 证据闭包。

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
