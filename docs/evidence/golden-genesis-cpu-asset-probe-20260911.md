# Golden Genesis CPU asset probe — 2026-09-11

固定实现：`7e145274a10e8d80f3966b491e05a512294588fc`；固定 Genesis World：
`0e74bf392781884ccad765c3f344419c86b872ca`。

这次运行把已迁入 CAS 的 legacy mouse `AssetRepresentationV1` 交给 Harness-owned、无相机的
`GenesisCpuAssetProbeBackend`。backend 只使用 CPU/Newton 场景，执行初态观测和 1,000 次、每次
0.004 秒的 step；没有创建 camera、renderer、rasterizer 或媒体。producer 发布 typed probe input、
原始 load observation、load evidence、1,001 行 canonical NDJSON、contact trace 和 probe report，随后
deep verifier 从 CAS 重新读取并重算完整闭包。

真实 Genesis load 观测与源文件语义刻意分开：源 URDF 是 1 link、0 joint、8 个 collision member；
Genesis 实际载入为 1 link、1 free joint、6 DOF，并将 8 个源 collision mesh 合并为 1 个 loaded geom
（502 vertices / 1,000 faces）。把“8 个源文件”写成“8 个运行时 shape”会是假证据，本次合同和报告已
拒绝这种混用。载入质量为 `0.07000000029802322 kg`，摩擦为 `0.2`。

1,001 个样本中，989 个 step 有 candidate-ground contact，共 4,040 条 contact、1 个 collision pair；
最大穿透 `0.0004873909056186676 m`，最大法向力 `1.4540600776672363 N`，累计法向冲量
`2.7468003143923414 N·s`。末态线速度 `6.854644206770141e-07 m/s`、角速度
`2.562796953528175e-05 rad/s`，因此七项 exact checks（load、closure、collision shape、contact、
dynamic release、penetration、settled）全部通过。

最终 CAS 有 23 个对象、7,711,609 bytes。probe report ArtifactRef SHA-256 为
`9e46011ecfb6364aeeed3d58ffd7d0ac81b35f4d61880a3909f88a60d866f5b9`，语义 self-hash 为
`2ec845bfcaac19c0beb7d00de4d68caf545e8ece54aabd92b72e966d7d9b0988`。整套 CAS 被复制到第二个绝对
路径后，在未加载 Genesis、也不需要原 source root 的新进程中重建并 strict verify：23/23 supporting
artifacts、1,001 rows、路径摘要、bytes 和 SHA 全部一致。relocated verification SHA-256 为
`58682a8d6f29c9ca69a8bcc8a908dbdf839e092448b49fc5d15de8d378b91bed`。

原始摘要位于本机
`/home/jingxiang/bingsheng/golden-genesis-cpu-probe-20260911.WQoKzh/acceptance-summary.json`，
SHA-256 为 `36fdcca8cdeafa31aba81ca521b61e214c42e1842529a7808f0d881cad338745`；搬迁摘要位于
`/home/jingxiang/bingsheng/golden-genesis-probe-relocated-20260911.oGormX/relocated-verification.json`。
仓库只保留[小型摘要](golden-genesis-cpu-asset-probe-20260911.json)，完整 CAS 未提交。
固定实现的环境节点可用以下命令复核；该节点只检查正式环境，不替代上述 1,000-step 实跑：

```bash
PYTHONPATH="$PWD:/home/jingxiang/gujie/gen-env/external/genesis-world" \
/home/jingxiang/gujie/gen-env/.venv/bin/python -m pytest -q \
tests/self_improving/harness/test_genesis_cpu_backend.py::test_formal_runtime_environment_is_fixed_cpu_genesis_checkout
```

focused 门为 77 passed / 1 base-environment skip；固定 Genesis 环境的正式节点另为 1 passed；93 份
public schema snapshots 全部通过 exporter。第一次真实 1,000-step 运行曾因 contact force 作用方符号
解释错误产生“有 contact 但零 force/impulse”的失败样本；修复后永久测试要求有 contact 时 force 与
impulse 必须为正，并要求末态 settling。该失败样本不属于通过证据。

边界必须保留：这是一份 asset-level Genesis-native CPU probe pass，不是完整 x2env environment compile/
replay，也没有签发 `GenesisAssetQualificationV1`。当前 runtime lock 只绑定 Genesis archive 与 backend
实现，还没有把 Python、Torch CPU、Quadrants、NumPy、native libraries 和完整解释器依赖闭包封成可迁移
production lock；因此不能进入 production Registry/MCP。质量与摩擦来自 SimFoundry VLM estimate，
本证据只证明该参数下的仿真 load/contact/settling，不证明真实物体测量值、robot-policy 或 promotion。
