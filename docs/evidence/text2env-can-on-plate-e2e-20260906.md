# Text2Env can-on-plate compile → replay → validate 验收（2026-09-06）

## 结论

当前 `worktree/bingsheng` 工作树中的稳定核心链路没有在这个固定案例上回退：一份全新扫描的 RoboTwin catalog 成功把 `Place a can on top of a plate.` 编译为 `071_can` + `003_plate` 场景；真实 SAPIEN replay 跑满 900 个物理步并录制 120 帧；独立 `validate --require-runtime` 最终为 `pass`、`fail_count=0`、`not_run_count=0`。

本验收只证明“已有 RoboTwin 资产的稳定核心仍能 compile/replay/validate”。它不证明 EmbodiedGen 已接入、不证明任意缺失资产都能生成，也不把这两个 RoboTwin 资产冒充成新生成的 sim-ready 资产。

## 路径

所有大体积运行产物都位于正式本地数据目录：

```text
data/text2env_acceptance/can_on_plate_20260906/
├── catalog/
│   ├── asset_catalog.json
│   └── missing_assets.json
├── compile/place_a_can_on_top_of_a_plate_fe0b76e316/
│   ├── request.txt
│   ├── scene_spec.json
│   ├── resolved_scene.json
│   ├── generated_scene.py
│   ├── package_manifest.json
│   └── validation_report.json
├── replay/
│   ├── runtime_evidence.json
│   ├── runtime_validation_report.json
│   ├── observer_start.png
│   ├── observer_mid.png
│   ├── observer_end.png
│   ├── observer_runtime.mp4
│   ├── preview_head.png
│   ├── preview_world_left.png
│   ├── preview_world_right.png
│   └── preview_segmentation.png
└── validate/validation_report.json
```

这里不再使用 `self_improving/studies/ASPIRE/artifacts/` 作为输出位置。ASPIRE 中原有的 can-on-plate 文件是历史研究/资格化证据，能用于回溯，但不是本次产品链路的新产物。

## 输入和环境

- prompt：`Place a can on top of a plate.`
- seed：`7`
- 当前分支：`worktree/bingsheng`
- RoboTwin root：`/home/jingxiang/workspace/robot-harness-gen-env/external/RoboTwin`
- RoboTwin commit：`266f3aadf505a4f7fe9af0faa41a20f5f47cd123`
- 真实 replay Python：`/home/jingxiang/miniconda3/envs/robotwin-5090/bin/python`
- catalog：127 条记录，15 条当前可用
- catalog 语义 digest：`6d25cb2816e725e5cc4f2232ba6a3ce9dea89cf6cb8853a13e2eb1942ba8b2a7`

源码检出没有安装进上述 Python 环境，因此命令使用 `PYTHONPATH=.`。如果先执行 `python -m pip install -e '.[dev]'`，不需要这个前缀。

## 实际执行

### 1. 构建独立 catalog

```bash
PYTHONPATH=. /home/jingxiang/miniconda3/envs/env-gen-sc311/bin/python \
  -m scene_gen.catalog \
  --robotwin-root /home/jingxiang/workspace/robot-harness-gen-env/external/RoboTwin \
  --overrides scene_gen/asset_overrides.yml \
  --source-commit 266f3aadf505a4f7fe9af0faa41a20f5f47cd123 \
  --out data/text2env_acceptance/can_on_plate_20260906/catalog/asset_catalog.json \
  --missing-out data/text2env_acceptance/can_on_plate_20260906/catalog/missing_assets.json
```

结果：`PASS entries=127 available=15`。

### 2. Compile

```bash
PYTHONPATH=. /home/jingxiang/miniconda3/envs/env-gen-sc311/bin/python \
  script/generate_scene.py \
  --prompt "Place a can on top of a plate." \
  --seed 7 \
  --asset-catalog data/text2env_acceptance/can_on_plate_20260906/catalog/asset_catalog.json \
  --out-root data/text2env_acceptance/can_on_plate_20260906/compile
```

结果：

- scene id：`place_a_can_on_top_of_a_plate_fe0b76e316`
- resolved scene digest：`96e995144468707f0f6e169341ce13e2330b1f42a8acaf73d9c126925e4be3ee`
- can：`071_can`，动态物体，关系为 `on_top_of plate_1`
- plate：`003_plate`，动态物体，关系为 `on_table`
- 静态 validation：`incomplete`，因为此时还没有 runtime evidence；符合设计

### 3. Replay

