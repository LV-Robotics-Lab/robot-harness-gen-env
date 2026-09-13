# ASPIRE 中 LIBERO API 与控制执行路径说明

> **冻结版本**：本文只解释 NVlabs/ASPIRE 公共代码提交
> [`7ba73d3bcac8f6b6d4a7d67ed4040988f768d282`](https://github.com/NVlabs/ASPIRE/tree/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282)。
> **证据标记**：`[事实]` 是固定源码、论文或官方文档直接显示的行为；`[推断]`
> 是依据这些接口拼出的调用链或对协议边界的解释，不是额外的实验结果。

## 先给结论：三层不要混为一个“API”

ASPIRE 的 LIBERO 运行至少有三层：

1. **LIBERO benchmark / env Python API**：选择 suite 和 task，读取 BDDL 与
   init states，构造 `OffScreenRenderEnv`，并通过 `reset`、`step`、
   `check_success` 驱动/判定任务。
2. **ASPIRE 注入生成 Python 的 curated API**：面向 coding agent 的
   `get_observation`、SAM3 分割、mask-to-points、抓取规划、IK、
   `move_to_joints`、夹爪和几何辅助函数。它们是包装后的高层工具，不等于
   LIBERO 的原始 env 方法。
3. **robosuite / MuJoCo control layer**：LIBERO 的 `OffScreenRenderEnv` 基于
   robosuite；控制器把动作解释为关节位置控制并计算执行所需的 torque，
   MuJoCo 再推进动力学与接触。

这一区分很重要：生成程序通常调用第 2 层；第 2 层内部才会进入第 1 层，
然后由第 3 层完成物理执行。[推断]

论文 §3.1 的总述是：agent 在 **CaP-X** 中写 code-as-policy 程序，CaP-X
建立在 MuJoCo Playground 上，并提供感知、几何和运动规划 API。固定公开
仓库把 LIBERO 实验具体实现为 LIBERO-Pro `OffScreenRenderEnv`、robosuite
与 MuJoCo，再在其上接 ASPIRE curated API。因此把论文缩写成“agent 同时
直接调用 LIBERO API 和 MuJoCo 原生控制 API”并不准确：前者主要由环境
adapter 调用，后者主要位于 robosuite/controller 的下层。[事实：
论文 experimental setup](https://arxiv.org/html/2607.00272#S3.SS1)
[事实：固定 LIBERO loader](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/libero/__init__.py#L49-L161)

## 1. LIBERO benchmark / env Python API

### 1.1 suite、task、BDDL 和 init state

ASPIRE 的 `load_libero_task()` 首先执行：

```python
benchmark_dict = benchmark.get_benchmark_dict(help=False)
task_suite = benchmark_dict[suite_name]()
task = task_suite.get_task(task_id)
```

这里的 `suite_name` 选择一个 LIBERO benchmark suite，`task_id` 选择 suite
内的任务对象。[事实：ASPIRE loader](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/libero/__init__.py#L78-L84)

任务对象提供 `problem_folder`、`bddl_file`、`init_states_file` 和自然语言
`language` 等元数据。loader 用
`get_libero_path("bddl_files") / problem_folder / bddl_file` 定位 BDDL，
从 BDDL 的 `(:language ...)` 读取任务描述（失败时回退到 `task.language`）。
BDDL 是环境/任务的声明式描述；它不是 agent 要直接执行的 Python 控制代码。
[事实：loader 的路径和语言解析](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/libero/__init__.py#L35-L54)
[事实：LIBERO 官方 README](https://github.com/Lifelong-Robot-Learning/LIBERO/blob/master/README.md)

同一个 loader 再调用 `task_suite.get_task_init_states(task_id)`，取得该任务
的初始状态集合；必要时按 `init_states_file` 从 LIBERO 的 init-state 路径
直接加载。它把这组状态放入 `LiberoHandle.init_states`。[事实：
init-state 加载](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/libero/__init__.py#L121-L154)

### 1.2 OffScreenRenderEnv 的构造

loader 传给 LIBERO 的环境构造参数包括：

```python
{
    "bddl_file_name": bddl_file_path,
    "camera_heights": cam_h,
    "camera_widths": cam_w,
    "controller": "JOINT_POSITION",  # Franka LIBERO path
    "horizon": horizon,
    "control_freq": control_freq,
    "camera_depths": camera_depths,
}
env = OffScreenRenderEnv(**env_args)
```

`OffScreenRenderEnv` 是 LIBERO 提供的、适合离屏图像采集的环境入口；相机
尺寸、深度输出、控制器类型和 episode horizon 在这里确定。[事实：ASPIRE
loader](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/libero/__init__.py#L105-L117)
[官方 LIBERO 环境入口（源码）](https://github.com/Lifelong-Robot-Learning/LIBERO/blob/master/libero/libero/envs/env_wrapper.py)

特别是公开的 Franka LIBERO 路径固定使用 `controller="JOINT_POSITION"`。
这表示 action 的机械臂部分是关节位置控制量（ASPIRE 的移动 helper 会将
目标与当前关节位置形成 delta），不是“直接给 MuJoCo torque”。[事实：
FrankaLiberoEnv 构造和动作注释](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L51-L78)

### 1.3 reset、step、check_success

ASPIRE 的轻量 `LiberoHandle` 只做一层转接：

```python
def reset(self, seed=None):
    self.env.seed(seed)
    obs = self.env.reset()
    if self.init_states is not None:
        self.env.set_init_state(self.init_states[0])
    return obs, {}

def step(self, action):
    obs, reward, done, info = self.env.step(action)
    return obs, float(reward), bool(done), info
```

因此，原始 LIBERO/robosuite env 的四元组 `obs, reward, done, info` 在
`LiberoHandle.step` 中保留；ASPIRE 的更外层 simulator wrapper 再把它包装成
Gym 风格的 terminated/truncated 语义。[事实：`LiberoHandle`](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/libero/__init__.py#L18-L33)

`FrankaLiberoEnv.reset()` 会调用 handle reset，并在指定 seed 时按
`(seed - 1) % len(init_states)` 选 init state，再重置一次环境；这只是
ASPIRE 对 trial seed 的确定性映射，不应误读为 LIBERO benchmark API 的
通用保证。[事实：ASPIRE reset](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L155-L194)

低层 `FrankaLiberoEnv.step(action)` 主要是 fallback：采集 observation、
读取当前 reward，并按 wrapper 的 `max_steps` 设置 truncated；通常生成代码
调用的是 curated movement helpers，而不是直接调用这个 fallback。[事实：
step 实现](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L204-L218)

任务完成的权威检查是：

```python
def task_completed(self) -> bool:
    return self.handle.env.check_success()
```

也就是说，ASPIRE 的 `task_completed` 对 LIBERO 任务等价于调用 benchmark
环境的 `check_success()`；它不是“进程退出正常”、不是单纯 reward 非零、也
不是截图相似度。[事实：completion bridge](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L533-L535)

## 2. ASPIRE 注入给生成 Python 的 curated API

生成代码看到的是 `FrankaLiberoApiReduced.functions()` 返回的受选 API 字典，
而不是自动获得一份新的 LIBERO SDK。固定 commit 中公开的主要入口为：

| 类别 | 注入函数 | 作用（源码事实） |
|---|---|---|
| 观察 | `get_observation()` | 返回 agentview / wrist RGB、depth、相机内参/位姿、机械臂关节、末端位姿和夹爪状态 |
| 分割 | `segment_sam3_text_prompt()`、`segment_sam3_point_prompt()` | 用文本或图像点提示取得 mask 与 score |
| 3-D | `mask_to_world_points()`（相关 reduced skill API） | 用 mask、depth、相机几何反投影到世界/机器人坐标点 |
| 抓取 | `plan_grasp()`、`plan_grasp_from_point_clouds()` | Contact-GraspNet 生成抓取候选与分数 |
| 几何 | `get_oriented_bounding_box_from_3d_points()`、`subsample_point_cloud()`、`filter_noise()` | 点云几何与噪声处理 |
| 运动 | `solve_ik()`、`goto_pose()`、`move_to_joints()` | PyRoki/IK 求关节；阻塞式移动到目标关节 |
| 夹爪 | `open_gripper()`、`close_gripper()` | 以 curated helper 设置夹爪 |

`functions()` 明确列出这些名称；是否包含 Molmo 点提示取决于本地服务是否
可达，注释掉的 CuRobo 函数也不是默认注入 API。[事实：API 注册表](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced.py#L99-L126)

### 2.1 观察、SAM3、mask-to-points 与抓取

`get_observation()` 将 LIBERO 相机观测整理成稳定的 ASPIRE 结构：
`obs["agentview"]["images"]["rgb"]`、depth、intrinsics、`pose_mat`，
以及 `robot0_eye_in_hand` 的对应字段；机器人字段包含关节位置、末端
位置/四元数和归一化夹爪值。[事实：observation contract](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced.py#L129-L154)

SAM3 helper 接收 RGB 与文本或像素点提示，返回包含布尔 `mask` 和置信度
`score` 的字典列表；它不直接修改 LIBERO 状态。[事实：SAM3 API](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced.py#L157-L224)

mask-to-points 是“视觉证据 → 3-D 几何”的桥：使用深度、内参与外参筛选 mask
像素并反投影，所得点再可交给 OBB 或抓取规划。它不是 MuJoCo ground-truth
物体位姿读取。[事实：点云/几何实现](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced_skill_library.py#L143-L223)

`plan_grasp*` 产生相机/点云坐标系的候选抓取位姿和分数；curated API 再将
候选变换到机器人参考系供 IK 使用。[事实：抓取规划 contract](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced.py#L255-L350)

### 2.2 IK、move_to_joints 与夹爪

`solve_ik(position, quaternion_wxyz)` 调用 PyRoki，并保留/复用上次配置；
`goto_pose` 是 IK + `move_to_joints` 的组合。[事实：运动 API](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced.py#L353-L489)

公开 LIBERO 路径的关键细节是：`move_to_joints(joints)` 接收形状为 `(7,)`
的目标关节角；环境层读取当前 7 关节，构造 **7 个 joint deltas + 1 个
gripper action** 的 LIBERO action，再送入 `handle.step(action)`。[事实：
动作构造](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L220-L253)

夹爪在 ASPIRE 内部用 0（闭）到 1（开）的 fraction 表示，送给 LIBERO 前
映射为控制器使用的符号（开为 `-1`、闭为 `+1`）。因此生成程序应优先
调用 `open_gripper` / `close_gripper` 或 curated movement helper，而不应
自己猜 action 的夹爪符号。[事实：gripper state 与映射](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L273-L290)

## 3. robosuite / MuJoCo control layer：真正怎样动起来

公开 LIBERO action 的实际路径可画成：

```text
generated Python
  -> curated move_to_joints / open_gripper / close_gripper
  -> 7 joint deltas + gripper action
  -> LIBERO OffScreenRenderEnv.step(action)
  -> robosuite JOINT_POSITION controller
  -> controller computes actuator torques
  -> MuJoCo mj_step / contact and dynamics
  -> observation, reward, done; check_success for task_completed
```

ASPIRE 的代码明确选择 `JOINT_POSITION`，并在环境层每个控制周期调用
`handle.step`；所以“移动到关节”是位置控制目标/增量的接口，不是把七个
数值写进 `sim.data.ctrl` 的 shortcut。[事实：ASPIRE action path](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L226-L252)

robosuite 官方控制器文档说明 controller 将高层 action 转成机器人 actuator
所需的控制信号；其 joint-position controller 属于 position-based control
系列。[事实：robosuite controller 文档](https://robosuite.ai/docs/modules/controllers.html)
ASPIRE 固定的 robosuite fork 更直接显示：输入是相对关节位置 action，控制器
以位置误差和速度误差算 torque，robot wrapper 再把裁剪后的 torque 写入
`sim.data.ctrl`。[事实：固定 joint-position controller](https://github.com/Max-Fu/robosuite/blob/a498b087d4bc5a3981e3d27030d09bc537a537f3/robosuite/controllers/joint_pos.py#L10-L16)
[事实：固定 torque 计算](https://github.com/Max-Fu/robosuite/blob/a498b087d4bc5a3981e3d27030d09bc537a537f3/robosuite/controllers/joint_pos.py#L210-L248)
[事实：固定 actuator 写入](https://github.com/Max-Fu/robosuite/blob/a498b087d4bc5a3981e3d27030d09bc537a537f3/robosuite/robots/single_arm.py#L216-L260)
MuJoCo 官方文档说明 actuator control 位于 `mjData.ctrl`，`mj_step` 推进
动力学。[事实：MuJoCo actuation 文档](https://mujoco.readthedocs.io/en/stable/computation/index.html#actuation-model)
[事实：MuJoCo stepper](https://mujoco.readthedocs.io/en/stable/overview.html)

因此本文使用“robosuite 计算 torque，MuJoCo 执行”的简写：更精确地说，
固定 fork 的 robosuite controller 根据 joint-position action 与当前状态产生
torque，写入 MuJoCo actuator control；MuJoCo 再结合模型、接触与积分器推进
动力学。[事实；不同 robosuite 版本的内部类名仍不应被当作 ASPIRE curated API]

### raw MuJoCo 边界

ASPIRE simulation constitution 对生成代码的协议要求禁止读取 MuJoCo
ground truth、asset 文件等不可迁移捷径；允许的做法是使用观察、视觉、
几何、抓取和运动 API。[事实：ASPIRE simulation constitution](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/CLAUDE.md#L33-L82)

但这不是硬安全 sandbox：固定 commit 的 executor 仍将 `env`、curated API
和 helper 放进 persistent globals，然后对生成代码使用 Python `exec`；
生成代码理论上仍有 full import access。raw MuJoCo 被禁止是 protocol /
agent-constitution 约束，不是 AST denylist 或操作系统级能力隔离。[事实：
executor 注入与代码执行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/tasks/base.py#L159-L211)
[事实：官方安全警告](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/README.md#L105-L108)

## 4. 论文/公开代码中的使用协议

### 4.1 code-as-policy 与 primitive trace

ASPIRE 论文把 coding agent 生成的可执行 Python 程序作为 policy：程序
调用感知、规划和动作 primitive，在环境中运行；失败后由 agent 根据执行
证据修复程序并复用 validated skill，而不是在线更新一个端到端策略网络。[事实：
ASPIRE 论文](https://arxiv.org/html/2607.00272)

每个 primitive 的调用都会写入 trace，包含函数名、参数/结果摘要、step、
耗时和错误；视觉/深度、SAM3 mask、抓取和 IK 等较大证据另行保存。这样
可以区分“没看到”“分割错误”“抓取失败”“IK 不可达”和“任务检查失败”。
[事实：trace logger](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/trace_logger.py#L318-L340)
[事实：primitive-specific capture](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/trace_logger.py#L53-L263)

这里的 `task_completed` 统计应理解为 LIBERO `check_success()` 的任务级
标签；“sandbox_rc == 0”只表示程序没有 crash，不能替代任务成功。[事实：
completion 与 evaluator](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L533-L535)
[事实：evaluator](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/evosearch_eval.py#L559-L598)

### 4.2 seed split、一个任务一个程序

公开 LIBERO Fix Loop / evolutionary runbook 使用如下角色边界：

| 角色 | seeds / 程序规则 | 含义 |
|---|---|---|
| debug / development | **51–65** | 允许诊断并修复候选程序 |
| held-out evaluation | **1–50** | 修复完成后评估，不把结果写回正在修复的程序/skill |
| policy unit | **one program per task** | 一个任务的程序跨其评估 seeds 运行，而不是每个 seed 重新生成一份 policy |

这些 split 和“每任务一个固定程序”的实验语境来自论文的 LIBERO-Pro 描述
及固定 commit 的公开 Quick Start / actor prompt；它们不是 LIBERO Python API
本身的限制。[事实：论文实验协议](https://arxiv.org/html/2607.00272#S3.SS3)
[事实：Quick Start](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/QUICKSTART.md#L5-L11)
[事实：debug prompt](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/subagent-prompt.md#L72-L98)

**边界提醒**：论文算法中的 development / validation / held-out 术语与某一
个脚本是否机械强制 seed range 不是同一件事；应以实验 manifest 和 runbook
记录为准，不能从 `reset(seed=...)` 单独推断评估隔离。[推断]

## 最小可复述模型

对读者/agent 最安全的短句是：

> LIBERO suite/task 决定 BDDL、init state 和 `OffScreenRenderEnv`；ASPIRE
> 把观察、SAM3、mask-to-points、抓取、IK、关节移动、夹爪和几何工具作为
> curated API 注入生成程序；公开 Franka 路径使用 `JOINT_POSITION`，
> `move_to_joints` 发 7 joint deltas + gripper，robosuite controller 计算
> torque，MuJoCo 推进物理；任务完成只认 LIBERO `check_success()`。raw
> MuJoCo 访问是 protocol 禁止项，但 executor 是 `exec` 加 env 注入，故这
> 是行为约束而非硬 security sandbox。
