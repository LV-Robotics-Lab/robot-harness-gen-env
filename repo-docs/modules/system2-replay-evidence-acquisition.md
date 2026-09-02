# System 2 动态 Replay Evidence Acquisition

`self_improving.system2_replay_evidence_acquisition.System2ReplayEvidenceAcquirer.acquire`
把已经 promotion 到 replay CAS 的 `PromotedSystem2ReplayInput` 交给正式 application factory 的固定组装
入口，并对一次动态 replay 返回的终态、Invocation、事件页、journal 与 CAS 引用做本地一致性对账。它是
replay 执行与持久证据读取之间的窄 seam，不是 validate 或发布门。

## 一条成功路径

```text
ReplayApplicationSettings + PromotedSystem2ReplayInput
  -> 要求 fresh state root
  -> destination root exact-match application evidence CAS
  -> 解析 canonical EnvironmentPackage；resolve catalog/package-manifest refs
  -> production factory 组装唯一 text2env.replay@1.0.0 application
  -> 对 promoted replay input 调用一次 replay
  -> 两次读取 terminal RunState 与 Invocation，并读取完整 EventPage
  -> 对账 descriptor、dynamic input、依赖名、事件、journal 与 CAS artifacts
  -> succeeded: strict Text2EnvReplayOutput
     blocked/failed: 无 typed output 的正常终态
  -> System2ReplayEvidenceAcquisitionResult
```

state root 必须此前不存在，所以 acquirer 是 one-shot。promotion destination、settings 中的 evidence root
和 application 暴露的 artifact root 必须是同一个 canonical 目录；换言之，实际部署时 promotion
destination 必须直接指向部署侧 replay evidence CAS，不能先推广到一个与 application 隔离的空 CAS。

## 固定资格与动态运行不是同一主张

checked-in replay qualification 的固定 can-on-plate 案例证明既定源码、依赖下的 factory wiring 与
`text2env.replay@1.0.0` descriptor 可用。它不自动证明当前动态 `PromotedSystem2ReplayInput` 已 qualification，
也不证明这个动态结果已 qualification。本 seam 只在同一 application contract 下执行并对账当前输入与
本地持久记录；它不重新计算 Registry 私有的 Invocation digest，也不把动态依赖摘要冒充固定资格事实。

真实部署的正向运行仍须同时提供固定资格所依赖的外部 63-ref CAS closure、解释器与运行工具、RoboTwin
资产、runtime capability、delegated cgroup 和新的 state root。package-only promotion 没有搬运 request
provenance、compile receipt、history authority 或 qualification closure，因此不能单独组成离线 authority。

## 终态与证据对账

一次 started run 可以正常结束为 `succeeded`、`blocked` 或 `failed`。只有 `succeeded` 可以携带严格
`Text2EnvReplayOutput`；`blocked` / `failed` 返回 `typed_output=None`，保留可诊断终态而不伪造成功。
attempt 0 只允许 preflight `blocked` / `failed`，且没有 Invocation；已开始的 run 则要求 Invocation 与动态
输入、终态 digest、五个 replay dependency 名和最大重试次数一致。

返回的 terminal state 必须与事件读取前后的两次持久 state 完全相同，Invocation 也必须两次一致；完整
`EventPage` 的正整数 cursor、顺序、run/skill identity 和 envelopes 要逐项对应 `RunState.events`。已开始
run 的 state/event artifacts 必须是 destination CAS 中可解析的 canonical refs；重复 ref 只解析一次。
成功输出的 runtime evidence 与 replay artifacts 还必须属于 terminal artifacts。journal 最终必须是 fresh
state root 下精确的 canonical `harness.sqlite3` 普通文件。

## 已验证范围

本切片的 71 项测试通过模块内部对 `create_replay_application` 边界的替身验证上述 contract；仓库没有提交
可执行的部署 CAS、runtime 与工具 fixture，所以这些测试不是一次真实 deployed dynamic replay 的证据。
本轮没有启动部署仿真、没有声称 physical validation，没有执行 validate v2，不产生 `publishable` 决策，
不推进 System 2 world state，也不产生 portable offline authority。

精确源码摘要、TDD 演进、194 statements / 72 branches 的 100% coverage、363 项九文件邻接回归与两轮
独立 review 见
[`docs/evidence/system2-replay-evidence-acquisition-20260902.md`](../../docs/evidence/system2-replay-evidence-acquisition-20260902.md)。

证据状态：2026-09-02 已验证动态 replay acquisition 的本地 contract 与持久证据对账；真实部署正向运行、
物理验收、validate v2、System 2 状态推进、portable authority 与发布资格仍未由本切片验证。
