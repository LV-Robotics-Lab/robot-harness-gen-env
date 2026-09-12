# 从失败环境到可验证 Repair Skill：研究计划

> 状态：Research plan；尚未执行新实验，也不是实现完成声明
>
> 编写日期：2026-08-31（Asia/Singapore）
>
> 本项目行为快照：`dcf6be01c54d80e4de02ba01a3050e561a973fd6`
>
> ASPIRE 源码快照：`7ba73d3bcac8f6b6d4a7d67ed4040988f768d282`
>
> 计划产物根：`self_improving/studies/ASPIRE/environment_failure_skills/`
>
> 约束：不新建分支；`scene_gen/`、validator、runtime evidence producer 与 gate profile 是只读信任根

## 0. 执行摘要

### 判断

**值得研究，而且当前项目已经有足够的类型化 trace 与物理 gate 来做一个可证伪实验。**

但命题必须从：

> “每次失败都积累成 skill”

改成：

> **每次失败都可以进入不可变的 incident store；只有被复现、正确归因、由有界修复产生因果改善、通过 clean/near-miss/attack qualification，并在独立 held-out 上保持收益的模式，才可晋升为 active repair skill。**

失败本身是证据、反例或未知项，不是能力。一个真正的 skill 至少是：

```text
typed failure signature
  + applicability / exclusions
  + bounded repair operator
  + source and counterexample receipts
  + independent qualification
  + version / expiry / rollback
```

环境噪点、穿模、重叠、支撑失败、掉落和 articulation 错误可以全部被记录；它们进入 active library 的路径不同：

- 可测的视觉/传感噪声优先产生“重观察、聚合或 abstain”类诊断 skill，不得凭视觉修改物理真值；
- solver/几何失败适合产生受限的 pose/asset/constraint repair operator；
- runtime 物理失败必须由真实 SAPIEN contact、settling、drop、containment 等 gate 验证；
- timeout、依赖漂移、hash/path/security 错误进入 ops/security 队列，不能污染环境 skill；
- pending、unknown、VLM disagreement 和歧义意图保持不确定，不能被强行标成负例。

### 可证伪的核心主张

在固定模型、候选数、token、仿真 rollout 和 validator 调用预算下，与“只有 typed trace/history、没有跨 case memory 的 evolutionary repair”相比，读取一个在 development/qualification 上构建、在 held-out 期间冻结的 qualified skill library，是否能：

1. 提高未见 task/asset/fault clusters 上的权威物理恢复完成率；
2. 不产生任何 unsafe publication 或 clean regression；
3. 降低平均物理 rollout、token 或 wall time；
4. 对未知、冲突、过期或投毒 memory 正确 abstain/quarantine。

若主效应的预注册 cluster-level 置信区间不能排除零，或只在重复的 seed/variant 上有效，则拒绝“提高 robust 环境生成”的主张。若成功率不升、但成本在非劣条件下降低，只能称为效率收益。

### 当前不能声称什么

- ASPIRE 已经改善本项目的 SAPIEN 物理成功率；
- 现有 synthetic E2 等价于真实仿真；
- 一次修复成功说明它可跨 task/asset 迁移；
- failure memory 比等容量的 success-example memory 更好；本计划没有做这个对照；
- render、VLM、截图或 agent 自评可以替代 runtime physics gate；
- 这里的“学习”是权重训练。当前研究对象是**非梯度的、验证后可复用程序知识**。

## 1. 第一性原理建模

### 1.1 失败中究竟有什么信息

一个失败环境不是一个标量 `fail`。它是约束系统的反例：

```text
输入意图 I
+ 场景候选 C
+ 资产/仿真/validator 身份 D
+ 执行预算 B
  --Execute--> trace Z + authoritative checks G
```

失败说明 `C` 在固定 `I,D,B,G` 下违反了至少一个可观测约束。它是否可复用，取决于能否找到作用域明确的变换 `r`：

```text
G(C) = fail
G(r(C)) = pass
G(clean) = pass
```

并且该转换在未参与提炼的多个 clusters 上仍成立。若只知道 `G(C)=fail`，尚未知道根因，也没有 skill。

### 1.2 四层记忆，而不是一个混合“经验库”

```text
所有失败
   │
   ▼
FailureIncident ───────────────► ops / security / human-review / unknown
   │ 复现、聚类、单变量归因
   ▼
SkillCandidate
   │ fault + clean + near-miss + attack qualification
   ▼
QualifiedSkill (eval-only)
   │ 单 writer、冻结内容身份
   ▼
FrozenEvaluationLibrary ──只读──► confirmatory evaluation
                                      │ library + per-skill 门通过
                                      ▼
                          PromotionCandidate overlay
                                      │ lifecycle 门通过
                                      ▼
                           ActiveSkill + promotion receipt
```

四层分别解决四个问题：

1. `FailureIncident` 保真，不要求已经理解；
2. `SkillCandidate` 是待证伪的机制假设；
3. `QualifiedSkill` 只表示在声明作用域内通过资格门，状态为 `eval-only`；
4. `FrozenEvaluationLibrary` 防止 held-out 结果回流和选择性记忆；
5. confirmatory 只生成不改 skill bytes 的 candidate overlay；只有逐 skill confirmatory 与 lifecycle gates 都通过后，独立 promotion receipt 才能把它变成 `active`。因此这是四层记忆加两个决策 receipts，不是 Phase 2 自动上线。

### 1.3 “负经验”如何进入系统

失败可产生三种长期价值：

- **positive repair**：满足 trigger 时执行一个有界变换；
- **negative guard**：说明某个修复在特定 precondition 下不应执行；
- **counterexample**：约束检索/分类器，促使系统 abstain。

“不要使用 outer AABB 判断 containment”“不要把 nested dynamic source 设成 static”是 guard，不是环境成功脚本。它们仍需反事实证据：使用该 guard 后避免 unsafe candidate，且 clean cases 不回归。

## 2. ASPIRE 中真正值得迁移的机制

### 2.1 论文机制

