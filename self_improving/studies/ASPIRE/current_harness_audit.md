# 当前 Agent Harness / 鲁棒环境生成 / Self-Improving 实现审计

> 审计日期：2026-08-31
> 审计对象：当前分支的 `self_improving/harness/`、`self_improving/alchedata/`、
> `self_improving/stage5/`、`scene_gen/` 及对应测试和 `repo-docs/`。
> 约束：本文件只陈述本地仓库可复核事实；不根据名称猜测 ASPIRE 的实现。
> 下文的“ASPIRE 接入点”只是待论文与外部代码轨道验证的接口假设，不是 ASPIRE 能力结论。
> 时间锚：§0–§10 描述研究起点 `732e190` 到实验快照 `1180aef` 的 point-in-time 状态；
> 后续同分支实现与本研究处置只在 §11 追加，不能倒灌改写基线。

## 0. 结论先行

当前项目不是“缺一个 agent 就能闭环”的状态，而是已有四块成熟度不同的能力：

1. `scene_gen/` 已经是一条较深的确定性信任链：受限语义输入、目标局部几何、
   哈希绑定回放包、连续 SAPIEN 采样和 fail-closed 物理门控；它必须继续是验收权威。
   包构建会拒绝未绑定的 `SceneSpec` / `ResolvedSceneSpec`，并对包内文件重算摘要；
   运行时 validator 对接触比例、未声明接触、containment、掉落、穿透、稳定性、可见性和
   视频互异帧逐项判定。证据：`scene_gen/builder.py:39-71`、
   `scene_gen/builder.py:74-105`、`scene_gen/validator.py:282-315`、
   `scene_gen/validator.py:315-525`。
2. 在研究起点，`self_improving/harness/` 是严格的 **schema tranche**，不是可执行 agent harness。
   它冻结了 14 个公共 schema、运行状态机、artifact 引用和 Text2Env
   compile/replay/validate 边界，但 Registry、handler、MCP adapter 和真实 retry 尚未实现。
   证据：`repo-docs/modules/harness-schema-tranche.md:1-14`、
   `repo-docs/modules/harness-schema-tranche.md:174-187`。
3. `self_improving/stage5/` 有一个可运行的“设计—静态 critic—smoke—视觉 critic—修复”
   原型循环，但其 smoke 明确只证明加载/渲染，不能替代 `scene_gen` 的物理门控。
   证据：`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:243-286`、
   `self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:326-425`、
   `self_improving/stage5/generate_scene/run_robotwin_placement_smoke.py:245-271`。
4. `self_improving/alchedata/` 已经保存若干有界的 closed-loop 科学证据：固定 checkpoint
   RGB adapter 因果消融、单条失败记忆消融、placement failure-to-data 迭代和拒绝晋升记录。
   它们证明“受控 harness 改动可以被测量”，尚未证明通用的自动弱点聚类、技能复用、
   跨失败类记忆或广泛鲁棒自改进。证据：
   `self_improving/alchedata/scripts/build_harness_causal_ablation.py:74-177`、
   `self_improving/alchedata/scripts/build_text2env_memory_ablation.py:33-80`、
   `self_improving/alchedata/scripts/build_placement_robustness_diagnosis.py:186-275`。

因此，外部 agent 系统若要帮助本项目，最合适的角色是 **诊断、候选补丁、经验检索与
有界重试控制器**；它不能成为新的物理真值源，也不能直接把 render/smoke/VLM 结果升级成
publishable，更不能在同一个实验臂里同时改 checkpoint、场景分布和 harness。

## 1. 第一性原理：这里的 harness 到底要控制什么

### 1.1 被优化对象与可归因干预必须分开

本项目自己的 harness spec 把“被测对象”定义为：固定版本的模型或策略 checkpoint，运行在
声明的 scene、task、embodiment 和 simulator/robot adapter 上；可变化的 harness 则包括 prompt、
tool、adapter、reset/evaluation protocol、validator、memory、data requirement、promotion rule 和
rollback pointer。固定 checkpoint、seed、task set 与环境条件是 harness-only 因果归因的前提；
policy retraining 必须作为另一种干预单独报告。证据：
`self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:4-27`。

从这个定义可推出三个不可省略的量：

- **状态**：当前 active harness 是谁、父版本是谁、允许改哪些 surface；对应 spec 中的
  `h_t`、candidate 和 rollback。证据：
  `self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:30-38`、
  `self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:103-110`。
- **可观测反馈**：不能只有总 success score，必须保留运行、接触、视觉、动作、verifier 和
  不可解析状态；对应 weakness mining 与 provenance-preserving cluster gate。证据：
  `self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:40-65`。
- **受控选择**：candidate 只有在 matched regression、safety、robustness 与 provenance gate
  均满足后才能成为 `h_{t+1}`；拒绝结果仍是证据。证据：
  `self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:85-110`。

### 1.2 “程序跑完”与“任务成功/环境可发布”是不同随机变量

