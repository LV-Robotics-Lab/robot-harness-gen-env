# 运行时门控

## 渲染不是物理证据

仓库根 `AGENTS.md` 把这条钉成 acceptance rule：「渲染不是物理证据」。意思就是——拿一张 preview_head.png 甚至 120 帧视频给人看说「看，物体稳稳在 plate 上」，仍然不构成验收。验收只看 SAPIEN 终末接触 / 漂移 / 掉落 / 可见性 / 不穿透 / 视频互异帧。`scene_gen/rendered_critic.py` 的 VLM 评判只查可见语义，是相邻辅助层。这一步在 [一条真实路径 Step 7 + Step 8](../walkthroughs/one-real-run.md) 体现。

为什么「直接看图」不够：静态图把 AABB 看起来稳但物体刚撞翻的瞬间拍下；contact 数没采集；视频能把「释放后坠地」整段藏掉只留终末静止。所以门控是 SAPIEN 在终末窗口里逐帧采 contact、再算 fraction，加上位置/姿态的全局漂移，不依赖视觉判断。

## 回放怎么采、采什么

`script/run_scene_runtime.py` 的轨迹：

```text
total_steps = max(settle_steps, video_frames)            # 默认 max(900, 120)
video_steps = video_sample_steps(total_steps, 120)      # 119 释放帧 + 1 终末帧
contact_window_steps = min(max(1, contact_window_steps=120), total_steps)
# 终末 120 步每步采一次 contact
for index in range(total_steps):
    task.scene.step()
    if index in video_step_set: frames.append(get_observer_rgb())
    if index >= total_steps - contact_window_steps:
        contact_sample = summarize_contacts(get_contacts())
        for name in generated:
            if contact_sample.support_by_object[name]: support_contact_hits[name] += 1
            if contact_sample.unexpected_targets_by_object[name]:
                unexpected_contact_hits[name] += 1
```

`--precheck-steps 0` 默认：不预步进。`check_stable` 仅跑 precheck_steps（即 0），不参与判 acceptance——这一步有意为之，避免把不稳初态躲到「正式记录」之前，那样记录的「释放帧」其实已经是稳态之后的假释放。

`head_camera_arrays(task)` 拿 head rgb + 分割标签；`world_camera1`/`world_camera2` 拿两个世界视角；最终还写 `preview_head.png`、`preview_segmentation.png`、`preview_world_left.png`、`preview_world_right.png`、`observer_start.png`、`observer_mid.png`、`observer_end.png`、`observer_runtime.mp4`。每段 mp4 的 `unique_video_frame_count` 用 `hashlib.sha256(frame.tobytes())` 去重。

为什么 900 步：仓库根 `README.md` 给了具体证据——apple-in-basket 在 300 步时仍在动，900 步是为该 asset pair 测下来的「真的稳了」阈值。这不是通用物理参数，是实测值。

## contact 怎么分类

`summarize_contacts` 把每条 SAPIEN contact 按 body 名解析：

| 接触对 | 计入哪类 |
| --- | --- |
| `generated ↔ generated` 且其中一个是另一个的 expected support target | `support_by_object[generated] = True` |
| `generated ↔ generated` 但 target 关系反向（other 的 support 是 generated） | 跳过，不重复计 |
| `generated ↔ generated` 未声明的对 | unexpected_target；penetration_count += |
| `generated ↔ table` 且 table 是生成对象的 expected support target | support target=table |
| `generated ↔ table` 且 table 非声明 nest target | unexpected_target=table |
| `generated ↔ ground / wall` | unexpected_target |
| `generated ↔ 任何其它非 table / 非 generated` | robot_collision_pair |

每条 contact 还算 `contact_point_metrics`：点数、≤ 1 mm 的活动点数、最小 separation、最大 impulse 范数。`active` 要求至少一个 active point。`contact_penetration_count` 数 `separation < -0.002` 的点为穿透。

## validator 把采到的证据转成 checks

`scene_gen/validator.py:validate_resolved_scene` 接 `runtime_evidence` 关键字，转换为 bug-by-bug checks。状态机：fail（任一 check fail）> incomplete（有 not_run）> pass（全 pass）。

每物体的门控按 `is_static` 分两种模式：

