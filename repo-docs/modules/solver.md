# 受限求解器

## 为什么需要「受限」+「回退」

几何求解器要解决的问题是：在一块桌面上摆 N 个物体，每个物体按其 support 关系落在 table / 另一物体顶面 / 容器内部，每两个之间还有 left_of / near / distance_at_least 之类约束，所有约束要同时满足。这是个组合问题——全局最优既慢又不必；本地贪心会卡死。所以 `solver.py` 走的是 bounded rejection backtracking：随机采样 + 拒掉违规 + 偶尔回退。这一步在 [一条真实路径 Step 4](../walkthroughs/one-real-run.md) 体现。

为什么「bounded」是设计而非偷懒：编译器必须能在固定时间内给出可解释的「失败」结论。如果求解器开放回退到天荒地老，自然语言一句话就能让 CI 挂半小时。96 + 48 这两个上限是当前 fixture 下能让所有 committed prompt 矩阵用例都能成立、且把不可行工作区案例 fail 在几秒之内的取值。

## 摆位顺序由 support depth 决定

不知道先摆谁，nested 物体没法摆——`on_top_of` 的 source 要落在 target 顶面，必须先把 target 摆好。`_support_depth` 递归数：target 为 table → depth 0；否则 1 + 深度(target)。`place` 按此排序：

```python
order = sorted(
    spec.objects,
    key=lambda item: (
        _support_depth(item.object_id, supports),
        -degree[item.object_id],
        item.object_id,
    ),
)
```

`degree` 是被多少对约束提到的次数（约束越多越先摆，先摆就少受制于后摆的）；最后用 `object_id` 当词法 tie-break。所以桌上物体先摆，叠在最上面的最后摆。

## 一次尝试怎么做

对每个 object，最多跑 96 次尝试。每次：

1. 选 yaw。`on_table` 时随机；`nested` 时取 `target.yaw + 噪声`；带 articulation 且没 target 时锁 `yaw = π/2`（articulated 物体通常要面对机器人）。
2. 按 support 关系采 (x, y, bottom_z)。
   - `on_table`：在 region_bounds 里随机。
   - `on_top_of`：在 target 局部 support 面里用 `sample_supported_offset` 采样，落不下记 reason「object footprint does not fit stable support surface」。
   - `inside`：在 target `interior_dimensions` 内采样、bottom 落到 `interior_floor_z`。
3. 把 (x, y, yaw, bottom_z) 喂进 `_candidate_pose`，它构造 footprint、AABB3、interior bounds、support surface 等几何读数。
4. `_candidate_reasons` 在 `assigned` 已摆集合上检查：(a) 是否压到 robot keepout；(b) 是否与已摆的非 support-pair 物体三维重叠；(c) 嵌套 source 不能是 `is_static`（静态物体不能当 nested source）；(d) 已摆对象之间所有 pair_relations 是否满足。
5. reasons 长度为 0 → 接受；记一条 `SolverAttempt(accepted=True)`；递归 `place(index+1)`。
6. reasons 非空 → 记 `accepted=False` + reasons 元组；继续下个采样。

回退规则：`place(index+1)` 递归失败时，pop 当前物体、`backtracks += 1`；超过 `max_backtracks=48` 整体失败。

## 关系检查的几何在 `_pair_relation_reasons`

每个 pair-relation 的违反都对应一条 reason 串，机读可解析：

| 关系 | reason | 几何判定 |
| --- | --- | --- |
| `on_top_of` | `on_top_of support height failed` | source bottom 偏离 `target.support_surface_z + spawn_clearance` > 3 mm |
| `on_top_of` | `on_top_of stable support margin failed` | `support_footprint_margin` < `target.support_margin_m` |
| `inside` | `inside target has no interior dimensions` | target 没声明 interior |
| `inside` | `inside footprint containment failed` | margin < 0 |
| `inside` | `inside vertical containment failed` | source bottom < interior_floor 或 top > interior_floor + interior_height |
| `left_of` / `right_of` / `front_of` / `behind` | `<rel> footprint inequality failed` | x 或 y 的 footprint 边界带 15 mm gap 不满足方向 |
| `near` | `near maximum center distance failed` | 中心距 > `max_distance_m`（默认 0.25） |
| `distance_at_least` | `distance_at_least minimum center distance failed` | 中心距 < `min_distance_m` |

每个 reason 进 `SolverAttempt.reasons`，整条 attempts trace 进 `SolverTrace.attempts`，再进 `ResolvedSceneSpec.solver_trace`。下游 validator 不重算这些，但要拿 trace 时直接读。

## 失败长什么样

`max_attempts` 跑完仍不解决、或回退耗尽，`solve_scene` 抛 `SceneSolveError`，其 `report` 是机读 dict：

```text
schema_version: robotwin.scene_solver_failure.v1
blocker: bounded solver exhausted
max_attempts_per_object: 96
max_backtracks: 48
total_attempts: <int>
attempts: [<SolverAttempt>, ...]
```

`generate_scene.py` 捕获它并落到 `data/generated_scenes/<scene_id>/failure_report.json`，stdout 仅一行 `FAIL <path>`、退出码 2。攻击用例 `test_impossible_workspace_fails_with_bounded_machine_readable_trace` 故意把工作区 bounds 压到放不下任何 footprint，并断言失败 trace 是可解析的。`prompt_matrix.json` 里的 `infeasible_apple_plate_back_region` 用真实工作区把一条实际不可行的「apple 在 plate 右边、放在 back 区」让 solver 拒掉，并作为正向预期。

## 边界：嵌套 source 必须是动态的

`solver.py` 里这两行封死了静态堆叠假阳性：

```python
if model.is_static:
    pre_reasons.append("nested support source cannot be static")
```

`on_top_of` 与 `inside` 分支都查。这是设计上的强约束——静态物体的 pose 是钉死的，被钉在 nested support 上面如果没实际接触、只看 AABB 就会假阴性放过。`tests/scene_gen/test_builder_validator.py:test_runtime_validator_rejects_static_contact_free_nested_support` 锁住这条攻击。

## 改动入口与验证

- 改采样上限 / 回退上限：调 `solve_scene` 默认参数，并加 fixture 攻击测试覆盖「新上限仍能跑出 fixture 矩阵」。
- 加新关系检查：在 `_pair_relation_reasons` 加分支，并在 `RelationType` 与 schema 跨字段不变量同步；`support_geometry.py` 一般也要同步语义。
- 改成功路径布局：注意 `ResolvedObject` 的 pose 写盘精度是 `(round 9, round 9, round 9)` + `round 12`——`_orientation` 给出归一化 quaternion，pose digest 与稳定朝向是绑的。
- 改完跑 `pytest -q tests/scene_gen/test_solver.py tests/scene_gen/test_builder_validator.py`。

要回到主路径，去 [一条真实路径](../walkthroughs/one-real-run.md)。要继续追「摆不下且 catalog miss 时编译器怎么补」，去 [确定性代理](derived-proxy.md)。

证据状态：除特别标注外，本页基于当前源码已确认。