# Harness SkillDescriptor v2 reproducibility evidence — 2026-08-31

真实物理回放会包含仿真浮点、媒体编码和 cgroup 资源指标。即使输入、资产与执行器身份完全相同，
也不能在没有实证时声称所有输出 bytes 位级一致。旧公共 descriptor 却只允许
`deterministic=true`；继续沿用它会让 replay qualification 建立在错误声明上。

本切片没有放宽或重解释既有 schema。`harness.skill_descriptor.v1` 继续只接受
`deterministic=true`，其 committed JSON snapshot 前后 SHA-256 均为
`271ddb20c81954a29295b8cbc5488d53f7a6abcafc27fde7e8ae17ae6b878ea2`。v1 输入不会被 Registry
静默转换成新模型。

新增 `harness.skill_descriptor.v2`，删除含糊的 boolean，要求 `reproducibility` 严格取一个值：

- `content_bitwise_deterministic`：同一类型化输入和精确依赖身份必须得到逐字节相同的 typed output
  与 artifact 内容。
- `evidence_invariant_repeatable`：物理、媒体、耗时与资源 bytes 可以变化，但固定 qualification case
  必须重新满足每一个具名证据不变量。

v1 与 v2 互相拒绝对方字段。Registry 的 register/list/resolve 接受两种明确模型并原样保存，不做
载荷转换；两个版本仍引用严格的 `harness.skill_qualification.v1` receipt。compile 继续用 v1 的
确定性声明，replay factory 改为 v2 的 evidence-invariant 声明。这个改动只让后续资格声明变得
诚实，并没有生成或伪造 replay pass bundle。

专项覆盖 common schema、catalog、Registry、compile/replay descriptor factory 与 replay handler，
结果为 `209 passed`；四个被测模块共 1,127 statements / 358 branches，全部 100%。schema exporter
复核 15 份 committed snapshot。新增 v2 snapshot SHA-256 为
`2e0bd92c3312b174e84c4c92561f3bb956ba8ce77b1148eb22809c56c180fa90`。
