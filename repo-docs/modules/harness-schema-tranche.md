# Harness Schema Tranche

PR1 schema tranche 先回答“进程内到底用什么类型说话”，还没有回答“谁来执行 Skill”。
`self_improving/harness/` 把 Text2Env compile、replay、validate 的边界冻结为严格模型；
`scene_gen/` 仍拥有 SceneSpec、resolved scene、包 manifest、运行时证据和验证报告的内部格式。

本页对 qualification/RunStore 的执行状态固定到 `ab03859`；之后观察到的并发 Harness commits
`c365874`、`8d9a01c`、`587b49f`、`0e6716a` 未纳入这次 clean-archive 审计。以下“未接线”边界
只对固定快照负责，不冒充移动 HEAD 的现状认证。

PR1 之后又增加相邻执行 seam：`LocalArtifactStore`/`ArtifactResolver` 会读取本地 bytes 并核对
SHA-256/大小；`RunRecorder`/`EventSink` 由 callback 生成连续事件和 RunState；
`SQLiteEventJournal` 以 SQLite WAL 持久化 append-only events；`PackageStore` 可把 manifest members
发布到 CAS 并安全重物化；通用 `SkillRegistry` 按 exact version 注册有 qualification receipt 的
descriptor，再调用注入 handler。`284ffb` 已接入首个 `Text2EnvCompileHandler`，但 replay、validate、
跨 run promotion transaction 与 MCP 仍没有实现。

固定 follow-on `ab03859` 又包含两个独立 adapter：`qualification.py` 严格核一份预制 pass bundle，
`run_store.py` 保存 immutable Invocation/terminal RunState 并对账 event journal。前者在该树没有
Registry callsite、不执行 qualification cases、两个 CAS publish 也不是 promotion transaction；后者
同样未接 Registry，只接受调用者提供的 digest、没有 running/resume 状态，也不解引用 artifact bytes。
所以两者是 P0 前置构件，不是 identity-frozen EvaluationRun 或资格晋升闭环。

Registry 的“qualified”边界也要精确：注册会解引用并按 digest 读取 qualification artifact、校验其
schema/status/`skill_ref`；`51447da` 又要求 `report_sha256` 对应的 CAS bytes 确实存在且摘要匹配。
它仍不解析该 report 的领域内容、对账 source manifest，也不会把实际 handler bytes 与 descriptor 的
`implementation_sha256` 对账；因此还不是完整代码/资格 attestation。

完整、逐字段的实现记录在
[PR1 核心 Schema 实现报告](../../docs/contracts/HARNESS_MVP_PR1_IMPLEMENTATION_REPORT.zh-CN.md)，
规范来源是 [Harness MVP 契约](../../docs/contracts/HARNESS_MVP_CONTRACT_V1.zh-CN.md)。

面向项目组成员的互动入口位于
[三个 Skill Walkthrough](../../docs/harness-skill-walkthrough/README.md)。它用四种概念情景串起
compile 的 reuse-first fallback、静态 `incomplete`、连续物理 replay 和最终 publishability；页面
按 PR1 当时状态区分 schema 与尚待实现的 Registry/handler/MCP；它是历史 walkthrough，当前
Registry follow-on 以本页和 `self_improving/harness/IMPLEMENTATION_LOG.md` 为准。

## 先分清四层

```text
调用方 / 未来 MCP
        |
        v
当前通用 SkillRegistry：exact version、qualification、类型、依赖、摘要、attempt、审计
        |
        v
当前 Compile handler；待实现 Replay / Validate handlers
        |
        v
现有 schema + scene_gen：类型、解析、grounding、求解、包、回放、物理验证
```

MCP 未来只能把已注册 descriptor 映射成工具；它不能再发明类型或默认值。Registry 已负责通用
执行与审计，但不把 Text2Env 分支写进通用核心。PR1 schema 通过 `ArtifactRef` 与
`EnvironmentPackage` 指向 `scene_gen` 已有载荷；compile 已由专用 adapter 组装，完整发布闭环仍
需要 replay/validate adapters。

## PR1 的 14 个公共入口与当前第 15 个版本化入口

