# Golden E2E 实施计划

状态：`active`

每个切片遵循：更新 TODO → 通过公共 seam 写 RED → 记录失败 → 最小 GREEN → focused tests + 100%
新模块 statement/branch coverage → full regression/必要真实 runtime → 更新证据和 repo-docs → 只暂存本
切片文件 → 独立提交。资格 bundle 只在其实现树稳定后重签，不用旧 hash 装绿。

## P0 — 复原可工作的干净基线

1. `fix(platform): align required capabilities after portal removal`
   - 以现有 `python -m self_improving --json` 为 public seam，修复删除 portal 后仍 required 的一致性。
   - 不恢复已明确删除的 portal，不碰现有用户 demo/repo-docs 修改。
2. 在 clean worktree 复核根套件；共享 dirty tree 的 replay qualification hash failure 单独记录。
   - 最终只针对 committed implementation tree 重签资格；不把未提交文档 bytes 写入 manifest。

## P1 — Actor-neutral workflow identity 与 schema

提交建议：`feat(harness): add golden workflow identity contracts`

- `WorkflowRun`、`RunSnapshot`、`OperationSnapshot`、`RunCommand`、`RegistrySnapshot`。
- 一个 parent workflow、多 child Skill run、连续 turn/event、三重 CAS 与 idempotency。
- `ExternalAgentCallIntent/AdvisoryReceipt`；不再强制伪造 `model_snapshot_sha256`。
- blocked/failed attempt 可持久恢复但不推进可信事实。
- 导出 JSON Schema snapshot 和 tamper/correlation 攻击测试。

## P2 — 泛化 qualified Skill execution

提交建议：`refactor(system2): make receipt dispatch skill-neutral`

- 从 compile-specific Dispatcher 提取通用 `RegisteredSkillApplication` 和 per-Skill evidence policy。
- 复用 TrustedWorldState、StateDelta、System2ToolResult 和 history；旧 compile 行为全量兼容。
- 把 replay 接到同一 `ToolResult → receipt → state/history`，先用现有真实 application，再补 Genesis。
- 删除 exact `CompileSystem2Application`/Text2Env input/output assertions，换成 descriptor codec。

## P3 — GoldenRunHarness 持久聚合

提交建议：`feat(harness): add durable golden run aggregate`

- 实现 S1 `start/submit/read`、SQLite/CAS repository、operation journal 和 transactional outbox。
- 每 workflow 单 state-mutating operation；crash-before/after-execute/commit 注入测试。
- 状态更新和 receipt/event head 原子提交；重启后可从不可变 records 重建。

## P4 — 真 MCP adapter

提交建议：`feat(mcp): expose qualified golden workflow tools`

- 新增 optional `mcp>=2.2,<3` 和 console entry point。
- 实现 S2：frozen RegistrySnapshot → exact tools、resources、structured output/media、stable errors。
- 用官方 MCP Python client 测试 stdio negotiation、tools/list/call、resources、timeout/cancel、stdout
  cleanliness；项目 `.codex/config.toml` 配置 required server 与适当 tool timeout。
- 一个 scripted external client 先跑 compile/replay/validate workflow；随后用真实 `codex exec` 证明
  Codex 实际调用工具。

## P5 — Validate 与 promotion 唯一门禁

提交建议分两笔：

1. `feat(validate): publish qualified portable validation decisions`
   - 完成 validate handler/Application/qualification/decision receipt；从同一 CAS closure 重算。
2. `feat(promotion): atomically promote validated environments`
   - validation 决定 publishability；promotion 只验证新鲜度/identity并事务发布。
   - ephemeral 与 operator-authorized production publication 严格分开。

至此先跑一个 legacy text/SAPIEN same-workflow tracer，证明证据链结构；不得称 Genesis 完成。

## P6 — 历史资产债务 bootstrap repair

按独立小提交：

1. `feat(asset-repair): classify recoverable ledger debt`
2. `feat(asset-repair): stage exact byte recovery`
3. `feat(asset-repair): qualify collision provenance`
4. `feat(asset-repair): qualify runtime and atomically promote`
5. `feat(asset-repair): add Genesis backend qualification`

先做 `003_plate`、`071_can`；通过后扩 825 upstream 与 27 个可恢复第三方 cohort。34 个 hash
mismatch 与 4 个缺文件资产必须新 identity 重建或明确退休。不得批量就地改 162 份坏 ledger。

