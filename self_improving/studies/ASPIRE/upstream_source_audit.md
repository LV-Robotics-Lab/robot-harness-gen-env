# ASPIRE 上游 agent harness 源码审计

## 0. 审计元数据与结论标签

- 审计对象：`external/ASPIRE`，官方上游提交
  `7ba73d3bcac8f6b6d4a7d67ed4040988f768d282`。
- 审计日期：2026-08-31。
- 审计方式：静态源码与 runbook 取证；未安装依赖、未初始化子模块、未启动服务、
  未运行仿真或 real-robot、未执行上游测试。
- 关注范围：agent harness，而非论文主张复述。重点检查 tracing、生成代码执行、
  timeout/watchdog、安全边界、coordinator/actor 状态、数据划分、skill promotion、
  evolutionary search、测试，以及对本项目的最小可移植面。
- 遵守的上游约束：上游明确声明生成代码有完整 import 权限，trial/watchdog 不是安全
  沙箱（`external/ASPIRE/CLAUDE.md:34-41`；`external/ASPIRE/README.md:105-108`；
  `external/ASPIRE/aspire/sim/CLAUDE.md:21-29`）。本审计不把 `sandbox_rc` 误解为安全隔离。

本文只使用三类结论：

- **[源码事实]**：可直接由所列文件和行号确认。
- **[源码推断]**：由多处可见控制流推出，但本次没有运行实验验证；不得当成已测事实。
- **[未实现]**：在该提交的完整相关源码、配置、runbook 和测试静态检索中没有找到对应的
  运行时机制；不等于作者永远没有其他私有实现。

复核本审计所用的主要静态检索包括：

```bash
git -C external/ASPIRE rev-parse HEAD
rg -n "TraceLogger|trace_logger|SkillLibrary|extract_functions|evolve_skill_library" external/ASPIRE
rg -n "result_watchdog|_run_single_trial_with_timeout|evosearch_eval" external/ASPIRE/aspire/sim
rg -n "campaign-state|frozen-manifest" external/ASPIRE/aspire/sim
rg -n "TraceLogger|SkillLibrary|evosearch|watchdog|timeout" external/ASPIRE/aspire/sim/tests
```

## 1. 执行摘要

从第一性原理看，一个可信的 self-improving harness 至少要把下列闭环变成可执行且可审计的
状态转换：

```text
候选身份固定
  -> 在明确角色的数据分区上执行
  -> 产生完整、哈希绑定的观测与物理证据
  -> 用预先固定的门控判定
  -> 只从开发证据提炼可复用技能
  -> 对技能做资格验证、原子发布和可验证回滚
  -> 在从未反馈的 held-out 上最终测量
```

ASPIRE 在这个闭环中提供了几块有价值、但成熟度差异很大的实现：

| 部分 | 本提交中的真实形态 | 审计结论 |
|---|---|---|
| API trace + keyframes | 可执行 Python instrumentation | 有诊断价值；证据完整性、哈希绑定和失败可见性不足 |
| LIBERO held-out validation | 可执行、不可变、可续跑的 manifest | 最值得移植的实现模式 |
| 并行评测 | 可执行 worker pool + 软/硬 watchdog | 可借鉴进程隔离思想；当前超时语义存在控制流风险 |
| Fix Loop 状态 | 少量磁盘状态分类器 + 大量 Markdown 协议 | 部分机械化，不是自主 coordinator runtime |
| BEHAVIOR campaign | Markdown 状态表和 runbook | 协议设计较严谨，但几乎完全依赖代理自律 |
| Skill promotion ledger | 可执行 begin/finish/verify | 有审计骨架；没有真正的 integrity verify 和 rollback |
| 自动 `SkillLibrary` | 正则抽取 + JSON + `exec` 注入 | 与论文 runbook skill library 是两套不同机制，且未闭环接入 |
| “Evolutionary Search” | LLM/代理写候选；Python 只批量评测和按 pass rate 排序 | 实际是 prompt-driven 多假设搜索，不是代码化进化算法 |
| 安全边界 | 完整 Python `exec`，raw env 可达 | 官方也明确不安全；不能移植到本项目可信执行面 |

**总判断：**ASPIRE 能提升本项目的地方主要是 harness 的实验组织方法，而不是直接复制其
机器人执行栈。最小有价值迁移是：

1. 将 `run_fix_loop_validation.py` 的“内容哈希身份 + 原子 manifest + 精确续跑 + 分区
   allowlist”模式移植到本项目的 `self_improving/` 控制层；
2. 以 `TraceLogger` 的“调用级因果线索 + 关键帧”思想设计项目原生的、流式且哈希绑定的
   typed trace；
3. 以 promotion ledger 的 before/after snapshot 和 patch 为起点，补上强校验、锁、资格门控和
   rollback；
4. 将多候选、同一 development 集合、公平比较的实验结构用于 robust 仿真环境生成，但继续
   由本项目现有物理 validator 决定是否可发布。

本次静态审计**没有实证证明**上述迁移会提高 robust 环境生成成功率或 self-improvement
泛化率；它只确定了可检验的实现假设和上游真实边界。

## 2. Harness 的真实分层

### 2.1 可执行层

- **[源码事实]** `CodeExecutionEnvBase` 把低层环境和 API 实例化后，直接执行模型生成的
  Python；API 文档来自 `ApiBase.functions()` 暴露函数的签名/docstring
  （`external/ASPIRE/aspire/sim/cap/integrations/base_api.py:19-27`、
  `external/ASPIRE/aspire/sim/cap/integrations/base_api.py:98-127`；
  `external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:97-131`、
  `external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:145-180`）。
- **[源码事实]** Trial runner、parallel evaluator、trace logger、LIBERO validation manifest、
  progress generator 和 skill promotion recorder 都是 Python 可执行部件。
- **[源码事实]** `gen_progress.py` 自己说明状态取自磁盘 artifact 而非 agent memory
  （`external/ASPIRE/aspire/sim/scripts/libero/gen_progress.py:5-13`）。

### 2.2 Prompt/runbook 控制层

