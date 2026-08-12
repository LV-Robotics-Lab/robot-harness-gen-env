# 一条真实路径：把一句 prompt 编译成 SAPIEN 验证过的场景

这一页跟通一条真实路径：一句受限的中英文 prompt 进编译器，在几秒内变成一份哈希绑定的 `ResolvedSceneSpec` 包，再在 RoboTwin/SAPIEN 里物理回放，最后被一组运行时门控放过。被跟的具体行为是仓库 README 引的那条命令：

```bash
python script/generate_scene.py \
  --prompt "Place a can on top of a plate." \
  --seed 42 \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-root data/generated_scenes
```

短到一句话的版本：受限解析 → 类型化契约 + 资产 grounding → 目标局部求解 → 哈希绑定包 → SAPIEN 回放采集 → 运行时门控。这个路径的难点不是某一步本身，而是它跨越的两条信任边界：一条是文本到机器人意图（prompt 能不能干净地只带语义、绝不夹带后端字段或 pose），另一条是看起来稳到物理稳（外层 AABB 看着能落、其实 plate 里圈 100 mm 那块才是稳定面；渲染图能过、其实 SAPIEN 终末接触还过不去）。每一步存在的理由都是在某一类塌方之前先把它挡住。若「为什么不能用外层 AABB」、「为什么 120 帧」这类问题读完本页还模糊，去 [代码地图](../code-map.md) 找源码入口，或读对应模块：[受限解析](../modules/bounded-parser.md)、[类型化场景契约](../modules/scene-contract.md)、[目标局部几何](../modules/target-local-geometry.md)、[受限求解器](../modules/solver.md)、[确定性代理](../modules/derived-proxy.md)、[哈希绑定包](../modules/replay-package.md)、[运行时门控](../modules/runtime-gates.md)。

## Step 1: 句子先被收进受限解析器，绝不变成代码或 pose

prompt 进入系统的第一件事不是被理解，而是被收紧。解析器只接受一个有界的子集——桌面物体名 + 颜色/材质/区域 + on top of / inside / left of / right of / front of / behind / near / distance 至少 这几种关系。它显式拒绝可执行代码、文件系统路径、后端字段名（`asset_id`、`qpos`、`quaternion`、`world_xyz` 等）、坐标赋值、坐标元组，以及 `between`、`align` 这类尚未支持的 MVP 特性。这一步把「文本输入」压缩成「语义意图」：剩下的是一个 `SceneSpec`，没有路径，没有 id，没有 pose。

具体到这里这条 prompt：解析器先在 `OBJECT_TERMS` 词典里认出 `can` 和 `plate` 两个提及，给它们分配 `can_1` 与 `plate_1` 这两个稳定 id；再从两个提及之间的词判断出 `on_top_of` 关系，所以 `can_1` 是 nested source、不再分配 `on_table`，而 `plate_1` 拿一条 `on_table`。最终输出 `SceneSpec`。规则在 `scene_gen/parser.py:parse_rule_based` 与 `extract_mentions`/`_relation_between`；边界拒绝在 `validate_prompt_boundary` 和 schema 的 `FORBIDDEN_SCENE_KEYS`。这一步的设计原因在 [受限解析](../modules/bounded-parser.md)，类型化字段在 [类型化场景契约](../modules/scene-contract.md)。

## Step 2: 类型化契约把它锁成不可变、可复现的spec

解析器吐出的还是普通字典形状，下一步是让它穿过 `SceneSpec` 这道 pydantic 契约。`SceneSpec` 是 `extra="forbid"` 且 `frozen=True`：传入未知字段直接报错，对象之间唯一性、每个对象必须有且只有一条 support 关系、left/right/front/behind 三组轴向约束不能成环、`distance_at_least` 不能和 `near` 的上限矛盾——这些跨字段语义由 `model_validator` 在构造时一次过完。`seed` 来自命令行，写在 spec 里，后面所有随机都从它派生。`scene_id` 也是从 prompt 派生的稳定串。

到这一步，系统手里的是一份不可变、可哈希的 `SceneSpec`；`SceneSpec.digest()` 是后续绑定的根。任何一个下游阶段如果想悄悄改意图，diff 就会跳出来。契约的完整字段和跨字段不变量在 [类型化场景契约](../modules/scene-contract.md)。如果一个 prompt 走到这里失败（比如出现禁用键或关系成环），`generate_scene.py` 不会继续，而是写到 `_failures/<id>/failure_report.json`，schema 里抛的是 `SceneSpecError` 或 pydantic `ValidationError`。

## Step 3: 资产 grounding 把每个 object 摆到真实 RoboTwin 模型上

