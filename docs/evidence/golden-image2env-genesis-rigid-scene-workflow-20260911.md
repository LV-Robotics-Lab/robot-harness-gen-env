# 已注册 image2env → Genesis 刚性场景：2026-09-11 实证

固定实现提交 `1cf8908acb83b86b15a7ae8a7c96cb943cc39945` 完成了一条有限桌面、单鼠标、已知资产精确复用的真实链路：`image2env.compile@2.0.0` 已注册终态 → 新 `image2env.replay@2.0.0` → 新 `genesis.observe@3.0.0` 实际捕获 → `image2env.validate@2.0.0`。正式节点 **1 passed，1610.72 秒**；不是整个原始 Golden E2E 任务完成。

[同名机读摘要](golden-image2env-genesis-rigid-scene-workflow-20260911.json) 保存完整 SHA、UUID、原始证据路径和统计，不嵌入资产、视频或 runtime manifest 大对象。其 JSON 使用排序键、紧凑分隔符及末尾 LF。

## 输入、来源与职责

最终输入是 Gujie 历史仿真输出 `data/media_acceptance/graph_workflow_mouse_002/half_dt/diagnostics/final_side.png`，seed **41**；图像解释是明确的 **operator-authored 已知鼠标复用 proposal**。此前 overview/17、top/23、side/41 三视角已分别完成 registered compile；本次复用 side 的真实终态，不重新编译、不把候选 receipt 冒充注册来源，也不声称模型自主理解图片或重新构建资产。

Gujie 固定来源 `eb0b710581fd7794bc01b447b0f77cb871c8a711`：适配 `import_simfoundry_scene.py` 的对象/坐标读取与 XY、四元数保持，`standard_urdf.py` 的单刚体几何解释，以及 `position_solver.py` 的完整 footprint 支撑语义。Harness 补齐 CAS、测量闭包、有限 Box 桌面、版本化合同、资格和状态证据；这不是整套 Gujie importer/通用多对象 solver 的直接接通。Genesis 固定来源为 `0e74bf392781884ccad765c3f344419c86b872ca`；第三方仿真器及 renderer 的主体归属不转为 Harness 原创。

场景 SHA 为 `e543ef65e21d380fc5965ff8f8d4f7fb69e2e2408f7a946f4377624c11f98e00`。有限桌面中心 `(0,0,0.2)`、尺寸 `(1,0.8,0.4)` 米，地面 Z=0；不是把 `on_table` 转成地面探针。质量、摩擦仍是估计参数，本结果不证明真实物性测量。

| 阶段 | 主要实现 | 合同或 schema |
|---|---|---|
| Gujie 场景适配 | [gujie_scene_adapter.py](../../self_improving/harness/gujie_scene_adapter.py) | [适配合同](../../self_improving/harness/GUJIE_SCENE_ADAPTER_CONTRACT.md) |
| native compile 与注册 | [compile application](../../self_improving/harness/image2env_genesis_compile_application.py) | [compile 合同](../../self_improving/harness/IMAGE2ENV_GENESIS_COMPILE_APPLICATION_CONTRACT.md) |
| 有限桌面 replay | [scene application V2](../../self_improving/harness/genesis_scene_application_v2.py) | [执行 V2 合同](../../self_improving/harness/GENESIS_SCENE_EXECUTION_V2_CONTRACT.md) |
| 独立新捕获 | [fresh capture application](../../self_improving/harness/genesis_fresh_capture_application.py) | [捕获 schema](../../self_improving/harness/schemas/genesis_fresh_capture.py) |
| 刚性场景 validate | [validation application](../../self_improving/harness/genesis_scene_validation_application.py) | [评估合同](../../self_improving/harness/GENESIS_SCENE_ASSESSMENT_CONTRACT.md) |
| 同一链路交付 | [workflow](../../self_improving/harness/image2env_golden_workflow.py) | [workflow 合同](../../self_improving/harness/IMAGE2ENV_GOLDEN_WORKFLOW_CONTRACT.md) |

## 真实失败与修复

Attempt 1 用 `genesis.observe@2.0.0` 读取 replay 最后一帧，耗时 **1237.82 秒**后失败：capture→replay terminal 153.632 秒、→observe preflight 223.699 秒、→TTL 拒绝 314.406 秒。TTL 保持 300 秒；replay 成功，observe 失败，validate 没有预留 invocation/intent/state，也没有成功 summary。失败摘要 SHA `cff10ca7ec44e9a78a4fd864ad7cf57cc42a02b660f66fe67fc1d4a402c9a3f8`，原始日志、时间线和终态均保留。

修复没有提前填写 `checked_at`、延长 TTL 或跳过深验。V3 在独立封闭进程中恢复已验证的 replay 末态 pose、将速度置零，实际拍摄新 PNG；`physics_steps=0`，`process_continuation=false`，权威明确为 `observed_restored_final_pose`。独立 capture manifest SHA `3696c296a230a9254fb26bfa28c880c83ca1d0966b78324604d220b5f6777931`，与资产、replay runtime 分账。

