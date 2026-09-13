# Compile qualification refresh evidence — 2026-09-01

`db24ea9` 收紧 generated-asset admission 与 asset-ledger v3 后，checked-in
`text2env.compile@1.0.0` 资格立即按设计拒绝旧 bundle：loader 报
`implementation_file_mismatch`，首个不匹配文件是 `self_improving/harness/assets.py`。本次没有手改
manifest 摘要，而是在全新 scratch/library 中重新执行固定三轮 generator，再用 production loader
重读三文档、实现文件、`scene_gen` 与 ledger contract tree。

## 复现入口

```bash
python - <<'PY'
from datetime import date
from pathlib import Path
from self_improving.harness import CompileQualificationSettings, generate_compile_qualification

root = Path.cwd()
generate_compile_qualification(CompileQualificationSettings(
    bundle_root=Path("<new-bundle>"),
    scratch_root=Path("<new-scratch>"),
    distribution_root=root,
    scene_gen_root=root / "scene_gen",
    ledger_contract_root=root / "self_improving/asset_pipeline/active/1_asset_reuse/lib",
    admission_date=date(2026, 8, 31),
))
PY
pytest -q tests/self_improving/harness/test_qualification.py \
  tests/self_improving/harness/test_qualify_compile.py
```

固定 `admission_date` 是资格案例输入的一部分，不是把 2026-09-01 的运行伪装成旧运行；本文件与
新三文档摘要记录实际刷新日期。

## 结果

- 三轮终态均为 `succeeded`，处置严格为 `admitted -> reused -> reused`，资产 id 三次相同：
  `900_gen_hexagonal_pedestal_5695f4e5`。
- 七项门禁全部 pass：admission lifecycle、CAS resolution、Invocation stability、ledger files、package
  binding、source stability、static-validation boundary。
- 当前严格 ledger closure 有 3 个 file records，`check_files=True` 为 0 violation；ledger SHA-256 为
  `72a8bc471e0b21c6b58cf6e81902d4d0661dbb94d762e507793bca467949462e`。
- 第二、三轮 Invocation SHA-256 都是
  `efcfe2bf4ea7c9531ff66f7124acf7fbf981a0af070a4b7a7026883928db40d2`，typed output SHA-256 都是
  `9507350fbb9a0f861d93f0f154db0593ce7318a081c19cd922178507ae28f548`。
- implementation / scene-gen / ledger-contract 摘要分别为
  `cd57e5491c54b253c65da98429baf79a27af33587678154a3eb6380e9daef3e7`、
  `e2fe9fd66d917dc6dea40e8990248206e180e3a086c1619b9f40558c3efec330`、
  `1d52584d44ca5f5d759f5e72d327fed282f0bdb4adaf763387b2fda2467ef489`；report SHA-256 为
  `a0ebc959d214a24329788466a3eaea257b0f74851b2ed445c0ab29f293f80971`。

新 bundle 仍诚实报告 static validation `incomplete` 与
`physical_qualification=pending_settle`。这次刷新证明当前 compile 的输入→缺失生成→严格 ledger
入库→稳定复用→package/CAS 绑定链仍成立；它不是 SAPIEN settle、replay qualification 或资产发布资格。
