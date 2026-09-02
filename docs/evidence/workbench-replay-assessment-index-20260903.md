# Workbench replay assessment index 证据（2026-09-03）

配套结构化报告为
[`workbench-replay-assessment-index-20260903.json`](workbench-replay-assessment-index-20260903.json)，
文件 SHA-256 为 `c40a1b1a139379e3db447850b8f768e38124faa0294dc4ad36af6f0e7022118d`。
本证据以 commit `e046731ef3017b4aa3e646927105df30640bf2b7` 为新增 Workbench replay
assessment index 前的冻结基线。

## 已验证接口与当前表面

公开 Python seam 为：

```text
WorkbenchReplayAssessment(
    journal_path: Path,
    artifact_root: Path,
    scratch_parent: Path,
    busy_timeout_ms: int = 5000,
).record(
    acquisition: System2ReplayEvidenceAcquisitionResult,
) -> WorkbenchReplayAssessmentView

WorkbenchReplayAssessment(...).inspect(
    run_id: UUID,
) -> WorkbenchReplayAssessmentView
```

`record` 接收完整 acquisition，是刻意的深接口：模块在一次命令里拥有公开
`System2ReplaySnapshotAssessmentRecorder`、公开 artifact-only
`System2ReplaySnapshotAssessmentVerifier` 与 commit-last index 的编排，调用方不需要先生成一个尚未索引的
assessment ref 再自行绑定 SQLite。`inspect` 只接收 run ID，因此新进程可只凭相同 journal、CAS 与 scratch
配置重读，不依赖原 acquisition 对象或进程内缓存。

本切片只提供 Python record/inspect seam 与 read model；`demo/app.py` 尚未接入 HTTP route，浏览器也没有新增
assessment 页面、卡片或交互。

## `record`：先对账，再记录，最后提交索引

```text
exact typed acquisition
  -> canonical configured journal / CAS / scratch preflight
  -> shared SQLite Invocation + terminal RunState + complete EventPage
  -> acquisition / durable authority / strict Text2EnvReplayOutput exact match
  -> public assessment Recorder
  -> public artifact-only Verifier against the same CAS
  -> immediate SQLite authority re-read + exact match
  -> assessment metadata and runtime/snapshot refs bound to durable authority
  -> narrow WorkbenchReplayAssessmentView
  -> immutable SQLite index row committed last
```

前置对账要求 acquisition 的 journal 与 artifact root 精确属于当前 Workbench 配置，并且 acquisition 携带的
`Invocation`、terminal `RunState`、完整 `EventPage` 与 SQLite 重读结果完全相同。terminal output 必须能 strict
解析为 `Text2EnvReplayOutput`。这使 stale acquisition 在 recorder 写 CAS 之前失败。

Recorder 与 Verifier 都通过各自公开接口调用；本模块不复制 snapshot validation policy，也没有 adapter、recorder
或 verifier 注入 seam。真实 `ValidateV2SnapshotAdapter` 仍拥有 snapshot 判定，assessment verifier 只负责重算
编排与 report ref、status、`fail_count`、`not_run_count` 四项精确对账。

在索引提交前，模块再次读取并精确比较 SQLite authority，然后把 verified assessment 的 run ID、Invocation
identity、Skill/version、尝试上限、起止时间、effective parameters 与 dependencies 对到 durable
`Invocation`/`RunState`。assessment 的 runtime evidence 与唯一 runtime asset snapshot 还必须和 strict terminal
output 相同，而且 terminal output 的全部 refs 都必须属于 `RunState.artifacts`。

索引写在最后。相同 run 与完全相同 canonical index 重复 `record` 时幂等返回；相同 run 已有不同 index 时固定为
authority conflict。commit-last 不等于 SQLite 与 CAS 之间存在跨存储事务，也不承诺索引写失败时回滚已经内容寻址
写入的 CAS 对象。

## `inspect`：重启后从冻结索引回到当前 authority

```text
run_id
  -> canonical index row + row digest
  -> current SQLite Invocation / terminal RunState / complete EventPage
  -> private frozen authority digests exact match
  -> reconstruct exact assessment ref
  -> same-CAS artifact-only verification
  -> durable run/output bindings repeated
  -> current projection exactly equals frozen public view
```

