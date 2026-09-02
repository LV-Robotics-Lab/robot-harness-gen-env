# System 2 replay snapshot assessment artifact-only verifier 证据（2026-09-02）

配套结构化报告为
[`system2-replay-snapshot-assessment-verifier-20260902.json`](system2-replay-snapshot-assessment-verifier-20260902.json)，
文件 SHA-256 为 `05fe8ba764d761c4e512b83b1cf2ba83134c1417b3e173e8ae65b67154eeba85`。本证据以 commit
`cbeb12d3b61a3439778dee05cb68a406f13d41c1` 为新增 artifact-only verifier 前的冻结基线。

原 recorder 的不可变冻结证据仍保留在
[`system2-replay-snapshot-assessment-20260902.md`](system2-replay-snapshot-assessment-20260902.md)；
本文件只记录其后的 verifier 增量与累计回归结果。

## 已验证的两个公开接口

记录接口保持为：

```text
System2ReplaySnapshotAssessmentRecorder(
    scratch_parent: Path,
).record(
    acquisition: System2ReplayEvidenceAcquisitionResult,
) -> System2ReplaySnapshotAssessmentResult
```

`record` 仍只接收 acquisition，不接收调用方组装的 validation result。它内部直接构造未经修改的
`System2ReplaySnapshotValidator`，并把同一 acquisition 交给 `recompute`。返回值只含 canonical assessment ref
与该次 `System2ReplaySnapshotValidationResult`。

新增的 artifact-only 验证接口为：

```text
System2ReplaySnapshotAssessmentVerifier(
    artifact_store: LocalArtifactStore,
    scratch_parent: Path,
).verify(
    assessment: ArtifactRef,
) -> VerifiedSystem2ReplaySnapshotAssessment
```

`verify` 方法只接收 assessment ref；没有 acquisition、validation result 或 adapter 注入参数。返回值固定为三项：
原 assessment ref、严格解析后的 `System2ReplaySnapshotAssessment` record，以及裸的
`validate_v2_snapshot.SnapshotValidationResult`。这里的裸结果不是 acquisition-aware
`System2ReplaySnapshotValidationResult`。

## Artifact-only 同 CAS 验证

verifier 先要求 assessment ref 的 name、media type、schema 与
`artifact://sha256/<digest>` URI 全部精确，再从构造时传入的同一个 `LocalArtifactStore` 读取 payload。payload
必须通过 strict model validation，而且必须逐字节等于该 model 再序列化所得的 canonical JSON；仅仅能解析的
缩进 JSON 会被拒绝。

严格 record 的内部一致性还要求：

- replay 起止时间都是 UTC aware datetime，且开始不晚于结束；
- dependencies 恰为按稳定名称排序的五项 replay 依赖，每项 version 都是 `1`；
- `text2env.replay.runtime_assets` digest 与 runtime asset snapshot manifest digest 相同；
- package catalog、package manifest、runtime evidence、runtime asset snapshot 与 validation report refs 具有各自
  要求的 JSON media type、schema、canonical URI；后三项还要求固定 artifact name。

这一步只证明 record 是 canonical、typed 且内部自洽。replay run id、Invocation digest 与时间没有向原始 run
做独立回查；四项非 runtime 依赖 identity 也没有向其 producer 或 Registry 独立溯源。它们仍是记录保留的 typed
metadata，不是 verifier 新建立的 origin authority。

通过 record 门后，verifier 要求 recorded validation report 已经存在于同一 CAS；然后从 record 取出 environment
package、runtime evidence 与 runtime asset snapshot manifest，在独立且不与 CAS 相交的 scratch 下内部构造真实
`ValidateV2SnapshotAdapter` 重算。scratch 与 CAS 相同、互为祖先或互为后代都会被拒绝。没有调用方可注入的
adapter 替身。

成功只在重算的 validation report ref、validation status、`fail_count`、`not_run_count` 与 record 四项精确相等时
返回。换言之，`VerifiedSystem2ReplaySnapshotAssessment` 只验证该 package/runtime/snapshot closure 在该本地 CAS
中的 snapshot summary；它不认证其余记录字段的外部来源。

## 记录路径与幂等性

recorder 在内部 snapshot 重算成功后，生成 schema
`harness.system2_replay_snapshot_assessment.v1`、scope
`local_cas_snapshot_recomputation` 的 canonical JSON。记录包含 replay run id、Invocation digest、UTC 起止时间、
`text2env.replay@1.0.0`、`max_attempts == 2`、完整 `Text2EnvReplayInput`、五项依赖，以及 runtime evidence、
runtime asset snapshot、validation report 和状态计数。

assessment 通过 `LocalArtifactStore(acquisition.artifact_root)` 写回 acquisition 的同一本地 CAS；recorder 随后
重读 exact bytes 并严格复核。payload 不记录宿主机路径，也不包含 `source_kind`、world state、state delta、
receipt、decision 或 `publishable` 字段。

同一 acquisition 重复记录会得到完全相同的 assessment ref、payload bytes 与 snapshot validation result。同一
assessment ref、CAS 与 scratch 重复 artifact-only 验证，也会得到相等的 verified record/result。对独立且起初
为空的 scratch，成功或已经进入 adapter 的受测路径不会遗留新的 attempt 目录；overlap 或 missing scratch 不主张
“为空”。这是单一本地 CAS 中的内容寻址确定性与幂等性，不是跨机器 portable authority 或事务性回滚承诺。

## 正常结果与固定失败语义

`PASS`、`FAIL`、`INCOMPLETE` 都是 recorder 与 artifact-only verifier 的正常结果。PASS 与 FAIL 使用未经替换的
committed snapshot adapter 路径；INCOMPLETE 仍经过同一 adapter，但测试用受控 raw-validator `not_run` 分支
生成。这三种状态都只是 snapshot 重算摘要，不是新的 validation decision 或 physical-success 结论。

