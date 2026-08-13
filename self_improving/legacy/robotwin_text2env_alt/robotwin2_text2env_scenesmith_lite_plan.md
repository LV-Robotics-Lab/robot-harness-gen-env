# Build RoboTwin2 Text2Env Demo (SceneSmith-lite Tabletop)

更新时间：2026-06-24  
所属大任务：Self-Improving Agents for Physical AI  
当前关注任务：Build RoboTwin2 Text2Env demo (SceneSmith-lite tabletop)

---

## 0. 一句话目标

把自然语言 tabletop 机器人任务转换成 RoboTwin2 中可执行、可验证、可收集数据的仿真任务：

```text
Natural language task
  -> Text2Env structured spec
  -> RoboTwin2 task program
  -> simulator smoke test
  -> data-collection dry run
  -> policy train/eval hook or explicit blocker
```

这个 demo 是 Self-Improving Agents for Physical AI 的第一个最小闭环切片。它不要求一开始完成完整的自动训练系统，也不要求复刻完整 SceneSmith 室内场景生成；目标是先把“任务需求 -> 可执行仿真环境/任务”这条线打通。

---

## 1. Self-Improving Agents for Physical AI 在做什么

这个大任务想做一个面向 Physical AI / robotics 的自改进数据与评估系统。它的核心不是单独训练一个 policy，也不是单独生成漂亮的仿真场景，而是建立一个闭环：

```text
Setup
  -> Collect
  -> Train
  -> Evaluate
  -> Diagnose
  -> targeted Setup
```

也就是：

1. 给定一个机器人任务或当前模型失败点。
2. 自动生成或修改仿真环境、物体摆放、任务条件和验证器。
3. 收集专家轨迹或 rollout 数据。
4. 训练或微调 policy。
5. 评估成功率、失败模式、数据缺口。
6. 把诊断结果结构化成下一轮环境/数据需求。

最终想要的是一个“Physical AI data engine”：

- 能记住之前的任务 spec、数据集、checkpoint、eval report 和失败诊断。
- 能根据 failure trace 生成更有针对性的新任务和新数据。
- 能用 governance gate 检查环境是否物理合理、任务是否可执行、验证器是否可信。
- 能把 agent 生成的环境、任务、数据和结论放进一个可审计的 blackboard / memory。

所以这个大任务真正的研究点是：

- Agentic environment generation：agent 自动设计环境和任务。
- Structured task/data spec：每个任务、物体、动作、相机、验证器都有结构化定义。
- Failure diagnosis：不只看成功/失败，还要知道为什么失败。
- Targeted data generation：根据诊断补数据，而不是随机扩数据。
- Closed-loop improvement：生成、训练、评估、诊断、再生成。

---

## 2. 这个 Text2Env demo 在大任务里的位置

Build RoboTwin2 Text2Env demo 是大任务的 v0 demo。

它要证明：

> 一个 agent 能把自然语言 tabletop 任务，变成 RoboTwin2 里可执行、可验证、可收集数据的任务程序。

暂时不做：

- 不做完整 house / room generation。
- 不做完整 SceneSmith 级别的室内空间生成。
- 不做完整 policy self-improvement 闭环。
- 不一开始追求大规模训练。

先做：

- 1-2 个 tabletop 任务。
- 一个稳定的 Text2Env schema。
- 一个 RoboTwin2 task program 生成/改写流程。
- 一个 simulator smoke test。
- 一个 data-collection dry run。
- 一个 Pi / OpenVLA-OFT train/eval hook，或者写清楚 blocker。

---

## 3. RoboTwin2 和 SceneSmith 的角色分工

### 3.1 RoboTwin2 是执行底座

RoboTwin2 是这个任务的仿真、任务和数据生成底座。根据公开资料，RoboTwin2 是一个面向双臂机器人操作的数据生成和 benchmark 框架，包含 expert data generation pipeline、50-task benchmark、RoboTwin Object Dataset、domain randomization，以及自动 task program synthesis 能力。

在本任务中，RoboTwin2 负责：

- 提供机器人 embodiment、双臂设置、相机、物体和任务 API。
- 承接 Text2Env 输出的环境/任务 spec。
- 执行 task reset、step、observation、success check。
- 做专家数据生成或 data collection dry run。
- 后续连接 policy 训练和评估入口。

你需要重点摸清：

- 新 task 怎么定义。
- task 文件应该放在哪里。
- object/category/asset 怎么声明。
- pose/randomization 怎么写。
- camera/observation/action space 怎么配置。
- success verifier 怎么接 simulator state。
- expert data generation 命令怎么跑。
- Pi / OpenVLA-OFT 训练评估入口在哪里。

### 3.2 SceneSmith 是方法参考

SceneSmith 是一个从自然语言生成 simulation-ready indoor scenes 的 agentic framework。它的完整版本面向室内房间/住宅环境，通过 designer、critic、orchestrator 等 VLM agents，在 layout、furniture placement、object population、asset generation/retrieval、physical property estimation 等阶段生成物理可用的场景。

在本任务中，SceneSmith 不作为完整复刻目标，而是作为 workflow 参考：