公共 Harness 状态机明确区分 handler 的 `succeeded/blocked/failed` 与 validator 的
`pass/incomplete/fail`；一个 validate handler 可以正常 `succeeded`，同时返回“场景不能发布”。
代码中 `RunStatus` 与 `ValidationStatus` 是两套 enum，`Text2EnvValidateOutput` 只有在
`validation_status=pass` 且 blocker 为空时才允许 `publishable=true`。证据：
`self_improving/harness/schemas/common.py:37-47`、
`self_improving/harness/schemas/text2env.py:172-194`。

Alchedata evaluator 也做了同样分离：`pass_generated_act_evaluate_execution` 仅表示所有 episode
执行基础设施完成，任务成败另由 `success_count`、`policy_success_rate` 和 `policy_result` 表达。
证据：`self_improving/alchedata/scripts/run_generated_act_eval_smoke.py:781-801`。

这条分离原则是任何 agent-harness 接入的首要约束：agent 调用成功、代码生成成功、仿真进程
退出码为 0、smoke 可加载、VLM 说“看起来对”，都不能被折叠成物理通过或 policy success。

## 2. 当前分层与真实职责

| 层 | 当前真实职责 | 已实现的闭环部分 | 明确不拥有的权力 |
| --- | --- | --- | --- |
| `scene_gen/` | 受限文本到 resolved scene、包、真实回放与物理 gate | compile、replay、validate | policy 训练、agent repair、promotion orchestration |
| `self_improving/harness/`（研究起点） | 严格、冻结、可审计的公共类型 | 状态/事件/attempt/artifact/publishability 的形状 | Skill 执行、URI 内容验证、retry controller、MCP |
| `self_improving/stage5/` | designer/critic/orchestrator 原型与 render-in-loop | 有界 placement 修复循环 | 核心物理验收的替代实现 |
| `self_improving/alchedata/` | collect/train/evaluate/diagnose/transfer 证据、记忆与 promotion gate | 若干任务特定闭环和消融 | 未经匹配实验的通用 self-improvement 结论 |

这不是解释性重分层，而是仓库自己声明的边界：稳定核心、Harness 契约、Stage5 场景编排与
Alchedata 闭环分别归属上述目录，平台消费者不得绕过 `scene_gen` 的物理门控。证据：
`repo-docs/modules/self-improving-platform.md:1-18`。

## 3. 现有状态机与闭环

### 3.1 公共 Harness 基线：严格记录状态机，但还没有执行引擎

`RunState` 的合法生命周期是：

```text
null -> running -> succeeded | blocked | failed
```

首事件必须是 `null -> running`；事件 seq 连续、时间不倒退；只有 `running` 可以继续追加事件；
attempt 只能保持或加一，且不得超过 `max_attempts`；进入 terminal 后不能复活。`succeeded`
必须有 output 且无 blocker，`blocked/failed` 必须有 blocker 且无 output。证据：
`self_improving/harness/schemas/common.py:156-227`。

失败记录不是裸异常：`Blocker` 带 `code/message/stage/retryable/details/unknowns/artifact_refs`，
而 `Event` 带 seq、时间、stage、attempt、状态转换和 artifact refs。证据：
`self_improving/harness/schemas/common.py:73-95`。

但它目前只能校验记录，不能产生记录。仓库指南明确列出尚缺 descriptor 组装、qualification
内容验证、invocation digest、artifact 解引用、第二次 replay attempt、三个 handler 和 MCP adapter。
证据：`repo-docs/modules/harness-schema-tranche.md:174-187`。

### 3.2 Stage5：实际有界 repair loop

当前 Stage5 的实际控制流是：

```text
grounding
  ├─ fail -> fail_asset_grounding
  └─ pass -> initial design
       -> [attempt i]
          -> static validation
             ├─ fail -> scene critic: fail_preflight
             │          ├─ LLM + budget -> repair spec -> attempt i+1
             │          └─ otherwise -> fail_static_preflight
             └─ pass -> codegen
                  ├─ no smoke requested -> pass_static_scene_module
                  └─ smoke -> visual review -> unified scene critic
                       ├─ pass -> accept_final
                       ├─ pending -> hold_for_review（当前实现仍写 final artifact）
                       ├─ fail_smoke / repair_required -> repair if budget remains
                       └─ budget exhausted -> fail/repair_required
```

