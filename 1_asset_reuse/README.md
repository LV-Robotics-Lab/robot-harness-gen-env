# 1_asset_reuse — 资产复用（阶段 1）

让 RoboTwin 和 Isaac/USD 生态两边的资产互通、并都能被 env-gen 全流程复用。
三条线：**A** RoboTwin→Isaac USD；**B** 外部 USD→RoboTwin 布局；**C** 注册进扩展
catalog 被文字场景真实选中。全景与逐文件说明见本目录 `OVERVIEW.md`。

## 结构

| 路径 | 作用 |
|---|---|
| `scripts/` | 全部步骤脚本（打样样本版 s0–s10 + 批量导入 import_*） |
| `configs/external_manifest.json` | 外部资产批量清单（来源 URL、资产归组、类别/别名/footprint） |
| `../data/asset_library/` | 外部资产池（RoboTwin 布局；`_source/` 存源镜像+哈希清单；不入 git） |
| `../data/robotwin_shadow/` | 影子根（symlink 真 RoboTwin + 注入外部资产；不入 git） |
| `../data/scene_gen_ext/` | 扩展 overrides + catalog（上游扫描器产出；不入 git） |

## 用法

```bash
export OMNI_KIT_ACCEPT_EULA=YES
# 打样回归（A+B+C 样本，11 步）
bash scripts/run_smoke.sh
# 批量导入外部资产（按 configs/external_manifest.json）
~/miniconda3/envs/isaac-smoke/bin/python  scripts/import_fetch_convert.py \
  --manifest configs/external_manifest.json \
  --source-root ../data/asset_library/_source --staging <staging目录>
~/miniconda3/envs/env-gen-yuxin/bin/python scripts/import_materialize.py \
  --staging <staging目录> --library-dir ../data/asset_library \
  --out <结果目录> --overrides-fragment ../data/scene_gen_ext/external_overrides_fragment.yml
# 重建影子根 + 扩展 catalog（消费上面的 fragment）
~/miniconda3/envs/env-gen-yuxin/bin/python scripts/s9_build_shadow_root.py \
  --library-dir ../data/asset_library --shadow ../data/robotwin_shadow \
  --ext-dir ../data/scene_gen_ext \
  --extra-overrides ../data/scene_gen_ext/external_overrides_fragment.yml
# e2e 回归（文字→场景选中外部资产→回放→全量验证）
bash scripts/s10_e2e_scene.sh
```

前置：conda `env-gen-yuxin`（SAPIEN 侧）+ `isaac-smoke`（Isaac Sim 5.1，py3.11）。

## 批量验收纪律（4.5 对齐）

- 每模型硬门：settle 位移<2mm、无穿模（原点 z>-2mm）、直立（<15°；自然平躺物 <45°）。
- 未过门的模型进 `import_matrix.json` 淘汰记录（含原因），**不进 catalog**。
- 来源→转换→产物全程 sha256；质量/许可证等未知项标结构化 unknown。
- 判定一律以产物内容为准（Kit 吞退出码）。

## 已知限制（当前版本事实）

- 碰撞体 = 视觉网格副本（加载时凸分解）；容器类内腔任务需 CoACD 精化。
- 穿模判据基于原点 z，对翻滚静止的物体（如 banana）过严——保守淘汰。
- A 线脚本（s1/s2）绑定打样样本 bottle/cabinet；RoboTwin 全库批量时再参数化。
- 外部资产许可证标 unknown（NVIDIA EULA / YCB 条款），再分发前必须补查。
- 质量默认 0.1kg（结构化未知记录在各 bundle）。