- **[源码事实]** LIBERO coordinator 的 GPU 分配、actor dispatch、完成通知、何时 idle、
  何时更新 skills 等规则写在 Markdown 中
  （`external/ASPIRE/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md:24-49`、
  `external/ASPIRE/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md:73-95`）。
- **[源码事实]** Evolutionary Search 的候选产生、top-3 survivor、K=8 多样性和迭代终止也写在
  actor prompt 中，而不是 evaluator 中
  （`external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:180-208`、
  `external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:228-253`）。
- **[源码推断]** 因而“ASPIRE agent harness”不能只按 Python package 理解；论文复现实验的
  实际控制器是“Claude/agent 平台行为 + Markdown runbook + 若干 Python 工具”的组合。
- **[未实现]** 本提交没有一个统一的、类型化的 coordinator service 来持久化 GPU ownership、
  actor lease、通知、预算、候选谱系、阶段转换和信息防火墙。

## 3. TraceLogger 与 keyframes

### 3.1 它实际记录什么

- **[源码事实]** `TracedApiMixin.functions()` 只包装底层 `ApiBase.functions()` 返回的公开函数；
  wrapper 在 `finally` 中记录函数名、参数摘要、结果/错误和耗时
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:478-516`）。LIBERO 和
  Robosuite 分别通过很薄的 traced subclass 接入
  （`external/ASPIRE/aspire/sim/cap/integrations/libero_trace_logger.py:19-27`；
  `external/ASPIRE/aspire/sim/cap/integrations/robosuite_trace_logger.py:15-23`）。
- **[源码事实]** 每条 entry 含递增 `step`、相对时间、function、参数摘要、耗时和 result/error；
  logger 没有在 entry 中加入 episode id、seed、代码 hash、config hash、reward 或 success
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:318-340`）。
- **[源码事实]** 大 ndarray 不写内容，只写 shape/dtype；小数组和 numpy scalar 转成 JSON
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:34-50`）。
- **[源码事实]** 参数摘要是按函数名定制的：观测不记参数，SAM 记 prompt/shape，IK 记目标
  pose，motion 记 joints；未匹配函数会尝试保存所有可 JSON 化的小参数
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:53-159`）。
- **[源码事实]** 结果摘要保留观测 shape、robot joint/cartesian state、SAM top-3 mask 的
  score/bbox/area、GraspNet 最佳抓取与 IK joints；`move_to_joints` 和 gripper 调用只标记
  `completed=True`
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:162-263`）。
- **[源码事实]** 对 gripper action，logger 会额外读取环境观测以推断实际 gripper width，
  读取失败被静默忽略
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:341-365`）。

### 3.2 keyframe 策略

- **[源码事实]** `get_env_observation` 会缓存 RGB JPEG 和 depth NPY
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:367-379`）。
- **[源码事实]** `get_observation` 仅检查固定相机名 `agentview`、`robot0_robotview`、
  `robot0_eye_in_hand`，并缓存 RGB、depth、intrinsics、extrinsics
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:380-402`）。
- **[源码事实]** SAM 调用缓存 overlay 和 top mask；`plan_grasp` 缓存全部 poses/scores NPZ
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:404-426`）。
- **[源码事实]** 所有图片/数组先驻留内存，trial 结束时才写盘；单个 keyframe/array 保存异常被
  无提示吞掉，而 `trace.json` 仍可含 `keyframe_saved=True`
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:297-308`、
  `external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:428-461`）。
- **[源码事实]** 普通 trial 在主要 artifact 保存后才调用 trace `save()`/`reset()`
  （`external/ASPIRE/aspire/sim/cap/envs/trial.py:923-939`）。replay 还另外保存最多十张均匀采样
  的 video frames
  （`external/ASPIRE/aspire/sim/scripts/libero/replay_trial.py:546-588`）。
- **[源码事实]** bulk evosearch 为速度显式清空图片和数组 buffer，只保存 JSON trace
  （`external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:184-191`）。

### 3.3 诊断价值与证据边界

- **[源码事实]** `analyze_evosearch_traces.py` 从 trace 提取 gripper、IK、segmentation、运动和
  blocking 线索，并按成功/失败分组
  （`external/ASPIRE/aspire/sim/scripts/libero/analyze_evosearch_traces.py:23-143`、
  `external/ASPIRE/aspire/sim/scripts/libero/analyze_evosearch_traces.py:146-233`）。
- **[源码推断]** 这种“失败阶段 + 参数 + 观测”的因果局部化比只看首尾截图更适合 agent
  自诊断；它是 ASPIRE 对本项目最有启发的部分之一。
- **[源码推断]** 由于 logger 无界缓冲 RGB/depth/masks/grasps，长 episode 或高频
  `get_observation()` 会令内存随调用数增长；本次未运行内存基准。
- **[源码推断]** timeout artifact builder 没有调用 trace save，而正常保存发生在 trial 尾部；
  因此在该异常路径成功中断时，已缓冲 trace/keyframes 可能丢失
  （`external/ASPIRE/aspire/sim/cap/envs/runner.py:347-418` 对照
  `external/ASPIRE/aspire/sim/cap/envs/trial.py:932-939`）。
- **[未实现]** 没有 trace schema/version、entry/artifact checksum manifest、写盘确认、buffer
  上限/背压、敏感值 redaction policy，也没有把 trace/keyframe/video 绑定到生成代码、seed、
  invocation、resolved scene 或 validation result。
- **[未实现]** video 路径只报告 frame 总数并取十张等距帧，没有报告 unique frame 数，也没有
  对视频或帧做 hash manifest
  （`external/ASPIRE/aspire/sim/scripts/libero/replay_trial.py:567-588`）。

**对本项目的含义：**只移植“在明确 stage 边界记录输入摘要、结果摘要、耗时、错误和关键证据
引用”的思想；不要复制内存 buffer、静默失败和无 hash 的文件布局。对 robust 环境生成，trace
的 stage 应是 parse、grounding、solve、build、static validate、runtime replay、physical gate，
而不是 ASPIRE 特定的 SAM/IK/gripper 函数。

## 4. 生成代码执行、timeout/watchdog 与安全边界

### 4.1 实际执行语义