ASPIRE 的核心对象是机器人程序而不是 simulator environment。论文 Algorithm 1 的闭环是：执行初始程序、收集 primitive-level trace、用当前任务/历史 top programs/skill library/失败 trace 产生 `K` 个候选、在 debug configurations 上选择、在独立 validation 上验证，再抽取 validated patterns。[论文 Algorithm 1](https://arxiv.org/html/2607.00272#alg1)、[§2.2 skill library](https://arxiv.org/html/2607.00272#S2.SS2)、[§2.3 evolutionary search](https://arxiv.org/html/2607.00272#S2.SS3)。

论文的可迁移思想是：

- trace 应足够细，能把 perception、planning、execution 等失败分开；
- skill 保存 failure signature、when-to-apply、repair strategy 和必要代码，而不是完整聊天历史；
- evolutionary search 是离散程序候选的生成、执行、选择、再生成；不是权重更新，也没有固定 crossover 算子；
- validation 之后才抽取可复用模式。

论文及官方页面没有证明这一闭环能修复我们的 SceneSpec、asset catalog、solver、SAPIEN 物理或 runtime validator。因此本研究必须重新建立环境侧因果证据。[ASPIRE 官方项目页](https://research.nvidia.com/labs/gear/aspire/)、[论文](https://arxiv.org/abs/2607.00272)。

### 2.2 公开代码中的可执行协议

固定源码 `7ba73d3...` 的 LIBERO Evolutionary Search runbook 明确：

- debug seeds 为 51–65，evaluation seeds 为 1–50，迭代期间禁止读取 evaluation；
- 若现有 baseline 存在，初始 `candidate_A` 原样保留它；
- 每轮 `K=8` 候选应测试不同假设；
- 下一轮由 top-3 survivors、历史和残余 traces 条件化；
- 最终代码冻结后才跑 50 个 evaluation seeds。

来源：[split lockout 第 29–37 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/evosearch/subagent-prompt.md#L29-L37)、[候选与跨轮搜索第 180–253 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/evosearch/subagent-prompt.md#L180-L253)、[冻结 best 后 Stage 2 第 257–308 行](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/evosearch/subagent-prompt.md#L257-L308)。

Fix Loop 的共享 skill writer 由 coordinator 串行化；promotion recorder 保存修改前快照、exact patch、逐文件/整库 hash 和 append-only JSONL。held-out outcome 不驱动 skill edit。[Fix Loop skill](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/.claude/libero/fix-loop/SKILL.md#L1-L18)、[promotion recorder](https://github.com/NVlabs/ASPIRE/blob/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282/aspire/sim/scripts/libero/record_skill_promotion.py#L1-L10)。

本计划迁移这些**实验结构**，不迁移上游的 raw code execution、prompt-only 信任边界或论文结果。

### 2.3 环境侧的对应关系

| ASPIRE | 本研究对应物 |
| --- | --- |
| robot program | typed environment repair proposal / SceneSpec transform |
| primitive trace | compile events、solver attempts、runtime checks/contact/media |
| task success | immutable static + real SAPIEN runtime validator pass |
| program population | 不同机制的 bounded repair candidates |
| top programs/history | parentage、survivor set、已排除假设 |
| shared skill library | qualified environment repair library |
| debug/validation seeds | grouped development/selection/held-out clusters |

### 2.4 邻近一手工作只作为设计参照

| 工作 | 可借鉴 | 不能据此声称 |
| --- | --- | --- |
| [FATE](https://arxiv.org/abs/2603.01505) | 分离静态 scene auditor 与动态 in-step auditor；使用离散 scene/solver/policy repair action；先便宜静态门再仿真 | 没有跨任务 repair-skill promotion/遗忘，也没有可运行公开 baseline；不能证明跨环境 memory |
| [REFLECT](https://arxiv.org/abs/2306.15724) / [代码](https://github.com/real-stanford/reflect) | 把长多模态轨迹压缩成失败阶段、关键事件、实际/期望状态后再归因 | 没有持久 skill library 或环境 mutation；单次 correction 不是 qualified skill |
| [Reflexion](https://arxiv.org/abs/2303.11366) / [代码](https://github.com/noahshinn/reflexion) | current-trace/no-memory/reflection ablation；短期轨迹与长期 lesson 分层 | 文本/同任务 episodic memory 不能证明机器人物理或跨任务 promotion |
| [Eureka](https://arxiv.org/abs/2310.12931) / [代码](https://github.com/eureka-research/Eureka) | 并行产生候选，由可执行 fitness 而非 LLM 自评选优，保存 component feedback | 搜索 reward code，不修 scene，也没有 repair-skill library |

可辨识的新问题不是“agent 能否从失败修程序”——ASPIRE 已经研究了它；而是：**类型化环境失败能否被压缩成不篡改 validator、可跨 task/asset/fault cluster 迁移的安全修复算子。**

## 3. 当前项目的实证起点

本节只描述 committed `dcf6be0`。工作区还有大量并发 dirty/untracked Harness 改动，本计划不把它们当作现有能力。下列固定证据由 `git show dcf6be01c54d80e4de02ba01a3050e561a973fd6:<path>` 读取；相对工作树文件不会充当 commit receipt。

| 固定路径 | Git blob SHA-1 |
| --- | --- |
| `scene_gen/compiler.py` | `0de9c40e2ed2dbd374af861ecd20cc25ca6a111b` |
| `scene_gen/solver.py` | `c96bf0ead672a1162db80a645377f452629f66ce` |
| `scene_gen/validator.py` | `cd6a2f321e73f011fd4dd2b3287b9f8995f5fd7f` |
| `self_improving/harness/runtime_executor.py` | `b4ded93ca807be38aa4ee96e9f334a5f1183da04` |
| `self_improving/harness/application.py` | `a260bbab3d399aa87a915a32267e1a2ab4c95774` |

### 3.1 已经存在的类型化失败面

1. 编译器已有阶段和稳定拒绝码：
   - `T2E_REQUEST_REJECTED / parse`；
   - `T2E_CATALOG_INVALID / catalog`；
   - `T2E_ASSET_UNAVAILABLE / asset_generation|solve`；
   - `T2E_SOLVER_EXHAUSTED / solve`。
   来源：`dcf6be0:scene_gen/compiler.py:72-128,141-201`。

2. bounded-exhaustion solver path 已保存 `scene_id`、seed、attempt/backtrack budget，以及每个 object 的 candidate XY/yaw、accepted 和 reasons。reasons 覆盖 keepout/3D overlap、support height/margin、inside footprint/vertical fit、region fit、nested-static 等。articulation `SceneSolveError` 也可映射为 `T2E_SOLVER_EXHAUSTED`，但该分支没有同一份 bounded trace；其 incident 必须从 invocation receipt 补齐预算或明确标 `not_available`。来源：`dcf6be0:scene_gen/solver.py:188-303,385-505,515-528`；错误码映射见 `dcf6be0:scene_gen/compiler.py:185-201`。

3. runtime validator 已逐对象检查：
   - scene/digest 声明一致性与 evidence status；
   - initial robot collision、视频时间线与互异帧；
   - penetration、settled、support target/contact fraction；
   - unexpected contact、target-local support margin、inside containment；
   - dropped、visibility、articulation qpos、runtime relations。
   任何 `fail` 优先；存在 `not_run` 时状态是 `incomplete`，不是 pass。来源：`dcf6be0:scene_gen/validator.py:369-640`。

4. replay executor 把 capability/asset drift、protocol/security、timeout/crash 分成稳定 failure taxonomy。这为未来 ingestion 的 environment/infrastructure 分流提供稳定标签；`dcf6be0` 尚没有完整 failure ingestion/classification loop。来源：`dcf6be0:self_improving/harness/runtime_executor.py:52-76`。

### 3.2 已有 memory 证据，但范围很窄

仓库已有一个结构化 RGB double-swap failure-memory **summary receipt**：它绑定 source evaluator、checkpoint、camera、applicability 和 recommendation。该 committed summary 报告 matched ablation 只改变 memory availability，并在一个固定 checkpoint/placement 的 3 seeds 上从 0/3 变为 3/3：

- [`rgb_adapter_failure_memory_v1.json`](../../alchedata/artifacts/text2env_empirics/rgb_adapter_failure_memory_v1.json)
- [`memory_ablation_rgb_adapter_v1.json`](../../alchedata/artifacts/text2env_empirics/memory_ablation_rgb_adapter_v1.json)

其引用的原始 controller/evaluator/run 路径不在当前 checkout，现阶段不能从 raw run 独立重算这些 hash。因此它至多支持“committed receipt 报告结构化 memory 控制了一个 Harness 决策”，不说明跨 failure class、placement、asset 或环境生成迁移。

已有 E2 在 120 个固定 synthetic fault cases 上观察到 frozen/reactive/validated-memory mHRC 为 `0.00/0.50/1.00`，但 variant 类型重叠、CI 因固定合成差异退化、Oracle 没有实例化，也没有 LLM、真实 SAPIEN 或长期生命周期。其冻结 receipt 只检查了 case-ID intersection，没有冻结 digest-set intersection；本计划因此不声称 E2 已证明 digest-set 隔离。E2 只保留为 mechanism preflight，不能作为本计划的物理先验。[既有 E2 审计](final_report.md#74-e2held-out-validated-memory-benchmark)。

另一个重要负结果是：failure-to-data loop 回收并重训后，fresh held-out policy 仍为 1/4，因此拒绝 promotion。它提醒我们“失败被使用了”与“学习到可迁移能力”完全不同。[placement diagnosis](../../alchedata/artifacts/diagnosis/placement_robustness_diagnosis.json)。

### 3.3 生产能力缺口

`dcf6be0` 已有 ArtifactRef、Blocker、Run/Event、Registry 和 compile adapter 等可信底座，但 production assembly 没有完整的：

```text
failure ingestion
→ classification/retrieval
→ causal qualification
→ atomic promotion/rollback
→ frozen held-out evaluation
```

所以本计划首先构建研究模块和 sidecar schemas，不把未经资格化的 repair 注入生产路径。固定源码依据：`dcf6be0:self_improving/harness/application.py:1-9,99-103,134-186,219-290`；其 blob identity 已列在本节表中。

## 4. 故障 taxonomy 与路由

### 4.1 Incident taxonomy

| ID | 失败面 | 当前可用证据 | 可以形成什么 | 不得形成什么 |
| --- | --- | --- | --- | --- |
| RQ | request/schema | parse code、Pydantic errors、object/relation graph | 语法/约束修复或明确 abstain | 猜测歧义意图、删除要求 |
| GA | grounding/asset | scores、reasons、rejected candidates、catalog/model identity | asset/model scoped selection、measured orientation/scale repair | 猜尺寸/物理参数、绕过来源/qualification |
| SG | solver geometry | attempts、XY/yaw、overlap/support/containment reasons | bounded pose/yaw/re-solve operator | 放宽 workspace/overlap/support 阈值 |
| PG | package/static gate | workspace、height、files、overlap、relation、manifest checks | 重新构包或拒绝 | 改 hash、manifest 或 validator 让坏包通过 |
| RP | runtime physics | penetration、settling、drop、contact、margin、containment、qpos | 受限 clearance/pose/stable-state/asset repair | `is_static` laundering、无接触堆叠、伪 evidence |
| VN | visual/sensor noise | visible pixels；未来冻结的 typed noise/perception probe | re-observe、多视角、聚合、denoise、abstain | 用同一 VLM提案又裁决，render 当 physics |
| RF | robot feasibility | robot collision；未来冻结 reachability/task probe | placement/keepout repair 或探索性诊断 | 把 policy/task failure默认归为环境失败 |
| OP | infrastructure | timeout、crash、capability/dependency drift | retry/quarantine/ops action | 环境 repair skill |
| SE | security/integrity | path/hash/protocol/media/evidence mismatch | fail-closed、rebind/replay、security incident | 修改证据或 threshold |
| UH | unknown/human | pending、VLM disagreement、missing ground truth、多根因 | unknown/composite incident、人工复核 | 自动 negative label 或 active skill |

### 4.2 对“环境噪点”的操作定义

“看起来有噪点”不是可复现实验标签。必须在协议冻结前归入以下之一：

1. **sensor noise**：RGB/depth/calibration/dropout 的已知注入，参数只对 injector/oracle 可见；
2. **media integrity**：帧重复、时间线非单调、decode 不一致，走 security/evidence gate；
3. **visibility/occlusion**：由可见像素、独立 segmentation/perception probe 测量；
4. **asset visual artifact**：mesh/material/topology 的 asset-QC 问题，不等于物理错误；
5. **physics perturbation**：pose、friction、mass、contact 等变化，必须由 SAPIEN gate 评估。

没有冻结 oracle 的 noise family 只能作为 exploratory，不进入主指标。

### 4.3 首批候选 repair operators

所有 operator 都是 typed、bounded、可审计的 sidecar action；LLM 只能选 operator 和受 schema 限制的参数，不能任意改核心代码。

| Operator | 典型 trigger | 有界动作 | 必须验证 |
| --- | --- | --- | --- |
| `REOBSERVE_MULTI_VIEW` | visibility/noise 低、physics 尚未知 | 固定次数重采、多视角或时间聚合 | 独立 perception probe；仍 unknown 时 abstain |
| `PROJECT_TO_SUPPORT_INTERIOR` | solver/runtime support margin fail | 在 target-local footprint 内重解 XY | full footprint margin、contact target/fraction、clean |
| `PROJECT_TO_CONTAINER_INTERIOR` | containment fail | target-local interior 内重解 XY/Z/yaw | footprint+vertical containment、无 table unexpected contact |
| `RESAMPLE_YAW_FOR_CLEARANCE` | overlap/keepout/region fit | 从预注册 yaw set 搜索 | static no-overlap + runtime penetration/settling |
| `INCREASE_BOUNDED_SPAWN_CLEARANCE` | initial penetration | 在最大允许 clearance 内调 z 并重新 settle | 最终 contact/support/drop；不能悬空 |
| `SELECT_COMPATIBLE_MODEL` | grounded model/loader/geometry mismatch | 从 catalog 可用、已测量候选中替换 model | prompt semantics、provenance、package、physics |
| `USE_MEASURED_STABLE_STATE` | qpos/orientation/stability fail | 只读 catalog/probe receipt 中的 qpos/pose | source receipt、articulation、settling |
| `QUARANTINE_AND_REPLAY` | drift/hash/protocol/stale dependency | 不修环境；冻结 incident、重建可信输入后 replay | 全链 binding；不得复用旧物理 payload |

任意涉及 mass、friction、COM、inertia、collision mesh、scale 的动作，在没有 measured geometry 或独立 simulator probe 时禁止自动晋升。

## 5. 数据契约

### 5.1 `failure_attribution_case.v1`

RQ0 使用独立评测记录，而不是把 clean control 伪装成 failure incident：

```yaml
schema_version: failure_attribution_case.v1
case_id: content-derived id
case_role: injected_fault | natural_failure | no_failure_control |
  infrastructure | security | unknown | composite
cluster_key: ...
source_run_refs: [...]
policy_view_ref: ...
ground_truth:
  route: environment_f1 | environment_f2 | environment_f3 |
    environment_f4 | environment_f5 | observation_f6 | feasibility_f7 |
    infrastructure | security | unknown | composite | no_failure
  method: deterministic_gate | blinded_human_adjudication
  evidence_refs: [...]
classifier_output:
  route: ...
  confidence: ...
  abstained: true | false
budget_receipt: ...
```

只有 `ground_truth.route` 为真实 failure 的案例才可进一步生成 `environment_failure_incident.v1`；`no_failure` 只用于 false-positive/clean-regression 测量。真实 composite 必须绑定多个冻结的失败 gate 与同一 source run，不能用文字拼接伪造。

### 5.2 `environment_failure_incident.v1`

最小字段：

```yaml
schema_version: environment_failure_incident.v1
incident_id: content-derived id
status: observed | reproduced | unresolved | routed | superseded
phase: calibration | build | qualification | pilot | confirmatory | lifecycle
case_role: natural_observation | build_fault | qualification_fault |
  search_debug | selection_validation | sealed_heldout | clean_control |
  near_miss | attack | stale | poison
cluster_key: content-derived task-template × asset-geometry × root-cause key
split_manifest_sha256: ...
identity:
  run_id: ...
  invocation_sha256: ...
  request_sha256: ...
  scene_spec_sha256: ... | null
  resolved_scene_sha256: ... | null
  package_manifest_sha256: ... | null
  asset_catalog_sha256: ... | null
  asset_snapshot_sha256: ... | null
  simulator_config_sha256: ... | null
  validator_tree_sha256: ... | null
  gate_profile_sha256: ... | null
  model_checkpoint_sha256: ... | null
  seed: ... | null
failure:
  stage: ...
  code: ...
  failed_checks: [...]
  observed_signature: ...
  classification: family | unknown | composite
  confidence: ...
  adjudicator: deterministic_gate | blinded_human | exploratory_model
evidence:
  artifact_refs: [...]
  trace_sha256: ... | null
  runtime_evidence_sha256: ... | null
  validation_report_sha256: ... | null
  media_manifest_sha256: ... | null
  missing_artifacts:
    <field>: parse_failed_before_scene_spec | stage_not_reached | not_requested
reproduction:
  attempts: ...
  matching_failures: ...
  deterministic: true | false | unknown
budget:
  candidate_slots: ...
  simulator_rollouts: ...
  validator_calls: ...
  model_tokens: ...
claim_boundary: ...
```

原始 evidence 不塞进自由文本 memory，只保存 CAS refs/digests。agent 总结与 deterministic gate facts 分字段保存。身份字段按阶段强制：例如 parse 失败必须有 request/invocation，但可以没有 SceneSpec；一旦进入 solve 就必须有 SceneSpec/catalog；进入 runtime 就必须有 resolved/package/simulator/gate 身份。缺失必须用枚举原因表示，不能用空字符串或虚构 digest。

### 5.3 `environment_repair_skill.v1`

```yaml
schema_version: environment_repair_skill.v1
skill_id: ...
version: ...
status: candidate | qualified_eval_only
trigger:
  stage: ...
  codes: [...]
  failed_check_predicate: ...
applicability:
  simulator_range: ...
  camera_profile: ...
  relation_types: [...]
  asset_geometry_families: [...]
  task_families: [...]
  gate_profile_sha256: ...
exclusions: [...]
near_miss_counterexamples: [...]
repair:
  operator: allowlisted enum
  parameter_schema: ...
  allowed_write_surfaces: [...]
  forbidden_write_surfaces: [...]
  abstention_behavior: ...
evidence:
  source_incident_refs: [...]
  causal_ab_receipts: [...]
  clean_control_receipts: [...]
  qualification_report: ...
lifecycle:
  dependency_keys: [...]
  expires_on_change: [...]
  conflicts_with: [...]
  rollback_to: ...
content_sha256: ...
claim_boundary: ...
```

### 5.4 `repair_skill_qualification_receipt.v1`

qualification receipt 必须记录：候选内容身份、source clusters、单变量 A/B、fault/clean/near-miss/attack outcomes、独立执行器与 gate 身份、预算、判定和 claim boundary。它只能把 candidate 变为 `qualified_eval_only`，不能产生 production 权限。

### 5.5 `repair_skill_confirmatory_decision.v1`

Phase 4 为每个 skill 写不可变 overlay，不修改 frozen skill bytes：skill ID/content hash、预注册 triggered strata、实际 exposure、paired effect/CI、multiplicity adjustment、unsafe/clean verdict，以及 `remain_eval_only | promotion_candidate | reject`。library-level `EQ-E` 只是必要条件，不能替代逐 skill 决策。`promotion_candidate` 只存在于该 overlay；它不是 base skill 的第二个可变 status。

### 5.6 `repair_skill_promotion_receipt.v1`

promotion receipt 与 active manifest 必须记录：

- single coordinator writer；
- candidate/implementation/qualification hashes；
- library before/after hash；
- exact added/changed/retired records；
- fault、clean、near-miss、attack outcomes；
- source clusters 与 digest-set non-intersection checks；
- confirmatory/lifecycle receipts 与 `promotion_candidate`/`active`/reject/quarantine 决策；
- rollback pointer；
- protocol version 和全部 deviation refs。

active manifest 只引用不可变的 `skill_id + content_sha256` 与已通过的 confirmatory/lifecycle receipts，不回写 base skill。quarantine/reject/retire 也只存在于 qualification/confirmatory/lifecycle overlays 与 ledger，不是 base skill 的可变状态。由此 base skill、decision overlays、active manifest 各有唯一权威字段。

### 5.7 生命周期与写权限

| 角色 | 可写 | 不可写 |
| --- | --- | --- |
| Incident collector | append-only incidents 与原始 artifacts | classification 真值、skill library |
| Miner/agent | candidate proposal、hypothesis、summary | core validator、evidence、active library |
| Executor | attempt-local candidate outputs | CAS source、task semantics、gate profile |
| Qualification coordinator | qualification receipt；冻结 eval-only library | production active library、held-out outcomes 驱动的补丁 |
| Held-out evaluator | outcomes/deviation ledger | frozen library、protocol、threshold |
| Promotion coordinator | Phase 4 confirmatory decision overlay；Phase 5 后 promotion receipt、active/rollback | 修改 frozen skill bytes、历史 evidence、补做 held-out patch |

## 6. 不可学习、不可修改的裁判

单 case 的 robust physical completion 定义为：

```text
Y(a,c,s,B) = 1，当且仅当：
  - 在固定候选/rollout/token 预算 B 内；
  - prompt 所需对象、关系和语义未删除或改写；
  - SceneSpec、ResolvedSceneSpec、catalog、package、asset snapshot、
    simulator/config、validator/gate profile 与 evidence 内容身份全部匹配；
  - static/package gate 和真实 SAPIEN runtime validation 都为 pass；
  - 没有 pending、incomplete、render-only 被发布为 final；
  - 没有 oracle 泄漏或 forbidden mutation。
否则 Y = 0。
```

候选永远不能写：

- `scene_gen/schema.py`、`scene_gen/validator.py` 或 runtime evidence producer；
- validator/gate thresholds；
- prompt、必需 object/relation；
- catalog 的测量真值、asset qualification 或 evidence hash；
- split/expected action/outcome；
- `is_static`、contact、outer-AABB 等安全语义以换取通过。

运行时应把 validator、collector、gate profile 和 simulator config 以固定 tree/content hash 只读挂载。任一漂移作废 confirmatory block。

## 7. 研究问题与预注册假设

### 7.1 问题与对比

| RQ | 主对比 | 可被证伪的假设 |
| --- | --- | --- |
| RQ0：失败能否被可靠定位？ | typed classifier vs frozen oracle/blinded audit | attribution precision、coverage、abstention 可量化；unknown 不被强行分类 |
| RQ1：typed trace 是否有用？ | Reactive − Unguided | held-out mPHRC 至少 +5pp，clustered 95% CI 下界 >0 |
| RQ2：population/history 是否有用？ | Evolutionary − Reactive | 至少 +5pp，CI 下界 >0 |
| RQ3：qualified memory 是否有用？ | Evo+Memory − Evolutionary | 至少 +5pp，CI 下界 >0；这是主确认性对比 |
| RQ4：是否跨 cluster 泛化？ | near-IID vs unseen asset/model/template/fault variant | 分别报告；不能用 seed 泛化冒充 variant/asset 泛化 |
| RQ5：是否负迁移？ | clean/near-miss/composite/unknown | unsafe=0、clean regression=0、错误检索与不必要 repair 受控 |
| RQ6：长期是否安全？ | stale/poison/dependency shifts | 过期时 abstain/requalify，poison 全拒绝，library 可 rollback |

### 7.2 RQ0 attribution protocol

在构建 skill 前先冻结一个不参与检索、候选生成或 qualification 的 240-case attribution set：F1–F5 各 20（100），F6–F7 各 20（40），OP、SE、UH、composite 各 15（60），再加 40 个 no-failure controls。140 个单根因环境故障和 40 个 clean 来自 Phase 1 预注册 calibration blocks；OP/SE/UH 共 45 个是不启动物理 rollout 的协议/设施/unknown fixtures；15 个 composite 是额外的真实多根因 SAPIEN runs，必须同时触发至少两个冻结 gate。它按完整 cluster key/source run 分组隔离，不能与后续 build/qualification/pilot/confirmatory 共享 evidence。

首个 classifier 是 protocol freeze 前写死的 deterministic router，只读取 `stage/code/failed-check-prefix/evidence-availability` 并输出 typed route 或 abstain；它不在这 240 cases 上拟合，也不能读自由文本 repair suggestion。若以后研究 learned classifier，必须另开 development split/protocol，不能复用本轮 RQ0 数字。

有 deterministic injector/gate 的案例以其冻结 receipt 为标签；自然或多模态案例由两名看不到 classifier 输出、repair proposal 和最终 arm 的领域审阅者只读原始 CAS evidence 独立标注，分歧由第三人裁决。classifier 只能看 production policy view，不能看到 injector 参数、预期 action、human-readable case name 或 split。

预注册输出为 confusion matrix、每类 precision/recall/coverage、selective-risk curve、environment-vs-ops/security dangerous false-route、unknown abstention 和全部 adjudication receipts。自动进入 repair retrieval 的 provisional 准入门为：macro precision ≥0.90、environment-repair 路由的 pooled one-sided 95% Wilson 下界 ≥0.85、coverage ≥0.75、OP/SE/UH/composite 被危险地路由为 environment repair 为 0、UH abstention ≥0.95。任一门失败，RQ1–RQ6 只能在人工给定 typed failure 的 exploratory 模式运行，不能声称自动 failure-to-skill 闭环。

### 7.3 Confirmatory 决策阈值

初始 confirmatory keep target：`Evo+Memory − Evolutionary ≥5pp` 且 hierarchical paired 95% CI 下界大于 0；绝对 mPHRC ≥0.80、worst-family ≥0.60。它们是**预注册决策阈值**，不是已有数据推导的事实。

这里的 `N` 明确定义为每个 admitted F1–F5 family 的 sealed held-out cluster 数，每 cluster 固定 6 paired fault seeds +6 matched clean seeds。Phase 0 冻结 `N_min=3`、`N_max=4`、效应阈值、two-sided α=0.05、power=0.80、8,600-rollout 总 cap 和一份 deterministic 20,000-draw power script。Phase 3 覆盖 F1–F5，并将四个 deployable arm labels 隐藏；先对每个 anonymous arm 算 cluster-level 六-seed completion，再取全部 physical-family pilot blocks 中所有 anonymous deployable-arm pair difference variance 的最大值作为保守 paired variance。脚本按五个 physical families 分层、目标差 0.05 模拟 `N=3` 和 `N=4`。最小达到 power 的 N 被冻结后才解盲；若 N=4，预留 300 evaluations 同比例增加四个 deployable arms 的 240 final evaluations 和 60 个共享 P0 fault/clean trace acquisitions。Oracle 不参与 primary power、保持 N=3。若 N=4 仍不足，研究标为 underpowered，不得下调 5pp、删 family 或把 seed 当独立样本。

若 completion 在预注册 `−2pp` 非劣界内且 rollout/token/wall time 至少降低 20%，才可作“效率收益”次主张；不能称 robust success 提升。

## 8. 等预算实验设计

### 8.1 四个 deployable arms + 一个 Oracle

本地 pilot 暂定 `K=4, T=2`，不冒充 ASPIRE 官方 `K=8,T≤5` 复现。四臂使用相同初始生成器 `P0`、模型 checkpoint、prompt scaffold、tool allowlist、sampling seeds、候选数、token、validator 和 simulator rollout 上限。

| Arm | 可见信息 | 允许的状态 |
| --- | --- | --- |
| U — Unguided | task、公开 API、初始候选；无 failure trace | 独立候选；无共享 history/memory |
| R — Reactive | 每条 lineage 只看自己的当前 typed trace | 无跨 lineage survivor、无跨 case memory |
| E — Evolutionary | pooled traces、candidate lineage、历史 survivors、淘汰假设 | pre-heldout population/history search；heldout controller 冻结且无 cross-case memory |
| EQ — Evo + Qualified Memory | 与 E 相同，加 trigger 检索到的冻结 skills | controller binary/prompt/sampling hash 与 E 完全相同；唯一处理差异是 read-only memory view |
| O — Oracle ceiling | 可见 injector/root-cause label，只能选择 allowlisted repair | 同预算；只作诊断上限，不参与 deployable superiority |

F1–F5 的 Oracle **必须执行**；若设施原因不能执行，必须在 protocol freeze 前删除并升级版本，不能重演 E2 的预注册后静默遗漏。F6/F7 从一开始就由各自 probe 定义辅助终点，不属于该 O arm。

`P0` first-pass 是四臂共享的零修复起点。主因果增量依次是 `R-U`、`E-R`、`EQ-E`。

### 8.2 Evolutionary loop

以下 loop 只运行在 search-debug clusters；它选择的是可迁移的 repair controller/operator policy，而不是把某个已修好的环境实例带到 held-out。为识别 memory 增量，E 路径决定公共 evolutionary controller，EQ 的 controller bytes/prompt/sampling config 强制复用 E；EQ search outcomes 只检查 memory-interface compatibility，不能改 controller 或 skill library。每轮：

1. 每候选声明 `hypothesis`、`parent`、`mutation/operator` 和“若错误，预期在哪个 gate 失败”；
2. 保留一个原样历史 best/P0 槽，防止最新候选自动覆盖；
3. 在完全相同 debug seeds 上执行；
4. 先淘汰语义/安全违规，再按权威 pass、worst-seed、残余 violations、成本排序；
5. E/EQ 的下一轮由 top survivors、失败 traces 和淘汰 ledger 条件化；R 的 lineages 不共享；
6. U/R/E 的 top-2 只进入 cluster-key 不同的一次独立 selection-validation；validation 不能反馈回搜索；EQ 复跑 E 的同一 top-2 只验证 memory-interface compatibility，不参与选择；
7. 选定 controller、prompt scaffold、operator set 和 library 冻结；EQ 记录的 controller/prompt/sampling hashes 必须与 E 相同，随后才进入全新 cluster 的 held-out。

不预设 crossover。候选差异来自 agent 提议的 typed operator/参数与 parentage；全部 lineage 都要落盘。

### 8.3 Fault families

主实验只纳入有独立 injector 和冻结 oracle/gate 的 family：

| Family | 故障 | 权威判据 |
| --- | --- | --- |
| F1 layout/geometry | 越界、table penetration、object overlap、错误 z/yaw | workspace、height、no-overlap、runtime penetration |
| F2 support | footprint 越界、错 target、间歇接触 | target-local margin、target/contact fraction |
| F3 containment | outer-AABB 假内含、底/顶越界、接触 table | local interior footprint/z、runtime containment、unexpected contact |
| F4 dynamics | 未 settle、漂移、掉落、错误 support mode | settled、drop、contact、drift（适用对象） |
| F5 asset physics | scale/collision mismatch、COM/inertia/friction、qpos | measured asset qualification + runtime/articulation；无 probe 则 exploratory |
| F6 observability/noise | occlusion、可见像素低、已知 RGB/depth/calibration/dropout 注入 | visibility + 冻结且独立的 perception/noise probe；不进入物理 mPHRC |
| F7 robot feasibility | initial collision、keepout、不可达 placement | robot collision + 冻结 reachability/task probe；无权威 probe 则 exploratory |
| S1 integrity attacks | stale/copied evidence、hash 错配、伪 pass、重复/非单调帧、decode/unique-frame 不一致 | fail-closed security/media suite，不作环境 repair |
| S2 composite/unknown | 多根因、同症状异根因、未见 family | abstention/negative-transfer/OOD suite |

物理主指标只覆盖通过 admission 的 F1–F5。F6 用单列的 observation recovery 指标；F7 用单列的 robot-feasibility recovery 指标；S1 是安全门；S2 是 abstention/OOD 门。不得把这些不同终点混进一个成功率。

每个 injector 入组前须独立通过 20 attack 全拒绝与 20 matched clean 全接受。未通过者在 protocol freeze 前移到 exploratory，不能用 agent/VLM 自评补真值。

自然失败进入 chronological corpus。只有通过单变量反事实或同初态 replay 能确认 causal transition 的自然案例，才参与 qualification；多根因案例保持 composite/unknown。

### 8.4 Grouped split，不能只分 seed

统计 cluster 定义为：

```text
task/prompt template × asset/geometry family × root-cause mechanism
```

seed 是 cluster 内重复，不是独立样本。完整 cluster key 的隔离优先于“换 seed”：

- Skill build：每 family 至少 2 个独立 clusters；允许探索和提 candidate；
- Qualification：每 skill 至少 30 fault cases，覆盖 ≥3 个此前未见 clusters、≥2 task/asset categories；加 30 clean 与 20 near-miss；
- Confirmatory search-debug：每 eligible family 2 个新 clusters ×2 seeds；F1–F5 运行 U/R/E/EQ/O，F6/F7 运行 U/R/E/EQ；
- Confirmatory selection-validation：每 family 1 个再新的 cluster ×2 seeds；U/R/E 选择 frozen controller，EQ 只对 E 的同一 top-2 做 compatibility replay，F1–F5 另跑 O，全部不返回搜索；
- Sealed held-out：每 family 3 个再新的 clusters，每 cluster 6 fault seeds +6 matched clean seeds；
- calibration/RQ0、build、qualification 的 keys 与 pilot/confirmatory 全部无交集；每个 phase 内 search-debug、selection-validation、sealed-heldout 两两无交集，pilot 与 confirmatory 的所有同名/异名 buckets 也交叉无交集；receipt 按 `(phase, case_role)` 计算 cluster/source-run/case/input/SceneSpec/ResolvedSceneSpec/injector digest sets 并硬断言任意禁止组合的交集为空；
- sealed fault 与 matched clean case 都先由独立 evaluator 运行一次共享 P0，冻结真实 pre-action trace 与 reset receipt；U 看不到该 trace，R/E/EQ 得到 byte-identical policy-view，F1–F5 的 O 另得 oracle label。基础 N=3 时该 acquisition 是 `7×3×(6 fault+6 clean)=252` 次真实 simulator evaluations，单列计费；clean P0 还用于测量错误 trigger/不必要 repair；
- held-out 每个 case 随后独立运行：controller/prompt/library 不变，case outcome 不进入后续 case 的 history、retrieval 或 library；每个 seed 只产出一个 final repair candidate；
- composite/unknown：每 family 2 clusters ×4 seeds，library freeze 后运行并单列。

因此主结果是**全新 cluster 上的 frozen-controller transfer**，不是同一 cluster 换 seed。若另做 ASPIRE 风格“在 target cluster 调试、在该 cluster 新 seeds 验证”，只能标为 `target-cluster-adapted / seed-heldout` exploratory diagnostic，不能并入主 mPHRC。

必须在 machine-readable receipt 中验证以下集合无交集：

- case IDs；
- 完整 cluster keys；
- canonical input/case digests；
- SceneSpec/ResolvedSceneSpec digests；
- injector parameter digests；
- source-run digests；
- OOD 部分的 asset/model digests、template IDs 和 fault variants。

held-out 前后 controller、prompt、operator-set 与 library SHA-256 必须相同。policy view 不得出现 family、variant、expect、expected action/outcome、human-readable test name 或 injector 参数。

### 8.5 Provisional budget

若 F1–F7 都通过各自 oracle/probe admission，最大规模为：

```text
每 family/arm：
  search-debug:        2 clusters × 4 candidates × 2 rounds × 2 seeds = 32
  selection-validation: 1 cluster × top-2 × 2 seeds                    = 4
  sealed held-out:       3 clusters × 6 fault seeds                     = 18
  matched clean:         3 clusters × 6 clean seeds                     = 18
  total                                                                    = 72 simulator evaluations

7 families × 4 deployable arms × 72 = 2,016 evaluations
Oracle 只覆盖物理主指标 F1–F5，不混入 deployable superiority；上限是 `5 × 72 = 360`。F6/F7 使用各自冻结 probe，不另设 O arm。
```

规划硬上限；Phase 0 先冻结 cap 和样本量重估公式，Phase 3 后再按盲化方差重估冻结最终 N：

- Phase 1 calibration/RQ0：`7×40 + 15 composite + 25 clean-baseline repeats = 320`；40 个 no-failure attribution controls 从 140 个 matched clean 中预注册抽取；
- five-physical-family blind pilot（含 Oracle）：`5×5×72 = 1,800` arm evaluations，加 `5×3×(6 fault+6 clean)=180` shared P0 traces；硬 cap 2,000；
- skill build/qualification：≤800，其中最多 5 skills 的正式 qualification 为 `5×(30 skill-fault +30 matched no-skill +30 clean +20 near-miss)=550`，causal build/search reserve ≤250；
- confirmatory deployable arms：≤2,016；
- confirmatory Oracle ceiling（F1–F5）：≤360；
- sealed held-out shared P0 trace acquisition（基础 N=3）：`7×3×(6 fault+6 clean) = 252`；
- per-skill `EQ-mask(skill)` diagnostics：最多 `5×(18 fault+18 clean)=180`；
- lifecycle/negative-transfer：最多 5 个 promotion candidates；每 skill 为 staleness 100 evaluations + negative transfer `100 paired cases×2 arms=200 evaluations` + rollback/requalification workflow smoke 80 + matched-repeat reserve 60 =440，另加全库 poison 60；最大 `5×440+60=2,260`；
- blinded power expansion reserve：≤300，只允许把 admitted F1–F5 的四个 deployable arms 从 N=3 等比例扩到 N=4（240 final +60 shared P0 fault/clean traces）；
- 显式最大分配：`320+2,000+800+2,016+360+252+180+2,260+300 = 8,488`；总硬上限 8,600 simulator evaluations，剩余 112 不构成可自由追加预算；
- candidate-generation calls ≤2,000；
- LLM ≤16M tokens；
- 单卡 ≤14 GPU-days；
- evidence storage ≤600 GB。

这些是规划 cap，不是已经获准消耗的资源，也不是论文尺度复现。未通过 admission 的 family 或未形成 promotion candidate 的逐-skill预算，不在看过 outcome 后转给其他 family/skill。若实测单 rollout 时间表明不可行，必须在未打开 arm outcome 前升 protocol version、按预注册公式重做 power/成本设计；不能事后缩小 held-out 或改阈值。

每 candidate 先过 compile/static/package gate；static fail 消耗 candidate slot但不启动 physics rollout。达到 cap 后状态是 `incomplete`，不能挑选已成功子集报告。

## 9. Qualification 与 promotion

### 9.1 单 case 因果证据

一个 failure-to-repair 对至少需要：

1. 固定初始 input/package/dependencies/seed；
2. baseline 在可复现尝试中触发同一权威失败；
3. 只改变一个声明的 typed repair operator；
4. repaired candidate 通过同一不可变 gate；
5. revert/no-repair 对照仍失败，或有等价 matched counterfactual；
6. clean 和 near-miss 未被错误修改；
7. 全部 before/after artifacts 内容寻址。

单 seed/transient 只能是 run-local memo。

### 9.2 Provisional skill qualification gate

候选进入 frozen evaluation library 须同时满足：

- source 来自至少 2 个 digest-distinct incident clusters；
- qualification fault 至少 24/30 通过；
- 相同 executor 无该 skill 的 matched arm 至少低 20pp；
- clean 30/30 通过；
- near-miss 至少 19/20 正确 abstain；
- unsafe publication=0；
- identity/package/evidence binding=100%；
- 不触及 forbidden surface；
- qualification receipt、内容版本与 library freeze 可独立重算。

这些只是 `qualified_eval_only` 准入门，不等于 confirmatory 统计结论，也不授予 production 权限。若 skill 只适用于一个 asset digest，保存为 asset-local override/memo，不称 general skill。

### 9.3 Production promotion gate

library-level `EQ-E` 和 hard safety gates 只是必要条件。第一轮研究每个 admitted F1–F5 family 最多预指定一个 promotion-evaluable skill；其他 skills 保持 `qualified_eval_only`。每个被指定 skill 在打开 outcome 前必须冻结唯一 trigger，并在 sealed held-out 中获得至少 3 个独立 clusters、18 个 pre-repair trigger-positive fault cases 和 18 个 matched clean exposures。trigger 在 arm outcome 前由冻结 trace 判定；逐 skill stratum 中 EQ policy view 只包含该唯一检索结果，不暴露其他 skill 内容。多个 skills 同时触发的 composite 不归因给任何单 skill。

逐 skill 使用预注册的 `EQ-mask(skill)` diagnostic：保持 EQ controller、prompt、sampling、其他可见字段完全相同，只用 evaluation-only overlay 将该唯一 skill 替换为 typed `no_match`。在同一 triggered stratum 比较 EQ 与 EQ-mask，要求点估计至少 +5pp、hierarchical paired 95% CI 下界 >0、unsafe=0、18/18 clean 不回归、wrong repair=0；对 promotion-evaluable skills 的 efficacy tests 用 Holm 控制 familywise α=0.05。每个 skill 最多增加 18 fault +18 clean =36 evaluations，最多 5 skills/180 evaluations。未达到 exposure、效应或安全门者保持 eval-only，即使整个 library 显著更好也不得搭便车。F6/F7 若未来要 active，需另开使用 mOHRC/mFHRC 的同构 protocol。

Phase 4 通过后，Promotion coordinator 只写 `repair_skill_confirmatory_decision.v1` overlay，把满足逐 skill 门的记录标成 `promotion_candidate`，不修改 frozen skill bytes。某个 skill 的 Phase 5 staleness/poison/negative-transfer/rollback receipts 全部通过后，才可把该 `skill_id + content_sha256` 以 two-phase transaction 写入 active manifest，并追加 promotion receipt。事务必须在 crash 前保持旧 manifest 可用；失败时不允许出现部分 active 状态。receipt 记录 before/after hashes、exact diff、dependency keys、expiry 和 rollback target。若底层存储尚不能证明多文件事务，则只能生成 integration proposal，不能声称 active 或 atomic production promotion。

### 9.4 检索策略的实验顺序

1. 首先使用 exact typed key：`stage/code/check-prefix/relation/backend/gate-profile`；
2. 再加入 asset geometry/task family guard；
3. 遇到多个冲突 skill 默认 abstain；
4. semantic embedding retrieval 只在 exact-key baseline 完成后作为独立实验；
5. retrieval score 永远不能覆盖 applicability/exclusion 或 validator。

这样先回答“机制是否可行”，再回答“模糊检索能否提高 coverage”。

## 10. 指标与统计

### 10.1 主指标

`mPHRC` 指 mean Physical Held-out Robust Completion，只覆盖通过 admission 的 F1–F5：先对同一 cluster 内 paired seeds 聚合，再对 clusters 聚合，最后对 physical failure families 宏平均。

```text
mPHRC_arm =
  across families macro-mean(
    across clusters mean(
      across paired seeds mean(Y)
    )
  )
```

即先 seed→cluster，再 cluster→family，最后 family 等权。不能把所有 seed 当 IID。

主确认性 contrast：`EQ − E`。`R−U` 与 `E−R` 是预注册机制性次对比，用来拆 trace 与 evolution 的贡献。

### 10.2 次指标

- per-family 与 worst-family completion；
- F6 的 `mOHRC`（mean Observation Held-out Recovery）与 F7 的 `mFHRC`（mean Feasibility Held-out Recovery），各自单列且不并入 mPHRC；
- first-pass completion；
- success-vs-rollout-budget curve；
- physical rollouts、candidate/validator calls、tokens、wall time、storage；
- unsafe publication 与 clean regression；
- wrong retrieval、unnecessary repair、negative transfer、abstention；
- trigger precision/coverage；
- identity/package/evidence binding；
- gate breakdown：penetration、settling、drop、support/contact、containment、visibility、qpos；
- infrastructure failure，单独报告。

### 10.3 推断

1. 同 cluster、同 seed paired comparison；
2. 20,000 次 hierarchical paired bootstrap：family 内重采 clusters，再 cluster 内重采 paired seeds；
3. cluster-level sign-flip/randomization 作为敏感性分析；
4. mixed-effects logistic model 只作次分析；
5. primary `EQ-E` 只检验一次，two-sided familywise α=0.05；`R-U` 与 `E-R` 作为两个机制性次对比用 Holm 校正 α=0.05，其结果不 gate primary；
6. CI 退化或独立 cluster 不足时，只给 effect size/逐 case outcome，不作总体 superiority；
7. 观察到 0 unsafe 时仍报告 one-sided 95% 上界，不表述为“风险为零”。

由 arm 自己造成的 timeout/OOM 计失败。只有确认所有 arms 共享设施故障时，才可将整个 matched block 一起重跑一次；不能只删除某臂失败。

## 11. Negative transfer、staleness 与 poisoning

### 11.1 Negative transfer suite

每个 promotion candidate 各自冻结 5 类 ×20 paired cases =100；每 case 都运行 frozen E 与 EQ 两臂，因此是 200 simulator evaluations/skill：

- 同 trigger 的 true positives；
- 表面 signature 相似、根因不同的 near-miss；
- 原本通过的 clean；
- 两条冲突 skill 的 composite；
- 完全未知 family。

报告 `E pass / EQ fail`、wrong retrieval、unnecessary repair、abstention 和成本上升。任何 clean 被 memory arm 修坏都触发 no-promotion。

### 11.2 Staleness matrix

对每个 promotion candidate 依次改变；每类 20 matched cases，共 100/skill：

1. simulator/physics dependency digest；
2. validator/gate profile；
3. visual/collision asset digest；
4. camera calibration/noise profile；
5. task/embodiment/workspace profile。

合法行为只有 exact-compatible 执行，或 abstain + requalify。旧 skill 在新身份上静默激活即失败。

### 11.3 Poisoning attacks

对完整 frozen library/ingestion path 冻结 6 类 ×10 attacks，共 60（全局一次，不按 skill 重复）：

- 重复同一 incident 冒充独立证据；
- scene/evidence hash 错配；
- agent 文本或伪 report 自称 pass；
- trace/asset metadata prompt injection；
- held-out label/action 泄漏；
- repair 尝试修改 validator、collector、threshold、任务语义或 evidence。

要求 60/60 拒绝。任何 poison 进入 frozen evaluation/promotion-candidate library，立即停止 promotion；已完成的效能结果不覆盖 library safety failure。

### 11.4 Rollback/requalification

每个 promotion candidate 冻结 4 类 identity shifts ×20 matched cases =80：validator/gate、simulator dependency、asset/collision identity、camera/task profile。每个 block 只做 requalification workflow smoke：先证明旧 skill fail-closed，再验证新 identity 会产生独立 qualification request，最后用隔离的 baseline-active manifest fixture 模拟 transaction 中断并确认旧 manifest 完整；20 cases **不满足** §9.2 的正式资格门，也不授予新 identity active 权限。真正 requalification 必须另开 protocol，重新满足 30 fault +30 clean +20 near-miss 与 matched no-skill 对照。每个 skill 另保留 60 matched evaluations，只能用于预注册的 all-arm repeat 或 transaction-crash 复验，不能追加到表现较好的 family。

Phase 5 的每条 outcome 和 verdict 都必须以 `skill_id + content_sha256` 为键。逐 skill 任一 lifecycle 门失败，只能写一个 `quarantined/reject` lifecycle overlay；不得改 base skill，library-size/conflict 汇总也不能掩盖它。若单 skills 都通过但组合产生冲突，整个组合 manifest 保持 inactive，直到预注册 conflict rule 通过新 protocol 验证。

## 12. 分阶段执行计划

### Phase 0 — Protocol freeze 与可信 instrumentation

目标：在运行任何新 outcome 前冻结问题、身份、schema、预算、split 和偏差规则。

工作：

1. 实现/验证六种 schemas；
2. 将 compile/solver/static/runtime/ops/security trace 映射成 incident；
3. 生成 validator/gate/simulator/model/dependency identity receipt；
4. 冻结 fault injector spec、split、arms、Oracle、效应阈值、硬 cap、盲化样本量重估公式和统计脚本；
5. 运行 leakage、hash binding、forbidden-write 与 duplicate-evidence attack tests；
6. 冻结 provisional minimum 与“超 cap 即 underpowered”的处理，不在 Phase 0 伪造尚未观测的 cluster variance。

Exit gate：incident 事实能从 CAS artifacts 独立重算；policy view 不含隐藏 label；library freeze 可验证；否则不进入 Phase 1。

### Phase 1 — Injector calibration 与 clean SAPIEN baseline

目标：证明 fault label 来自冻结 oracle，而不是 agent opinion。

工作：

1. 对每 family 构建 parameterized injector；
2. 每 injector 20 injected faults +20 matched clean；
3. 另做 15 个真实多根因 composite runs 和25个跨 family clean baseline repeats；RQ0 的40个 no-failure controls 从140个 matched clean 中预注册抽取；
4. 复跑已知 task/asset families 的 static/package gate 与正式 `900 settle + 120 contact/video`；
5. 记录设施失败率、reset reproducibility 和单 rollout 成本；
6. 无权威 oracle 的 family 移到 exploratory。

Exit gate：每个 injector 的 injected fault 20/20 被权威 gate 拒绝、matched clean 20/20 被接受；真实 clean pipeline 可复现。失败则先修测量系统，不研究 memory。

### Phase 2 — Incident corpus、因果修复与 skill qualification

目标：建立首批 support、containment、overlap/penetration/stability skills。

工作：

1. 先收 chronological natural failures，不挑成功案例；
2. 同时运行受控 injector cases；
3. 对每 incident 做 reproduce→single-operator A/B→clean/near-miss；
4. 生成 candidates，保留 rejected/unknown/composite；
5. 运行 qualification，生成 `qualified_eval_only` receipts；
6. 冻结 evaluation library hash，不写 active production library。

Exit gate：至少 3 个 core physical families 各有一个通过 qualification 的 skill；否则记录负结果并停止大规模 confirmatory。

### Phase 3 — Core real-SAPIEN matched pilot

目标：验证整个 matched arm、reset、evidence、统计与成本管线，不作最终 superiority 主张。

范围固定为 F1 layout/penetration、F2 support、F3 containment、F4 dynamics、F5 asset physics，按 §8.4 的 search/selection/sealed-heldout role 形成完整盲化块；U/R/E/EQ/O 按固定随机顺序执行。F6/F7 只在独立 probe admission 后作为辅助终点，不参与主 power estimate。

每候选：

```text
static/package gate
→ headless load/no-motion preflight
→ 900-step settle + 120-step terminal contact window
→ sequential evidence/video
→ authoritative runtime validation
→ 环境先 pass，才进入 downstream robot feasibility/task probe
```

Exit gate：全部身份/预算/split receipts 完整；设施故障 ≤5%；无 unsafe。独立统计员只读取 pooled、arm-label-blinded cluster variance/runtime，按预注册公式冻结最终 N；随后 pilot arm outcomes 才可作为诊断打开。pilot 不能作 superiority 主张，也不能改变 controller-generating algorithm、prompt scaffold、operator set、library、retriever、success definition、family、effect threshold 或 gate；Phase 4 只能按已冻结算法在预注册 search/selection buckets 中选择 final controller。

### Phase 4 — Confirmatory grouped held-out

目标：回答 RQ1–RQ5。

工作：

1. 在 protocol/library SHA 冻结后运行全新 clusters；
2. 全部 admitted families 跑四个 deployable arms；Oracle ceiling 只在 F1–F5 单列；
3. held-out 期间禁止 library update；
4. 一次性运行预注册统计；
5. 对预指定 promotion-evaluable skills 运行预算内 `EQ-mask(skill)` diagnostics；
6. 同时报告 clean、near-miss、unknown、cost 与全 case outcomes。

只有 library-level `EQ−E` keep gate、absolute/worst-family、安全/clean/binding gates 全通过，才保留“qualified memory improves robust simulator-environment repair”作为候选结论；随后按 §9.3 的逐 skill exposure、效应、安全和 Holm 门写 confirmatory decision overlays。只有逐 skill 通过者成为 `promotion_candidate`；其余保持 eval-only，此时没有任何 skill active。

### Phase 5 — Lifecycle/OOD

目标：回答长期库是否会 stale、冲突、污染和负迁移。

工作：在隔离的 promotion-candidate overlay 上依次做 dependency/camera/asset/task shifts、composite failures、conflicting skills、poison attacks、rollback/requalification workflow smoke；重复 library sizes 和 retrieval policies。

若 promotion-candidate library 的 clean/unknown 安全性随规模恶化，即使 Phase 4 有平均收益，也必须限制作用域或拒绝 activation。每个 skill 只有自己的逐项 lifecycle verdict 全通过才有资格写 active manifest；底层事务尚未被证明时只输出 integration proposal，不报告 active。

## 13. Keep、Stop、Abort 规则

### Keep

- 主增量 ≥5pp 且 hierarchical paired 95% CI 下界 >0；
- final mPHRC ≥0.80、worst-family ≥0.60；
- unsafe publication=0、clean regression=0、binding=100%；
- worst-family 不相对前臂回归超过预注册 margin；
- poison/stale/negative-transfer gates 通过。

### Stop 单个搜索

- round 1 已对全部 debug seeds 通过，可提前进入一次性 validation；
- 或达到固定 `K,T`；
- validation 不能返回搜索；
- semantic laundering/unsafe candidate 立即淘汰但消耗 slot；
- 最新候选不能自动覆盖历史 best。

### Abort/作废 confirmatory

- validator/collector/gate/sim/model/library identity 漂移；
- held-out 期间 library 改变；
- split、digest 或 source-run 有交集；
- policy 看到了 hidden family/variant/expected action/outcome；
- task semantics 被修改；
- arms 的 budget/model/tools 不一致；
- 无法恢复的 shared infrastructure blocks >5%；
- evidence manifest 不完整或 binding 失败；
- cap 耗尽；
- 预注册 family 缺 oracle，却被主观视觉/agent judge 替代。

出现 unsafe publication 后可在隔离模式继续收诊断，但该 arm/library 不得 promotion/deploy。

## 14. 可见资产与逐阶段产物

### 14.1 目录约定

```text
self_improving/studies/ASPIRE/environment_failure_skills/
  README.md
  research_log.md
  decision_log.jsonl
  claim_ledger.md
  sources/
    source_receipt.json
  protocol/
    protocol.vN.yaml
    protocol.sha256
    arms.yaml
    budgets.yaml
    power_analysis.py
    power_analysis.sha256
    failure_router.yaml
    failure_router.sha256
    fault_injectors.yaml
    split_manifest.json
    split_receipt.json
    deviation_ledger.jsonl
  schemas/
    failure_attribution_case.v1.schema.json
    environment_failure_incident.v1.schema.json
    environment_repair_skill.v1.schema.json
    repair_skill_qualification_receipt.v1.schema.json
    repair_skill_confirmatory_decision.v1.schema.json
    repair_skill_promotion_receipt.v1.schema.json
  incidents/
    index.jsonl
    <incident_id>.json
  skills/
    candidates/
    qualified/
    rejected/
    quarantined/
    confirmatory_decisions/
    promotion_candidates/
    lifecycle_verdicts/
    promotion_ledger.jsonl
    library_manifest.json
    active_manifest_or_proposal.json
  experiments/
    calibration/
      attribution_cases.jsonl
      attribution_metrics.json
    pilot/
    confirmatory/
    lifecycle/
  artifacts/
    runs/<run_id>/
  gallery/
  reports/
    pilot_report.md
    attribution_report.md
    confirmatory_report.md
    final_report.md
    case_outcomes.jsonl
    summary.json
    statistics.json
```

大体积 SAPIEN media/runtime outputs 可保持 Git-ignored，但其相对 locator、bytes、SHA-256、生成命令和 producer identity 必须进入 committed receipt。不得提交 RoboTwin assets、checkpoint、secret 或本机绝对私有路径。

### 14.2 Step → visible assets matrix

| 步骤 | 机器可读资产 | 人可见资产 | 作用 |
| --- | --- | --- | --- |
| Source/protocol freeze | `source_receipt.json`, `protocol.vN.yaml`, hashes | `README.md`, `claim_ledger.md` | 固定 claim、源码、预算、判据 |
| Taxonomy/schema | 6 JSON Schemas、`fault_injectors.yaml` | taxonomy 表与 schema 文档 | 分开 attribution、incident、candidate、qualification、confirmatory decision、production promotion |
| Attribution gate | `attribution_cases.jsonl`, confusion/selective-risk metrics | `attribution_report.md` | 先证明失败路由可靠；失败则禁止自动 failure-to-skill claim |
| Split | `split_manifest.json`, cluster/digest sets/receipt | split summary | 证明 build/qualification/search/selection/held-out 完整 cluster key 隔离 |
| 每个失败 run | invocation、events、spec/resolved/catalog/package、incident、runtime evidence、validation、command receipt | start/mid/end PNG、MP4、trace summary | 复现失败且定位阶段 |
| 每个修复 candidate | proposal、parentage、operator params、budget、before/after hashes | candidate diff、before/after montage | 证明实际改了什么 |
| Evolution round | leaderboard、survivors、eliminated hypotheses、all outcomes | round table/plot | 防止只保存赢家 |
| Qualification | causal A/B、fault/clean/near-miss/attack outcomes | qualification report | 只决定 candidate 能否进入 frozen eval-only library |
| Promotion | confirmatory/lifecycle receipts、before/after library hashes、exact diff、rollback | active skill card 或 integration proposal | Phase 5 后且事务被证明才由单 writer 激活；否则只提案 |
| Pilot/confirmatory | all case outcomes、bootstrap/randomization、`EQ-mask(skill)` results | completion/cost/negative-transfer plots | library 主效应、逐 skill 效应与边界 |
| Lifecycle | per-skill stale/negative-transfer/requalification verdicts、global poison/conflict outcomes、rollback receipt | failure gallery、lifecycle matrix | 逐 skill 检验长期安全，避免库平均掩盖失败 |
| Final | frozen result artifact 与 hashes | `final_report.md`, gallery index | 结论、负结果、偏差、下一步 |

每个真实 SAPIEN run 的最小可见 media 是 `observer_start.png`、`observer_mid.png`、`observer_end.png` 和有真实顺序/互异帧的 `observer_runtime.mp4`。这些只用于诊断/审阅；物理 verdict 仍来自 runtime validation JSON。

### 14.3 本轮实际生成的资产

本次 failure-skill research-plan 子任务只做调研和计划，新增：

- `environment_failure_skill_research_plan.md`（本文件）。

没有运行新的 SAPIEN、没有生成新的 skill、图片、视频或统计结果。同一 dirty 工作树中的其他 modified/untracked/concurrent assets 不归因于本子任务；已有 ASPIRE 论文审计、源码审计、E2 和 SAPIEN evidence 是输入证据，也不应重标为本轮产物。

## 15. Protocol deviation 与全程留痕

运行前生成带 hash 的 frozen protocol。每个偏差 append-only 记录：

```yaml
timestamp: ...
detected_before_or_after_outcome_access: before | after
reason: ...
affected_cases: [...]
affected_artifact_sha256: [...]
decision: continue | matched_rerun | invalidate | exploratory_only
claim_boundary_change: ...
```

规则：

- outcome 未打开前的实质变更：升 protocol version，重建 split；
- outcome 打开后的 metric/family/threshold/exclusion 变更：原 confirmatory 不补写，另开 exploratory；
- 设施重排只有 config/hash 不变且 all-arms matched rerun 才允许；
- case exclusion 只能用预注册 infrastructure reason，并对所有 arms 同时应用；
- final 报告列 expected/realized counts、missing blocks、全部偏差和 claim 影响。

研究中的每项决定、失败命令、negative result、被拒 skill、预算耗尽与 unknown 都进入 `research_log.md`/`decision_log.jsonl`。不允许只留 promoted skills。

## 16. 风险与缓解

| 风险 | 第一性原理问题 | 缓解 |
| --- | --- | --- |
| Validator gaming | 优化了评分器而非环境 | core/gate read-only hash；forbidden writes；attack suite |
| Label leakage | agent 知道 injector/expected repair | policy view 最小化；split/digest audit；Oracle 单列 |
| Seed/template overfit | 同分布重复被误读为泛化 | grouped clusters；OOD asset/template/variant；macro metric |
| Raw-memory poisoning | agent 文本被当真值 | deterministic facts 分字段；CAS evidence；single writer |
| Negative transfer | 相似症状不同根因 | exclusions、near-miss、conflict abstain、clean gate |
| Stale skills | simulator/camera/asset 变化 | exact dependency keys、expiry、requalification、rollback |
| VLM circularity | 同一模型既提案又裁决 | VLM 只诊断；冻结 deterministic/independent probe |
| Infrastructure contamination | timeout 被当环境负例 | stable routing；matched block policy；单列统计 |
| Multi-root failures | 错误因果归因 | composite/unknown；单变量反事实才 qualification |
| Selection bias | 只保存成功 candidate | 保存全 population、budget、失败/淘汰 ledger |
| Compute escalation | memory arm 获得更多 rollout | matched hard budget；success-vs-budget curve |
| Library bloat | stale/重复/冲突导致非单调收益 | dedupe、size ablation、conflict graph、retire/quarantine |

## 17. 研究准备过程留痕

本计划的形成过程：

| 序号 | 动作 | 观察 | 决策/产物 |
| --- | --- | --- | --- |
| 1 | 读取本仓 `AGENTS.md` 与 research skill | 要求一手来源、可追溯、repo-doc sync；不得污染 `scene_gen` 信任边界 | 采用一手资料 + committed source audit；输出单一 plan 文档 |
| 2 | 读取规定 dashboard 入口 | 三个 read endpoint 均返回 HTTP 404 | 不伪报 task 状态，也不把控制面故障当研究结果 |
| 3 | 复核 ASPIRE 论文、官方页、固定源码 | 识别 Algorithm 1、debug/eval 隔离、top survivors、promotion ledger；skill 风险含 stale/specific/redundant/misleading | 迁移实验结构，不迁移结论/raw exec |
| 4 | 审计本项目 committed `dcf6be0` | compiler/solver/runtime 已有足够 typed failures；工作区有并发 dirty changes | 所有 present capability claim 钉在 committed snapshot |
| 5 | 复核现有 RGB memory 与 E2 | 一次 0/3→3/3 harness decision；synthetic E2 为 0/.5/1 但有 variant/CI/Oracle 边界 | 作为 plausibility/preflight，不作物理证据 |
| 6 | 复核现有 negative result | failure-to-data retrain 后 held-out 仍 1/4，promotion rejected | 把 reject/unknown 作为一等结果 |
| 7 | 做 failure-surface 与 protocol adversarial audit | ops/security/pending 必须分流；seed 不是独立单位；memory effect 要与 trace/evolution 分离 | 四臂等预算 + Oracle；cluster-level inference；poison/stale suite |
| 8 | 查邻近一手工作 | FATE/REFLECT/Reflexion/Eureka分别支持分层 repair、trace 压缩、memory ablation、executable fitness | 只借设计选择，逐项保留不能支持的 claim |
| 9 | 手动执行 repo-doc foreground sync gate | reader guide 已明确 `scene_gen`/platform 边界、render≠physics、runtime gates；本轮无行为变更 | `answer-only`；不修改 reader-facing repo-docs |
| 10 | 编写本计划 | 未执行新仿真或实现 | 唯一新增资产为本文件 |

## 18. 最终决策树

```text
Phase 0 evidence/split/gate 不可信？
  └─ 是：STOP，先修 instrumentation

Phase 1 injector/clean SAPIEN 不可复现？
  └─ 是：STOP，不研究 memory

Phase 2 没有 ≥3 core physical families 通过 qualification？
  └─ 是：保留 incidents/negative result，不做大规模 confirmatory

Phase 3 发现 unsafe、leakage、budget mismatch 或 shared infra >5%？
  └─ 是：INVALIDATE/修协议后重开，不补挑 cases

Phase 4 library-level EQ−E 效应和 hard gates 都通过？
  ├─ 否，成功不升但成本显著降且非劣：只保留 efficiency candidate
  ├─ 否：拒绝 robust-memory hypothesis
  └─ 是：逐 skill 检查 triggered exposure、Holm-adjusted efficacy 与安全门
             ├─ 未通过/证据不足：保持 qualified_eval_only
             └─ 通过：只写 promotion_candidate overlay，进入 Phase 5 lifecycle

Phase 5 stale/poison/negative-transfer 失败？
  ├─ 某 skill 失败：按 skill_id+hash quarantine；不得被 library 平均掩盖
  ├─ 组合冲突：整个组合 manifest 保持 inactive
  └─ 某 skill 全通过且事务已证明：生成该 skill promotion receipt 并写 active manifest
```

## 19. 一手来源与本地证据索引

### ASPIRE

- [论文 HTML](https://arxiv.org/html/2607.00272)
- [官方项目页](https://research.nvidia.com/labs/gear/aspire/)
- [官方源码](https://github.com/NVlabs/ASPIRE/tree/7ba73d3bcac8f6b6d4a7d67ed4040988f768d282)
- [本地论文/官方来源审计](paper_and_official_sources.md)
- [本地上游源码审计](upstream_source_audit.md)

### 本项目

- [当前 Harness 审计](current_harness_audit.md)
- [既有实验协议](experiment_protocol.md)
- [既有最终报告与 E2 边界](final_report.md)
- [研究全过程](research_log.md)
- [RGB failure memory](../../alchedata/artifacts/text2env_empirics/rgb_adapter_failure_memory_v1.json)
- [RGB matched ablation](../../alchedata/artifacts/text2env_empirics/memory_ablation_rgb_adapter_v1.json)
- [failure-to-data negative result](../../alchedata/artifacts/diagnosis/placement_robustness_diagnosis.json)

### 邻近一手资料

- [FATE](https://arxiv.org/abs/2603.01505)
- [REFLECT](https://robot-reflect.github.io/) / [paper](https://arxiv.org/abs/2306.15724) / [code](https://github.com/real-stanford/reflect)
- [Reflexion](https://arxiv.org/abs/2303.11366) / [code](https://github.com/noahshinn/reflexion)
- [Eureka](https://eureka-research.github.io/) / [paper](https://arxiv.org/abs/2310.12931) / [code](https://github.com/eureka-research/Eureka)

## 20. 计划结论

从机制上看，失败环境与成功样例库是互补知识源：失败给出约束边界、错误触发条件和反例，成功给出可行分布。本计划只检验 failure memory 相对 no-memory/current-trace 的增量，没有等容量 success-memory arm，因此不比较二者孰优。只有以下闭环才配称 self-improving：

```text
immutable failure evidence
→ typed causal diagnosis
→ bounded candidate repair
→ same-state counterfactual replay
→ clean / near-miss / attack qualification
→ frozen held-out transfer
→ versioned promotion / expiry / rollback
```

本项目已有的 solver trace、runtime physics checks、CAS/identity 和一次窄范围 memory ablation，使这项研究具备现实起点；现有证据仍不足以支持真实物理收益。因此推荐按 Phase 0→5 推进，并把第一确认性问题固定为 `EQ−E`：**qualified cross-case memory 是否在相同搜索预算下，比仅有 typed trace/history 的 frozen evolutionary controller 更好。**