接下来要做的是：每个 object 提及，都要落到一个真实存在、可用、有碰撞、有尺寸的 catalog 模型上。`grounding` 按 category 精确匹配、`semantic_name` 匹配、alias 匹配三档打分；color/material 元数据在 catalog 里有就加分、有但不符就拒、为空就保留 query；最后按可碰撞可用、有 normalized dimensions 加分，并按 `seed` + object_id + asset_id + model_id 算的确定性 tie-break 排序，选出唯一赢家。所有被拒候选（含不可用、缺碰撞、被更高分挤掉）保留进 `rejected_candidates`，最多 25 条，机读可追溯。

这一步的设计理由是「自然语言里说 `can`，模型库里可能有十几个 can，但必须挑出同一个」。确定性来自 seed——同一个 catalog + 同一个 spec + 同一个 seed 必出同一个 `ResolvedObject` 集合。函数在 `scene_gen/grounding.py:ground_object` 与 `ground_scene`，攻击在 `tests/scene_gen/test_grounding.py:test_grounding_is_reproducible_for_fixed_catalog_query_and_seed`。catalog 自身从 RoboTwin checkout 扫描，实测尺寸/朝向/关节用 `scene_gen/asset_overrides.yml` 覆盖——比如 `003_plate` 的 100 mm 稳定面、`110_basket` 的 12 mm 内底偏移——这些 override 必须有实测几何或文档化仿真器探测，不许编尺寸。

## Step 4: 求解器按目标局部几何逐个摆位，摆不下就回退

grounding 选完模型后，求解器才进场摆 (x, y, yaw, z)。它不是贪心，是 bounded rejection backtracking：按 support depth + 反向 degree + object_id 排序，先摆桌上的（depth 0）、再摆嵌套的（depth 1+）；每个物体最多 96 次随机尝试，最多 48 次回退。每次尝试按物体声明的 support 关系选约束：`on_table` 在工作区里随机；`on_top_of` 在 target 的 `support_surface_*` 几何里采样，且 source footprint 要落在 target 局部稳定面 + 余量内；`inside` 在 target 的 `interior_dimensions_m` 内采样，bottom 落到 `interior_floor_z_offset_m`。

关键设计点是「目标局部几何」。不在外层 AABB 上算「能不能放下」——plate 圆面 230 mm 见方但只有内侧 100 mm 那块是实测稳定的，外缘算上来一眼能落但物理上一碰就翻。求解器读的是 override 写明的 `support_surface_dimensions_m = [0.1000, 0.1000]` 与 `support_margin_m = 0.0080`，全靠 `scene_gen/support_geometry.py` 里的 `support_footprint_margin`、`sample_supported_offset`、`support_surface_z`。摆不下就记 reason 进 `SolverAttempt`，继续采样；96 次都失败就回退上一物体；回退也耗尽就抛 `SceneSolveError`，里头带完整 attempts trace，对应 resolved-scene 目录下的 `failure_report.json`。机制在 [受限求解器](../modules/solver.md)，几何在 [目标局部几何](../modules/target-local-geometry.md)。

## Step 5: catalog miss 或不稳时用确定性代理替代，且携来源 lineage

不是每个 object 都能在 catalog 里挑到 usable 模型。本案里 `004_fluted-block` 的 catalog collision mesh 在真实 SAPIEN 里不稳，原始 footprint 也满足不了 plate 的 8 mm 余量。编译器不会用「找一个最像的」糊弄过去：当 catalog miss 或运行时不稳时，`asset_generator` 走确定性代理——procedural primitive 或 derived uniform scale。代理生成 mesh + collision + `generation_provenance.json`，里头写 `source_asset_id`、`source_model_id`、`uniform_scale_factor`，可追溯到它替代的 catalog 缺失项。

自动 scale 受严格限制：`SCALE_HEADROOM = 0.92`、`MIN_DERIVED_SCALE = 0.35`，且当前只白名单 `PRIMITIVE_PROXY_SOURCES = {("004_fluted-block", 0)}`——意味着自动兼容缩放仅限已通过真实 SAPIEN 回放的 block/cube 类。`ResolvedObject` 会把 `asset_provenance` 设成 `derived_scaled_proxy`（或 `procedural_generated`），并把 `derived_from_asset_id`/`derived_from_model_id`/`uniform_scale_factor` 写进 resolved 里，下游看得到。这一步是为了让 prompt 不必因 catalog 不全就报错，又不让「生成一个 3D 物体」变成无界的自由。机制在 [确定性代理](../modules/derived-proxy.md)。

## Step 6: builder 把 resolved 写成哈希绑定的回放包

求解成功后，`build_scene_package` 在 `data/generated_scenes/<scene_id>/` 下写五个文件：`request.txt`、`scene_spec.json`、`resolved_scene.json`、`generated_scene.py`、`package_manifest.json`。manifest 里记录每个文件的 SHA-256、大小，外加 `source_scene_spec_sha256`、`resolved_scene_sha256`、`asset_catalog_sha256`、`compiler_version`、`entrypoint`、`resolved_only_entrypoint`。`builder.py:build_scene_package` 在落盘前先验 `resolved.source_scene_spec_sha256 == spec.digest()`、`scene_id` 与 `seed` 相同——resolved 与 spec 不绑同一根就拒写。

