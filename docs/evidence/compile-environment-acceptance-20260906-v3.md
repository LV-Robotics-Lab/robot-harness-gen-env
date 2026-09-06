# Compile 完整环境验收（2026-09-06，v3）

## 结论

这次不再把单个资产转台当成 compile 结果。20 条输入都经过真实
`Text2EnvCompileHandler`，从 `Text2EnvCompileOutput.environment_package` 重建完整场景包，随后由
RoboTwin/SAPIEN 加载同一份 `resolved_scene.json`，输出工作台、机器人和目标物体同时可见的图片与
36 帧 MP4。

严格按用户原始口径，结论仍是 **部分通过**：

- 全新输入、缺失资产生成、入库和完整环境构建：`10/10`；
- 已入库资产按相同资产编号直接复用并构建完整环境：`5/5`；
- 本地资产库中的“同类别、同几何和同支撑用途”候选复用：`5/5`；
- 上游 ACDC / BEHAVIOR Digital Cousin 资产真实使用：`0/5`。

最后五条能证明 compile 可以用较宽松的任务条件选择已入库候选，但不能冒充 ACDC。当前 ACDC
子模块没有可用输出或 catalog，环境也缺少 `digital_cousins`、`omnigibson`、`groundingdino` 和
`sam2`；其 README 还要求显式接受并下载 BEHAVIOR/OmniGibson 数据许可。未获授权前没有自动接受
许可或下载约 70 GB 的两套资产数据。

## 复现命令

```bash
/home/jingxiang/miniconda3/bin/python script/run_compile_acceptance.py \
  --out-root data/compile_environment_acceptance_20260906_v3 \
  --seed 20260906 \
  --robotwin-root /home/jingxiang/workspace/robot-harness-gen-env/external/RoboTwin \
  --runtime-python /home/jingxiang/miniconda3/envs/robotwin-5090/bin/python \
  --preview-frames 36
```

批次根目录：

`/home/jingxiang/bingsheng/robot-harness-gen-env/data/compile_environment_acceptance_20260906_v3`

旧的 `v1` 目录没有覆盖。它保留了首次尝试中五条候选因为颜色/材质硬约束不匹配而被
`T2E_ASSET_UNAVAILABLE` 正确拒绝的证据。

## 证据怎么读

| 证据 | 路径 | 能证明什么 |
| --- | --- | --- |
| 20 条结构化总表 | `campaign_summary.json` | 输入、种子、终态、资产号、环境摘要、复用关系、媒体哈希 |
| 原始输入 | `inputs.json` | 固定随机种子产生的 10 + 5 + 5 输入，不是跑完后手挑 |
| 主 CAS | `state/cas/sha256/` | compile 运行和完整环境证据按内容哈希保存 |
| 可移植 CAS | `portable-cas/sha256/` | 只靠目标 CAS 独立回读每条运行收据和依赖闭包 |
| 全新入库资产 | `asset-library/generated/<asset_id>/` | OBJ、碰撞体、材质、metadata、provenance、v3 ledger |
| 精确复用 catalog | `catalogs/existing-reuse.catalog.json` | fresh-01 至 fresh-05 的五个资产进入第二轮选择 |
| 本地候选 catalog | `catalogs/digital-cousin-reuse.catalog.json` | fresh-06 至 fresh-10 被标为任务等价候选；不是 ACDC catalog |
| ACDC 预检 | `digital_cousins_preflight.json` | 子模块 commit、缺失导入、零可用输出以及 `used_by_this_campaign=false` |
| 文件清单 | `evidence_manifest.json` | 1,628 个文件的大小与 SHA-256；独立回算全部一致 |
| 单条完整收据 | `runs/<case>/portable_receipt.json` | Invocation、事件、终态和 artifact closure 的可移植收据 |
| 单条环境 | `runs/<case>/environment/` | request、SceneSpec、ResolvedSceneSpec、加载脚本、manifest、catalog 和验证报告 |
| 单条运行媒体 | `media/<case>/` | 两个世界视角、头部视角、分割图、首中末帧、36 帧 MP4 和 runtime evidence |

批次共有 10 个唯一资产编号。主 CAS 有 319 个对象，可移植 CAS 有 399 个对象；整个批次约
92 MB。20 个环境包均通过包内哈希复核和 SAPIEN load smoke；20 个视频都是 36/36 个不同帧。

## 逐组结果

### 1. 全新生成：10/10

每条从空 catalog 开始，开启 `generate_missing_assets`。缺资产时先生成 OBJ/MTL/碰撞体和 metadata，
再写 provenance 与 ledger、入库、更新有效 catalog，最后求解完整场景。

