# RoboTwin2 Task API Notes

更新时间：2026-06-24 Asia/Shanghai  
远程 repo：`~/RoboTwin`  
目标：为 Text2Env / SceneSmith-lite 生成 RoboTwin2 tabletop task scaffold 提供接口说明。

---

## 1. 任务运行入口

官方数据采集入口：

```bash
cd ~/RoboTwin
source ~/miniconda3/etc/profile.d/conda.sh
conda activate RoboTwin
bash collect_data.sh <task_name> <task_config> <gpu_id>
```

例如：

```bash
bash collect_data.sh beat_block_hammer demo_smoke 0
```

入口链路：

```text
collect_data.sh
  -> script/collect_data.py
  -> import envs.<task_name>
  -> getattr(envs.<task_name>, <task_name>)
  -> task.setup_demo(...)
  -> task.play_once()
  -> task.check_success()
  -> task.save_traj_data(...)
  -> replay planned joints with save_data=True
  -> merge pkl cache to hdf5/video
  -> description/gen_episode_instructions.sh
```

关键约束：

- `envs/<task_name>.py` 文件名、class 名、命令行 `task_name` 必须一致。
- task class 必须继承 `Base_Task`。
- `setup_demo()` 通常只调用 `super()._init_task_env_(**kwags)`。
- task 至少实现 `load_actors()`、`play_once()`、`check_success()`。
- `play_once()` 需要返回 `self.info`，其中 `self.info["info"]` 会用于生成语言指令。

---

## 2. 最小 task 文件契约

最小结构：

```python
from ._base_task import Base_Task
from .utils import *
import sapien
import numpy as np


class my_task_name(Base_Task):

    def setup_demo(self, **kwags):
        super()._init_task_env_(**kwags)

    def load_actors(self):
        # create objects, randomize pose, set mass, add prohibited areas
        ...

    def play_once(self):
        # generate expert plan via built-in motion primitives
        ...
        self.info["info"] = {
            "{A}": "object_description_key_or_plain_text",
            "{a}": str(arm_tag),
        }
        return self.info

    def check_success(self):
        # simulator-state/contact predicate
        return True
```

生命周期：

```text
setup_demo()
  -> Base_Task._init_task_env_()
      -> setup_scene()
      -> create_table_and_wall()
      -> load_robot()
      -> load_camera()
      -> robot.move_to_homestate()
      -> open grippers
      -> robot.set_origin_endpose()
      -> load_actors()
      -> optional cluttered table
      -> check_stable()
```

`load_actors()` 会在 robot/camera/table 已经创建后调用，所以里面可以直接使用：

- `self.scene`
- `self.table_z_bias`
- `self.add_prohibit_area(...)`
- `create_box(...)`
- `create_actor(...)`
- `rand_create_actor(...)`
- `create_urdf_obj(...)`

---

## 3. 常用 actor 创建接口

### 基础几何体

```python
self.block = create_box(
    scene=self,
    pose=sapien.Pose([x, y, z], [qw, qx, qy, qz]),
    half_size=(0.025, 0.025, 0.025),
    color=(1, 0, 0),
    name="box",
    is_static=False,
)
```

适合 Text2Env v0 的自定义 tabletop 区域、block、zone marker。

### 资产物体

```python
self.object = create_actor(
    scene=self,
    pose=pose,
    modelname="020_hammer",
    convex=True,
    model_id=0,
)
```

约定：

- 资产路径在 `assets/objects/<modelname>/`。
- metadata 通常来自 `model_data.json` 或 `model_data<id>.json`。
- `Actor` wrapper 提供 `get_pose()`、`get_name()`、`set_mass()`、`get_contact_point()`、`get_functional_point()` 等接口。

### 随机 pose

```python
pose = rand_pose(
    xlim=[-0.25, 0.25],
    ylim=[-0.05, 0.15],
    zlim=[0.76],
    qpos=[1, 0, 0, 0],
    rotate_rand=True,
    rotate_lim=[0, 0, 0.5],
)
```

### 随机创建资产

```python
self.object = rand_create_actor(
    scene=self,
    modelname="057_toycar",
    model_id=0,
    xlim=[-0.25, -0.2],
    ylim=[-0.1, 0.1],
    rotate_rand=True,
    rotate_lim=[0, np.pi / 6, 0],
    qpos=[0.707225, 0.706849, -0.0100455, -0.00982061],
    convex=True,
)
```

### 禁止区域

```python
self.add_prohibit_area(self.object, padding=0.07)
self.prohibited_area.append([x_min, y_min, x_max, y_max])
```

用途：

- 避免 cluttered table 随机物体生成在任务关键区域。
- 对 SceneSmith-lite 很重要：Text2Env schema 应该输出 `protected_regions`。

---

## 4. Motion primitive 接口

所有 primitive 通常返回 `(arm_tag, [Action, ...])`，然后通过 `self.move(...)` 执行。

### arm tag

```python
arm_tag = ArmTag("left")
arm_tag = ArmTag("right")
arm_tag.opposite
str(arm_tag)
```

