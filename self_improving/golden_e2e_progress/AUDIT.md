# Golden E2E 只读审计

审计日期：2026-09-09
审计基线：`worktree/bingsheng@c88a19b` 以及 Gujie 独立克隆最初的
`/home/jingxiang/gujie/gen-env@a8ced27`；2026-09-10 主窗口又复核其已提交增量
`eb0b710`。本页把 committed source、dirty workspace 和 ignored
runtime output 分开陈述。

## 1. Gujie x2env / Genesis

- 本仓 `worktree/gujie@a25bc58` 是 bingsheng 的旧祖先，ahead 0、behind 378，且 worktree 已
  prunable；它不是当前 x2env 实现。
- Gujie 当前实现位于独立克隆。其两个大提交 `9e8d28c`、`a8ced27` 合计约 40K 新增行，另有大量
  未提交 Genesis/SimFoundry 变更；不能整块 cherry-pick，也不能把 dirty 行为写成分支能力。
- 独立克隆随后把一批物理验收工作固定为 `eb0b710`（70 files，约 7,232 additions）：增加 scene
  physics graph、position solver、baseline/half-dt workflow、判据分类、有界 numerics repair、地面平面
  fixed-root 和媒体恢复检查。该提交自报常规回归 `1125 passed / 56 skipped` 与真实非凸网格 6 pass，
  但同时明确保留一个 1 mm 穿透门不可满足的既有失败，依赖落体证据的资产入库路径仍关闭；因此不能把
  提交信息里的局部通过扩张为完整 x2env qualification。
- `gujie-x2env-design.md` 自称架构设计稿。真正实现使用 `genenv.*` JSON、URDF closure 和 Python
  Genesis adapter，并没有设计稿里的完整 SceneIR/portable bundle。
- 可复用实现包括媒体 hash/逐帧完整性、标准 URDF closure、SimFoundry asset/scene import、场景图与
  布局检查、bounded position solver、稳定化不等于通过、baseline/half-dt 自由回放、接触/支撑/
  穿透判据以及 physics-pass 后才生成 final media 的门禁。
- 初始 build 默认 `collision=False`、`physics_steps=0`，因此 load/render success 不是 sim-ready。
  `validate_imported_scene` 和 dirty dual-dt workflow 才调用真实 `Genesis Scene.step()`。
- 已复核一个 ignored 文本运行产物：1,500 physics steps、180/180 unique video frames、终态
  `physics_passed`。它仍缺 Harness run/Registry/ToolResult/portable receipt 和 robot policy API。
- 已复核的 mouse image 产物只到 `scene_built`，physics/final render 为 `not_run`；它同时保留成功
  `run_report.json` 与旧 `failure.json`，暴露 TaskOutput 的语义一致性缺口。
- 当前 mouse video 共 124 帧，但没有该视频到 Genesis 的完整运行产物。历史 Fruits 证据来自
  OmniGibson，不是 Genesis。
- 当前输出绑定绝对路径；没有 image/video qualified Skills、MCP、Codex 路由、多模态融合、统一资产
  Registry，亦没有 robot/reset/action/observation/reward/policy/data-collection contract。
- `eb0b710` 改善的是 Genesis 刚体场景物理验收与受限修复，并未补上述 Harness/CAS/receipt/MCP 或
  robot-policy 合同；其最新 dirty workspace 仍包含 SimFoundry 子模块变化、Genesis 单资产 validator
  修改和未跟踪 physics-completion/WorldComposer 内容，必须继续与 committed 能力分开。

结论：Gujie 提供了“Genesis 原生加载与有限刚体物理回放”的重要底座，尚不是本任务要求的
policy-ready golden X2Env。接入应通过 anti-corruption adapter，把 `genenv.*` 映射到 Harness schema、
CAS、receipt 与 promotion，而不是复制其整个 workspace。

## 2. 历史资产债务

| 范围 | ledger | clean | violations |
| --- | ---: | ---: | ---: |
| 历史 asset library | 65 | 0 | 701 |
| upstream ledgers | 97 | 0 | 3,381 |
| 历史合计 | 162 | 0 | 4,082 |
| 新 generated ledger | 1 | 1 | 0（但 `pending_settle`） |

4,082 条违规包括 1,915 个已删除字段、1,757 个必需字段缺失和 410 个未绑定
`stable_poses.measured_against`。主要债务是 1,101 个缺 `files[]`、1,101 个废弃 `size_bytes`、563
个缺 collision provenance、407+407 个废弃 runtime mass 字段。

- 当前 `migrate_v3 --dry-run` 为 `migrated=0 failed=163`。
- `settle_repair` 在物理执行前要求 ledger 已完全合法，runtime load 又要求既有可信 settle/joint
  receipt，writeback 在追加前仍验证坏 ledger，形成 bootstrap deadlock。
