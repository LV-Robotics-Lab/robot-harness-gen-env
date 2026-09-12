# ASPIRE 如何驱动机器人，以及执行引擎里是否有 baseline 策略

本文回答两个容易混淆的问题：ASPIRE 的 coding agent 产生什么，机器人最终由什么执行；论文里的
baseline、搜索初始程序、curated helper API 和底层控制器是否是同一个“baseline 策略”。

证据只使用论文、NVIDIA 项目页和公开仓库固定提交
`7ba73d3bcac8f6b6d4a7d67ed4040988f768d282`。论文事实、源码观察和本文推论不互相冒充。

## 结论

ASPIRE 是 **code-as-policy**：LLM/coding agent 写高层 Python 任务程序，程序调用预定义的感知、
抓取、规划和控制 API；API 再调用 IK、关节运动、夹爪控制和仿真器/真实机器人控制栈。LLM 不直接
输出关节力矩，ASPIRE 也没有用梯度下降训练一个新的低层运动策略。论文把“训练”放在程序修复和
可复用 skill 的累积上。[论文 §2](https://arxiv.org/html/2607.00272v1#S2)、
[官方 README](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/README.md#L1-L9)。

**执行引擎本身没有一个隐含的、负责整项任务的 baseline 高层策略。** 它是程序执行器、curated
primitive API、低层适配器和 trace recorder。系统确实有多种 baseline/初始程序/固定控制 routine，
但它们位于不同层，不能统称为“执行引擎里的 baseline 策略”。

## 从任务语言到机器人运动

```text
任务 + API 文档 +（可选）检索 skills / 视觉上下文
                    │
                    ▼
          coding actor 生成 Python 程序 P
                    │
                    ▼
    CodeExecutionEnv 把 API 函数注入 globals 后 exec(P)
                    │
                    ▼
 感知/SAM3 → 抓取候选 → IK/规划 → 关节运动/夹爪动作
                    │
                    ▼
        仿真器或真实机器人控制栈改变世界状态
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
 task_completed/reward   primitive trace/keyframes/errors
          └─────────┬─────────┘
                    ▼
        actor 诊断、改程序；coordinator 验证并晋升 skill
```

### 1. Actor 先写高层程序；公开实现有两条不同路径

论文复现的 LIBERO Fix Loop 主要是**外层 repository-aware coding actor**：它先按 task-exploration
协议查看 seed 51、读取共享 skills、保存场景图和分析，写 `initial_code.py`；随后用
`replay_trial.py --replay-code` 在 development seeds 51–65 回放同一文件，再从失败目录读取 trace、
keyframes、code 和 summary，修改文件后重新回放。
[Fix Loop actor](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/subagent-prompt.md#L72-L146)。

仓库另有一个通用/基线 runner：每个 trial reset 到指定 seed，可按配置采集初始视觉并向模型查询
初始代码；程序可拆成多个 block，可选 multi-turn 根据已执行代码、stdout/stderr 和视觉差分选择
`REGENERATE` 或 `FINISH`。初始视觉不是必然输入，取决于 `use_visual_feedback`、image/video
differencing 等配置。这条 inner runner 不能冒充论文 Fix Loop 外层 actor 的唯一执行方式。
[trial 主流程](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L642-L650)、
[视觉条件](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L231-L292)、
[生成与执行循环](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L727-L879)。

### 2. 执行引擎把 primitive 注入 Python 命名空间

API 类通过 `functions()` 暴露 callable，`combined_doc()` 把签名和 docstring 加进任务 prompt。
执行器把 `obs`、原始 `env`、全部 API 和 helper functions 放进持久 globals，再用 Python `exec`
运行生成代码。[API 文档与注册](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/base_api.py#L19-L127)、
[prompt 和执行 namespace](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/tasks/base.py#L145-L211)。

公开 LIBERO reduced API 暴露相机观测、SAM3 分割、Contact-GraspNet 抓取候选、PyRoKi IK、
`move_to_joints`、`goto_pose`、`open_gripper` 和 `close_gripper` 等函数。
[初始化与公开 API](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced.py#L58-L126)。

### 3. 真正的运动由低层控制器/环境完成

例如 `goto_pose` 先调用 IK，把目标位姿转为 7 轴关节配置，再调用环境的
`move_to_joints_blocking`；夹爪 primitive 则执行固定步数的开合控制。这里的 IK、轨迹执行和夹爪
routine 是控制基础设施，不是一个会自主决定“先抓什么、再放哪里”的 task policy。
[LIBERO Franka 控制实现](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced.py#L380-L489)。

### 4. 每个 primitive 旁边留下诊断 trace

traced API wrapper 在 `finally` 中记录函数名、参数摘要、结果或异常、耗时；特定调用还可缓冲 RGB、
depth、相机内外参、SAM overlay/mask、grasp poses/scores 和夹爪宽度。在 traced 配置下，trial 结束
后写 `trace.json`；只有确实缓冲了对应证据才有 `keyframes/`，只有启用 recording/video
differencing 且捕获到帧时才有视频。它们不是任意配置下每个 trial 的必然产物。
[trace entry](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/trace_logger.py#L318-L430)、
[保存与 wrapper](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/trace_logger.py#L432-L516)、
[trial 落盘](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L923-L947)。

### 5. “代码没崩”不等于“机器人完成任务”

执行代码后，environment 另算 reward，并调用低层 `task_completed()`；LIBERO 的实现委托 benchmark
自身的 `check_success()`。`TrialSummary.success` 却只等于 `sandbox_rc == 0`。因此选择程序、报告
机器人成功率时必须看 `task_completed`，不能看 `success`。
[step 语义](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/tasks/base.py#L269-L304)、
[LIBERO 成功判据](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L533-L535)、
[TrialSummary 字段](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L949-L983)。

### 6. 外环修程序、选程序、积累 skill

论文 Algorithm 1 先执行初始程序 `P0`，再让模型基于历史 Top-3、失败 traces 和 skill library 提议
`K` 个候选，在 debug configurations 上选择，最后在独立 validation configurations 上运行最佳程序，
并从经过验证的历史中抽取可复用 pattern。搜索对象是离散 Python 程序，不是神经网络权重；论文也
没有规定固定 crossover 算子。[Algorithm 1](https://arxiv.org/html/2607.00272v1#alg1)。

## Baseline 到底在哪里

| 对象 | 是否存在 | 准确含义 |
| --- | --- | --- |
| 执行引擎内置的整任务 baseline policy | **否** | engine 执行 actor 写的程序，并提供 primitive 与 trace；它不自行决定任务级动作序列。 |
| 搜索初始程序 `P0` | **是** | Algorithm 1 必须从一个初始程序出发；它是候选/性能地板，不是 engine 内部策略。 |
| 论文消融的 base system | **是** | Table 7 描述为 zero-shot Claude Opus 4.6 with 15 example programs；再加入 execution-engine repair + skill library，最后加 evolutionary search。[Table 7](https://arxiv.org/html/2607.00272v1#A4.T7)。 |
| 论文比较基线 CaP-Agent0 | **是，但在方法外比较** | 它使用视觉差分、预定义 skill library、每 episode 推理与 retry；ASPIRE 在 LIBERO/Robosuite held-out 上则每任务使用一个固定生成程序。[§3.2](https://arxiv.org/html/2607.00272v1#S3.SS2)、[§3.3](https://arxiv.org/html/2607.00272v1#S3.SS3)。 |
| VLA baselines `OpenVLA`、`π0`、`π0.5` | **是，但在方法外比较** | 这些是实验对照，不在 ASPIRE execution engine 中。 |
| deterministic IK、home pose、夹爪开合 | **是** | 它们是底层 primitive/controller，给高层程序使用，不是 task-level baseline policy。 |
| `FrankaLiberoApiReducedSkillLibrary` | **是，但名称易误导** | 它是 curated callable helper 集合，主要补坐标变换、深度几何等函数；不是论文中 coordinator 持续晋升的跨任务 Markdown skill library。[源码](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced_skill_library.py#L38-L73)。 |
| 通用 Python `cap.skills.SkillLibrary` | **有，且 opt-in/未完整接线** | trial 仅在 `evolve_skill_library=true` 且 `task_completed` 时抽取顶层函数并保存；library 类另提供按出现次数晋升和注入的方法，但当前公开 trial/Fix Loop 非测试路径没有调用这些方法。论文 Fix Loop 主路径使用 coordinator 审计的 Markdown skills，二者不能混称。[trial hook](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L951-L964)、[library methods](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/skills/library.py#L72-L158)。 |
| oracle code 开关 | **有** | trial 可显式选择 `env.oracle_code`，用于 oracle/debug 路径；不能据此说正常 ASPIRE actor 由 oracle 驱动。[trial](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L727-L735)。 |

公开仓库的 runbook 与论文叙事还有一个应保留的边界：当前 LIBERO Fix Loop 明确要求 actor 不读
外部 baseline 代码或输出，而是观察一次场景后从头生成 `initial_code.py`；另有独立
`run-baseline.md`/baseline config 用于收集对照。因而“public Fix Loop 是 baseline-free”与“论文有
CaP-Agent0/base-system 对照”并不矛盾，它们说的是不同实验角色。
[Fix Loop 说明](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/SKILL.md#L14-L23)、
[baseline config](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/env_configs/libero/franka_libero_baseline_debug.yaml#L1-L59)。

## 真实机器人不是直接部署仿真策略

论文的 sim-to-real 实验把仿真中发现的 skill 作为 in-context guidance 提供给真实机器人 coding
agent；真实机器人仍使用自己的感知、标定、控制 API，并通过真实执行反馈重新编程。论文明确说这
不是 direct policy deployment。[论文 §3.6](https://arxiv.org/html/2607.00272v1#S3.SS6)。

## 必须保留的安全边界

公开实现是 raw in-process `exec`，并把原始 `env` 放进 globals；生成代码拥有 full import access。
官方 README 明确警告 trial process/watchdog 不是安全 sandbox。`sandbox_rc` 只表示执行异常状态，
不是隔离证明，也不能把 simulation agent 直接接到物理硬件。
[执行路径](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/tasks/base.py#L159-L211)、
[官方警告](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/README.md#L105-L108)。

## 本次研究过程与可见资产

| 步骤 | 产生/固定的资产 | 能说明什么 | 不能说明什么 |
| --- | --- | --- | --- |
| 前置约束与主机预检 | `preflight.md`、`research_log.md`、`source_receipt.json` | 记录单卡主机、缺失的服务/凭据/环境以及停止规则 | 不是 ASPIRE 效果证据 |
| 一手资料与固定源码 | `external/ASPIRE@7ba73d3`、`paper_and_official_sources.md`、`upstream_source_audit.md` | 把论文事实、源码观察和本项目推论分开 | 官方仓库没有论文原始 rollout，不能重算论文表格 |
| 上游无服务机制复现 | `upstream_selected_tests.xml`、`upstream_selected_tests_pinned.xml`、`upstream-unit-venv/`、`reproduction_report.md` | 官方 pin 环境下选定机制测试为 20 pass / 4 explicit skip | 没跑 LLM actor、TraceLogger rollout、感知/规划服务或论文指标 |
| 本项目静态基线 | `real_catalog/`、`prompt_matrix_*`、两个 `acceptance_100_*` bundle | 真实 catalog 上 33/33 静态编译，两个任务各 100/100 静态求解 | `runtime_required=false`，不是物理成功或 ASPIRE 改善 |
| E1 声明身份 gate | `runtime_binding_before.json`、`runtime_binding_after.json` | 五例 accuracy 从 0.20 到 1.00，四个 unsafe accepts 降到 0 | 只校验 producer 自报 identity，不是不可伪造 provenance |
| E1 单例真实 SAPIEN 回放 | `real_replay_can_on_plate_seed7/` 中 JSON、7 张 PNG、1 个 MP4 | 一次 can-on-plate 物理 gate 通过；视频 320×240、12 fps、10 秒、120 帧 | 不是 ASPIRE agent/task-policy rollout，也没有 matched repair 对照 |
| E1b/c/d 状态完整性 | `pending_review_*`、`pending_entrypoints_*`、`static_only_state_*` JSON；`stage5_pending_review_e2e/` | before/after 攻击探针锁住 pending/static-only 不得伪装 final/pass | legacy smoke 被目录漂移阻断，目录没有成功的 smoke 媒体 |
| E2 validated-memory proxy | `heldout_harness_benchmark.json` 及 `experiments/` | 120 个固定合成 fault cases 上 frozen/reactive/memory mHRC 为 0/0.5/1.0 | 是确定性 contract proxy，不是 LLM、simulator physics 或 policy learning 结果 |
| 收尾和 claim audit | `final_report.md`、`results.tsv`、`source_receipt.json`、`research_log.md` | 把 commits、哈希、偏差、失败和结论边界串起来 | 后续移动工作树不能倒灌成原实验结果 |

`artifacts/` 当前在本机有 4,840 个文件、约 240 MB，但被根 `.gitignore` 的 `artifacts/` 规则忽略；
普通 clone 不会得到这些大体量本地证据。Git 跟踪的是研究文档、实验代码以及主要 artifact 的哈希
回执。`logs/` 当前为空；命令和失败过程写在 `research_log.md`，独立原始测试回执只保留了 XML/JSON。

本次研究没有完成论文规模的 LIBERO/Robosuite ASPIRE rollout，也没有生成官方论文指标的原始
trace/keyframe/video。唯一真实 SAPIEN 媒体是本项目 can-on-plate seed 7 的物理 gate 回放；它验证
一次本地 runtime-evidence 消费路径，不是 ASPIRE agent 驱动机器人完成任务的复现。

本说明生成于 2026-08-31，起因是对研究结果的执行引擎/baseline 追问；没有创建分支，没有改动
ASPIRE submodule，也没有把并发工作树的后续实现计入固定源码结论。
