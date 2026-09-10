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
  E2E 的核对清单，并以 `sources/*.md` 的逐人模块/功能矩阵回答“谁具体做了什么”。每项能力分别
  记录原始来源、第三方上游、Harness 整合责任、固定 ref、目标路径、合同和验证状态；功能切片与
  台账更新必须同一提交。
- 边界：目录所有者或整合者不自动成为被整合代码的原作者；`integrated` 不等于
  `runtime_pass`；未固定的 dirty workspace 字节不能计入完成来源。

## D008 — 保留 legacy validate v2，Genesis validate 升为 v3

- 日期：2026-09-10
- 状态：accepted_by_existing_upgrade_clause
- 决策：既有 `harness.text2env_validate_input/output.v2` 明确绑定 `EnvironmentPackageV1`、RoboTwin
  receipt 与 `robotwin.scene_validation.v1`，因此只用于 legacy RoboTwin/SAPIEN validator。Genesis
  `EnvironmentPackageV2` 与 `genesis.robot_policy@1` 的 text validation Skill 固定为
  `text2env.validate@3.0.0` / `text2env_validate_v3_0_0`；不得原地改变 v2。
- 依据：D003 的唯一晋升权威，以及 `SKILL_CONTRACTS.md` 已接受的“不兼容则升 v3”条款。
- 边界：本决定只冻结版本语义，不表示 v2 或 v3 已实现、资格化或运行通过。

## D009 — replay 源码资格使用逻辑安装根，部署依赖继续物理绑定

- 日期：2026-09-10
- 状态：accepted
- 决策：`text2env.replay@1.0.0` qualification 的源码 identity 由逻辑根
  `replay_distribution`、相对路径、exact bytes/file hash 与 closure tree hash 构成；绝对源码安装路径
  只作为 load 时的本地 mapping，不进入资格身份。loader 必须在该 mapping 上重新执行完整的三轮
  nofollow closure，不能跳过 source identity。
- 部署分账：RoboTwin 资产、解释器、media launcher、静态 FFmpeg、runtime capability 与 delegated
  cgroup 继续作为 machine-local operator binding 记录和复验。worker `PYTHONPATH` 只有精确等于已声明
  local source root 时才映射成逻辑身份，其他值仍保持部署敏感。
- 边界：这只保证同一源码字节/资格包跨代码安装绝对路径复用，不提供跨机器 provisioning，也不扩大为
  validate、Genesis 或 promotion。

## D010 — Image 与 Video 保持不同身份，legacy 输出先适配再重跑

- 日期：2026-09-10
- 状态：accepted_by_contract_implementation
- 决策：`image2env.*@1.0.0` 与 `video2env.*@1.0.0` 使用不同 source-media schema 和 exact
  compile/replay/validate codec。Video 必须持久化完整 ordered frame manifest、总/唯一帧数、FPS、时长
  与 sequence digest；抽帧不能降格成 Image 输入或完整视频证据。Gujie `genenv.*` 只经
  anti-corruption adapter 进入 `legacy_genenv_adapted` candidate，随后在当前 Harness run 重做真实
  Genesis replay/validate。
- 边界：合同与旧输出中的 `passed`、render、文件格式或 process success 均不授予 simulator-ready、
  physical pass 或 publishable。External Codex 提供 proposal/advisory，不在 exact Skill 内调用模型。

## D011 — Fresh observation 先闭合受控证据链，再开放 production tool

- 日期：2026-09-11
- 状态：accepted_by_existing_skill_contract
- 决策：fresh observation 的 exact Skill 固定为 `genesis.observe@1.0.0`。首个受控纵切复用 S1
  `GoldenRunHarness.submit/read`，并要求 FreshObservationReceipt、`ToolResult.fresh_observations`、
  `TrustedToolReceipt.derived_fact_claims`、StateDelta 与 resulting state 对 key/value/capture time/TTL
  逐项绑定。原始 observation 指向 FreshObservationReceipt，可信状态 fact 指向闭合它的外层
  TrustedToolReceipt。TTL 取 `1..300` 秒，使用闭区间 `[captured_at, valid_until]`。
- 新鲜度：capture timestamp 由 runtime 产生，`valid_until == captured_at + TTL`；历史截图、旧 replay
  media 或旧 observation receipt 只能作为历史证据，重新包装不获得 fresh 身份。
- 边界：受控 test adapter 必须记录 `simulator_executed=false`。真实 Genesis sealed-checkpoint capture、
  production qualification 与 production Registry/MCP exposure 未通过前，不得把受控结果写成 Genesis
  runtime pass，也不得在 production MCP 目录列出 `genesis_observe_v1_0_0`。