V3 两次真实捕获候选分别为 `f59afa14-67f5-47e0-a9ca-574903d6e960`、`248b851a-6962-4f70-821b-d4d06094575b`；耗时 300.864、301.776 秒。资格 `e05500d5bd931b9d5bd17bc4eae897cf9a0cf648191a4e61ebfcabab62cd4043` 已真实注册，并用 `roots={}` 新应用完成恢复（资格/恢复节点 1130.23 秒）。这两次只用既有注册 replay 作捕获来源；最终验收另跑了新 replay。

## Attempt 2 终态与交付

| Skill | 注册终态 UUID |
|---|---|
| image2env.compile@2.0.0 | `a42a3a83-98ea-4c01-a35f-3003a9109c49` |
| image2env.replay@2.0.0 | `bfc30903-0a69-478b-94ed-3ad2c9bbea03` |
| genesis.observe@3.0.0 | `90af3aa7-6d77-4c5e-87d8-185b3bea36ea` |
| image2env.validate@2.0.0 | `510c3c7d-c020-4623-b2bc-67e0dd045212` |

四个终态均 succeeded，资格、durable invocation、intent、output 和 CAS receipt 已逐段核对。汇总 SHA **`46f21368f0b7dadb0491809b164fa1dac585f79691d68961001f2c928394f5b6`**，48,395 bytes。它是 `checked_application_lineage_only` 审计对象，不新增状态或物理权威。

新图捕获于 `10:57:29.934580Z`，检查于 `10:58:48.881502Z`，汇总写后实际交付于 `11:01:55.434310Z`，有效期至 `11:02:29.934580Z`；**交付时余量 34.50027 秒**。汇总阶段直接重复 deep-read 次数为零，使用各 production invoke 已深验输出，再核轻量 CAS 和跨阶段等值。这里证明历史交付时新鲜，不声称此图现在仍新鲜。

## 物理、媒体与集中验收

- baseline 1,001 行、half-dt 2,001 行 canonical 连续 NDJSON；两套均 4 秒。
- 每套 41 张真实顺序 RGB PNG，唯一像素帧分别 24/4；lossless MKV 解码逐帧等于有序 PNG。直接 MP4 均为 41 帧、10 fps、0–4 秒时间序列。
- 独立 public batched reader 重算 **34 项物理检查**，与存储 report 完全一致；validate 为 `genesis.rigid_scene@1` passed。
- fresh PNG 为 640×480 RGB；文件 SHA `4cb9b4afb8672a63161dd337368ab80c9cdc70f48f88735fb6a89e37952a2c73`，像素 SHA `dd6080b1029ed9636ec4df6925329c1a86e2eaa8c688846830d16ba962f7aafa`。目视鼠标在有限桌面上；图像不替代物理证据。
- 三个进程均记录 299 个 native members、Landlock ABI 8，原始部署映射、环境、线程、typed observations 与 manifests 相符。源 CAS 中 **82,334 个显式 ArtifactRef 对象、4,790,060,117 bytes** 校验通过；不将 opaque `report_sha256` 字符串自动算作递归引用。

三份只读集中验收位于 `/home/jingxiang/bingsheng/golden-four-skill-workflow-20260911/`：

| 报告 | SHA256 |
|---|---|
| acceptance-lineage.json | `a1b03b9de69c1fe079deb87279e3eb10545a225f56395814b54e7d42475c5a37` |
| acceptance-media-cas.json | `a1f324b1a499e68f2470f2f2ed4b66e34be9a9425fe65b80f786aefdec40a0e5` |
| acceptance-failure-relocation.json | `601afecbed9255d1912e716bf3a250918d56a3ad1c3bf014b8c09d28ffab4e1b` |

媒体审计初稿把不含 environment 的 raw identity 与完整 typed identity 整体比较，形成审计脚本误报；最终报告保留初稿 SHA、异常和修正理由，按身份字段及部署环境展开分别校验。这不是隐藏的仿真失败。

第二绝对 CAS 的 fresh process 恢复了 **4,115 个对象、1,970,263,053 bytes**，没有读取原 CAS/部署、没有 import Genesis。层级仅为 summary 与传递输出证据、runtime manifest **索引**；未复制或验证 runtime member payload、部署 roots 或应用 SQLite，也没有在第二部署重新执行完整 workflow。不能称为新运行时完整 relocation 或该部署资格恢复。

## 复现入口与未完成边界

正式配置为 `/home/jingxiang/bingsheng/golden-four-skill-workflow-20260911/config-v3.json`，SHA `6306007d3cc7e4135a5a9aa0d69b80d744b355292a8d337e0e2f4e653e9400b6`。固定提交上的公开入口测试：

```text
export IMAGE2ENV_GOLDEN_WORKFLOW_CONFIG=/path/to/new-attempt-config.json
.venv/bin/pytest -q tests/self_improving/harness/test_image2env_golden_workflow.py::test_formal_four_registered_skills_publish_one_real_workflow
```

重跑前使用新的 attempt/log/summary 路径保留本次证据；测试会生成新 replay/observe/validate UUID。需要原有可信应用 journal、资格 CAS、资产资格和两个固定 runtime 部署，不能只有摘要文件。

本证据没有完成 external Codex/MCP 路由、自主视觉解释、text/image/video 三模态全部路径、新资产生成、digital cousins、robot policy 或 promotion。最终 robot-policy 门仍未通过。只证明上述输入、固定实现与估计参数下的已知鼠标精确复用、有限桌面刚性场景、独立新捕获及受限验证闭环。