- Designer：根据自然语言任务提出环境、物体、位置、约束和验证器。
- Critic：检查物理可行性、碰撞、任务可执行性、API 可实现性。
- Orchestrator：决定接受、修改、重试，并输出最终 spec 和代码生成 prompt。

这里的 SceneSmith-lite 只保留 tabletop manipulation 需要的部分：

- table / workspace。
- manipulable objects。
- articulated objects if needed，例如 drawer、box、cabinet。
- camera setup。
- robot reachability。
- task verifier。
- simple collision/stability/reachability checks。

---

## 4. What To Do

### 4.1 目标产物

最终交付一个可运行的最小 demo：

```text
输入：
  "Put the red cup into the drawer while avoiding the blue bowl."

输出：
  1. text2env JSON spec
  2. RoboTwin2 task scaffold / task program
  3. simulator reset 成功
  4. object pose / collision / reachability 基本检查通过
  5. success verifier 可调用
  6. data collection dry run 可启动
  7. train/eval hook 位置明确
```

### 4.2 必须完成

- [ ] 整理 RoboTwin2 新 task API。
- [ ] 定义 Text2Env 中间 schema。
- [ ] 做 1-2 个 tabletop task examples。
- [ ] 写 Text2Env prompt / script：自然语言 -> env JSON。
- [ ] 写 RoboTwin2 task generation / scaffold route：env JSON -> task code。
- [x] 跑 simulator smoke test。
- [x] 跑 data-collection dry run。
- [x] 写 train/eval hook 或 blocker note。
- [x] 保存命令、日志、截图/视频或失败证据。

### 4.3 暂不追求

- [ ] 大规模自动任务生成。
- [ ] 完整 indoor scene generation。
- [ ] 完整 policy training result。
- [ ] 完整 self-improvement loop。
- [ ] 复杂多房间、长时序家庭任务。
- [ ] 高质量 photorealistic rendering。

---

## 5. How To Do

### Step 1: 摸清 RoboTwin2 task 接口

先不要写大系统。第一步是读 RoboTwin2 repo，把“人工写一个新任务需要什么”弄清楚。

需要回答：

- 一个 task program 的最小文件结构是什么？
- task 类或配置文件在哪里注册？
- reset 函数负责什么？
- object spawn / asset loading 怎么写？
- randomization 在哪里配置？
- camera 和 observation 怎么设置？
- success condition 怎么实现？
- expert policy / demo collection 怎么调用？
- data 保存格式是什么？
- train/eval 脚本期待什么数据结构？

产出：

- [ ] `docs/robotwin2_task_api.md`
- [ ] 至少一个已有 task 的文件路径和调用命令。
- [ ] 一个“新建 task checklist”。

### Step 2: 定义 Text2Env schema

不要让 LLM 直接生成代码。先让它生成结构化 JSON，这样更容易检查、修改、复用。

建议 schema：

```json
{
  "task_name": "put_cup_in_drawer",
  "language_instruction": "Put the red cup into the drawer.",
  "workspace": {
    "type": "tabletop",
    "surface": "table",
    "bounds": {
      "x": [-0.4, 0.4],
      "y": [0.2, 0.8],
      "z": [0.0, 0.4]
    },
    "robot_setup": "dual_arm"
  },
  "objects": [
    {
      "id": "red_cup",
      "category": "cup",
      "asset_hint": "cup",
      "initial_pose": {
        "region": "left_front_table",
        "xyz": [-0.2, 0.45, 0.02],
        "rpy": [0, 0, 0]
      },
      "properties": {
        "graspable": true,
        "movable": true
      }
    },
    {
      "id": "drawer",
      "category": "drawer",
      "asset_hint": "articulated_container",
      "initial_pose": {
        "region": "back_center_table",
        "xyz": [0.0, 0.7, 0.02],
        "rpy": [0, 0, 0]
      },
      "properties": {
        "articulated": true,
        "openable": true
      }
    }
  ],
  "cameras": [
    "front",
    "left_wrist",
    "right_wrist"
  ],
  "success": {
    "type": "spatial_relation",
    "condition": "red_cup_inside_drawer",
    "tolerance": {
      "position_m": 0.05
    }
  },
  "randomization": {
    "object_pose": true,
    "lighting": true,
    "texture": false
  },
  "safety_constraints": [
    "no_initial_object_collision",
    "target_reachable_by_robot",
    "success_condition_sim_state_observable"
  ]
}
```

产出：

- [x] `schemas/text2env.schema.json`
- [x] `examples/tabletop_tasks/put_cup_in_drawer.json`，当前是 `draft_requires_articulation`
- [x] `examples/tabletop_tasks/move_object_between_zones.json`，当前是 `ready_for_scaffold`

配套说明：

- [x] `schemas/text2env_schema_v0.md`

### Step 3: 做 SceneSmith-lite agent 流程

完整 SceneSmith 是多阶段 indoor scene generation。这里缩小成 tabletop 任务生成：