- **[源码事实]** `SimpleExecutor` 的 docstring 和实现都明确允许完整 import，并把低层 `env`、
  全部 `APIS`、`INPUTS`、`RESULT` 放进 `exec` globals
  （`external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:64-88`）。
- **[源码事实]** `CodeExecutionEnvBase` 虽然构造了 `SimpleExecutor`，但 `step()` 并不调用它，
  而是直接调用自己的 `_exec_user_code()`；因此实际 trial 的控制流应以后者为准
  （`external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:97-108`、
  `external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:269-276`）。
- **[源码事实]** 实际 `CodeExecutionEnvBase.step()` 走 `_exec_user_code()`；它使用 episode 内持久
  namespace，注入 raw `low_level_env`、API object 和所有 API helper，再直接 `exec`
  （`external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:129-131`、
  `external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:159-191`、
  `external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:269-304`）。reset 才重建 namespace，避免跨
  episode 变量泄漏
  （`external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:193-211`、
  `external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:246-267`）。
- **[源码事实]** `_exec_user_code()` 捕获 `BaseException`，把 traceback 写入 stderr，并以
  `sandbox_rc=1` 返回，而不是把异常继续抛给 runner
  （`external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:170-191`、
  `external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:292-304`）。
- **[源码事实]** prompt 明列 forbidden ground-truth API，并解释其无 real-world transfer
  （`external/ASPIRE/aspire/sim/CLAUDE.md:33-57`）；但运行时同时把 raw env 放进 namespace。

**边界结论：**

- **[未实现]** 没有 AST 校验、import allowlist、builtins 收缩、文件系统/网络隔离、syscall
  sandbox、内存/CPU quota，或对 forbidden API 的动态拦截。
- **[源码推断]** “只使用 allowed API”是 benchmark policy，不是 capability security；生成代码
  能沿 raw `env`/`APIS[*]._env` 或 Python import 越过这条线。上游文档对此安全风险的表述是
  准确的，不应把问题描述成上游疏忽。
- **[源码推断]** Trace wrapper 只覆盖 `ApiBase.functions()`，因此生成代码直接使用 raw env、
  import、文件系统或网络的行为不会形成对应 API trace；trace 不是完整的执行审计日志
  （`external/ASPIRE/aspire/sim/cap/integrations/trace_logger.py:499-516` 对照
  `external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:159-180`）。
- **[源码推断]** 通用参数摘要会记录任何可 JSON 化的小值；如果未来把 token/secret 误传入
  public API，它可能进入 trace。上游的正确运行前提是 worker 环境本身没有敏感信息。

### 4.2 软 timeout

- **[源码事实]** runner 设置每次 trial 1000 秒、最多两次尝试；只有返回的
  `TrialSummary.timed_out` 为真才重试
  （`external/ASPIRE/aspire/sim/cap/envs/runner.py:45-50`、
  `external/ASPIRE/aspire/sim/cap/envs/runner.py:206-234`）。
- **[源码事实]** soft watchdog 是 daemon thread：deadline 后设置 event，再调用
  `_thread.interrupt_main()`；外层只有捕获到 `KeyboardInterrupt` 才构造 `timed_out=True` artifact
  （`external/ASPIRE/aspire/sim/cap/envs/runner.py:276-344`）。文档也承认 C extension 中断会延迟
  （`external/ASPIRE/aspire/sim/cap/envs/runner.py:293-297`）。
- **[源码推断]（高置信）** 若 watchdog 恰在生成 Python 的 `exec` 内触发，
  `KeyboardInterrupt` 会先被 `_exec_user_code()` 的 `except BaseException` 吞掉并转为普通
  `sandbox_rc=1`；外层 runner 因此可能看不到 `KeyboardInterrupt`，不会标为 timed out，也不会
  触发 timeout retry。依据是
  `external/ASPIRE/aspire/sim/cap/envs/tasks/base.py:175-191` 与
  `external/ASPIRE/aspire/sim/cap/envs/runner.py:303-326` 的嵌套控制流。本次没有用最小程序动态
  复核，故不标为已证实 bug。
- **[源码事实]** `_run_single_trial()` 还会取消并有条件重启一个 1000 秒 SIGALRM，但 runner
  当前 soft watchdog 本身用 thread，不用 alarm；这段看起来是另一套 timeout 的遗留耦合
  （`external/ASPIRE/aspire/sim/cap/envs/trial.py:657-665`）。

### 4.3 硬 watchdog 与其他悬挂面

- **[源码事实]** 只有 `num_workers > 1` 的 headless 路径启用 parent result watchdog，超时值为
  3000 秒
  （`external/ASPIRE/aspire/sim/cap/envs/runner.py:151-168`）。
- **[源码事实]** parent 若长时间收不到任何 result，会 kill 所有仍存活 worker，并把所有未
  accounted trial 记到本地 `errors`；函数最后只返回成功 `results`
  （`external/ASPIRE/aspire/sim/cap/utils/parallel_eval.py:293-344`）。
- **[源码推断]** 因 missing trial 不被转成 `TrialSummary`，headless caller 仍对部分
  `summaries` 写 summary 和 `aaa_done_flag`，这个 flag 不能证明所有请求 trial 均有结果
  （`external/ASPIRE/aspire/sim/cap/envs/runner.py:170-176`）。
- **[未实现]** 单 worker/sequential 路径没有独立进程 kill backstop；它复用一个 env 逐个执行
  （`external/ASPIRE/aspire/sim/cap/envs/runner.py:237-269`）。
- **[源码事实]** evosearch worker 用 180 秒 SIGALRM 抛 `TimeoutError`
  （`external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:121-170`）。
- **[源码推断]（高置信）** 该 `TimeoutError` 若发生在同一个 `_exec_user_code()` 中，也可能先被
  `except BaseException` 转为 `sandbox_rc=1`，使 evosearch 外层 `except TimeoutError` 不生效。
- **[未实现]** evosearch multiprocessing pool 没有 parent result watchdog；highlight 和 rare
  success replay 的 `subprocess.run()` 没有 timeout
  （`external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:262-280`、
  `external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:405-418`）。
