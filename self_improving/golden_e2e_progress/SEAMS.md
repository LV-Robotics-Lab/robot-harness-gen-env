# TDD 公共 seam 草案

状态：`accepted_2026-09-09`

按仓库 `tdd` skill，以下是功能测试唯一允许跨越的公共 seam。每个切片先从这些 seam 观察 RED，
再做最小 GREEN，不围绕 private helper 建测试。

## M1 已验证组合 seam 与 M2 下一门

`Image2EnvGoldenWorkflow.run` 在 `1cf8908` 以可信 registered compile UUID 为入口，调用
`GenesisSceneApplication.invoke`、`GenesisFreshCaptureApplication.invoke` 和
`GenesisSceneValidationApplication.invoke`，复用 Registry/SQLite 的 intent、Invocation 与终态。
四个 exact Skill 分别为 image compile/replay/validate `@2.0.0` 与 `genesis.observe@3.0.0`。
fresh producer 在采集前深读历史 replay，采集后核对固定新 capture 闭包与 TTL；纯读恢复不授 live
capability，也不能把历史 captured_at 更新为当前时间。结果 scope 为 `genesis.rigid_scene@1`。

M2 必须从用户可见的 Harness 入口发起：Harness 先持久化 workflow，再调用受管理的真实
CodexBackend，经当前任务分配的 exact MCP 把此组合接回同一 workflow。本地 assembly 与外部
Codex/MCP helper 的正式通过都只证明组件能力。保留无 generic invoke、Harness 唯一 authority、
失败不推进的门。

## S1 — Workflow aggregate

```python
class GoldenRunHarness:
    def start(self, request: RunStartRequest) -> RunSnapshot: ...
    def submit(self, request: RunCommandRequest) -> OperationSnapshot: ...
    def read(self, request: RunReadRequest) -> RunSnapshot: ...
    def read_workflow_artifact(
        self, request: RunReadRequest, *, sha256: str
    ) -> tuple[ArtifactRef, bytes]: ...
```

可观察行为：workflow/child-run identity、revision/state/receipt CAS、幂等、连续 attempt history、
qualified Skill execution、ToolResult、fresh observations、advisory、prompt revision、promotion 和 crash
recovery。compile/replay/validate 内部实现可替换而不改调用者。

### Fresh observation 受控纵切

`genesis.observe@1.0.0` 继续穿过同一个 `GoldenRunHarness.submit/read` seam，不新增旁路写接口。首个
受控 tracer 必须逐项闭合以下唯一链条：

```text
FreshObservationReceipt
  → ToolResult.fresh_observations
  → TrustedToolReceipt.derived_fact_claims
  → StateDelta.mutations
  → resulting TrustedWorldState
```

五处的 observation key、value、capture time 与 `valid_until` 必须按合同逐项相等；原始 observation 的
`source_artifact` 指向 FreshObservationReceipt，写入可信状态的 mutation 则指向闭合该原始 receipt 的
外层 TrustedToolReceipt。任何缺项、额外项、重排或漂移都不能推进 workflow revision。受控 test adapter 必须显式记录
`simulator_executed=false`，只证明 aggregate/receipt/state 行为，不证明 Genesis capture。真实
Genesis runtime、production qualification 和 production MCP exposure 仍须分别通过后续运行门。

## S2 — Registry-backed MCP adapter

```python
class HarnessMCPAdapter:
    def list_tools(self, snapshot: RegistrySnapshotRef) -> tuple[MCPTool, ...]: ...
    def call_tool(self, request: MCPToolCall) -> MCPToolResult: ...
    def read_resource(self, request: MCPResourceRead) -> MCPResourceResult: ...
```

可观察行为：只导出 exact qualified Skill；committed schema/tool snapshot；command envelope；
structuredContent/TextContent/ImageContent/ResourceLink；协议错误与领域错误映射；stdout 零污染；
official MCP client lifecycle。artifact resource 必须带 workflow identity，并只允许读取经完整历史重建
可达的 typed refs；不能把裸 CAS digest 当授权。真实 stdio/HTTP server 是此 seam 的 transport adapter，
不复制业务逻辑。