```text
Designer
  -> propose objects, workspace, poses, cameras, success condition

Critic
  -> check missing objects, collisions, reachability, API support, verifier clarity

Orchestrator
  -> accept / revise / retry
  -> output final Text2Env JSON
```

Designer prompt 应该输出：

- task name。
- task instruction。
- object list。
- object pose。
- robot setup。
- cameras。
- success verifier。
- randomization。

Critic prompt 应该检查：

- 是否缺少关键物体。
- 物体是否重叠或初始状态不可行。
- 目标是否在 tabletop workspace 内。
- 机器人是否能 reach。
- 是否用了 RoboTwin2 没有的 object/category。
- success condition 是否能从 simulator state 判断。
- 任务是否太模糊。

Orchestrator 应该决定：

- 直接通过。
- 修改 object pose。
- 替换 unsupported object。
- 简化任务。
- 要求重新生成。

产出：

- [x] `docs/scenesmith_lite_agent_flow.md`
- [x] `prompts/text2env_designer.md`
- [x] `prompts/text2env_critic.md`
- [x] `prompts/text2env_orchestrator.md`
- [x] `scripts/generate_text2env.py`

### Step 4: Env JSON -> RoboTwin2 task program

这一步把结构化 spec 翻译成 RoboTwin2 可运行代码。

建议先做 scaffold，不要追求自动生成完美代码：

```text
env_json
  -> task name
  -> object declarations
  -> initial poses
  -> reset function scaffold
  -> success checker scaffold
  -> randomization config
  -> data collection command template
```

初版可以允许人工修补 task program。关键是要把“LLM 生成不确定性”限制在 scaffold 内，让 Critic 和 smoke test 把错误暴露出来。

产出：

- [x] `scripts/generate_robotwin_task.py`
- [x] generated task scaffold：`generated/robotwin_tasks/move_object_between_zones/`
- [x] 一条可重复运行的生成命令。

当前 Task B 生成结果：

```text
Local generated root:
generated/robotwin_tasks/move_object_between_zones/

Generated files:
generated/robotwin_tasks/move_object_between_zones/envs/move_object_between_zones.py
generated/robotwin_tasks/move_object_between_zones/description/task_instruction/move_object_between_zones.json
generated/robotwin_tasks/move_object_between_zones/manifest.json

Remote deployed files:
~/RoboTwin/envs/move_object_between_zones.py
~/RoboTwin/description/task_instruction/move_object_between_zones.json
```

复现生成命令：

```bash
python scripts/generate_robotwin_task.py generate examples/tabletop_tasks/move_object_between_zones.json
```

下一步 smoke 命令：

```bash
cd ~/RoboTwin
conda activate RoboTwin
bash collect_data.sh move_object_between_zones demo_smoke 0
```

### Step 5: Smoke test

每个生成任务至少要过以下检查：

- [x] RoboTwin2 能加载任务。
- [x] reset 不报错。
- [x] 物体出现在预期区域。
- [x] 初始物体没有明显碰撞/穿模。
- [x] robot/camera observation 可读。
- [x] success checker 初始为 false。
- [x] 人工或 scripted 成功状态下 success checker 能变 true。
- [x] 运行 N steps 不崩。

建议 smoke test 输出：

```text
reports/smoke_tests/<task_name>/
  command.txt
  stdout.log
  stderr.log
  reset_screenshot.png
  object_state.json
  success_check.json
  notes.md
```

### Step 6: Data-collection dry run

不要求马上获得高质量训练数据，但要证明数据通路可启动。

需要检查：

- [x] expert collection 命令能启动。
- [x] episode 文件能保存。
- [x] observation/action/state 字段完整。
- [x] language instruction 被写入数据。
- [x] camera frames 或 state observation 可用。
- [x] 失败时有明确日志。

产出：

- [x] 一条 dry run 命令。
- [x] 一个小样本 episode 或明确 blocker。
- [x] 数据字段说明。

### Step 7: Policy train/eval hook

不要求完成 policy training，但要知道下一步怎么接。

至少写清楚：

- Pi 或 OpenVLA-OFT 的训练入口在哪里。
- RoboTwin2 生成的数据是否需要格式转换。
- language instruction、image observation、action/state 的字段映射。
- eval entrypoint 在哪里。
- 如果不能跑，blocker 是什么。

产出：

- [x] `docs/policy_hook_note.md`
- [x] 一条 train/eval command draft。
- [x] blocker list。

---

## 6. 最小交付物 MVP

MVP 不要求漂亮，但必须可复现。

### 必交文件

- [x] `robotwin2_task_api_notes.md`
- [x] `schemas/text2env.schema.json`
- [x] `schemas/text2env_schema_v0.md`
- [x] `examples/tabletop_tasks/put_cup_in_drawer.json`，draft
- [x] `examples/tabletop_tasks/move_object_between_zones.json`
- [x] `scripts/generate_text2env.py`
- [x] `scripts/generate_robotwin_task.py`
- [x] `reports/smoke_tests/move_object_between_zones/notes.md`
- [x] `docs/policy_hook_note.md`

### 必交能力