- **[未实现]** Fix Loop held-out runner 的 per-seed `subprocess.run()` 也没有 timeout
  （`external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:199-218`）。

**对本项目的含义：**不能移植 in-process raw `exec`。若项目将来需要执行 agent 生成代码，
最小可信边界应是：每次 attempt 独立 OS/container sandbox、无 secret/敏感 mount、默认无网络、
结构化 tool RPC、父进程 wall-clock/memory/CPU hard limit、超时结果也必须写完整 terminal
artifact。软中断只能作为体验优化，不能作为最终 containment。

## 5. Coordinator / actor 状态机

### 5.1 LIBERO Fix Loop：部分机械化

- **[源码事实]** runbook 定义 `pending -> stage1-done -> done`，其中 `fix_code.py` 存在即
  `stage1-done`，50 个 held-out result 即 `done`
  （`external/ASPIRE/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md:10-20`）。
- **[源码事实]** 机器实现 `get_status()` 确实只检查 fix code 是否存在和 trials 是否达到 50
  （`external/ASPIRE/aspire/sim/scripts/libero/gen_progress.py:157-160`）。
- **[源码事实]** 新 validation manifest 路径会按 code/config hash 过滤，并优先完整 1–50 run；
  进度文件以临时文件 rename 原子替换
  （`external/ASPIRE/aspire/sim/scripts/libero/gen_progress.py:116-154`、
  `external/ASPIRE/aspire/sim/scripts/libero/gen_progress.py:233-267`、
  `external/ASPIRE/aspire/sim/scripts/libero/gen_progress.py:304-314`）。
- **[源码事实]** actor 被 prompt 限定为 Stage 0/1、debug seeds 51–65、不得读 held-out、不得编辑
  shared skills；最多三次 replay/failed seed 也是 prompt 规则
  （`external/ASPIRE/aspire/sim/.claude/libero/fix-loop/subagent-prompt.md:22-39`、
  `external/ASPIRE/aspire/sim/.claude/libero/fix-loop/subagent-prompt.md:102-177`）。
- **[源码事实]** coordinator 在 actor 完成后先启动 Stage 2，再从 `findings.md` 更新 skills，
  eval 完成后做 promotion verify
  （`external/ASPIRE/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md:134-177`）。

边界：

- **[未实现]** GPU ledger 只是 coordinator “working notes”，没有持久化锁或 lease
  （`external/ASPIRE/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md:24-49`）。
- **[未实现]** actor dispatch、自动通知、in-flight eval 去重、三次 replay budget、必须写
  `findings.md`、promotion 后才释放 GPU，均未进入 Python state machine；它们依赖 agent 平台
  和 prompt compliance。
- **[源码推断]** `fix_code.py` 的单纯存在会推进到 `stage1-done`，即使 findings、actor summary、
  debug partition 完整性或 promotion 尚未满足；状态分类器不是 workflow invariant verifier。

### 5.2 BEHAVIOR：协议严谨，但不是可执行状态机

- **[源码事实]** BEHAVIOR runbook 明确规定 Stage 1 seeds 26–35、freeze、Stage 2 seeds 1–25、
  每个 held-out seed 新 context/空 policy、禁止跨 seed 信息流
  （`external/ASPIRE/aspire/sim/.claude/behavior/fix-loop/INSTRUCTIONS.md:25-40`、
  `external/ASPIRE/aspire/sim/.claude/behavior/fix-loop/INSTRUCTIONS.md:98-135`）。
- **[源码事实]** freeze 要复制 read-only skill library，并 SHA-256 skills/config/API/prompt；变更
  必须新 campaign
  （`external/ASPIRE/aspire/sim/.claude/behavior/fix-loop/INSTRUCTIONS.md:107-116`）。
- **[源码事实]** `campaign-state-template.md` 是人/agent 更新的 Markdown 状态表
  （`external/ASPIRE/aspire/sim/.claude/behavior/fix-loop/campaign-state-template.md:1-44`）。
- **[未实现]** 全树静态检索没有发现 Python/shell 读取该 campaign state、验证
  `frozen-manifest.sha256`、创建 fresh agent context 或执行跨 seed information firewall。这里的
  freeze、hash verification 和状态转换是 runbook 约定，不是 runtime enforcement。

## 6. Debug / validation / held-out 数据划分

### 6.1 Fix Loop 中真正机械化的部分

- **[源码事实]** held-out runner 的 identity 包含 suite、task、code SHA-256、config SHA-256 和
  去重排序后的 seeds；run id 是该 identity 的 hash
  （`external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:32-58`）。
- **[源码事实]** CLI 默认 seeds 1–50，运行时拒绝任何不在 1..50 的 seed
  （`external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:65-75`、
  `external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:132-150`）。
- **[源码事实]** run directory 由 identity 决定；已有 manifest 只有 identity 完全一致且显式
  `--resume` 才可继续；manifest 和每个 seed 更新都原子写入
  （`external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:151-184`、
  `external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:186-244`）。
- **[未实现]** identity 不含 dependency/simulator version、git commit、GPU、runner version、
  model/prompt 或 timeout policy；git commit 只是 manifest metadata，不参与 `run_id`
  （`external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:46-58`、
  `external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:166-183`）。
- **[源码事实]** 只有完整、已完成的 1–50 partition 才写 `validation_result.json` 并升级 Stage 1
  validation 状态
  （`external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:61-63`、
  `external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:246-250`）。
- **[源码事实]** 单元测试覆盖 code/config identity 改变、manifest 选择、完整 partition 检查和
  50-result 状态门槛
  （`external/ASPIRE/aspire/sim/tests/test_libero_fix_loop_pipeline.py:24-48`、
  `external/ASPIRE/aspire/sim/tests/test_libero_fix_loop_pipeline.py:51-104`）。

局限：

- **[源码事实]** 通用 `replay_trial.py` 的 `trial` 是任意整数，没有 development/held-out role
  参数或 partition guard
  （`external/ASPIRE/aspire/sim/scripts/libero/replay_trial.py:69-117`）。
