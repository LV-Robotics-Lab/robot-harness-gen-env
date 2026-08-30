# Compile production qualification evidence — 2026-08-31

这份证据回答一个很具体的问题：固定的 `text2env.compile@1.0.0` 是否真的能从业务输入出发，生成缺失
资产、把它放进资产库、产出可重建 package，并在进程重启后复用同一资产。结论是：这条 compile
纵向链已经跑通；它还没有经过仿真物理回放，所以不能据此宣称资产可发布。

> 当前 bundle 刷新：A035 修改了 scene-gen loader 接线后，旧 source-tree 摘要立即 fail closed。
> 在干净 commit `7766fee` 上重新运行下述三轮 generator，仍观测
> `admitted -> reused -> reused`；A037 又把 Invocation dependencies 交给 handler，因此在干净
> `607b15e` 上再次按同样门禁刷新。当前 implementation / scene-gen / report 摘要分别为
> `88f6cb42…72599`、`e2fe9fd6…ec330`、`b73b3bbc…edb26`。下文 CLI run id 与 output 摘要是 A031
> 当时的历史运行证据；当前资格门禁的逐项观测值以 packaged `report.json` 为准。

## 复现方法

先由已提交的 qualification generator 生成固定三文档 bundle。它会在全新 scratch/library 中直接执行
候选三次，只有 `admitted -> reused -> reused`、CAS/package/ledger/source 等七组门禁全部通过才发布：

```bash
python - <<'PY'
from datetime import date
from pathlib import Path
from self_improving.harness import CompileQualificationSettings, generate_compile_qualification

root = Path.cwd()
generate_compile_qualification(CompileQualificationSettings(
    bundle_root=root / "self_improving/harness/qualified_skills/text2env.compile/1.0.0",
    scratch_root=Path("/tmp/robot-harness-compile-qualification-20260831-a030"),
    distribution_root=root,
    scene_gen_root=root / "scene_gen",
    ledger_contract_root=root / "self_improving/asset_pipeline/active/1_asset_reuse/lib",
    admission_date=date(2026, 8, 31),
))
PY
```

随后用全新的 state root 和空 catalog 调正式 CLI；相同命令在三个独立进程中执行三次：

```bash
python -m self_improving.harness.compile_cli \
  --state-root /tmp/robot-harness-compile-production-smoke-a031 \
  --trusted-catalog-root /tmp/robot-harness-compile-qualification-20260831-a030 \
  --allowed-asset-root /tmp/robot-harness-compile-qualification-20260831-a030 \
  --admission-date 2026-08-31 \
  --catalog-path /tmp/robot-harness-compile-qualification-20260831-a030/empty_catalog.json \
  --request 'Place a purple hexagonal pedestal on the table.' \
  --seed 77 \
  --generate-missing
```

## 实测结果

| 运行 | 终态 | 资产处置 | Invocation identity | Typed output identity |
| --- | --- | --- | --- | --- |
| 1 | succeeded | admitted | `400434c1…aeec` | `803f5997…6e30` |
| 2 | succeeded | reused | `55a10c77…6795` | `803f5997…6e30` |
| 3 | succeeded | reused | `55a10c77…6795` | `803f5997…6e30` |

首轮入库改变了 `asset-library-state`，所以首轮 Invocation identity 与后两轮不同，这是预期行为；资产库
稳定后，第二、三轮 Invocation 和 typed output 都完全一致。三轮各有 18 条真实执行事件和 11 个 CAS
产物。进程重启后重新装配 application，三份 Invocation、54 条事件、三份终态均能从 SQLite 恢复，所有
RunState artifact 都能从 CAS 重读并复算哈希。

入库资产是 `900_gen_hexagonal_pedestal_5695f4e5`。其 ledger 为 `asset_ledger.v3`，使用
`check_files=True` 校验得到 0 项违规；visual/collision OBJ、材质、model data 和 generation provenance
均有独立文件摘要。EnvironmentPackage 的 semantic resolved digest 是
`8b0391f81ff0b282d1f32d8bd278cc95b998eedefe1c385acd98ce717b00f922`。

## 诚实边界与尝试记录

Static validation 的结果是 `incomplete`（0 fail、1 not-run），资产资格仍是
`generation_qc_only / pending_settle`。这是有意保持的边界：compile 能证明生成、入库、绑定和可恢复，
不能替代 replay 的物理证据。

检查过程中有三次后处理脚本假设错误：用了旧的 `blockers`/SQLite 表名、把可空 schema 值和字符串直接
排序、以及用普通 import 语法导入带数字的包段。它们都发生在成功运行之后，没有改变运行或证据；修正
方式与每一步结果保存在同名 JSON 的 `attempt_log`。完整文件大小、SHA、run id、事件/持久化计数和资产
文件摘要见 `compile-production-qualification-20260831.json`。