### grasp

```python
self.move(self.grasp_actor(
    self.object,
    arm_tag=arm_tag,
    pre_grasp_dis=0.1,
    grasp_dis=0,
    gripper_pos=0.0,
))
```

说明：

- 依赖 asset metadata 中的 `contact_points_pose`。
- 如果是 `create_box`，默认内置多个 top-down contact points。
- 对 Text2Env v0，优先用 box 或已有 metadata 完整的资产。

### lift / translate

```python
self.move(self.move_by_displacement(arm_tag=arm_tag, z=0.1))
self.move(self.move_by_displacement(arm_tag=arm_tag, x=0.05, y=0.0, z=0.05))
self.move(self.move_by_displacement(arm_tag=arm_tag, z=0.07, move_axis="arm"))
```

### place

```python
self.move(self.place_actor(
    self.object,
    arm_tag=arm_tag,
    target_pose=target_pose,
    functional_point_id=0,
    pre_dis=0.05,
    dis=0.0,
    is_open=True,
))
```

常用参数：

- `target_pose`: list、numpy array 或 `sapien.Pose`
- `functional_point_id`: 指定 actor 上哪个 functional point 对齐到 target
- `pre_dis`: 预放置距离
- `dis`: 最终接近距离
- `is_open`: 是否在末尾打开夹爪
- `pre_dis_axis`: 可用 `"grasp"` 或 `"fp"`

### direct pose / gripper / reset

```python
self.move(self.move_to_pose(arm_tag=arm_tag, target_pose=pose))
self.move(self.open_gripper(arm_tag=arm_tag))
self.move(self.close_gripper(arm_tag=arm_tag))
self.move(self.back_to_origin(arm_tag=arm_tag))
```

### 双臂同步

```python
self.move(
    self.back_to_origin(arm_tag="left"),
    self.grasp_actor(self.basket, arm_tag="right"),
)
```

`self.move()` 会把左右臂 action list 对齐；同一步左右都是 `move` 时会调用 `together_move_to_pose()`。

---

## 5. Success checker 常用模式

`check_success()` 必须返回 bool。它应该只依赖 simulator state，避免依赖语言文本。

常用状态接口：

```python
obj_p = self.object.get_pose().p
obj_q = self.object.get_pose().q
self.check_actors_contact(actor_name_1, actor_name_2)
self.is_left_gripper_open()
self.is_right_gripper_open()
self.robot.is_left_gripper_open()
self.robot.is_right_gripper_open()
```

常见 predicate：

- spatial relation: object in target region / near target object
- stacking: top object z about bottom z + height
- containment: object near basket/cup plus contact relation
- contact: two actor names are touching
- gripper state: both grippers open after release
- articulation state: for URDF object, use `ArticulationActor.get_qpos()` / `set_qpos()`

示例：

```python
def check_success(self):
    object_pose = self.object.get_pose().p
    target_pose = self.target_object.get_pose().p
    return (
        np.linalg.norm(object_pose[:2] - target_pose[:2]) < 0.05
        and self.is_left_gripper_open()
        and self.is_right_gripper_open()
    )
```

---

## 6. task_config 接口

配置文件位置：

```text
task_config/<task_config>.yml
```

最小 smoke 配置可参考：

```yaml
render_freq: 0
episode_num: 1
use_seed: false
save_freq: 1
embodiment: [aloha-agilex]
language_num: 1
domain_randomization:
  random_background: false
  cluttered_table: false
  clean_background_rate: 1
  random_head_camera_dis: 0
  random_table_height: 0
  random_light: false
  crazy_random_light_rate: 0
camera:
  head_camera_type: D435
  wrist_camera_type: D435
  collect_head_camera: true
  collect_wrist_camera: true
data_type:
  rgb: true
  third_view: false
  depth: false
  pointcloud: false
  observer: false
  endpose: true
  qpos: true
  mesh_segmentation: false
  actor_segmentation: false
pcd_down_sample_num: 1024
pcd_crop: true
save_path: ./data
clear_cache_freq: 1
collect_data: true
eval_video_log: false
```

重要字段：

- `episode_num`: 成功 episode 数，不是尝试次数。
- `use_seed: false`: 先搜索能成功规划的 seed，并保存 `_traj_data`。
- `collect_data: true`: 使用 seed 和 joint paths 重放并保存 hdf5/video。
- `save_freq`: 保存帧频率；smoke 可设 1，正式可用 15。
- `embodiment`: 一个元素代表双臂同型号；三个元素代表左右不同型号和距离。
- `language_num`: 每个 episode 生成多少条 instruction。

---

## 7. instruction 模板接口

每个新 task 还需要：

```text
description/task_instruction/<task_name>.json
```

格式：

```json
{
  "full_description": "short task description",
  "schema": "{A} notifies object A, {B} notifies target B, {a} notifies the arm",
  "preference": "num of words should not exceed 10",
  "seen": [
    "Move {A} to {B} using {a}."
  ],
  "unseen": [
    "Place {A} on {B}."
  ]
}
```