```bash
PYTHONPATH=. /home/jingxiang/miniconda3/envs/robotwin-5090/bin/python \
  script/run_scene_runtime.py \
  --robotwin-root /home/jingxiang/workspace/robot-harness-gen-env/external/RoboTwin \
  --resolved-scene data/text2env_acceptance/can_on_plate_20260906/compile/place_a_can_on_top_of_a_plate_fe0b76e316/resolved_scene.json \
  --asset-catalog data/text2env_acceptance/can_on_plate_20260906/catalog/asset_catalog.json \
  --out-dir data/text2env_acceptance/can_on_plate_20260906/replay \
  --precheck-steps 0 \
  --settle-steps 900 \
  --contact-window-steps 120 \
  --video-frames 120 \
  --fps 12
```

结果：`PASS scene=place_a_can_on_top_of_a_plate_fe0b76e316 fail=0 video_frames=120`。

关键物理数据：

| 检查 | can | plate |
| --- | ---: | ---: |
| 资产编号 | `071_can` | `003_plate` |
| 末段仍在移动 | 否 | 否 |
| 穿透点 | 0 | 0 |
| 目标接触占比 | 1.0 | 1.0 |
| 非预期接触占比 | 0.0 | 0.0 |
| 平移漂移 | 3.921 mm | 1.007 mm |
| 旋转漂移 | 0.917° | 0.000° |
| 可见像素 | 928 | 3628 |
| 掉落 | 否 | 否 |

视频为 12 fps、120 帧、100 个互异帧；采样覆盖开始连续帧和第 899 个物理步。运行时报告和 resolved scene 绑定到同一个 digest。

SAPIEN 渲染器打印了 OIDN/CUDA denoiser warning，但命令成功退出，PNG/MP4 已生成，物理证据和验证门全部通过。该 warning 影响的是降噪可用性，不被当作物理成功证据。

### 4. 独立 Validate

```bash
PYTHONPATH=. /home/jingxiang/miniconda3/envs/robotwin-5090/bin/python \
  -m scene_gen.validator \
  --resolved-scene data/text2env_acceptance/can_on_plate_20260906/compile/place_a_can_on_top_of_a_plate_fe0b76e316/resolved_scene.json \
  --asset-catalog data/text2env_acceptance/can_on_plate_20260906/catalog/asset_catalog.json \
  --package-root data/text2env_acceptance/can_on_plate_20260906/compile/place_a_can_on_top_of_a_plate_fe0b76e316 \
  --runtime-evidence data/text2env_acceptance/can_on_plate_20260906/replay/runtime_evidence.json \
  --require-runtime \
  --out data/text2env_acceptance/can_on_plate_20260906/validate/validation_report.json
```

结果：`PASS fail=0 not_run=0`。报告共 38 项：30 项 `pass`，8 项阈值不适用而标为 `not_applicable`；没有 `fail` 或 `not_run`。单独重算时传了 `--package-root`，所以它比 replay 命令顺手生成的报告多检查一次 package manifest。

## 内容哈希

| 文件 | SHA-256 |
| --- | --- |
| `catalog/asset_catalog.json` | `8c29cf4c2dc759bd4bd55243ccd4a2688deef40601d153553d61757c96e65364` |
| `replay/runtime_evidence.json` | `6c7d08ffef8b6475ec6f747687a8ec5b86fef0b382af3eda78cfcef14054804b` |
| `replay/observer_runtime.mp4` | `a41d05366050ba0c2af635e69299a12a4b2a0e78ed27490517d27b122fa12a4e` |
| `validate/validation_report.json` | `fc13c398013b48ffad2b1947e01d0eac895d3140df687ed61946f9e3b64f074e` |

catalog 的文件字节哈希与 `AssetCatalog.digest()` 不同是预期行为：后者哈希规范化后的模型内容，前者哈希带排版的 JSON 文件字节。

## 失败过但没有混入成功结论的尝试

最初选择的 `env-gen-sc311` 环境可以导入 SAPIEN，但缺少 RoboTwin 间接导入的 `curobo`，replay 在场景加载前就失败，且没有产生 runtime evidence。随后改用完整的 `robotwin-5090` 环境，才得到上面的成功结果。证据结论只引用后一次成功运行。

## 当前边界

- 已确认：稳定核心对已有真实 RoboTwin can/plate 资产的 compile → replay → validate 没有回退。
- 未确认：EmbodiedGen 接入、未知资产生成、生成资产入库、跨机器资产快照、Harness production replay qualification、validate v2 晋升。
- 媒体是检查入口，最终结论来自与 resolved scene digest 绑定的物理 evidence 和 validator，而不是“图片看起来像”。