产品调用者是 Harness CLI/API，而不是 MCP client。Harness controller 只向 CodexBackend 分配当前
workflow、当前阶段和预算内的 exact tools；Codex 不能创建产品任务、提交可信状态或发布 package。
直接 MCP client 与旧 external-Codex runner 只能形成协议/组件证据，不能计为 Harness 产品 E2E。

## S3 — Genesis runtime verification

```python
class GenesisRuntime:
    def replay(self, request: GenesisReplayRequest) -> GenesisReplayResult: ...
    def observe(self, request: GenesisObservationRequest) -> GenesisObservationResult: ...
```

可观察行为：从 portable package 新根 materialize、原生 Genesis load/build/step、collision、物理检查、
robot reset/action/observation、trajectory、图片/连续视频、runtime attestation 与全部 hashes。测试 adapter
只验证 Harness 行为；真实验收必须使用 Gujie-derived production adapter 和真实 Genesis。

Observe 的新鲜度时间区间冻结为闭区间 `[captured_at, valid_until]`，请求 TTL 为 `1..300` 秒且
`valid_until == captured_at + TTL`。capture timestamp 由 runtime 生成；已存在的 replay 图片或历史截图
只能作为旧 supporting artifact，不能重新包装成 FreshObservationReceipt。

### S3 runtime dependency lock 前置纵切（2026-09-11 用户授权）

公共 interface 为 `capture_runtime_manifest`、`read_runtime_manifest` 与
`verify_runtime_deployment`：从明确的逻辑根、部署映射与类型化 process identity 生成 canonical、
CAS-backed 内容清单；read 只从 CAS 重算清单成员，deployment verification 另外重算整个目录集合并
核对显式提交的 process identity。部署映射不进入清单内容身份。

可观察行为：相同字节在两个绝对根得到相同逻辑身份；全部成员逐 bytes/SHA 可核；缺成员、多成员、
symlink、路径越界、native bytes 或 process environment 漂移拒绝；读取过程中成员/目录身份变化也
拒绝。CAS-only 验证与部署验证必须在 interface 中明确区分，不用 optional 参数暗示未发生的验证。

该前置切片新增版本化 runtime manifest，不改变已提交 `GenesisRuntimeLockV1` 的语义。声明根的字节
完整性不是实际 import/native dependency discovery 的完整性，也不授予 asset qualification；后续须
由独立依赖发现、固定 fresh process 与 sealed runner binding 证明完整运行闭包。
process identity 当前是调用方提供的事实，不能把它与 manifest 相等解释为已自动验证运行中进程；
真实验收必须另从 fresh process 采集环境、解释器和实际加载来源。

### S3 manifest 驱动的 CPU execution 纵切

新增的执行producer必须调用固定launcher实际运行child，并在执行前后重验部署与实际观测；最后才
发布执行receipt。新request/observation/receipt将manifest、runtime lock、runner成员身份与asset probe
input/raw-load/load/rows/trace/report精确交叉绑定。runtime lock v1可使用其既有dependency字段明确
携带manifest identity，旧记录的语义和schema保持不变；新lock须驱动fresh probe，不能复用旧report。

可观察行为：固定归档源码能真实运行，不再借用旁边checkout；解释器/ABI/env/thread/实际加载成员与
声明一致；只读运行根与单独可写run目录的约束不静默放宽；精确native部署别名逐字节绑定；失败留下
诊断但不发布pass receipt；第二部署的新进程与CAS-only纯读都能重建相同逻辑闭包。

authority区分：纯读verifier只证明所给证据的闭包一致性，不能从调用方可构造的JSON单独证明执行
发生。供后续issuer使用的opaque live capability只能来自固定producer的成功真实执行；无live restart
的资格恢复另须由durable application对账可信intent/terminal，不能公开`issue(arbitrary report)`。

### S3 durable asset qualification 纵切

`GenesisAssetQualificationApplication(state_path, artifact_store, approved_manifest_ref)`以
`invoke(request_ref, run_id=UUID, roots=...)`执行及签发，`execute(request_ref, run_id=UUID, roots=...)`
显式执行并停在持久executed阶段，`read(run_id)`只读取持久状态。应用内部使用固定
execution producer，不接收调用方注入的producer或任意report。既有qualification v1可复用，但
应用必须精确签发`backend_load`与`collision_probe`两项检查，绑定整条运行证据及issuer实现身份。

