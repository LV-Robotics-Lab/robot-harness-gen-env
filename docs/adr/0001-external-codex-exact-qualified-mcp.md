---
status: accepted
date: 2026-09-09
---

# External Codex through exact qualified MCP tools

Golden Workflow 将 External Codex Agent 置于受信执行边界之外，由深层 `GoldenRunHarness`
聚合独占工作流状态、状态推进与晋升事务，并通过薄 MCP adapter 暴露来自冻结资格快照的精确、
版本化 Skill tools 和只读 resources。选择这一组合，是因为内部 model provider 会混淆模型建议与
Harness 权威并把目标架构绑到历史 Qwen/VLM，而泛型 `skill.invoke` 会隐藏每个 Skill 的 schema、
资格身份和路由选择，削弱授权、审计与成功率测量；代价是公开工具面更宽，但 MCP 层不得补默认值、
重试或替 Harness 决策。
