# System 2 Replay Snapshot Validation

`self_improving.system2_replay_snapshot_validation.System2ReplaySnapshotValidator.recompute`
把一次已经在 acquisition seam 内完成对账、且终态为 `succeeded` 的动态 replay，接到仓库已提交的 snapshot
validator。它从同一个本地 CAS 重算 `SnapshotValidationResult`，但不把结果提升为完整 Validate v2、发布
决定或 System 2 状态推进。

## 一条重算路径

```text
System2ReplayEvidenceAcquisitionResult + canonical scratch parent
  -> 要求 started replay 的 terminal state 为 succeeded
  -> 只从 Invocation.effective_parameters 重建严格 Text2EnvReplayInput
  -> 对账 Invocation / RunState / typed output / terminal artifacts
  -> 对账固定 replay skill、max_attempts=2 与五项公开 dependency 名
  -> 选择 exact runtime_evidence 和唯一 runtime_asset_snapshot ref
  -> 要求 refs canonical、属于 terminal artifacts、可从 acquisition CAS 解析
  -> 以 LocalArtifactStore 调用 committed ValidateV2SnapshotAdapter
  -> 从同一 CAS 重读报告并核对 result/report 的四个顶层值
  -> System2ReplaySnapshotValidationResult
```

模块不另接 promoted input，也不复制 replay acquisition 的 journal/EventPage 验证。它只保留这个 bridge 特有
的交叉绑定：Invocation 必须能严格重建 replay input；run id、Invocation digest、output 和 artifacts 必须与
终态一致；五项 dependency 都是 version 1，其中 `text2env.replay.runtime_assets` 的摘要必须等于选中的
snapshot。其余四项依赖摘要由上游权威负责，本模块不重算。

## Evidence refs 与 adapter 边界

runtime evidence 要求名称 `runtime_evidence`、schema `robotwin.scene_runtime_evidence.v2` 和 JSON media；
runtime asset snapshot 要求名称 `runtime_asset_snapshot`、schema `harness.runtime_asset_snapshot.v1` 和 JSON
media，并且在 replay artifacts 中恰好出现一次。两者都必须使用 canonical CAS URI、出现在 terminal
artifacts 中，并能从 acquisition 的 canonical 本地 CAS 解析。

scratch parent 必须是已存在的 canonical 目录，不能与 CAS 重叠。模块用 Invocation 的 environment package
和上述两个 refs 组成 `SnapshotValidationRequest`，再调用真实 committed
`ValidateV2SnapshotAdapter.recompute`。返回后只从同一 CAS 解析 report ref，并核对
`schema_version`、`status`、`fail_count`、`not_run_count`；具体 checks、locator 与物理判定继续由 adapter
拥有，bridge 不实现第二套报告 verifier。

## 如何解释结果

`PASS`、`FAIL`、`INCOMPLETE` 都是正常返回：它们描述的是对当前提供 CAS evidence 的重算，不是一次部署
replay 的真实性证明，也不是 publishable 决策。`blocked` 或 `failed` replay 不会进入 adapter，而会在任何
adapter 写入前以 `replay_not_succeeded` 停止。

本切片的正向 acquisition 是本地 typed fixture，不是一次真实 deployed replay；测试没有启动 simulator。
factory 的 checked-in 固定资格案例只资格化固定 wiring，并不资格化这里的动态 input、acquisition、report
或 physical outcome。测试中的 PASS 与基于本地 CAS evidence 得到的 FAIL 走未替换的 committed adapter；INCOMPLETE 仍走同一
adapter，但 raw-validator 的 `not_run` 分支由测试控制。

即使使用真实 snapshot adapter，本结果仍不是完整 Validate v2、portable run receipt、provenance closure、
standalone media receipt 或 RuntimeConfig receipt；它不生成 decision / `publishable` 结论，也不推进 System 2
world state。旧 source locator 可能接受 metadata `stat`，所以不能声称 zero filesystem lookup。

48 项定向测试覆盖 139 statements / 40 branches，语句与分支均为 100%；五文件邻接回归为 290 passed。
精确文件摘要、RED→GREEN 类别、复跑命令和主张矩阵见
[`docs/evidence/system2-replay-snapshot-validation-20260902.md`](../../docs/evidence/system2-replay-snapshot-validation-20260902.md)。

证据状态：2026-09-02 已验证成功 replay acquisition 到本地 snapshot 重算的窄 bridge；部署 replay、完整
Validate v2、portable authority、发布资格与 world-state advancement 仍未由本切片验证。
