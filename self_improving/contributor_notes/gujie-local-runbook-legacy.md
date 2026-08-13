# Robot Harness gen-env 运行说明

项目路径：

```text
/home/jingxiang/gujie/robot-harness-gen-env
/home/jingxiang/gujie/RoboTwin
```

## 一、环境分工

`env-gen-sc311` 用来扫描资产、编译场景和做静态检查：

```bash
conda activate env-gen-sc311
```

`robotwin-5090` 用来运行 RoboTwin/SAPIEN 真实物理仿真、截图和视频：

```bash
conda activate robotwin-5090
```

当前物理回放应使用 `robotwin-5090`，不要使用缺少 Curobo 的大写 `RoboTwin` 环境。

执行命令前先进入项目：

```bash
cd /home/jingxiang/gujie/robot-harness-gen-env
```

## 二、`python -m` 是什么

推荐：

```bash
python -m script.run_scene_runtime
```

不推荐：

```bash
python script/run_scene_runtime.py
```

`-m` 表示按模块运行。这样项目根目录会进入 Python 搜索路径，程序就能找到同级的 `scene_gen/`，避免：

```text
ModuleNotFoundError: No module named 'scene_gen'
```

它不会安装或修改软件。转换规则是去掉 `.py`，把 `/` 换成 `.`。

## 三、生成资产 Catalog（通常只执行一次）

```bash
conda activate env-gen-sc311
cd /home/jingxiang/gujie/robot-harness-gen-env

python -m scene_gen.catalog \
  --robotwin-root /home/jingxiang/gujie/RoboTwin \
  --overrides scene_gen/asset_overrides.yml \
  --source-commit "$(git -C /home/jingxiang/gujie/RoboTwin rev-parse HEAD)" \
  --out data/scene_gen/asset_catalog.json \
  --missing-out data/scene_gen/missing_assets.json
```

作用：扫描 RoboTwin 中的杯子、盘子、罐子等资产，整理成 gen-env 可以查询的清单。

- `--robotwin-root`：RoboTwin 所在位置。
- `--overrides`：人工修正的物体尺寸、支撑面和容器信息。
- `--source-commit`：记录当前 RoboTwin Git 版本。
- `--out`：可用资产清单。
- `--missing-out`：缺失或无法识别的资产。

输出：

```text
data/scene_gen/asset_catalog.json
data/scene_gen/missing_assets.json
```

## 四、编译一个文字场景

```bash
conda activate env-gen-sc311
cd /home/jingxiang/gujie/robot-harness-gen-env

python -m script.generate_scene \
  --prompt "Place a can on top of a plate." \
  --seed 42 \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-root data/generated_scenes
```

作用：把“把罐子放到盘子上”翻译成确定的资产、位置、方向和物体关系。

- `--prompt`：自然语言要求。
- `--seed 42`：随机种子；相同 prompt 和 seed 会得到相同结果。
- `--asset-catalog`：使用的资产清单。
- `--out-root`：输出根目录。

这个示例的 scene ID 是：

```text
place_a_can_on_top_of_a_plate_fe0b76e316
```

输出：

```text
data/generated_scenes/place_a_can_on_top_of_a_plate_fe0b76e316/
```

其中 `resolved_scene.json` 是下一步物理回放的输入。

## 五、运行一个场景的真实物理仿真

```bash
conda activate robotwin-5090
cd /home/jingxiang/gujie/robot-harness-gen-env

python -m script.run_scene_runtime \
  --robotwin-root /home/jingxiang/gujie/RoboTwin \
  --resolved-scene data/generated_scenes/place_a_can_on_top_of_a_plate_fe0b76e316/resolved_scene.json \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-dir data/runtime/place_a_can_on_top_of_a_plate_fe0b76e316 \
  --precheck-steps 0 \
  --settle-steps 900 \
  --contact-window-steps 120 \
  --video-frames 120 \
  --fps 12
```

作用：把场景加载进 SAPIEN，让物体真实掉落、碰撞并稳定，然后检查罐子是否真的在盘子上。

- `--resolved-scene`：需要回放的场景。
- `--out-dir`：结果保存目录。
- `--precheck-steps 0`：不提前预跑，从物体释放开始记录。
- `--settle-steps 900`：运行 900 个物理步骤等待稳定。
- `--contact-window-steps 120`：用最后 120 步统计接触关系。
- `--video-frames 120`：保存 120 帧。
- `--fps 12`：视频每秒 12 帧。