- [x] 给一个自然语言 tabletop 任务，能生成结构化 env JSON。
- [x] env JSON 能被转换成 RoboTwin2 task scaffold。
- [x] 至少一个 task 能在 RoboTwin2 中 reset。
- [x] 至少一个 success verifier 可调用。
- [x] 至少一个 data collection dry run 尝试完成或有明确 blocker。
- [x] 有清楚的复现命令和日志路径。
- [x] 至少一个 policy train/eval hook 能启动或有明确 blocker。

### Demo 通过标准

```text
Pass if:
  1. Natural language -> Text2Env JSON works.
  2. Text2Env JSON -> RoboTwin2 task scaffold works.
  3. RoboTwin2 task loads and resets.
  4. Objects appear in valid tabletop positions.
  5. Success checker is implemented or scaffolded with simulator-state fields.
  6. Data collection dry run starts or blocker is documented with evidence.
```

---

## 7. 推荐的 1-2 个初始任务

### Task A: Put cup into drawer

自然语言：

```text
Put the red cup into the drawer.
```

为什么适合：

- 有 graspable object。
- 有 articulated / container object。
- success verifier 明确：cup inside drawer。
- 可以测试 object pose、drawer state、robot reachability。

风险：

- drawer asset / articulated object 支持可能复杂。
- expert policy 可能需要开抽屉动作。

降级版本：

```text
Put the red cup into the open box.
```

### Task B: Move object between zones

自然语言：

```text
Move the green block from the left zone to the right zone without moving the blue bowl.
```

为什么适合：

- 不需要 articulated object。
- success verifier 简单：block position in target region。
- 可以测试 obstacle / distractor。
- 更适合作为第一条 smoke test。

风险：

- 如果加入 obstacle avoidance，expert path 可能复杂。

降级版本：

```text
Move the green block to the right target zone.
```

建议顺序：

1. 先做 Task B，验证基本 Text2Env -> task -> reset。
2. 再做 Task A，引入 drawer/container 类任务。

---

## 8. 工作路线图

### Phase 0: Repo and API orientation

- [ ] clone / locate RoboTwin2 repo。
- [ ] 跑通 RoboTwin2 官方 demo 或已有 task。
- [ ] 记录环境安装、依赖、GPU/CPU 要求。
- [ ] 选一个最接近 tabletop pick-place 的 existing task 作为模板。

### Phase 1: Schema and examples

- [x] 起草 Text2Env schema。
- [x] 写 Task B JSON。
- [x] 写 Task A JSON draft。
- [x] 人工检查 object/category 是否能映射到 RoboTwin2，当前 Task B 使用 custom box 和 RoboTwin2 `002_bowl`。

### Phase 2: Generation loop

- [x] 写 Designer prompt。
- [x] 写 Critic prompt。
- [x] 写 Orchestrator prompt。
- [ ] 跑 3-5 次自然语言输入，比较输出稳定性。已完成 1 次 sanity test：`examples/tabletop_tasks/move_red_block_to_blue_zone.json`
- [x] 固化 JSON validation。

### Phase 3: RoboTwin2 scaffold

- [x] 写 env JSON -> task scaffold 转换脚本。
- [x] 生成 Task B scaffold。
- [x] 手工补齐 RoboTwin2 API 细节：region marker 使用薄 static box，place target 使用 marker functional point。
- [x] 记录哪些字段无法自动生成：articulated object / drawer qpos 暂不自动生成，保留为 draft。

### Phase 4: Smoke and dry run

- [x] Task B reset 通过。
- [x] Task B success checker 通过。
- [x] Task B data-collection dry run 尝试。
- [x] 保存日志和截图。

### Phase 5: Extend to harder task

- [ ] Task A scaffold。
- [ ] drawer/container 支持检查。
- [ ] 如果 drawer 太复杂，记录 blocker 并使用 open box 降级。

### Phase 6: Handoff note

- [ ] 写 policy hook note。
- [ ] 写 demo report。
- [ ] 更新 dashboard task comment / status。

---

## 9. 每日工作记录模板

复制下面模板追加到本文件底部即可。

```markdown
## YYYY-MM-DD Update

### Done

- [ ] 

### Findings

- 

### Blockers

- 

### Next

- [ ] 

### Artifacts

- Commands:
- Logs:
- Screenshots/videos:
- Commit/hash:
```

---

## 2026-06-24 Update

### Done

- [x] Completed Step 5 smoke test for `move_object_between_zones`.
- [x] Completed data-collection dry run for one episode.
- [x] Pulled local preview artifacts: MP4, instruction JSON, scene info, seed, and three sampled frames.
- [x] Added smoke report: `reports/smoke_tests/move_object_between_zones/notes.md`.
- [x] Completed Step 7 ACT policy hook: two-episode data collection, ACT preprocessing, one-epoch smoke train, checkpoint load, and eval rollout startup.

### Findings