| 用例 | 输入摘要 | 入库资产编号 |
| --- | --- | --- |
| fresh-01 | blue ceramic slender bollard | `900_gen_slender_bollard_0ea95028` |
| fresh-02 | pink metal rounded caddy | `900_gen_rounded_caddy_7e5d9572` |
| fresh-03 | purple glass ribbed obelisk | `900_gen_ribbed_obelisk_5b266bdd` |
| fresh-04 | green ceramic low riser | `900_gen_low_riser_b48ecdc5` |
| fresh-05 | pink plastic square marker | `900_gen_square_marker_58006ccb` |
| fresh-06 | white glass octagonal plinth | `900_gen_octagonal_plinth_3c70a118` |
| fresh-07 | red plastic hexagonal pedestal | `900_gen_hexagonal_pedestal_2b168a85` |
| fresh-08 | yellow metal stout tower | `900_gen_stout_tower_6ee2c88e` |
| fresh-09 | blue wooden grooved puck | `900_gen_grooved_puck_9dccac6e` |
| fresh-10 | white glass tapered stand | `900_gen_tapered_stand_50e257c4` |

这些资产是当前确定性 `procedural_proxy`，不是 EmbodiedGen 高保真生成物。多数未识别形状会退化成
有界 box；因此它们“能被仿真器加载”，但视觉质量和语义外形仍很初级。

### 2. 精确复用：5/5

reuse-01 至 reuse-05 分别复用 fresh-01 至 fresh-05。五条均关闭资产生成，并同时满足：

- `selected_asset_id == source_asset_id`；
- 没有 generation artifact；
- 没有 admission artifact；
- 新环境使用新的场景种子和新的环境 digest；
- 资产编号和源文件仍是第一次入库的同一份内容。

例如 `runs/reuse-01/summary.json` 中可见
`900_gen_slender_bollard_0ea95028 → 900_gen_slender_bollard_0ea95028`。

### 3. 本地任务等价候选：5/5；上游 ACDC：0/5

首次 v1 尝试把颜色和材质也改了，编译器没有假装旧资产符合要求，而是五条全部阻断。v2 起删除了
目标请求中的外观硬条件，只保留类别、几何和 `on_table` 用途，再从 fresh-06 至 fresh-10 选择候选。
这五次都选择了对应源资产编号，并且没有生成或再次入库。

这是一条真实的“宽条件候选复用”路径，但它不等于 ACDC：每份
`digital_cousin_selection` 都明确记录 `upstream_acdc_image_pipeline_executed=false`，预检也记录
`used_by_this_campaign=false`。所以用户要求的五条 ACDC/BEHAVIOR 资产仍不能计为通过。

## 图片和视频

- 新生成环境汇总：`media/fresh-contact-sheet.png`
- 精确复用环境汇总：`media/reuse-contact-sheet.png`
- 本地候选复用环境汇总：`media/digital-cousins-contact-sheet.png`
- 任意单条视频：`media/<case>/observer_runtime.mp4`

这些媒体来自完整场景的短时 SAPIEN 加载，不是单资产渲染。它们证明环境包可重建、机器人/桌面/
目标物体可以一起加载、连续帧可以写出。它们不用于给物理质量盖章；沉降、漂移、接触窗口、任务
完成和发布资格仍应由正式 replay/validate 判断。

## 校验记录

- 新增验收计划与证据门测试：`6 passed`；先看到错误颜色/材质候选测试失败，再修正输入规则。
- compile application、handler、portable receipt、核心 compiler 和 runtime 定向回归：`121 passed`。
- lint 与 Python 编译检查：通过。
- `evidence_manifest.json` 独立逐文件 SHA-256 回算：1,628/1,628 一致。

## 下一步与阻塞项

compile 的“完整环境输出”主链已经跑通，可进入 replay/validate 批量验收。但若要把原始验收中的
Digital Cousin 项也判为通过，需要用户先明确授权接受第三方数据许可和大体积下载，然后完成：

1. 安装 ACDC/OmniGibson 运行环境并下载官方所需数据；
2. 用真实图片跑 ACDC step 1–3，固定输入、输出和 asset model id；
3. 新增 OmniGibson package 或有许可依据的 RoboTwin adapter，不能直接把受限资产复制进 CAS；
4. 用五个真实 ACDC 资产重跑 compile 环境构建和媒体证据；
5. 再把全部环境交给正式 replay/validate，不以本报告的 load smoke 代替物理验收。