下表是 PR1 冻结的 14 个入口。A042 没有修改其中的 `SkillDescriptor` v1，而是新增第 15 个
`SkillDescriptorV2`：v1 只允许 `deterministic=true`；v2 用
`content_bitwise_deterministic` / `evidence_invariant_repeatable` 明确声明可复现语义。Registry
保留精确模型、不在两版之间转换。

| 分组 | Schema | 读者模型 |
| --- | --- | --- |
| Skill 身份 | `SkillDescriptor`、`SkillQualification` | 这是什么精确版本、由哪个实现提供、是否有通过资格 |
| 调用审计 | `Invocation` | 默认值展开后到底调用了什么、依赖和摘要是什么 |
| 运行生命周期 | `RunState`、`Event`、`Blocker` | run 怎么开始、attempt 怎么推进、为何终止 |
| 制品引用 | `ArtifactRef`、`EnvironmentPackage` | 位置不是身份；包引用不复制包内容 |
| Text2Env 边界 | compile/replay/validate 六个输入输出 | 三个 Skill 各收什么、产什么、谁能表达 publishable |

`CompileConfig`、`RuntimeConfig`、`DependencyRef` 和 `UnknownField` 是嵌套 `$defs`，不是新的
公共 `$id`，所以 PR1 总数是 14；计入 A042 的 descriptor v2 后，当前总数是 15。

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

`uri` 只负责定位。PR1 schema 本身只校验形状；后续 `LocalArtifactStore.resolve()` 已能打开本地
`artifact://sha256/...` 或 `file://`、重算 SHA-256 并核对 bytes。它还不是安全 capability：
`file://` 没有 allowed-root，校验后返回可变原路径，存在 trusted-input/TOCTOU 边界；CAS 文件也
没有 OS 级只读保证。`50e8f18` 已把 `put_file` 改为单次流式 hash+copy、fsync、同文件系统原子
rename，并拒绝已污染的同 digest 目标；生产 handler 仍应只消费重验后的 CAS snapshot。

首个 compile handler 在 `284ffb` 只完成了“复制”而没有闭合“消费 snapshot”：定向 mutation 可在
precheck 后替换原 catalog，让 root 外资产进入 resolved 输出。冻结攻击与协议先进入 `ef5e29e`，
`910ccb1` 再把 trust check 与 `compile_scene` 的唯一输入切到 digest-verified CAS path；同一攻击通过，
完整 Harness 74 passed、statement/branch 100%。通用 resolver 的任意 `file://` 边界仍只适合 trusted
orchestrator，但这个 compile adapter 已不再回读其原始 locator。

`EnvironmentPackage` 同理：它只保存 resolved scene、catalog 和 manifest 的摘要/引用，
不是另造一个包格式。`package_id` 必须等于 `resolved_scene_sha256`；catalog 摘要是否真的与
resolved scene 和 manifest 内部一致，要由 handler 读内容后核对。

相邻的稳定核心现在会在直接消费 `runtime_evidence` 时核对证据自声明的 `scene_id` 与
`resolved_scene_sha256`。这会拒绝缺失/未改写错值，但不签名完整 evidence/media/run receipt。
Harness schema 本身仍不解引用 URI；resolver 只解决单个本地 artifact equality，未来 handler 仍
必须核对完整 package→run→media 链。

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

`schema_catalog.py` 当前从 Pydantic 模型生成 15 份 Draft 2020-12 文档（PR1 14 份 + descriptor v2），
committed snapshot 让字段、
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
| 本地 artifact 解引用 | `artifacts.py` | `test_artifacts.py` |
| 事件生命周期与持久主账 | `events.py`、`event_journal.py` | `test_events.py`、`test_event_journal.py` |
| 通用 Skill 注册/调用 | `registry.py` | `tests/self_improving/harness/test_registry.py` |
| package CAS 发布/重物化 | `package_store.py` | `test_package_store.py` |
| Text2Env compile 竖切 | `handlers/text2env_compile.py` | `test_text2env_compile_handler.py` |
| 安装包内容 | `pyproject.toml` | wheel 内容检查 |

统一入口同时强制 Harness 语句和分支覆盖率 100%：

```bash
script/run_self_improving_tests.sh
```