- Initial generated task failed because `002_bowl` was unstable with qpos `[1, 0, 0, 0]`.
- After changing the bowl qpos to `[0.5, 0.5, 0.5, 0.5]`, the next failure was placement planning at step 3.
- The passing v0 uses a center-near target zone, a farther bowl distractor, and no generated keep-out area for region marker boxes.
- HDF5 output contains `endpose`, `joint_action`, `observation`, and `pointcloud`, with 2293 frames.
- ACT requires at least two episodes for its default train/validation split; a single `demo_smoke` episode gives an empty train split.
- ACT eval starts, loads `policy_last.ckpt`, and enters rollout, but `script/eval_policy.py` hardcodes `test_num = 100`, so the smoke eval was stopped with `timeout 180`.

### Blockers

- None for Task B smoke/data dry run.
- Log noise remains: many `svulkan2 OIDN Error: invalid handle` messages during video export, but output files are valid.
- For a short policy eval, add a `test_num` argument to `script/eval_policy.py` or create a small eval wrapper.

### Next

- [x] Write `docs/policy_hook_note.md`.
- [x] Decide whether Step 6 should be considered fully done for MVP or extended with a second task.
- [ ] Try Task A or downgrade it to open-box/container if drawer articulation takes too long.

### Artifacts

- Command log: `~/RoboTwin/install_logs/smoke_collect_move_object_between_zones_step5_pass.log`
- Remote HDF5: `~/RoboTwin/data/move_object_between_zones/demo_smoke/data/episode0.hdf5`
- Remote video: `~/RoboTwin/data/move_object_between_zones/demo_smoke/video/episode0.mp4`
- Local preview folder: `robotwin_text2env_smoke/move_object_between_zones/`
- Smoke report: `reports/smoke_tests/move_object_between_zones/notes.md`
- Policy hook report: `docs/policy_hook_note.md`
- Policy hook script: `scripts/run_policy_hook.sh`
- Remote ACT checkpoint: `~/RoboTwin/policy/ACT/act_ckpt/act-move_object_between_zones/demo_policy_hook-2-smoke/`

---

## 10. 远程 5090 环境状态

更新时间：2026-06-24 Asia/Shanghai

默认远程工作目录：

```text
zhengye = /data/sdb/zhengye
```

RoboTwin2.0 当前环境：

- Repo: `~/RoboTwin`
- Conda env: `RoboTwin`
- Python: 3.10.20
- PyTorch: `2.7.0+cu128`
- CUDA toolkit for JIT: conda env 内 `nvcc 12.8.93`
- Assets: `~/RoboTwin/assets`，约 16G
- VS Code Remote CPU hygiene: 已在 `~/RoboTwin/.vscode/settings.json` 排除 `.git`、`assets`、`data`、`outputs`、`checkpoints`、`logs`、`install_logs`、`wandb` 等大目录

为什么没有完全照官方 pinned torch：

- RoboTwin 官方安装脚本会安装 `torch==2.4.1+cu121`。
- RTX 5090 是 `sm_120`，`torch 2.4.1+cu121` 在 5090 上会报 `no kernel image is available for execution on the device`。
- 当前环境改用 `torch==2.7.0+cu128` 和 CUDA 12.8 的 `nvcc`，以支持 RTX 5090 / `compute_120`。

已完成 smoke test：

```bash
cd ~/RoboTwin
source ~/miniconda3/etc/profile.d/conda.sh
conda activate RoboTwin
bash collect_data.sh beat_block_hammer demo_smoke 0
```

输出：

- Dataset: `~/RoboTwin/data/beat_block_hammer/demo_smoke/data/episode0.hdf5`
- Trajectory: `~/RoboTwin/data/beat_block_hammer/demo_smoke/_traj_data/episode0.pkl`
- Instruction: `~/RoboTwin/data/beat_block_hammer/demo_smoke/instructions/episode0.json`
- Video: `~/RoboTwin/data/beat_block_hammer/demo_smoke/video/episode0.mp4`
- Smoke size: about 231M
- Video: 1657 frames, 320x240, 30 FPS

注意：

- `conda activate RoboTwin` 已配置自动设置 `CUDA_HOME`、`CPATH`、`LIBRARY_PATH` 等 CUDA 12.8 路径。
- 已设置 `TORCH_CUDA_ARCH_LIST=12.0`，对应 RTX 5090 的 `compute_120/sm_120`，避免 PyTorch extension 为所有可见 GPU 架构编译。
- 默认限制编译并发 `MAX_JOBS=4`、`CMAKE_BUILD_PARALLEL_LEVEL=4`，避免远程 CPU 占用过高。
- 日志中可能出现大量 `svulkan2 OIDN Error: invalid handle`，当前 smoke test 中这类信息没有阻止数据保存。

已完成 Text2Env Task B smoke / dry run：

```bash
cd ~/RoboTwin
source ~/miniconda3/etc/profile.d/conda.sh
conda activate RoboTwin
timeout 900 bash collect_data.sh move_object_between_zones demo_smoke 0 2>&1 | tee install_logs/smoke_collect_move_object_between_zones_step5_pass.log
```

输出：