- **[源码事实]** held-out runner 即使 subprocess exit 非零，只要能找到最新同 seed artifact，
  仍将它写入 manifest；artifact identity 只从目录名解析，没有验证该目录内 code/config hash
  （`external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:85-102`、
  `external/ASPIRE/aspire/sim/scripts/libero/run_fix_loop_validation.py:199-228`）。
- **[源码推断]** 因此该脚本强约束“它自己启动的请求 seeds”，但不能阻止 actor 直接调用 replay
  偷看 held-out，也不能完全证明被发现 artifact 正是本次 subprocess 产生。

### 6.2 Evolutionary Search 的分区不一致

- **[源码事实]** evosearch actor prompt 把 51–65 定为 debug、1–50 定为 held-out，并明确要求
  迭代期间 lockout 1–50
  （`external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:29-37`、
  `external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:196-208`）。
- **[源码事实]** evaluator 接受任意 `--trial-seeds`；未提供时从 1..10000 随机抽样，没有
  `development|validation|heldout` role 或 seed range enforcement
  （`external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:435-464`、
  `external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:511-520`）。
- **[源码事实]** runbook 让同一个 task subagent 选完 debug winner 后直接运行 1–50 并读回结果
  （`external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:41-47`、
  `external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:257-310`；
  `external/ASPIRE/aspire/sim/.claude/libero/evosearch/main-agent-prompt.md:54-72`）。
- **[源码事实]** 同一 experiment 的 `INSTRUCTIONS.md` 又要求运行 validation selection；对应
  `run_validation_comparison.py` 在 seeds 66–80 比较 fix 与 evosearch code
  （`external/ASPIRE/aspire/sim/.claude/libero/evosearch/INSTRUCTIONS.md:8-10`；
  `external/ASPIRE/aspire/sim/scripts/libero/run_validation_comparison.py:5-10`、
  `external/ASPIRE/aspire/sim/scripts/libero/run_validation_comparison.py:26-32`、
  `external/ASPIRE/aspire/sim/scripts/libero/run_validation_comparison.py:125-170`）。
- **[源码推断]** 这构成协议顺序歧义：若 66–80 是选择集，它应在 1–50 最终测量之前；当前
  actor runbook 已经先运行并观察 1–50。代码没有防止利用 held-out 结果再做后续选择。
- **[未实现]** 没有一个全局 split registry/seed lease，确保一条样本在整个 campaign 中只扮演
  一个角色，或阻止 held-out 反馈进入候选/skill 更新。

**对本项目的含义：**必须把 split role 作为 typed run identity 的字段，并在低层 replay 入口
做 allowlist intersection，而不是只在 runbook 中写 seed 范围。final held-out 应只运行一次，
其详细结果不能再流回 optimizer 或 promotion。

## 7. Skill promotion ledger、rollback 与完整性

### 7.1 已实现的审计骨架

- **[源码事实]** `begin` 会验证 suite/task 名字、禁止已有 completed record、禁止同 suite 另一
  pending promotion，计算 skill files 和整个 library hash，并完整复制 before snapshot
  （`external/ASPIRE/aspire/sim/scripts/libero/record_skill_promotion.py:53-85`、
  `external/ASPIRE/aspire/sim/scripts/libero/record_skill_promotion.py:171-215`）。
- **[源码事实]** begin record 还捕获对应 `findings.md` 的路径和当时 hash
  （`external/ASPIRE/aspire/sim/scripts/libero/record_skill_promotion.py:201-213`）。
- **[源码事实]** `finish` 比较 before/after hashes，要求有变化或显式 no-op reason，写精确 unified
  patch、after hashes 和原子 `record.json`，再 append JSONL ledger
  （`external/ASPIRE/aspire/sim/scripts/libero/record_skill_promotion.py:218-292`）。
- **[源码事实]** ledger append 会 flush + fsync，并按 `promotion_id` 幂等
  （`external/ASPIRE/aspire/sim/scripts/libero/record_skill_promotion.py:159-168`）。
- **[源码事实]** 测试覆盖精确 patch/hash、幂等 ledger、串行 pending、no-op reason 和 finish 前
  verify 失败
  （`external/ASPIRE/aspire/sim/tests/test_record_skill_promotion.py:44-112`）。

### 7.2 `verify` 实际没有验证什么

- **[源码事实]** `verify_promotion()` 只查找 completed `record.json`，然后确保同一 record 被 append
  到 ledger；它不重新计算任何 hash
  （`external/ASPIRE/aspire/sim/scripts/libero/record_skill_promotion.py:295-303`）。
- **[未实现]** verify 没有检查：当前 library 是否仍等于 `library_after_sha256`、before snapshot
  是否等于 before hashes、patch 能否重放、findings 是否仍等于所记 hash、record 是否与 ledger
  byte-for-byte 一致、sequence 是否连续。
- **[未实现]** JSONL 没有 previous-entry hash/hash chain、签名或外部 immutable anchor；“append-only”
  是写入方式，不是抗删改属性。
- **[未实现]** 没有 file lock/transaction lock；“只能一个 pending promotion”是先读后写，两个并发
  begin 可能竞态取得同一 sequence/promotion path。
- **[未实现]** 没有 promotion 资格测试、回归 suite、forbidden API 静态/动态检查、development
  evidence completeness 或 held-out contamination gate。

### 7.3 rollback

- **[源码事实]** before snapshot 与 exact patch 为人工恢复提供了材料。
- **[未实现]** CLI 只有 `begin`、`finish`、`verify`，没有 `rollback`/`revert` 命令、逆向 patch
  校验、rollback record 或把 library 原子恢复到某个已验证版本
  （`external/ASPIRE/aspire/sim/scripts/libero/record_skill_promotion.py:306-351`）。
- **[源码推断]** 因而这是一套 provenance recorder，不是完整的 transactional skill registry。

**对本项目的含义：**可复用 snapshot + patch + ledger 概念，但发布必须接到本项目已有
`SkillQualification` 和 implementation hash，而不是以 Markdown 文件变化本身作为成功。
本项目已经有 qualification artifact 和 implementation SHA-256
（`self_improving/harness/schemas/common.py:97-131`），应在此之上增加 promotion transaction，
而不是用上游 schema 替换它。