grounding fail-fast 与静态 preflight repair 的实现证据在
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:168-192`、
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:243-286`；
smoke/visual/critic/repair 的实现证据在
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:326-425`。

统一 critic 的失败分类是 `fail_preflight`、`fail_smoke`、`pending_visual_review`、
`repair_required`、`pass_preflight` 和 `pass`；它把静态失败、smoke 失败与视觉失败分别写成
issue source 和 repair suggestions。证据：
`self_improving/stage5/generate_scene/scene_critic.py:91-175`。

这个循环有两个重要的定量事实：

- CLI 默认 `--visual-repair-attempts=0`，`max_attempts = visual_repair_attempts + 1`，所以默认只有
  **1 次生成/评判尝试、0 次修复**。证据：
  `self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:126-131`、
  `self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:236-243`。
- repair 函数只接收当前 placement、当前 critic report 与 catalog；pipeline 虽保留 attempts
  列表，却没有把历史尝试、相似失败或已验证技能传给 repair agent。证据：
  `self_improving/stage5/generate_scene/gpt_agent.py:466-521`、
  `self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:150-164`。

### 3.3 PEARL / Alchedata：九步科学闭环是规范，执行仍是多段脚本

规范中的闭环完整列出 `h_t -> embodied execution -> weakness mining -> clustered failures ->
harness proposal -> proposed edits -> regression -> promotion -> h_{t+1}`，并为每步声明输入、输出、
gate 与 evidence path。证据：
`self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:30-110`。

`scripts/embodied_harness.py` 的真实行为是读取 spec/audit/ablation，验证 JSON Schema、必需表面、
证据路径、报告 hash、proof obligation 与文档标题，然后返回一个汇总；它不是运行九步循环的
orchestrator。证据：`self_improving/alchedata/scripts/embodied_harness.py:140-250`。

已经执行过的最清晰闭环是 placement failure-to-data：它检查 held-out execution、回收失败 placement
成为新 expert data、重新训练/评估，并因最终 held-out 仍为 1/4、未做 domain randomization、
额外任务不是 learned-policy eval 而拒绝 promotion。证据：
`self_improving/alchedata/scripts/build_placement_robustness_diagnosis.py:186-243`、
`self_improving/alchedata/scripts/build_placement_robustness_diagnosis.py:244-275`。

## 4. 权威证据边界

### 4.1 必须保持的信任链

1. `build_scene_package` 先验证 resolved scene 绑定输入 spec 的 digest、scene id 与 seed，再写包含
   每个文件 SHA-256、resolved digest、catalog digest 和 compiler version 的 manifest。
   证据：`scene_gen/builder.py:39-71`。
2. `verify_package` 重算文件摘要与 `resolved_scene.json` canonical digest；任一不符即 fail。
   证据：`scene_gen/builder.py:74-105`。
3. 真正 runtime 由 `script/run_scene_runtime.py` 输出 `robotwin.scene_runtime_evidence.v2`，其中带
   `resolved_scene_sha256`，并在终末窗口逐步采集 support/unexpected contact fraction。
   证据：`script/run_scene_runtime.py:509-545`、`script/run_scene_runtime.py:546-573`。
4. runtime report 保留每物体 drift、settled、visible pixels、penetration、contact fraction、
   unexpected targets、support margin、containment、drop 与 articulation error，然后交给
   `validate_resolved_scene(..., require_runtime=True)`。证据：
   `script/run_scene_runtime.py:670-775`、`script/run_scene_runtime.py:776-826`。
5. validator 要求 nested 动态源和声明 target 在终末窗口至少 0.8 contact fraction，拒绝碰桌或
   其它未声明 target；`on_top_of` 还查 target-local margin，`inside` 还查 containment。
   证据：`scene_gen/validator.py:410-469`。
6. penetration、still moving、drop、head visibility、articulation、runtime relation 和视频帧/互异帧
   均是独立 gate；任一 fail 则总 status fail，有未运行项则 incomplete，全部完成才 pass。
   证据：`scene_gen/validator.py:293-315`、`scene_gen/validator.py:398-525`。

这些 gate 已有针对典型假阳性的攻击测试：静态 nested source 无接触、间歇接触、nested source
碰桌都会被拒。证据：`tests/scene_gen/test_builder_validator.py:303-375`、
`tests/scene_gen/test_builder_validator.py:378-411`。

### 4.2 Stage5 smoke 和视觉 critic 只能是候选生成反馈

Stage5 smoke 的 `check_success()` 固定返回 `True`，执行若未抛异常就把 report 写成 `status=pass`；
它记录 pose delta，但没有阈值判定，报告注释也只声称确认加载/渲染。证据：
`self_improving/stage5/generate_scene/run_robotwin_placement_smoke.py:189-205`、
`self_improving/stage5/generate_scene/run_robotwin_placement_smoke.py:245-271`。

因此 Stage5 的 `smoke pass` 不能成为环境物理通过。视觉辅助层本身也明确说 artifact existence
不是 semantic review，需要人或 VLM 检查；即使 semantic review 完成，它仍没有 SAPIEN contact
fraction 等物理量。证据：`self_improving/stage5/generate_scene/tools.py:139-162`、
`scene_gen/validator.py:315-469`。

### 4.3 发现的证据绑定缺口

Runtime CLI 会写 `resolved_scene_sha256`，但 `validate_resolved_scene` 在消费任意传入的
`runtime_evidence` 时只先检查 `status` 和 collision，随后直接读 objects/relations；它没有比较
evidence 中的 resolved digest 与当前 `resolved.digest()`。证据：
`script/run_scene_runtime.py:509-516`、`scene_gen/validator.py:282-315`、
`scene_gen/validator.py:494-525`。

这个缺口已被测试行为反向确认：positive runtime validator fixture 没有
`resolved_scene_sha256`，仍能 `status=pass`；v2 positive fixture 同样没有 digest，仍能 pass。
证据：`tests/scene_gen/test_builder_validator.py:81-108`、
`tests/scene_gen/test_builder_validator.py:136-172`。

直接 runtime CLI 目前把同一个内存中的 `resolved` 同时用于执行与 validate，所以这条 CLI
路径不会自然混包；但任何未来 Registry、agent 或外部 repair 系统若按 artifact URI 组装输入，
都必须先补上 **runtime evidence digest == EnvironmentPackage.resolved_scene_sha256** 的强制检查，
否则内容正确但属于另一场景的证据可能被错误复用。

公共 Harness schema 当前只保证 `EnvironmentPackage.package_id == resolved_scene_sha256`，以及
runtime ArtifactRef 的 schema version 为 v2；它不解引用 URI，也不把 runtime artifact 内容摘要
与 package 内 resolved digest 做跨对象验证。证据：
`self_improving/harness/schemas/text2env.py:59-87`、
`self_improving/harness/schemas/text2env.py:155-169`、
`repo-docs/modules/harness-schema-tranche.md:79-92`。

## 5. 失败分类、记忆与重试

### 5.1 已有失败分类

| 区域 | 当前分类能力 | 实证边界 |
| --- | --- | --- |
| Harness core | `blocked` 与 `failed` 分开；Blocker 带 code、stage、retryable、unknowns、artifact refs | code 只是结构化大写字符串，不是统一领域 taxonomy；执行器尚缺 |
| Stage5 critic | static / smoke / visual 三个来源；输出 fail_preflight、fail_smoke、pending、repair_required、pass | smoke 不是物理 gate；分类面向 placement 原型 |
| Alchedata policy diagnosis | 固定六类：wrong grasp、knock-over、arm jitter、gripper toggle、after-contact、material mismatch；保留 observed/suspected/not_observed/not_measured | 规则与阈值是该 evaluator 的任务特定启发式 |
| Promotion diagnosis | 假设可标 confirmed/falsified，并保留 infrastructure 与 policy outcome 的区别 | 已有结果仍拒绝 broad robustness promotion |

对应代码证据分别是：`self_improving/harness/schemas/common.py:37-95`、
`self_improving/stage5/generate_scene/scene_critic.py:20-88`、
`self_improving/stage5/generate_scene/scene_critic.py:91-175`、
`self_improving/alchedata/scripts/run_generated_act_eval_smoke.py:53-60`、
`self_improving/alchedata/scripts/run_generated_act_eval_smoke.py:303-386`、
`self_improving/alchedata/scripts/build_placement_robustness_diagnosis.py:244-269`。

规范要求 weakness mining 输出 trace-grounded verdict，cluster 必须保留 episode/artifact provenance；
但当前 `embodied_harness.py` 对这两步只检查 spec 所列 evidence path 存在且非空，并不执行
通用聚类或从 trace 推导 cluster。证据：
`self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:49-65`、
`self_improving/alchedata/scripts/embodied_harness.py:155-160`。

### 5.2 已有失败记忆

当前唯一被严格因果验证的 memory path 是 RGB adapter 例子：先要求 no-memory arm 已执行 3 次且
0/3 success，再把 controller/report 的 SHA-256、task、checkpoint、camera source、推荐 adapter
写入一条 `active_failure_memory`。证据：
`self_improving/alchedata/scripts/build_rgb_adapter_failure_memory.py:20-66`。

读取时 controller 仅检查 memory active、task id 和 checkpoint hash，然后直接采用推荐 adapter；
虽然 memory 保存了 `runtime_camera_source`，selection 函数没有比较这个字段。证据：
`self_improving/alchedata/scripts/run_memory_adapter_arm.py:24-55`、
`self_improving/alchedata/scripts/build_rgb_adapter_failure_memory.py:51-61`。

matched memory/no-memory 消融固定 protocol、校验 memory 与 source controller hash，观察到 0/3
对 3/3；代码同时明确把结论限制为 one-memory、one-decision，不声称 retrieval quality、长期记忆、
placement robustness 或跨失败类迁移。证据：
`self_improving/alchedata/scripts/build_text2env_memory_ablation.py:33-80`。

因此当前 memory 更接近 **精确匹配的配置规则**，还不是可检索、可冲突消解、可衰减、可做
negative transfer 检测的经验库。未来外部 agent 接入必须把 applicability 至少扩展为 task、
checkpoint、scene/placement signature、observation schema、camera source、simulator adapter、
validator/gate profile 和 failure taxonomy；否则“记住一个修复”可能在不适用上下文中复用。

### 5.3 已有重试

公共 schema 已能表达 `max_attempts`、attempt 递增以及 blocker 是否 retryable，但没有执行器把
blocker 类型映射为 retry policy。证据：`self_improving/harness/schemas/common.py:107-120`、
`self_improving/harness/schemas/common.py:156-208`、
`repo-docs/modules/harness-schema-tranche.md:174-184`。

Stage5 只有一个统一的整数 repair budget；static、smoke、visual 三类失败最终都调用同一个
`moonshot_repair_from_scene_critic`，没有按 timeout/infrastructure、deterministic contract failure、
physics failure、semantic mismatch 选择不同 retry policy。证据：
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:258-286`、
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:364-425`、
`self_improving/stage5/generate_scene/gpt_agent.py:466-521`。

第一性原理上，只有“候选改动可能改变失败原因且没有破坏归因”才值得 retry：基础设施 timeout
可重跑同一 candidate；确定性 schema/asset id 错误应先修 candidate；物理 gate 失败需改变受限
placement/asset hypothesis；缺证据应 block 而不是让 LLM 猜；相同 candidate + 相同 seed 的确定性
失败不应无条件重复。

## 6. 可量化缺口与当前基线

### G1 — Harness 执行面为 0

当前有 14 个公共 schema，但可执行 Registry、compile/replay/validate handler、MCP adapter 和
retryable second attempt 均未实现；因此“调用成功率、retry 改善率、artifact 内容校验率”目前没有
统一 runner 可测。证据：`repo-docs/modules/harness-schema-tranche.md:35-46`、
`repo-docs/modules/harness-schema-tranche.md:174-187`。

**可测完成条件**：三个 skill descriptor 可解析；固定输入产生稳定 invocation digest；所有 ArtifactRef
解引用后重算 SHA-256；compile/replay/validate 各至少一条 deterministic qualification；replay 的
retryable failure 能产生 attempt 1→2 的连续 Event 链；非 retryable failure 不重跑。

### G2 — 默认 Stage5 repair 次数为 0

当前默认 `visual_repair_attempts=0`，所以 baseline 是 1 attempt / 0 repair。证据：
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:126-131`、
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:236-243`。

**建议基线指标**：固定 failing-scene corpus 上的 core runtime pass rate、每个成功修复的 attempt 数、
wall time/token cost、unseen prompt/asset/seed pass rate、攻击测试假阳性数。批量整体通过的仓库既有
门槛是至少 95%，空样本必须 fail。证据：`scene_gen/acceptance.py:8-23`、
`tests/scene_gen/test_acceptance.py:8-21`。

### G3 — Stage5 smoke 的物理 gate 数为 0

Stage5 smoke 写 pose delta，但不对 delta、contact、penetration、settled、support target、containment
或 unique frames 设 pass/fail gate；无异常即 pass。证据：
`self_improving/stage5/generate_scene/run_robotwin_placement_smoke.py:219-271`。

**完成条件**：Stage5 candidate 的最终接受必须引用 `scene_gen` 的 hash-bound runtime evidence 与
`robotwin.scene_validation.v1 status=pass`，而不是提升 smoke report 的语义。

### G4 — `pending_visual_review` 被写成 final pass 字段

pipeline 把 `overall_status in {pass, pending_visual_review}` 都设为 `accepted=True` 并写 final spec；
`_mark_final_spec` 无条件写 `decision=accept_final`、`scene_critic=pass`、
`render_visibility=pass_visual_review`，而 CLI 对 `pending_visual_review` 也返回退出码 0。证据：
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:64-97`、
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:380-415`、
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:440-450`。

