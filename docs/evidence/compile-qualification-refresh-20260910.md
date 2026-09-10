# Compile qualification refresh evidence — 2026-09-10

本次在干净集成提交
`2841d0a17c9df12c434178969544dc7ed8e87fc9` 上，只重新执行并签入
`text2env.compile@1.0.0` 资格；没有启动 replay。旧 checked-in bundle 先由正式 loader
以 `implementation_file_mismatch` 拒绝，首个漂移文件为
`self_improving/harness/qualification.py`，因此保留了真实 RED。

## 执行

解释器为 `/home/jingxiang/miniconda3/bin/python`（Python 3.13.12，binary SHA-256
`e537ecc7a9aa3a863106ee9ec81e519424c23e700590aa0ea1e59016ec0f1be9`）。在全新外部
`<run-root>/bundle`、`<run-root>/scratch` 和 `<run-root>/strict-load-cas` 中运行：

```bash
PYTHONDONTWRITEBYTECODE=1 /home/jingxiang/miniconda3/bin/python -B - <<'PY'
from datetime import date
from pathlib import Path
from self_improving.harness import CompileQualificationSettings, generate_compile_qualification

root = Path.cwd()
generate_compile_qualification(CompileQualificationSettings(
    bundle_root=Path("<run-root>/bundle"),
    scratch_root=Path("<run-root>/scratch"),
    distribution_root=root,
    scene_gen_root=root / "scene_gen",
    ledger_contract_root=root / "self_improving/asset_pipeline/active/1_asset_reuse/lib",
    admission_date=date(2026, 8, 31),
))
PY
```

官方 generator 固定请求 `Place a purple hexagonal pedestal on the table.`、seed 77 和
case `generated-purple-hexagonal-pedestal-seed-77`，直接运行真实 compile handler 三次。
生成后先调用 `verify_qualification_bundle(...)` 只读深验，再以全新 CAS 调
`load_qualification_bundle(...)` 严格加载；两者均为 `pass`。最后把三份生成器原始输出逐字安装
到 checked-in bundle，再运行同一 production loader gate。

## 结果

- 三轮处置为 `admitted -> reused -> reused`；三轮 asset id 均为
  `900_gen_hexagonal_pedestal_5695f4e5`。
- 每轮 18 个事件、11 个已解析且内容复核的 CAS artifact；package id 均为
  `0c546491da6b9f44309da9f2ea40e9a3d895a90099fb55933bc5fafee45c0e85`。
- ledger v3 三轮摘要均为
  `badb6609bb422040b61e3ac3241f135be13ad56e854de1d3a025e7213cfc9bf8`，
  3 个 file records，`check_files=True` 且 0 violation。
- 第二、三轮 Invocation 均为
  `77f46513252c6cd99f141c34f6991078fdd23402817d4afb5877fe2894f37385`，
  typed output 均为
  `c39b1ca9fd64f7aa673ef62f7fc01f36ad4bc1034a4a17d6f7ab9c76d2ec3524`；
  第一到第二轮只有 `asset-library-state` dependency 改变。
- implementation / scene-gen / ledger-contract 摘要分别为
  `0394575ff26f8da601507416573656b3fb04d7ca06fc45ff3f8195005f470b7f`、
  `e2fe9fd66d917dc6dea40e8990248206e180e3a086c1619b9f40558c3efec330`、
  `1d52584d44ca5f5d759f5e72d327fed282f0bdb4adaf763387b2fda2467ef489`。
- 三文档 raw SHA-256：manifest
  `0da43faa711d265ecd78c55170a583c6500f159afd346e5fdad4aab599d85f90`，
  qualification
  `f8685f3258fb0953969b09a62b0d584f97572ea87200b7e4224ecf1f218665d7`，
  report
  `d3232f1aa28520c3aebd47b67cb0eab88e078af580d6b95e0f8f7d1bee1d2c66`。

这不是只改 expected hash：新 bundle 来自三次真实候选执行，报告内部绑定 11 个当前实现文件、
两棵 source tree、三轮 CAS/package/ledger/Invocation 观测；严格 loader 再逐文件重算当前
distribution identity 后才变绿。static validation 仍为 `incomplete`，资产仍为
`pending_settle`；本证据不声明物理、replay、Genesis 或发布资格。
