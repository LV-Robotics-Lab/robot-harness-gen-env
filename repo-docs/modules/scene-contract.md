# 类型化场景契约

## 为什么下游全信 schema，不信 dict

`/gen-env` 的每一阶段在 `scene_gen/schema.py` 里都有一个类型守门：解析器吐 dict，先过 `SceneSpec`；grounding 通到 `ResolvedObject`；builder 把整个 `ResolvedSceneSpec` 写盘。一旦字段不合规，pydantic 在构造时就抛，下游拿不到「半成 dict」。你在这里第一次看到它的位置在 [一条真实路径 Step 2](../walkthroughs/one-real-run.md)。

设计原因是「文本到机器人的信任边界」。自然语言是松的，下游几何求解器、builder、validator 都期望「我已经收到的字段全到、没字段是后端走私」。schema 的工作就是把这个假设变成一条可执行的合同。

## 严格从哪几个方向把门

`StrictModel` 把所有契约都设成 `extra="forbid"`、`frozen=True`、`protected_namespaces=()`：

- `extra="forbid"` —— 多传一个字段就报错，挡住 provider 走私。
- `frozen=True` —— 对象不可变；下游无人能偷偷改 pose / id。
- `protected_namespaces=()` —— 让 `model_*` 字段名能用而不被 pydantic 占。
- model_validator 在 `mode="before"`/`mode="after"` 都有，构造前后两层过滤。

输入边界在 `SceneSpec.input_boundary`：对整个输入 dict 递归跑 `_reject_forbidden_keys`，命中 `FORBIDDEN_SCENE_KEYS` 里任何一个（`asset_id`/`asset_path`/`file_path`/`model_id`/`pose`/`position`/`position_m`/`xyz`/`qpos`/`quaternion`/`orientation`/`orientation_wxyz`/`python`/`code`）就抛 `SceneSpecError`。这条独立于 parser 的 prompt 正则检查，是 schema 自己的最后一道。

## `SceneSpec` 的关键跨字段不变量

`SceneSpec.semantic_consistency` 在所有字段构造完后强制一组约束，这组是解析器和求解器之间的真合同：

| 不变量 | 拒绝条件 |
| --- | --- |
| object_id 唯一 | 重复 id 抛 `object_id values must be unique` |
| 每个 object 恰好一条 support 关系 | `every object requires exactly one support relation: <missing>` |
| relation source/target 已知 | 未知 id 直接拒 |
| 同模式不重复 | 同 `(relation, source, target, max_dist, min_dist)` 元组重复抛 |
| support 关系不成环 | `_has_cycle(known, support_edges)` 真则抛 |
| left/right 轴向约束不成环 | 同上对 `axis_x` |
| front/behind 轴向约束不成环 | 同上对 `axis_y` |
| `near` 与 `distance_at_least` 不矛盾 | 同对 pair 的 min > max 抛距离约束矛盾 |
| `on_table` 必须 target table | 否则抛 |
| 非 `on_table` 关系不能 target table | 否则抛 |
| `near` 收 `max_distance_m` 不收 min | 否则抛 |
| `distance_at_least` 必须 `min_distance_m` | 否则抛，且其它关系不能带 min |

边界朝向、单位、坐标系都钉死——`FrameSpec` 锁 `robotwin_world` 右手系（`x=right`、`y=front`、`z=up`），`unit` 锁 `m`，`WorkspaceSpec` 锁工作区 bounds 与 robot keepout。`seed` 在 `0..2_147_483_647`，所有后续随机从这里派生。

## `ResolvedSceneSpec` 把上游哈希也写进自己

`ResolvedSceneSpec` 不只是输出，它把根也带上：

| 字段 | 意义 |
| --- | --- |
| `source_scene_spec_sha256` | `spec.digest()` 拷贝；下游可在不信任生成环境时核对来源意图 |
| `asset_catalog_sha256` | `catalog.digest()`；catalog 一变 resolved 就不再相容 |
| `compiler_version` | 比如 `scene_gen.stage5_solver.v3`；版本漂移会让哈希一致失效 |
| `solver_trace` | 完整 `SolverAttempt` 序列；失败回放可机读 |
| `objects: tuple[ResolvedObject, ...]` | 每个物体的资产 id、模型 id、pose、来源链、footprint、稳定朝向 |

`ResolvedObject` 自己也有跨字段不变量——`stable_pose_plus_yaw_only` 强制 resolved orientation 必须等于「稳定朝向 × yaw 四元数」的乘积，防止下游偷偷改一个不会让物体稳的朝向；articulation 数组长度要么全 0、要么全同长；rigid 物体不能有 qpos；derived scaled proxy 的 lineage 字段必须全在，非 derived 物体不能带 lineage 字段。

`digest()` 是 canonical JSON sorted-keys 的 sha256；`builder.py` 会在写盘前验 `resolved.source_scene_spec_sha256 == spec.digest()`，详见 [哈希绑定包](replay-package.md)。

## 改动入口与验证

- 加字段：加进 `StrictModel` 子类后，检查下游是否实际能填这个字段；同时改 `tests/scene_gen/test_schema.py` 与 `tests/fixtures/asset_catalog.json`/`golden_prompts.json`（如影响 prompt surface）。
- 加跨字段不变量：加进对应的 `model_validator(mode="after")`，并在 `test_schema.py` 加一个攻击的正面 + 反面。
- 改 `FORBIDDEN_SCENE_KEYS`：注意 parser 的 `FORBIDDEN_PROMPT_PATTERNS` 是相邻但独立的拒绝层，两边一起改才有完整边界。
- 改完跑 `pytest -q tests/scene_gen/test_schema.py`。

要继续追「digest 怎么和磁盘包绑」，去 [哈希绑定包](replay-package.md)。要追「catalog 模型怎么挑出唯一一个」，去 [确定性代理](derived-proxy.md) 的相邻 ground 部分——但 grounding 本身的逻辑不分模块，在 `scene_gen/grounding.py` 直接读。

证据状态：除特别标注外，本页基于当前源码已确认。