# 目标局部几何

## 为什么不用外层 AABB 算「能不能放」

plate 是 230 mm 圆盘但只有内圈 100 mm 那块是平的、稳的；篮子外面 233 mm 但内空只有 190 × 112 mm。如果几何验证用外层 AABB 判「source 落不落得下 target」，立马出误报：source 看似落在 plate 上、其实悬在 plate 外缘，物理上一碰就翻。所以 `/gen-env` 的几何计算只在 target 自己声明的局部面上做，外层 AABB 一律不算 support。你看到这条压力的位置在 [一条真实路径 Step 4](../walkthroughs/one-real-run.md)。

几何数学全在 `scene_gen/support_geometry.py` 里——很短的文件，但承担「整条编译器敢不敢信几何结果」的全部责任。

## 三个关键概念

| 概念 | 来自 | 在几何里干啥 |
| --- | --- | --- |
| `support_surface_*` | override 或 catalog metadata | 声明 target 顶面哪块是稳定 support 面，可以是 box 或 circle；plate 给的是 100 mm 圆 |
| `interior_dimensions_m` + `interior_floor_z_offset_m` | override | 声明容器（basket/box）内部空间，从 object 底起算的偏移给到内底；basket 写 12 mm |
| `support_margin_m` | override | source footprint 必须超出 target support 面边界的最小余量，缺省 0.005 m，plate 实测 0.0080 m |

几何对自己 footprint 的歧义性也分清楚：`footprint_shape` 是 `box` 还是 `circle` 决定 `footprint_2d` 给的是矩形半边长还是半径；旋转时 box 的 half-x/half-y 随 yaw 重新算。

## `support_footprint_margin` 是核心算子

求解器、静态 validator、运行时 validator 都靠它判「source 落在 target 上时距离稳定边界的最小余量」。它接受 source 的 dimensions/yaw/shape、target 的 surface dimensions/yaw/shape、以及两者中心差 (dx, dy)，返回以米为单位的 margin——负数表示 source 已经超出 target 稳定面。

两个判断由它支撑：

- `on_top_of`：margin 必须 ≥ target 的 `support_margin_m`。plate 8 mm 余量不是「看上去中心对齐就好」，是「整圈最小余量 ≥ 8 mm」。
- `inside`：在这上面扩到「source footprint 必须能装进 target interior_dimensions」+「vertical 上 source bottom 不低于 interior_floor、top 不超过 interior_floor + interior_height」。

`support_surface_z` 用 `bottom_z`、`top_z`、显式 `support_surface_z_offset_m` 解析稳定面的高度——它不一定等于 object 的顶面（override 可写别的），是声明值。`sample_supported_offset` 给求解器在稳定面里随机采点，采不到满足 margin 的就返回 `None`。

## 实测 override 把这套喂上数字

`scene_gen/asset_overrides.yml` 写的是真实测出来的几何，不是编出来的数字：

```yaml
003_plate:
  models:
    "0":
      dimensions_m: [0.2297, 0.2300, 0.0280]
      footprint_shape: circle
      support_surface_shape: circle
      support_surface_dimensions_m: [0.1000, 0.1000]   # 实测稳定面
      support_surface_z_offset_m: 0.0060
      support_margin_m: 0.0080                          # 实测余量
      support_spawn_clearance_m: 0.0040
110_basket:
  models:
    "1":
      dimensions_m: [0.2332, 0.1512, 0.1767]
      interior_dimensions_m: [0.1900, 0.1120, 0.1450]
      interior_floor_z_offset_m: 0.0120                  # 实测内底偏移
```

仓库根 `AGENTS.md` 把「override 条目须有实测几何或文档化仿真器探测」当一条强契约。也就是说，加一个新 override 必须先测量或探测，不许「觉得 plate 应该有 80 mm 的稳定面就这样写」。

## 改动入口与验证

- 改稳定面/interior 几何：改 `asset_overrides.yml`，并跑 `pytest -q tests/scene_gen/test_catalog.py` 验 fixture catalog 还能与 override 一致。任何对 support/containment 契约的改动另需真机回放（见根 `AGENTS.md`）。
- 改 `support_footprint_margin` 算法：必须同时看 `solver.py:_pair_relation_reasons`、`validator.py:_relation_pass`、`run_scene_runtime.py:runtime_support_margin` 三处调用；它们必须对同一对 (source, target) 算出同一逻辑结果。
- 加新几何形状（非 box / circle）：改 `footprint_2d`、`support_surface_shape` 的 `Literal` 类型与解析器与 catalog 都要同步；当前只 `box`/`circle` 两种。
- 改完跑 `pytest -q tests/scene_gen/test_solver.py tests/scene_gen/test_builder_validator.py`，再视影响在真机过 SAPIEN 回放。

要回到主路径，去 [一条真实路径](../walkthroughs/one-real-run.md)。要继续追「求解器调用这套函数摆位、摆不下怎么回退」，去 [受限求解器](solver.md)。

证据状态：除特别标注外，本页基于当前源码已确认。