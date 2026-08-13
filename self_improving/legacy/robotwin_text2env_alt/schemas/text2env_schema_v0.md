# Text2Env Schema v0

更新时间：2026-06-24 Asia/Shanghai  
机器可校验版本：`schemas/text2env.schema.json`

---

## 1. 目标

Text2Env v0 的目标不是让 LLM 直接写 RoboTwin2 代码，而是先生成一个可检查、可修改、可复用的 tabletop task spec。

```text
natural language
  -> Text2Env JSON
  -> schema / critic checks
  -> RoboTwin2 task scaffold
  -> smoke test
```

v0 只覆盖 SceneSmith-lite tabletop 的最小可交付范围：

- pick-place
- place object in region
- stacking
- simple tool-use with existing metadata
- simple container placement with non-articulated target

暂时不强行覆盖：

- drawer / cabinet / microwave 等 articulated object 的完整自动生成
- complex multi-stage task synthesis
- automatic asset metadata creation

---

## 2. 顶层字段

```json
{
  "schema_version": "text2env.tabletop.v0",
  "task_name": "move_block_between_zones",
  "language_instruction": "Move the green block from the left zone to the right zone.",
  "intent": "place_in_region",
  "status": "ready_for_scaffold",
  "workspace": {},
  "objects": [],
  "regions": [],
  "arm_policy": {},
  "plan": [],
  "success": [],
  "language": {},
  "randomization": {},
  "validation_constraints": [],
  "generation_targets": {}
}
```

字段含义：

- `schema_version`: 固定为 `text2env.tabletop.v0`。
- `task_name`: RoboTwin2 文件名和 class 名，必须是 snake_case。
- `language_instruction`: 原始自然语言目标。
- `intent`: 粗任务类型，用于选择 scaffold 模板。
- `status`: 是否可直接生成 scaffold。
- `workspace`: 桌面、机器人、可用边界。
- `objects`: 任务中所有物体，包括可操作物、目标物、干扰物、工具。
- `regions`: 逻辑区域或可视化 marker，例如 left_zone / right_zone。
- `arm_policy`: 如何选择左臂或右臂。
- `plan`: `play_once()` 的动作意图。
- `success`: `check_success()` 的状态谓词。
- `language`: RoboTwin2 instruction 模板和 placeholder。
- `validation_constraints`: Critic 必须检查的约束。
- `generation_targets`: 期望生成到 RoboTwin2 的哪些文件。

---

## 3. RoboTwin2 映射

| Text2Env 字段 | RoboTwin2 位置 | 说明 |
|---|---|---|
| `task_name` | `envs/<task_name>.py` class `<task_name>` | 文件名和 class 名必须一致 |
| `workspace` | `Base_Task._init_task_env_()` 参数 | v0 大多使用默认桌面 |
| `objects` | `load_actors()` | 生成 `create_box` / `create_actor` / `create_urdf_obj` |
| `regions` | `load_actors()` + task fields | 可生成 marker actor 或纯逻辑 target pose |
| `protected_region` | `self.add_prohibit_area(...)` | 防止 cluttered objects 干扰任务关键区 |
| `arm_policy` | `play_once()` | 转成 `ArmTag("left"|"right")` |
| `plan` | `play_once()` | 转成 `grasp_actor`、`move_by_displacement`、`place_actor` 等 primitive |
| `success` | `check_success()` | 转成位置/contact/gripper predicates |
| `language` | `description/task_instruction/<task_name>.json` + `self.info["info"]` | 生成 episode instruction |
| `generation_targets` | scaffold writer | 记录输出文件和默认 config |

---

## 4. Object 设计

v0 支持三类主要 object：

```json
{
  "id": "green_block",
  "role": "manipulated",
  "kind": "box",
  "category": "block",
  "geometry": {
    "shape": "box",
    "half_size": [0.025, 0.025, 0.025],
    "color": [0.0, 0.8, 0.1]
  },
  "initial_pose": {
    "mode": "random_uniform",
    "xlim": [-0.28, -0.12],
    "ylim": [-0.05, 0.08],
    "zlim": [0.766, 0.766],
    "qpos": [1, 0, 0, 0],
    "rotate_rand": true,
    "rotate_lim": [0, 0, 0.4]
  },
  "physical": {
    "mass_kg": 0.05,
    "graspable": true,
    "movable": true,
    "is_static": false
  },
  "protected_region": {
    "enabled": true,
    "padding_m": 0.07
  }
}
```

`kind` 的使用建议：

