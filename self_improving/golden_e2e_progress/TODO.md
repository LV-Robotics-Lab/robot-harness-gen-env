# 当前 TODO

更新时间：2026-09-09

## Doing

- P0：分类旧 `text2env.replay@1.0.0` qualification 的 clean-source 漂移；最终只能由新真实运行重签，
  不能改 hash 装绿。
- P1：复核 actor-neutral workflow identity/receipt 的第一个 `GoldenRunHarness.start()` tracer。

## Next

- 完成 P0 后，从 P1 workflow identity/receipt tracer bullet 开始逐切片 RED→GREEN。
- 在 P4 前冻结官方 MCP dependency/runtime lock，不在实验循环中安装依赖。

## Blocked

- ClawCross/dashboard 的 portfolio、tasks、project 三个状态 URL 均返回 HTTP 404；无法同步 TODO
  控制面，也没有可用 task id 写回进度。
- Autoresearch 的 benchmark/真实 MCP/Genesis 前置尚未实现；设置已确认但尚不能建立诚实 baseline。
- clean root suite 的旧 replay qualification source identity 仍有 1 个失败；原始真实 qualification
  settings/CAS 尚在，但旧 delegated cgroup 已不存在，且后续 Harness 实现还会继续改变源码身份。

## Done

- 接受 Codex 同时承担中枢 agent 与视觉判读职责的稳定架构决定。
- 建立本进度目录和根 `AGENTS.md` 触发入口。
- 完成 Gujie committed/dirty/runtime 三层 x2env 与 Genesis 审计。
- 完成历史 162 份 ledger、4,082 条违规和可恢复字节审计。
- 完成现有 compile/replay/validate、System 2、Codex/MCP 同-run 断点审计。
- 完成极简 aggregate、capability kernel、exact Skill MCP 三套独立接口设计。
- 推荐 A 内核 + C 外观 + B 最小共享模型，并起草 Skill/验收/实施合同。
- 用户确认 S1–S6、exact Skill MCP、`genesis.robot_policy@1` 最终门、`mcp>=2.2,<3` 与
  36/12 cases、30 次或 12 小时的 autoresearch 设置。
- 修复 portal 删除后的 required-capability 漂移；当前共享 checkout 的
  `python -m self_improving --json` 返回 `ready=true`，portal 为 `required=false/status=missing`。
- 已确认领域语言写入 `CONTEXT.md`，并建立 external Codex/deep aggregate/exact MCP 的 ADR。
- 建立 `docs/integration-provenance/`，按功能固定 Bingsheng、Gujie、Yuxin 的来源、版本、整合责任、
  第三方上游与验证状态，并从根 `AGENTS.md` 建立强制更新入口。
