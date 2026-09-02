# System 2 replay snapshot validation 证据（2026-09-02）

配套结构化报告为
[`system2-replay-snapshot-validation-20260902.json`](system2-replay-snapshot-validation-20260902.json)，
文件 SHA-256 为
`94f32c6fbce876e9cd8f0a352d1553398217a6fc11501452b04f505ffff2b730`。
本证据以 commit `b515a3ace08f5c6d7aa7bc395c1a78f78de6c948` 为实现冻结时的基线。

## 已验证接口

公开 seam 为：

```text
System2ReplaySnapshotValidator(
    scratch_parent: Path,
).recompute(
    acquisition: System2ReplayEvidenceAcquisitionResult,
) -> System2ReplaySnapshotValidationResult
```

结果只包含 `replay_run_id`、`replay_invocation_digest`、`runtime_evidence`、
`runtime_asset_snapshot_manifest` 和 `snapshot_validation`。模块不另接 promoted input，而是只从
`acquisition.invocation.effective_parameters` 严格重建 `Text2EnvReplayInput`。

输入必须是 exact `System2ReplayEvidenceAcquisitionResult`。已开始的 replay 只有 `succeeded` 才进入
adapter；`blocked` 或 `failed` 在任何 adapter 写入前以稳定 reason `replay_not_succeeded` 停止。模块还会
对账 Invocation、终态 `RunState` 与严格 typed output 的 run id、Invocation digest、输入、输出和 artifacts，
并要求 factory-owned 形状保持 `text2env.replay@1.0.0`、`max_attempts == 2`。五项依赖名来自公开
`REPLAY_DEPENDENCY_NAMES`，每项都是 exact `DependencyRef` 且 `version == "1"`；其中
`text2env.replay.runtime_assets` 的 SHA-256 必须等于选中的 runtime asset snapshot，另外四项摘要不在本
bridge 内重算。

## 从 replay artifacts 到 snapshot 报告

模块从严格 replay output 中选出唯一一个名称为 `runtime_asset_snapshot`、schema 为
`harness.runtime_asset_snapshot.v1`、media type 为 `application/json` 的 canonical CAS ref，并要求它属于终态
artifacts。`runtime_evidence` 同样必须具有 exact 名称、`robotwin.scene_runtime_evidence.v2` schema、JSON
media type、canonical CAS URI 和终态 membership。两者都必须能从 acquisition 的 canonical 本地 CAS
解析。

调用方提供的 scratch parent 必须是 canonical 已存在目录，且不得与 acquisition CAS 重叠。模块通过
`LocalArtifactStore(acquisition.artifact_root)` 调用仓库已提交的
`ValidateV2SnapshotAdapter.recompute(SnapshotValidationRequest)`；request 由 Invocation 中的 environment
package、选中的 runtime evidence 和 snapshot ref 组装。真实 adapter 产生的
`snapshot_validation_binding` 将 package id、asset catalog 与 package manifest 摘要、两个 runtime 摘要和
固定 gate profile `robotwin.scene_validation.v1` 绑定在同一报告中。

bridge 从同一个 CAS 重读 adapter 返回的 report ref，只交叉核对报告顶层的 `schema_version`、`status`、
`fail_count`、`not_run_count` 与 `SnapshotValidationResult`。它不复制 adapter 私有的 checks、locator、计数
推导或物理判定策略。每次调用的临时 attempt 目录在正常返回或错误后都会清理。

`PASS`、`FAIL` 和 `INCOMPLETE` 都是对调用方提供的 CAS evidence 所做重算的正常结果；它们不是发布决定。
PASS 与基于本地 CAS evidence 得到的 FAIL 均用未经替换的 committed adapter 路径验证。INCOMPLETE 用同一个 committed adapter，
但测试以受控 raw-validator `not_run` 分支制造未完成结果；不能把这个测试解释为一次新的部署物理测量。
两个内容相同但目录独立的 CAS 副本产生逐字节相同、SHA-256 相同的 validation report。

## TDD 与冻结身份

第一条 RED 是公开模块不存在时的 `ModuleNotFoundError`。随后普通 RED→GREEN 批次覆盖：

- public recompute tracer，以及真实上游五项依赖、最大尝试次数和 runtime snapshot 摘要绑定；
- canonical scratch/CAS 边界，Invocation、RunState、typed output 与 terminal artifacts 的交叉绑定；
- runtime evidence / snapshot 的缺失、重复、名称、schema、media、URI、terminal membership 与 CAS 可用性；
- adapter 错误映射，PASS/FAIL/INCOMPLETE 正常返回，结果与报告四个顶层值的一致性；
- 两个独立 CAS 副本的报告 bytes / SHA-256 确定性。

最终实现与测试身份为：

- implementation：332 lines、13,062 bytes、SHA-256
  `d8ef7492ac8bec864738dcf180c455e76d32b2a58953527c253a4d6fdd6612c7`；
- test：1,069 lines、39,990 bytes、SHA-256
  `646502da12593f3735fbaa9daf2ceecde307cb9b62d7526e3e589a30b39a174d`。

定向功能与 100% coverage 门：

```bash
pytest -q -p no:cacheprovider \
  tests/self_improving/test_system2_replay_snapshot_validation.py \
  --cov=self_improving.system2_replay_snapshot_validation \
  --cov-branch --cov-report=term-missing --cov-fail-under=100
```

结果为 48 passed、0 failed、无 warnings；139 statements、40 branches，statement/branch 均为 100%。五文件
邻接回归：

```bash
pytest -q -p no:cacheprovider \
  tests/self_improving/test_system2_replay_snapshot_validation.py \
  tests/self_improving/test_system2_replay_evidence_acquisition.py \
  tests/self_improving/test_system2_replay_input_promotion.py \
  tests/self_improving/test_system2_replay_handoff.py \
  tests/self_improving/test_validate_v2_snapshot.py
```

结果为 290 passed。静态门：

```bash
ruff check self_improving/system2_replay_snapshot_validation.py \
  tests/self_improving/test_system2_replay_snapshot_validation.py
ruff format --check self_improving/system2_replay_snapshot_validation.py \
  tests/self_improving/test_system2_replay_snapshot_validation.py
git diff --no-index --check /dev/null \
  self_improving/system2_replay_snapshot_validation.py
git diff --no-index --check /dev/null \
  tests/self_improving/test_system2_replay_snapshot_validation.py
```

Ruff 与 format 均通过。两个 no-index 命令因新文件与 `/dev/null` 不同而按预期返回 1，但没有 whitespace
diagnostics。

## 主张边界

测试 acquisition 是本地、类型化、语义合法的 CAS fixture，不依赖受保护的 run-receipts 文件，也没有启动
simulator 或执行一次真实 deployed dynamic replay。replay factory 的 checked-in 固定资格案例只资格化其
固定 wiring；它不资格化本次动态 input、acquisition、report 或 physical outcome。

本切片确实调用 committed `ValidateV2SnapshotAdapter`，但只完成 snapshot validation recomputation，不等于
完整 Validate v2。它没有生成 compile/replay portable receipts、portable provenance closure、standalone
media receipt 或 RuntimeConfig receipt，也没有生成 validation decision、`publishable` 结论或推进 System 2
world state。PASS/FAIL/INCOMPLETE 只能解释为针对所提供 CAS evidence 的重算结果。

重算以 CAS 内容为权威；但从重建 metadata 得到的旧 source locator 仍可能接受 metadata `stat`。因此这里不
声称 zero filesystem lookup，也不把本结果称为可移植的 acquisition-origin authority。
