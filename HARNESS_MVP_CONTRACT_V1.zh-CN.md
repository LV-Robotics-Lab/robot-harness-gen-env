# Harness MVP 契约 v1

- 状态：提议中
- 契约版本：`1.0.0`
- 范围：最小 Harness 注册表以及现有的 Text2Env Stage 0-5 路径
- 权威性：本契约定义面向 Harness 的接口；现有的 `scene_gen` schema、包清单和验证报告仍是各自载荷的权威定义。

## 决策与边界

进程内的 `SkillRegistry` 是执行、类型、版本和审计的唯一权威。MCP 是根据已注册描述符生成的无状态协议适配器。它不得拥有业务类型、选择版本、重试调用、更改结果或决定发布。

本契约仅涵盖 Text2Env 编译、RoboTwin/SAPIEN 回放和验证。它不定义 Anchor2Env、网络资产搜索、第二个仿真器、Transfer、插件热加载、分布式调度、策略执行、数据采集、训练或评估。

## Skill 标识与版本控制

一个 Skill 的标识是元组 `(skill_id, version)`，其规范显示形式为 `<skill_id>@<version>`。`skill_id` 必须匹配 `^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$`。注册版本使用稳定的 SemVer `MAJOR.MINOR.PATCH`，不得有前导零、预发布后缀或构建后缀。每次调用都必须指定精确版本；禁止使用版本范围、隐式 `latest` 和静默回退。

| 规范 Skill 引用 | MCP 工具名称 | 输入 schema | 输出 schema |
| --- | --- | --- | --- |
| `text2env.compile@1.0.0` | `text2env_compile_v1_0_0` | `harness.text2env_compile_input.v1` | `harness.text2env_compile_output.v1` |
| `text2env.replay@1.0.0` | `text2env_replay_v1_0_0` | `harness.text2env_replay_input.v1` | `harness.text2env_replay_output.v1` |
| `text2env.validate@1.0.0` | `text2env_validate_v1_0_0` | `harness.text2env_validate_input.v1` | `harness.text2env_validate_output.v1` |

MCP 名称通过机械规则派生：将 `skill_id` 中的 `.` 替换为 `_`，在版本前加 `_v`，并将版本中的点替换为 `_`。规范 Skill 引用仍是所有记录中保存的标识。

版本变更遵循以下规则：

- `MAJOR`：删除字段或重命名字段；添加必填字段或任何输出字段；缩小有效输入范围；更改默认值、状态/错误含义、确定性或发布门控。
- `MINOR`：添加默认值不变的可选输入、扩大接受的输入范围、在现有制品集合中添加制品，或添加不改变现有结果的故障关闭错误码。
- `PATCH`：修复实现缺陷，但不更改公共 schema 或声明的语义。

Skill SemVer 独立于 Python 包版本、`robotwin.*.v1` schema 标识符、包清单版本和 `compiler_version`；每一项都单独记录。已注册的 `(skill_id, version)` 不可变。以不同的描述符或实现摘要重新注册它，必须以 `HARN_VERSION_UNSUPPORTED` 失败。公共 JSON schema 的每次形状变更都要获得新的整数 schema 标识符；破坏性形状变更还要求提升 Skill 主版本。消费者必须保留未知的 blocker 代码并采用故障关闭策略，因此新增代码属于次版本变更。

## 通用记录

所有通用记录都采用严格模式：拒绝未知字段，绝不猜测必填的未知值，并且 JSON 使用 UTF-8、排序后的键和紧凑分隔符进行规范哈希计算。

| 记录 | 必填字段 |
| --- | --- |
| `SkillDescriptor` (`harness.skill_descriptor.v1`) | `skill_id`, `version`, `mcp_tool_name`, `input_schema`, `output_schema`, `implementation_name`, `implementation_version`, `implementation_sha256`, `deterministic=true`, `max_attempts`, `qualification_artifact` |
| `Invocation` (`harness.skill_invocation.v1`) | `run_id`, `skill_id`, `skill_version`, `effective_parameters`, `dependencies`, `max_attempts`, `invocation_digest` |
| `RunState` (`harness.run_state.v1`) | `run_id`, `invocation_digest`, `skill_id`, `skill_version`, `status`, `attempt`, `max_attempts`, `started_at`, `ended_at`, `events`, `artifacts`, `output`, `blocker` |
| `Event` (`harness.event.v1`) | `seq`, `timestamp`, `stage`, `attempt`, `from_status`, `to_status`, `artifact_refs` |
| `ArtifactRef` (`harness.artifact_ref.v1`) | `name`, `uri`, `media_type`, `sha256`, `bytes`, `schema_version` |
| `Blocker` (`harness.blocker.v1`) | `code`, `message`, `stage`, `retryable`, `details`, `unknowns`, `artifact_refs` |

`run_id` 是审计标识符，不是内容标识。`ArtifactRef.uri` 是定位符，不能作为可信的标识依据；消费者必须验证 `sha256`。对于类型化制品，`schema_version` 是字符串；对于无类型媒体则为 `null`。`unknowns` 包含字段严格限定为 `field`、`reason` 和 `source` 的记录。