- Dataset: `~/RoboTwin/data/move_object_between_zones/demo_smoke/data/episode0.hdf5`，约 348M
- Instruction: `~/RoboTwin/data/move_object_between_zones/demo_smoke/instructions/episode0.json`
- Video: `~/RoboTwin/data/move_object_between_zones/demo_smoke/video/episode0.mp4`
- Local preview: `robotwin_text2env_smoke/move_object_between_zones/`
- Report: `reports/smoke_tests/move_object_between_zones/notes.md`
- HDF5 summary: `joint_action/vector (2293, 14)`, `observation/head_camera/rgb (2293,)`, `endpose/left_endpose (2293, 7)`

---

## 11. 后续方向：Generated Assets -> RoboTwin asset adapter

博士后续可能会安排其他人负责自然语言到 3D asset 的生成。我的重点不需要放在训练 3D 生成模型本身，而是要定义清楚：**生成出来的资产应该以什么格式交付，怎样放进 RoboTwin，并怎样验证它能被 RoboTwin 稳定加载、碰撞、抓取和用于 task program。**

### 11.1 目标边界

当前 Text2Env demo 使用 RoboTwin 现有资产或简单几何体。后续要支持自然语言中出现 RoboTwin asset library 没有的物体，例如 `peach`、`toy duck`、`custom mug`、`small wooden tray`。

新的目标链路：

```text
Natural language
-> Text2Env scene/task spec
-> asset requirement spec
-> generated 3D mesh from asset-generation owner
-> RoboTwin asset adapter
-> ~/RoboTwin/assets/objects/<asset_id>/
-> RoboTwin task program uses create_actor(...)
-> load/stability/task smoke test
```

我的责任范围：

- 定义 RoboTwin 可用资产包格式。
- 把别人生成的 mesh/metadata 转成 RoboTwin asset folder。
- 写 asset install / validate / smoke 脚本。
- 在 Text2Env schema 和 task generator 中支持 `generated_asset` 引用。
- 跑 RoboTwin load test、stability test、task smoke test。

暂不负责：

- 训练 3D 生成模型。
- 从零生成 articulated drawer/cabinet 的 joint hierarchy。
- 解决任意复杂物体的专家策略。

### 11.2 RoboTwin 普通刚体资产接口

RoboTwin 普通刚体资产通过 `create_actor(...)` 加载。接口位置：

```text
~/RoboTwin/envs/utils/create_actor.py
/data/sdb/zhengye/RoboTwin/envs/utils/create_actor.py
```

典型 task program 调用：

```python
self.peach = create_actor(
    scene=self,
    pose=rand_pose(...),
    modelname="generated_peach",
    convex=True,
    is_static=False,
    model_id=0,
)
```

RoboTwin 会读取：

```text
~/RoboTwin/assets/objects/generated_peach/
/data/sdb/zhengye/RoboTwin/assets/objects/generated_peach/
```

推荐资产目录结构：

```text
~/RoboTwin/assets/objects/<asset_id>/
  visual/
    base0.glb
  collision/
    base0.glb
  model_data0.json
  points_info.json
  asset_manifest.json
  preview.png
```

最小可加载结构：

```text
~/RoboTwin/assets/objects/<asset_id>/
  visual/base0.glb
  collision/base0.glb
  model_data0.json
```

如果没有 `visual/` 或 `collision/` 子目录，RoboTwin 也会尝试读取根目录下的 `base0.glb`，但不推荐这样做。后续统一要求 visual mesh 和 collision mesh 分开。

### 11.3 `model_data0.json` 必须包含什么

`model_data0.json` 是 RoboTwin asset 能否用于 skill / grasp / place 的关键 metadata。最小字段建议：

```json
{
  "center": [0.0, 0.0, 0.0],
  "extents": [1.0, 1.0, 1.0],
  "scale": [0.08, 0.08, 0.08],
  "transform_matrix": [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0]
  ],
  "target_pose": [],
  "contact_points_pose": [],
  "functional_matrix": [],
  "orientation_point": [],
  "stable": true
}
```

字段含义：

- `scale`: RoboTwin 加载 mesh 时使用的缩放比例，必须让物体落在 tabletop 合理尺度，例如 peach 直径约 `0.07m`。
- `center` / `extents`: asset 原始几何的中心和包围盒尺寸，用于 sanity check。
- `transform_matrix`: mesh 坐标系到 RoboTwin 约定坐标系的修正矩阵。
- `contact_points_pose`: 可抓取点。`grasp_actor(...)` 会用这些点来找抓取姿态。
- `functional_matrix`: 功能点。例如容器内部、杯口中心、按钮位置、放置目标点。
- `target_pose`: 一些 task 会用 target point。
- `stable`: 标记这个 asset 是否经过稳定性检查。

如果资产只是被动 distractor，`contact_points_pose` 和 `functional_matrix` 可以先为空；如果资产要被抓取或作为放置目标，就必须补这些点。

### 11.4 `points_info.json` 建议格式

`points_info.json` 给 agent / human 解释点位语义。推荐格式：

```json
{
  "contact_points": [
    {
      "id": [0, 1],
      "description": "Opposite side grasp points on the peach.",
      "usage": "Used for side grasping the peach."
    }
  ],
  "functional_points": [
    {
      "id": 0,
      "description": "Top center of the peach.",
      "usage": "Used for checking object pose or stacking target."
    }
  ]
}
```

