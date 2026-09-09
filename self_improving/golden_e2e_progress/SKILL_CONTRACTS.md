# X2Env Skill 合同草案

状态：`accepted_2026-09-09`

本页冻结公共语义；具体 Pydantic 字段在 RED 测试切片中按此实现。现有
`text2env.compile/replay@1.0.0` 保持兼容，不原地改变其 SAPIEN 语义。

## 1. 版本与公开工具

| Skill ref | MCP tool | 作用 |
| --- | --- | --- |
| `text2env.compile@2.0.0` | `text2env_compile_v2_0_0` | text-primary 输入到 Genesis candidate package |
| `text2env.replay@2.0.0` | `text2env_replay_v2_0_0` | 对 text lineage package 做真实 Genesis replay |
| `text2env.validate@2.0.0` | `text2env_validate_v2_0_0` | 重算证据与 profile satisfaction |
| `image2env.compile@1.0.0` | `image2env_compile_v1_0_0` | image-primary，可带 text/image 补充 |
| `image2env.replay@1.0.0` | `image2env_replay_v1_0_0` | 对 image lineage package 做真实 Genesis replay |
| `image2env.validate@1.0.0` | `image2env_validate_v1_0_0` | 重算证据与 profile satisfaction |
| `video2env.compile@1.0.0` | `video2env_compile_v1_0_0` | video-primary，可带 text/image/video 补充 |
| `video2env.replay@1.0.0` | `video2env_replay_v1_0_0` | 对 video lineage package 做真实 Genesis replay |
| `video2env.validate@1.0.0` | `video2env_validate_v1_0_0` | 重算证据与 profile satisfaction |
| `genesis.observe@1.0.0` | `genesis_observe_v1_0_0` | 在当前执行边界后捕获 fresh visual/runtime observation |
| `environment.promote@1.0.0` | `environment_promote_v1_0_0` | 消费已 publishable 的 validate receipt并原子晋升 |

validate Skill 的版本与既有 `harness.text2env_validate_input.v2` schema 名相同并不代表旧未实现路径
自动获得资格；实现时必须为新 handler/application 单独出 descriptor、qualification 和 decision
receipt。若字段审查表明语义不兼容，则在 RED 切片中升为 `text2env.validate@3.0.0`，不得静默改 v2。

## 2. Workflow command envelope

每个 exact Skill MCP 工具使用相同外层 envelope；只有 `parameters` 进入 Skill input codec：

```text
SkillCommandEnvelope[T]
- schema_version
- workflow_run_id
- command_id
- expected_workflow_revision
- expected_state_sha256
- expected_receipt_head_sha256
- idempotency_key
- parameters: T
```

同一 `(principal, workflow, tool, idempotency_key)` 与相同 canonical digest 幂等返回原 operation；同
key 不同 digest、旧 revision/state/head 或并行第二个 state-mutating command 都在执行前拒绝。

## 3. 输入归一化

`InputBundleV1`：

```text
- bundle_id / bundle_sha256
- objective_ref                         # UTF-8 text CAS artifact，可为空字符串但必须有 provenance
- parts[]
  - part_id
  - modality: text | image | video
  - artifact_ref
  - normalized_artifact_ref
  - media_probe_ref | null
- primary_input_id
- requested_profile_ref
- created_at
- provenance_ref
```

规则：

- 至少一个 part；ID 唯一且排序规范。
- text 在 workflow create 时 UTF-8/CAS 化；image/video 必须先 ingest 并重算 bytes/hash/MIME。
- image 解码后保存尺寸、色彩模式和规范 PNG identity；拒绝伪 MIME、解码炸弹和超限输入。
- video 必须完整探测/解码，保留容器 hash、总帧数、唯一帧数、FPS、时长、逐帧顺序摘要；不能只
  保存 15 张抽帧并声称完整视频。
- `primary_input_id` 决定 façade：text/image/video compile 必须分别匹配 text/image/video primary。
  补充模态全部保留 provenance。
- video → image fallback 必须显式创建 frame ArtifactRef 和新 prompt revision，再调用 image Skill；
  原 video failure 仍在 history。
