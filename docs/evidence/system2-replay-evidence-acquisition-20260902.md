# System 2 dynamic replay evidence acquisition 证据（2026-09-02）

配套结构化报告为
[`system2-replay-evidence-acquisition-20260902.json`](system2-replay-evidence-acquisition-20260902.json)，
文件 SHA-256 为
`3e57971c001fe5f4e0035f91a6a85567fc9afee7913f95e5097d5fe88108e714`。

## 已验证接口

公开 seam 为：

```text
System2ReplayEvidenceAcquirer(
    application_settings=ReplayApplicationSettings,
).acquire(
    promoted=PromotedSystem2ReplayInput,
) -> System2ReplayEvidenceAcquisitionResult
```

acquirer 要求 fresh state root，并要求 promotion destination、settings 中的 evidence root 与 factory
application 暴露的 artifact root 是同一个 canonical 目录。它先从这个 destination CAS 重读并严格解析
canonical `EnvironmentPackage`，再 resolve 其中的 catalog 和 package-manifest refs；随后只通过正式
`create_replay_application(settings)` 组装 `text2env.replay@1.0.0` application，并对动态 input 调用一次
`replay`。

返回前会读取两次 terminal `RunState` 与 `Invocation`、读取一次完整 `EventPage`，并对账 descriptor、
Invocation 的动态输入和五个 dependency 名、event envelopes、终态 artifacts、严格 typed output，以及 fresh
state root 下精确的 canonical `harness.sqlite3` 普通文件。已开始 run 的 state/event artifacts 必须在
destination CAS 中可解析，重复 refs 只解析一次。

`succeeded` 返回严格 `Text2EnvReplayOutput`；`blocked` 和 `failed` 是正常终态，返回
`typed_output=None`，不会伪造成功。attempt 0 只允许 preflight `blocked` / `failed`，且没有 Invocation。
持久化本身失败时，绑定同一 run id 的 terminal state 最多作为 error diagnostic，不会作为成功结果返回。

## 资格关系与部署要求

checked-in replay qualification 的固定 can-on-plate 案例只授权既定 factory wiring 和
`text2env.replay@1.0.0` descriptor。它不把本次动态 `PromotedSystem2ReplayInput` 或动态结果变成已
qualification 的事实；本切片也没有重新证明动态 dependency digests 等于固定案例。

测试在模块内部替换 `create_replay_application` 这个外部边界，以验证 acquisition 与持久证据对账 contract。
仓库没有提交可执行的部署 CAS、runtime 与工具 fixture，因此本轮真实 deployed dynamic replay **NOT RUN**。
要完成部署正向运行，仍须同时提供固定资格绑定的外部 63-ref evidence CAS、解释器与运行工具、RoboTwin
资产、runtime capability、delegated cgroup 和新的 state root；promotion destination 必须就是该部署
application 使用的 evidence CAS，而不是另一个 package-only 空目标。

package-only promotion 没有复制 request provenance、compile receipt、history authority、qualification
或完整 authority closure，所以它不是可移植、离线可验的运行授权。

## TDD 与验证

第一条 RED 是目标模块尚不存在时的 `ModuleNotFoundError`。随后普通 contract REDs 逐步锁住 attempt-0
终态、durable state/journal、`EventPage` 结构与 identity、state/event/blocker/output artifact 绑定、
environment/package media 与 canonical JSON、event-only artifacts 和重复 ref 去重；最终实现与测试身份为：

- implementation：474 lines、19,359 bytes、SHA-256
  `037e7eecea22a070995b2207fe8e057f3ed51a2ebfd657373bda6c2afa6f2eea`；
- test：1,759 lines、63,338 bytes、SHA-256
  `70f543be5f7a4b192aaa994463d23cc68b490c98eb09fe0691c62486062e703f`。

定向功能与 100% coverage 门：

```bash
pytest -q -p no:cacheprovider \
  tests/self_improving/test_system2_replay_evidence_acquisition.py \
  --cov=self_improving.system2_replay_evidence_acquisition \
  --cov-branch --cov-report=term-missing --cov-fail-under=100
```

结果为 71 passed；194 statements、72 branches，statement/branch 均为 100%。九文件邻接回归：

```bash
pytest -q -p no:cacheprovider \
  tests/self_improving/test_system2_replay_evidence_acquisition.py \
  tests/self_improving/test_system2_replay_input_promotion.py \
  tests/self_improving/test_system2_replay_handoff.py \
  tests/self_improving/harness/test_replay_application.py \
  tests/self_improving/harness/test_replay_dependencies.py \
  tests/self_improving/harness/test_text2env_replay_handler.py \
  tests/self_improving/harness/test_artifacts.py \
  tests/self_improving/harness/test_package_store.py \
  tests/self_improving/harness/test_event_journal.py
```

结果为 363 passed。静态门：

```bash
ruff check self_improving/system2_replay_evidence_acquisition.py \
  tests/self_improving/test_system2_replay_evidence_acquisition.py
ruff format --check self_improving/system2_replay_evidence_acquisition.py \
  tests/self_improving/test_system2_replay_evidence_acquisition.py
git diff --no-index --check /dev/null \
  self_improving/system2_replay_evidence_acquisition.py
git diff --no-index --check /dev/null \
  tests/self_improving/test_system2_replay_evidence_acquisition.py
```

两个 no-index 命令因文件与 `/dev/null` 不同而返回 1，但没有 whitespace diagnostics；Ruff、format 与
whitespace gate 均通过。两轮独立最终 review 的 P0/P1/P2 findings 均为 0/0/0。

## 主张边界

本证据只证明模块内 factory 外部边界的普通 contract 测试与本地 reconciliation。它不证明真实部署动态
replay 已运行，不声称 simulator 或 physical validation 通过，不执行 validate v2，不产生 `publishable`
决定，不推进 System 2 world state，也不产生 portable offline authority。一次动态 `succeeded` 终态只说明
application 与持久记录在这个 seam 下成功对账，不能提升为上述任一结论。