| Check | 静态物体（`is_static=True`）| 动态物体（`is_static=False`）|
| --- | --- | --- |
| translation_drift | ≤ 20 mm | not_applicable |
| rotation_drift | ≤ 5° | not_applicable |
| resolved_translation_error | ≤ 20 mm | not_applicable |
| resolved_rotation_error | ≤ 5° | not_applicable |
| support_contact | 走 `fixed_static_pose` 模式：`is_static ∧ on_table ∧ support_mode=fixed_static_pose ∧ not_dropped`，否则须 physical_support | 须 `support_contact ∧ target == support_target ∧ contact_fraction ≥ 0.8` |
| no_unexpected_support_contact | not_applicable（嵌套关系才查）| 未声明 target fraction = 0 且无 unexpected_targets |
| runtime_support_margin | not_applicable（`on_top_of` 才查）| `on_top_of` 时 margin ≥ `target.support_margin_m` |
| runtime_inside_containment | not_applicable（`inside` 才查）| `inside` 时 `inside_contained = True` |
| settled | not False | not False |
| penetration | 0 | 0 |
| not_dropped | True | True |
| head_visibility | ≥ `min_visible_pixels=64` | 同 |
| articulation_qpos | 误差 ≤ 0.02 | 同（有 qpos 才查）|

视频门控单独两条：`observer_video_frame_count` 要求 ≥ 3，`observer_video_unique_frames` 要求 ≥ `min(video_frame_count, 30)`。120 帧 → 30 互异是基线，committed 矩阵实测每个 MP4 至少 100/120 互异。runtime_relations 对 left/right/front/behind/near/distance_at_leas每条匹配 `runtime_evidence.relations` 同 key 的 `pass`。

## 关键攻击：哪些误报模式被锁住

`tests/scene_gen/test_builder_validator.py` 里专门给「假阳性会被门控拒掉」准备的攻击测试，按误报模式排：

| 攻击用例 | 它锁什么 |
| --- | --- |
| `test_runtime_validator_rejects_static_contact_free_nested_support` | 静态物体被钉在 nested support 但不实际接触目标，曾经 allowed 的假阳性——现拒 |
| `test_runtime_validator_rejects_intermittent_nested_contact` | 嵌套源只在终末窗口一部分帧碰 target fraction 仍 ≥ 0.8 → 拒 |
| `test_runtime_validator_rejects_nested_source_contacting_table` | 嵌套源接触桌子（应是接触 nested target）→ 拒 |
| `test_static_validator_rejects_edge_placement_even_inside_outer_plate_bounds` | source 中心在 plate 外缘内但 `support_footprint_margin` < 8 mm → 静态就拒 |
| `test_static_validator_rejects_target_local_container_overflow` | source 中心在 basket 内但 footprint 超出 `interior_dimensions` → 静态就拒 |
| `test_runtime_v2_validates_dynamic_relations_instead_of_exact_spawn_pose` | 动态物体不该按 spawn pose 比 drift，应按最终接触 + 关系 |
| `test_runtime_validator_accepts_explicit_fixed_static_support_only_for_static_objects` | `fixed_static_pose` 路径只对 `on_table ∧ is_static` 开 |

`tests/scene_gen/test_acceptance.py:test_acceptance_requires_at_least_95_percent_passes` 锁住批量验收门槛：100-seed / 矩阵 batch 至少 95% 通过才整体 pass。单失败 mode 不会让整个 batch 丢，但低于 95% 就 fail。

## 改动入口与验证

- 改阈值：改 `validate_resolved_scene` 默认参数（如 `max_translation_drift_m`）；同步改 `docs/evidence/<date>.md` 的声明阈值，并跑 `pytest -q tests/scene_gen/test_builder_validator.py` 看攻击用例仍生效。
- 加新门控：在 `validate_resolved_scene` 里加 `_check`，schema 不变；同步加攻击用例锁假阳性、加正面用例锁假阴性。
- 改 contact 分类规则：改 `summarize_contacts`，并改 `tests/scene_gen/test_builder_validator.py` 的对应假数据用例。
- 改视频采样：改 `runtime_sampling.video_sample_steps`，必须同步 `tests/scene_gen/test_prompt_matrix.py:test_video_samples_cover_full_settle_window_without_duplicates`。
- 涉及 validator 或 loader 契约的改动另需真机回放（根 `AGENTS.md`）。

要回到主路径，去 [一条真实路径](../walkthroughs/one-real-run.md)。要追到证据底座被谁用了，去 [证据底座](../references/source-evidence.md)。要看本指南是否真的把这一套传给了读者，去 [质检报告](../references/quality-review.md)。

证据状态：除特别标注外，本页基于当前源码已确认。