- 网络 URL 和未哈希的任意绝对路径不进入 Skill input。

## 4. Codex interpretation 与 advisory

Codex 在调用 compile 时提交 `SceneInterpretationProposalV1`：

```text
- input_bundle_ref / base_state_sha256
- entities[] / relations[] / unknowns[]
- task_intent
- cited_input_parts[]
- requested_asset_strategy
- confidence / abstention
- proposal_sha256
```

Harness 只验证 schema、引用、状态和允许范围；它不把 Codex 的对象、颜色或关系判断自动升级为
可信事实。compile 的确定性输出必须绑定这份 proposal 和原始输入。

`harness_advisory_record_v1` 接受三种 advisory：`visual_assessment`、`diagnosis`、`repair_proposal`。
advisory 必须引用 Codex 实际读取的 fresh observation/diagnostic refs，并生成
`ExternalAgentAdvisoryReceipt`；它只进入 attempt history。

`harness_prompt_revision_record_v1` 从原 prompt + advisory + patch 生成不可变 `PromptRevision`。下一次
compile 必须显式引用 predecessor package、失败 receipt 和 revision；禁止隐式覆盖或静默 fallback。

## 5. Compile contracts

三类 compile input 共享：

```text
- input_bundle_ref
- interpretation_proposal_ref
- requested_profile_ref
- acquisition_policy
- seed
- predecessor_package_ref | null
- repair_evidence_refs[]
```

模态 input codec 额外强制 primary 要求。`acquisition_policy` 可为 `auto`、`exact_only`、
`digital_cousin_allowed`、`generate_on_miss`；它不允许跳过 license、geometry、collision 或 backend
capability 门。

每个对象的资产决策必须是以下显式 lane 之一：

- `environment_reuse`：复用完整环境包并重新验证输入/profile兼容性。
- `exact_asset_reuse`：语义和 identity 精确匹配、closure 可用、Genesis qualification 新鲜。
- `digital_cousin_reuse`：非 exact；必须记录 task-relevant affordance/geometry/physics 等价维度、差异、
  阈值和验证证据，不能称为同一资产。
- `fresh_asset_generation`：复用耗尽后进入 run-owned staging；generation QC 不等于物理资格或入库。

成功输出统一为 `X2EnvCompileOutputV1`：

```text
- package_ref: EnvironmentPackageV2
- scene_ir_ref
- task_ir_ref
- asset_decision_refs[]
- staged_asset_refs[]
- static_validation_ref
- compile_receipt_ref
- predecessor_package_ref | null
```

compile success 只表示 package closure 和 static validation 成立，不表示 replay、publishable 或权威资产
入库成功。

## 6. Shared IR 与 package

首批冻结四个模型：

- `ObservationBundleV1`：原始输入、候选观察、unknowns、provenance；agent claim 不自动可信。
- `SceneIRV1`：frames、entities、transforms、visual/collision/support roles、physical properties、
  support/containment/attachment/proximity relations、affordances、asset requirements。
- `TaskIRV1`：embodiment、reset、action、observation、goal/success、reward、termination、capture refs。
- `EnvironmentPackageV2`：input/SceneIR/TaskIR、全部资产 closure、Genesis backend variant、runtime lock、
  requested/satisfied capability refs、binding manifest。

`package_id` 绑定整棵内容闭包，不再等于 resolved scene hash。manifest 只含 POSIX 逻辑相对路径；本地
materialization path 只进入单次 receipt。Simulator-specific extension 必须有 schema+digest，不能是自由
dict。

## 7. Genesis profiles

中间 profile：

- `genesis.rigid_scene@1`：portable load、collision enabled、非零 step、接触/支撑/穿透/稳定性、连续
  media。它不声称可供策略使用。

最终完成指标建议定义为 `genesis.robot_policy@1`：

- 满足 `genesis.rigid_scene@1`；
- 固定、版本化 robot profile（首条建议 `franka_tabletop@1`）；
- 可重置且 reset state 有 digest；
- typed action/observation/termination contract；
- 真实执行至少一个 bounded 非零 action sequence，验证 actuator/state 响应；
- RGB、proprioception 和 contact observation 有 shape/dtype/frame/time binding；
- trajectory recorder 保存逐 step action/observation/timestamp/hash；
- 可选 task goal/success/reward 实际执行；缺任何项时不得声称 task-policy ready。

