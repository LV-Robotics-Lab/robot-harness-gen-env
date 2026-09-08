# TDD 公共 seam 草案

状态：`pending_user_confirmation`

按仓库 `tdd` skill，以下是功能测试唯一允许跨越的公共 seam。用户确认前不写功能测试；确认后每个
切片先从这些 seam 观察 RED，再做最小 GREEN，不围绕 private helper 建测试。

## S1 — Workflow aggregate

```python
class GoldenRunHarness:
    def start(self, request: RunStartRequest) -> RunSnapshot: ...
    def submit(self, request: RunCommandRequest) -> OperationSnapshot: ...
    def read(self, request: RunReadRequest) -> RunSnapshot: ...
```

可观察行为：workflow/child-run identity、revision/state/receipt CAS、幂等、连续 attempt history、
qualified Skill execution、ToolResult、fresh observations、advisory、prompt revision、promotion 和 crash
recovery。compile/replay/validate 内部实现可替换而不改调用者。

## S2 — Registry-backed MCP adapter

```python
class HarnessMCPAdapter:
    def list_tools(self, snapshot: RegistrySnapshotRef) -> tuple[MCPTool, ...]: ...
    def call_tool(self, request: MCPToolCall) -> MCPToolResult: ...
    def read_resource(self, request: MCPResourceRead) -> MCPResourceResult: ...
```

可观察行为：只导出 exact qualified Skill；committed schema/tool snapshot；command envelope；
structuredContent/TextContent/ImageContent/ResourceLink；协议错误与领域错误映射；stdout 零污染；
official MCP client lifecycle。真实 stdio/HTTP server 是此 seam 的 transport adapter，不复制业务逻辑。

## S3 — Genesis runtime verification

```python
class GenesisRuntime:
    def replay(self, request: GenesisReplayRequest) -> GenesisReplayResult: ...
    def observe(self, request: GenesisObservationRequest) -> GenesisObservationResult: ...
```

可观察行为：从 portable package 新根 materialize、原生 Genesis load/build/step、collision、物理检查、
robot reset/action/observation、trajectory、图片/连续视频、runtime attestation 与全部 hashes。测试 adapter
只验证 Harness 行为；真实验收必须使用 Gujie-derived production adapter 和真实 Genesis。

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

- [ ] 接受 S1–S6 为公共测试 seam。
- [ ] 接受 exact Skill MCP tools，而不是 public generic `skill.invoke`。
- [ ] 接受 `genesis.robot_policy@1` 作为最终完成指标，`genesis.rigid_scene@1` 只作中间门。
- [ ] 允许新增 optional dependency `mcp>=2.2,<3`，并用官方 Python client/server 做真协议测试。