## P7 — Gujie anti-corruption 与 portable Genesis rigid profile

提交建议按来源拆分：

1. `feat(genesis): import Gujie standard URDF closure`
2. `feat(genesis): reject legacy path and status conflicts`
3. `feat(genesis): add bounded layout and position solve`
4. `feat(genesis): add baseline and half-dt replay evidence`
5. `feat(genesis): qualify portable rigid-scene profile`

只移植可审阅模块并保留 Gujie attribution；优先从 committed `a8ced27` 获取，dirty-only 代码须先冻结成
明确 patch/source manifest。所有旧 output 先 snapshot 再 ACL import；当前 Harness 必须重新 replay。

## P8 — Genesis robot-policy/data-collection profile

提交建议：`feat(genesis): qualify robot policy environment profile`

- 首批固定 `franka_tabletop@1`，提供 reset/action/observation/termination 和 trajectory recorder。
- 执行 bounded nonzero action probe、RGB/proprioception/contact capture、reset repeatability、dataset
  manifest；结果 hash-bound。
- 有 task goal 时执行 success/reward evaluator；否则明确只给通用 policy/data interface。
- 真实 Genesis CPU/GPU 按 profile记录，不外推到真机。

## P9 — 共享 IR 与三组 x2env Skills

1. `feat(x2env): add shared input scene task package contracts`
2. `feat(text2env): qualify Genesis compile replay validate v2`
3. `feat(image2env): qualify compile replay validate v1`
4. `feat(video2env): qualify compile replay validate v1`

三个 façade 共享 normalizer、IR、asset policy、Genesis compiler/runtime，不复制流水线。每组先独立跑
exact reuse、digital cousin、fresh generation 三条 golden line，再进入统一 Codex 路由。

## P10 — Fresh observation、Codex diagnosis 与 fallback

提交建议按行为拆分：

1. `feat(observation): bind fresh Genesis observations`
2. `feat(codex): record visual and diagnostic advisories`
3. `feat(codex): version prompt repairs and cross-modal fallback`

Codex经 MCP读取当前 ImageContent/keyframes 和 diagnostics；advisory 不推进可信世界事实。prompt revision
与 video→image fallback 均产生新 turn/package，完整保留失败。

## P11 — Golden runner、真实 Codex 与案例

提交建议：`feat(golden-e2e): run Codex MCP Genesis workflows`

- 实现 S6 统一 runner、case schema 和 canonical report。
- scripted client 的 deterministic golden lines 必须 100%。
- 真实 `codex exec --ephemeral --json --sandbox read-only` 只经项目 MCP 操作，同一 workflow 完成路由、
  compile/replay/observe/diagnose/revise/validate/promotion。
- 每个成功 case 保存小型 evidence index、可观看图片/视频链接和 portable verification command；bulk
  runtime output 不进 Git。

## P12 — Autoresearch

- 在确认的独立 worktree/branch 建 baseline，按 `AUTORESEARCH_SETUP.md` 运行至门限或预算耗尽。
- 每个 experiment 先提交，结果追加 TSV；discard/crash 也保留原因和 evidence refs。
- runtime/Harness bug 退出实验循环，用普通 TDD feature 修复后重建 baseline。

## P13 — 真实事件驱动工作台

提交建议按页面能力拆分：

1. `feat(workbench): render workflow dashboard from MCP events`
2. `feat(workbench): add draggable operation console`
3. `feat(workbench): show assets replay validation and receipts`
4. `docs(workbench): link source repo docs roadmap and evidence`

前端/BFF只读写同一 MCP tool/resource/event surface；不得通过文件 mtime、日志关键词或 demo job JSON
推断阶段。浏览器 E2E 覆盖 event cursor、断线补页、失败/retry、媒体 playback 与验证详情。

## P14 — 全量验收与 walkthrough

- clean committed checkout：root pytest、100% new-module coverage、ruff、schema snapshot、diff check。
- 三模态 standalone + multimodal Codex suite、真实 Genesis、portable relocation、restart/replay。
- 写 `docs/walkthroughs/golden-codex-x2env-genesis.md` 与 evidence index，逐步链接契约、主要代码、测试、
  run receipts、图片/视频和失败记录。
- 同步 `repo-docs/`、change-log、code map、source evidence；把 walkthrough 地址和本进度目录都写到根
  `AGENTS.md`。
- 最终报告分别列出 verified、blocked、not implemented；不声称真机或未跑 simulator/profile。
