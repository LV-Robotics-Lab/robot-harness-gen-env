# Text2Env compile 使用手册

这份手册面向第一次使用仓库的人。最简单的心智模型是：`compile` 像“场景装配单生成器”。它把一句受限的任务描述，变成一份确定的、可检查的场景包；它本身不启动仿真，所以不会在这一步生成物理回放视频。

## 1. 输入和输出是什么

输入有四项：

- `prompt`：任务描述，例如 `Place a can on top of a plate.`。
- `seed`：随机种子；同一输入、同一 catalog、同一代码版本和同一种子应得到同一求解结果。
- `asset catalog`：可用资产清单，记录资产编号、尺寸、碰撞体、稳定姿态和源文件。
- `out root`：本次生成结果的父目录。

一次成功的 compile 会在 `<out-root>/<scene-id>/` 写出：

| 文件 | 用途 |
| --- | --- |
| `request.txt` | 原始文字输入 |
| `scene_spec.json` | 解析后的对象和关系，例如 can `on_top_of` plate |
| `resolved_scene.json` | 已选定资产编号、模型、姿态和位置的完整场景 |
| `generated_scene.py` | RoboTwin 加载入口 |
| `package_manifest.json` | 包内文件的 SHA-256 清单及 resolved scene 绑定 |
| `validation_report.json` | compile 阶段的静态检查；正常情况下可能是 `incomplete`，因为还没有 runtime evidence |

如果输入不受支持、catalog 无效、资产缺失或求解无解，命令退出码为 `2`，并写出结构化 `failure_report.json`，而不是伪造成功结果。

## 2. 环境准备

推荐先把仓库安装到当前 Python 环境：

```bash
python -m pip install -e '.[dev]'
```

如果只是在源码检出目录临时运行，也可以在命令前加：

```bash
PYTHONPATH=. python ...
```

真实 replay 还需要另一套包含 RoboTwin、SAPIEN 和 RoboTwin 运行依赖的 Python 环境；compile 本身不要求启动 SAPIEN。

## 3. 先构建资产 catalog

下面命令扫描一份真实 RoboTwin checkout，并把 catalog 放在产品数据目录，而不是研究复现目录：

```bash
python -m scene_gen.catalog \
  --robotwin-root /path/to/RoboTwin \
  --overrides scene_gen/asset_overrides.yml \
  --source-commit "$(git -C /path/to/RoboTwin rev-parse HEAD)" \
  --out data/scene_gen/asset_catalog.json \
  --missing-out data/scene_gen/missing_assets.json
```

参数说明：

| 参数 | 是否必填 | 含义 |
| --- | --- | --- |
| `--robotwin-root` | 是 | RoboTwin 仓库根目录 |
| `--overrides` | 否 | 实测尺寸、稳定姿态等修正规则；默认是 `scene_gen/asset_overrides.yml` |
| `--source-commit` | 否但推荐 | 资产来源对应的 RoboTwin commit，便于审计 |
| `--out` | 是 | catalog JSON 保存位置 |
| `--missing-out` | 是 | 扫描时缺文件或不可用资产的报告位置 |

当前 catalog 会保存资产源文件的绝对路径，因此换机器或移动 RoboTwin 后应重新扫描。compile 包虽然有哈希清单，但当前核心包并没有把全部 GLB/JSON 资产复制进去；这也是产品化 replay 仍需补齐可移植资产快照的原因之一。

## 4. 用命令行 compile

```bash
python script/generate_scene.py \
  --prompt "Place a can on top of a plate." \
  --seed 7 \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-root data/generated_scenes
```