PR1 基线是 21 个 Harness 专项测试、顶层 125 passed；ASPIRE E0–E2 快照 `1180aef` 的完整平台
矩阵为 595 passed/6 skipped，Harness 为 350/350 statements、74/74 branches。

这里的 `595/6` 与 `350/74` 是 ASPIRE 研究快照 `1180aef` 的完整回归，不是后续 Registry/compiler/
asset follow-on 的当前绿灯。后续提交必须重新通过默认 collection、行为契约与 100% coverage gate；
不能沿用旧计数。

只读源码快照 `28333de` 的 archive 验证进一步显示：默认 pytest 仍因两个同名
`test_registry.py` collection error；诊断性 `--import-mode=importlib` 为 155 passed / 1 failed，
唯一行为失败是非法 prompt 的 stage=`parse` 与历史 `scene_spec_validation` 期望不一致。该快照
Harness 自身为 895/895 statements、196/196 branches（100%）；所以全仓不绿不能再归因于 Harness
coverage，而是 collection 与行为契约仍未闭合。

## 现在还不能做什么

通用 Registry 已能调用注入的、资格身份匹配的 `text2env.compile@1.0.0` handler；但这只是一条
compile 竖切，不是可信的三 Skill spine。它还缺：

- asset admission 与 solve/package failure 间的 rollback/transaction；当前 solve blocked 后已入库
  资产仍会留下；
- replay/validate handlers 与 `max_attempts=1/2/1` 的完整静态组装；
- 完整 package→run→media 内容复核；
- `5915315` 已把 asset-library、selected asset bytes、ledger contract、scene_gen 与 trust config 记入
  invocation dependencies；但 referenced asset files 仍从外部目录消费，receipt 不能自动把这些
  payload bytes 变成 CAS snapshot；
- 跨 run 的 qualification/promotion transaction；
- MCP 工具生成与调用适配。

因此看到 `SkillRegistry` 与 compile handler 存在，只能得出“compile 竖切可调用、catalog JSON
snapshot 已用于执行”，不能得出“Text2Env 三个 Skill 已接通”“所有引用资产 bytes 已冻结”
“资产晋升具事务性”“物理发布闭环已完成”或“RFC 已 Accepted”。

要回到平台总边界，读 [Self-Improving 平台](self-improving-platform.md)；要审计具体实现和
验证结果，读 [PR1 实现报告](../../docs/contracts/HARNESS_MVP_PR1_IMPLEMENTATION_REPORT.zh-CN.md)。

证据状态：PR1 schema 基于 commit `9b72090` 与 PR #7；follow-on resolver/recorder/journal/Registry/
compiler 基于 `f5d1bab`、`5ccf779`、`568c3b6`、`f9d6ad5`、`315524f`、`9af9db5` 与 `28333de`
源码审计；compile/package/dependency follow-on 锚为 `284ffb`、`1a1f3d8`、`e98e8c1`、`5915315`，
snapshot-use 攻击/修复为 `ef5e29e`/`910ccb1`，CAS/qualification follow-on 为 `50e8f18`/`51447da`，
后续 replay/CLI follow-on 为 `e6ed0ff`/`148001d`。
完整平台绿色计数只钉在 `1180aef`；`28333de` 是明确不绿的诊断快照。`51447da` 的实现日志只另
报告 Harness 专项 76 passed、statement/branch 100%，但独立 clean-archive 复核是 76 passed、
1313 statements 缺 1、322 branches 有 1 partial、总计 99.88%，coverage gate 失败。该快照默认
pytest 与平台脚本也仍有同名测试 collection error；它不是完整平台绿色计数。

`148001d` clean archive 已把 importlib 诊断推进到 201 passed / 0 failed，证明历史 CLI
failure-stage 漂移闭合；默认 collection error 仍在，Harness 仍为 76 passed / 99.88% coverage。
因此最新固定诊断剩下 collection 与 coverage 两个 blocker，依然不是完整平台绿灯。

封存后 `ab03859` clean archive 的 Harness 增至 133 passed，但 1673 statements 缺 1、436 branches
有 1 partial，总 coverage 99.91%，同一 coverage gate 仍失败；默认 collection error 也仍在。该
快照新增 qualification loader/RunStore 均有 focused tests，但三项 P0 spine 仍未接线，不能只凭
模块存在改写为 closed。