本任务不声称真机、训练过的 policy 或任意任务成功。`genesis.robot_policy@1` 只证明该环境能通过冻结
接口被机器人策略驱动和采样。

## 8. Replay contracts

共享 replay input：

```text
- package_ref / compile_receipt_ref
- requested_profile_ref
- runtime_attestation_ref
- seed
- physics_plan
- capture_plan
- robot_probe_plan
```

成功输出：

```text
- replay_evidence_ref
- physics_report_ref
- robot_probe_report_ref | null
- trajectory_ref | null
- image_refs[]
- video_ref
- media_verification_ref
- runtime_receipt_ref
- fresh_observation_refs[]
```

replay 必须从新的 materialization root 复核完整 closure、使用 pinned Genesis/adapter identity、启用
collision、实际调用非零 `Scene.step()`；请求视频时保存真实顺序帧并报告 total/unique。baseline/half-dt
双回放的稳定性可作为 rigid profile 的保护性门，但稳定化 intervention 的状态不得用作 pass 证据。

## 9. Observe / validate / promote

`genesis.observe` 必须在当前调用执行边界之后从 sealed package/checkpoint 重新捕获，绑定 workflow、
child replay run、媒体 hash、capture time 与 TTL。历史截图重新贴标签不算 fresh observation。MCP 可把小
图作为 ImageContent 返回；structured result 始终保留 CAS ArtifactRef。

validate 只从同一 portable CAS closure 重读并重算：

- package/asset/runtime/qualification/receipt lineage；
- physics、media、robot probe、trajectory 和 requested capability satisfaction；
- fresh observation binding；Codex advisory 仅是支持材料，不能覆盖确定性失败。

成功执行 validate 但结论 `publishable=false` 是合法 typed output，不是 MCP protocol error。只有
`publishable=true` 的 validation decision 才可交给 `environment.promote`。

promotion 不重新判断物理；它验证 validate decision 仍新鲜、package/registry/runtime identity 未漂移，
然后在 ephemeral registry 原子发布 package/qualified staged assets。生产 publication 另需 operator
scope，Codex不能在参数中自报批准人。

## 10. Qualification 与组合门

每个 façade exact version 必须有：committed schema snapshot、implementation manifest、qualification
report、固定 regression command 和至少一个真实组合 case。component pass 不能自动推出任意组合 pass。

资格必须覆盖：

- 每个模态的 input normalization 和至少一个 compile/replay/validate；
- exact reuse、digital cousin、fresh generation 三 lane；
- package relocation 后重新 materialize/replay；
- Genesis fixed runtime、collision/nonzero steps、media decode；
- `genesis.robot_policy@1` 的 reset/action/observation/trajectory probe；
- forged agent physical-pass、stale observation、missing closure、path escape、duplicate video frames、
  Gujie status conflict、dependency/RegistrySnapshot drift 的 fail-closed 攻击测试。

## 11. 稳定错误分类

`HARN_AUTH_DENIED`、`HARN_IDEMPOTENCY_CONFLICT`、`HARN_STALE_STATE`、
`HARN_SKILL_UNQUALIFIED`、`HARN_INPUT_SCHEMA_INVALID`、`HARN_ARTIFACT_UNAVAILABLE`、
`HARN_DEPENDENCY_DRIFT`、`HARN_EXECUTION_BLOCKED`、`HARN_EXECUTION_FAILED`、
`HARN_OBSERVATION_UNBOUND`、`HARN_DIAGNOSIS_UNSUBSTANTIATED`、
`HARN_VALIDATION_INCOMPLETE`、`HARN_PROMOTION_DENIED`、`HARN_PERSISTENCE_CONFLICT`。

unknown MCP tool/resource、malformed JSON-RPC 和未协商 capability 是协议错误；已注册工具的领域失败
返回 `isError=true` 的 structured ToolResult，并保留 RunState、blocker、diagnostics 和 receipts。
