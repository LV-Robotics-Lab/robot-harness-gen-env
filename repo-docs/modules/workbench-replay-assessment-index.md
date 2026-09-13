# Workbench Replay Assessment 持久索引

历史说明：本页未接路由的 Python read-model 及其旧 System 2 依赖已在 canonical C13 退役，
可从 `42d5bb7` Git 历史恢复；不迁移成第二个默认 workflow。当前实现见
[Canonical x2env](canonical-x2env.md)。下文不是当前调用配方。

`demo.harness_replay_assessment` 是 Workbench 的本地 replay-assessment read-model seam。它把一次已经完成的
`System2ReplayEvidenceAcquisitionResult` 交给现有 Recorder 与 artifact-only Verifier，再把窄 view 以 immutable
row 写入 acquisition 所属的共享 SQLite journal。之后的新进程可以只用 run ID、同一 SQLite、同一 CAS 与独立
scratch 重验并读取该 view。

这还是 Python 后端能力：当前 `demo/app.py` 没有 assessment route，浏览器也没有对应页面或交互。不要把它与现有
[Workbench 运行活动板](workbench-run-activity.md)混为一谈；活动板投影 event feed，本模块索引并重验一个 frozen
replay snapshot assessment view。

## 两个公开动作

```text
record(acquisition)
  -> durable replay authority preflight
  -> public assessment Recorder
  -> public artifact-only Verifier
  -> durable authority confirmation
  -> immutable index commit last
  -> WorkbenchReplayAssessmentView

inspect(run_id)
  -> immutable index + current durable authority
  -> same-CAS artifact-only recomputation
  -> current view == frozen view
  -> WorkbenchReplayAssessmentView
```

`record` 接收 acquisition 而不是 assessment ref，是为了把 recorder、verifier、SQLite/CAS binding 与 commit-last
indexing 收进一次深命令。否则调用方必须先创建一个尚未索引的 assessment，再自行证明它属于同一 durable replay。
模块调用 [System 2 Replay Snapshot Assessment](system2-replay-snapshot-assessment.md) 的两个公开 seam，不复制
snapshot policy，也没有供调用方替换 Recorder、Verifier 或 adapter 的接口。

`inspect` 不需要原 acquisition。它从 SQLite 取得 index、Invocation、terminal RunState 与完整 EventPage，从 CAS
取得 assessment closure，再重新执行 artifact-only snapshot-summary verification；所以进程重启不会丢失 read
model authority。

## `record` 的 commit-last 边界

模块先 canonicalize 配置的 journal、CAS 与 scratch。scratch 与 CAS 必须分离，journal 必须位于 CAS 外。
acquisition 自带的 journal/CAS locator 会先在 preflight 与构造配置精确对齐；后续 Recorder 仍从完整
acquisition 取得 artifact root 以写入同一 CAS，但这些路径不会持久化到公开 view 或私有 index。

写 assessment 前，它要求 acquisition 的 exact typed `Invocation`、terminal `RunState`、完整 `EventPage` 与 SQLite
重读值相同，并把 terminal output strict 解析为 `Text2EnvReplayOutput`。Recorder 写入同一 CAS 后，Verifier 立即从
该 CAS 重算。提交 index 前再读一次 SQLite authority，并完成两组 binding：

- assessment 的 run ID、Invocation identity、Skill/version、attempt bound、时间、effective parameters 与
  dependencies 对到 durable Invocation/RunState；
- runtime evidence 与唯一 runtime asset snapshot 对到 terminal output，且 output 的全部 refs 都属于 terminal
  `RunState.artifacts`。

只有这些步骤都通过，SQLite index row 才最后写入。相同 run、相同 canonical index 重复写是幂等；相同 run 已有
不同 index 是 authority conflict。这里没有跨 SQLite/CAS 的事务或回滚承诺：commit-last 保护的是“不会先发布一个
未完成验证的索引”，不是删除此前内容寻址写入的 CAS blob。

## 冻结 view 与私有 authority identity

公开 `WorkbenchReplayAssessmentView` 是 path-free、浏览器精确的窄投影：

- run ID、Invocation digest、terminal run status 与 terminal event count/cursor；
- assessment 与 validation report 的 SHA-256/bytes；
- validation status、`fail_count` 与 `not_run_count`。

event cursor 与两个 byte count 用 canonical positive decimal string，避免 JavaScript 整数精度丢失；两个 validation
count 必须位于浏览器精确整数范围，status 与 counts 必须一致。view 不暴露完整 Event、artifact URI、artifact 内容
或宿主机路径。Harness run status 固定是 `succeeded`，但 snapshot validation 的 `PASS`、`FAIL`、`INCOMPLETE` 都是
正常 view；成功 run 不等于 validation PASS。

私有 index payload 是 canonical JSON，row 同时保存其 SHA-256。payload 包含公开 view，以及 exact typed
`Invocation`、exact typed `RunState`、完整 `EventPage` 投影各自的 SHA-256。它们让 `inspect` 发现窄 view 看不见的
durable-authority 漂移。私有 `invocation_sha256` 只是 canonical Invocation model 摘要，不是 Registry
`Invocation.invocation_digest` 的独立重算或 run-origin 认证。

## 读取与失败语义

`inspect` 依次校验 index row digest、strict schema、canonical bytes、三项 private authority digest、assessment
artifact-only recomputation、durable bindings，以及当前 view 与 frozen view 的 exact equality。四个公开异常把失败
压缩为稳定类别：

- `WorkbenchReplayAssessmentInputError`：调用参数或固定存储布局无效；
- `WorkbenchReplayAssessmentNotFoundError`：run 没有 index；
- `WorkbenchReplayAssessmentAuthorityError`：SQLite/CAS/acquisition/assessment/index identities 不一致；
- `WorkbenchReplayAssessmentUnavailableError`：所需本地 SQLite 或 CAS 操作无法完成。

## 已验证范围

验证命令运行在保留无关既有及并行修改的共享 dirty worktree。本轮只冻结/审查 implementation、test、evidence
MD/JSON、本文与 `repo-docs/README.md` 单行入口这六个路径；测试计数是该共享工作树快照上的观察值，不是 clean
baseline + slice 归因，也不是完整仓库干净或完整仓库测试通过的主张。

74 项定向测试覆盖 259 statements / 68 branches，语句与分支均为 100%；八文件邻接回归 454 passed，完整
`tests/demo` 成功记录为 416 passed。target 与邻接 suite 的 `ResourceWarning` error gate 干净；完整 demo 的严格
warning probe 仍报告一条来自既有 `test_app.py` file-response 的旁支 warning，因此没有“完整 demo warning clean”
主张。精确命令、冻结源码摘要与 companion JSON 见
[`docs/evidence/workbench-replay-assessment-index-20260903.md`](../../docs/evidence/workbench-replay-assessment-index-20260903.md)。

测试证明的是本地 typed semantic SQLite + CAS fixture 的 record/restart-inspect contract，不是一次真实 deployed
replay 或新的物理测量。本模块不新增 run Event，不修改 terminal RunState/Invocation，不产生 Fresh Observation、
`WorldFactEvidence`、receipt、完整 Validate v2、decision、`publishable` 或 portable authority，也不推进 System 2
state。固定 qualification 不授权当前 dynamic input、acquisition、assessment 或 outcome。

证据状态：2026-09-03 已验证同一本地 SQLite/CAS 下的 frozen replay-assessment view 与 restart 重验；HTTP route、
浏览器 UI、deployed replay、physical success、dynamic qualification、完整 Validate v2 和发布决策仍不在本切片内。
