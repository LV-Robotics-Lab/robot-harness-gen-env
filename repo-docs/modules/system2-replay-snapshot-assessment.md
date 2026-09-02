# System 2 Replay Snapshot Assessment

`self_improving.system2_replay_snapshot_assessment` 现在有两个窄 seam：recorder 把一次本地 replay acquisition 的
snapshot 重算写成同一 CAS 内的 canonical assessment；artifact-only verifier 只凭 assessment ref、该 CAS 与
独立 scratch，再跑一次真实 snapshot adapter 并核对记录摘要。两者都不推进 System 2 状态。

## 记录路径

```text
System2ReplayEvidenceAcquisitionResult + scratch parent
  -> recorder 内部构造现有 System2ReplaySnapshotValidator
  -> 用同一个 acquisition 重算 acquisition-aware snapshot result
  -> 组合 run / Invocation / replay input / 五项依赖与 runtime refs
  -> canonical assessment JSON 写入 acquisition 的同一本地 CAS
  -> 复核 exact ref、CAS readback、exact bytes 与 strict typed payload
  -> System2ReplaySnapshotAssessmentResult(assessment, snapshot_validation)
```

公开 `record` 只有 acquisition 参数。调用方不能传入已组装的 validation result，也没有 adapter/verifier 注入
seam。recorder 不复制既有 validator 的对账策略，也不拥有第二套 validation policy。

## Artifact-only 验证路径

```text
ArtifactRef + same LocalArtifactStore + disjoint scratch parent
  -> exact assessment ref header + CAS read
  -> strict System2ReplaySnapshotAssessment + exact canonical bytes
  -> UTC / five dependencies / runtime-assets binding / artifact refs 自洽门
  -> 要求 recorded validation report 已存在于同一 CAS
  -> 内部构造 ValidateV2SnapshotAdapter
  -> 由 record 的 package + runtime evidence + asset snapshot 重算
  -> exact report ref + status + fail/not-run counts 对账
  -> VerifiedSystem2ReplaySnapshotAssessment(record, bare SnapshotValidationResult)
```

公开 `verify` 只接收 assessment ref；constructor 接收 exact `LocalArtifactStore` 和 `Path` scratch。它既不接收
acquisition，也不允许 adapter 注入。scratch 必须存在并与 CAS 分离；同址、scratch 为 CAS 祖先、或 scratch 为
CAS 后代都拒绝。重复验证同一 ref/store/scratch 得到相等结果；独立空 scratch 在成功或已进入 adapter 的受测
路径后不遗留新的 attempt 目录，overlap 或 missing scratch 则没有“为空”主张。

## 严格 record 与实际验证范围

assessment 名称为 `system2_replay_snapshot_assessment`，schema 为
`harness.system2_replay_snapshot_assessment.v1`，scope 为
`local_cas_snapshot_recomputation`。payload 记录 run id、Invocation digest、UTC 起止时间、固定
`text2env.replay@1.0.0` / `max_attempts=2`、完整 `Text2EnvReplayInput`、按名称排序且 version 为 1 的五项依赖，
以及 runtime evidence、runtime asset snapshot、validation report 与状态计数。

strict model 要求开始时间不晚于结束时间，五依赖集合精确且 runtime-assets digest 绑定 snapshot manifest；相关
JSON refs 的 schema、media type、canonical URI 也必须匹配。verifier 还拒绝任何不是 exact canonical JSON bytes
的 payload，并要求 pre-existing report 与重算输入都可从同一 CAS 解析。

成功返回只证明该本地 CAS 中的 package/runtime/snapshot closure 重算出与记录完全相同的 validation report ref、
status、`fail_count` 和 `not_run_count`。run id、Invocation digest 与时间没有向原始 run 独立回查；四项非 runtime
依赖 identity 也没有向 producer 或 Registry 独立溯源。它们是 typed、时间与依赖形状自洽的记录 metadata，
不是 independently verified origin。

## 状态、错误与幂等性

`PASS`、`FAIL`、`INCOMPLETE` 对 recorder 和 verifier 都是正常结果。PASS/FAIL 走未经替换的 committed adapter；
INCOMPLETE 仍走同一 adapter，只在测试中以受控 raw-validator `not_run` 分支产生。状态不是 recorder/verifier 新作
的发布或物理成功决定。

recorder 保留三个固定 reason：

- `snapshot_validation_failed`：acquisition-aware snapshot 重算不能完成；
- `assessment_publish_failed`：assessment 不能写入本地 CAS；
- `assessment_verification_failed`：recorder 的 ref、readback、exact bytes 或 strict payload 复核失败。

artifact-only verifier 使用另外三个固定 reason：

- `assessment_invalid`：ref、CAS read、strict/canonical record 或内部一致性无效；
- `assessment_recompute_failed`：pre-existing report/输入、scratch 隔离或真实 adapter 重算失败；
- `assessment_mismatch`：report ref、status 或两个 count 与重算不一致。

同一 acquisition 的记录 ref、bytes 和 snapshot result 保持内容寻址幂等；同一 assessment 的验证结果也确定且
相等。这些都是单一本地 CAS 的性质，不是跨机器 portable authority 或事务回滚保证。

## 权威边界

测试输入是 local typed semantic CAS fixture，没有启动 simulator、执行真实 deployed dynamic replay 或产生新物理
测量。verifier 执行 snapshot adapter recomputation，不等于完成完整 Validate v2；PASS 也不是
physical-success claim。

Local Snapshot Assessment 不是 `fresh_observation` 或 `WorldFactEvidence`。它不生成 receipt、validation
decision、`publishable` 结论或 portable authority，不推进 System 2 world state。固定 qualification 只资格化
replay factory 的固定 wiring，不能授权当前动态 input、acquisition、report 或 outcome。

41 项定向测试覆盖 143 statements / 22 branches，语句与分支均为 100%；六文件邻接回归为 331 passed。两轮独立
最终 review 的 P0/P1/P2 findings 均为 0/0/0。verifier 增量的精确文件摘要、RED→GREEN 类别、命令和主张矩阵见
[`docs/evidence/system2-replay-snapshot-assessment-verifier-20260902.md`](../../docs/evidence/system2-replay-snapshot-assessment-verifier-20260902.md)；
原 recorder 的不可变冻结记录仍见
[`docs/evidence/system2-replay-snapshot-assessment-20260902.md`](../../docs/evidence/system2-replay-snapshot-assessment-20260902.md)。

证据状态：2026-09-02 已验证 acquisition-only recorder 与同 CAS artifact-only snapshot-summary verifier；Fresh
Observation、WorldFactEvidence、物理成功、完整 Validate v2、receipt、portable authority、发布资格与
world-state advancement 仍未由本切片验证。
