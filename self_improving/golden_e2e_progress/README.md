# Golden E2E 进度账本

本目录是 2026-09-09 启动的 x2env → Genesis golden E2E 工作流单一进度事实源。它记录当前
任务、待办、稳定决策、测试 seam、实验参数和已复核结果；不替代 `docs/evidence/` 的不可变运行
证据，也不把 render 当成物理验证。

## 文件地图

- `TASKS.md`：阶段验收清单。
- `TODO.md`：当前 doing/next/blocked/done。
- `DECISIONS.md`：已经接受的稳定架构决定。
- `AUDIT.md`：Bingsheng、Gujie、资产债务和 Codex/MCP 的只读审计结论。
- `DESIGN_OPTIONS.md`：三套 interface 的 depth/locality/seam 比较和推荐混合方案。
- `SKILL_CONTRACTS.md`：九个 x2env Skills、共享 IR、Genesis profile 和错误合同草案。
- `SEAMS.md`：TDD 公共 seam；确认前不写功能测试。
- `AUTORESEARCH_SETUP.md`：实验目标、指标、范围、预算和隔离策略。
- `ACCEPTANCE.md`：E0–E4 证据等级和最终验收门。
- `IMPLEMENTATION_PLAN.md`：按 TDD 和独立提交组织的纵向实施顺序。
- `X2ENV_IMAGE_VIDEO_IMPLEMENTATION_PLAN.md`：六个 image/video exact Skills 的逐 namespace
  compile/replay/validate golden line、adapter/资格/真实运行门和 catalog 路由条件。
- `RESULTS.md`：命令、通过/失败数字、运行证据和不可扩张主张。
- `../../docs/integration-provenance/LEDGER.md`：三方能力来源、固定版本、整合责任和验证状态台账。

## 每次工作顺序

1. 先读 `DECISIONS.md`、`TASKS.md` 和 `TODO.md`。
2. 开始或结束一个切片时更新 `TODO.md`。
3. 新的稳定架构决定写入 `DECISIONS.md`。
4. 测试、真实运行、哈希和失败结果写入 `RESULTS.md`；正式运行证据另存带日期的
   `docs/evidence/` 页面并从这里链接。
5. 公共测试 seam 以 `SEAMS.md` 为准；未确认的 seam 不开始 TDD 功能实现。
6. 路由优化实验遵守 `AUTORESEARCH_SETUP.md`，每次尝试先提交、后测量，并保留失败记录。
7. 从 Bingsheng、Gujie 或 Yuxin 整合功能时，同一提交更新来源台账；dirty workspace 只有在固定
   内容 manifest/hash 后才能成为来源证据。

## 当前阶段

`p1_p6_implementation`

只读审计和第一版契约设计已经完成；用户于 2026-09-09 确认公共 seam、exact Skill MCP、
`genesis.robot_policy@1` 最终门、MCP optional dependency 与 autoresearch 设置。P1/P2 已建立 durable
start/read 与第一个 exact compile operation；P3 候选又在同一 actor-neutral workflow 中完成 exact
qualified replay operation，并以真实 RoboTwin/SAPIEN 把 revision 推进为 0→1→2。该结果仍不是
Genesis 或 promotion。replay qualification 现进一步以逻辑源码根与 exact relative
bytes/tree identity 跨代码安装路径复用：同一 bundle 在第二个绝对路径 strict load 后又真实完成一次
0→1→2；外部 runtime/operator 依赖仍为部署绑定。最终 revision-3 integration 现已在
同一 workflow、同一 Harness 与同一冻结 RegistrySnapshot 中调用 exact qualified
`text2env.validate@2.0.0`，从 revision 2 的受信 replay lineage 独立重算物理报告并推进至 revision 3；
最终固定实现已重新生成 replay/validate 资格，并在另一代码安装路径真实完成 can-on-plate
revision 0→1→2→3 和无 live application 重启幂等。该结果仍只是 legacy RoboTwin/SAPIEN
publishability，不是 promotion、Genesis 或 robot-policy。新增 validate execution/policy/handler/
application/dependency 五模块的 statement/branch 门均已达到 100%。Image2Env/Video2Env 的六个合同
已经过两轴功能验收；55 份公共
schema 现在既能整目录注册，也能逐文件独立校验。Gujie 现存 evidence 仍只证明 image-derived
existing reconstruction 的 legacy Genesis 候选，不证明 fresh image、video 或 Harness runtime。
P5 exact MCP adapter 现已由官方 MCP 2.2 stdio Client 在真实 RoboTwin/SAPIEN 上完成 raw-text
create、compile、replay、validate revision 0→1→2→3，并在第二个 server 进程重提 validate 而无
SQLite/CAS 增长；workflow-scoped 证据、媒体和 validate input 都只来自 Harness 权威。下一门是外部
Codex planner 回合、fresh observation/diagnosis/promotion，而不是把 transport client 误称为 LLM
路由。P6 继续修复资产 staging；autoresearch 只在其冻结 benchmark 可以真实运行后开始。
