# 术语表

本仓库里反复出现、容易误解的术语与缩写。机制深度去对应模块页。

| 术语 | 项目里的意思 | 延伸阅读 |
| --- | --- | --- |
| SceneSpec | 受限解析的输入契约。pydantic `StrictModel` 锁死的不可变类型化 spec；只含语义字段，明确不含 `asset_id`/`pose`/`position` 等后端字段。常被误认为「用户传的原始字典」，其实它已经是契约后的产物。 | [类型化场景契约](modules/scene-contract.md) |
| ResolvedSceneSpec | 编译输出契约。带每个 object 的资产绑定、pose、footprint、稳定朝向、来源 lineage，外加 `source_scene_spec_sha256` 与 `asset_catalog_sha256` 把上游哈希写进自己。常被误认为「SceneSpec 加一些字段」，其实它跨过了三道门（schema、grounding、solver）。 | [类型化场景契约](modules/scene-contract.md) 与 [哈希绑定包](modules/replay-package.md) |
| SkillDescriptor | Harness 中一个精确 `(skill_id, version)` 的不可变描述，记录 MCP 名、输入输出 schema、实现摘要、确定性、attempt 上限和资格制品。它是注册候选，不等于已经注册或可执行。 | [Harness Schema Tranche](modules/harness-schema-tranche.md) |
| ArtifactRef | Harness 的制品引用。`uri` 只定位；可信内容身份由 `media_type + schema_version + sha256` 组成。类型化制品的 schema version 是字符串，无类型媒体必须显式为 `null`。 | [Harness Schema Tranche](modules/harness-schema-tranche.md) |
| RunState | Harness 的 run 生命周期快照，携连续 Event、attempt、制品、类型化 output 或终止 blocker。`succeeded` 只表示 handler 正常产出，不自动等于 validation pass 或 publishable。 | [Harness Schema Tranche](modules/harness-schema-tranche.md) |
| EnvironmentPackage | 对现有 `scene_gen` 哈希绑定包的不可变引用，不是第二套包格式。`package_id` 等于 resolved scene 摘要，内部引用 catalog 和 package manifest。 | [Harness Schema Tranche](modules/harness-schema-tranche.md) 与 [哈希绑定包](modules/replay-package.md) |
| support surface / support 目标局部几何 | target 顶面被实测声明为「稳定可承」的一块，独立于 target 外层 AABB。`003_plate` 是 100 mm 内圆、`110_basket` 又有自己的 interior。常与「物体顶面」混淆——稳定面是 override 显式声明，可以是某高度 offset 上的局部一段。 | [目标局部几何](modules/target-local-geometry.md) |
| support margin | source footprint 必须超出 target 稳定面边界的最小余量。`003_plate` 实测 8 mm。常被误读为「视觉余量」，其实是几何算子 `support_footprint_margin` 的最小一圈取值。 | [目标局部几何](modules/target-local-geometry.md) |
| interior_dimensions_m + interior_floor_z_offset_m | 声明容器内部可用空间的「水平尺寸 + 高度」与「内底从 object 底起算的偏移」。用于 `inside` 关系几何与运行时 `runtime_inside_contained`。常被误以为「外径减壁厚」，实际全部直接来自 override 实测。 | [目标局部几何](modules/target-local-geometry.md) |
| bounded rejection backtracking | 求解器的算法：每物体最多 96 次随机采样、最多 48 次回退，按 support depth 排序摆位。常被误以为「全局最优 / 暴力穷举」，实质只是固定时间下界内的可机读失败 trace。 | [受限求解器](modules/solver.md) |
| SolverAttempt | 一次采样尝试的机读记录，含 `(x, y, yaw, accepted, reasons)`。完整 attempts 序列进 `ResolvedSceneSpec.solver_trace.attempts`。追「为什么这次摆不下」直接读这一行。 | [受限求解器](modules/solver.md) |
| SceneSolveError | 求解失败抛的异常。其 `report` 是机读 dict，schema 版本 `robotwin.scene_solver_failure.v1`，含 blocker、max_attempts、attempts trace。常被读者误以为普通 `RuntimeError`——实质是带结构化报告的失败产物。 | [受限求解器](modules/solver.md) |
| derived_scaled_proxy | 当 catalog 候选 footprint 满足不了 nested support、或原 collision mesh 在 SAPIEN 真机不稳时，编译器按一个 uniform scale 生成可落可稳的 primitive proxy。`ResolvedObject.asset_provenance="derived_scaled_proxy"` 时还必须带 `derived_from_*` 字段。 | [确定性代理](modules/derived-proxy.md) |
| procedural_generated | catalog 完全无候选时由按 category 家族生成的受限几何代理（hex/oct/cyl/pedestal/bounded_box）。受家族约束、不是任意 text-to-3D；携 `generation_provenance.json`。 | [确定性代理](modules/derived-proxy.md) |
| PRIMITIVE_PROXY_SOURCES | 显式白名单 `{("004_fluted-block", 0)}`。只有这里登记的 asset_id + model_id 对才会触发自动 derived 缩放。常被读者误以为「所有 block/cube 都自动缩」，实质是已通过真机回放的少数对。 | [确定性代理](modules/derived-proxy.md) |
| package_manifest | `data/generated_scenes/<scene_id>/package_manifest.json`。含四文件每条的 SHA-256 与 bytes，外加 scene_id/seed/上游哈希根/compiler_version/entrypoint。`verify_package` 反向对账。常被误读为「文件清单」，其实是自证根。 | [哈希绑定包](modules/replay-package.md) |
| runtime_evidence | `script/run_scene_runtime.py` 写出的 `runtime_evidence.json`，schema 版本 `robotwin.scene_runtime_evidence.v2`。包含每物体终态 pose/drift/contact_fraction/visibility/penetration、视频 frame_count + unique_frame_count、终末 contact relations 等。validator 拿它转 checks。常被误以为「视频 + 截图」，实际重点是结构化终末数据。 | [运行时门控](modules/runtime-gates.md) |
| fixed_static_pose 模式 | `is_static = True ∧ on_table ∧ support_mode = fixed_static_pose ∧ not_dropped` 时的支持 stem：静态桌面物体不被强求 contact fraction≥0.8（因为它们就是钉死的）。常被误读为「静态就能放过嵌套」，实质 [运行时门控](modules/runtime-gates.md) 的 attack 测试专门拒这种假阳性。 | [运行时门控](modules/runtime-gates.md) |
| min_support_contact_fraction | 动态物体在终末 120 步采样窗口里必须与声明 target 接触的最小 fraction，缺省 0.8。嵌套源接触未声明 target 的 fraction 必须为 0。常与 `support_margin` 混淆——前者是物理接触频率，后者是几何余量。 | [运行时门控](modules/runtime-gates.md) |
| rendered_critic | 可选 VLM（Qwen-VL）渲染评判，只查 preview_* 图里的可见语义。schema 标 `vlm` 才装。常被误以为「投资一片图就能取代物理门控」——它显式不是物理证据。 | [运行时门控](modules/runtime-gates.md) |
| acceptance | 批量验收层 `scene_gen/acceptance.py`；只做 `pass_rate >= 0.95` 聚合，不复判单跑。常被误认为「整体判定函数」，其实是统计外层。 | [代码地图](code-map.md) |
| FORBIDDEN_SCENE_KEYS | `SceneSpec` 在 schema 端拒绝的输入键集合（如 `asset_id` / `asset_path` / `model_id` / `pose` / `position` / `qpos` / `quaternion` / `orientation` / `python` / `code`）。即使 provider 路径返回带这些字段的 JSON 也会在 `model_validator(mode="before")` 阶段被拒。常与 `FORBIDDEN_PROMPT_PATTERNS` 混淆——后者在 parser 文本端拒绝、前者在 schema 字典端拒绝，相邻但独立。 | [受限解析](modules/bounded-parser.md) |
| FORBIDDEN_PROMPT_PATTERNS | parser 在 prompt 文本上跑的正则拒绝集：可执行代码、文件系统路径、后端字段名、坐标赋值、坐标元组。命中任一直接抛 `SceneSpecError`，prompt 不会进入 catalog/grounding 阶段。 | [受限解析](modules/bounded-parser.md) |
| extra="forbid" | `StrictModel` 对所有 pydantic 契约设的字段策略——多传一个未知字段就报错。挡住 provider 走私，挡住下游误传 dict 把字段偷偷加进 spec。 | [类型化场景契约](modules/scene-contract.md) |
| frozen=True | `StrictModel` 对所有 pydantic 契约设的不可变性——构造后改字段直接报错。让 resolved spec 在 builder 与 manifest 哈希层不会被偷改。 | [类型化场景契约](modules/scene-contract.md) |
| generated ↔ generated | 运行时 contact 分类用到的简写，指 SAPIEN 场景里两个由编译器生成的 actor 互相接触。它们若是彼此声明的 support target，计入 support contact；否则计为 unexpected target 与潜在穿透。 | [运行时门控](modules/runtime-gates.md) |
| OBJECT_TERMS | `scene_gen/parser.py` 里把英文/中文物体词映射到 catalog category 的双语词典常量。新增物体词须改这里，并同步补 `asset_overrides.yml` / catalog 的真实模型。 | [受限解析](modules/bounded-parser.md) |
| target.support_margin_m | runtime validator / solver 在判 `on_top_of` 源能否落稳时，从目标物体读取的稳定面 margin 字段，等价于 schema 端 `ResolvedObject.support_margin_m`（plate 实测 `0.0080 m`）。 | [目标局部几何](modules/target-local-geometry.md) |
| --precheck-steps 0 | `script/run_scene_runtime.py` 的默认 precheck 模式：不在物理释放前预步进。有意为之——保留释放全过程证据，避免把不稳初态躲到记录之前。 | [运行时门控](modules/runtime-gates.md) |
| MIN_DERIVED_SCALE = 0.35 | `scene_gen/asset_generator.py` 的 derived uniform scale 下限——缩到低于 0.35 直接拒，避免把物体缩成失真无意义。配上限 `SCALE_HEADROOM = 0.92`，且当前只对 `PRIMITIVE_PROXY_SOURCES` 白名单生效。 | [确定性代理](modules/derived-proxy.md) |
| `SceneSpec.digest()` / `ResolvedSceneSpec.digest()` | 各类型化契约上的 canonical-JSON SHA-256。`SceneSpec.digest()` 是意图根，进入 `ResolvedSceneSpec.source_scene_spec_sha256`；`ResolvedSceneSpec.digest()` 是产物根，进 `package_manifest.json` 的 `resolved_scene_sha256`。同一 spec+catalog+seed 必出同一 digest。 | [类型化场景契约](modules/scene-contract.md) 与 [哈希绑定包](modules/replay-package.md) |
| `SceneSpec.semantic_consistency` | `SceneSpec` 构造完后跑的跨字段不变量总闸：object 唯一、每个 object 恰有一条 support 关系、关系不成环、`near` 与 `distance_at_least` 不矛盾、`on_table` 必须指 table 等。任何一条不满足抛 `SceneSpecError`。 | [类型化场景契约](modules/scene-contract.md) |
| `is_static=True` / `is_static=False` | `ResolvedObject.is_static` 开关。`is_static=True` 走「精确终态门控」（drift ≤ 20 mm、≤ 5°）+ 「`on_table + fixed_static_pose`」支持模式；`is_static=False` 按 final contact + runtime relations 验。静态物体不能当 nested support 的 source（解析器与 solver 都拒）。 | [运行时门控](modules/runtime-gates.md) |
| `generated ↔ table` | `summarize_contacts` 里 contact 对的简写。`generated ↔ table`：若 table 是该生成对象的 expected support target，计入 support；若 table 非声明 nest target（即不该接触桌子却接触了），就是 unexpected_target，会让 validator 拒。与 `generated ↔ generated` 同一族。 | [运行时门控](modules/runtime-gates.md) |
| `place(index+1)` | `scene_gen/solver.py` 内 `solve_scene` 闭包里的递归调用。当前物体被接受后递归摆下一个；递归失败就 pop 当前物体、`backtracks += 1`。bounded rejection backtracking 的核心递归 step。 | [受限求解器](modules/solver.md) |
| `protected_namespaces=()` | `StrictModel` 在 `ConfigDict` 上设的 pydantic 选项——交还 `model_*` 前缀字段给用户使用而不被 pydantic 的「protected」守门拒掉。与 `extra="forbid"`、`frozen=True` 一起构成契约严格性的三位一体。 | [类型化场景契约](modules/scene-contract.md) |
| `request.txt` | `build_scene_package` 写出的 resolved 包里五个文件之一，内容是用户原始 prompt + 结尾换行。被 `package_manifest.json` 列入 SHA-256 核对清单——表明 prompt 与产物绑定可追溯。 | [哈希绑定包](modules/replay-package.md) |
| `runtime_sampling.video_sample_steps` | `scene_gen/runtime_sampling.py` 里的无依赖采样函数：给 `total_steps` 与 `requested_frames`，给出 `119 释放帧 + 1 终末帧` 那种保证连续性的步采样下标。被 `run_scene_runtime` 与 `test_prompt_matrix` 共用。 | [运行时门控](modules/runtime-gates.md) |
| `support_surface_*` | `ResolvedObject` 上 `support_surface_shape`/`support_surface_dimensions_m`/`support_surface_z_offset_m` 三个字段的简写。由 override 声明哪块是 target 上的实测稳定面；solver/validator/runtime 在三处共用，几何由 `scene_gen/support_geometry.py` 算。 | [目标局部几何](modules/target-local-geometry.md) |

证据状态：除特别标注外，本页基于当前源码已确认。