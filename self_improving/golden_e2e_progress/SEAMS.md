# TDD 公共 seam 草案

状态：`accepted_2026-09-09`

按仓库 `tdd` skill，以下是功能测试唯一允许跨越的公共 seam。每个切片先从这些 seam 观察 RED，
再做最小 GREEN，不围绕 private helper 建测试。

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

## S4 — Legacy Gujie anti-corruption import

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

可观察行为：输入 ingest、外部真实 Codex 启动、MCP negotiation/tool calls、同一 workflow receipt chain、
Genesis package/replay/validate/observation/diagnosis/fallback/promotion、媒体、阶段成功率与最终证据闭包。
scripted MCP client 可用于 deterministic integration；只有 `agent_mode=codex` 才能算真实 Codex 证据。

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
- [x] 接受 `genesis.robot_policy@1` 作为最终完成指标，`genesis.rigid_scene@1` 只作中间门。
- [x] 允许新增 optional dependency `mcp>=2.2,<3`，并用官方 Python client/server 做真协议测试。
