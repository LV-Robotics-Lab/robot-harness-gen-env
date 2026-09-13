# System 2 replay-input package-only CAS promotion 证据（2026-09-02）

配套结构化报告为
[`system2-replay-input-promotion-20260902.json`](system2-replay-input-promotion-20260902.json)，
文件 SHA-256 为
`8257a3816f4367b884513e4d2b19339e985091d6b875509badf30d81e38d85ca`。

## 已验证接口

唯一 public execution seam 为：

```text
System2ReplayInputPromoter(
    source_artifact_store=LocalArtifactStore,
    replay_artifact_store=LocalArtifactStore,
    scratch_root=Path,
).promote(
    compile_result=System2CompileTurnResult,
    prepared=PreparedSystem2Replay,
) -> PromotedSystem2ReplayInput
```

它在两个互不重叠的同机本地 CAS root 之间复制 replay package closure。返回值保留原
`Text2EnvReplayInput` 与 canonical environment-package ref，并指明 destination artifact root；源 CAS
随后不可用时，destination 中的 package closure 仍可独立物化。

## Source authority 与稳定 staging

promotion 先用构造时固定的 source store 重跑 `System2ReplayHandoff.prepare`，runtime config 取自 supplied
`PreparedSystem2Replay`。重跑结果必须与 supplied `PreparedSystem2Replay` exact-match；因此 compile
result、history authority、trusted receipt、终态与 request provenance 的交叉绑定会在 source 再验一次。

随后模块从 source CAS 按 manifest 物化 package，并把 staging package 重新发布回 source `PackageStore`；
重发得到的 manifest 必须与原 ref 完全相同。canonical `EnvironmentPackage` JSON bytes、typed catalog 与
catalog digest 也会重验。上述 source 验证和独立 scratch staging 全部完成、artifact roots 再次稳定后，
才发生第一次 destination write。

## 精确 closure、提交顺序与回读

destination 恰好得到七个 logical refs：

1. canonical `EnvironmentPackage` JSON；
2. asset catalog；
3. package manifest；
4. `request.txt`；
5. `scene_spec.json`；
6. `resolved_scene.json`；
7. `generated_scene.py`。

写入顺序是 package manifest 及其四个 exact members、asset catalog、最后 canonical environment-package
JSON。每一步产生的 ref 都必须 exact-match source；环境包 JSON 是 commit-last 对象。写完后模块重读
environment JSON 与 catalog、重新解析 typed replay input，并从 destination manifest 再物化一次 package。
只有这些检查全部通过才返回成功。

操作是 additive 且 idempotent：相同输入再次执行返回相同结果，不增加新的 content-addressed 对象。失败若
发生在 destination 已写入部分 immutable objects 之后，可能留下没有成功返回 authority 的不可达前缀；
方法不会返回部分成功，也不会删除或回滚可能由其他 ref 共享的 CAS 对象。

## Artifact-root 与文件系统边界

构造时会保存 source/destination 的 canonical roots，并据此建立私有 frozen stores。caller 持有的两个
store handle 若在 prepare、staging、destination write 或 verification 的阶段边界发生 root 漂移，操作会
fail closed；source、destination 与 scratch 也必须互不重叠，scratch 路径不能含 symlink component。

该结论的威胁模型是 trusted same-host local filesystem。实现没有在整个操作期持有 directory fd、文件系统
lease 或等价锁，因此不承诺抵御 active same-UID actor 在检查与使用之间并发替换 filesystem path。阶段边界
root 检查不能改写成这类并发路径攻击防护。

## 复现

核对实现与测试身份：

```bash
sha256sum self_improving/system2_replay_input_promotion.py tests/self_improving/test_system2_replay_input_promotion.py
wc -l self_improving/system2_replay_input_promotion.py tests/self_improving/test_system2_replay_input_promotion.py
wc -c self_improving/system2_replay_input_promotion.py tests/self_improving/test_system2_replay_input_promotion.py
```

预期分别为：

- implementation：335 lines、14,062 bytes、SHA-256
  `0d6d82e021b06a8db7eb50ec3650c73ce701ea99d9be5a8908cc17f95ad3f1f2`；
- test：1,025 lines、38,378 bytes、SHA-256
  `c86836c3a03b2caf70ec94a23c2df7cc92d040039122c5fbc5920692fc9e6b80`。

定向功能与 100% coverage 门：

```bash
pytest -q -p no:cacheprovider tests/self_improving/test_system2_replay_input_promotion.py --cov=self_improving.system2_replay_input_promotion --cov-branch --cov-report=term-missing --cov-fail-under=100
```

预期为 47 passed；169 statements、36 branches，statement/branch 均为 100%。System 2 邻接回归：

```bash
pytest -q -p no:cacheprovider tests/self_improving/test_system2_compile_turn.py tests/self_improving/test_system2_replay_handoff.py tests/self_improving/test_system2_replay_input_promotion.py
```

预期为 77 passed。CAS、`PackageStore`、replay dependencies 与本切片回归：

```bash
pytest -q -p no:cacheprovider tests/self_improving/harness/test_artifacts.py tests/self_improving/harness/test_package_store.py tests/self_improving/harness/test_replay_dependencies.py tests/self_improving/test_system2_replay_input_promotion.py
```

预期为 89 passed。静态门：

```bash
ruff check self_improving/system2_replay_input_promotion.py tests/self_improving/test_system2_replay_input_promotion.py
ruff format --check self_improving/system2_replay_input_promotion.py tests/self_improving/test_system2_replay_input_promotion.py
git diff --no-index --check /dev/null self_improving/system2_replay_input_promotion.py
git diff --no-index --check /dev/null tests/self_improving/test_system2_replay_input_promotion.py
```

两个 no-index 命令因文件与 `/dev/null` 不同而返回 1，但 stdout 为空且没有 whitespace diagnostics；Ruff、
format 与 whitespace gate 均通过。

## 主张边界

本切片只复制 package closure，不复制 request provenance、compile receipt、history authority、qualification、
完整 authority closure 或 runtime assets。它不执行 `ReplayApplication`、simulator 或 validate，不产生
physical pass 或 `publishable` 结论。

catalog 内的 locator 仍可能指向 CAS 外部资产，实际 replay 仍须提供并检查 allowed asset roots；package
closure 完整不等于 runtime-asset portability。未来 validate v2 仍须正式的 provenance 与 portable receipt
closure，不能把本次 package-only promotion 当作二者的替代品。
