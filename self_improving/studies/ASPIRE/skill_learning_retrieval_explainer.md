# ASPIRE 的 API 绑定、失败技能积累与检索机制

> 研究日期：2026-09-02
>
> 上游源码冻结点：[`NVlabs/ASPIRE@7ba73d3bcac8f6b6d4a7d67ed4040988f768d282`](https://github.com/NVlabs/ASPIRE/tree/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282)
>
> 论文版本：[`arXiv:2607.00272v1`](https://arxiv.org/html/2607.00272)
>
> 证据边界：只使用论文、NVIDIA 官方项目页、NVlabs 固定源码和仓库内正式 runbook。

## 0. 证据标记

- **[F]**：论文、官方项目页或固定源码直接给出的事实。
- **[O]**：对固定源码树进行静态搜索或确定性计算后得到的观察。
- **[I]**：由上述事实推出的解释；不是论文独立实验结论。
- **[U]**：公开材料不足以证明，本文不补猜。

论文实验数字均为作者报告。固定公开提交未包含论文原始 rollout、完整累积 skill
snapshots、snapshot tags 或可直接重算全部表格的输出包，因此本文不把论文数字称为本地复现。

## 1. 对用户理解的直接判定

用户的理解**大方向正确，但最后一步的运行机制不正确**。

正确部分：

1. **[F]** agent 面向的是一套预定义的机器人编程 API，包含观察、分割、点云、
   抓取、IK、关节运动和夹爪控制等能力。
2. **[F]** API 的函数名、签名和 docstring 会成为 agent 的工具说明；公开 Fix Loop
   runbook 也列出 allowed APIs。
3. **[F]** agent 把这些 primitive 组合成可执行 Python，即 code-as-policy。
4. **[F]** 高层 movement helper 内部最终调用 LIBERO 的环境步进，任务完成则由
   LIBERO 的 `check_success()` 判定。

需要修正的部分：

1. **[F]** 系统不是在生成文本中发现 `fns` 的 key 后，再去“检索”对应实现。
2. **[F]** `functions()` 返回的是 `名字 -> 已绑定 Python callable` 的字典；executor
   在执行代码之前，已把每个 callable 放进持久的 Python globals。
3. **[F]** 生成代码里的 `get_observation()` 由普通 Python 名称解析找到这个 callable。
   名字只出现在字符串或注释中不会调用任何东西。
4. **[F]** 被注释掉或未被 `functions()` 返回的函数不会自动出现。例如公开文件中的
   四个 CuRobo 函数默认没有暴露；`point_prompt_molmo` 也只在服务可用时暴露。

源码链：[`functions()` 注册表](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced.py#L99-L126)、
[`combined_doc()` 动态汇总签名和 docstring](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/base_api.py#L98-L127)、
[`prompt` 拼装与 globals 绑定](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/tasks/base.py#L145-L211)。

## 2. 第一性原理模型：手册、工具箱与调试手册是三件事

最通俗的比喻是：

- curated robot API 是已经摆在工作台上的**工具箱**；
- API docstring / API reference 是告诉 agent 如何用工具的**使用手册**；
- ASPIRE skill library 是记录“何时容易失败、怎样修”的**调试手册**；
- primitive trace 是记录每次工具调用发生了什么的**飞行记录仪**。

因此完整链条是：

```text
任务描述 + API 手册 +（可用的）修复 skill + 当前失败 trace
                         ↓
                    coding agent
                         ↓
                   Python 任务程序
                         ↓
     预绑定 globals 中的 curated callable + Python exec
                         ↓
      LIBERO / robosuite controller / MuJoCo physics
                         ↓
        primitive trace + keyframes + check_success
```

## 3. `functions()` 到底做了什么

### 3.1 两条独立通道

**知识通道。** `ApiBase.combined_doc()` 遍历 `functions()`，用 Python reflection
读取每个 callable 的签名和 docstring；task wrapper 将这些内容追加到 prompt。
这使模型知道函数名、参数和返回值。

**执行通道。** task wrapper 初始化和每次执行前都遍历同一映射，把
`fn_name -> bound method` 写入 `exec` globals；然后对任务 Python 执行 `exec`。
这使代码真正能够调用实现。

两条通道缺一不可：只有文档，代码会得到未定义名称；只有 callable，模型则可能不知道
怎样正确调用。[F]

### 3.2 为什么不叫“文本命中后自动检索”

下面三种文本的运行含义不同：

```python
"open_gripper"       # 普通字符串，不动作
# open_gripper()     # 注释，不动作
open_gripper()       # Python 函数调用，解析到预绑定 callable
```

实现并没有被复制进 agent 生成的代码；生成程序通常只保留调用语句。实际方法仍属于已经
实例化、并持有环境引用的 API 对象。[F]

### 3.3 LIBERO 的 `step` 在哪一层

agent 通常不直接把原始 `env.step(action)` 当公开 primitive 使用。公开 API 的
`move_to_joints()` 会调用环境层的 blocking movement：读取当前 7 关节位置，构造
7 个 joint deltas 加一个 gripper action，然后反复调用 `handle.step(action)`。
LIBERO 再把动作交给 robosuite 的 joint-position controller 和 MuJoCo 动力学。
[源码：movement bridge](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L218-L253)

任务程序执行完后，ASPIRE 的 `task_completed()` 委托 LIBERO 环境的
`check_success()`；“Python 没崩溃”和“机器人任务完成”是两个不同判据。
[源码：成功判据](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/simulators/libero.py#L533-L535)

### 3.4 这套 API 不是 baseline policy

**[I]** API 规定机器人程序能看什么、能调用什么；它不是一个负责完成整项任务的固定策略。
它确实带有人工先验，例如预选的 SAM3、GraspNet、IK、top-down grasp helper，但“看哪个物体、
选哪个抓取、经过哪些 waypoint、何时恢复”仍由生成程序决定。

## 4. 三个容易混为一谈的 `skill`

| 名称 | 实际内容 | 怎样使用 | 与论文共享 skill library 的关系 |
|---|---|---|---|
| `FrankaLiberoApiReducedSkillLibrary` | 固定 Python 几何/点云 helper，例如 `mask_to_world_points`、`normalize_vector` | 通过 `functions()` 预绑定为 callable | 是 curated API 的扩展，不是运行中持续成长的记忆 |
| `.claude/libero/skills/*.md` | 失败签名、触发条件、修复策略、代码片段和证据 | 读入 coding agent 上下文，由模型选择、复制或改写 | 这是公开 LIBERO runbook 中最接近论文方法的共享 library |
| `cap.skills.SkillLibrary` | 从成功代码提取顶层函数、按名字计出现次数的 JSON 原型 | 类本身提供 occurrence promotion 和 `exec` injection | 当前主 Fix Loop 未接线，不能当作论文主机制 |

固定 helper 类见[源码](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/integrations/franka/libero_reduced_skill_library.py#L38-L73)。

通用 Python `SkillLibrary` 的类确实提供 `get_promoted_skills(min_occurrences=2)` 和
`inject_into_namespace()`；但是固定源码的非测试 trial 路径只在显式
`evolve_skill_library=true` 且 `task_completed=true` 时执行
`extract_from_code()` 和 `save()`，没有调用 promotion/injection 方法。
[library 类](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/skills/library.py#L72-L158)、
[唯一 opt-in trial hook](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/cap/envs/trial.py#L951-L964)。

## 5. 失败怎样积累成 skill

最重要的边界是：**失败本身不是 skill；通过干预验证的修复模式才是 skill。**

### 5.1 论文层的闭环

论文 §2.1–2.2 给出的链是：

1. agent 写一个 code-as-policy 程序；
2. execution engine 记录每个 perception、planning、grasp、control primitive 的 API、
   输入、输出、状态和可能的视觉证据；
3. agent 从 trace 定位失败 primitive，而不是只看最终 reward；
4. agent 提出根因假设并修改程序；
5. 在 debugging configurations 上重跑，检查修复是否真的改变结果；
6. actor 输出结构化 finding：失败模式、已验证修复、潜在可迁移模式；
7. coordinator 审计可复用性与 allowed-API 合规性，只把通过 debug validation 的模式
   写进共享 library；
8. 未来 actor 把它作为 in-context guidance 复用。

来源：[Robot Execution Engine](https://arxiv.org/html/2607.00272#S2.SS1)、
[Skill Library](https://arxiv.org/html/2607.00272#S2.SS2)。

论文中的 radio 例子很典型：trace 显示感知已经找到 radio，但连续
`navigate_to_pose` 返回 `PLANNING_ERROR`；根因是目标位姿落进桌边碰撞 buffer。
修复不是改 perception，而是加入多角度 approach；重跑成功后，进入库的是
“障碍边缘规划失败时尝试替代接近方向”这一模式，不是完整 radio 程序。[F]

### 5.2 固定公开 LIBERO runbook 的具体实现

公开 Fix Loop 把抽象过程实现为可审计文件流：

1. actor 先在 seed 51 探索，写 `initial_code.py`；
2. 在 development seeds 51–65 运行，失败 seed 读取 `trace.json`、`code.py`、
   `keyframes/` 和 `summary.txt`；
3. trace 中的 `num_masks=0`、gripper width、缺失 IK result、sandbox crash 等帮助归因；
4. 每个 seed 最多三次 repair replay；
5. 汇总跨 seed 证据，写一个非 seed-specific 的 `fix_code.py`；
6. 写 `findings.md`，逐项给 Root Cause、What Fixed、Trigger、真实 Code、Evidence、
   Target skill file；
7. actor 不得直接改共享库；coordinator 读取 findings，把内容路由到
   `localize.md`、`grasp.md`、`transport.md` 或 `manipulation.md`；
8. coordinator 在编辑前保存 library snapshot，编辑后保存精确 patch、前后哈希和
   append-only JSONL promotion ledger；
9. held-out seeds 1–50 单独评估，并明确不得用于反向修改 library。

来源：[actor debug 与 findings 协议](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/subagent-prompt.md#L72-L209)、
[coordinator promotion 协议](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md#L150-L202)、
[promotion receipt 实现](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/record_skill_promotion.py#L171-L303)。

**[O]** promotion recorder 证明“哪些 Markdown bytes 被改了、由哪个 finding 驱动”；
它自身不运行 simulator，也不独立重算 finding 声称的成功率。结果真实性仍依赖 actor 输出、
coordinator 审计和外部 evaluation artifacts。

### 5.3 LIBERO-90 library build 的额外过滤

公开 zero-shot build runbook 还给了更明确的 admission heuristic：开发集成功至少
15/30；替换已有模式要至少提高 10 个百分点，否则保留为 alternative；拒绝绝对坐标等
过度特化条目；要求模式应能帮助至少两种 task types；skill entry 必须记录 pass rate。
[源码 runbook](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/zeroshot-transfer/main-agent-prompt.md#L178-L213)

这些是公开 runbook 的规则，不应倒推为论文 Algorithm 1 中形式化证明过的统一准入定理。[I]

## 6. skill 怎样被检索

### 6.1 论文给出的机制

论文只明确说：未来 actor 将共享 library 中的 skill 作为 **in-context guidance**；
skill 包含 failure signature、when-to-apply、repair strategy，以及必要时的代码片段。
在 evolutionary search 中，下一批候选同时条件化于 skill library、历史 Top-3、完整历史和
残余 failure traces。[F]

论文没有在 Algorithm 1 中规定 embedding model、向量数据库、相似度公式或 learned retriever。
[Algorithm 1](https://arxiv.org/html/2607.00272#alg1)

### 6.2 公开 LIBERO 源码中的实际检索

**[O]** 对固定 commit 的 LIBERO runbooks、scripts 和 `cap.skills` 搜索后，没有发现
FAISS、Chroma、embedding、cosine-nearest-neighbor 或其他语义向量检索路径。

公开实现采用更朴素的三步：

1. **粗粒度路由**：根据问题属于 localization、grasp、transport 或 manipulation，选择文件；
2. **上下文装载**：Fix Loop 要求读 relevant files；evosearch 和 zero-shot prompt 直接
   `cat` 四个 Markdown 文件；
3. **模型内匹配**：LLM 根据 skill 的 Trigger、对象、场景几何、错误信号和代码片段判断
   是否适用，随后生成或改写任务程序。

zero-shot evaluation 甚至明确要求从冻结 snapshot worktree 读取全部四个文件，并逐项检查
对象是否匹配、场景几何是否匹配、操作模式是否适用、prompt 是否过度特化。
[冻结 snapshot 检索协议](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/library-size-scaling/subagent-prompt.md#L189-L208)

所以这里的“检索”更接近“把调试手册放进上下文，让模型按触发条件选用”，不是数据库 RAG。

### 6.3 evolutionary search 不是传统遗传算法

论文的 `ProposeRepairs` 让 LLM 基于 Top-3、library、history 和 traces 生成 K 个候选，再由
真实执行结果选优；它没有规定固定 crossover 或 mutation operator。[F]

公开 LIBERO runbook 把它具体化为 K=8 distinct hypotheses、最多五轮，并把已有 repaired
program 作为 candidate A 性能下限。这是公开复现协议的实现选择，不是 Algorithm 1 的普遍常数。
[runbook](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/evosearch/subagent-prompt.md#L180-L234)

## 7. 论文做了哪些实验，结果是什么

### 7.1 共同设置与对照

**[F]** 仿真实验使用 Claude Code + Claude Opus 4.6 + 1M context；agent、环境和 API
固定。主要 coding baseline 是 CaP-Agent0，另比较 OpenVLA、π0、π0.5 和部分 human
programs。LIBERO-Pro 每任务在 seeds 51–65 学习，在 1–50 上用一个固定生成程序评估；
Robosuite 在 101–125 学习、1–100 评估；BEHAVIOR-1K 在 26–35 学习、1–25 评估。
[Experimental Setup](https://arxiv.org/html/2607.00272#S3.SS1)、
[Baselines](https://arxiv.org/html/2607.00272#S3.SS2)、
[Protocol](https://arxiv.org/html/2607.00272#S3.SS3)

CaP-Agent0 会针对每个 eval seed 重新生成并 retry；ASPIRE 在 LIBERO/Robosuite 使用每任务
一个程序跨 held-out seeds。BEHAVIOR-1K 则是根据当前 trace 增量生成下一代码块，不能误称为
同样的单一静态程序协议。[F]

### 7.2 LIBERO-Pro：扰动鲁棒性

论文 Table 2 的 macro success：

| 方法 | Overall Pos | Overall Task | All |
|---|---:|---:|---:|
| OpenVLA | 0.00 | 0.00 | 0.00 |
| π0 | 0.00 | 0.00 | 0.00 |
| π0.5 | 0.25 | 0.01 | 0.13 |
| CaP-Agent0 | 0.20 | 0.16 | 0.18 |
| ASPIRE | **0.77** | **0.67** | **0.72** |

按 suite 的 Pos/Task 平均，ASPIRE 相对各 suite 最强 baseline 的增益约为：Object 77、
Goal 41.5、Spatial 42.5 个百分点。Object 本身为 0.98/0.95，Goal 为 0.81/0.45，
Spatial 为 0.51/0.60。[Table 2](https://arxiv.org/html/2607.00272#A2.T2)

### 7.3 Robosuite：接触与双臂

7 个任务、每任务 100 个 held-out trials，CaP-Agent0 均值 0.68，ASPIRE 0.81。
最大提升是 `two_arm_handover` 的 0.20→0.92。结果并非每任务都更好：
`two_arm_lift` 为 0.74→0.71，`spill_wipe` 为 1.00→0.99。
[Table 3](https://arxiv.org/html/2607.00272#A2.T3)

这说明主张应是总体/特定困难任务改进，不是“所有任务严格支配”。[I]

### 7.4 BEHAVIOR-1K：长时序移动操作

| 任务 | CaP Nav / Task | ASPIRE Nav / Task |
|---|---:|---:|
| Soda Can pick-up | 0.84 / 0.72 | **0.92 / 0.88** |
| Radio pick-up | 0.80 / 0.56 | **1.00 / 0.88** |

每任务 25 个 held-out seeds；radio task completion 提升 32 个百分点。
[Table 4](https://arxiv.org/html/2607.00272#A2.T4)

### 7.5 skill-library size 与 zero-shot transfer

在 LIBERO-90 累积 repair skills，再对 LIBERO-Pro Long 做 zero-shot：没有额外 debug、retry
或 task-specific library update。

| library snapshot | Pos | Task | Overall |
|---|---:|---:|---:|
| N=0 | 0.000 | 0.094 | 0.047 |
| N=25 | 0.056 | 0.218 | 0.137 |
| N=50 | 0.138 | 0.292 | 0.215 |
| N=90 | **0.226** | **0.383** | **0.305** |

**N 是为 library 提供 repair skills 的 LIBERO-90 来源任务数量，不是 skill 条目数。**
[Table 5](https://arxiv.org/html/2607.00272#A3.T5)

该实验支持“在这一构建顺序和 benchmark 上，更多来源任务对应更好的 macro zero-shot
success”；它不单独识别是 skill 数量、skill 质量、上下文长度还是来源任务覆盖度造成增益。[I]

### 7.6 跨 embodiment 的真实机器人 guidance

这不是把仿真策略直接部署到真机：真机使用自己的 perception、calibration、control stack，
simulation-discovered skill 只作为 in-context guidance。[F]

| 任务 | 无 skill 总 tokens | 有 skill 总 tokens | 无 skill success | 有 skill success |
|---|---:|---:|---:|---:|
| Put bowl on plate | 8.65M | 5.11M | 20/20 | 20/20 |
| Lift soda can | 61.94M | 6.58M | 13/20 | 19/20 |
| Open/push drawer | 334.917M | 81.67M | 0/20 | 11/20 |

作者把它称为 selected skills 的初步跨 embodiment 证据；bowl 只减少调试成本，没有提高最终
success。[Table 1](https://arxiv.org/html/2607.00272#S3.T1)

### 7.7 消融：执行引擎、library 与 evolutionary search

论文主文报告：base system 约 14%，加入中间系统约 62%，完整 ASPIRE 约 72%。
[Ablation section](https://arxiv.org/html/2607.00272#S3.SS7)

关键精度边界：附录把中间臂明确定义为 **Robot Execution Engine repaired program
(Execution engine + skill library)**。所以 14→62 不能被严格拆成“trace execution engine
单独贡献”；它同时包含 library context 和修复过程。[Table 7](https://arxiv.org/html/2607.00272#A4.T7)、
[Table 8](https://arxiv.org/html/2607.00272#A4.T8)

完整 ASPIRE 也不是盲目接受 evolutionary result：按任务用 seeds 66–80 在 repaired program
和 evo candidate 之间选择。raw evolutionary search 会在一些任务下降；Table 9 也展示了明显
非单调轨迹，例如某任务 0.62→0.60→0.60→0.18→0.86。
[Table 9](https://arxiv.org/html/2607.00272#A4.T9)

## 8. 不足与风险

### 8.1 论文明确承认的五项限制

1. **真机闭环不完整**：仍需要可靠 success detection、safe reset、安全监控与 calibration
   maintenance，尚非 fully autonomous real-world lifelong learner。
2. **依赖 frontier LLM**：使用 Claude Opus 4.6 + 1M context，未验证小模型能维持同样闭环。
3. **能力受 predefined API 限制**：API 外的新 sensing/control/interaction primitive 仍可能要人扩展。
4. **长期 memory management 未解决**：条目可能 stale、overly specific、redundant 或对新任务
   misleading，需要更好的 retrieval、pruning、ranking、re-validation。
5. **成本高**：每任务需要很多 LLM calls 与 simulator/robot rollouts。

来源：[论文 Limitations](https://arxiv.org/html/2607.00272#S5)、
[NVIDIA 官方限制列表](https://research.nvidia.com/labs/gear/aspire/#limitations)。

### 8.2 固定公开源码进一步暴露的边界

1. **“curated” 不是硬 sandbox。** executor 把 `env`、`APIS` 放入 globals，并执行拥有
   full import access 的 Python。禁止 raw MuJoCo/asset 读取主要是 agent protocol；官方 README
   明确警告 watchdog 不是安全沙箱。
   [官方警告](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/README.md#L105-L108)
2. **retrieval 很依赖 LLM 自己判断。** 公开 LIBERO 路径没有 typed applicability、向量排名、
   conflict resolution 或自动 negative-transfer 检测；库变大可能把错误/过时建议一起塞进 context。
3. **promotion receipt 不是 qualification runner。** 它冻结 findings、patch 和哈希，但不亲自
   重跑 evidence；coordinator 仍是关键可信判断点。
4. **skill promotion 与 held-out 隔离有双刃剑。** held-out 不回流可防 benchmark leakage；但
   普通 Fix Loop 的 library entry 是基于 development evidence 晋升，尚未要求逐 skill 的独立
   held-out efficacy 和 clean-regression gate。
5. **消融有组件捆绑。** execution-engine arm 同时含 skill library，不能精确分离二者因果贡献。
6. **真实机器人证据范围小。** 只有三个选定 simulation-discovered skills、一个真机平台和每任务
   20 个 evaluation trials，论文也只称 initial evidence。
7. **公开可复算性有限。** 固定提交有源码、runbooks、模板和展示媒体，但没有论文原始 rollout、
   累积库 snapshots/tags、promotion ledgers 或完整结果包；论文表格不能仅靠 committed bytes 重算。
8. **API 先验很强。** SAM3、GraspNet、IK、关节控制以及部分人工/历史提炼 helper 都已提供；
   ASPIRE 证明的是“在这套工具与模型上做程序调试/知识累积”的效果，不是从裸 motor torque 自主
   发现所有机器人能力。

## 9. 最终心智模型

最准确的一句话是：

> ASPIRE 先把一组机器人 callable 和它们的手册交给 coding agent；agent 编写 Python，
> callable 在执行前已绑定。失败时，trace 帮助定位根因；只有经重跑支持、被 coordinator
> 判断为可迁移的修复，才压缩进 Markdown skill library。未来 agent 读取这些修复说明作为
> 上下文，再生成新的程序；这不是函数名文本命中，也不是已公开的向量检索系统。