`play_once()` 里的 `self.info["info"]` 必须覆盖模板里用到的 placeholder：

```python
self.info["info"] = {
    "{A}": "020_hammer/base0",
    "{B}": "red zone",
    "{a}": str(arm_tag),
}
```

替换规则：

- 如果 value 看起来像 `object_name/base_id`，脚本会去 `description/objects_description/<value>.json` 找 object description。
- 单个小写字母 placeholder，例如 `{a}`，会被转成 `the left arm` / `the right arm`。
- 模板如果少了 arm placeholder，也可能被接受；脚本允许只缺 arm 参数的 instruction。

---

## 8. Text2Env v0 应该生成的最小文件

对一个新 tabletop task，最小生成物：

```text
envs/<task_name>.py
description/task_instruction/<task_name>.json
task_config/<task_config>.yml  # 可先复用 demo_smoke
```

为了减少失败，v0 先限制：

- 使用 `create_box` 和已有 metadata 完整的 `create_actor`。
- 任务类型先做 pick-place / stack / place-in-region。
- 成功条件只用 spatial/contact/gripper open。
- 先不用 articulated object，drawer/microwave/cabinet 放到 v1。
- target region 先用 invisible logical region 或 thin colored `create_box` marker。

---

## 9. Text2Env schema 到 RoboTwin task 的映射

建议 schema 字段：

```yaml
task_name: move_block_between_zones
objects:
  - id: block
    kind: box
    pose_sampler:
      xlim: [-0.25, -0.10]
      ylim: [-0.08, 0.08]
      zlim: [0.766]
    size: [0.025, 0.025, 0.025]
    color: [1, 0, 0]
regions:
  - id: target_zone
    center: [0.18, -0.10, 0.752]
    size: [0.06, 0.06, 0.003]
    color: [0, 1, 0]
protected_regions:
  - around: block
    padding: 0.07
plan:
  - grasp: block
  - lift: {z: 0.08}
  - place: {object: block, target: target_zone}
success:
  - in_region: {object: block, region: target_zone, tolerance: 0.04}
  - grippers_open: true
language:
  placeholders:
    "{A}": "red block"
    "{B}": "green zone"
```

映射到 RoboTwin：

- `objects[*]` -> `load_actors()`
- `pose_sampler` -> `rand_pose(...)`
- `regions` -> target pose / optional marker actor
- `protected_regions` -> `add_prohibit_area(...)` 或 append rectangle
- `plan` -> `play_once()` 中的 primitive sequence
- `success` -> `check_success()`
- `language` -> `self.info["info"]` 和 instruction JSON

---

## 10. 新 task 开发 checklist

1. 选 task name：小写 snake_case。
2. 新建 `envs/<task_name>.py`，class 同名并继承 `Base_Task`。
3. 实现 `setup_demo()`。
4. 实现 `load_actors()`：
   - 创建所有 task actor。
   - 设置质量。
   - 添加 prohibited areas。
   - 保存初始高度或 target pose，供 success checker 使用。
5. 实现 `play_once()`：
   - 根据 object pose 选择 `ArmTag("left"|"right")`。
   - 用 `grasp_actor`、`move_by_displacement`、`place_actor` 等拼出专家动作。
   - 填写 `self.info["info"]`。
6. 实现 `check_success()`：
   - 用位置、接触、夹爪状态判断。
   - 不依赖 instruction 文本。
7. 新建 `description/task_instruction/<task_name>.json`。
8. 用 `demo_smoke` 跑 1 episode：
   ```bash
   bash collect_data.sh <task_name> demo_smoke 0
   ```
9. 检查输出：
   - `data/<task_name>/demo_smoke/seed.txt`
   - `data/<task_name>/demo_smoke/_traj_data/episode0.pkl`
   - `data/<task_name>/demo_smoke/data/episode0.hdf5`
   - `data/<task_name>/demo_smoke/video/episode0.mp4`
   - `data/<task_name>/demo_smoke/instructions/episode0.json`
10. 如果失败，优先排查：
    - actor metadata 是否有 contact/functional point。
    - target pose z 是否贴合桌面高度。
    - 初始 pose 是否离 robot/target 太近。
    - `check_success()` actor name 是否和 `get_name()` 一致。
    - `self.info["info"]` placeholder 是否匹配 instruction JSON。

---

## 11. 已检查的参考文件

远程 `~/RoboTwin`：

- `collect_data.sh`
- `script/collect_data.py`
- `envs/_base_task.py`
- `envs/beat_block_hammer.py`
- `envs/place_a2b_left.py`
- `envs/place_object_basket.py`
- `envs/stack_blocks_two.py`
- `envs/utils/action.py`
- `envs/utils/actor_utils.py`
- `envs/utils/create_actor.py`
- `envs/utils/rand_create_actor.py`
- `task_config/_config_template.yml`
- `task_config/demo_clean.yml`
- `task_config/demo_smoke.yml`
- `description/utils/generate_episode_instructions.py`
- `description/task_instruction/beat_block_hammer.json`
