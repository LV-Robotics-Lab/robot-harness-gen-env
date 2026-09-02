# System 2 的一次真实 Compile 回合

`self_improving.system2_compile_turn.System2CompileTurn` 把现有的 System 2 零件收进一个
公开入口：调用方给出任务意图、seed、受信 catalog 路径和 generate-on-miss 策略，模块返回
`System2CompileTurnResult`。调用方不需要自己拼 planner context、保存模型回执、调用 Registry、
套 ToolResult、应用 StateDelta 或发布 history authority。

## 回合如何闭合

```text
operator input
  -> catalog snapshot + 两条 CAS-backed user facts
  -> TrustedWorldState v1
  -> bounded PlannerContext
  -> System2Planner 的 prompt / raw response / decision / receipt
  -> exact typed compile-input 对账
  -> qualified CompileApplication + Registry + handler
  -> TrustedToolReceipt + System2ToolResult
  -> apply StateDelta
  -> TrustedWorldState v2 + complete history authority
```

planner 只能选择 context 中那份完整的 `Text2EnvCompileInput`。它若改 seed、catalog、配置或
request，回合会在 Skill 执行前停止，并保留已发布的 planner receipt 供诊断；下层 planner、
dispatcher 与 application 的异常类型不会泄漏成调用方必须理解的 API，统一收敛为
`System2CompileTurnError(reason=...)`。

成功回合使用真实的 packaged compile qualification、Registry 与 handler。返回的世界状态是
version 2，新增的 catalog 和 environment package facts 由 trusted receipt 支撑；完整两级 state
lineage 与 history entry 会写入 CAS，再由 history verifier 复读。

## 为什么 blocked 不发布 history authority

compile 因 catalog miss 等原因得到 `blocked` 或 `failed` 时，ToolResult 与 TrustedToolReceipt 仍是
正常终态，不应改写成异常。但当前 history-v1 只允许「成功 receipt 导致 state mutation」来推进
世界状态和时间。模块因此保留原 version-1 state，并明确返回 `history_authority=None`；它不会伪造
一个空 StateDelta 或假的 v2 lineage。后续若要让失败也进入可续跑历史，应先扩展 Harness 的
failure-transition 契约并重新资格化，而不是在组合层绕过。

## 当前边界

这是一次初始、compile-only 的回合，不是通用 agent loop：

- planner provider 在验收中是确定性本地替身，尚未证明真实 LLM planner 已资格化；
- dispatcher 仍只开放 `text2env.compile@1.0.0`，没有 replay/validate Tool；
- blocked/failed 终态不能作为下一回合的完整 history authority；
- ToolResult 成功不等于物理 validation 通过，更不等于可以发布。

实现位于 `self_improving/`、而不是 `self_improving/harness/`。这让组合层可以复用已资格化的
Harness，而不改变当前 replay qualification 所哈希的 Harness、`scene_gen` 或 ledger contract
源码集合。

精确文件摘要、6 项公开 seam 测试、462 项邻接回归和 100% statement/branch coverage 见
[`docs/evidence/system2-compile-turn-20260902.md`](../../docs/evidence/system2-compile-turn-20260902.md)。

证据状态：2026-09-02 已由真实 CompileApplication 路径与 CAS history verifier 验证；真实 LLM
planner、replay/validate dispatch 和失败历史续跑仍未验证。