**完成条件**：pending 只能产出 review-required candidate，不得出现 pass_visual_review 或
accept_final；publishable 只能由 core validate handler 在 runtime pass 后给出。

### G5 — Runtime evidence 与 resolved scene 的消费端交叉 hash gate 缺失

正向测试证明没有 resolved digest 的 runtime evidence 当前仍可 pass；详见 §4.3。证据：
`tests/scene_gen/test_builder_validator.py:81-108`、
`tests/scene_gen/test_builder_validator.py:136-172`。

**完成条件**：缺 digest、digest 格式错误、digest 属于另一 resolved scene、ArtifactRef 内容 hash
错误四个攻击用例全部 fail；正确 CLI evidence 保持 pass。

### G6 — 已验证因果样本极小且单一

固定 checkpoint harness edit 和 memory ablation 都是每臂 3 episode、同一 RGB adapter failure；
项目自己的 proof obligation 要求扩到 signature-disjoint placements、另一 task、domain randomization、
独立 failure classes 与 null-effect controls。证据：
`self_improving/alchedata/scripts/build_harness_causal_ablation.py:104-176`、
`self_improving/alchedata/scripts/build_text2env_memory_ablation.py:41-80`、
`self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:255-279`。

### G7 — 既有 failure score 没有预测失败，不能当选样器真值