它不会弹出交互窗口，但会使用 GPU 离屏渲染。成功时显示：

```text
PASS scene=place_a_can_on_top_of_a_plate_fe0b76e316 fail=0 video_frames=120
```

输出目录：

```text
data/runtime/place_a_can_on_top_of_a_plate_fe0b76e316/
```

重要文件：

- `runtime_validation_report.json`：最终验证结果，重点看 `"status": "pass"`。
- `runtime_evidence.json`：接触、位置、稳定性、可见性和视频统计。
- `observer_runtime.mp4`：物理过程视频。
- `preview_head.png`、`preview_world_left.png`、`preview_world_right.png`：相机预览。

`missing pytorch3d` 或 OIDN 的 CUDA 日志目前不影响实际通过结果，应以最后的 `PASS` 和 JSON 报告为准。

## 六、运行提示词矩阵

```bash
conda activate robotwin-5090
cd /home/jingxiang/gujie/robot-harness-gen-env

python -m script.run_prompt_matrix \
  --matrix tests/fixtures/prompt_matrix.json \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --generated-objects-root /home/jingxiang/gujie/RoboTwin/assets/objects \
  --out-root data/prompt_matrix \
  --report data/prompt_matrix/report.json \
  --runtime \
  --robotwin-root /home/jingxiang/gujie/RoboTwin
```

作用：批量测试中英文提示词，包括堆叠、容器、左右/前后关系、柜门状态，以及应该被拒绝的非法场景。

- `--matrix`：测试用提示词和 seeds。
- `--generated-objects-root`：几何代理物体目录。
- `--out-root`：每个场景的输出位置。
- `--report`：总报告。
- `--runtime`：对成功案例执行真实物理回放。

默认每个成功案例只回放第一个 seed。总报告：

```text
data/prompt_matrix/report.json
```

单场景结果和日志：

```text
data/prompt_matrix/<case-id>/seed_<seed>/<scene-id>/
data/prompt_matrix/<case-id>/seed_<seed>/<scene-id>/runtime/runtime.log
```

如需所有 seed 都回放，再加 `--runtime-all-seeds`，但耗时会明显增加。

## 七、100-seed 静态验收

```bash
conda activate env-gen-sc311
cd /home/jingxiang/gujie/robot-harness-gen-env

python -m script.run_100_seed_acceptance \
  --prompt "Place a can on top of a plate." \
  --seed-count 100 \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-root data/acceptance/can_plate \
  --report data/acceptance/can_plate.json
```

作用：用 seed 0 到 99 把同一句话生成 100 次，检查不同随机位置下能否持续得到合法场景。

因为没有 `--runtime`，它不启动 SAPIEN、不生成视频，只做解析、位置求解和静态验证。默认最低通过率为 95%。

输出：

```text
data/acceptance/can_plate/seed_000000/
...
data/acceptance/can_plate/seed_000099/
data/acceptance/can_plate.json
```

理想结果：

```text
PASS pass=100/100 rate=1.000
```

## 八、可选：100 个 seed 全部做真实物理验收

```bash
conda activate robotwin-5090
cd /home/jingxiang/gujie/robot-harness-gen-env

python -m script.run_100_seed_acceptance \
  --prompt "Place a can on top of a plate." \
  --seed-count 100 \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-root data/acceptance/can_plate_runtime \
  --report data/acceptance/can_plate_runtime.json \
  --runtime \
  --robotwin-root /home/jingxiang/gujie/RoboTwin
```

作用：让 100 个随机场景都进入 SAPIEN 做真实掉落、碰撞、接触和稳定性验证。

默认所有 seed 都保存 JSON 物理证据，只有 seed `0`、`17`、`99` 保存 MP4。已有且哈希匹配的成功结果会复用；如需强制重跑，加 `--no-resume`。

## 九、推荐顺序

```text
1. scene_gen.catalog：扫描资产，通常只执行一次。
2. script.generate_scene：编译一个场景。
3. script.run_scene_runtime：真实回放一个场景。
4. script.run_prompt_matrix：批量测试多种提示词。
5. script.run_100_seed_acceptance：测试随机稳定性。
```





