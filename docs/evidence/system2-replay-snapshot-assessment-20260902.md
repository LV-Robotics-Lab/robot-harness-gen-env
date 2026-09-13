# System 2 replay snapshot assessment 证据（2026-09-02）

配套结构化报告为
[`system2-replay-snapshot-assessment-20260902.json`](system2-replay-snapshot-assessment-20260902.json)，
文件 SHA-256 为
`dcab7a40754e09af6034b28a496558059daa21aed090a854688030f55277fa79`。
本证据以 commit `135fc56566e6bedac350c1ff3b19ee5c38e0545e` 为实现冻结时的基线。

## 已验证接口

公开 seam 为：

```text
System2ReplaySnapshotAssessmentRecorder(
    scratch_parent: Path,
).record(
    acquisition: System2ReplayEvidenceAcquisitionResult,
) -> System2ReplaySnapshotAssessmentResult
```

`record` 只接收 `acquisition`，不接收调用方组装的 validation result。它在内部直接构造未经修改的
`System2ReplaySnapshotValidator(scratch_parent=...)`，再把同一个 acquisition 交给 `recompute`；也没有公开
adapter 或 verifier 注入 seam。返回值只含 canonical `assessment` ref 和该次
`System2ReplaySnapshotValidationResult`。

这层 recorder 不复制 replay 或 snapshot validator 的对账策略。是否允许进入重算、Invocation / 终态 / typed
output / 五项依赖 / runtime refs 如何绑定，以及 snapshot 报告如何判为 `PASS`、`FAIL` 或 `INCOMPLETE`，仍由
既有 snapshot validator 和其 committed adapter 路径负责。

## 从 acquisition 到本地 assessment

内部 snapshot 重算成功后，recorder 生成 schema
`harness.system2_replay_snapshot_assessment.v1`、scope
`local_cas_snapshot_recomputation` 的 canonical JSON。记录包含：

- replay run id、Invocation digest、开始/结束时间；
- `text2env.replay@1.0.0`、`max_attempts == 2`；
- 完整 `Text2EnvReplayInput` 与按名称排序的五项 `DependencyRef`；
- runtime evidence、runtime asset snapshot manifest 和 validation report 三个 `ArtifactRef`；
- validation status、`fail_count` 与 `not_run_count`。

assessment 通过 `LocalArtifactStore(acquisition.artifact_root)` 写回同一个本地 CAS。ref 必须具有 exact name
`system2_replay_snapshot_assessment`、JSON media type、上述 schema 与 canonical
`artifact://sha256/<digest>` URI；recorder 随后从该 CAS 重读逐字节相同的 payload，并以严格 typed model 再验一次。
payload 不记录宿主机路径，也不包含 `source_kind`、world state、state delta、receipt、decision 或
`publishable` 字段。

同一 acquisition 重复记录会得到完全相同的 assessment ref、payload bytes 和 snapshot validation result；这
是同一本地 CAS 内的内容寻址幂等性，不是跨机器的 portable authority。

## 正常结果与固定失败语义

`PASS`、`FAIL`、`INCOMPLETE` 都是正常 assessment 结果。它们只是如实记录对所提供本地 CAS evidence 的
snapshot 重算状态，不由 recorder 增加第二套判定。PASS 与 FAIL 用未经替换的 committed snapshot adapter
路径验证；INCOMPLETE 仍经过同一 adapter，但测试通过受控 raw-validator `not_run` 分支产生。

异常被压缩为三个固定 reason：

- `snapshot_validation_failed`：内部 snapshot recomputation 不能完成；测试覆盖 unsuccessful replay，且在
  assessment 写入前停止；
- `assessment_publish_failed`：canonical assessment 不能写入 acquisition CAS；
- `assessment_verification_failed`：ref header、CAS readback、exact bytes 或严格 typed payload 复核失败。

定向测试同时确认成功路径及已覆盖失败路径结束后 scratch 为空。该清理性质不把 assessment 提升为事务性
run receipt，也不声明对任意外部 CAS 故障具有回滚语义。

## TDD 与冻结身份

第一条 RED 是公开模块尚不存在时的 `ModuleNotFoundError`。随后普通 RED→GREEN 批次覆盖：

- acquisition-only public seam 与仅含两项的结果；
- snapshot recomputation 失败在 assessment 写入前映射为固定 reason；
- assessment CAS 写入、ref header、readback 与 exact bytes 的失败映射，以及成功路径的 strict typed
  roundtrip；
- canonical same-CAS payload、完整绑定字段和被排除概念；
- 相同 acquisition 的内容寻址幂等性；
- PASS、FAIL、INCOMPLETE 三种正常记录结果。

最终实现与测试身份为：

- implementation：209 lines、7,846 bytes、SHA-256
  `c4de1998fc7d7ceb6d57f69ae6d31d69ba9e6a80b5c44f8a6e6e555a5576761b`；
- test：719 lines、26,108 bytes、SHA-256
  `82fae41d170e546cf0a4f39bcdc9b5144f77ab0795a8d3547988e7d53b27d16c`。

定向功能与 100% coverage 门：

```bash
pytest -q -p no:cacheprovider \
  tests/self_improving/test_system2_replay_snapshot_assessment.py \
  --cov=self_improving.system2_replay_snapshot_assessment \
  --cov-branch --cov-report=term-missing --cov-fail-under=100
```

结果为 10 passed、0 failed；77 statements、4 branches，statement/branch 均为 100%。六文件邻接回归：

```bash
pytest -q -p no:cacheprovider \
  tests/self_improving/test_system2_replay_snapshot_assessment.py \
  tests/self_improving/test_system2_replay_snapshot_validation.py \
  tests/self_improving/test_system2_replay_evidence_acquisition.py \
  tests/self_improving/test_system2_replay_input_promotion.py \
  tests/self_improving/test_system2_replay_handoff.py \
  tests/self_improving/test_validate_v2_snapshot.py
```

结果为 300 passed。静态门：

```bash
ruff check self_improving/system2_replay_snapshot_assessment.py \
  tests/self_improving/test_system2_replay_snapshot_assessment.py
ruff format --check self_improving/system2_replay_snapshot_assessment.py \
  tests/self_improving/test_system2_replay_snapshot_assessment.py
git diff --no-index --check /dev/null \
  self_improving/system2_replay_snapshot_assessment.py
git diff --no-index --check /dev/null \
  tests/self_improving/test_system2_replay_snapshot_assessment.py
```

Ruff 与 format 均通过。两个 no-index 命令因新文件与 `/dev/null` 不同而按预期返回 1，但没有 whitespace
diagnostics。

## 主张边界

测试 acquisition 是本地、类型化、语义合法的 CAS fixture；本切片没有启动 simulator、执行真实 deployed
dynamic replay 或产生新的物理测量。即使 assessment 内记录 `PASS`，它也不是 physical-success claim。
replay factory 的 checked-in 固定资格案例只资格化固定 wiring；它不授权这里的动态 input、acquisition、
report 或 outcome。

Local Snapshot Assessment 不是 `fresh_observation`，也不是 `WorldFactEvidence`。它没有完成完整 Validate v2，
没有生成 trusted / portable receipt、validation decision、`publishable` 结论或 portable authority，也没有触发
System 2 world-state transition。其 path-free canonical payload 只证明同一本地 CAS 中记录的内容身份；它不把
上游 local acquisition 或 snapshot recomputation 的来源边界升级成跨机器权威。