同一个 `System2ReplaySnapshotAssessmentError` 公开六个固定 reason，按阶段分成两组。recorder 的三个 reason
保持不变：

- `snapshot_validation_failed`：内部 acquisition-aware snapshot recomputation 不能完成；
- `assessment_publish_failed`：canonical assessment 不能写入 acquisition CAS；
- `assessment_verification_failed`：recorder 的 ref header、CAS readback、exact bytes 或严格 typed payload 复核失败。

verifier 新增且固定的三个 reason 为：

- `assessment_invalid`：assessment ref、CAS read/strict parse、canonical bytes、UTC/依赖/runtime-asset binding 或
  artifact-ref 结构不合法；
- `assessment_recompute_failed`：pre-existing report 或重算输入不可解析、scratch 配置无效、scratch/CAS 相交，或
  真实 snapshot adapter 不能完成重算；
- `assessment_mismatch`：重算所得 report ref、status、fail count 或 not-run count 任一项与记录不一致。

## TDD、冻结身份与验证

最初 recorder 切片的第一条 RED 是公开模块尚不存在时的 `ModuleNotFoundError`。在该基础上，新增
artifact-only verifier 的普通 RED→GREEN 批次覆盖：

- verifier 的 ref-only 方法签名、三字段 verified result 与裸 `SnapshotValidationResult`；
- exact ref header、strict model、canonical bytes 和固定 artifact-ref 结构；
- UTC 顺序、五依赖名称/顺序/version 与 runtime-assets digest 绑定；
- pre-existing report、package/runtime/snapshot closure 与真实 committed adapter 重算；
- report ref/status/fail/not-run 四项逐项 mismatch；
- scratch 不存在及 scratch/CAS 同址、祖先、后代三类 overlap；
- PASS、FAIL、INCOMPLETE 正常返回与重复 artifact-only 验证幂等；
- constructor exact store type、Path 类型与 compile producer 的 `effective_asset_catalog` 名称兼容。

原 recorder 的 acquisition-only seam、三项记录错误、canonical same-CAS payload、内容寻址幂等和三种正常状态
测试全部保留。最终冻结身份为：

- implementation：413 lines、16,526 bytes、SHA-256
  `2e309456be122590fb9aff1dbd4056b044061bb7aa61bda2c47a586e42f23bbb`；
- test：1,261 lines、47,566 bytes、SHA-256
  `c10907aab03dbbab46059018f281c915b604e7eef74692c0b8ca8feacb0f408e`。

定向功能与 100% coverage 门：

```bash
pytest -q -p no:cacheprovider \
  tests/self_improving/test_system2_replay_snapshot_assessment.py \
  --cov=self_improving.system2_replay_snapshot_assessment \
  --cov-branch --cov-report=term-missing --cov-fail-under=100
```

结果为 41 passed、0 failed；143 statements、22 branches，statement/branch 均为 100%。六文件邻接回归：

```bash
pytest -q -p no:cacheprovider \
  tests/self_improving/test_system2_replay_snapshot_assessment.py \
  tests/self_improving/test_system2_replay_snapshot_validation.py \
  tests/self_improving/test_system2_replay_evidence_acquisition.py \
  tests/self_improving/test_system2_replay_input_promotion.py \
  tests/self_improving/test_system2_replay_handoff.py \
  tests/self_improving/test_validate_v2_snapshot.py
```

结果为 331 passed。静态与 whitespace 门：

```bash
ruff check self_improving/system2_replay_snapshot_assessment.py \
  tests/self_improving/test_system2_replay_snapshot_assessment.py
ruff format --check self_improving/system2_replay_snapshot_assessment.py \
  tests/self_improving/test_system2_replay_snapshot_assessment.py
git diff --check -- \
  self_improving/system2_replay_snapshot_assessment.py
git diff --check -- \
  tests/self_improving/test_system2_replay_snapshot_assessment.py
git diff --check -- \
  repo-docs/modules/system2-replay-snapshot-assessment.md repo-docs/README.md
git diff --no-index --check /dev/null \
  docs/evidence/system2-replay-snapshot-assessment-verifier-20260902.md
git diff --no-index --check /dev/null \
  docs/evidence/system2-replay-snapshot-assessment-verifier-20260902.json
```

Ruff、format 与 tracked diff check 均通过。两个 evidence no-index 命令因新文件与 `/dev/null` 不同而按预期
返回 1，但没有 whitespace diagnostics。两轮独立最终 review 的 P0/P1/P2 findings 均为 0/0/0。

## 主张边界

测试使用本地 typed semantic CAS fixture。本切片没有启动 simulator、执行真实 deployed dynamic replay 或产生
新的物理测量。artifact-only verifier 调用的是 snapshot adapter 重算，不是完整 Validate v2；即使返回 PASS，
也不是 physical-success claim。

Local Snapshot Assessment 不是 `fresh_observation`，也不是 `WorldFactEvidence`。recorder 与 verifier 都不生成
receipt、validation decision、`publishable` 结论或 portable authority，也不触发 System 2 world-state
transition。replay factory 的 checked-in 固定 qualification 只资格化固定 wiring；它不授权这里的动态 input、
acquisition、report 或 outcome。

path-free canonical assessment 与 artifact-only verified result 只维持同一本地 CAS 内的记录身份及 snapshot
summary 对账。它们不会把 run id、Invocation digest、时间、四项非 runtime 依赖或上游 local acquisition 的来源
边界升级成 independently verified origin，更不会形成可携带到另一台机器的权威。