## 8. 自动 `SkillLibrary` 与论文 runbook skill library 不是同一个系统

### 8.1 自动 Python `SkillLibrary`

- **[源码事实]** `cap/skills/library.py` 声称从成功 trial 代码抽取 function、统计 occurrence、在多
  task 出现后 promotion；存储是默认 `.capx_skills.json`
  （`external/ASPIRE/aspire/sim/cap/skills/library.py:7-11`、
  `external/ASPIRE/aspire/sim/cap/skills/library.py:35-66`）。
- **[源码事实]** 真实 identity 只有 function name；同名再次出现就 `occurrences += 1`，代码被
  最新版本覆盖，`source_tasks` 只是去重附加
  （`external/ASPIRE/aspire/sim/cap/skills/library.py:72-104`）。
- **[源码事实]** `get_promoted_skills()` 默认 occurrence >= 2 即 promotion；
  `inject_into_namespace()` 对 promotion code 再次直接 `exec`
  （`external/ASPIRE/aspire/sim/cap/skills/library.py:110-125`、
  `external/ASPIRE/aspire/sim/cap/skills/library.py:147-158`）。
- **[源码事实]** extractor 不是 AST，而是只匹配 column 0 同步 `def` 的 regex
  （`external/ASPIRE/aspire/sim/cap/skills/extractor.py:17-35`、
  `external/ASPIRE/aspire/sim/cap/skills/extractor.py:55-88`）。
- **[源码事实]** trial 只有在 config `evolve_skill_library=True` 且 task completed 时才抽取并
  保存；异常只打印
  （`external/ASPIRE/aspire/sim/cap/envs/trial.py:951-964`）。
- **[源码事实]** 有两个 helper 能把 promoted skills 格式化为 prompt Markdown 或 Python
  source
  （`external/ASPIRE/aspire/sim/cap/skills/claude_integration.py:14-58`）。

连接性审计：

- **[未实现]** 全树静态检索未发现任何 YAML/config 设置 `evolve_skill_library`，也未发现 trial
  推理路径调用 `get_promoted_skills()`、`inject_into_namespace()` 或上述 prompt formatter。
  可选 extraction hook 存在，但本提交没有形成“抽取 -> 资格验证 -> 注入下一任务”的闭环。
- **[源码推断]** docstring 的“multiple tasks”与实现不完全一致：同一 task 的同名函数重复成功
  两次也能把 occurrence 推到 2；promotion 条件没有要求两个不同 `source_tasks`。
- **[未实现]** 没有 function code hash/version、依赖闭包、行为测试、API policy check、性能
  evidence、冲突合并、并发写锁、atomic save、rollback 或 provenance ledger。
- **[源码推断]** regex 会漏掉/误截断 decorators、`async def`、某些复杂 signature 或非规则
  缩进结构；本次没有对 extractor 做 fuzz test。

### 8.2 论文/runbook skill library

- **[源码事实]** suite library 是 `.claude/<suite>/skills/*.md` 的人/agent 可读知识库；上游
  registry 明确说 master 上是模板/placeholder，冻结的 learned snapshots 位于
  `learned-skills` branch
  （`external/ASPIRE/aspire/sim/.claude/README.md:27-46`）。
- **[源码事实]** LIBERO coordinator 读取 Stage 1 `findings.md`，由 coordinator 把 trigger、真实
  working code、why/evidence/provenance 合并进 shared Markdown skills；held-out outcome 明令不得
  驱动更新
  （`external/ASPIRE/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md:150-202`）。
- **[源码事实]** 这套 runbook library 使用前述 promotion recorder，而不是 `.capx_skills.json`。

**结论：**

- **[源码事实]** 两者持久化格式、promotion 规则、调用路径和审计机制完全不同。
- **[源码推断]** 论文复现实验主要依赖 Markdown runbook library；自动 Python `SkillLibrary`
  更像尚未完整接线的通用原型。
- **[源码事实]** 类名中的 `Franka*ReducedSkillLibrary` 是一组静态、人工实现的 callable robot
  helper API，不应与 `cap.skills.SkillLibrary` 自动抽取器混为一谈；traced wrappers 的继承关系
  可见于 `external/ASPIRE/aspire/sim/cap/integrations/libero_trace_logger.py:13-27` 和
  `external/ASPIRE/aspire/sim/cap/integrations/robosuite_trace_logger.py:9-23`。

**对本项目的含义：**不要移植 regex 自动抽取/occurrence>=2 promotion。应把“技能”视为有稳定
输入输出 contract、implementation hash、依赖、确定性 qualification 和回归证据的版本化对象；
本项目 `SkillDescriptor`/`Invocation` 已提供更强起点
（`self_improving/harness/schemas/common.py:107-153`）。

## 9. “Evolutionary Search”的实际代码与 prompt 驱动部分

### 9.1 Python evaluator 真正做的事

- **[源码事实]** `evosearch_eval.py` 初始化持久 simulator worker，读取已存在的
  `candidate_*/code.py`，对候选 × seeds 做笛卡尔积并行运行
  （`external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:5-33`、
  `external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:99-230`、
  `external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:501-558`）。
- **[源码事实]** 它按每个 candidate 的 `task_completed` 计算 pass count/rate、mean reward、
  errors，再只按 pass rate 降序；winner 是排序后的第一项
  （`external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:559-609`）。
- **[未实现]** `eval_results.json`/`iter_summary.json` 没有 candidate code hash、config hash、
  dependency digest、git commit 或 immutable run id；只记录可被后续改写的 code path、seeds 和
  本轮统计
  （`external/ASPIRE/aspire/sim/scripts/libero/evosearch_eval.py:559-598`）。
- **[源码事实]** trace analyzer 只是汇总诊断信号，不修改候选
  （`external/ASPIRE/aspire/sim/scripts/libero/analyze_evosearch_traces.py:210-268`）。

### 9.2 实际由 agent/prompt 完成的事

- **[源码事实]** actor prompt 要 agent 写 K=8 不同假设、保留 baseline 为 candidate A、每轮从
  top-3 survivors 设计下一轮候选，并在同一 51–65 seeds 上重评
  （`external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:101-124`、
  `external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:180-208`、
  `external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:228-253`）。
