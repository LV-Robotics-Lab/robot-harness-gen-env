# System 2 compile → replay 受信交接证据（2026-09-02）

配套结构化报告为
[`system2-replay-handoff-20260902.json`](system2-replay-handoff-20260902.json)，文件 SHA-256 为
`c4dace43e7b61e10f3c835b63f63848d08066db5cd9ee5b3f22fe0bb38070ca7`。

## 已验证接口

公开 seam 为：

```text
System2ReplayHandoff(artifact_store, scratch_root).prepare(
    compile_result=System2CompileTurnResult,
    runtime_config=RuntimeConfig,
) -> PreparedSystem2Replay
```

它只接受带非空 history authority 的 version-2、`succeeded` compile 结果。模块先从 CAS 复核
planner history authority 和 trusted tool receipt，再把 receipt 中的 Invocation、终态 RunState、
typed output、环境包闭包与内存中的 ToolResult 和最终 world state 交叉绑定。receipt 的 StateDelta 还会
从 history 的基态重放；只有重建出的终态与受信 lineage 相同时，写制品才会开始。

成功结果包含严格 `Text2EnvReplayInput`，并发布两份 path-free canonical JSON：

- `environment_package`：`harness.environment_package.v1`；
- `text2env_request_provenance`：`harness.text2env_request_provenance.v1`，绑定 request、seed、
  compile run / Invocation、trusted receipt、history authority、world-state 摘要与环境包 ref。

两份制品都使用 canonical CAS locator，并在写入后重新读取逐字节比对。相同输入和相同 CAS 内容复制到
两个 store 后，返回对象、环境包 bytes 与 provenance bytes 完全相同；provenance 不含宿主路径。

## Fail-closed 攻击证据

测试覆盖 malformed result/history、缺失或损坏的 Invocation、RunState、package member、内存 ToolResult
漂移、history 与 receipt 跨回合交换，以及 CAS 在 `put` 后首次 `resolve` 时发生的 byte drift。上述情况均
在无可用输出的状态下停止。

同基态 A/B 攻击另行锁住一条较隐蔽的错配：A、B 两份 receipt 都各自有效，且两次 compile 从同一基态
和冻结时间开始；攻击者把 A 的 ToolResult 放进含 A/B entries、但终态取 B 的 history。模块会把 A 的
StateDelta 应用到 B history 的基态，并因无法重建 B 终态而拒绝 `compile_result_mismatch`。

## 复现

核对实现与测试身份：

```bash
sha256sum \
  self_improving/system2_replay_handoff.py \
  tests/self_improving/test_system2_replay_handoff.py
wc -l \
  self_improving/system2_replay_handoff.py \
  tests/self_improving/test_system2_replay_handoff.py
wc -c \
  self_improving/system2_replay_handoff.py \
  tests/self_improving/test_system2_replay_handoff.py
```

预期分别为：

- implementation：317 lines、12,345 bytes、SHA-256
  `356e9bd81d2e8b91aaa7d3e62461b4662e71719bec510691c352f2a0617e591b`；
- test：698 lines、23,900 bytes、SHA-256
  `f7d3b99c422cdb51992b67dd922f78d444b44af5a9b656443cade87fd7426a55`。

定向测试和 100% 覆盖门：

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider \
  tests/self_improving/test_system2_replay_handoff.py \
  --cov=self_improving.system2_replay_handoff \
  --cov-branch --cov-report=term-missing --cov-fail-under=100
```

预期为 24 passed；134 statements、20 branches，statement/branch 均为 100%。邻接 System 2 回归：

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider \
  tests/self_improving/test_system2_compile_turn.py \
  tests/self_improving/test_system2_replay_handoff.py \
  tests/self_improving/harness/test_system2_context.py \
  tests/self_improving/harness/test_system2_dispatcher.py \
  tests/self_improving/harness/test_system2_qwen_local.py \
  tests/self_improving/harness/test_system2_history.py \
  tests/self_improving/harness/test_system2_planner.py \
  tests/self_improving/harness/test_system2_domain.py
```

预期为 428 passed。静态门：

```bash
ruff check self_improving/system2_replay_handoff.py \
  tests/self_improving/test_system2_replay_handoff.py
ruff format --check self_improving/system2_replay_handoff.py \
  tests/self_improving/test_system2_replay_handoff.py
git diff --no-index --check /dev/null \
  self_improving/system2_replay_handoff.py
git diff --no-index --check /dev/null \
  tests/self_improving/test_system2_replay_handoff.py
```

两个 no-index 命令因文件与 `/dev/null` 不同而返回 1，但都没有 whitespace diagnostics；Ruff、
format 与 whitespace gate 均通过。独立 review 的 P0/P1/P2 findings 为 0/0/0。

## 主张边界

这是 prepare-only 交接，不调用 `ReplayApplication`，不启动 simulator，不采集 runtime evidence，
不执行 validate，不生成 portable run receipt，也不作物理通过或 `publishable` 判断。

provenance 使用模块私有的 frozen strict payload；它的 ArtifactRef 符合
`Text2EnvValidateV2Input.request_provenance` 已预声明的 schema/media/CAS 契约，但本切片没有注册新的公开
payload schema。若环境包写入成功、第二份 provenance 写入失败，第一个不可变 CAS 对象可能保留；调用方
仍只收到 `artifact_write_failed`，不会收到部分 `PreparedSystem2Replay`。删除首个对象反而可能破坏共享
content-addressed 内容，因此这里不承诺回滚。

实现与测试都是 `self_improving/harness/**` 的同级文件，没有改变 replay qualification 的源码哈希集合；
因此已检查的 replay qualification source identity 保持不变。