预注册的 12-case failure-score 实验有 10 success / 2 failure，Pearson `r=-0.2545`、
Spearman `rho=-0.2551`、exact permutation `p=0.7273`；仓库结论是 higher score 没有与更多失败
相关。证据：`self_improving/alchedata/artifacts/text2env_empirics/failure_score_correlation_v1.json:1-19`。

这条负结果应保留为未来 agent selector 的 baseline：任何新 weakness miner / curriculum selector
必须在预注册 held-out 样本上胜过它，不能把同一个 score 重新包装成“智能选择”。

### G8 — Repo-docs 的 rotation drift 阈值与源码不一致

源码默认 `max_rotation_drift_deg=3.0`，而 `runtime-gates.md` 和 one-real-run walkthrough 都写
rotation drift ≤5°；源码的 `max_resolved_rotation_error_deg` 才是 5°。证据：
`scene_gen/validator.py:169-181`、`repo-docs/modules/runtime-gates.md:57-63`、
`repo-docs/walkthroughs/one-real-run.md:72-78`。

本审计任务被限定只编辑本文件，因此没有直接修 guide；在任何实验报告引用阈值前，应以源码
与运行命令为准，并单独同步读者文档。

## 7. ASPIRE 候选接入点（仅接口假设，不假设其能力）