- 不能通过放松 validator 或伪造 pose/collision receipt 解锁。缺失能力是：恢复 bytes 到隔离
  candidate staging → 枚举真实 loader closure → 生成 collision/settle evidence → durable journal
  原子晋升。
- RoboTwin 的 827 个 representation 中有 825 个 primary bytes 可按 SHA-256 精确恢复，仅缺两个
  USD。第三方旧 26G 副本中 19 个资产完全匹配，8 个仅旧 size 错；34 个 hash mismatch、4 个缺文件
  必须新身份重建或明确退休。
- 当前 ledger backend 枚举和 qualified issuer 不含 Genesis；SAPIEN pass 也不得自动满足 Genesis。

建议先用 `003_plate`、`071_can` 做 exact-recovery tracer bullet，再扩 825 upstream 与 27 个可恢复
第三方 cohort；不可恢复资产按来源/许可分批重建并重做资格，不能一次批量写 162 份权威账本。

## 3. Harness / System 2 / Codex / MCP

### 可以复用

- compile 已有生产 Application、资格 Registry、CAS、资产 admission/reuse、RunState/Invocation/Event。
- replay 已有独立 Application、真实 RoboTwin/SAPIEN 资格运行、运行时资产冻结、媒体和物理证据、
  execution receipt。
- `TrustedWorldState`、CAS compare-and-swap `StateDelta`、完整 `System2ToolResult`、receipt-backed
  history、planner context/decision binding 是可复用的领域底座。
- portable run receipt 已能包装 compile/replay 的共同 durable seam。

### 尚不存在

- `System2Dispatcher` 的生产实现和 receipt verifier 均硬编码为 `text2env.compile@1.0.0`。
- compile/replay 使用不同 Application/CAS 装配；replay handoff、acquisition、snapshot assessment 是
  独立窄 seam，没有统一 `ToolResult → StateDelta → History`。
- validate v1 必然因缺 promotion evidence fail-closed；validate v2 只有 schema、CAS reader 和
  snapshot recompute，没有 handler、descriptor、qualification、Application 或最终 decision receipt。
- planner 虽能提 `request_observation`，Dispatcher 会拒绝非 `invoke_skill`，且所有 ToolResult 的
  `fresh_observations=()`。
- diagnostics 当前只是 supporting artifacts 差集，不是类型化因果诊断；失败也没有完整 attempt
  history/state resume。
- 没有最终 `PromotionApplication/Decision/Receipt`；当前同名“promotion”只是跨 CAS 复制 replay
  input。
- 没有真实 MCP server 或 Codex adapter；`mcp_lite` 是 argparse wrapper，`codex_reference` 是
  deterministic function，不是真 Codex。

### 目标边界

Codex 必须作为仓外 MCP client。MCP server 不能在内部再次调用 Codex，也不能伪造 model snapshot。
一个 `workflow_run_id` 下应有多个独立 Skill `run_id` 和连续 `turn_seq`；每次 mutation 用
`expected_state_sha256` 和 idempotency key。Codex 的视觉判断只能形成绑定 replay/media 的 fresh
observation 或 diagnosis，不能签发 physics pass/publishability。只有确定性 Genesis replay/validate
和完整 closure 通过后，promotion policy 才能签发 publishable receipt。

[官方 Codex MCP 文档](https://learn.chatgpt.com/docs/extend/mcp)确认本地客户端支持 STDIO 与
Streamable HTTP MCP；本机 `codex-cli 0.153.4` 也提供 `codex mcp` 和
`codex exec --json --output-schema`。[Codex non-interactive 文档](https://learn.chatgpt.com/docs/non-interactive-mode)
说明 `codex exec` 可用于脚本和 CI。真实验收可让一个外部 Codex thread 经项目级 MCP 配置执行，
并保留 MCP 协商、tool calls、Harness receipts 和最终结构化回应；unit/integration benchmark 使用
同一 MCP surface 的 scripted client，不能用 fake provider 冒充真实 Codex。

## 4. 当前测试观察

- 根套件：`3015 passed, 19 skipped, 1 failed`；唯一失败是用户修改的
  `IMPLEMENTATION_LOG.md` 与 replay qualification manifest 身份漂移，fail-closed 正常但工作树不绿。
- System 2/portable receipt/validate-v2/replay bridge 专项：`756 passed in 24.01s`。
- Asset ledger/migration/settle trust 专项：`87 passed`；asset admission：`104 passed`。
- Gujie Genesis 默认离线 suite：`770 passed, 55 skipped`；真实 Genesis/GPU/在线 case 多数为 opt-in
  skip，不能按通过计。
- 20-case compile campaign 的 `physical_validation_gate_enforced=false`；当前所谓 digital cousin
  是 `task_affordance_equivalent_asset_reuse`，真实 ACDC 为 0/5。