### 11.5 资产生成方应该交付什么

资产生成负责人可以使用 TRELLIS、Hunyuan3D、Meshy、Tripo 或其他 text/image-to-3D 方法。但交付给本项目时，不要只给 prompt，至少要给：

```text
incoming_assets/<asset_id>/
  source_prompt.txt
  visual.glb 或 visual.obj
  preview.png
  optional_metadata.json
```

如果他们能额外交付 collision mesh，更好：

```text
incoming_assets/<asset_id>/
  collision.glb
```

如果他们不能生成 collision mesh，我这边的 adapter 需要自动做：

- mesh cleanup / normalize origin。
- scale normalization。
- collision mesh simplification 或 convex decomposition。
- stable pose 检查。
- contact point / functional point 初始估计。

### 11.6 我们项目中的推荐 staging 路径

不要把大 GLB/HDF5 直接提交到 GitHub。建议本项目只提交 schema、脚本和小 metadata，大资产放在 5090 或单独 artifact storage。

本项目 repo 中的 staging 路径：

```text
/data/sdb/zhengye/robotwin-text2env-demo/generated/assets/<asset_id>/
```

建议内容：

```text
generated/assets/<asset_id>/
  asset_manifest.json
  model_data0.json
  points_info.json
  README.md
```

大文件只保存在 5090：

```text
/data/sdb/zhengye/generated_assets/<asset_id>/
  visual/base0.glb
  collision/base0.glb
  preview.png
```

安装到 RoboTwin 的最终路径：

```text
~/RoboTwin/assets/objects/<asset_id>/
/data/sdb/zhengye/RoboTwin/assets/objects/<asset_id>/
```

### 11.7 Adapter / installer 需要做的事情

后续新增脚本建议：

```text
scripts/prepare_generated_asset.py
scripts/install_generated_asset_to_robotwin.sh
scripts/validate_robotwin_asset.py
```

目标命令形式：

```bash
python scripts/prepare_generated_asset.py \
  --asset-id generated_peach \
  --input /data/sdb/zhengye/generated_assets/generated_peach/source.glb \
  --category fruit \
  --size-m 0.075 0.075 0.065 \
  --mass-kg 0.12 \
  --out /data/sdb/zhengye/generated_assets/generated_peach/robotwin_asset

bash scripts/install_generated_asset_to_robotwin.sh \
  /data/sdb/zhengye/generated_assets/generated_peach/robotwin_asset \
  ~/RoboTwin

python scripts/validate_robotwin_asset.py \
  --robotwin-root ~/RoboTwin \
  --asset-id generated_peach \
  --model-id 0
```

安装后应生成：

```text
~/RoboTwin/assets/objects/generated_peach/visual/base0.glb
~/RoboTwin/assets/objects/generated_peach/collision/base0.glb
~/RoboTwin/assets/objects/generated_peach/model_data0.json
~/RoboTwin/assets/objects/generated_peach/points_info.json
~/RoboTwin/assets/objects/generated_peach/asset_manifest.json
```

### 11.8 Text2Env schema 后续扩展

当前 Text2Env object 使用 `kind: "asset"` 时默认是 RoboTwin 已有资产。后续应增加：

```json
{
  "id": "peach",
  "role": "manipulated",
  "kind": "generated_asset",
  "category": "fruit",
  "asset": {
    "asset_id": "generated_peach",
    "modelname": "generated_peach",
    "model_id": 0,
    "convex": true,
    "required_files": [
      "visual/base0.glb",
      "collision/base0.glb",
      "model_data0.json"
    ]
  },
  "physical": {
    "mass_kg": 0.12,
    "graspable": true,
    "movable": true,
    "is_static": false
  }
}
```

task generator 最终仍然生成：

```python
self.peach = create_actor(
    scene=self,
    pose=...,
    modelname="generated_peach",
    convex=True,
    is_static=False,
    model_id=0,
)
```

### 11.9 Articulated asset 暂时怎么处理

drawer / cabinet / scissors / faucet 这类 articulated asset 不能只靠普通 text-to-3D mesh。RoboTwin articulated object 需要：

```text
~/RoboTwin/assets/objects/<asset_id>/
  mobility.urdf
  model_data.json
  optional mesh files
```

加载接口：

```python
create_urdf_obj(
    scene=self,
    pose=...,
    modelname="<asset_id>",
    fix_root_link=True,
)
```

短期策略：

- 不要求资产生成方从零生成 articulated URDF。
- drawer/cabinet 先使用 RoboTwin/PartNet/SAPIEN 已有 articulated 模板。
- 生成模型只负责外观 mesh 或 texture，不能作为 joint hierarchy 的权威来源。
- Task A 的 drawer 类任务继续标记为 harder task / articulated blocker。

### 11.10 验收标准

一个 generated asset 被认为可以接入 RoboTwin，至少要满足：