SQLite状态为intent→executed→qualified。intent/pending重试拒绝且不二次运行；executed只能来自
live capability，重启恢复须重验可信应用记录和完整CAS。UUID与canonical输入、批准manifest及
issuer身份固定，相同UUID的不同输入拒绝。terminal重试只读，SQLite/CAS损坏拒绝；qualification
是最后一个权威CAS写入。机制可在真实运行等待期间先TDD，实际签发须等execution门通过。

## S4 — Legacy Gujie anti-corruption import

M1已批准整合纵切：S4把固定Gujie source scene、representation、测得完整geometry及显式有限桌面
转成portable场景；首条保留source XY/orientation，显式计算Z/clearance，超范围/倾斜/articulation
明确拒绝。S3消费同一typed scene作baseline/half-dt环境replay，输出逐步state/contact、实际媒体与
固定判据；pure-read重算target-local footprint、向上支撑、穿透/稳定性/终态agreement。真实runner
与纯读一致性分账；M1最后必须绑定production image compile的同一个输入/package，而非孤立scene。

M1执行应用复用现有`SQLiteRunStore`与`SkillRegistry`的UUID/intent/Invocation/terminal恢复，
不新建通用状态权威。新固定scene producer消费approved renderer manifest和typed scene，产出
live capability、双profile过程观测、完整trace/media及execution receipt；pure reader不能自行
提升为live来源。asset资格lock与scene renderer lock分别验证，explicit新Skill I/O版本与资格
消费新receipt，不能继承旧asset资格或把内部receipt冒充Registry qualification。

```python
class LegacyGenenvImporter:
    def import_snapshot(self, request: LegacyImportRequest) -> LegacyImportResult: ...
```

可观察行为：只接受 CAS directory snapshot；把已知 `genenv.*` 映射为共享 IR/package；返回完整
CompatibilityReport；绝对路径、缺成员、未知语义、status conflict fail closed；不修改 Gujie workspace，
也不把旧 physics report 晋升为当前 replay evidence。

## S5 — Asset debt staging and promotion

```python
class AssetRepairApplication:
    def plan(self, request: AssetRepairPlanRequest) -> AssetRepairPlan: ...
    def stage(self, request: AssetStageRequest) -> AssetStageResult: ...
    def promote(self, request: AssetPromotionRequest) -> AssetPromotionResult: ...
```

可观察行为：4,082 debt inventory、exact-byte recovery、loader closure、collision/settle/Genesis receipts、
durable journal、no-write failure、旧 digest stale 和原子权威 ledger publish。validator 本身不放松。

## S6 — Golden runner

```python
class GoldenE2ERunner:
    def run(self, case: GoldenE2ECase) -> GoldenE2EReport: ...
```

可观察行为：用户从 Harness 入口提交、Harness 启动受管理的真实 Codex、MCP negotiation/tool calls、
同一 workflow receipt chain、Genesis package/replay/validate/observation/diagnosis/fallback/promotion、
媒体、阶段成功率与最终证据闭包。scripted MCP client 可用于 deterministic integration；只有从 Harness
入口发起并由 controller 管理的真实 Codex 调用，才能算产品 Codex E2E 证据。

## 测试边界

- 新 workflow/domain/MCP/asset-repair core 模块要求 statement + branch coverage 100%。
- 测试替身必须命名为 test adapter，并在结果中标注 `simulator_executed=false` 或
  `external_agent_executed=false`；不能冒充真实验收。
- Genesis、Codex 和可选在线资产生成器是系统边界；真实 adapter 必须另跑集成/运行时门。
- 前端只消费 S1/S2 的 event/resource，不直接扫描目录或导入 application。
- 旧 shallow-module 测试只有在仍保护兼容性时保留；新的行为测试穿过上述 deep seam。

## 待确认事项

- [x] 接受 S1–S6 为公共测试 seam。
- [x] 接受 exact Skill MCP tools，而不是 public generic `skill.invoke`。
- [x] 原最终指标`genesis.robot_policy@1`保留至后续联调；用户新批准的本阶段交付以rigid-scene
  sim-ready环境、Codex闭环及服务发布为门，完成后暂停。
- [x] 允许新增 optional dependency `mcp>=2.2,<3`，并用官方 Python client/server 做真协议测试。
