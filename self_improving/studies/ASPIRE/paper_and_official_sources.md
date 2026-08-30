# ASPIRE 一手资料研究：论文、官方项目页与公开实现

> 研究日期：2026-08-31
> 资料边界：只使用 ASPIRE 论文、NVIDIA 官方项目页、NVlabs 官方仓库及其中源码/文档；未使用二手资料。
> 论文版本：[arXiv:2607.00272v1，2026-06-30](https://arxiv.org/abs/2607.00272)。
> 源码冻结点：[NVlabs/ASPIRE `7ba73d3bcac8f6b6d4a7d67ed4040988f768d282`（Public release，2026-08-21）](https://github.com/NVlabs/ASPIRE/commit/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282)。

## 0. 证据标记与结论边界

本文统一使用以下标记，避免把作者主张、源码事实和我们的判断混在一起：

- **[F｜原文事实]**：论文、官方项目页或官方仓库文档直接陈述。
- **[O｜可复现观察]**：在上述冻结提交上，通过静态阅读、文件枚举或确定性算术可以复核；不等于成功跑完论文实验。
- **[I｜推论]**：由第一性原理或多项一手证据推出，必须另做实验才能升级为本项目事实。
- **[U｜未证实]**：官方材料未给足证据，本文不补猜。

本文件完成的是“理解 + 官方实现审计”，不是本地复现报告。安装、运行、基准实验及对本项目的最终价值判断应分别记录，不能用论文数字代替本地证据。

## 1. 一句话理解与第一性原理模型

**[F]** ASPIRE（Agentic Skill Programming through Iterative Robot Exploration）不是通过梯度下降更新机器人策略权重，而是让编码 agent 编写可执行机器人程序、从执行失败中修复程序，并把通过验证的修复压缩成可复用 skill；官方 README 直接把它概括为“training is skill refinement”“trained model is a repo of sensorimotor skills”“distributed training is a panel of agents”。来源：[官方 README 第 1–9 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/README.md#L1-L9)、[论文摘要](https://arxiv.org/html/2607.00272)。

**[I]** 从最小必要变量看，它可以写成一个非梯度的闭环搜索系统：

```text
状态     x = (任务 τ, 当前程序 P, skill 库 L, 执行历史 H, 诊断文档 A)
动作     a = 编写/修改/选择一个程序候选
环境反馈 z = (task_completed, reward, primitive traces, keyframes, errors)
更新     P <- 经 debug seeds 验证的更优程序
         L <- Audit(L ∪ ExtractValidatedPatterns(H, P))
目标     用更少的真实/仿真试验，找到在未见配置上成功率更高的固定程序
约束     程序只能依赖可迁移的观测、感知、规划与控制 API
```

论文 Algorithm 1 把这一过程抽象成 `Execute(P,S) -> (r,Z)`、基于历史 top-3 和 skill 库提议 `K` 个修复候选、在 debug 集上选优、在 validation 集上验证，再提取 validated patterns。来源：[算法 1](https://arxiv.org/html/2607.00272#alg1)。公开 LIBERO evaluator 的实际排序量是 `task_completed` 的通过率，另行记录 mean reward；这使一个可操作的等价目标成为：

\[
\hat J_S(P)=\frac{1}{|S|}\sum_{s\in S}\mathbf 1[\texttt{task\_completed}(\operatorname{Execute}(P,s))],
\qquad P^*=\arg\max_{P\in\Pi_{allowed}}\hat J_{S_{dbg}}(P).
\]

这是**[I] 等价操作化**，不是论文声称训练了一个可微目标；实现依据是 evaluator 对 `task_completed` 求和并按 `pass_rate` 排序：[evosearch evaluator 第 559–598 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/evosearch_eval.py#L559-L598)。

## 2. 问题设定：ASPIRE 试图修复什么根因

### 2.1 机器人程序失败的信用分配问题

**[F]** 论文认为机器人编码 agent 的核心困难不只是“rollout 失败”，而是失败可能来自感知、抓取候选、运动规划、接触动力学或长时序恢复中的任一环节；只有任务级成功/失败，无法定位应修改哪一层。来源：[Introduction](https://arxiv.org/html/2607.00272#S1)、[Robot Execution Engine](https://arxiv.org/html/2607.00272#S2.SS1)。

**[I]** 第一性原理上，这是一类部分可观测的因果归因问题：如果 harness 只返回最终二值标签，则许多彼此冲突的修复假设具有相同观测；只有把证据贴近 primitive 调用边界，才能区分“没看见”“看见但抓错”“规划不可达”“抓住后掉落”。因此，ASPIRE 的第一项真正贡献不是更强的 primitive，而是提高失败信号的信息量。

### 2.2 跨任务经验不累积

**[F]** 论文指出既有系统通常在任务结束后丢弃修复与恢复策略，使第 100 个任务的 agent 并不比第 1 个任务更有经验。ASPIRE 将成功修复蒸馏成跨任务可检索的紧凑上下文。来源：[Introduction](https://arxiv.org/html/2607.00272#S1)、[Skill Library](https://arxiv.org/html/2607.00272#S2.SS2)。

**[I]** 所存储的最有价值对象并非完整任务脚本，而是条件化的“失败签名 → 适用条件 → 修复策略 → 证据”。完整脚本容易过拟合对象、场景和 embodiment；机制级修复更可能迁移。

### 2.3 单轨迹自我修复会陷入局部最优

**[F]** 论文认为单一 repair trajectory 容易反复微调同一个错误策略，因此引入程序级 evolutionary search，让每轮多个候选测试不同假设，并以 survivors 和剩余失败 trace 条件化下一轮。来源：[Evolutionary Search](https://arxiv.org/html/2607.00272#S2.SS3)。

**[I]** 这里“evolutionary”指离散程序种群的生成、执行、选择和再生成，不是神经网络遗传训练，也没有交叉/突变算子的固定数学定义；变化算子由 LLM 根据历史、skill 和 trace 生成。

## 3. 三个算法组件

### 3.1 Closed-loop Robot Execution Engine

**[F]** 对每个 perception、planning、grasp 和 control 调用，engine 记录 API、输入、输出、返回状态、耗时以及可用的 RGB keyframe、overlay、grasp candidates、object poses 和 motion-planning 结果。论文称 agent 不接收整段原始视频，而聚焦于 primitive 周围的证据。来源：[§2.1](https://arxiv.org/html/2607.00272#S2.SS1)、[Figure 2](https://arxiv.org/html/2607.00272#S2.F2)、[官方项目页 Method](https://research.nvidia.com/labs/gear/aspire/#method)。

**[O]** 公开实现通过 mixin 包装 API 函数，在 `finally` 中记录调用，即使函数抛异常也会留下错误和耗时；记录项包含 step、相对时间、函数名、参数摘要、结果摘要及 `keyframe_saved`。来源：[trace wrapper 第 478–516 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/trace_logger.py#L478-L516)、[trace entry 第 318–340 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/trace_logger.py#L318-L340)。

**[O]** tracer 的多模态落盘内容比一个 JSON 更丰富：

- gripper action 后额外读取 gripper width，作为抓空/边缘抓/实抓诊断信号；
- `get_observation` 保存 RGB、depth、intrinsics、extrinsics；
- SAM3 保存 overlay 和 top mask；
- `plan_grasp` 保存 pose/score 数组；
- `save()` 写 `trace.json` 和 `keyframes/` 下的 JPEG/NPY/NPZ。

来源：[gripper width 第 341–365 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/trace_logger.py#L341-L365)、[keyframes 第 367–428 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/trace_logger.py#L367-L428)、[保存逻辑第 432–471 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/trace_logger.py#L432-L471)。

**[O]** 大数组在 JSON 中不会原样展开：超过 50 个元素会被压成 shape/dtype 字符串，原始深度、mask、grasp 则作为旁路数组文件保存。这是降低上下文噪声与保留可深挖证据之间的实现折中。来源：[encoder 第 34–50 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/trace_logger.py#L34-L50)。

论文 Figure 2 的 radio 案例给出具体因果链：感知找到 radio，但靠近目标的 navigation goals 落入桌子 collision buffer，反复返回 `PLANNING_ERROR`；agent 因而没有改 perception/grasp，而是尝试多个绕物体的 approach angles，成功后把它提升为 Multi-Angle Approach skill。来源：[Figure 2 与 §2.1](https://arxiv.org/html/2607.00272#S2.F2)。

### 3.2 Continually Expanding Skill Library

**[F]** skill 内容不预设为单一 taxonomy，可包括 localization heuristics、perception prompts、grasp constraints、navigation recovery、motion primitives、scene reasoning 和 debugging workflows。每条推荐包含 failure signature、when-to-apply、repair strategy，必要时附代码片段；actor 上报结构化 findings，coordinator 审计可复用性、API 合规与 debug validation 后才准入。来源：[§2.2](https://arxiv.org/html/2607.00272#S2.SS2)、[Appendix A](https://arxiv.org/html/2607.00272#A1)、[Appendix E.1](https://arxiv.org/html/2607.00272#A5.SS1)。

**[O]** 当前 LIBERO Fix Loop 的共享库是 Markdown 文件，writer 只有 coordinator；actor 只能写 `findings.md`。promotion 工具在修改前保存整库快照，修改后保存 exact patch、逐文件哈希、整库哈希与 append-only JSONL，并把 `verify` 作为下一任务 dispatch gate。来源：[coordinator skill promotion 第 150–202 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md#L150-L202)、[promotion recorder 第 1–10 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/record_skill_promotion.py#L1-L10)、[begin/finish 第 171–303 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/record_skill_promotion.py#L171-L303)。

**[O]** LIBERO-90 scaling runbook进一步规定 admission filters：debug 成功率至少 15/30、不能无证据替换已有有效策略、拒绝场景常数/绝对坐标、应能帮助至少两类任务，并要求把通过率写入 skill。来源：[zero-shot build coordinator 第 178–213 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/zeroshot-transfer/main-agent-prompt.md#L178-L213)。这比论文主文的“validated reusable repairs”更具体，但属于 public-release protocol，不应反推为所有论文实验都用了完全相同阈值。

**[O]** 仓库还包含另一套通用、opt-in 的 `cap.skills.SkillLibrary`：当 `evolve_skill_library=true` 且 trial `task_completed` 时，它从成功代码中抽取顶层函数，以出现次数累计并在默认至少 2 次后 promotion。论文复现实验 runbook 使用的则是前述 coordinator 审计的 Markdown skills；当前 traced LIBERO config 没有开启该 opt-in flag。两套机制不能混称为同一个实验实现。来源：[trial opt-in hook 第 951–964 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L951-L964)、[generic library 第 72–124 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/skills/library.py#L72-L124)、[traced config](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/env_configs/libero/franka_libero_traced.yaml)。

### 3.3 Evolutionary Search over Programs

**[F]** 论文每轮由模型基于任务、历史 top-3、skill 库和失败 traces 提议 `K` 个程序，逐一执行并扩充历史；达到阈值或用完 `T` 轮停止，最后对最佳程序做 validation 并抽取 validated patterns。来源：[§2.3](https://arxiv.org/html/2607.00272#S2.SS3)、[Algorithm 1](https://arxiv.org/html/2607.00272#alg1)。

**[O]** 当前 LIBERO runbook 固定 `K=8`，debug seeds 为 51–65；`candidate_A` 原样保留现有 fix 作为性能地板，其他候选必须测试不同机制假设。后续轮从 top-3 survivors 出发，固定同一组 debug seeds，并要求至少一个结构性新方向。来源：[evosearch subagent 第 180–208 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/evosearch/subagent-prompt.md#L180-L208)、[第 228–253 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/evosearch/subagent-prompt.md#L228-L253)。

**[O]** `task_analysis.md` 是跨轮持久状态：记录场景几何、假设、每轮 leaderboard、已经排除的方向、以及“因 workspace/arm constraint 暂时阻塞但未用新重配置测试”的方向。这个 ledger 防止无理由重试，同时允许在获得新技术后重新打开曾经 blocked 的分支。来源：[evosearch iteration 第 60–85 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/evosearch/skills/evosearch-iteration.md#L60-L85)、[第 133–196 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/evosearch/skills/evosearch-iteration.md#L133-L196)。

## 4. Agent harness：论文抽象与公开执行循环

### 4.1 Coordinator–actor 分工

**[F]** 论文的 coordinator 管理共享 skill 库并把每个任务交给一个 actor；actors 不互传完整 chat history 或原始 rollout，而把跨任务知识压缩进 skill 库，从而把单个 actor 上下文限制在当前任务、程序与相关 traces。来源：[Method](https://arxiv.org/html/2607.00272#S2)、[Figure 1](https://arxiv.org/html/2607.00272#S1.F1)。

**[O]** public Fix Loop 的 coordinator 维护显式状态机：

```text
pending --Stage 1 actor--> stage1-done --Stage 2 script--> done
```

`done` 任务禁止重发，以免重复 trial 目录污染聚合；一个 GPU 从 actor 开始到该任务 Stage 2 结束始终归属于同一任务。来源：[Fix Loop coordinator 第 10–20 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md#L10-L20)、[GPU ledger 第 24–49 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md#L24-L49)。

**[O]** coordinator 被明确禁止直接 debug 或读取 task-specific traces；它只 dispatch、运行 held-out eval、审计 findings、串行更新共享 skill。这个信息隔离减少 coordinator 上下文膨胀和并行 writer 冲突。来源：[coordinator rules 第 89–95 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md#L89-L95)。

### 4.2 Fix Loop actor

**[O]** 当前 LIBERO Fix Loop actor 的确定流程为：

1. 在 seed 51 只观察一次场景，读取共享 skills，写 `initial_code.py`；单场景推断必须标成可被后续 seed 推翻的 hypothesis。
2. 在 seeds 51–65 各运行一次初始程序。
3. 对失败 seed 读取 `trace.json`、`code.py`、`keyframes/`、`summary.txt`；按 gripper width、SAM mask count、IK、stderr 定位根因。
4. 每个失败 seed 最多 3 次 replay；仍失败则写 `BLOCKED.md`。
5. 综合跨 seed 证据，产出一个无 seed-specific branch 的 `fix_code.py`；若没有完全成功，依次按 development successes、crashes、更简单的 observation-driven 行为选择。
6. 写 `findings.md`，逐项列 root cause、哪些 seed 被修复、可迁移代码片段和证据；actor 不修改共享 skills。

来源：[Stage 0 第 72–98 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/subagent-prompt.md#L72-L98)、[Stage 1 第 102–187 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/subagent-prompt.md#L102-L187)、[findings 第 189–209 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/subagent-prompt.md#L189-L209)。

### 4.3 程序实际如何执行

**[O]** runtime 每个 trial 先按 seed reset，捕获初始视觉上下文，查询模型生成初始代码，然后逐 block 执行；可选 multi-turn 会根据 stdout/stderr/视觉差分决定 `REGENERATE` 或 `FINISH`，最后保存 code、responses、summary、trace 和视频。来源：[trial 主循环第 635–650 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L635-L650)、[执行/再生成第 791–879 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L791-L879)、[trace/video 保存第 923–947 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L923-L947)。

**[O]** 公开 LIBERO traced config 的 inner multi-turn prompt 直接回传 executed code、stdout 和 stderr，不把整个 `trace.json` 自动塞进同一模型回合；trace 在 trial 结束后落盘，由外层 coding actor 按需用文件工具读取。因此实际 harness 是“内层 code-block execution loop + 外层 repository-aware debugging loop”两层闭环。来源：[traced config 第 27–52 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/env_configs/libero/franka_libero_traced.yaml#L27-L52)、[Fix Loop trace diagnosis 第 102–146 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/subagent-prompt.md#L102-L146)。

**[O]** code blocks 共享一个 persistent globals namespace；runtime 把 `env`、APIs 和所有 helper functions 直接注入 globals，再使用 Python `exec`。因此它可以跨 block 保留变量，但生成代码拥有 full imports。来源：[executor 与 globals 第 64–88 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/tasks/base.py#L64-L88)、[执行路径第 159–211 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/tasks/base.py#L159-L211)。

**[F/O] 重要安全边界**：作者明确警告这不是安全 sandbox；trial isolation 和 watchdog 只是可靠性机制。生成代码有 full import access，应该在无凭证、无敏感挂载、限制网络的隔离主机上运行，不能给 simulation agent 接触物理硬件。来源：[仓库 README warning 第 105–108 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/README.md#L105-L108)、[simulation constitution 第 21–29 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/CLAUDE.md#L21-L29)。

**[O]** allowed/forbidden API 边界目前主要由 agent constitution/runbook 约束；执行路径仍把 low-level `env` 注入生成代码，且直接 `exec`，在该执行路径及测试中未见自动 AST/API denylist enforcement。因此，把官方文档中的 `sandbox_rc` 理解为“代码是否异常退出”更准确，而不是安全隔离证明。官方也明示禁止读取 MuJoCo ground truth 与 asset 文件，因为这种修复不能迁移到真实机器人。来源：[forbidden/allowed APIs 第 33–82 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/CLAUDE.md#L33-L82)、[`CodeExecutionEnvBase.step` 第 269–304 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/tasks/base.py#L269-L304)。

### 4.4 数据流与留痕

```text
任务语言 + API 文档 + 检索 skills + 初始图像
                  │
                  ▼
              actor 写程序 P
                  │
                  ▼
    seeded simulator / real robot execution
                  │
          ┌───────┴────────┐
          ▼                ▼
 task_completed/reward   primitive trace + keyframes + stderr + video
          │                │
          └───────┬────────┘
                  ▼
          failure attribution / repair
                  │
          debug-seed re-execution
                  │
        ┌─────────┴──────────┐
        ▼                    ▼
  final task program      structured findings
        │                    │
  held-out manifest       coordinator audit
                             │
                             ▼
                    versioned shared skill library
                             │
                             └──> future actors / zero-shot tasks
```

**[O]** 每个 trial 目录名编码 seed、`sandbox_rc`、reward、`task_completed`，目录内保存 final code、raw response、all responses、summary、prompts 和视觉反馈。来源：[artifact writer 第 386–484 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/utils/launch_utils.py#L386-L484)。

**[O]** Fix Loop 的 held-out runner 以 `(suite, task, code SHA-256, config SHA-256, exact seeds)` 计算 run identity；不同 identity 不混合，resume 只能继续同一 manifest。manifest 还保存 git commit、每 seed evidence path、exit code、reward、task completion，并原子更新。来源：[validation runner 第 1–10 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/run_fix_loop_validation.py#L1-L10)、[identity 第 32–62 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/run_fix_loop_validation.py#L32-L62)、[manifest 第 148–184 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/run_fix_loop_validation.py#L148-L184)、[per-seed update 第 193–258 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/run_fix_loop_validation.py#L193-L258)。

**[I]** 这一“程序/配置/种子哈希绑定 + 不可变 manifest + skill patch ledger”是 ASPIRE 最直接可迁移到 agent harness 的工程思想；它解决的是证据归属和并行知识写入问题，而不是机器人算法本身。

## 5. 训练目标、奖励与验证协议

### 5.1 它“训练”了什么

**[F]** simulation 使用冻结的 Claude Opus 4.6（1M context）作为 coding agent；没有更新 LLM 权重。real-robot study 使用 Codex GPT-5.5 reasoning-xhigh。来源：[Experimental Setup](https://arxiv.org/html/2607.00272#S3.SS1)、[官方 README 第 31 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/README.md#L31)。

**[F]** 被持续更新的对象是：任务级程序、failure-derived skills、程序搜索历史；不是 VLA policy、reward model 或 world model 参数。官方项目页也把 skill library 描述为可作为未来任务 in-context guidance 的 modular knowledge。来源：[官方项目页](https://research.nvidia.com/labs/gear/aspire/#method)。

### 5.2 奖励与成功判定

**[O]** `CodeExecutionEnvBase.step` 在执行代码后从 low-level env 取 `reward` 和 `task_completed`，并以 `reward == 1.0` 标记 Gym termination；LIBERO 的 `task_completed()` 调用 benchmark `check_success()`。来源：[task wrapper 第 269–304 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/tasks/base.py#L269-L304)、[LIBERO completion 第 533–535 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L533-L535)。

**[O]** `TrialSummary.success` 在通用 trial 代码里被设置为 `sandbox_rc == 0`，即“程序没有 crash”；论文实验 evaluator 的任务通过则统计 `task_completed`。两者不是同义词，任何移植都必须保持命名和指标隔离。来源：[trial 第 949–978 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L949-L978)、[evaluator 第 559–575 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/evosearch_eval.py#L559-L575)。

### 5.3 数据分割

| 场景 | 学习/调试 | 选择/验证 | 最终报告 | 依据 |
|---|---:|---:|---:|---|
| LIBERO-Pro Fix Loop | seeds 51–65 | — | seeds 1–50；每任务一个固定程序 | [Figure 4 / §3.3](https://arxiv.org/html/2607.00272#S3.F4)、[public Quick Start](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/QUICKSTART.md#L5-L11) |
| LIBERO-Pro + evolutionary selection | debug program；论文算法写 `S_dbg` | Appendix D 明确用 seeds 66–80 在 repaired program 与 evo program 间选 winner | seeds 1–50 | [Table 7](https://arxiv.org/html/2607.00272#A4.T7)、[Table 8](https://arxiv.org/html/2607.00272#A4.T8) |
| Robosuite | seeds 101–125 | 未另述 | seeds 1–100；每任务一个程序 | [Figure 4](https://arxiv.org/html/2607.00272#S3.F4) |
| BEHAVIOR-1K | seeds 26–35 | 未另述 | seeds 1–25；incremental block execution | [Figure 4](https://arxiv.org/html/2607.00272#S3.F4)、[§3.3](https://arxiv.org/html/2607.00272#S3.SS3) |
| LIBERO-Pro Long zero-shot | skill 来源为 LIBERO-90 的 N 个任务 | 无 task-specific debugging/retry/library update | held-out long-horizon tasks；每任务一个程序 | [§3.5](https://arxiv.org/html/2607.00272#S3.SS5)、[Table 5](https://arxiv.org/html/2607.00272#A3.T5) |

**[F]** CaP-Agent0 会为每个 evaluation seed 重新生成程序并使用 test-time reasoning/retries；ASPIRE 在 LIBERO-Pro/Robosuite 用一个生成程序跨所有 held-out seeds。这一比较协议对 ASPIRE 更严格，但也表示两者计算分配不同，不能把成功率差异解释为“等推理预算下”的结论。来源：[§3.3](https://arxiv.org/html/2607.00272#S3.SS3)。

**[F]** real-robot evaluation 的问题不是直接部署 simulation policy，而是把 simulation skill 作为 in-context guidance，仍由 real-robot coding agent 在不同 embodiment/API 上执行与调试；token 计到第一次成功，之后对生成程序做 20 次评估。来源：[§3.6](https://arxiv.org/html/2607.00272#S3.SS6)、[Table 1](https://arxiv.org/html/2607.00272#S3.T1)。

## 6. 实验结论：精确数字与不过度解释

### 6.1 LIBERO-Pro

下表是论文 Table 2 的宏平均成功率；每个 cell 跨 10 个任务，ASPIRE 每任务在 50 个 held-out seeds 上运行同一程序。

| 方法 | Object Pos | Object Task | Goal Pos | Goal Task | Spatial Pos | Spatial Task | Overall All |
|---|---:|---:|---:|---:|---:|---:|---:|
| OpenVLA | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| π0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| π0.5 | 0.17 | 0.01 | 0.38 | 0.00 | 0.20 | 0.01 | 0.13 |
| CaP-Agent0 | 0.22 | 0.18 | 0.26 | 0.17 | 0.12 | 0.14 | 0.18 |
| ASPIRE | **0.98** | **0.95** | **0.81** | **0.45** | **0.51** | **0.60** | **0.72** |

来源：[Table 2](https://arxiv.org/html/2607.00272#A2.T2)。

**[O]** 论文摘要的“up to 77%”从表内算术看是 Object 两轴平均 96.5% 对 CaP-Agent0 20.0%，即约 **+76.5 个百分点**；不是 77% 相对增长。Goal 与 Spatial 对最强基线分别约 +41.5、+42.5 个百分点，与正文陈述一致。来源：[§3.4](https://arxiv.org/html/2607.00272#S3.SS4)。

### 6.2 Robosuite

| 任务 | CaP-Agent0 | ASPIRE | 差值（百分点） |
|---|---:|---:|---:|
| cube_lift | 0.97 | 0.97 | 0 |
| cube_stack | 0.98 | 0.99 | +1 |
| cube_restack | 0.89 | 1.00 | +11 |
| spill_wipe | 1.00 | 0.99 | -1 |
| two_arm_handover | 0.20 | **0.92** | **+72** |
| two_arm_lift | 0.74 | 0.71 | -3 |
| nut_assembly | 0.00 | 0.09 | +9 |
| Mean | 0.68 | **0.81** | +13 |

来源：[Table 3](https://arxiv.org/html/2607.00272#A2.T3)。

**[F]** 最大提升集中于 bimanual handover；ASPIRE 并非每项都胜出，two-arm lift 和 spill wipe 略低于 CaP-Agent0。任何“全面支配”表述都不符合原表。

### 6.3 BEHAVIOR-1K

| 任务 | Human Nav/Task | CaP-Agent0 Nav/Task | ASPIRE Nav/Task |
|---|---:|---:|---:|
| Soda Can pickup | 0.80 / 0.72 | 0.84 / 0.72 | **0.92 / 0.88** |
| Radio pickup | 0.88 / 0.36 | 0.80 / 0.56 | **1.00 / 0.88** |

来源：[Table 4](https://arxiv.org/html/2607.00272#A2.T4)。最大 task-level 增益是 radio 对 CaP-Agent0 的 +32 个百分点。

### 6.4 Engine 与 evolutionary search 消融

**[F]** 论文将 LIBERO-Pro 宏平均从没有 engine/evo 的约 14% 提升到加入 execution engine 的约 62%，再由 evolutionary search/最终选择提升到约 72%；作者因此判断 dense execution engine 是平均贡献最大的组件，evo 主要补剩余 hard tasks。来源：[§3.7](https://arxiv.org/html/2607.00272#S3.SS7)、[Table 7](https://arxiv.org/html/2607.00272#A4.T7)、[Table 8](https://arxiv.org/html/2607.00272#A4.T8)。

**[O]** 这三个总数可由 Appendix D 两个 axis 的 overall mean 算出：base `(0.20+0.09)/2=0.145`，engine `(0.62+0.61)/2=0.615`，final `(0.77+0.67)/2=0.72`。

**[O]** 但 Appendix D 对中间项的精确定义是“Robot Execution Engine repaired program（Execution engine + skill library）”，而 base 是带 15 个示例程序的 zero-shot Claude；因此 14%→62% 是 trace-rich repair/skill package 的联合效应，不能从该消融单独识别“trace logger 本身”的因果增益。来源仍为 [Table 7 caption](https://arxiv.org/html/2607.00272#A4.T7) 与 [Table 8 caption](https://arxiv.org/html/2607.00272#A4.T8)。

**[F]** evo 并不逐轮单调；例如 Bowl→plate 在 Table 9 为 0.62、0.60、0.60、0.18、0.86，说明种群搜索需要保留历史 best，而不能把“最新候选”当“当前最优”。来源：[Table 9](https://arxiv.org/html/2607.00272#A4.T9)。

### 6.5 Skill library 的 zero-shot scaling

| LIBERO-90 来源任务数 N | Pos | Task | Overall |
|---:|---:|---:|---:|
| 0 | 0.000 | 0.094 | 0.047 |
| 25 | 0.056 | 0.218 | 0.137 |
| 50 | 0.138 | 0.292 | 0.215 |
| 90 | **0.226** | **0.383** | **0.305** |

对照：CaP-Agent0 overall 0.038，π0.5 为 0.05。来源：[Table 5](https://arxiv.org/html/2607.00272#A3.T5)。正文把 0.305 四舍五入为 31%。

**[F/O]** 聚合结果随库规模上升，但单任务并不单调。例如 Stove+moka 的 Task 轴在 N=50 为 0.68、N=90 降到 0.26；Mug on two plates 的 Task 轴从 N=0 的 0.16 最终降到 0.00。来源：[Table 6](https://arxiv.org/html/2607.00272#A3.T6)。这与作者关于 stale、specific、redundant 或 misleading entries 的限制相吻合，而不是“库越大，每个任务必然越好”。

### 6.6 跨 embodiment 的真实机器人迁移

| 任务 | Total tokens：无/有 skill (M) | Success：无/有 skill |
|---|---:|---:|
| Put bowl on plate | 8.65 / 5.11 | 20/20 / 20/20 |
| Lift soda can | 61.94 / 6.58 | 13/20 / 19/20 |
| Open/push drawer | 334.917 / 81.67 | 0/20 / 11/20 |

来源：[Table 1](https://arxiv.org/html/2607.00272#S3.T1)。

**[F]** bowl 任务主要降低调试成本而没有提高最终成功率；can 同时显著降 token 并提高成功；drawer 在无 skill 条件下没有找到成功程序，有 skill 后达到 11/20。论文准确措辞是“initial evidence”，样本只覆盖 3 个被选择的 skill/任务，不足以推出普遍 sim-to-real transfer。

## 7. 论文明确承认的限制

以下均为 **[F]**，来源：[论文 §5](https://arxiv.org/html/2607.00272#S5)、[官方项目页 Limitations](https://research.nvidia.com/labs/gear/aspire/#limitations)。

1. **未闭合真实世界 lifelong loop**：真实部署仍缺 robust success detection、safe reset、safety monitoring 与 calibration maintenance。
2. **依赖 frontier LLM**：simulation 使用冻结的 Claude Opus 4.6；未验证小/弱模型能否维持相同 debug loop。
3. **表达能力受 predefined primitive API 限制**：安全、可调试性更高，但 primitive 不包含的 sensing/control/interaction 无法自然表达。
4. **长期记忆管理未解决**：skill 会 stale、过度具体、重复或误导，需要 retrieval、pruning、ranking、re-validation。
5. **计算昂贵**：每任务需要大量 LLM calls 与 simulator/robot rollouts；扩大任务集需要更便宜推理或更高 sample efficiency。

## 8. 公开实现的复现审计

这些不是对论文作者动机的猜测，而是冻结提交上的 **[O] 可复现观察**。

### 8.1 强项

- 安装说明固定 suite-specific venv、submodule revisions、gated weights、感知服务、GPU topology，并要求 paper-scale launch 前做 preflight。来源：[simulation README](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/README.md)、[Quick Start 第 54–71 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/QUICKSTART.md#L54-L71)。
- 开发/held-out seed lockout 在 agent prompts、runner 参数和 immutable manifest 三处重复约束。
- trial 证据细到 primitive，最终评估又用 code/config/seed hash 绑定；共享 skill 更新有 serialized writer 和 exact patch ledger。
- evaluator 保存每个候选的 per-seed result、pass rate、mean reward、error count 与跨候选 leaderboard。来源：[evosearch evaluator 第 559–609 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/evosearch_eval.py#L559-L609)。

### 8.2 缺口与协议漂移

1. **原始论文结果不随 repo 发布。** `outputs/` 被 `.gitignore` 排除，冻结提交中没有 tracked trial manifests/traces/results；因此官方数字只能通过重新运行验证，不能从 release 内的 raw evidence 复算。来源：[`.gitignore` 第 32–36 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/.gitignore#L32-L36)、[冻结提交 tree](https://github.com/NVlabs/ASPIRE/tree/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282)。
2. **zero-shot build 的必要资产缺失。** runbook 称 `ordering.txt` 必须是已提交的 90-task ordering，并依赖 `snapshot-N5`…`snapshot-N90` tags；冻结提交 tree 中没有 `ordering.txt`，官方 remote 在检查时也没有这些 tags。来源：[build coordinator 第 8–23 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/zeroshot-transfer/main-agent-prompt.md#L8-L23)、[library-size instructions 第 1–8 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/library-size-scaling/INSTRUCTIONS.md#L1-L8)、[官方 tags 页面](https://github.com/NVlabs/ASPIRE/tags)。这阻断按原 runbook 直接复现 N-scaling，除非先明确补建 ordering/snapshots。
3. **模型版本并非所有 orchestration path 都强固定。** 论文与根 README 指 Opus 4.6 1M，但部分 subagent runbook 使用 `model="opus"` alias，并在 public release 文本中称当前解析为 Opus 4.7。来源：[paper setup](https://arxiv.org/html/2607.00272#S3.SS1)、[zero-shot subagent 第 6–10 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/zeroshot-transfer/subagent-prompt.md#L6-L10)。精确论文复现必须另外钉住 model/provider/context/reasoning 参数。
4. **arXiv v1 与 public Fix Loop 的 Stage 2 ownership 有漂移。** Appendix E.3 描述 task actor 完成 held-out Stage 2；当前 Fix Loop 明确禁止 actor 运行 1–50，由 coordinator script 执行。来源：[paper Appendix E.3](https://arxiv.org/html/2607.00272#A5.SS3)、[public instructions 第 6–8 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/INSTRUCTIONS.md#L6-L8)。这可能是 release 后的可靠性改进，但必须按版本记录，不能假定两个协议完全相同。
5. **evosearch stopping docs 内部不一致。** coordinator reference 包含“连续两轮 <5pp 即 plateau”，而 subagent prompt 明确要求不要因小增益提前停、只以 ≥80% 或 5 轮停止。来源：[coordinator 第 129–135 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/evosearch/main-agent-prompt.md#L129-L135)、[subagent 第 228–238 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/evosearch/subagent-prompt.md#L228-L238)。复现前必须选择一套并写入实验 receipt。
6. **安全约束不是技术 sandbox。** 这是官方明确警告，也是执行源码事实；不可把 prompt-level forbidden API policy 当成对恶意/误生成代码的强制隔离。
7. **统计呈现以 point estimates 为主。** 论文表格给确定 seed 集上的成功率，但未为主要数字报告置信区间，也未说明对 coding-agent 随机性做多少独立重复。本文因此不推断显著性或方差。

## 9. 对 agent harness 的可检验迁移假设（不是结论）

下列均为 **[I]**，应由本项目的受控实验裁决：

1. **Trace-granularity hypothesis**：若当前失败反馈仅到 scene/episode 级，把证据提升到 primitive 调用级应减少错误修复和达到首个通过程序所需的迭代数。最小消融是 `coarse outcome` 对 `outcome + structured primitive traces`，固定 agent、任务、预算与种子。
2. **Validated-repair memory hypothesis**：只有同时记录 failure signature、applicability guard、repair、跨 seed 证据与 negative cases 的 skill，才有机会跨任务提高 sample efficiency；仅保存成功脚本可能造成更强负迁移。
3. **Serialized-admission hypothesis**：并行 actors 负责产出 findings、单 writer 负责 library admission，可在不共享完整历史的前提下降低写冲突和上下文成本；promotion patch ledger 应成为可回滚/可审计的知识 checkpoint。
4. **Evidence-binding hypothesis**：对 resolved task/program、sim config、seed set、validator version 和 runtime evidence 建立统一内容哈希，可防止“程序变了但沿用旧成功证据”。ASPIRE 的 held-out manifest 已验证这类工程模式可落地。
5. **Diverse-program-search hypothesis**：当失败能被可靠归因且 validator 可信时，K 个机制不同的候选优于对单一路线连续微调；若 validator 有 false positive，evolutionary search 反而会放大 reward hacking。因此必须先强化验证器，再扩大搜索。
6. **Memory-scaling caveat**：总库规模不是单调收益变量。检索质量、冲突解析、失效重验证和 negative skill 同样重要；应同时报告 aggregate transfer、per-task regressions 和 retrieval precision。
7. **Do-not-copy hypothesis**：ASPIRE 的 full-import in-process executor 不应原样移植到面向不可信 agent 的 harness；应保留 trace/provenance 思想，同时使用真正的进程/容器隔离、资源限额、网络/文件权限与强制 API capability boundary。

**[U]** ASPIRE 官方材料没有证明它能直接提升“仿真环境生成”的物理正确性、场景编译器的 deterministic validity、support/containment contract 或 world-model 参数学习。它直接支持的结论是：在既有 simulator、task success checker 和 predefined robot APIs 之上，agentic debugging、程序搜索和经验库能提高机器人程序鲁棒性。能否帮助本项目的 robust environment generation，必须把“生成器/validator failure trace → repair skill → held-out scene success”具体化后再做本地实验。

## 10. 本次研究操作留痕

| 顺序 | 决策/动作 | 结果与边界 |
|---:|---|---|
| 1 | 只接受论文、NVIDIA 项目页、NVlabs repo | 未引入博客、媒体、社区复述或其他二手结论。 |
| 2 | 固定论文 v1 和源码 public-release commit | 所有源码链接使用 commit permalink，避免 `main` 漂移。 |
| 3 | 下载论文 PDF/HTML并逐节核对 | 阅读主文、Algorithm 1、Tables 1–9、Appendices A/E；数字按表原样抄录并做可复算算术。 |
| 4 | shallow clone 官方 repo 到临时目录作静态审计 | 未在本项目创建分支或 commit；未改官方 clone。 |
| 5 | 沿 execution path 阅读 | 覆盖 code execution、trace logger、trial artifacts、Fix Loop、evosearch、held-out manifest、skill promotion ledger。 |
| 6 | 检查可复现资产 | 官方 release commit 为 `7ba73d3...`；tracked tree 无 paper raw outputs、`ordering.txt`、snapshot tags。 |
| 7 | 分离证据等级 | 论文结果标 [F]；源码行为/缺失资产标 [O]；对本项目价值只写 [I] 可检验假设；没有把未运行结果写成复现成功。 |

## 11. 官方来源索引

- [论文摘要页（版本、作者、提交历史）](https://arxiv.org/abs/2607.00272)
- [论文 HTML（可定位章节、图、表、附录）](https://arxiv.org/html/2607.00272)
- [论文 PDF](https://arxiv.org/pdf/2607.00272)
- [NVIDIA GEAR ASPIRE 官方项目页](https://research.nvidia.com/labs/gear/aspire/)
- [NVlabs/ASPIRE 官方仓库](https://github.com/NVlabs/ASPIRE)
- [本研究固定的 public-release commit](https://github.com/NVlabs/ASPIRE/commit/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282)