- `box`: 最稳，适合 v0 的 block/zone marker。
- `asset`: 使用 RoboTwin2 已有 `assets/objects/<modelname>`，要求 metadata 能支持 grasp/place。
- `urdf`: 用于 drawer/cabinet 等 articulated object，v0 只允许 draft，不建议直接 scaffold。
- `zone_marker`: 可视化区域 marker，通常是薄 box。
- `logical_region`: 纯逻辑区域，不一定生成物体。

---

## 5. Plan 设计

v0 plan 是 motion primitive 序列，不是低层 joint action。

常见 action：

```json
[
  {
    "op": "grasp",
    "object": "green_block",
    "arm": "$main_arm",
    "pre_grasp_dis": 0.09,
    "grasp_dis": 0.0
  },
  {
    "op": "move_by",
    "arm": "$main_arm",
    "delta": [0.0, 0.0, 0.08],
    "axis": "world"
  },
  {
    "op": "place",
    "object": "green_block",
    "arm": "$main_arm",
    "target": "right_zone",
    "pre_dis": 0.05,
    "dis": 0.0,
    "is_open": true
  }
]
```

`arm` 可以是：

- `"left"`
- `"right"`
- `"auto"`
- `"$main_arm"` 这种来自 `arm_policy.save_as` 的变量

v0 建议优先用这些 primitive：

- `grasp`
- `move_by`
- `place`
- `open_gripper`
- `back_to_origin`

---

## 6. Success 设计

`success` 必须能从 simulator state 判断。v0 不接受只能靠语言解释的成功条件。

常见 predicate：

```json
[
  {
    "type": "in_region",
    "object": "green_block",
    "region": "right_zone",
    "tolerance_m": 0.04
  },
  {
    "type": "max_displacement",
    "object": "blue_bowl",
    "reference": "initial",
    "threshold_m": 0.03
  },
  {
    "type": "grippers_open",
    "required": true
  }
]
```

RoboTwin2 scaffold 映射：

- `in_region`: `object.get_pose().p[:2]` 与 region center/size/tolerance 比较。
- `near`: 两个 object 的 position distance。
- `contact` / `no_contact`: `self.check_actors_contact(...)`。
- `grippers_open`: `self.is_left_gripper_open()` 和 `self.is_right_gripper_open()`。
- `max_displacement`: 在 `load_actors()` 保存初始 pose，在 `check_success()` 比较位移。

---

## 7. Language 设计

RoboTwin2 的 language instruction 由两个来源合成：

1. `description/task_instruction/<task_name>.json`
2. `play_once()` 返回的 `self.info["info"]`

Text2Env 中写：

```json
{
  "language": {
    "full_description": "Move a green block from the left zone to the right zone while keeping the blue bowl still.",
    "schema": "{A} is the manipulated block, {B} is the target zone, {C} is the object that should stay still, {a} is the arm.",
    "preference": "Short imperative instructions.",
    "placeholders": {
      "{A}": "green block",
      "{B}": "right zone",
      "{C}": "002_bowl/base3",
      "{a}": "$main_arm"
    },
    "seen_templates": [
      "Move {A} to {B} without moving {C}."
    ],
    "unseen_templates": [
      "Place {A} in {B} and keep {C} still."
    ]
  }
}
```

生成 scaffold 时：

- `seen_templates` / `unseen_templates` 写入 instruction JSON。
- `placeholders` 写入 `self.info["info"]`。
- 如果 placeholder value 是 `$main_arm`，需要在 `play_once()` 里替换成 `str(arm_tag)`。
- 如果 value 是 `002_bowl/base3`，RoboTwin2 会尝试读取 object description。

---

## 8. Critic 检查清单

在生成 RoboTwin2 代码前，Critic 至少检查：

- `task_name` 是否是 snake_case。
- 所有 `plan.object` / `success.object` 是否存在于 `objects`。
- 所有 `plan.target` / `success.region` 是否存在于 `regions` 或 `objects`。
- 所有 placeholder 是否有绑定。
- `asset.modelname` 是否存在于 RoboTwin2 `assets/objects`。
- v0 ready task 是否避免 `kind=urdf` 或 `intent=articulated_place`。
- `success` 是否完全可由 simulator state 判断。
- manipulated object 是否 `graspable=true`。
- protected region 是否覆盖关键 object/target。
- pose sampler 是否在 tabletop bounds 内。

---

## 9. 当前文件

- JSON Schema: `schemas/text2env.schema.json`
- Task B example: `examples/tabletop_tasks/move_object_between_zones.json`
- Drawer draft example: `examples/tabletop_tasks/put_cup_in_drawer.json`
