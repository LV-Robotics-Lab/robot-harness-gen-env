# 稳定决策

## D001 — Codex 是认知中枢与视觉判读者

- 日期：2026-09-09
- 状态：accepted
- 决策：Codex 取代原计划中的独立 System 2 LLM 中枢和独立 VLM。Harness 保留确定性、类型化、
  fail-closed 的执行与证据权威；Codex 只能提出下一步、诊断和视觉判断，不能自报执行成功、物理通过
  或 publishable。
- 影响：现有 Qwen planner/VLM 代码作为历史实验或可选 adapter 评估，不再决定目标架构。新接口优先
  表达 `Codex proposal → Harness validation/execution → trusted ToolResult/observation → Codex next turn`。

## D002 — Golden output 是 sim-ready Genesis 环境

- 日期：2026-09-09
- 状态：accepted
- 决策：文本、图像、视频或组合 prompt 的最终机器产物必须能被 Genesis 原生加载，供机器人策略
  测试或数据采集；同时输出哈希绑定的资产、场景、运行报告、图像和连续视频。
- 边界：是否复用 `worktree/gujie` 的 Genesis 原生输出不影响其接入 Harness，但非 Genesis 原生输出
  必须经过明确 adapter 和真实加载门，不能仅凭文件扩展名或 render 判为 sim-ready。

## D003 — 证据权威与晋升

- 日期：2026-09-09
- 状态：accepted
- 决策：compile success、Codex/VLM judgement、媒体可见和 replay process success 均不能单独给出
  publishability。只有哈希闭包完整、真实 Genesis replay/validate 通过且 promotion gate 接受的候选
  才能晋升。

## D004 — Deep aggregate 与 exact Skill MCP

- 日期：2026-09-09
- 状态：accepted
- 决策：workflow 业务通过 S1 `GoldenRunHarness.start/submit/read` 聚合；外部 Codex、benchmark 与
  frontend 使用同一 S2 MCP adapter。MCP 公开精确版本、已资格的 x2env tools，而不是通用
  `skill.invoke`；耗时执行以 durable operation/resource 呈现。
- 边界：Codex 位于 MCP seam 外。它的 route、视觉判断和诊断是 advisory，Harness 对状态、执行、
  validate 和 promotion 保持唯一权威。

## D005 — Genesis 最终能力门

- 日期：2026-09-09
- 状态：accepted
- 决策：最终 golden case 必须满足 `genesis.robot_policy@1`，包括版本化 robot profile、reset、typed
  action/observation/termination、bounded nonzero action probe 与 trajectory；仅 load/build/step 的
  `genesis.rigid_scene@1` 是中间门。
- 边界：该能力不声明训练 policy 成功或真机可用。

## D006 — Autoresearch 冻结设置

- 日期：2026-09-09
- 状态：accepted
- 决策：路由 suite 为 36 cases、执行 suite 为 12 cases；primary metric 是
  `closed_loop_success_rate`，higher is better。首轮独立 worktree 最多 30 次或 12 小时，保持已确认的
  in/out scope、保护门和简单性优先策略。
- 前置：真实 benchmark command、MCP、Codex 与 Genesis 基线全部可执行后才开始实验计数。

## D007 — 功能级来源与整合责任必须分账

- 日期：2026-09-09
- 状态：accepted
- 决策：以 `docs/integration-provenance/LEDGER.md` 作为 Bingsheng、Gujie、Yuxin 工作进入 Golden
  E2E 的核对清单。每项能力分别记录原始来源、第三方上游、Harness 整合责任、固定 ref、目标路径、
  合同和验证状态；功能切片与台账更新必须同一提交。
- 边界：目录所有者或整合者不自动成为被整合代码的原作者；`integrated` 不等于
  `runtime_pass`；未固定的 dirty workspace 字节不能计入完成来源。
