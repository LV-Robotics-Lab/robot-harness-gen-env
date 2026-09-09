# 当前 TODO

更新时间：2026-09-09

## Doing

- P1：补正式 `RegistrySnapshot`、canonical request CAS/ref 与可演进 receipt-head；workflow start 与
  重启幂等已完成，但在这些审计闭包修复前不进入 P2 dispatch。

## Next

- P2：在 actor-neutral v2 receipt/state-delta 上接入 exact qualified compile → replay 两步调度；不得复用
  planner-specific v1 receipt 字段冒充外部 Codex/MCP 调用。
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
- P0 clean baseline 已完成分类：根套件唯一红项来自旧 replay qualification 把整个 Harness 树当作
  source closure；最终在实现树稳定后真实重签或撤销，不能用更新 expected hash 伪修复。
- P1 首个 S1 纵切：从 canonical CAS user-input evidence 构建可信世界状态、actor-neutral start receipt
  与父 workflow snapshot；3 份公共 schema 已导出，focused 19 tests 通过，两个新增模块语句/分支
  覆盖率均为 100%。