SQLite 表 `workbench_replay_snapshot_assessments` 以 run ID 为主键，保存 canonical JSON BLOB 与该 BLOB 的
SHA-256。私有 payload 只有公开 view，以及 exact typed `Invocation`、exact typed `RunState`、完整 `EventPage`
投影各自的 SHA-256；不保存 acquisition 路径、原始模型 payload 或 artifact 内容。`inspect` 先校验 row digest、
strict schema 与 canonical bytes，再用三项私有 digest 检查 durable authority 没有发生 view 看不见的协调漂移。

这些私有 digest 是本模块对 canonical typed models/projection 的内容摘要；其中 `invocation_sha256` 不是 Registry
维护的 `Invocation.invocation_digest` 的独立重算，也不认证 deployed run origin。随后 verifier 从同一本地 CAS
重算 snapshot summary；当前投影还必须逐字段等于冻结 view，才会返回。

## 浏览器精确的窄 view

`WorkbenchReplayAssessmentView` 只公开：

- schema、run ID、Invocation digest 与 terminal Harness run status；
- terminal event count，以及用 canonical positive decimal string 表示的 last event ID；
- assessment/report SHA-256，以及用 canonical positive decimal string 表示的两个 byte count；
- snapshot validation status、`fail_count` 与 `not_run_count`。

event count 固定不超过 200；validation counts 是浏览器可精确表示的非负整数，status 必须与 counts 自洽。大于浏览器
整数精度范围但仍可能属于 SQLite 范围的 event ID 与 byte count 以十进制字符串传递。view 不暴露宿主机路径、完整
Event、artifact URI 或 artifact 内容。

Harness run status 固定为 `succeeded`，但 snapshot validation 的 `PASS`、`FAIL`、`INCOMPLETE` 都是可检查的正常
view。成功 replay 终态不蕴含 snapshot validation 必须 PASS。

## 固定失败分类

- `WorkbenchReplayAssessmentInputError`：调用参数或固定存储布局不属于公开接口；
- `WorkbenchReplayAssessmentNotFoundError`：给定 run ID 没有 index row；
- `WorkbenchReplayAssessmentAuthorityError`：typed SQLite、CAS、acquisition、assessment 或 immutable index
  identities 不一致；
- `WorkbenchReplayAssessmentUnavailableError`：所需本地 SQLite/CAS 操作无法完成。

## 工作树与归因边界

这些命令是在保留无关既有及并行修改的共享 dirty worktree 中观察到的。本证据只冻结和审查以下六个路径：两个
implementation/test 文件、新增 evidence MD/JSON、新增 module guide，以及 `repo-docs/README.md` 的单行入口。
无关 dirty changes 均原样保留，没有纳入本切片。

因此下列结果描述的是该共享工作树快照上的实际测试，不归因为“clean baseline 加本切片”本身，也不声称完整仓库
干净或完整仓库测试套件已经通过。上文 baseline commit 只标识新增模块前的源码基点，不改变这条归因边界。

## 冻结身份与验证

冻结的实现与测试身份为：

- implementation：674 lines、27,005 bytes、SHA-256
  `7bb72c5fef033e52570249918dd957d721cd8a239802faa55dec57922421ffe2`；
- test：1,860 lines、63,971 bytes、SHA-256
  `4a2068a58bb9268422f21416fb43883f6075119ea4fa0c167d9ae087f347a64c`。

复核身份：

```bash
sha256sum \
  demo/harness_replay_assessment.py \
  tests/demo/test_harness_replay_assessment.py
wc -l -c \
  demo/harness_replay_assessment.py \
  tests/demo/test_harness_replay_assessment.py
```

定向功能、warning 与 coverage 门：

```bash
pytest -q tests/demo/test_harness_replay_assessment.py \
  --cov=demo.harness_replay_assessment \
  --cov-branch --cov-report=term-missing \
  -W error::ResourceWarning
```

结果为 74 passed、0 failed；259 statements、68 branches，statement/branch 均为 100%，没有
`ResourceWarning`。

精确八文件邻接回归：