参数说明：

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--prompt` | 无，必填 | 受限的中英文场景描述 |
| `--seed` | `0` | 布局随机种子 |
| `--asset-catalog` | 无，必填 | catalog JSON 路径 |
| `--out-root` | `data/generated_scenes` | 场景包父目录；最终还会追加稳定的 `scene-id` |
| `--generate-missing-assets` | 关闭 | catalog 缺资产时启用仓库现有的确定性 proxy/派生缩放生成器 |
| `--generated-objects-root` | 无 | 缺失资产生成后的对象目录；只有启用上一项时才有意义 |

注意：这里的“生成缺失资产”不是 EmbodiedGen。当前活跃 compile 没有接入 EmbodiedGen provider；不要把确定性几何 proxy 说成外部生成模型资产。

成功时会打印类似：

```text
PASS scene_id=place_a_can_on_top_of_a_plate_fe0b76e316 resolved_sha256=<digest> validation=incomplete
```

`validation=incomplete` 是正确的阶段语义：编译完成，但物理验证尚未发生。只有带 runtime evidence 的验证结果为 `pass`，才能说明场景在仿真中稳定。

## 5. 在 Python 代码中 compile

```python
from pathlib import Path

from scene_gen import CompileEvent, CompileRequest, compile_scene


def on_event(event: CompileEvent) -> None:
    print(event.stage, event.phase, [str(path) for path in event.artifact_paths])


outcome = compile_scene(
    CompileRequest(
        request="Place a can on top of a plate.",
        seed=7,
        asset_catalog_path=Path("data/scene_gen/asset_catalog.json"),
        out_root=Path("data/generated_scenes"),
        generate_missing_assets=False,
    ),
    observer=on_event,
)

print(outcome.scene_spec.scene_id)
print(outcome.output_dir)
print(outcome.resolved_scene.digest())
print(outcome.static_validation["status"])
```

返回的 `CompileOutcome` 里有解析结果、resolved scene、实际使用的 catalog、输出目录、manifest、静态验证报告，以及可选的资产生成/入库报告。失败时会抛出 `CompileFailure`，其中 `code`、`stage` 和 `details` 可供 API 层生成可信错误回执。

## 6. 接着做 replay 和 validate

核心 CLI 的完整复现命令如下。运行 replay 时要使用真正装有 RoboTwin/SAPIEN 依赖的 Python：

```bash
python script/run_scene_runtime.py \
  --robotwin-root /path/to/RoboTwin \
  --resolved-scene data/generated_scenes/<scene-id>/resolved_scene.json \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-dir data/runtime/<scene-id> \
  --precheck-steps 0 \
  --settle-steps 900 \
  --contact-window-steps 120 \
  --video-frames 120 \
  --fps 12
```

这个命令既采集 `runtime_evidence.json`、PNG、MP4，也会在同一目录生成一次 `runtime_validation_report.json`。若希望把 validate 明确作为第三步独立重算：

```bash
python -m scene_gen.validator \
  --resolved-scene data/generated_scenes/<scene-id>/resolved_scene.json \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --package-root data/generated_scenes/<scene-id> \
  --runtime-evidence data/runtime/<scene-id>/runtime_evidence.json \
  --require-runtime \
  --out data/validation/<scene-id>/validation_report.json
```

`--require-runtime` 很重要：没有它，缺少物理证据时报告可以是 `incomplete`；有它，想宣称完整验收就必须提供并通过 runtime evidence。

## 7. 目录边界

- `data/generated_scenes/`、`data/runtime/`、`data/validation/`：正式本地运行产物，通常体积较大且不提交 Git。
- `docs/evidence/`：小型、可审阅的验收说明，记录命令、摘要、哈希和本地证据路径。
- `self_improving/studies/`：研究复现和实验材料；不应作为产品 API 的默认输出位置。
- `self_improving/asset_pipeline/active/data/asset_library/`：平台层长期可复用资产库，与单次 compile/replay 输出分开。

本机 2026-09-06 的 can-on-plate 实跑证据见 [`text2env-can-on-plate-e2e-20260906.md`](../docs/evidence/text2env-can-on-plate-e2e-20260906.md)。

20 条批量环境验收见
[`compile-environment-acceptance-20260906-v3.md`](../docs/evidence/compile-environment-acceptance-20260906-v3.md)。
该批次从 Harness compile 输出重建完整环境，再用 SAPIEN 做短时加载预览；10 条全新生成与 5 条
精确复用已通过。本地任务等价候选另有 5 条通过，但上游 ACDC/BEHAVIOR 资产实际使用仍为 0 条，
不能把这组本地候选称作已经接入官方 Digital Cousins。
