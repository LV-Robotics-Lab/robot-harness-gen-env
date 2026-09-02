# System 2 Replay Snapshot Assessment

`self_improving.system2_replay_snapshot_assessment.System2ReplaySnapshotAssessmentRecorder.record`
把一次本地 replay acquisition 的 snapshot 重算结果写成同一 CAS 内的 canonical assessment。它只增加一个
可内容寻址、可重读的本地记录，不把 validation status 提升为观测、事实、发布决定或状态推进。

## 一条记录路径

```text
System2ReplayEvidenceAcquisitionResult + canonical scratch parent
  -> recorder 内部构造现有 System2ReplaySnapshotValidator
  -> 用同一个 acquisition 重算 SnapshotValidationResult
  -> 组合 run / Invocation / 完整 replay input / 五项依赖绑定
  -> 记录 runtime evidence、asset snapshot、validation report 与状态计数
  -> canonical JSON 写入 acquisition 的同一本地 CAS
  -> 复核 ref header、CAS readback、exact bytes 与 strict typed payload
  -> System2ReplaySnapshotAssessmentResult(assessment, snapshot_validation)
```

公开 `record` 只有一个 `acquisition` 参数。调用方不能塞入已经组装好的 validation result，也没有 adapter 或
verifier 注入 seam；是否可以重算、输入和 evidence 如何绑定、snapshot status 如何产生，都继续由现有
snapshot validator 及其 committed adapter 路径拥有。recorder 不实现第二套 validation policy。

## Assessment 契约

artifact 名称为 `system2_replay_snapshot_assessment`，schema 为
`harness.system2_replay_snapshot_assessment.v1`，scope 为
`local_cas_snapshot_recomputation`。payload 记录 replay run id、Invocation digest、开始/结束时间、固定
`text2env.replay@1.0.0` / `max_attempts=2`、完整 `Text2EnvReplayInput`、按名称排序的五项依赖，以及 runtime
evidence、runtime asset snapshot manifest、validation report 三个 refs 和 status / fail / not-run 计数。

它用 `LocalArtifactStore(acquisition.artifact_root)` 写回 acquisition 的同一本地 CAS。返回 ref 必须是
canonical CAS URI；随后逐字节重读并通过严格模型。payload 不带宿主机路径，也不写 `source_kind`、world
state、state delta、receipt、decision 或 `publishable`。同一 acquisition 重复调用得到相同 ref、bytes 与
snapshot result，因此幂等性是内容寻址意义上的本地幂等性。

## 如何解释状态与错误

`PASS`、`FAIL`、`INCOMPLETE` 都会正常落入 assessment；它们描述的是对调用方提供的本地 CAS evidence 所做
的重算，不是 recorder 新作出的决策。三个固定错误 reason 划开了失败阶段：

- `snapshot_validation_failed`：现有 snapshot validator 未能完成重算；
- `assessment_publish_failed`：assessment 未能写入本地 CAS；
- `assessment_verification_failed`：ref、重读 bytes 或严格 payload 复核失败。

PASS 与 FAIL 的测试走未经替换的 committed snapshot adapter；INCOMPLETE 仍走同一 adapter，但由测试控制
raw-validator 的 `not_run` 分支。这个受控分支不是一次新的部署物理测量。

## 权威边界

测试输入是 local typed semantic CAS fixture，没有启动 simulator，也没有执行一次真实 deployed dynamic
replay。固定 qualification 只资格化 replay factory 的固定 wiring，不能授权当前动态 input、acquisition、
report 或 outcome；assessment 中出现 `PASS` 也不能解释为 physical-success claim。

Local Snapshot Assessment 不是 `fresh_observation` 或 `WorldFactEvidence`，也不是完整 Validate v2。它不生成
trusted / portable receipt、validation decision、`publishable` 结论或 portable authority，不推进 System 2
world state。path-free canonical payload 只维持同一本地 CAS 内的内容身份，不升级上游来源权威。

10 项定向测试覆盖 77 statements / 4 branches，语句与分支均为 100%；六文件邻接回归为 300 passed。精确
文件摘要、RED→GREEN 类别、复跑命令与主张矩阵见
[`docs/evidence/system2-replay-snapshot-assessment-20260902.md`](../../docs/evidence/system2-replay-snapshot-assessment-20260902.md)。

证据状态：2026-09-02 已验证 acquisition-only 的本地 snapshot assessment 记录；Fresh Observation、
WorldFactEvidence、物理成功、完整 Validate v2、portable authority、发布资格与 world-state advancement 仍未
由本切片验证。
