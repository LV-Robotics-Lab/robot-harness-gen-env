# Golden E2E interface：Design It Twice 结果

状态：`recommended_pending_user_confirmation`

## 问题空间与约束

目标不是再造一个会“说成功”的 agent wrapper，而是建立一条可重建的父 workflow：外部 Codex 从
已资格 Skill 中选择下一步，Harness 校验并执行，每次 compile/replay/validate/observation/diagnosis/
promotion 都留下同一 receipt chain。最终产物必须达到冻结的 Genesis profile；render、模型判断和
进程退出码都不能代替物理或 publication 证据。

任何设计都必须满足：

- 一个 `workflow_run_id` 下有多个独立且不可变的 child Skill `run_id`；不能复用单 Skill RunState。
- 精确版本、已资格、未撤销的 Skill 才能执行；`latest`、候选和源文件扫描结果不可执行。
- 每次 mutation 绑定 `expected_revision`、`expected_state_sha256`、receipt head 和 idempotency key。
- blocked/failed 进入 attempt history，但不得写成可信世界事实；fallback 产生新 turn 和新 package。
- Codex 是 MCP seam 外的 caller，不是 server 内 provider。Codex advisory 不授予 physics pass 或
  publishability。
- 所有跨阶段输入输出进入可搬迁 CAS closure；绝对部署路径不进入 package identity。
- 最终 robot-policy/data-collection 能力必须有 reset/action/observation/trajectory 的真实 Genesis
  probe，不能由 rigid load/step 自动推出。

依赖分类：

- in-process：Pydantic domain、canonical hash、状态/回执校验、IR、gate policy。
- local-substitutable：CAS、SQLite、operation worker、event journal、package materialization。
- remote-but-owned：前端 BFF/HTTP control plane；业务逻辑仍由同一个 Harness module 持有。
- true external：Codex client、Genesis runtime、可选资产生成器；均经真实 adapter + test adapter。

## 方向 A：极简 Run Aggregate

顶层只有一个 module、三个方法：

```python
class GoldenRunHarness:
    def start(self, request: RunStartRequest) -> RunSnapshot: ...
    def submit(self, request: RunCommandRequest) -> OperationSnapshot: ...
    def read(self, request: RunReadRequest) -> RunSnapshot: ...
```

所有 Skill、观察、diagnosis、prompt revision 和 promotion 都藏在 `submit` 后。优点是 depth 和状态
原子性最好；缺点是若 MCP 也只暴露一个泛型 `submit`，Codex 看不到精确工具 schema，路由测量和
授权会退化为一个 mega-command。

## 方向 B：Capability-driven X2Env Kernel

保留九个稳定 façade，内部归一到 `InputBundle → Observation/Scene/Task IR → asset policy → backend
package → replay/validate`。以版本化 capability/profile 表达 Genesis rigid、robot reset/action、RGB/
contact observation、success evaluator 和 trajectory recorder。

优点是未来增加模态、仿真器和 robot task 时避免复制笛卡尔积，也能通过 anti-corruption layer 接入
Gujie `genenv.*`；缺点是 schema/profile/binding 管理较重，而且组件资格绝不能替代真实组合资格。

## 方向 C：Exact Skill MCP + Resources

MCP 从冻结 RegistrySnapshot 机械暴露九个精确 Skill 工具，并用 resources 暴露 workflow head、
state、history、operation、event page、schema、artifact 和 observation。耗时执行成为 durable
operation；Codex、benchmark 和 frontend BFF 使用同一个 transport surface。

优点是工具选择、授权、事件和失败最易审计，模型能直接利用每个 Skill 的 JSON Schema；缺点是
外部工具数量较多，若把每个内部阶段也暴露会形成浅接口和调用顺序负担。

## 比较

| 方向 | Depth | Locality | Seam placement | 主要风险 |
| --- | --- | --- | --- | --- |
| A 极简 aggregate | 最高 | 状态与事务集中 | 一个 run module | generic MCP 隐藏精确 Skill，路由不可见 |
| B capability kernel | 中高 | 跨模态/后端共享 | IR/profile/adapter | 过早抽象、组合资格爆炸 |
| C exact MCP | 中高 | transport 与执行清晰分离 | 每个公开 Skill + resources | 工具面膨胀、调用回合变多 |

## 推荐：A 内核 + C 外观 + B 的最小模型

### 内部公共 seam

`GoldenRunHarness.start/submit/read` 是唯一 workflow 业务入口。它持有并原子提交：

- workflow identity/revision/status；
- current TrustedWorldState 与完整 attempt history；
- RegistrySnapshot；
- durable operation 与连续 event/receipt head；
- child Skill application、state delta 与 promotion transaction。

内部只在确有 production/test 两个 adapter 的地方设 ports：ArtifactStore、WorkflowRepository、
OperationExecutor、QualifiedSkillCatalog、SimulatorRuntime。Codex不是 port。

### 外部 MCP 外观

不暴露泛型 `skill.invoke`。MCP adapter 把一个 frozen RegistrySnapshot 映射为：

- `harness_workflow_create_v1`、`harness_workflow_read_v1`；
- 九个 exact x2env Skill 工具；
- `genesis_observe_v1_0_0`、`environment_promote_v1_0_0`；
- `harness_advisory_record_v1`、`harness_prompt_revision_record_v1`；
- workflow/schema/operation/event/artifact/observation resources。

所有 mutation 工具都只是向 `GoldenRunHarness.submit` 构造强类型 command；MCP 不补默认值、不重试、
不决定 route、validation 或 promotion。耗时命令返回 durable `OperationSnapshot`；Codex 轮询同一
operation resource。MCP Tasks 只可作为兼容加速，不能成为唯一证据路径。

### 最小共享模型

首批只冻结 `InputBundleV1`、`SceneIRV1`、`TaskIRV1`、`EnvironmentPackageV2`、
`CapabilityProfileV1` 和 `CapabilitySatisfactionV1`。不先建立任意下载/执行 plugin 系统，也不把每个
Gujie helper 变成 port。

Gujie 只通过一个 `LegacyGenenvImporter.import_snapshot()` anti-corruption seam 进入；输入必须是
CAS directory snapshot。绝对路径、status conflict、缺失成员或未知语义都成为 typed blocker；旧
physics report 只作 diagnostic，必须由当前 Harness 重新 materialize 和 replay。

### 持久化策略

采用 append-only receipt/event chain 和事务 outbox，但首批不建设一个通用 event-sourcing 平台。
每个 workflow 同时最多一个改变世界状态的 operation；相同 idempotency key + 相同 digest 返回原
operation，不同 digest fail closed。运行时不宣称 exactly-once，只保证每个 attempt 有独立 receipt，
且同一命令最多一次终态 commit。

### 授权边界

Codex 可创建 ephemeral workflow、调用 qualified Skill、记录 observation/advisory、请求 promotion
evaluation。生产资产/环境 publication 是独立 operator-authorized commit；benchmark 只能写
ephemeral registry。知道 ArtifactRef SHA 不等于有读取权限。
