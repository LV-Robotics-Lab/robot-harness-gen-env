# Text2Env Validate v2 信任边界与验收规约

日期：2026-08-31
状态：设计冻结，尚未实现或资格化
目标 Skill：`text2env.validate@2.0.0`

## 结论

`text2env.validate@1.0.0` 只能重算物理门禁报告，不能证明一条 replay 可晋升。
v2 必须把 compile package、replay execution receipt、运行资产快照、解码媒体、
运行时证据、资格证据和实际 Run 记录收敛为一个只从 CAS 读取的验证决定。
任何缺失、额外、跨对象摘要不一致或外部 locator 都必须 fail closed。

## 为什么不能扩充 v1

v1 的公开输入只有 `EnvironmentPackage`、`runtime_evidence` 和 gate profile。
它无法恢复以下事实：

- replay 用的是哪一个 `RuntimeConfig`、运行时 capability 和 handler policy；
- 视频、PNG 是否被真实解码，运行资产是否来自同一个不可变 snapshot；
- replay `Invocation`、终态 `RunState`、事件 journal 和 execution receipt 是否一致；
- compile/replay/validate 的实现是否是已资格化的版本；
- 请求、scene、catalog、package 和 replay 是否属于同一条链。

因此 v1 保持永久非 publishable；v2 使用新的 major-version typed input/output，
不通过可选 policy 或闭包推断缺失事实。

## v2 输入

输入至少包含下列 typed refs，且 locator 必须是 `artifact://sha256/...`：

1. `environment_package`
2. `compile_run_receipt`
3. `compile_qualification`
4. `replay_run_receipt`
5. `replay_qualification`
6. `replay_execution_receipt`
7. `runtime_evidence`
8. `runtime_validation_report`
9. `runtime_event_transcript`
10. `runtime_asset_snapshot_manifest`
11. `media_verification_receipt`
12. `request_provenance`
13. `gate_profile`

输入不接受宿主目录、`file://`、可变 URL 或调用方注入的 EligibilityVerifier。
运行资产成员和媒体成员由被验证的 manifest/receipt 闭包解析，不由调用方重复枚举。

## 纯 CAS 决策流程

1. 逐个解析原始 typed ref；先验证每一个原始 ref，再按内容身份去重。
2. materialize package 和 runtime-asset snapshot 到当前 attempt 的新目录。
3. 验证 package manifest 的全部 member bytes，并重算 SceneSpec、ResolvedSceneSpec、
   catalog 和 package canonical digests。
4. 验证 runtime-asset manifest 的 exact tree、全部 member bytes、空目录、闭包和
   resolved/catalog 绑定。
5. 严格解析 replay execution receipt，并验证它列出的 capability、五项 replay
   dependencies、runtime config、event transcript、runtime evidence、validation report、
   media 和 runtime-asset snapshot。
6. 严格解析 replay/compile qualification；资格实现摘要必须分别等于 receipt 和
   run 记录声明的实现摘要。
7. 从 CAS 中解析 Invocation、terminal RunState 和 event journal，验证 run_id、
   invocation digest、attempt、typed output、artifact closure 与 receipt 一致。
8. 使用 materialized snapshot locator 构造 validation view。不得调用
   `ResolvedSceneSpec.source_files` 中的原宿主路径。
9. 调用物理 validator 重算 report；它只读取 package 和 snapshot 中的文件。
10. 生成 validation decision receipt，绑定全部输入 ref、policy identity、重算报告、
    failed checks、publishable 决定和 validator implementation digest。

validate 不启动仿真；它复核既有 replay 证据。物理 fail/incomplete 是成功产出的 typed
验证结果，不是 handler 内部失败。只有无法构造权威决定时才 blocked/failed。

## 发布资格

只有同时满足以下条件才允许 `publishable=true`：

- compile、replay、validate 三个 Skill 的严格资格均通过并绑定当前实现；
- compile/replay Run 终态成功且 journal/receipt/typed output 完整一致；
- replay evidence acquisition 成功；
- 媒体真实解码，帧数、FPS、维度、唯一帧门槛与 runtime evidence 一致；
- runtime-asset snapshot exact-tree 验证通过；
- 物理 validation 状态为 pass，`fail_count=0`、`not_run_count=0`；
- request → scene → resolved → catalog → package → replay → validation 全链摘要一致；
- policy/validator/dependency identities 都进入当前 Invocation dependencies；
- validation decision receipt 已成功写入 CAS，并可重新读取验证。

VLM 结果只能作为诊断或 fallback route evidence，不能替代上述物理门禁。

## 确定性声明

在 v2 完全移除宿主路径读取、所有 policy/dependency identity 进入 Invocation、
且 decision receipt 只由 canonical CAS bytes 决定后，才可声明
`content_bitwise_deterministic`。在此之前 descriptor 必须不存在，不能以
`evidence_invariant_repeatable` 掩盖外部状态依赖。

## 必须保留的攻击测试

- 任一输入使用 `file://`，即使 SHA 正确也拒绝；
- 同内容一个合法 ref 加一个错误 locator/ref，不能被预去重遮住；
- package/runtime-asset/member/media/receipt 任一 byte 被改写；
- receipt 声称的 runtime config、dependency、capability、run_id、attempt 或 output 漂移；
- journal 缺事件、额外事件、乱序、终态后事件或 envelope 内容漂移；
- resolved 原宿主资产存在/删除/替换，都不得改变同一 v2 输入的决定；
- runtime snapshot 缺 member、多 member、symlink、FIFO、空目录丢失或闭包外引用；
- MP4 伪字节、帧数不足、重复帧、错误 FPS/SAR/codec 或 decoder identity 漂移；
- 物理 report 与 runtime evidence 绑定不同 resolved scene；
- 真 qualification 配任意 handler/validator policy，必须无法注册或调用；
- validation report 已 pass 但 compile/replay receipt 或 qualification 缺失；
- decision receipt 写入失败时不得返回 publishable output；
- 同一 typed input 在清理所有原宿主路径后，报告和 decision receipt 逐字节相同。

## 首条真实验收

固定 case：`Place a can on top of a plate.`，seed 7，RoboTwin/SAPIEN，
`precheck=0`、`settle=900`、`contact-window=120`、`video=120`、`fps=12`。
输入必须来自当前 Harness replay qualification 的同一 CAS closure；不得复用历史目录
冒充当前 run。验收保存：v2 typed input/output、全部 CAS refs、decision receipt、
重算 validation report、完整 Run journal 和一次从空工作目录复核的结果。

## 实施切片

1. 新 schema 与 strict CAS resolver；
2. replay/compile run receipt 与 qualification 聚合 verifier；
3. snapshot-aware validator adapter，删除宿主 `source_files` 依赖；
4. validation decision receipt 与参数感知 dependency resolver；
5. v2 handler/Registry production assembly；
6. fake 闭包集成、攻击测试、固定真实 case 资格化；
7. v2 bundle、安装态验证和 repo-docs 同步。