可空字段仍须存在：运行期间 `ended_at=null`；首个事件中 `Event.from_status=null`；预检阶段出现 Skill/版本/输入/依赖 blocker 时 `invocation_digest=null`；在类型化输出存在之前 `output=null`；除非运行被阻止或失败，否则 `RunState.blocker=null`。只有在预检已解析出精确描述符、有效参数和依赖之后，才创建 `Invocation` 记录。`qualification_artifact.schema_version` 必须为 `harness.skill_qualification.v1`；其载荷严格包含 `skill_ref`、`status="pass"`、`deterministic_case_id`、`regression_command` 和 `report_sha256`。

`dependencies` 按 `name` 排序；每个条目严格包含 `name`、`version` 和 `sha256`。计算哈希前展开默认值。`invocation_digest` 为：

```text
sha256(canonical_json({
  "skill_id": skill_id,
  "skill_version": skill_version,
  "effective_parameters": parameters_with_artifact_uris_replaced_by_content_identity,
  "dependencies": dependencies_sorted_by_name,
  "max_attempts": max_attempts
}))
```

制品的内容标识严格由 `media_type`、`schema_version` 和 `sha256` 组成。该摘要不包括 `run_id`、时间戳、事件数据、制品位置和输出目录。请求文本严格按接收时的原样计算哈希；Harness 不执行语言规范化。

每个描述符中的 `max_attempts` 是固定值：compile 为 1，replay 为 2，validate 为 1。只有 blocker 的 `retryable=true` 的结果，才能在同一 `run_id` 和 `invocation_digest` 下开始另一次尝试。每次尝试都会发出事件。调用方和 MCP 都不能更改此策略。

## Text2Env Skill 契约

| Skill | 必填输入 | 必填输出 |
| --- | --- | --- |
| `text2env.compile@1.0.0` | `request`, `seed`, `asset_catalog: ArtifactRef`, `config.generate_missing_assets` | `scene_spec: ArtifactRef`, `resolved_scene: ArtifactRef`, `environment_package: EnvironmentPackage`, `static_validation: ArtifactRef` |
| `text2env.replay@1.0.0` | `environment_package`, `runtime_config` | `runtime_evidence: ArtifactRef`, `replay_artifacts: tuple[ArtifactRef, ...]` |
| `text2env.validate@1.0.0` | `environment_package`, `runtime_evidence`, `gate_profile="robotwin.scene_validation.v1"` | `validation_report: ArtifactRef`, `validation_status`, `publishable`, `blockers: tuple[Blocker, ...]` |

`seed` 是 0 到 2,147,483,647（含）之间的整数。compile 请求只能包含语义意图；`SceneSpec` 仍禁止路径、资产标识符、位置和四元数。可信资产位置从 `ArtifactRef` 和依赖记录中解析，绝不从请求文本中解析。compile 的 `config` 严格包含 `generate_missing_assets: bool`，默认值为 `false`。replay 的 `runtime_config` 严格包含 `precheck_steps`、`settle_steps`、`contact_window_steps`、`video_frames` 和 `fps`，其有效默认值依次为 0、900、120、120 和 12；所有有效值都参与 `invocation_digest` 的计算。

`EnvironmentPackage` (`harness.environment_package.v1`) 是对现有哈希绑定包的不可变引用，而不是重复的包格式。它严格包含 `package_id`、`route_id="text2env"`、`producer_skill_ref`、`seed`、`scene_spec_sha256`、`resolved_scene_sha256`、`asset_catalog: ArtifactRef` 和 `package_manifest: ArtifactRef`。`package_id` 等于 `resolved_scene_sha256`，而且 `asset_catalog.sha256` 必须等于 resolved scene 和 package manifest 中存储的 catalog 摘要。

编译调用现有的解析、grounding、受限求解、builder 和静态 validator 行为。只要生成了类型化的编译输出，其运行状态就是 `succeeded`，即便静态验证状态通常因缺少运行时证据而为 `incomplete`。replay 生成连续的物理证据。validate 验证包、哈希绑定、运行时证据和所有门控；这三个 Skill 中只有它会发出 `publishable`。

## 状态、错误与发布

`RunState.status` 是 `running`、`succeeded`、`blocked` 或 `failed` 之一。唯一允许的转换是 `null -> running -> succeeded|blocked|failed`。重试时状态保持 `running`，递增 `attempt` 并追加事件。`blocked` 表示预期的领域、依赖或门控拒绝；`failed` 表示意外的 Harness 或实现故障。两个终止状态都必须有 `RunState.blocker`；`succeeded` 要求 `RunState.blocker=null`。Skill 成功执行本身并不代表验证通过。特别是，当 validate 生成类型化的 `fail` 或 `incomplete` 报告时，其状态仍为 `succeeded`；输出中的 `blockers` 解释拒绝发布的原因。

