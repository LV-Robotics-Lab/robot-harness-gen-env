# Harness Schema Tranche

这一批 Harness 代码先回答“进程内到底用什么类型说话”，还没有回答“谁来执行 Skill”。
`self_improving/harness/` 把 Text2Env compile、replay、validate 的边界冻结为严格模型；
`scene_gen/` 仍拥有 SceneSpec、resolved scene、包 manifest、运行时证据和验证报告的内部格式。

完整、逐字段的实现记录在
[PR1 核心 Schema 实现报告](../../docs/contracts/HARNESS_MVP_PR1_IMPLEMENTATION_REPORT.zh-CN.md)，
规范来源是 [Harness MVP 契约](../../docs/contracts/HARNESS_MVP_CONTRACT_V1.zh-CN.md)。

## 先分清四层

```text
调用方 / 未来 MCP
        |
        v
未来 SkillRegistry：版本、默认值、依赖、摘要、attempt、审计
        |
        v
本次 schema tranche：输入输出和运行记录的形状 + 跨字段自洽
        |
        v
现有 scene_gen：解析、grounding、求解、包、回放、物理验证
```

MCP 未来只能把已注册 descriptor 映射成工具；它不能再发明类型或默认值。Registry 未来
负责执行与审计；它不能把 Text2Env 分支写进通用核心。PR1 只实现第三层，并通过
`ArtifactRef` 与 `EnvironmentPackage` 指向第四层已有载荷。

## 14 个公共入口怎么分组

| 分组 | Schema | 读者模型 |
| --- | --- | --- |
| Skill 身份 | `SkillDescriptor`、`SkillQualification` | 这是什么精确版本、由哪个实现提供、是否有通过资格 |
| 调用审计 | `Invocation` | 默认值展开后到底调用了什么、依赖和摘要是什么 |
| 运行生命周期 | `RunState`、`Event`、`Blocker` | run 怎么开始、attempt 怎么推进、为何终止 |
| 制品引用 | `ArtifactRef`、`EnvironmentPackage` | 位置不是身份；包引用不复制包内容 |
| Text2Env 边界 | compile/replay/validate 六个输入输出 | 三个 Skill 各收什么、产什么、谁能表达 publishable |

`CompileConfig`、`RuntimeConfig`、`DependencyRef` 和 `UnknownField` 是嵌套 `$defs`，不是新的
公共 `$id`，所以总数仍是 14。

## 严格不只是“不多字段”

所有 Harness 模型都 `extra="forbid"` 且 `frozen=True`。字段类型也尽量 strict：例如
`generate_missing_assets=1` 不会被当成 `true`，`fps="12"` 不会被当成整数。模型构造后
不能改字段，避免已记录摘要的对象继续漂移。

几类格式在进入 Registry 前就被挡住：

- Skill ID 必须是两段小写标识；
- version 必须是稳定 SemVer，禁止 `latest`、range、prerelease 和 build suffix；
- SHA-256 必须是 64 位小写十六进制；
- run ID 当前使用 UUID v4；
- 事件时间必须含时区；
- MCP 工具名必须从 Skill ID + version 机械派生。

未知 blocker code 是例外：只要保持大写结构化格式就会原样保留。原因是新增故障关闭
code 不应被旧消费者吞掉或改写成普通异常文本。

## 为什么 schema 版本不塞进所有 JSON

`harness.run_state.v1` 这类字符串是 JSON Schema 文档的 `$id`，不是每个实例都重复携带的
业务字段。只有 `ArtifactRef.schema_version` 是实例字段，因为它在说明“这个引用指向哪种
权威载荷”。

这一区分避免四套版本混在一起：

- Harness `$id` 版本描述接口形状；
- `robotwin.*` 版本描述被引用载荷；
- Skill SemVer 描述调用语义；
- Python 包版本和 `compiler_version` 各自独立。

## ArtifactRef：URI 不是身份证

两个 URI 可以指向相同内容，一个 URI 也可能后来被覆盖。所以 artifact 内容身份只看：

```text
media_type + schema_version + sha256
```

`uri` 只负责定位。PR1 能校验摘要和 schema version 的形状，但不会打开 URI。后续 Registry
必须读取内容、重算 SHA-256，并把 URI 换成内容身份后再计算 invocation digest。

`EnvironmentPackage` 同理：它只保存 resolved scene、catalog 和 manifest 的摘要/引用，
不是另造一个包格式。`package_id` 必须等于 `resolved_scene_sha256`；catalog 摘要是否真的与
resolved scene 和 manifest 内部一致，要由 handler 读内容后核对。