- **[源码事实]** ≥80% 或五轮停止写在 prompt；main coordinator 还列出“两轮提升 <5pp 即 plateau”，
  而 subagent prompt 明令不要因小提升提前停止
  （`external/ASPIRE/aspire/sim/.claude/libero/evosearch/main-agent-prompt.md:129-136` 对照
  `external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:228-238`）。
- **[源码事实]** “best 必须胜过 candidate A，否则 fallback”的 shell/Python 片段也在 prompt；
  其中 quoted heredoc `<< 'PYEOF'` 内的 `Path("$RUN_DIR")` 不会被 shell 展开
  （`external/ASPIRE/aspire/sim/.claude/libero/evosearch/subagent-prompt.md:257-297`）。
- **[源码推断]** 该 fallback snippet 按字面执行时会查找名为 `$RUN_DIR` 的相对路径，可能无法
  比较实际 run；本次未执行它。

### 9.3 明确缺失的算法组件

- **[未实现]** Python 中没有 mutation、crossover、population controller、generation loop、
  survivor selection、novelty/diversity metric、candidate lineage graph 或 optimizer state。
- **[未实现]** evaluator 不验证 K=8、不验证候选差异、不保存 parent/ancestor、不执行 top-3
  规则，也不把 error count、motion efficiency 或复杂度作为排序 tie-breaker。
- **[源码推断]** 因而准确称呼应是“LLM/agent 驱动的多假设迭代搜索 + 确定性 batch scorer”，
  而不是已经代码化的 evolutionary algorithm。这个区分直接影响复现：只运行 Python 脚本不会
  自动产生下一代。

**对 robust 仿真环境生成的可检验启发：**在 development scene set 上，每轮生成若干**机制上
不同**的 compiler/solver/validator 修复候选，对所有候选运行同一固定 seed/attack matrix，再让
结构化 failure clusters 指导下一轮。但候选 identity、parent、diff、复杂度、validators 和选择
规则都应由本项目 runtime 记录，不能只存在 prompt 和记忆中。

## 10. 测试覆盖与缺口

本节描述本提交的静态测试面；本次没有执行测试。

| 能力 | 已找到的测试 | 缺口 |
|---|---|---|
| immutable held-out identity/progress | identity、manifest 选择、full partition、50-result 状态有测试（`external/ASPIRE/aspire/sim/tests/test_libero_fix_loop_pipeline.py:24-104`） | 无 subprocess timeout、artifact-to-identity 绑定、错误 exit、并发 resume、split bypass 测试 |
| promotion recorder | patch/hash、幂等、串行 pending、no-op、finish 前 verify 有测试（`external/ASPIRE/aspire/sim/tests/test_record_skill_promotion.py:44-112`） | 无 tamper、rehash、hash chain、rollback、并发 race、crash recovery、symlink/path、资格/回归测试 |
| simulator env | integration tests 只有在显式环境变量和外部服务存在时运行，并主要检查 oracle reward=1（`external/ASPIRE/aspire/sim/tests/test_environments.py:19-25`、`external/ASPIRE/aspire/sim/tests/test_environments.py:50-77`、`external/ASPIRE/aspire/sim/tests/test_environments.py:79-107`） | 不覆盖 adversarial generated code、安全边界、timeout 或信息泄漏 |
| pytest discovery | `testpaths=["tests"]`，integration marker 被声明（`external/ASPIRE/aspire/sim/pyproject.toml:196-207`） | scripts 本身被 ruff exclude（`external/ASPIRE/aspire/sim/pyproject.toml:185-190`），大量关键 harness 脚本没有 lint/test 保护 |
| TraceLogger/keyframes | 未找到直接测试 | 无 schema、异常写盘、buffer、相机名、keyframe/trace 一致性、timeout flush、redaction、hash 测试 |
| timeout/watchdog | 未找到直接测试 | 无 `BaseException`/KeyboardInterrupt 嵌套回归、C hang、sequential hard kill、missing-trial completeness 测试 |
| 自动 `SkillLibrary`/extractor | 未找到直接测试 | 无解析边界、同名冲突、跨 task promotion、atomic/concurrent save、unsafe injection 测试 |
| evosearch evaluator | 未找到直接测试 | 无 seed role lock、candidate lineage、排序 tie、worker hang、零结果/部分结果/重复 seed 测试 |
| BEHAVIOR campaign isolation | 未找到可执行测试 | freeze hash、fresh context、跨 seed firewall、terminal denominator 全是 runbook 自律 |

上述“未找到”来自对 `external/ASPIRE/aspire/sim/tests` 的完整关键词检索，不是通过测试执行推断。

## 11. 对本项目可移植的最小模块

### 11.1 P0：不可变 evaluation run（优先实现）

移植 `run_fix_loop_validation.py` 的模式，而非其 LIBERO 路径和 shell：

```text
EvaluationRunIdentity
  = route + candidate_sha256 + config_sha256 + dependency digests
  + split_manifest_sha256 + role + ordered seeds/cases + gate_profile

run_id = sha256(canonical identity)
```

要求：

1. `role` 只能是 `development|validation|heldout`，低层 replay 必须拒绝 case 不属于对应
   immutable split manifest；
2. 每 case 原子追加结果，resume 只接受完全相同 identity；
3. 每个结果必须绑定 candidate/config/dependencies/environment package，而非仅记录 artifact path；
4. missing/timeout/invalid 都是 terminal denominator row，不能从 summary 消失；
5. held-out 只输出聚合结果给 optimizer，详细 evidence 进入只读归档，不能回流 promotion；
6. completion 必须验证请求集合与 terminal result 集合相等，不能用普通 done flag 代替。

这可直接利用本项目已有 `Invocation` digest、artifact hashes 和连续 `RunState.events`
（`self_improving/harness/schemas/common.py:62-95`、
`self_improving/harness/schemas/common.py:135-227`）。

### 11.2 P0：项目原生 typed trace

借鉴 TraceLogger 的调用级诊断，不复制其 schema：