现有验证状态仍为 `pass`、`incomplete` 或 `fail`；不引入并行的门控状态枚举。当且仅当以下所有条件均成立时，`publishable=true`：

1. compile、replay 和 validate 运行均以精确的注册版本成功。
2. `verify_package` 通过，并且每个引用制品的摘要都匹配。
3. 运行时证据绑定到包的 `resolved_scene_sha256`。
4. 物理、碰撞、稳定性、可见性和视频检查的最终验证状态均为 `pass`。
5. 已注册的 Skill 描述符具有针对确定性测试和回归测试的通过资格制品。
6. 请求来源、版本、seed、依赖摘要和求解器 trace 均存在。

任何不成立的条件都会产生 `publishable=false`，并至少产生一个结构化 blocker；该 blocker 位于 validate 输出中，或在无法生成类型化输出时位于 `RunState.blocker` 中。本契约只定义发布资格；不定义 `published` 状态或外部发布副作用。

| 错误码 | 含义 | 默认位置 | 默认可重试 |
| --- | --- | --- | --- |
| `HARN_SKILL_NOT_FOUND` | 请求的 `skill_id` 没有对应描述符 | 运行 blocker：`blocked` | `false` |
| `HARN_VERSION_UNSUPPORTED` | 精确版本不存在、冲突或已被变更 | 运行 blocker：`blocked` | `false` |
| `HARN_INPUT_INVALID` | Invocation 或 Skill 输入未通过严格 schema 验证 | 运行 blocker：`blocked` | `false` |
| `HARN_DEPENDENCY_UNAVAILABLE` | 所需依赖的标识、版本或摘要不可用 | 运行 blocker：`blocked` | `false` |
| `T2E_REQUEST_REJECTED` | 请求未通过受限 prompt 或 `SceneSpec` 边界 | 运行 blocker：`blocked` | `false` |
| `T2E_ASSET_UNAVAILABLE` | catalog 或生成的资产无法满足 grounding 要求 | 运行 blocker：`blocked` | `false` |
| `T2E_SOLVER_EXHAUSTED` | 受限求解器结束时未得到有效的 resolved scene | 运行 blocker：`blocked` | `false` |
| `T2E_PACKAGE_INVALID` | 包构建、manifest、schema 或哈希绑定失败 | 运行或 validate 输出 blocker | `false` |
| `T2E_REPLAY_FAILED` | RoboTwin/SAPIEN 回放未生成完整证据 | 运行 blocker：`blocked` | `true` |
| `T2E_VALIDATION_INCOMPLETE` | 未运行所需的运行时检查 | validate 输出 blocker | `false` |
| `T2E_VALIDATION_FAILED` | 一个或多个必需的验证检查失败 | validate 输出 blocker | `false` |
| `T2E_REGRESSION_FAILED` | 注册资格验证或确定性回归未通过 | 注册 blocker | `false` |
| `HARN_INTERNAL` | Registry、适配器或 handler 出现意外缺陷 | 运行 blocker：`failed` | `false` |

异常文本是诊断细节，绝不是错误码。现有的 `SceneSpecError`、pydantic 验证错误、`SceneSolveError` 报告、包验证、replay 退出状态和验证报告，根据阶段和恢复操作映射到上表。

## Registry 与 MCP

MVP Registry 在进程启动时静态组装。它提供 `register(descriptor, handler)`、`list()`、`resolve(skill_id, exact_version)` 和 `invoke(skill_id, exact_version, parameters)`。注册会拒绝无效描述符、冲突的不可变标识或未通过的资格制品。调用过程验证并展开输入、计算 invocation 摘要、强制执行描述符的尝试次数上限、解析依赖记录、创建 `Invocation`、调用 handler、计算制品哈希并追加状态事件。Registry 代码不得包含 Text2Env 分支；三个 handler 用于适配现有的 Text2Env 函数和 CLI。

MCP `tools/list` 通过已注册描述符机械生成，并公开上述三个带版本的工具名称。MCP `tools/call` 将该名称解析为精确的规范 Skill 引用，把该 Skill 专用的 JSON 参数传给 `Registry.invoke`，并返回与进程内调用相同的类型化输出、`RunState`、制品和 blocker。格式错误的 JSON-RPC 仍属于 MCP 传输错误；注册工具调用进入 Registry 后，所有结果都使用本契约。参数默认值、依赖解析和运行元数据只能由 Registry 添加。MCP 不添加默认值或协议专用业务字段。

## 验收

当项目所有者确认精确的字段、名称、状态转换、错误码、版本规则和 MCP 映射，所有仓库测试通过，并且 RFC 头部记录 `Status: Accepted`、批准人和批准日期时，本契约即被接受。实现本契约时，必须保留当前 Text2Env 行为以及现有的哈希绑定和物理验证验收规则。

来源：REQ-4.1、REQ-4.3、`scene_gen/schema.py:13-14,186-275,451-492`、`scene_gen/builder.py:39-105`、`script/generate_scene.py:22-93`、`scene_gen/solver.py:34-40,492-505` 和 `repo-docs/walkthroughs/one-real-run.md`。