## RunState：成功不等于通过

合法生命周期只有：

```text
null -> running -> succeeded | blocked | failed
```

retry 不会先把 run 变成 terminal 再复活，而是在 `running` 下追加事件并递增 attempt。
事件序号必须连续，时间不得倒退，最后事件的 status/attempt 必须和 RunState 对上。

三种终态别混：

| 状态 | 含义 | output | blocker |
| --- | --- | --- | --- |
| `succeeded` | handler 产出了类型化结果 | 必须有 | 必须为 `null` |
| `blocked` | 预期的输入、依赖、领域或门控拒绝 | 必须为 `null` | 必须有 |
| `failed` | Harness 或实现意外故障 | 必须为 `null` | 必须有 |

validate 产出 `validation_status=fail` 仍可以是 run `succeeded`：前者说“场景不能发布”，
后者说“validator 正常完成并给出了类型化结论”。把两者合并会丢失审计含义。

## 三个 Text2Env 边界

### Compile

request 保持原文本，不 trim、不做语言规范化；长度边界沿用 `SceneSpec` 的 3..2000。
`config` 对象必须出现，但 `{}` 会展开成 `generate_missing_assets=false`。输入 catalog 和
四个输出制品都检查对应 `robotwin.*` schema version。

### Replay

`runtime_config` 对象也必须出现。有效默认值是：

```text
precheck=0, settle=900, contact_window=120, video_frames=120, fps=12
```

独立 runtime CLI 目前默认 contact window 为 60；未来 handler 必须传展开后的 120，不能把
CLI 默认值意外带入 Harness digest 或行为。

### Validate

gate profile 固定为 `robotwin.scene_validation.v1`。只有 status 为 `pass` 且 blockers 为空时，
模型才允许 `publishable=true`；`publishable=false` 至少要解释一个 blocker。跨 compile、
replay、qualification、包验证和全部物理 gate 的最终“当且仅当”判断，留给 validate handler。

## JSON Schema 快照能锁什么

`schema_catalog.py` 从 Pydantic 模型生成 14 份 Draft 2020-12 文档，committed snapshot 让字段、
required/default、正则和范围的变化进入普通 code review：

```bash
python script/export_harness_schemas.py --check
```

missing、changed、unexpected 任一出现都会失败。但 `model_validator` 的跨字段程序逻辑不会
完整变成 JSON Schema 条件；MCP 名称、RunState 事件链、包绑定和发布自洽仍以进程内
Pydantic 校验为准。这符合“Registry 是唯一类型和执行权威”的总边界。

## 改哪里，跑什么

| 改动 | 入口 | 最近测试 |
| --- | --- | --- |
| 标识、正则、严格基类 | `schemas/base.py` | `test_common_schemas.py` |
| 通用记录/状态机 | `schemas/common.py` | `test_common_schemas.py` |
| Text2Env 字段和默认值 | `schemas/text2env.py` | `test_text2env_schemas.py` |
| 公共 `$id` 或导出 | `schema_catalog.py` | `test_schema_catalog.py` |
| committed snapshot | `json_schemas/` | export `--check` |
| 安装包内容 | `pyproject.toml` | wheel 内容检查 |

统一入口同时强制 Harness 语句和分支覆盖率 100%：

```bash
script/run_self_improving_tests.sh
```

PR1 基线是 21 个 Harness 专项测试、顶层 125 passed、完整平台矩阵 564 passed/6 skipped，
Harness 为 350/350 statements、74/74 branches。

## 现在还不能做什么

当前代码不能执行 `text2env.compile@1.0.0`。它还缺：

- 三份精确 descriptor 和 `max_attempts=1/2/1` 的静态组装；
- qualification 内容读取和报告摘要验证；
- 默认值展开后的 invocation digest；
- 依赖和 artifact 内容解析；
- retryable replay 的第二次 attempt；
- compile/replay/validate handlers；
- MCP 工具生成与调用适配。

因此看到 `self_improving/harness/` 存在，只能得出“接口已冻结到 PR1 候选实现”，不能得出
“Harness 已可执行”或“RFC 已 Accepted”。

要回到平台总边界，读 [Self-Improving 平台](self-improving-platform.md)；要审计具体实现和
验证结果，读 [PR1 实现报告](../../docs/contracts/HARNESS_MVP_PR1_IMPLEMENTATION_REPORT.zh-CN.md)。

证据状态：基于实现 commit `9b72090` 与 PR #7 的本地/CI 验证结果确认。
