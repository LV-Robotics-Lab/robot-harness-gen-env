# System 2 → compile dispatch trust boundary — 2026-08-31

## 结论

本切片把已经通过结构校验的 System 2 规划结果接到了一个应用拥有的
`text2env.compile` 调用边界。Dispatcher 不把 callable 交给模型，而是重新读取并校验
planner 的 prompt、原始回答、decision、终态 receipt、事件顺序、Skill card、当前
descriptor 与 qualification，再将模型给出的参数解析为公开的 typed input 后调用一次
`CompileApplication.invoke_typed()`。

终态会生成三个可重算对象：Harness `Invocation`、完整 `RunState`、以及把精确
`TrustedWorldState`、`PlannerContext`、完整历史 authority、规划意图、Skill 实现、资格、
运行结果和 supporting artifacts 串起来的 `TrustedToolReceipt`。成功 compile 只派生两个
非物理事实：catalog 身份与 environment package 身份；blocked/failed 结果不会发布事实，
不会为 preflight 阻断伪造 `Invocation`，也不会返回一个语义上不可应用的空 `StateDelta`。

## 信任边界

- planner 的 prompt、decision、receipt 和所有事件必须在 dispatcher 自己的 CAS 中可读，
  且摘要、schema、call id、时间与阶段序列互相一致。
- Planner Skill card 必须与应用当前公开的 descriptor、实现摘要、qualification artifact、
  input/output schema 和重试上限完全一致。
- typed input 内每个 `ArtifactRef` 必须是同一 CAS 的
  `artifact://sha256/<digest>` 对象；外部 locator、foreign CAS、缺失或损坏对象均停止执行。
- 应用返回的 `RunState` 必须是终态，Skill 身份、typed output、artifact closure 和
  Registry 的 Invocation digest 必须可独立重算。
- receipt 与其 base state、planner context、qualification/report、planner receipt/decision、
  `RunState`、`Invocation`、history lineage 和 world-fact evidence 都必须是严格、排序、紧凑
  的 canonical JSON；pretty JSON、重复键、NaN、非法 UTF-8 与非对象载荷均拒绝。
- compile 的 typed `request` 必须与当前受信 `PlannerContext` 的 `task.objective` 精确一致；即使
  base state、context、prompt、decision 与 planner receipt 被整组重建为另一条自洽闭包，也不能把
  旧 compile 的 catalog/package facts 重绑到语义无关的新任务。
- version 2 及以后、含 receipt-derived fact、或发生 retained/omitted history 的 state 必须带
  完整 CAS history authority；它从 version 1 开始逐状态、逐 transition receipt 重建，不能
  省略一次状态迁移或只应用同一 receipt 的部分 claims。
- `StateDelta` 自身带内容摘要；应用前会重验 retained world state 与 retained delta，避免
  Pydantic model 构造后被嵌套可变对象改写。
- compile output 不是只查 artifact 摘要：dispatcher 从 CAS 重解析 `SceneSpec`、
  `ResolvedSceneSpec` 与 effective `AssetCatalog`，重跑权威 solver，重物化 package，重跑
  `validate_resolved_scene(require_runtime=False)`，再核 package/manifest/四个 member 与输出。
- compile/static validation 不能成为物理事实。回放、接触、稳定性、可见性和发布资格都
  仍需后续 replay/validate receipt。

## 验证

执行：

```text
python -m pytest -q \
  tests/self_improving/harness/test_application.py \
  tests/self_improving/harness/test_system2_dispatcher.py \
  tests/self_improving/harness/test_system2_domain.py \
  tests/self_improving/harness/test_system2_history.py \
  --cov=self_improving.harness.application \
  --cov=self_improving.harness.system2.dispatcher \
  --cov=self_improving.harness.system2.domain \
  --cov=self_improving.harness.system2.history \
  --cov-branch --cov-report=term-missing --cov-fail-under=100
```

结果：264 tests passed；四个实现模块合计 1,340/1,340 statements、552/552 branches，
均为 100%。Ruff、格式检查、`py_compile` 与 `git diff --check` 同时通过。独立只读复审
先复现 history authority 省略、非 canonical closure、blocked 空 delta 与完整协调重绑四类 P1；这些反例
转为攻击测试并修复后，复审结论为无剩余 P0/P1。

攻击测试覆盖 retained state/delta 改写、planner artifact 或事件漂移、Skill/qualification
错配、foreign/missing/corrupt CAS、参数 schema 绕过、应用 CAS 不同、Invocation digest
错配、非终态/矛盾 RunState、typed output 与 supporting artifact closure 漂移、solver/static
报告伪造、catalog/path/package 错绑、history 缺 transition/缺 claims/跨 branch，以及 canonical
JSON 的 pretty/duplicate/nonfinite/malformed/UTF-8/nonobject 绕过；另覆盖合法新 context 中的
旧 compile request 在 application 零调用前停止，以及 succeeded/blocked 两种完整 receipt closure
重绑后的离线拒绝。

## 诚实边界

主体是本地 unit/integration tests；其中一项使用真实 `create_compile_application`、真实
Registry/handler/CAS/SQLite 和缺失资产生成路径，但 qualification bundle 仍由测试临时生成，
不是 checked-in production qualification，也没有启动 SAPIEN。当前 checked-in
`text2env.compile@1.0.0` qualification 已因并行收紧的 asset-admission、ledger 与 Registry
实现发生源码漂移，生产 application 必须 fail closed。只有共享实现冻结后重新生成并复核
compile qualification，再用真实非空 catalog 执行 System 2 → Registry → compile，才能声称
“已资格化的 agent 驱动 compile 跑通”；物理能力仍必须另由 replay/validate 证明。