```bash
pytest -q \
  tests/demo/test_harness_replay_assessment.py \
  tests/demo/test_harness_feed.py \
  tests/demo/test_harness_compile.py \
  tests/self_improving/test_system2_replay_snapshot_assessment.py \
  tests/self_improving/test_system2_replay_snapshot_validation.py \
  tests/self_improving/test_system2_replay_evidence_acquisition.py \
  tests/self_improving/harness/test_run_store.py \
  tests/self_improving/harness/test_event_journal.py \
  -W error::ResourceWarning
```

结果为 454 passed、0 failed，`ResourceWarning` error gate 干净。

完整 demo 回归的成功记录为：

```bash
pytest -q tests/demo
```

结果为 416 passed、0 failed，并保留普通 warnings。另以更严格 flag 复核：

```bash
pytest -q tests/demo -W error::ResourceWarning
```

这次仍为 416 passed、0 failed，但 warnings summary 有一条既存于本切片之外的
`PytestUnraisableExceptionWarning`：`tests/demo/test_app.py::test_artifact_route_serves_only_registered_job_files`
中的 `preview.png` file-response reader 包装了一条 `ResourceWarning`。因此这里不声称完整 demo suite warning
clean；warning-clean 主张只适用于上述 target 与八文件邻接门。

静态与 whitespace 门：

```bash
ruff check demo/harness_replay_assessment.py \
  tests/demo/test_harness_replay_assessment.py
ruff format --check demo/harness_replay_assessment.py \
  tests/demo/test_harness_replay_assessment.py
git diff --no-index --check /dev/null \
  demo/harness_replay_assessment.py
git diff --no-index --check /dev/null \
  tests/demo/test_harness_replay_assessment.py
python -m json.tool \
  docs/evidence/workbench-replay-assessment-index-20260903.json >/dev/null
rg -n -i \
  -e '/h[o]me/' -e '/U[s]ers/' -e 'jingxiang[@]' -e '100[.]112[.]' \
  -e 'api[_-]?[k]ey' -e 'authorization[:]' \
  -e 'bearer[[:space:]]+[A-Za-z0-9]' -e 'to[k]en' \
  -e 'pass[w]ord' -e 'sec[r]et' -e 'session[_ -]?[i]d' \
  -e 'remote[-]control' \
  docs/evidence/workbench-replay-assessment-index-20260903.md \
  docs/evidence/workbench-replay-assessment-index-20260903.json \
  repo-docs/modules/workbench-replay-assessment-index.md \
  repo-docs/README.md
git diff --check -- repo-docs/README.md
git diff --no-index --check /dev/null \
  docs/evidence/workbench-replay-assessment-index-20260903.md
git diff --no-index --check /dev/null \
  docs/evidence/workbench-replay-assessment-index-20260903.json
git diff --no-index --check /dev/null \
  repo-docs/modules/workbench-replay-assessment-index.md
```

Ruff、format、JSON parse 与 tracked README diff check 均通过。新增源码和文档的 no-index 命令按预期返回 1，
但没有 whitespace diagnostics；四个文档路径的隐私扫描 findings 为 0。冻结 implementation/test pair 的两轮独立
最终 review，P0/P1/P2 findings 都是 0/0/0；这项 review 结论不包含本 evidence/guide 文档。

## 主张边界

测试使用本地 typed semantic SQLite + CAS fixture，并调用公开 Recorder、公开 artifact-only Verifier 与真实
snapshot adapter 的 PASS/FAIL 路径；INCOMPLETE 由受控 raw-validator `not_run` 分支产生。本切片没有执行真实
deployed dynamic replay、启动 simulator 或产生新的物理测量，因而不证明 physical success。

Workbench index 不新增 run Event，不修改 terminal `RunState` 或 `Invocation`。它不产生 Fresh Observation、
`WorldFactEvidence`、receipt、完整 Validate v2、validation decision、`publishable` 结论或 portable authority，也
不推进 System 2 state。checked-in 固定 qualification 只资格化固定 factory wiring，不能授权本次 dynamic input、
acquisition、assessment 或 outcome。

本切片唯一建立的是：同一本地 SQLite authority 与同一本地 CAS 中的 frozen assessment view，在 record 与 restart
inspect 时保持所述 typed identities、runtime/snapshot bindings 和 snapshot-summary 重算一致性。
