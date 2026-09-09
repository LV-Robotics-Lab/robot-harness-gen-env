# Robot Harness / Self-Improving Context

本上下文统一 Text2Env Harness 与 Self-Improving 平台的核心术语，避免把接口执行、物理验证和发布资格混为一谈。

## Language

**Compilation（编译）**:
把语义请求确定性地转换为类型化场景、grounded/resolved scene、哈希绑定环境包和静态验证报告；它复用现有 `scene_gen` 行为，不代表真实物理验证已经完成。
_Avoid_: environment generation、physical validation

**Environment Reuse（环境复用）**:
复用一个与当前请求兼容、来源可追溯且重新核验通过的既有环境包。
_Avoid_: cache hit、asset reuse

**Asset Reuse（资产复用）**:
为场景中的对象选择已存在且满足语义、几何、来源和仿真兼容约束的资产。
_Avoid_: environment reuse、asset generation

**Digital Cousin（数字近亲）**:
与目标对象不完全相同、但在任务相关的语义、几何和物理属性上足够相近，并经过验证可替代的资产。
_Avoid_: exact asset、unverified look-alike

**Asset Generation（资产生成）**:
当受允许的复用层都无法满足请求时，产生带完整来源记录并须经过准入验证的新资产。
_Avoid_: asset reuse、unbounded text-to-3D

**Static Validation（静态验证）**:
只根据类型化场景、resolved scene、资产与包内容执行的确定性检查；缺少真实运行时证据时，其结论可以是 `incomplete`。
_Avoid_: replay、physical proof

**Replay Evidence（回放证据）**:
在真实仿真执行中产生并与 resolved scene 哈希绑定的连续物理、接触、稳定性、可见性和视频证据。
_Avoid_: render、start/end screenshots

**Publishability（发布资格）**:
在编译、回放、验证、哈希绑定、资格报告和全部门控都满足后得出的可发布结论；只有 validate 可以给出该结论。
_Avoid_: run success、validation completeness

**Self-Improving Loop（自改进闭环）**:
把复用选择、拒绝原因、生成、回放和验证结果沉淀为可追溯证据，并用这些证据改进后续检索、选择、诊断和资产晋升的循环。
_Avoid_: one-shot generation、untracked fallback

## Golden E2E 语言

**Golden Workflow（黄金工作流）**:
把一次文本、图像、视频或组合请求，与其全部 Skill 尝试、状态推进、证据和晋升决定关联在
同一父级身份下的可恢复闭环。
_Avoid_: single Skill run、chat session、one-shot demo

**Genesis Rigid Scene（Genesis 刚体场景）**:
能由 Genesis 原生加载，并在启用碰撞的非零物理步进中通过冻结的刚体物理门与连续媒体门的
环境；它不承诺机器人接口或策略测试能力。
_Avoid_: render-only scene、robot-policy ready、successful process exit

**Genesis Robot-Policy Ready（Genesis 机器人策略就绪）**:
在 Genesis Rigid Scene 之上，提供可重复 reset、受约束 action、与 step 绑定的
observation/termination 及可重读 trajectory 的环境能力；它不表示训练策略成功或真机能力。
_Avoid_: rigid replay、trained-policy success、real-robot ready

**Promoted Golden（已晋升黄金产物）**:
已经达到 Genesis Robot-Policy Ready、由 validate 判定 publishable、通过晋升门并能从可移植
证据闭包重启回放的最终 golden 产物。
_Avoid_: successful demo、validated render、unpromoted candidate

## System 2 执行语言

**Trusted World State（受信世界状态）**:
规划器可以据此决策的当前世界事实集合；每项事实都来自类型化输入、新鲜观测或可验证回执，并带明确时效和来源。
_Avoid_: chat context、unchecked memory、scene description

**External Codex Agent（外部 Codex Agent）**:
位于 Harness 受信边界之外，读取资格化能力与受信证据并提出规划、诊断和视觉判断的认知
参与者；其输出是不受信建议，不能授予物理通过或发布资格。
_Avoid_: internal model provider、Qwen planner、VLM authority、trusted executor

**System 2 Planner（系统二规划器）**:
根据受信世界状态选择下一项版本化 Skill，并在失败后提议重试、修复、回放或停止的推理角色；
在目标架构中该角色由 External Codex Agent 承担，而不是 Harness 内部的模型 provider。
_Avoid_: internal Qwen planner、monolithic VLA、free-form chatbot、skill executor

**Planner Context（规划上下文）**:
从受信世界状态、已资格化 Skill 和回执绑定的近期历史中按固定预算编译出的只读投影；它记录被省略信息的集合摘要，并与源状态摘要绑定。
_Avoid_: chat transcript、whole repository dump、mutable memory

**Planner Decision（规划决策）**:
LLM 对单个下一步的类型化提议，只能选择上下文列出的精确 Skill、请求新鲜观测或停止；Harness 必须重新校验状态与上下文摘要后才能执行。
_Avoid_: executable model output、free-form tool name、self-reported success

**Skill（技能）**:
具有精确版本、类型化输入输出、资格证据和稳定执行契约的能力单元；它可以由解析器、控制器、VLA、仿真器或适配器实现。
_Avoid_: arbitrary function、prompt snippet、unversioned tool

**Skill Invocation（技能调用）**:
一次由精确 Skill 版本、规范参数和全部依赖身份共同确定的执行请求。
_Avoid_: tool name only、best-effort call

**Tool Result（工具结果）**:
一次 Skill 调用的类型化结果包，至少区分输出、状态增量、诊断、可信回执和新鲜观测；失败也必须产生可分类证据。
_Avoid_: raw text answer、exit code only

**State Delta（状态增量）**:
Tool Result 对受信世界状态提出的显式变化；只有通过绑定与新鲜度检查后才能提交到下一版世界状态。
_Avoid_: implicit memory mutation、log side effect

**Trusted Receipt（可信回执）**:
把调用、实现与依赖身份、实际执行事实、输出闭包和证据摘要交叉绑定的不可变记录。
_Avoid_: success flag、mutable report path、operator note

**Fresh Observation（新鲜观测）**:
在当前调用的真实执行边界之后采集、并与该调用和世界状态版本绑定的传感或仿真观测。
_Avoid_: cached screenshot、prior-run observation

**Local Snapshot Assessment（本地快照评估）**:
对一次已在同一本地 CAS 中对账的 replay 证据所做的确定性重算记录；它不是新鲜观测、可移植权威或世界状态更新。
_Avoid_: fresh observation、validation decision、trusted receipt

## 自改进语言

**Failure Evidence（失败证据）**:
失败调用留下的类型化 blocker、诊断、部分产物、观测和回执闭包；它是后续聚类与修复的输入，不是可丢弃异常文本。
_Avoid_: stack trace only、silent retry

**Repair Candidate（修复候选）**:
由一组失败证据驱动、对 Harness、Skill、路由、prompt 或评估集提出的版本化变更，并带可重放假设和预期改进范围。
_Avoid_: live patch、untracked prompt tweak

**Promotion Gate（晋升门禁）**:
比较候选与当前基线的固定回归决策；只有证据闭包完整、目标指标改善且保护性指标不退化时才允许候选成为新基线。
_Avoid_: one successful demo、manual approval alone
