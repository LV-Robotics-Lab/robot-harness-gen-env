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
`genesis.robot_policy@1` 最终门、MCP optional dependency 与 autoresearch 设置。当前按 P0/P1 的
纵向 TDD 切片实施。P1 的 revision-0 durable start/read 已合入；P6 正在修复 asset staging 的
RoboTwin loader sidecar 闭包，并以 staged-only 的真实 SAPIEN 回放验收。autoresearch 只在其冻结
benchmark 可以真实运行后开始。
