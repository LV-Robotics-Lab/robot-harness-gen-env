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

**Scene IR（场景中间表示）**:
把源媒体解释规范化为坐标系、实体、变换、物理属性要求和实体关系的后端可核语义；它不选择具体
资产，也不表示场景已经可被仿真器加载。
_Avoid_: asset binding、resolved simulator scene、render layout

**Task IR（任务中间表示）**:
与 Scene IR 实体对齐的任务目标、成功条件、reset/action/observation 需求和终止条件；缺少这些能力时
只能表达刚体场景目标，不能宣称机器人策略就绪。
_Avoid_: free-form prompt、policy implementation、trajectory

**Asset Qualification（资产资格）**:
把一个精确资产模型、表示字节闭包与指定仿真后端/能力 profile 的已核证据绑定起来的不可变结论。
它不同于 Skill qualification，也不能从目录命中或相似度分数推断。
_Avoid_: Skill qualification、catalog membership、visual similarity

**Asset Closure（资产闭包）**:
物化选定资产表示所需的完整、可移植、内容寻址成员集合；逻辑路径与字节身份属于闭包，本地主机路径
不属于闭包。
_Avoid_: asset directory path、partial manifest、download hint

**Runtime Content Manifest（运行时内容清单）**:
把明确声明的运行时成员、逻辑位置和逐字节身份固定下来的可移植清单；清单完整性只针对声明的
成员范围，不单独证明实际执行所需的依赖已经全部纳入。
_Avoid_: production runtime qualification、deployment path、import closure proof

**Runtime Deployment Mapping（运行时部署映射）**:
把运行时内容清单的逻辑位置映射到某次部署中的实际位置；位置可以变化，已验证内容身份不能变化。
_Avoid_: runtime identity、qualification、implicit source lookup

**Runtime Execution Receipt（运行时执行回执）**:
把固定运行时、一次具体请求、实际过程观测与该次产物绑定起来的不可变记录。回执的内容完整性与
回执的可信执行来源是不同事实；单独持有一份自洽记录不等于有权签发资产资格。
_Avoid_: content manifest、self-reported execution、asset qualification

**Asset Binding（资产绑定）**:
把每个 Scene IR 实体一一映射到选定且已资格化的资产表示、闭包和场景角色的显式清单；它是语义场景
与可物化环境包之间的桥梁。
_Avoid_: asset selection alone、implicit catalog lookup、runtime materialization path

**Source Media Identity（源媒体身份）**:
由原始媒体字节摘要、解码后可核事实和规范化产物共同确定的图像或视频身份；图像与视频是不同
类型，视频身份包含完整有序帧序列而不只是抽帧。
_Avoid_: filename、MIME claim、sampled frames

**Legacy-adapted Environment Candidate（旧格式适配环境候选）**:
由固定旧产物经 anti-corruption adapter 映射出的非就绪环境候选；它保留来源但不继承旧系统的运行
通过或发布状态。
_Avoid_: Genesis-native proof、sim-ready environment、promoted output

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

**Harness Workflow Controller（Harness 工作流控制器）**:
面向用户入口并拥有任务生命周期的控制者；它创建和恢复任务、校验建议、执行 Skill、记录状态与重试，
并依据真实验证结果决定是否发布。
_Avoid_: Codex session、external tool driver、chat-owned workflow

**Managed Codex Backend（受管理 Codex 后端）**:
由 Harness 在有界上下文、动作集合和预算内调用的智能组件；它提供理解、规划、视觉判断和诊断建议，
但不拥有工作流状态、Skill 执行或发布权威。
_Avoid_: external workflow owner、Qwen planner、VLM authority、trusted executor

**System 2 Planner（系统二规划器）**:
根据受信世界状态选择下一项版本化 Skill，并在失败后提议重试、修复、回放或停止的推理角色；
在目标架构中该角色由 Harness 管理的 CodexBackend 承担，工作流控制权仍属于 Harness。
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
