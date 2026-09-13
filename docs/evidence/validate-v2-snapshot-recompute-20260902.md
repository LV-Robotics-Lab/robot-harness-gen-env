# CAS snapshot-aware validation report recompute evidence — 2026-09-02

这份记录只证明 `self_improving.validate_v2_snapshot.ValidateV2SnapshotAdapter` 能从本地
CAS 中的 package、runtime evidence 与 runtime asset snapshot 重新计算一份确定性的
`robotwin.scene_validation.v1` 报告。输入来自已经完成的固定资格回放 run
`0000f972-15e7-4ada-aec6-707e8973acde`；原始资产树不是报告字节的读取权威。

机读摘要见
[`validate-v2-snapshot-recompute-20260902.json`](validate-v2-snapshot-recompute-20260902.json)。
该 companion report 为 5,187 bytes，SHA-256 为
`5421f556241a69c4ff06d09694cfe9f57ab685c4b7f691b0375c0c1d2f7a2048`。

## 输入与结果

| 项目 | 观测值 |
| --- | --- |
| runtime evidence SHA-256 | `1c74e310fff651ef7ca562f8e57b66fdf6f4e2c7a0e14abc8d2c878435351f1f` |
| runtime asset snapshot SHA-256 | `43df37804b8e8c6dcb807f2f803e0d591dee18b7d5bbb8863b476f12144b43d4` |
| adapter bytes / SHA-256 | `44,912` / `e7ef0c5cccb77e761f135be5632e8f726f9936496daf43ecbcd4eb08158c514e` |
| contract tests / coverage | `100 passed`; `434` statements + `162` branches = `100%` |
| validation status | `pass` |
| fail / not-run | `0 / 0` |
| base simulation / contact-window steps | `900 / 120` |
| total / unique video frames | `120 / 100` |
| 新报告 bytes | `8,788` |
| 新报告 SHA-256 | `7a34a21d3e7ee6b0e6babd98d08986aae510ca70e42c584e1db55936ffb03ef6` |

同一组精确输入分别放入两个独立 CAS 副本，并使用两个独立 scratch root 执行。两次均得到
上表中的状态、计数与报告身份；报告字节完全相同，两个 scratch root 在返回后均为空。报告是
canonical JSON，未包含私有 locator、绝对路径、scratch 路径、`decision`、`publishable` 或
`validation_decision_receipt`。

生产 CAS 在复算前后均为 79 个对象。两个副本各自从 79 个对象开始，仅因写入同一份新报告而变为
80 个对象；生产 CAS 未被修改。

adapter 直接核对 SceneSpec 与已提交 ResolvedSceneSpec 的 request、frame、unit、workspace、relations，
以及每个 object 的稳定语义字段和 support binding；它不会重新调用 solver。合同测试另以
`procedural_generated` 资产证明：初次编译时存在、随后删除的 generation provenance 和原始资产目录
不会成为复算依赖。这个检查只证明输入包内部的语义闭合，不证明 grounding、solver 执行或 compile
provenance。

## 文件访问审计

对第一份 CAS 副本的复算进程做了本地系统调用路径分类；第二份副本用于独立字节确定性复核：

- 没有打开原始资产文件，也没有访问生产 CAS；复算读取来自各自的 CAS 副本。
- 没有写入生产位置；SQLite 只读。
- 原始资产的六个 source path 各发生一次 metadata-only `stat` 探测，共六次；没有随后打开这些
  path。

因此本记录证明的是“原始资产字节不再是复算输入”，而不是“完全不查询原始 locator”。当前
`scene_gen.validator` 的兼容性 source-file check 仍会对六个来源 locator 做元数据探测；不得把本结果
描述成 zero lookup。

## 与历史报告的关系

历史报告
`01e85c898631343c46df50ecb2fb26dd0b4e9c2e424424553004f19b2ef09d75` 只在新报告已经生成后用于比较，
不是复算输入。两份报告的 37 个同名 check 状态一致，其中 33 个 check 的 evidence 完全相同；新报告
另增两个 `pass` check：`package_manifest` 与 `snapshot_validation_binding`。这项后验比较用于发现行为
漂移，不能把旧报告变成新报告的权威来源。

## 离线复核模板

仓库内合同与攻击测试不需要外部资产：

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider \
  tests/self_improving/test_validate_v2_snapshot.py
./script/run_self_improving_tests.sh
```

部署侧复算应先制作两个独立 CAS 副本，再由 operator-owned driver 从每个副本加载相同的
`EnvironmentPackage` 与两个精确 `ArtifactRef`，分别调用：

```python
from pathlib import Path

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.validate_v2_snapshot import ValidateV2SnapshotAdapter

# request 是由精确 EnvironmentPackage、runtime evidence ref 和 snapshot ref
# 构成的 SnapshotValidationRequest；它不从历史 validation report 构造。
result = ValidateV2SnapshotAdapter(
    artifact_store=LocalArtifactStore(Path("/path/to/independent-cas-clone")),
    scratch_parent=Path("/path/to/empty-scratch-parent"),
).recompute(request)
```

两个结果的 `validation_report.sha256`、CAS 报告 bytes、status、fail/not-run 计数必须分别相等；
scratch parent 必须为空。对生产存储的对象计数与写入审计应在复算前后独立完成。仓库当前没有把这个
operator driver 声明为通用 CLI。

## 主张边界

这个 adapter 返回 report ref、status、fail count 和 not-run count。它不是
`text2env.validate@2` output，不生成 validate decision、publishability、run provenance 或
qualification，也不完成 replay-to-validate promotion。报告仍使用
`robotwin.scene_validation.v1` schema。它也不重新执行 compile solver，因此不把 package 的语义一致性
外推为 deterministic grounding 或 compile provenance 证明。

本次 runtime evidence 观测到 900 个 base simulation steps、120-step contact window、120 帧与
100 个互异帧，但输入中没有独立的 `RuntimeConfig` receipt。adapter 因而不能证明 operator 当时
“请求的”视频/物理合同就是这些值；这些数只能作为已绑定 runtime evidence 的观测，不能单独证明
requested-video contract。
