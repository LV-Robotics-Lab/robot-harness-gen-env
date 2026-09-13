# System 2 Compile → Replay 受信交接

`self_improving.system2_replay_handoff.System2ReplayHandoff` 把一次成功的
`System2CompileTurnResult` 转成后续 replay 可以消费的严格输入和两份 CAS 证据。调用方不需要自行拼接
compile receipt、history 和环境包，但这个模块只负责交接，不负责执行 replay。

## 一条成功路径

```text
System2CompileTurnResult + RuntimeConfig
  -> 复核 version-2 succeeded 结果
  -> CAS 重验 planner history authority 与 trusted compile receipt
  -> 对账 Invocation / RunState / typed output / package closure
  -> 从 history 基态重放 StateDelta，并绑定最终 world state
  -> 构造 Text2EnvReplayInput
  -> 发布并回读 environment package + request provenance
  -> PreparedSystem2Replay
```

`PreparedSystem2Replay` 同时给出：

- `replay_input`：原 compile output 的 `EnvironmentPackage` 加调用方给出的严格 `RuntimeConfig`；
- `environment_package_ref`：整个环境包的 canonical JSON CAS ref；
- `request_provenance_ref`：request、seed、compile run / Invocation、receipt、history、world state 与环境包
  之间的 path-free 绑定。

两个 JSON 都在写入后从 CAS 回读并逐字节验证。相同 compile closure 和 runtime config 复制到另一个 CAS
store 后会得到相同返回值与相同 bytes，不依赖 scratch 目录或宿主路径。

## 为什么还要重放 StateDelta

history verifier 和 receipt verifier 分别能证明各自的记录有效，但“两个有效记录”不自动等于“它们描述
同一条状态转移”。模块要求 history 中恰好一条 entry 指向 supplied trusted receipt，并把 supplied
StateDelta 从 lineage 基态重新应用。这样即使攻击者把从同一基态产生的 A receipt 与 B 终态拼到一份
结构合法的 history 中，也不能通过交接。

malformed history、跨回合 history/receipt、丢失或损坏的 CAS member、内存 ToolResult 漂移，以及
制品写入后回读 bytes 漂移都会 fail closed。输入权威性全部验证完毕前，不开始发布新对象。

## 失败与所有权边界

这是 prepare-only seam：它不调用 `ReplayApplication`，不启动 simulator，不采集 runtime evidence，
不执行 validate，不生成 portable run receipt，也不判定物理通过或 `publishable`。

request provenance payload 是模块私有的 frozen strict model。它发布的 ArtifactRef 符合
`Text2EnvValidateV2Input` 预声明的 `harness.text2env_request_provenance.v1` 输入契约，但这里没有新增公开
payload schema。若环境包已进入不可变 CAS、随后 provenance 写入失败，首个对象可能继续存在；方法仍会
失败且不返回部分结果，模块也不会删除可能被其他 ref 共享的 content-addressed 对象。

实现位于 `self_improving/`，测试位于 `tests/self_improving/`；二者都是
`self_improving/harness/**` 的同级文件，因此 replay qualification 的源码哈希集合保持不变。

精确源码摘要、24 项定向测试、428 项邻接回归、100% statement/branch coverage 与攻击用例见
[`docs/evidence/system2-replay-handoff-20260902.md`](../../docs/evidence/system2-replay-handoff-20260902.md)。

证据状态：2026-09-02 已验证 prepare 与 CAS 交接；ReplayApplication、仿真运行、物理 validate、portable
receipt 与发布资格仍未由本切片验证。