### 接入点 A — `diagnosis -> bounded proposal`

输入应是不可变的失败包：EnvironmentPackage id、runtime evidence hash、validation checks、
run/events、失败 taxonomy、未知项与当前 harness version。输出只能是 proposal：修改 surface、
最小 diff、预期效果、风险、confound、测试计划、rollback id；不能直接声明成功。这个接口正好
填补当前规范已声明、但 `embodied_harness.py` 未执行的 weakness/cluster/proposal 段。现有接口
依据：`self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:49-83`。

### 接入点 B — Stage5 candidate repair provider

可把外部 agent 放在 `moonshot_repair_from_scene_critic` 的 provider 位置，用同一个 failing corpus
做 matched 对照；允许修改的仍应限于 placement/candidate 层，返回后必须重新过 schema、static、
core runtime 和 attack regressions。当前 repair 的明确输入/允许动作在
`self_improving/stage5/generate_scene/gpt_agent.py:466-521`，当前 pipeline 的重验入口在
`self_improving/stage5/generate_scene/run_scene_generation_pipeline.py:243-425`。

### 接入点 C — 尚未实现的 Harness Registry / retry controller

外部系统可以帮助实现“根据 typed blocker 决定 retry / repair / block / escalate”的策略，但
Registry 必须是唯一执行与审计权威，MCP 不能发明类型或默认值。现有架构边界在
`repo-docs/modules/harness-schema-tranche.md:16-33`；attempt 连续性约束在
`self_improving/harness/schemas/common.py:173-208`。

### 接入点 D — 版本化失败记忆与可复用技能候选

可以把已验证的 failure→repair 对抽取成候选 memory/skill，但 activation 前必须匹配完整
applicability，并在 signature-disjoint scene/task 上做 null control 与 negative-transfer gate。
当前单记忆例子的 provenance 与局限在
`self_improving/alchedata/scripts/build_rgb_adapter_failure_memory.py:32-66`、
`self_improving/alchedata/scripts/build_text2env_memory_ablation.py:73-80`。

### 接入点 E — Failure-to-data / curriculum proposal

外部 agent 可以从失败 cluster 提议下一个数据需求；真正是否改善必须通过固定 evaluator、fresh
held-out split 与 promotion gate。现有 placement loop 已证明“采到所有失败 placement 的 expert
data”仍不足以带来 broad generalization，因此不能以数据收集完成代替性能改进。证据：
`self_improving/alchedata/scripts/build_placement_robustness_diagnosis.py:228-275`。

### 接入点 F — 实验选择器，而不是真值评审器

候选系统可以排序下一项值得尝试的 repair/skill/scene，但最终 reward 必须来自 core runtime
validation、task verifier、held-out robustness 与成本；现有 failure-score 负结果可作朴素 baseline。
证据：`scene_gen/validator.py:514-525`、
`self_improving/alchedata/artifacts/text2env_empirics/failure_score_correlation_v1.json:9-19`。

## 8. 建议的最小实证实验协议

本节不是声称 ASPIRE 能做到，而是为外部代码复现轨道准备可证伪接口。

### 实验 1：Stage5 repair controller matched A/B

- **固定**：同一初始 PlacementSpec、catalog、模型 provider 配置、seed、RoboTwin/SAPIEN 版本、
  core validator profile、最大 attempts 与 token/time budget。
- **对照**：当前 `moonshot_repair_from_scene_critic`；**候选**：外部系统 repair provider。
- **主指标**：fresh held-out scene/seed 上 `robotwin.scene_validation.v1 status=pass` 的比例。
- **副指标**：每次成功的 attempts、wall time、模型调用数、token、产生的新 blocker 数、攻击测试
  假阳性数、对已通过场景的 regression 数。
- **晋升**：不能只比当前 Stage5 smoke/visual status；必须保留 package/evidence/report hashes，并按
  仓库既有批量 95% gate 聚合。核心门控与批量门槛证据：
  `scene_gen/validator.py:514-525`、`scene_gen/acceptance.py:8-23`。

