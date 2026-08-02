# 确定性代理

## 为什么 catalog miss 不会让编译报错、也不会变成「自由生成 3D」

`/gen-env` 的承诺是「有限自然语言中的物体都能进 RoboTwin」，但 RoboTwin 自己的 catalog 不可能覆盖所有可能词。两条直接出路都有问题：直接报错让 prompt 体验崩；用任意 text-to-3D 让自然的几何约束和稳定性保证都失守。第三条是 `scene_gen/asset_generator.py` 的做法：缺什么补什么——但只补一类受限几何代理，且把来源 catalog 缺失项一起带过来。这一步在 [一条真实路径 Step 5](../walkthroughs/one-real-run.md) 体现。

它有两类代理：

- **procedural_generated**：catalog 完全没候选时，按物体 category 给一个受家族约束的几何（hexagonal_prism / octagonal_prism / cylindrical_proxy / rectangular_pedestal / bounded_box_proxy），写 mesh + material + collision + `generation_provenance.json`。
- **derived_scaled_proxy**：catalog 有候选但它的 footprint 不能落在 nested support 面上、或运行时回放显示其原 collision mesh 不稳；则按一个确定性 uniform scale 把原 mesh 缩成可落、可稳的 primitive proxy。

两者都靠 `PRIMITIVE_PROXY_SOURCES` 与白名单严格收口。

## 模型怎么挑几何形

`_geometry` 按 category 匹配器返回形名 + 边数 + 默认尺寸：

| Category 含 | 几何家族 | 边数 | 默认尺寸 (m × m × m) |
| --- | --- | --- | --- |
| `hex` | hexagonal_prism | 6 | 0.09 × 0.09 × 0.075 |
| `oct` | octagonal_prism | 8 | 0.09 × 0.09 × 0.07 |
| `cylinder` 或 `column` | cylindrical_proxy | 24 | 0.075 × 0.075 × 0.09 |
| `pedestal` | rectangular_pedestal | 4 | 0.09 × 0.09 × 0.075 |
| `block` 或 `cube` | bounded_box_proxy | 4 | 0.055 × 0.055 × 0.04 |
| 其它 | bounded_box_proxy | 4 | 0.08 × 0.08 × 0.06 |

代表案（来自 `tests/scene_gen/test_asset_generator.py`）：catalog miss 一个 hexagonal prism 物体，`_write_proxy_asset` 写一个 OBJ + `material.mtl` + `generation_provenance.json`。攻击 `test_proxy_mesh_bounds_match_declared_dimensions` 验证 mesh 顶点边界对得上声明尺寸——「说自己多大就真的多大」。

## derived uniform scale 怎么算

`SCALE_HEADROOM = 0.92` 是缩放上限（缩完再保留 8% 余量），`MIN_DERIVED_SCALE = 0.35` 是缩放下限。算法把 source 原始 footprint 按比例缩到能在 `target.support_surface_dimensions_m` 内满足 `target.support_margin_m`，缩放因子落到 `[0.35, 0.92]` 之间才接受。低于 0.35 就拒——避免把物体缩到失真无意义。

代表案在 `docs/evidence/prompt-matrix-20260719.md`：「`004_fluted-block` 的 catalog footprint `92.275 × 90.626 × 65.293 mm` 满足不了 plate 的 8 mm 余量；编译器在 0.597 scale 上生成 primitive proxy，最终尺寸 `55.088 × 54.104 × 38.980 mm`，真实回放后保留 8.861 mm support margin、零穿透、终态静止、881 像素可见。」

### 当 derived 被触发

1. `004_fluted-block` 在 catalog 里有候选（不是 missing），但要么 `support_footprint_margin` 算负、要么原 collision mesh 在 SAPIEN 真机不稳。
2. `ensure_assets_for_scene` 走 `_write_proxy_asset(..., source=<GroundedSelection>, uniform_scale_factor=<factor>, relation=<on_top_of>, target=<GroundedSelection>, adaptation_reasons=<...>)`，写一个 `_primitive_proxy` 几何、新的 mesh + collision + provenance。
3. 新 catalog 条目的 `source_notes` 写 `geometry_compatible_derived`，下方的 `provenance.json` 记 `source_asset_id`、`source_model_id`、`uniform_scale_factor`、原 dimensions、生成文件哈希。
4. `ResolvedObject` 端填 `asset_provenance="derived_scaled_proxy"`、`derived_from_asset_id`/`derived_from_model_id`/`uniform_scale_factor`，并把 `generation_metadata_path` 指到 provenance 文件绝对路径。

`PRIMITIVE_PROXY_SOURCES = {("004_fluted-block", 0)}` 是显式白名单——只有这个 asset_id + model_id 对触发的 derived_scale 是「自动兼容缩放」。仓库根 `README.md` 写明「自动兼容缩放目前只限已通过真实 SAPIEN 回放的 block/cube 类」。要扩类必须先在真机过 SAPIEN 回放再扩白名单，否则不能自动缩。

## 为什么 lineage 写进 resolved

伦理上的理由是「不能让代理假装成原始 catalog 资产」。结构上的理由是 [哈希绑定包](replay-package.md) 的 manifest 与 `ResolvedSceneSpec.digest()` 都会基于 `ResolvedObject` 字段算哈希——derived 字段全在，意味着 catalog 改了或 scale 改了，包哈希立刻跳，能追溯。下游分析「这个 resolved 用的是真 catalog 还是 derived proxy」直接读 `asset_provenance` 与 `derived_*` 字段即可，不须 rediscover。

`scene_gen/schema.py:ResolvedObject.stable_pose_plus_yaw_only` 里有一段不变量专门管这条：derived 物体必须 lineage 字段全在，非 derived 物体不能带 lineage 字段。schema 自己保证 lineage 与 asset_provenance 同步。

## 改动入口与验证

- 扩 derived 白名单：先在真机过一组 SAPIEN 回放，记笔记到 `docs/evidence/<date>.md`，再把 pair 加进 `PRIMITIVE_PROXY_SOURCES`。
- 加新几何家族：在 `_geometry` 加分支、写好 `_prism_obj` 边数与默认尺寸；加测试断言 mesh bounds 对得上声明 dimensions。
- 改缩放下限：跑 `tests/scene_gen/test_asset_generator.py:test_incompatible_block_is_derived_at_a_uniform_scale_and_fits_plate` 看是否还成立。
- 改完跑 `pytest -q tests/scene_gen/test_asset_generator.py`。涉及 catalog / collision / loader 改动另需真机回放（根 `AGENTS.md`）。

要回到主路径，去 [一条真实路径](../walkthroughs/one-real-run.md)。要继续追「resolved 包怎么落盘自证」，去 [哈希绑定包](replay-package.md)。

证据状态：除特别标注外，本页基于当前源码已确认。