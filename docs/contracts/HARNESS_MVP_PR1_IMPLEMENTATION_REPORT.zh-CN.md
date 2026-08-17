# Harness MVP PR1 核心 Schema 实现报告

- 报告日期：2026-08-18
- 实现完成日期：2026-08-17
- 实现状态：已完成 schema tranche
- 实现基线：`9b720900ff1c3c1b5a6587f7bc5d78359d3af81b`
- Pull Request：[LV-Robotics-Lab/robot-harness-gen-env#7](https://github.com/LV-Robotics-Lab/robot-harness-gen-env/pull/7)
- 对应契约：[Harness MVP 契约 v1](HARNESS_MVP_CONTRACT_V1.zh-CN.md)
- 契约状态：仍为“提议中”，本报告不构成 RFC Accepted

## 1. 结论

PR1 已把 Harness MVP 的公共数据边界实现为严格、不可变的 Pydantic 模型，并导出
14 个 Draft 2020-12 JSON Schema 快照。实现覆盖通用审计记录、资格记录、
`EnvironmentPackage` 引用，以及 Text2Env compile/replay/validate 的六个输入输出。

本批实现没有改变 `scene_gen` 的 parser、grounding、solver、builder、回放或 validator；
Harness 只引用现有 `robotwin.*` 权威载荷。Registry 执行、descriptor 静态组装、
invocation digest、依赖解析、重试、三个 Text2Env handler 和 MCP adapter 均未在 PR1 中实现。

## 2. 范围

| 范围 | PR1 状态 | 说明 |
| --- | --- | --- |
| Harness 公共 Pydantic 模型 | 已完成 | 严格、frozen、拒绝未知字段 |
| 14 个公共 JSON Schema | 已完成 | 每个 schema 以 `$id` 标识，使用 Draft 2020-12 |
| Schema catalog 与快照漂移检查 | 已完成 | 模型、文件名和 committed snapshot 一一对应 |
| Python 包与 wheel 数据文件 | 已完成 | Harness 模块与 14 个快照均进入 wheel |
| 测试和覆盖率门 | 已完成 | Harness 语句、分支覆盖率均强制 100% |
| `SkillRegistry` | 未实现 | 留给后续 PR；当前 `self_improving/registry.py` 只是仓库模块审计 |
| Text2Env handlers | 未实现 | 尚未适配现有 CLI/函数 |
| invocation canonical hash | 未实现 | PR1 只定义并校验摘要字段形状 |
| artifact 内容解析与跨文件哈希对账 | 未实现 | 需要 Registry/handler 读取制品内容后执行 |
| MCP `tools/list` / `tools/call` | 未实现 | 留给 Registry 和 handler 稳定之后 |
| 发布副作用 | 不在契约范围 | PR1 只表达 `publishable` 资格 |

## 3. 代码结构

| 路径 | 责任 |
| --- | --- |
| `self_improving/harness/schemas/base.py` | 公共严格基类、Skill/SemVer/schema/SHA-256 正则、数值原语、MCP 名称机械派生 |
| `self_improving/harness/schemas/common.py` | 通用记录、枚举与跨字段生命周期约束 |
| `self_improving/harness/schemas/text2env.py` | Text2Env 输入输出、runtime 默认值、`EnvironmentPackage` 和权威 artifact 类型约束 |
| `self_improving/harness/schema_catalog.py` | 14 个 `$id -> model` 的只读目录、稳定渲染、写入和漂移检查 |
| `self_improving/harness/json_schemas/` | 14 份 committed JSON Schema 快照 |
| `script/export_harness_schemas.py` | `--check` 校验与显式再生成入口 |
| `tests/self_improving/harness/` | 21 个正向、拒绝、跨字段和快照测试 |
| `script/run_self_improving_tests.sh` | 顶层套件与 Harness 100% 语句/分支覆盖率门 |
| `pyproject.toml` | `pytest-cov`、Harness 包发现和 schema package-data |

`self_improving/registry.py` 中新增的 `harness_mvp` 仅让仓库 checkout audit 知道该源码区存在，
它不是契约中的 `SkillRegistry`。

## 4. 公共 Schema 清单

| JSON Schema `$id` | Pydantic 模型 | 作用 |
| --- | --- | --- |
| `harness.skill_descriptor.v1` | `SkillDescriptor` | Skill、实现、输入输出 schema、attempt 上限和资格制品描述 |
| `harness.skill_invocation.v1` | `Invocation` | 展开默认值后的参数、依赖、attempt 策略和调用摘要 |
| `harness.run_state.v1` | `RunState` | 一次 run 的生命周期、事件、制品、输出或终止 blocker |
| `harness.event.v1` | `Event` | 状态转换、阶段、attempt 和关联制品 |
| `harness.artifact_ref.v1` | `ArtifactRef` | URI 与可信内容身份的分离引用 |
| `harness.blocker.v1` | `Blocker` | 结构化拒绝/失败、未知项和关联制品 |
| `harness.skill_qualification.v1` | `SkillQualification` | 确定性 case 与回归资格报告 |
| `harness.environment_package.v1` | `EnvironmentPackage` | 对现有哈希绑定场景包的不可变引用 |
| `harness.text2env_compile_input.v1` | `Text2EnvCompileInput` | request、seed、asset catalog 和 compile config |
| `harness.text2env_compile_output.v1` | `Text2EnvCompileOutput` | SceneSpec、resolved scene、包引用和静态验证 |
| `harness.text2env_replay_input.v1` | `Text2EnvReplayInput` | 包引用和完整 runtime config |
| `harness.text2env_replay_output.v1` | `Text2EnvReplayOutput` | 运行时证据和回放媒体制品 |
| `harness.text2env_validate_input.v1` | `Text2EnvValidateInput` | 包、运行时证据和 gate profile |
| `harness.text2env_validate_output.v1` | `Text2EnvValidateOutput` | 验证报告、现有 validation status、发布资格和 blockers |

`UnknownField`、`DependencyRef`、`CompileConfig` 和 `RuntimeConfig` 是嵌套定义，
会出现在相关 schema 的 `$defs` 中，但不是独立的公共 `$id`。

## 5. 通用模型决策

### 5.1 严格与不可变

`HarnessModel` 统一设置：

```python
ConfigDict(extra="forbid", frozen=True, protected_namespaces=())
```

公共标量进一步使用 strict field。布尔值不能由 `0/1` 代替，整数不能由字符串隐式转换，
未知字段直接失败。`run_id` 当前收紧为 UUID v4，时间戳要求包含时区。

### 5.2 Skill 身份与 MCP 名称

- `skill_id` 严格匹配契约中的两段式正则。
- Skill 版本只接受无前导零、无 prerelease/build 后缀的稳定 SemVer。
- `mcp_tool_name` 必须由 `skill_id` 和 version 机械派生。
- `qualification_artifact.schema_version` 必须是 `harness.skill_qualification.v1`。
- `deterministic` 只能为 `true`。

通用 `SkillDescriptor` 只表达可注册 descriptor 的结构与自洽性。compile/replay/validate
固定的 `max_attempts=1/2/1` 以及三组 input/output schema 映射，仍需后续静态 descriptor
组装和 Registry 注册测试锁定，PR1 没有在通用模型中硬编码 Text2Env 分支。

### 5.3 Artifact 身份

`ArtifactRef.uri` 只是定位符。可信内容身份由以下三项组成：

```text
media_type + schema_version + sha256
```

`schema_version` 是必填可空字段：类型化 JSON 使用字符串，无类型视频/图片等媒体使用
`null`。模型校验摘要格式，但不通过 URI 读取内容；读取、重算 SHA-256 和跨文件对账属于
后续 Registry/handler。

### 5.4 未知 blocker code

`Blocker.code` 不使用封闭 enum，而是接受符合大写结构化正则的未来代码。这样新增故障关闭
code 时，旧消费者仍可保留原值并拒绝发布。`unknowns` 中每项严格只有 `field`、`reason`
和 `source`。

## 6. Invocation 与 RunState

`Invocation.dependencies` 必须按 `name` 排序且名称唯一；每项严格包含 `name`、`version`
和 `sha256`。PR1 校验 `invocation_digest` 是 64 位小写 SHA-256，但尚未实现契约中的
canonical JSON 计算。

`RunState` 在 Pydantic runtime 中校验：

```text
null -> running -> succeeded | blocked | failed
                    ^
                    +-- retry 时可追加 running -> running，并把 attempt 加 1
```

- 事件 `seq` 必须从 1 连续递增。
- 第一条事件必须是 `from_status=null`、`to_status=running`。
- `started_at` 等于第一条事件时间；事件时间不得倒退。
- attempt 不得倒退或一次跳过两个值，最终事件 attempt 必须等于 RunState attempt。
- 执行 attempt 不得超过 `max_attempts`，且必须有 `invocation_digest`。
- PR1 约定无法形成 Invocation 的预检记录可用 `attempt=0`、`max_attempts=0`、
  `invocation_digest=null`；这一表示法仍需项目所有者在 RFC 接受前确认。
- `running` 要求 `ended_at`、`output`、`blocker` 都为 `null`。
- `succeeded` 要求有类型化 output 且 blocker 为 `null`。
- `blocked` / `failed` 要求有 blocker 且 output 为 `null`。
- 终态 `ended_at` 必须等于最后事件时间且不早于 `started_at`。

Skill 执行成功不等于验证通过。validate 即使生成 `fail` 或 `incomplete` 的类型化报告，
RunState 仍可以是 `succeeded`；拒绝发布的原因位于 validate output 的 blockers。

## 7. Text2Env 边界

### 7.1 Compile

- request 长度 `3..2000`，与现有 `SceneSpec` 一致；模型不 trim 或语言规范化文本。
- seed 是严格整数，范围 `0..2_147_483_647`。
- `config` 必须出现，但允许 `{}`；Registry 展开后的默认值是
  `generate_missing_assets=false`。
- asset catalog 必须指向 `robotwin.asset_catalog.v1`。
- 输出分别绑定 `robotwin.scene_spec.v1`、`robotwin.resolved_scene.v1`、
  `robotwin.scene_validation.v1` 和现有包 manifest。

### 7.2 Replay

`runtime_config` 必须出现，允许 `{}` 后展开为：

| 参数 | 默认值 |
| --- | ---: |
| `precheck_steps` | 0 |
| `settle_steps` | 900 |
| `contact_window_steps` | 120 |
| `video_frames` | 120 |
| `fps` | 12 |

独立 `run_scene_runtime.py` CLI 当前默认 contact window 是 60；Harness 合同和已验证配方
使用 120。后续 replay handler 必须显式传展开后的 120，不能依赖 CLI 默认值。

runtime evidence 必须是 `robotwin.scene_runtime_evidence.v2`；回放媒体可以是
`schema_version=null` 的 `ArtifactRef`。

### 7.3 EnvironmentPackage

`EnvironmentPackage` 不是第二套包格式，只保存现有包的不可变引用。模型当前校验：

- `route_id="text2env"`；
- `producer_skill_ref` 指向 `text2env.compile@<stable-semver>`；
- `package_id == resolved_scene_sha256`；
- asset catalog 与 package manifest 的 schema version 正确。

“asset catalog 摘要同时等于 resolved scene 和 package manifest 内部保存值”需要读取两个
制品的实际内容，故留给 compile/validate handler，而不是仅凭引用模型猜测。

### 7.4 Validate

- gate profile 默认且只能为 `robotwin.scene_validation.v1`。
- `validation_status` 复用 `pass/incomplete/fail`，没有新增平行枚举。
- `publishable=true` 要求 status 为 `pass` 且 blockers 为空。
- `publishable=false` 至少需要一个 blocker。

完整的“当且仅当”发布条件还依赖三个成功 run、qualification、包验证、运行时哈希绑定、
全部物理 gate 和来源信息；这些条件要由后续 validate handler 汇总。PR1 的输出模型只锁定
类型化结果的局部自洽性。

## 8. JSON Schema 与快照

`schema_catalog.py` 暴露只读 `SCHEMA_MODELS`，按 `$id` 排序生成文档。快照使用 UTF-8、
排序键、两空格缩进和结尾换行，便于 code review；这与 invocation digest 使用的紧凑
canonical JSON 是两个不同用途。

```bash
python script/export_harness_schemas.py --check
python script/export_harness_schemas.py
```

第一条命令对 missing、changed、unexpected 三类漂移故障关闭。第二条命令是显式更新动作；
任何公共形状变化都必须按契约重新评估 schema 整数版本与 Skill SemVer。

Pydantic 的 `model_validator` 跨字段逻辑不会自动完整翻译成 JSON Schema 条件表达式。
因此 committed JSON Schema 锁住字段形状、严格对象、正则、范围、required/default 等表面；
Registry 进程内调用 Pydantic 模型时才执行 MCP 名称、事件链、包引用和发布状态等完整语义。

## 9. 测试与验证

### 9.1 Harness 专项测试

`tests/self_improving/harness/` 共 21 个测试，覆盖：

- strict/frozen/unknown-field 与 required-null 语义；
- stable SemVer、Skill/MCP 身份和 qualification 引用；
- blocker 未知 code 保留；
- dependency 排序、唯一性和 UUID v4；
- running/succeeded/blocked/failed、重试和破坏事件历史；
- compile/replay/validate 默认值、artifact 类型和发布状态；
- catalog 不可变、快照写入、missing/changed/unexpected 漂移。

### 9.2 覆盖率门

```bash
python -m pytest -q \
  --cov=self_improving.harness \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=100
```

结果：

| 指标 | 覆盖 |
| --- | ---: |
| statements | 350 / 350 |
| branches | 74 / 74 |
| total | 100.00% |
| 顶层测试 | 125 passed |

门槛已进入 `script/run_self_improving_tests.sh`，不是一次性人工报告；后续任何未覆盖的新
Harness 语句或分支都会令统一入口失败。

### 9.3 完整离线矩阵

```bash
script/run_self_improving_tests.sh
```

结果为 `564 passed, 6 skipped`。skip 是既有 SAPIEN/Isaac/SceneAgent/媒体原始包检查，
没有被 PR1 静默 mock。PR1 不修改 support、containment、loader 或 validator，因此没有
新增真实 RoboTwin/SAPIEN 回放要求，既有物理证据保持有效。

### 9.4 其他检查

| 检查 | 结果 |
| --- | --- |
| 14 个 committed snapshot 漂移 | 通过 |
| Draft 2020-12 metaschema | 14 / 14 通过 |
| wheel 内容 | 6 个 Harness Python 模块、14 个 JSON Schema 均存在 |
| 变更范围 Ruff | 通过 |
| `git diff --check` | 通过 |
| GitHub Actions Python 3.11 / 3.12 | 全部通过 |

全仓 `ruff check .` 仍会命中历史归档和 evidence 树中的既存错误；PR1 使用变更范围 Ruff，
没有把清理归档代码扩大进本次 schema 任务。

## 10. 兼容性与风险

- `scene_gen` 权威载荷及其 hash/物理门控未改变。
- 公共 schema `$id` 与 `ArtifactRef.schema_version` 是不同概念；只有后者进入 artifact 引用。
- wheel 已携带快照，安装后不依赖源码 checkout 才能读取 schema 文件。
- JSON Schema-only 消费者只能获得结构约束；跨字段语义必须通过 Registry/Pydantic 路径。
- UUID v4 `run_id`、aware datetime、字符串长度上限和预检 `max_attempts=0` 是 PR1 明确化的
  实现选择，RFC Accepted 前应由项目所有者确认。
- 当前没有可执行 Registry，不能把模型存在误读为三个 Skill 已可调用。

## 11. PR2 前置清单

后续 Registry 实现至少需要：

1. 静态组装三份精确 descriptor，锁定 schema 映射和 `max_attempts=1/2/1`。
2. 加载并验证 qualification payload 与报告 SHA-256。
3. 展开输入默认值、解析依赖并计算契约定义的 invocation digest。
4. 把 artifact URI 替换为内容身份，并逐项重算摘要。
5. 实现严格的 preflight blocker 与执行期 state/event 生成。
6. 只对 `retryable=true` 的 replay blocker 执行第二次 attempt。
7. 用三个独立 handler 适配现有 compile/replay/validate 行为，Registry 内不写 Text2Env 分支。
8. 在 Registry 稳定后机械生成 MCP `tools/list` / `tools/call`。

## 12. 审阅重点

项目所有者在接受 RFC 前应明确确认：

- 14 个公共 `$id` 和字段名称；
- UUID v4、aware datetime、字符串和数值边界；
- required-but-null 与 required-config-object 语义；
- 预检 `attempt=0/max_attempts=0` 表示法；
- Pydantic runtime 与纯 JSON Schema 的语义分工；
- RuntimeConfig 的 Harness 默认 120 与独立 CLI 默认 60 的适配责任；
- PR2 中三份 descriptor 的精确 schema 映射和 attempt 上限。

这些确认、所有后续实现测试通过，以及 RFC 头部记录批准人和批准日期之后，契约才能从
“提议中”更新为 Accepted。