### 实验 2：多失败类 memory retrieval

- 从 static schema、asset grounding、runtime support/contact、containment、visibility、policy action、
  infrastructure timeout 中各取独立 failure class；每条 memory 留 provenance 与 applicability。
- 每类做 no-memory、relevant-memory、irrelevant-memory/null-control 三臂，固定 checkpoint 与协议。
- 主指标为 candidate success delta；必须同时报告 wrong retrieval rate、negative transfer rate 和
  abstention/block rate。现有 one-memory result 只提供 0/3→3/3 的机制 baseline。证据：
  `self_improving/alchedata/scripts/build_text2env_memory_ablation.py:33-80`。

### 实验 3：failure miner / curriculum selector

- 预注册 held-out placements 与 failure labels；比较 random、既有 failure score、当前规则诊断与
  候选 selector 在固定采集预算下带来的最终 runtime/task success。
- 所有 selector 输出只是“下一实验建议”；最终比较要用执行后的任务结果，且 infrastructure failure
  不计为 policy failure。现有 correlation join 已实现这条分离。证据：
  `self_improving/alchedata/scripts/build_failure_score_correlation.py:60-120`。

## 9. 必须保持的边界清单

1. **不污染稳定核心**：agent、policy、training、memory、sim-specific orchestration 留在
   `self_improving/`；`scene_gen/` 只保持 `/gen-env` 确定性契约与权威验证。证据：
   `repo-docs/modules/self-improving-platform.md:1-14`。
2. **不把 render/VLM/smoke 当物理证据**：最终环境接受必须走连续 runtime contact/stability/
   containment/visibility/video gate。证据：`repo-docs/modules/runtime-gates.md:1-8`、
   `scene_gen/validator.py:282-525`。
3. **不接受静态 nested 假阳性**：nested source 必须动态、接触声明 target、contact fraction ≥0.8，
   不得触桌或其它未声明 support。证据：`scene_gen/validator.py:410-469`、
   `tests/scene_gen/test_builder_validator.py:303-411`。
4. **不放松目标局部几何**：support 用 target surface footprint/margin，containment 用 interior，
   不能退化成外层 AABB。证据：`scene_gen/validator.py:84-128`。
5. **不丢内容身份**：scene spec、resolved scene、catalog、package、runtime evidence、validation report、
   candidate diff、memory 与 regression report 都必须内容寻址并交叉绑定。现有 package 绑定在
   `scene_gen/builder.py:39-105`；runtime 交叉绑定缺口见 §4.3，接入前必须补齐。
6. **不混淆 harness edit 与 policy retraining**：matched harness regression 固定 checkpoint、seed、
   task 和声明环境；训练是另一条 versioned intervention。证据：
   `self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:4-27`。
7. **不因执行完成自动 promotion**：task、safety、robustness、provenance gate 必须独立；拒绝与
   blocked candidate 保留为 immutable evidence，带 rollback。证据：
   `self_improving/alchedata/artifacts/embodied_harness/embodied_harness_spec.json:85-110`。
8. **不让 agent 改 validator 来“赢指标”**：validator/gate profile 是受控 harness surface；任何
   threshold/contract 改动都需攻击测试与真实 RoboTwin/SAPIEN replay，而不是与 scene repair 混在
   同一 candidate。现有攻击测试职责与真机要求见
   `repo-docs/modules/runtime-gates.md:75-97`。
9. **外部项目保持独立生命周期**：第三方项目进入 `external/`，不复制为当前核心实现。
   证据：`repo-docs/modules/self-improving-platform.md:16-19`。

## 10. 审计留痕

| 时间 | 决策 / 动作 | 结果 |
| --- | --- | --- |
| 2026-08-31 | 遵循任务要求，不创建分支、不 commit，只写本文件 | 当前分支保持不变；其它工作区改动不触碰 |
| 2026-08-31 | 先读取 dashboard 的 portfolio/tasks/project 三个规定端点 | 三个端点均返回 HTTP 404；无法从公共 dashboard 同步 TODO，本审计继续使用父任务给定范围 |
| 2026-08-31 | 先读 `AGENTS.md` 约束与 `repo-docs`，再读源码/测试 | 确认 `scene_gen` 是稳定信任边界，Stage5/Alchedata 不能降级物理 gate |
| 2026-08-31 | 对 `self_improving/harness/` 做全文件枚举 | 仅发现 schemas、schema catalog、JSON snapshots；与 guide 所述“执行器尚缺”一致 |
| 2026-08-31 | 审查 RunState、Text2Env schema、Stage5 pipeline/critic/smoke、Alchedata causal/memory/diagnosis、runtime validator 与攻击测试 | 形成 §3–§6 的代码级状态与缺口 |
| 2026-08-31 | 不读取、不推断 ASPIRE 论文或外部实现细节 | §7 仅定义候选接入接口，等待论文/代码轨道实证映射 |
| 2026-08-31 | 首次从仓库根组合运行 Harness、Alchedata、validator 测试 | Alchedata 两个模块 collection 失败：`ModuleNotFoundError: scripts`；判定为子项目 `PYTHONPATH`/入口问题，不记为实现回归 |
| 2026-08-31 | 按入口拆分复跑 | 根 Harness + validator/acceptance：34 passed；Alchedata 显式设置子项目 `PYTHONPATH`：6 passed、1 skipped；skip 是测试中声明的 external report bundle 缺失路径 |
| 2026-08-31 | 自动核对本文件全部 `file:line` 引用并运行 `git diff --check` | 全部引用文件存在且行号未越界；diff check 通过 |

