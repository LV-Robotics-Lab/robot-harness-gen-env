# ASPIRE 对 Robot Harness Gen-Env 的适用性研究：最终报告

日期：2026-08-31
研究对象：[论文](https://arxiv.org/abs/2607.00272)、
[NVIDIA 项目页](https://research.nvidia.com/labs/gear/aspire/)、
[官方源码](https://github.com/NVlabs/ASPIRE)
上游固定提交：`7ba73d3bcac8f6b6d4a7d67ed4040988f768d282`
本地形态：`external/ASPIRE` Git submodule
研究分支：沿用 `worktree/bingsheng`；未创建新分支
E0–E2 行为快照：`1180aef33936fcf1955923ed2a270e5a775029fb`
后续历史诊断快照：`f9d6ad5c844f51b34c882e8d563f99e19208bd07`、
`28333dee6871ac6a217973c78ad691ea4ffabcf1`
研究收尾 Harness 验证快照：`910ccb127eb5d2564caac42f1cd5cca3c3ec2d29`
研究收尾固定源码诊断：`148001dd4049346b9deadca1fec3bb8f588fc8d5`
封存后并发固定审计：`ab0385967531737b4ae075f65af1fd9d224e03dc`

## 1. 结论先行

ASPIRE 启发的源码/协议审计**确实暴露并促成本地修复了三类可复现的 agent harness 契约缺陷**；
这不是 ASPIRE 全系统改善本项目的证明。另一个 validated-memory 结果只成立于本研究自定义的
确定性合成 proxy。研究最终保留三类生产契约修复和一个待真实仿真证伪的候选机制：

1. runtime evidence 的自声明 `scene_id` 与 resolved scene digest 必须和当前输入相等；
2. active Stage 5 入口的 `pending_visual_review` 与默认 static-only 中间态不再以 final/pass 终态暴露；
3. Text2Env compile 在校验/复制 catalog 后只消费 digest-verified CAS snapshot，不再回读可变 locator；
4. 只有在开发集通过资格门、冻结后再进入 held-out 的 trigger-specific skill memory，值得进入
   下一轮真实仿真实验。

固定的六类合成故障基准上，validated-memory arm 在 120 个 held-out fault cases 的 mHRC 为
1.00，reactive/no-memory 为 0.50，frozen retry 为 0.00；三臂均无 unsafe publication、clean
regression 或 budget violation。这个结果说明该 deterministic proxy **按设计能够表达验证后记忆
与 reactive 策略的行为差异**，不证明仿真物理成功率、LLM 自我学习、策略训练或论文尺度
ASPIRE 性能。

真实 SAPIEN 仿真物理回放层只完成了一次 can-on-plate：新 declared-identity equality gate 消费了
runner 产生的自声明字段；回放通过、0 fail checks、120 帧/100 unique。由于没有在相同
seed/task/budget 下比较 memory 与
reactive repair 的多次真实 rollout，关于“robust 仿真环境生成是否提升”的答案仍是**未知**，
而不是“已提升”。

## 2. 第一性原理拆解

一个 self-improving robot harness 若要真的“改进”，至少要闭合五个可独立证伪的环节：

```text
候选生成
  -> 受控执行
  -> 与输入/执行身份可复核绑定的结构化 trace
  -> 权威成功/失败判定
  -> 仅把跨样本有效的修复晋升为可复用技能
  -> 在未见数据上重新评估
```

其中任何一步缺失，都会产生伪进步：执行未崩不等于任务成功；渲染像对不等于物理通过；当前
trace 能修一个 case 不等于值得写入长期记忆；held-out 结果若反向修改技能库就不再是 held-out；
证据若未绑定输入内容则可以“借壳通过”。

因此本研究没有把论文名称、agent 自评或模型分数当指标。最终权威仍是本项目确定性的 schema、
target-local geometry、runtime physics gate、内容哈希与未见样本结果。

## 3. 对论文的理解

### 3.1 ASPIRE 是什么

ASPIRE（Agentic Skill Programming through Iterative Robot Exploration）是一种**非梯度式、
代码即策略**的机器人自改进方法。它不是直接更新神经网络权重，而是让 agent 反复生成/修复
Python task programs，把执行中发现的可迁移规律压缩进共享 skill library，再用候选程序搜索
继续提高任务成功率。

论文方法可分为三层：

- execution engine：执行代码、暴露预定义/curated robot programming API、记录关键帧/轨迹/错误，
  使 actor 能基于真实执行反馈修复程序；
- skill library：把跨任务可复用、经过验证的修复写成共享技能，而不是共享所有原始历史；
- evolutionary program search：依据 Top-3 候选、技能、历史和 traces 提议并评估下一轮程序，
  继续探索而不是只做一次局部修补；Algorithm 1 没有规定固定 crossover/组合算子。

公开实现可工程抽象为双环：内环执行生成代码并产出 trace，外环的 repository-aware actor 读取
trace 和代码状态后修改程序；coordinator 负责跨任务知识整理、晋升与实验编排。评估成功应看
`task_completed`，不能把 `TrialSummary.success` 这种“执行过程未崩”误当任务完成。

### 3.2 论文结果应如何解读

论文作者报告的 LIBERO 消融大致从基础约 14%，到 Robot Execution Engine 修复程序（execution
engine + skill library）约 62%，最终含搜索约 72%。Table 5 的 `N=90` 指 90 个 LIBERO-90
来源任务的 repair skills 用于 seed library，此时整体结果约 30.5%，但个别目标任务会倒退。真实机器人表中，
部分任务显示技能可减少 token 或把成功率从 13/20 提到 19/20、从 0/20 提到 11/20，也有
20/20 对 20/20 只降低成本而不提高成功率的情形。该 token 统计到首次成功；若未成功则统计到
预算耗尽，并且只覆盖表中选择的三个技能。

这些都是**作者报告**，不是本地重算结果。官方仓库没有提交论文原始运行输出；execution engine、
skill library 与搜索也不是每项都能由表格完全因果隔离。详细表图、算法和官方链接在
`paper_and_official_sources.md`。

### 3.3 论文自己暴露的限制

- 真实机器人 reset、success detection 和 safety 自动化仍不完整；
- 依赖强模型、昂贵调用与大量 rollout；
- 预定义 primitive API 限制可探索空间；
- skill entries 可能过时、过度特定、重复或误导；这可能解释非单调趋势，但论文未单独证明
  每次倒退都是 memory 导致的负迁移；
- 总体技能增加不保证每个任务单调变好。

这正是为什么本项目不能直接“加一个 memory 文件”就宣称 self-improving。

## 4. 官方源码与论文叙事的差异

源码审计确认，公开实现包含有价值的机制，但不是一个统一、类型化、可直接移植的 coordinator
runtime：

- identity-frozen、原子更新、可续跑的 held-out validation manifest 是最成熟、最适合移植的部分；
- TraceLogger 有诊断价值，但缺少输入 digest 绑定、流式原子写入和完整性复核，部分保存失败会
  静默降级；
- coordinator、GPU ledger、候选生成、谱系和 survivor selection 很大部分由 prompt/runbook
  驱动，Python “evolutionary search” 更接近批量 evaluator；
- 自动正则抽取的 Python `SkillLibrary` 与论文 runbook 使用的 Markdown skill library 是两套机制；
- promotion ledger 有 snapshot/patch 骨架，但 verify 不重算哈希，也没有锁、rollback 或资格门；
- 生成程序使用 raw in-process `exec` 并可访问原始环境，不符合本项目安全边界；源码控制流还
  显示 harness 自己的 `_exec_user_code` 会捕获 `BaseException`，可能先把 watchdog interrupt
  降级为普通 `{ok: false}`，而外层 runner 才期待捕获 interrupt。生成程序自行捕获只是额外风险，
  不是本次直接证据的归因对象。

所以本研究选择移植**原则**：冻结评估身份、trace provenance、资格晋升、held-out 冻结、
bounded evaluation；没有复制 raw `exec`、prompt 充当状态机或未经绑定的 logger。逐文件证据见
`upstream_source_audit.md`。

## 5. 本地复现

### 5.1 已复现

官方仓库以 submodule 固定在 `7ba73d3...`。在普通宿主环境运行选定的无服务测试得到
18 passed / 4 skipped / 2 failed；两个 failure 精确来自 NumPy/SciPy 版本门。随后在研究 artifact
下建立最小 Python 3.12 环境，安装官方锁定的 NumPy 1.26.4 与 SciPy 1.15.3，原测试不改动得到：

```text
20 passed, 4 skipped, 0 failed
```

四个 skip 明确对应 PyRoKi、Contact-GraspNet，以及需要 `ASPIRE_INTEGRATION_REAL=1` 与真实服务
的 LIBERO/Robosuite integration。该结果只复现 generation progress、validation
identity/progress、skill-promotion receipt 和 dependency contract 等无服务机制。

### 5.2 未复现及原因

官方 Goal-Swap Fix Loop 要求八张 GPU：SAM3、GraspNet、PyRoKi 和五个 task slots，还要求三个
服务、gated 权重、模型 endpoint、专用环境/嵌套子模块，以及固定 development seeds 51–65 与
held-out seeds 1–50。本机只有一张 RTX 5090，且完整服务、权重凭据和环境身份不具备。

因此没有把任务缩小后冒充论文复现，也没有启动不可比较的昂贵 rollout。详细命令、JUnit receipt
和 skip 边界见 `reproduction_report.md`。

## 6. 当前项目的 harness 基线

E0–E2 审计快照 `1180aef` 中，当前项目的优势在可信编译/验证核心：严格 `SceneSpec`、确定性 grounding/solver、target-local
support 与 containment、哈希绑定 package、连续 runtime contact/settling/video gate、攻击测试。
渲染和 VLM 明确不是物理证据。

主要缺口在 orchestration：

- 该快照的 `self_improving/harness/` 是 14-schema tranche，没有 Registry、handlers、MCP adapter
  或 retry executor；
- Stage 5 是有界 repair prototype，默认 1 attempt / 0 repair，smoke 只验证 load/render；
- failure memory 当前主要是单 RGB adapter，选择只匹配 task/checkpoint，未把 camera source 纳入
  memory identity；
- 12-case failure-score 相关性为 `r=-0.2545, p=.7273`，不能作为 selector 真值；
- 研究开始时 runtime evidence digest 未被 validator 消费，pending visual review 会被写成
  final/pass；这两项已在本研究中修复。

完整源码边界、八个量化 gap 和三个 matched protocol 见 `current_harness_audit.md`。

研究收尾期间，另一个并发实现轨道随后合入 `f5d1bab` 的本地 artifact resolver、`5ccf779` 的
`RunRecorder`/event sink、`d5aa185` 的 deterministic compiler module、`567969e` 的生成资产准入、
`568c3b6` 的通用 `SkillRegistry`、`315524f` 的 SQLite event journal、`9af9db5` 的实际 compile
stage events，以及 `28333de` 的 effective-catalog 文件校验。它们不是 E0–E2 的实验 intervention，
不能倒灌进基线解释；在已验证的 `28333de` 快照中仍无 Text2Env compile/replay/validate handlers
或 MCP adapter。其中 resolver
对 `file://` 没有 allowed-root，校验后返回可变原路径，存在 trusted-input 与 TOCTOU 边界；它不是
sandbox 或不可变 capability。生成资产准入回执虽写 `physical_qualification=pending_settle`，其
ledger 仍把未运行的 generation QC 记为 `backend=sapien/verdict=pass`；因此该 follow-on 尚不能
作为物理资格证据，也不属于本研究保留结论。

`284ffb` 后已有首个 compile handler，`1a1f3d8`/`e98e8c1`/`5915315` 又加入 CAS package
materialization 与 parameter-aware compile dependencies；但 replay/validate/MCP 仍缺。定向攻击曾
证明 handler 消费原始可变 catalog 而非已复制的 CAS snapshot；E1e 在 `910ccb1` 将 trust check 与
compiler 输入都切到同一个 digest-verified snapshot，冻结攻击从 fail 变 pass。另一个边界仍未修：
asset admission 在后续 solve 失败时不回滚。因此这些新 seam 已闭合 compile input 的 check/use
身份，却还没有闭合可信 compile→replay→validate spine；详见 §8 的收尾快照边界。

随后 `50e8f18` 还关闭了 CAS `put_file` 的 pre-hash/copy race，`51447da` 要求 qualification
`report_sha256` 的 CAS bytes 真正存在。后者只证明报告 bytes 可取且摘要匹配，不验证报告内容、
source manifest 或实际 handler implementation；不能把它写成完整 qualification attestation。

## 7. 实验与结果

所有实验先冻结 protocol/keep rule，再看结果。完整时间线在 `research_log.md`，机读摘要在
`results.tsv` 和 `source_receipt.json`。

### 7.1 E0：前置基线

- 顶层：125 passed；
- 完整 self-improving matrix：研究开始时 591 passed / 6 skipped；
- 真实 RoboTwin catalog：127 entries、15 available；
- real-catalog prompt matrix 静态编译：33/33；
- can-on-plate 与 apple-in-basket 各 100 seeds 静态求解：100/100。

静态编译已无 headroom，因此后续不得用“仍然 100/100”证明 ASPIRE 改善物理鲁棒性。

### 7.2 E1：runtime evidence 自声明身份一致性

冻结探针含一个 valid control 和四个 mutation：缺 scene id、错 scene id、缺 digest、错 digest。

| 状态 | accuracy | unsafe accepts |
| --- | ---: | ---: |
| 修复前 `113e54b` | 0.20 | 4/4 |
| 修复后 `795273f` | 1.00 | 0/4 |

同一 valid control 继续通过。修复增加 `runtime_scene_identity` 与
`runtime_resolved_scene_binding` 两个 check，没有修改物理阈值。它只拒绝缺失或未改写的错值：
validator 没有对整份 evidence、媒体、执行命令或环境签名，也没有在这次真实回放中重验 package
manifest。错误或恶意 producer 可以搬运别次物理字段并重写两项声明；因此这不是不可伪造的
provenance receipt。

随后在 `robotwin-5090` 环境做真实 can-on-plate seed 7 回放：900 settle steps、120 contact window、
120 video frames；结果 pass、0 fail、0 not-run、100 unique frames，can 对 plate contact fraction 1.0，
support margin 9.69 mm ≥ 8 mm。它证明 runner 写出的 declared identity 能通过 equality gate，
不证明完整 package→run→media provenance，也不证明 ASPIRE repair 提高了物理成功率。该 artifact
缺 exact command、resolved/package manifest、RoboTwin commit 和 env lock，不能单独重建整条链。

### 7.3 E1b：pending review 状态完整性

冻结探针检查五个决策：pending 不得使用 final stage、`accept_final`、`scene_critic=pass`、
`pass_visual_review` 或 exit 0；genuine pass 控制必须保持原行为。

| 状态 | pending accuracy | pass control |
| --- | ---: | ---: |
| 修复前 `bbfa19d` | 0/5 | 5/5 |
| 修复后 `fa9121f` | 5/5 | 5/5 |

真实 legacy Stage 5 smoke 尝试在进入视觉评审前被 RoboTwin 目录漂移阻断：旧脚本寻找
`RoboTwin/task_config/demo_smoke.yml`，当前 checkout 使用 `env_cfg/task_config/`。该失败保留为
compatibility blocker，没有被 mocked state test 冒充真实 e2e。

### 7.3.1 E1c：其余 active pending 入口

最终源码审计发现 E1b 的 5-case 探针只覆盖主 scene-generation helper。冻结 E1c 后，standalone
Scene Critic 对 pending 仍退出 0，batch 在显式收集 pending 后仍聚合成 `pass`/exit 0；八项 probe
accuracy 为 0.625。`bd4f64f` 将两者改为 review-required exit 2，batch 增加 publishable/review
计数；同一 probe 变为 1.00，pass controls 不变。

旧 placement compatibility 入口还曾在视觉决定前写 `final_placement.json`，即使进程随后崩溃也
会泄露 transient final。`fda13b8` 改为始终从 candidate artifact 开始，只有 visual pass 才逐文件
原子 rename/promotion；pending、视觉前 crash 与 pass 三条测试都锁住。这里不是跨多个 artifact
的事务。E1c 是 E1b 之后的独立 follow-on，不能追溯算进原 5-case 结果。

### 7.3.2 E1d：默认 static-only 状态完整性

进一步的默认路径审计发现：主入口的 `--run-smoke` 是 opt-in；未传时却调用 final helper，写出
`final_placement.json`、`final_render_accepted`、`accept_final`、`pass_smoke` 与
`pass_visual_review`。这意味着**默认命令**会把未运行的 smoke/视觉评审伪装为已通过。

E1d 在改动前冻结九项决策。基线 `52fef1e` 仅 exit 0 与兼容 status 两项正确，accuracy 为
`2/9 = 0.2222`；`72b35c4` 后同一 probe 为 `9/9 = 1.00`。现在默认路径写
`static_scene_candidate_placement.json`，stage=`static_scene_candidate`、decision=`render_next`，
smoke/visual 都是 `not_run`，不再出现 final key/file。exit 0 只表示静态阶段完成；它不是视觉或
物理 acceptance。focused Stage 5 状态测试为 10 passed。该结果与 E1c 分开预注册、分开留痕。

### 7.3.3 E1e：compile catalog snapshot/use 一致性

并发新增的 compile handler 会先把 input catalog 复制进 CAS，但 `284ffb` 版本随后仍从原始 locator
做 trust check 与 `compile_scene`。冻结攻击从一个 digest-valid、路径均在 allowed root 内的 catalog
开始，在 check/use 间只改写原文件为指向 root 外资产的另一份合法 catalog；旧实现 run 仍
`succeeded`，resolved `source_files` 来自 root 外，mutation guard 为 0/1。

测试与协议先在 `ef5e29e` 冻结；生产修复 `910ccb1` 在核对 snapshot digest 后重新解析 CAS ref，
后续 trust check、parse/solve/package 都只消费该 snapshot，原 locator 仅留作审计别名。同一攻击
变为 1/1：compile 成功，但 resolved 只含攻击前 allowed-root bytes。完整 Harness 为 74 passed，
1291/1291 statements、312/312 branches（100%）。因此 E1e 满足 keep rule，且没有放松任何
scene/runtime validator。

这仍不是完整 transaction。另一个强制 solve-failure 攻击实证显示：generated asset 在 solve 前已
写入 library，run 随后 `blocked/T2E_SOLVER_EXHAUSTED` 时不会回滚。该 P1 需要 stage–commit–abort
设计；不能在 handler exception 中盲删共享目录。ledger 把未跑 settle 的 QC 写作 `sapien/pass` 的
矛盾也仍存在。

### 7.4 E2：held-out validated-memory benchmark

六类故障：target-local support、containment、dynamic nested contact、runtime completeness、identity
binding、bounded workspace feasibility。第六族只把 pose 投影回 workspace，**没有**生成或验证
derived proxy。development 每类 2 fault + 2 clean；held-out 每类 20 fault + matching clean，case ID
与 seed offset 无交集。variant 标签部分复用：containment/feasibility 的 dev variant 集与 held-out
相同，其余家族也有重叠，因此 E2 不支持 variant-space 泛化。held-out 期间 skill library hash 前后
一致；冻结 receipt 只保存 case-ID intersection，没有冻结 digest-set intersection。

三臂预算相同：每 case 最多 1 action、2 validations。

| Arm | held-out mHRC | worst family | unsafe publish | clean regression | binding accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| frozen retry | 0.00 | 0.00 | 0 | 0 | 1.00 |
| reactive trace / no memory | 0.50 | 0.00 | 0 | 0 | 1.00 |
| development-validated memory | 1.00 | 1.00 | 0 | 0 | 1.00 |

memory − reactive 为 +0.50。固定 seed、10,000 次 family-stratified paired bootstrap 得到
95% CI `[0.50, 0.50]`，paired improved/regressed/tied 为 60/0/60。区间退化是因为固定合成家族
内 paired delta 完全相同；它**不能估计 simulator 或部署分布不确定性**。因此按预注册的
meaningful-interval 限制，本报告只陈述“这 120 个固定 cases 上 completion 是 1.00 vs 0.50”，
不作统计总体或分布外 superiority 主张。

最终审计还发现一项协议偏差：`experiment_protocol.md` 预注册了只作诊断上限的 Oracle ceiling，
但实现和结果 artifact 只有 B0/B1/H 三个 deployable arms，Oracle 没有实例化，也没有在执行前
记录省略原因。该遗漏不改变 H−B1 的逐 case 数值，且 H 已达到 1.00，但意味着 E2 没有完整执行
所有预注册报告项。这里如实保留偏差，不在看过结果后补造理由或把 post-hoc ceiling 冒充原实验。

结果 JSON 连续两次 byte-identical；其中排除自指字段后计算的 canonical `result_sha256` 为
`58b7fe73b6cef35d8259a0fae165648046ba49385e126a329aafd1c5420f2c99`，当前格式化 artifact 文件
SHA-256 为 `5a3d8c2d8decad3c456b054809ed43877486a17c690c07beed21dfa0a34f967b`。machine verdict 是
`keep_synthetic_harness_mechanism_candidate`，并显式把 paper-scale support 与 simulator-physics
improvement 标为 false。

## 8. 保留的生产改动

| Commit | 改动 | 为什么保留 |
| --- | --- | --- |
| `795273f` | runtime evidence declared scene/digest equality | 冻结攻击集 4/4 unsafe → 0/4，valid control 与 runner 自声明字段通过；非完整 provenance |
| `00e4ae9` | 历史正面 fixtures 补齐真实绑定字段 | 让旧 fixture 不再绕过新契约 |
| `fa9121f` | pending review 独立 candidate、exit 2、batch opt-in | 冻结状态探针 0/5 → 5/5，pass control 不回归 |
| `8c1bc6b` | pending artifact path e2e state test | 锁住无 `final_placement` 的真实落盘语义 |
| `bd4f64f` | critic/batch pending exit 与 aggregate | E1c 8-check probe 0.625 → 1.00，pass controls 不变 |
| `fda13b8` | legacy candidate-first + pass-only promotion | pending、视觉前 crash、pass 三条 artifact-state 路径通过 |
| `52fef1e` / `ed5197b` | E1d 预注册与 probe 生命周期修正 | 生产改动前冻结九项判据；修正只让 probe 在临时目录销毁前取样，不改预期 |
| `72b35c4` | 默认 static-only 输出改为 nonfinal candidate | 冻结 probe 2/9 → 9/9；focused Stage 5 10 passed |
| `ef5e29e` / `910ccb1` | E1e catalog mutation attack + snapshot-only execution | check/use mutation 0/1 → 1/1；Harness 74 passed、statement/branch 100% |
| `9540dd3` | held-out harness mechanism benchmark | 可重复、隔离标签、冻结库、与现有 validator 对接 |

E0–E2 快照 `1180aef` 的回归是：顶层 125 passed；完整 self-improving matrix 595 passed /
6 skipped；Harness 350/350 statements、74/74 branches；E2 自测 8 passed。该数字早于 E1c/E1d
和并发 Harness follow-on，不冒充最终 HEAD 全量验证；E1c/E1d 各自的 focused probe/test 证据如上。

在后续历史源码快照 `f9d6ad5` 上，默认顶层 pytest 与平台脚本都因两个不同目录的
`test_registry.py` 被导入为同名模块而 collection error；诊断性使用
`--import-mode=importlib` 后是 145 passed / 1 failed，真实 failure 是 compiler 将非法 prompt 的
failure stage 从历史契约 `scene_spec_validation` 改成 `parse`。同一轮 Harness coverage 为
99.89%（少一条 branch），未达到 100% gate。因此该快照的全量回归不是绿色，不能沿用
`1180aef` 数字。ASPIRE 启发的本研究 E1/E1b/E1c/E1d probes 均为 accuracy 1.00，E2 8 passed
且完整输出与冻结 artifact byte-identical；这些专项通过不抵消全仓回归。

随后又把只读验证锚冻结在干净的 `28333de` archive，而不是继续追逐移动中的共享分支。默认
pytest 仍因同名 `test_registry.py` collection error；诊断性 `--import-mode=importlib` 为
155 passed / 1 failed，唯一 failure 仍是非法 prompt 的 stage=`parse` 与历史
`scene_spec_validation` 契约不一致。该快照 Harness 自身已达到 895/895 statements、196/196
branches（100%），所以 f9 的 branch-coverage blocker 已消失，但 collection 与行为契约仍未闭合。
`28333de` 之后观察到的 `dc81ade` artifact-dedup 修复以及当时未提交的 Text2Env handler TDD 没有进入上述
验证。随后 `284ffb` 已提交首个 `Text2EnvCompileHandler`：它可通过通用 Registry 调用、检查
catalog 路径并把产物复制进本地 CAS、复核 package bindings。干净归档上的 focused handler 测试为
10 passed；同一提交上默认顶层 pytest 与平台脚本仍因同名 `test_registry.py` collection error 而
退出 2。额外攻击审计又证伪了两个更强说法：handler 虽复制 input catalog，却继续从原始可变路径
做 trust check 与 compile，precheck 后替换 catalog 可让 root 外资产进入 resolved 输出；生成资产
又在 solve 前 admission，后续 solve blocked 时 library side effect 不回滚。

`1a1f3d8` 的 CAS package materialization、`e98e8c1`/`5915315` 的 parameter-aware dependency
binding 是更晚的独立 follow-on。E1e 在 `ef5e29e` 先冻结 catalog mutation attack，再由
`910ccb1` 把实际 compiler 输入切到 digest-verified snapshot；攻击转绿，完整 Harness 74 passed、
statement/branch 100%。因此 catalog TOCTOU 已按 keep rule 修复；asset admission transaction、
ledger 假物理语义、replay/validate/MCP 仍未闭合。E1e 是实验后的独立 follow-on，不能倒灌解释
E0–E2；它的 before/after 证据单列保留。

`50e8f18`/`51447da` 又用攻击测试修复 CAS 捕获一致性和 qualification-report 可达性。独立的干净
`51447da` archive 复核纠正了实现日志的 100% coverage 说法：Harness 测试本身是 76 passed，
但 1313 条 statements 缺 1、322 条 branches 有 1 partial，总 coverage 99.88%，100% gate 失败；
缺口是 `text2env_compile_dependencies.py:154` 的 bytecode/cache 排除分支。默认根 pytest 与完成
submodule 初始化后的平台脚本都仍在两个同名 `test_registry.py` 上 collection error；诊断性
`--import-mode=importlib` 为 184 passed / 1 failed，唯一 failure 仍是 `parse` 对历史
`scene_spec_validation` 的行为契约漂移。这些是 E1e 后的独立 source-hardening follow-ons，既不抵消
默认全仓 blockers，也不证明报告内容或 handler implementation 得到完整对账。

在它之后，独立 follow-on `e6ed0ff` 增加 adaptive settle 的视频终点绑定，`148001d` 在 CLI 投影层
恢复历史 `scene_spec_validation` failure stage。对固定 `148001d` clean archive 的独立复核为：
默认 pytest 仍因同名测试 collection error；`--import-mode=importlib` 已变为 201 passed / 0 failed，
说明 CLI 行为漂移已闭合；Harness 仍是 76 tests 通过但 coverage 99.88%，同一 branch gate 未闭合。
所以最新固定源码诊断还剩两个 blocker，而不是三个。`e6ed0ff` 提交中的 SAPIEN timeline smoke 属于
独立实现轨道，本研究没有把它重算成 N1 matched repair trial，也不据此改变 ASPIRE 结论。

报告封存过程中，另一个并发轨道又落地 `4836ebf` 的 qualification bundle loader、`38518e5` 的
wheel packaging 修正和 `ab03859` 的 SQLite RunStore。固定 `ab03859` clean archive 上，Harness
133 tests 通过，但 1673 statements 缺 1、436 branches 有 1 partial，总 coverage 99.91%，默认根
pytest 仍因同名 `test_registry.py` collection error；因此这个 post-close 快照也不是绿色全仓。

源码审计把三项 P0 继续标为 **OPEN**：qualification loader 会严格复核一份预制 `pass` bundle 的
receipt/report/manifest、已列 implementation files 与两个源码树，但不执行 development fault + clean
controls，Registry 在该提交还没有调用 loader，两个 CAS publish 也不是带 rollback 的 promotion
transaction；implementation root 中未被 manifest 列出的 helper 完整性尚未证明。`38518e5` 只修复
ledger wheel 依赖并声明未来资源 glob，树中没有真实 qualification bundle。RunStore 能不可变、幂等地
保存 Invocation 与 terminal RunState 并对账 event journal，但未接入 Registry，接受调用者提供的
invocation digest，不支持 resume，也不重验 artifact bytes。ReplayOutput 仍没有 package/run 反向
绑定，媒体甚至可无 schema。因此这些是可复用前置构件，不是 identity-frozen EvaluationRun、
qualification/promotion transaction 或 package→run→media spine 已完成。

共享分支在该固定审计之后继续前进；封存时只观察到 `c365874`、`8d9a01c`、`587b49f`、`0e6716a`
四个后续 Harness commit 及尚未提交的 compile-CLI TDD。本研究没有追逐、测试或解释这些后来状态，
所以“OPEN”结论精确限定在 `ab03859`，既不把移动 HEAD 冒充已验证，也不声称后来提交必然没有关闭
缺口。

## 9. 对三类问题的最终回答

| 问题 | 判断 | 证据强度 |
| --- | --- | --- |
| 能否改善 agent harness？ | **ASPIRE 启发的审计已促成本地声明一致性、非终态完整性与 compile snapshot-use 修复；尚未证明 ASPIRE 全系统收益。** | 本地缺陷证据强：冻结攻击集与 focused 回归；identity gate 仍不是防篡改 provenance |
| 能否改善 robust 仿真环境生成？ | **未知。** 当前只观察到固定 synthetic contract proxy 的按设计行为和一次 declared-identity integration observation。 | 不足：缺 matched multi-seed physical rollouts 与完整 run receipt |
| 能否形成 self-improving / 学习范式？ | **可形成非梯度的“验证后程序/技能记忆”候选闭环；尚未证明持续学习。** | 中低：合成 held-out 正结果，无 LLM/真实 rollout、无长期负迁移测量 |

这里的“学习”是跨 case 保留经过资格门的程序知识，不是权重训练。若未来没有对新任务的可测
迁移、或引入 memory 后负迁移上升，就应判定该范式在本项目无效。

## 10. 推荐的最小架构

### P0：先建立可信 harness spine

1. **Identity-frozen EvaluationRun**：内容寻址地绑定 candidate code/spec、resolved scene、catalog、
   dependency lock、sim config、seed/split、gate profile、runtime evidence、媒体与执行命令；支持安全
   resume，但身份变化必须新建 run。consumer 必须重验 package/run/media manifest，而不是只信两项
   producer 自声明字段。
2. **Typed streaming trace**：事件序列号、单调时间、attempt、action、observation artifact digest、
   runtime check 与 terminal reason；分段原子落盘，结束时重算 manifest hash。
3. **Qualification/Promotion transaction**：技能候选必须在 development fault + clean controls 通过，
   记录适用 trigger、反例、来源 run、版本与 hash；原子 promotion、可 rollback、held-out 只读。
4. **Bounded executor**：进程/容器级超时、资源限额、文件与网络 allowlist；不使用上游 raw in-process
   `exec`，生成代码不能捕获 harness 的 hard timeout。
5. **Matched evaluator**：固定 task/checkpoint/seed/config/budget；先过不可修改的核心 validator，再
   比较 frozen、reactive、validated-memory、多候选 search；infrastructure failure 与 task failure 分离。

### P1：再增加能力

- memory retrieval 必须匹配 simulator、camera source、task family、checkpoint 和 gate profile；
- trace compression 只保存可复用摘要，同时保留原始 artifact digest 供追溯；
- 多候选搜索需记录 parentage、mutation、selection reason 和 survivor set，不能只在 prompt 里声称
  evolutionary；
- failure miner 只能建议下一实验，不可覆盖 runtime/task success 真值；
- skills 要有过期、冲突、negative transfer、abstention 和 rollback 指标。

## 11. 明确不应移植的部分

- raw in-process `exec` 与原始 env 暴露；
- 用 prompt/runbook 代替类型化 coordinator 状态机；
- 不重算内容 hash 的 promotion receipt；
- 静默吞保存错误、未绑定输入身份的 TraceLogger；
- 自动把当前 case 的修复写进长期 memory；
- 用 execution non-crash、render/VLM 或 agent 自评替代 task/runtime success；
- 把论文数值或本次合成结果当作当前项目物理提升证据。

## 12. 下一轮可证伪实验

### N1：真实 SAPIEN matched repair trial（最高优先级）

选择当前真实 catalog 可用的 support/containment task families，固定 resolved-scene generator、seed
集合、RoboTwin commit、物理配置和每 case rollout/repair budget。比较 reactive 与冻结
development-validated memory；held-out seeds 期间禁止修改 library。

主指标是权威 runtime pass rate 与 worst-family pass；同时报告 unsafe publish、clean regression、
mean attempts、infrastructure failure。只有 memory − reactive 的预注册 CI 高于 0、零 unsafe、无
worst-family 回归，才能声称物理鲁棒性候选成立。

### N2：LLM repair 与 negative-transfer trial

让两臂使用同一模型/checkpoint/token/tool budget。输入只给 typed trace，不暴露 family/expected
action。报告 retrieval precision、wrong retrieval、abstention、negative transfer、任务成功率和成本。
若 memory 只减少 token、不提高成功，也应如实归类为效率提升而非鲁棒性提升。

### N3：长期 skill lifecycle

跨版本 simulator/camera/task 执行旧技能，验证 qualification 是否会因 dependency/gate profile 改变而
失效，promotion ledger 能否检测 stale skill 并 rollback。若旧技能在 clean controls 或新版本产生
回归，必须自动降级而不是继续累积。

### N4：完整 ASPIRE 复现

仅在八 GPU、三服务、gated weights、专用环境、固定 split/model/budget 全部满足后运行官方 runbook。
先保存内容寻址的原始输出，再重算作者表格。设施不全时继续保持“未复现”，不做缩小版等价主张。

## 13. 证据索引与追溯

| 证据 | 文件 |
| --- | --- |
| 逐步决策、失败和命令时间线 | `research_log.md` |
| 论文与官方一手资料 | `paper_and_official_sources.md` |
| 固定上游源码审计 | `upstream_source_audit.md` |
| 当前项目 harness 审计 | `current_harness_audit.md` |
| 主机/依赖/服务前置条件 | `preflight.md` |
| 本地复现边界与 JUnit receipt | `reproduction_report.md`、`artifacts/upstream_selected_tests*.xml` |
| 预注册假设、指标与 keep rules | `experiment_protocol.md` |
| 逐实验机读摘要 | `results.tsv`、`source_receipt.json` |
| E1 before/after | `artifacts/runtime_binding_before.json`、`runtime_binding_after.json` |
| E1 真实回放 | `artifacts/real_replay_can_on_plate_seed7/` |
| E1b before/after | `artifacts/pending_review_before.json`、`pending_review_after.json` |
| E1c before/after | `artifacts/pending_entrypoints_before.json`、`pending_entrypoints_after.json` |
| E1d before/after | `artifacts/static_only_state_before.json`、`static_only_state_after.json` |
| E1e frozen attack/fix | `experiment_protocol.md`、`tests/self_improving/harness/test_text2env_compile_handler.py`、`ef5e29e`/`910ccb1` |
| E2 完整结果 | `artifacts/heldout_harness_benchmark.json` |

## 14. 最终 claim boundary

本研究实证支持：ASPIRE 启发的审计发现了本地声明一致性、非终态状态与 compile snapshot-use
缺陷，修复后对应冻结
攻击探针不再接受未改写的缺失/错配声明，也不再把 active-path pending 终态或 batch aggregate
写成 final/pass，不再把默认 static-only 伪装成 smoke/visual pass，compile 也不再在冻结 catalog
后回读可变 locator；在固定、合成、case ID/seed
未见但 variant 类型部分复用的 120 个契约故障 cases 上，
经开发集资格门后冻结的 trigger memory completion 为 1.00，current-trace-only repair 为 0.50。
该描述不外推到 ASPIRE 全系统、统计总体或部署分布。

本研究不支持：ASPIRE 论文结果被完整复现；本项目物理环境成功率提高；LLM 或机器人策略发生
权重学习；memory 在开放分布长期无负迁移；真实机器人或 sim-to-real 得到改善。上述任一主张都
必须由下一轮 matched physical/robot rollout 单独建立，不能由本报告外推。
