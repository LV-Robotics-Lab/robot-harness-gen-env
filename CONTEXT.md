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