### 审计使用的可复核查询

```bash
find self_improving/harness -maxdepth 3 -type f
rg -n 'retry|attempt|critic|repair|memory|failure|promotion|rollback' self_improving scene_gen tests repo-docs
nl -ba <上述逐项引用文件>
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider \
  tests/self_improving/harness \
  tests/scene_gen/test_builder_validator.py \
  tests/scene_gen/test_acceptance.py

cd self_improving/alchedata
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/home/jingxiang/bingsheng/robot-harness-gen-env/self_improving/alchedata \
pytest -q -p no:cacheprovider \
  tests/test_embodied_harness.py \
  tests/test_harness_causal_ablation.py
```

### 本审计的 claim boundary

本文件能确定当前项目有哪些状态机、物理证据边界、任务特定诊断/记忆/消融，以及哪些接口仍
缺失；它不能单独确定 ASPIRE 是否优于当前 baseline。只有外部复现轨道把论文方法映射到 §7
接口，并按 §8 的 matched protocol 产生 held-out、hash-bound、core-runtime 证据后，才可以回答
“ASPIRE 是否提升本项目”。

## 11. 后续实验处置（2026-08-31）

本节不改写前面的 point-in-time 审计事实，而是记录同一研究随后如何处理其高风险缺口：

- G3 runtime evidence 声明一致性已在 `795273f` 修复。validator 现在要求 evidence `scene_id` 与
  `resolved_scene_sha256` 精确匹配当前 `ResolvedSceneSpec`；同一冻结攻击探针从 accuracy 0.20、
  4/4 unsafe accepts 变为 accuracy 1.00、0/4 unsafe accepts。新的门还消费了一次真实
  RoboTwin/SAPIEN can-on-plate 回放并通过。该门只比较 producer 自声明字段；搬运别次 payload
  后重写两字段仍可能过门，因此不是完整 evidence/media/run provenance。
- G4 pending review 状态已在 `fa9121f` 修复。`pending_visual_review` 现在写独立 review candidate、
  保留 pending/hold 字段并退出 2；真实 pass 控制仍写 final 并退出 0。冻结探针从 0/5 变为 5/5。
- G4 的 follow-on 覆盖在 `bd4f64f` 扩到 standalone critic 与 batch aggregate，在 `fda13b8` 扩到
  legacy candidate-first/pass-only promotion；E1c probe 从 0.625 变为 1.00。默认 static-only
  另在 `72b35c4` 修复：不跑 smoke/visual 时写 nonfinal candidate 与 `not_run`，E1d 从 2/9 变为
  9/9。它们都是独立预注册结果，不能回填进原 E1b 的 5/5。
- G8 reader-facing rotation 阈值已同步为 rotation drift 3°、resolved rotation error 5°。
- 并发实现轨道在快照后增加了 artifact resolver、callback-driven recorder、compiler、生成资产准入、
  通用 `SkillRegistry`、PackageStore 与首个 compile adapter；因此“Registry/handler 未实现”只属于
  研究起点。replay/validate 与 MCP 仍缺。resolver 的 unrestricted `file://` 不是 sandbox；compile
  adapter 的 catalog check/use mutation 已由 `ef5e29e`/`910ccb1` 冻结并修复为 CAS-only input，但
  referenced asset payload 仍不是同一 snapshot。生成资产 ledger 还把未运行 settle 的 generation QC 写成 `sapien/pass`，admission 在 solve blocked
  后不回滚，不能当物理资格或原子 promotion。G5/G6/G7 的多相机 memory 绑定、
  failure-score 外部效度和检索负迁移仍未由真实 rollout 解决。
- `595 passed、6 skipped；Harness 350/350 statements、74/74 branches` 只属于 E0–E2 快照
  `1180aef`。后续并发提交必须用各自当前-HEAD 回归，不得沿用该绿色数字。

E2 只回答一个更窄问题：在六个固定合成契约故障家族的 120 个 held-out cases 中，开发集验证后
冻结的 trigger-specific memory completion 为 1.00，只看当前 trace 的 bounded action policy 为
0.50。退化 bootstrap 不支持统计总体 superiority 主张；这既不是物理环境生成成功率，也不是
LLM 自我学习或论文 ASPIRE 全系统复现。完整边界见
`final_report.md` 与 `artifacts/heldout_harness_benchmark.json`。