- [ ] 目录已安装到 `~/RoboTwin/assets/objects/<asset_id>/`。
- [ ] `create_actor(...)` 能加载，不返回 `None`。
- [ ] table 上 reset 后 2-3 秒不爆炸、不飞走、不穿透。
- [ ] 尺度合理，例如水果/杯子/小物体在 `5cm-15cm` 量级。
- [ ] collision mesh 和 visual mesh 大致对齐。
- [ ] 如果是 manipulated object，至少有可用 `contact_points_pose`。
- [ ] 如果是 target/container/support object，至少有可用 `functional_matrix`。
- [ ] 能跑一个最小 spawn smoke。
- [ ] 能被一个简单 Text2Env task 引用并进入 data collection dry run。

---

## 12. 当前 TODO

### High priority

- [x] 找到 RoboTwin2 repo / 本地环境位置。
- [x] 跑通一个官方 example task。
- [x] 找一个现有 tabletop manipulation task 作为模板。
- [x] 整理 RoboTwin2 task API note。
- [x] 定义 Text2Env schema v0。
- [x] 写 Task B: move block between zones 的 JSON。

### Medium priority

- [x] 写 Designer / Critic / Orchestrator prompt。
- [x] 写 JSON validator。
- [x] 写 env JSON -> RoboTwin2 task scaffold 脚本。
- [x] 生成 Task B scaffold。
- [x] 跑 Task B smoke test。

### Lower priority

- [ ] 尝试 Task A: cup into drawer。
- [ ] 定义 `generated_asset` schema 扩展。
- [ ] 写 generated asset -> RoboTwin asset package 的 adapter / installer 设计。
- [ ] 写最小 `generated_peach` 或 mock asset 接入测试。
- [x] 对接 data collection dry run。
- [ ] 写 Pi / OpenVLA-OFT hook note。
- [x] 整理 demo report。
- [ ] 更新 dashboard 状态和 comment。

---

## 13. 风险和判断

### Risk 1: RoboTwin2 API 复杂，自动生成 task code 容易错

处理方式：

- 先生成 scaffold。
- 允许人工补代码。
- 把 smoke test 和 checker 做扎实。

### Risk 2: SceneSmith 完整范围太大

处理方式：

- 只做 SceneSmith-lite tabletop。
- 不做完整房间布局、家具、房屋结构。
- 只保留 Designer / Critic / Orchestrator 的 agentic pattern。

### Risk 3: Drawer / articulated object 太复杂

处理方式：

- 第一任务用 block / cup / open box。
- drawer 作为第二任务。
- 如果 drawer asset 或 joint state 难接，明确 blocker。

### Risk 4: data collection dry run 卡在 expert policy

处理方式：

- 先证明 reset 和 verifier。
- dry run 可以用 random / scripted minimal action。
- 专家策略不可用时记录 blocker 和所需 API。

### Risk 5: 任务生成看起来合理，但 verifier 不可实现

处理方式：

- Critic 必须检查 success condition 是否能从 simulator state 判断。
- 所有 success 都先限制为 spatial relation / object state / region membership。

### Risk 6: Text-to-3D asset 长得像，但不能物理仿真

处理方式：

- 不把 GLB 直接当成 RoboTwin asset。
- 必须经过 asset adapter，生成 visual mesh、collision mesh、scale、stable pose、metadata。
- 每个 generated asset 先跑 isolated load/stability smoke，再进入 task smoke。
- 如果 contact/functional points 缺失，只允许作为 passive distractor，不允许作为 manipulated object 或 target object。

### Risk 7: 生成 articulated asset 难以自动接入

处理方式：

- drawer/cabinet 暂时不要求 text-to-3D 直接生成 URDF。
- articulated 物体使用模板或已有 asset，生成模型只做外观替换。
- Text2Env critic 必须把 `drawer`、`cabinet`、`door`、`scissors` 这类任务标记为 `requires_articulation`。

---

## 14. Dashboard 更新建议

如果要更新 dashboard，不要在 dashboard comment 中写任何 token。

建议 comment 格式：

```text
2026-06-23 update: Created SceneSmith-lite RoboTwin2 Text2Env work plan. Next: inspect RoboTwin2 task API, run one existing tabletop task, then define Text2Env schema v0 and generate the first move-block-between-zones example.
```

建议状态：

- 如果刚开始做 API orientation：`doing`
- 如果 schema + smoke test 都完成：再考虑 `done`

---

## 15. References

- RoboTwin2 project page: https://robotwin-platform.github.io/
- RoboTwin2 official repo: https://github.com/robotwin-Platform/robotwin
- RoboTwin2 documentation: https://robotwin-platform.github.io/doc/index.html
- RoboTwin2 arXiv: https://arxiv.org/abs/2506.18088
- SceneSmith project page: https://scenesmith.github.io/
- SceneSmith repo: https://github.com/nepfaff/scenesmith
- SceneSmith arXiv: https://arxiv.org/abs/2602.09153
- TRELLIS repo: https://github.com/microsoft/TRELLIS
- Hunyuan3D 2.1 repo: https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1
- Meshy Text to 3D API: https://docs.meshy.ai/en/api/text-to-3d
- Tripo API docs: https://platform.tripo3d.ai/