`verify_package` 反过来再算一遍：每个文件 SHA-256 是否对得上、`resolved_scene.json` canonical digest 是否对得上 manifest 里的 `resolved_scene_sha256`。攻击用例 `test_package_verifier_detects_tampering` 锁住篡改检测。也就是说从这一步起，包是自证的：拿到包就能在不信任生成环境的前提下，确认它没被改过、且和当初过契约的 spec 是同一份意图。结构在 [哈希绑定包](../modules/replay-package.md)。这一步也是 demo API 对外暴露的「每个 job 的产物根」。

## Step 7: 在 RoboTwin/SAPIEN 里真实回放并采到物理证据

编译做完静态 validator 后（验几何关系、bounds、roundtrip、manifest 完整性，但不验物理），真正物理证据要靠 SAPIEN 回放拿。运行时不跑在 CI 里；要跑需要 RoboTwin checkout 与 SAPIEN 环境：

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

`run_scene_runtime.py` 把 `--precheck-steps 0` 当默认是有意的：不预步进，从物理释放立刻开始记录，避免把不稳初态躲到证据之前。`total_steps = max(settle_steps, video_frames)`；视频采样 119 个释放帧 + 最后 1 个 settled 帧（`runtime_sampling.video_sample_steps`），所以 120 帧不是「随便抽 120 张」，而是把释放全过程留下来再加终态。终末 120 步里每步采一次 contact，统计 support contact fraction 和 unexpected contact fraction。终态读 position/orientation/qpos，算 drift、resolved 误差、可见像素、穿透计数、是否仍在动、是否掉落。回放入口走 `scene_gen/envs/generated_scene.py:load_resolved_scene`——只按 resolved 字段构建 SAPIEN actor，颜色 override 通过 material 改，articulation 用 `set_qpos` + drive 属性对准，绝不执行用户代码。

为什么 900 步？README 里给了一条具体证据：当前的 apple-in-basket 在 300 步时仍有可测运动，900 步是按这条测试定下来的。这条数不是普适物理参数，是当前 asset pair 的实测值。

## Step 8: 运行时门控把物理证据转成 pass / fail

回放完，`scene_gen/validator.py:validate_resolved_scene` 把 `runtime_evidence.json` 翻译成一组 checks。固定物体（`is_static=True`）按精确终态门控：translation drift ≤ 20 mm、rotation drift ≤ 5°、resolved translation error ≤ 20 mm、resolved rotation error ≤ 5°。动态物体（`is_static=False`）按最终接触与关系门控：support target 接触 fraction ≥ 0.8、对未声明 support target 的接触 fraction 必须为 0、不穿透、不仍在动、不掉落、首视角可见像素 ≥ 64。`on_top_of` 还要 runtime support margin 满足 target 的 `support_margin_m`；`inside` 要 `runtime_inside_containment=True`。视频最少 3 帧、互异帧 ≥ `min(120, 30)`。articulation qpos 误差 ≤ 0.02。

关键设计是「静态 + `on_table`」可以走 `fixed_static_pose` 分支（接触对他们不是必须的，因为它们就钉在桌上）；但「静态 + nested」不可——`test_runtime_validator_rejects_static_contact_free_nested_support` 就是攻击这条误报模式：曾经允许静态物体摆上 nested support 而不接触、只看 AABB，导致静态堆叠被假阳性放过，现在被拒绝。同样被攻击测试锁住的还有「只在某几帧接触 nested target」（intermittent contact）和「nested source 又碰到了桌子」。门控的完整阈值与失败模式在 [运行时门控](../modules/runtime-gates.md)。

最终结果是 `runtime_validation_report.json` 里的 `status ∈ {pass, incomplete, fail}`。`generate_scene.py` 不跑运行时，所以它的 `validation_report.json` 至多是 `incomplete`——只有 `run_scene_runtime.py` 把 runtime_evidence 传进来并且 require_runtime=True 时，pass 才是真正的物理通过。这条同一行为，已被仓库实测验证：`docs/evidence/prompt-matrix-20260719.md` 记录了 2026-07-19 在 RTX 5090 主机上 33/33 编译 + 10/10 SAPIEN 回放通过，每个 120 帧 MP4 至少 100 互异帧。

跑下面命令可验证整条无 RoboTwin 依赖的契约层：

```bash
pytest -q
```

要验运行时门控的失败分支但不在真机里，看 `tests/scene_gen/test_builder_validator.py` 里的 `test_runtime_validator_rejects_static_contact_free_nested_support`、`test_runtime_validator_rejects_intermittent_nested_contact`、`test_runtime_validator_rejects_nested_source_contacting_table`、`test_static_validator_rejects_edge_placement_even_inside_outer_plate_bounds`、`test_static_validator_rejects_target_local_container_overflow` 这几个攻击用例。

证据状态：除特别标注外，本页基于当前源码已确认。