- `EpisodeTraceHeader`：run id、invocation digest、candidate hash、seed/case、split role、dependency
  digests、environment package/resolved scene hash、trace schema version；
- `StageEvent`：单调 seq、stage、attempt、start/end、input/output summaries、error category、validator
  decision；
- `EvidenceRef`：URI、media type、bytes、SHA-256、capture stage、成功写盘状态；
- 流式 JSONL/分块写盘，限制大小，明确 backpressure；任何 evidence save failure 进入 terminal
  blocker，不能静默吞掉；
- 视频同时记录 total frame count、unique frame count、fps、codec/hash；render 仍不等于物理证据。

本项目已有 hash-bound `EnvironmentPackage` 与 typed runtime evidence
（`self_improving/harness/schemas/text2env.py:51-87`、
`self_improving/harness/schemas/text2env.py:132-169`），因此 ASPIRE trace 应作为诊断证据补充，
不能降低现有 resolved-scene/runtime binding。

### 11.3 P1：可验证 promotion transaction

在本项目 `SkillDescriptor`/`SkillQualification` 上增加：

1. begin：锁 registry，记录 parent version、before manifest/hash 和 development evidence refs；
2. qualify：固定 deterministic regression/attack matrix，验证 API boundary、依赖、性能与非回归；
3. finish：原子写 after manifest、exact diff、qualification report hash，并用前一 ledger entry hash
   构成 hash chain；
4. verify：重新 hash 当前 registry、snapshot、patch、evidence、qualification artifact 和 ledger chain；
5. rollback：只回到已验证 parent version，重放/验证 inverse diff，追加 rollback record，不改写历史；
6. 明确拒绝任何 held-out evidence 作为 promotion input。

### 11.4 P1：候选矩阵 evaluator，而非 prompt-only evosearch

最小实现包括：

- candidate identity = parent hash + patch hash + hypothesis id；
- 固定 development case matrix，所有候选完全相同；
- selection metric 以本项目 authoritative physical gates 为首要约束，再比较 robustness、invalid
  rate、复杂度/资源；
- failure cluster 是结构化 validator/code，不从目录名或自由文本推断；
- validation 集只用于候选选择；held-out 只用于最终一次报告；
- 若 LLM 产生候选，prompt/model/budget 也是 identity/provenance 的一部分。

### 11.5 一个最小、可证伪的实验顺序

这不是已完成实验，而是由本次源码审计导出的后续实验合同：

1. **Baseline**：固定一批 development/validation/held-out text-to-env requests 和 adversarial
   physical cases；记录现有编译成功、runtime valid、false-positive、耗时。
2. **Trace-only A/B**：不改 solver，仅增加 typed trace；度量失败 root-cause 可归类率、证据
   完整率、内存/磁盘开销。若不能改善诊断可重复性，就不继续增加复杂度。
3. **Multi-candidate A/B**：同一 dev matrix 下比较单候选与 4–8 个机制不同候选；选择前只看
   dev/validation，最终 held-out 一次。主要指标应是 authoritative runtime validation pass 和
   false-positive，不是 render 或自报 success。
4. **Promotion A/B**：只有 qualification 全通过的修复进入 registry；下一批完全未见任务上比较
   transfer gain 与 regression。无 statistically/operationally meaningful gain 就 rollback。
5. **污染审计**：自动检查任何 held-out artifact hash/path/seed 是否出现在 candidate prompt、
   skill evidence 或 promotion record；出现即判实验无效。

## 12. 明确不应移植的部分

- **不要移植** raw in-process `exec`、full imports、raw env/APIS access 和 `sandbox_rc` 命名；它们
  不提供安全边界。
- **不要移植** ASPIRE 的 simulator/perception server/GPU topology、固定 seed 数字、LIBERO
  taskcompleted 门槛或 shell/nohup 路径；这些是实验设施，不是通用 harness contract。
- **不要移植** regex `SkillLibrary`、occurrence>=2 自动 promotion 或 promoted code `exec` 注入。
- **不要移植** prompt-only coordinator、手写 GPU ledger、以 agent 通知充当 durable state。
- **不要移植** 只按 pass rate 的 winner selection；robust 环境生成必须以当前 support、
  containment、dynamic/contact、hash-bound runtime validators 为权威。
- **不要移植** 从目录名/mtime 推断唯一真值；artifact 必须有 typed identity 和内容 hash。
- **不要移植** held-out 结果回流候选、skills 或 optimizer 的流程。
- **不要移植** 静默吞掉 trace/keyframe/skill extraction 失败的做法。
- **不要改变** `scene_gen/` 的稳定信任边界。所有 candidate search、trace、promotion、split 和
  orchestration 都应留在 `self_improving/` 明确模块中。

## 13. 最终判断

### 对 robust 仿真环境生成

- **[源码推断]** ASPIRE 的 trace-driven failure localization 和同一开发矩阵上的多假设比较，
  有合理机制帮助发现 parser/grounding/solver/validator 的脆弱模式。
- **[源码事实]** 本次没有上游仿真运行或本项目 A/B 数据，因而本审计不能声称会提升成功率或降低
  false-positive。
- **不可妥协条件**：本项目现有 authoritative physical gates、resolved-scene hash binding 和
  runtime evidence contract 必须高于任何 agent 自报、render 或 pass-rate leaderboard。

### 对 self-improving / learning 范式

- **[源码事实]** ASPIRE 展示了一个有用的人机/agent 学习范式：development failure -> trace
  diagnosis -> generalizable finding -> shared skill -> frozen evaluation。
- **[源码事实]** 在该提交中，这一闭环的大部分学习决策仍由 agent 阅读 Markdown、代码和
  trace 后完成；自动 `SkillLibrary` 不是论文 runbook 的机械实现。
- **[源码推断]** 真正可泛化的贡献是“严格数据角色 + 可审计 evidence + 受资格门控的知识
  promotion”，而不是某个 prompt 或 occurrence counter。
- **实施建议**：先做 P0 immutable evaluation run 与 typed trace；只有它们能证明数据未污染、
  artifact 完整、门控可信后，才值得做 candidate evolution 和 skill promotion。否则
  self-improving 只会更快地放大错误。
