# 当前 TODO

更新时间：2026-09-09

## Doing

- P1：在已完成的 canonical start/Registry 闭包上补公共 `read` 与可注入的 durable aggregate store，
  再以单条 RED→GREEN tracer 进入 submit/operation 生命周期。

## Next

- P2：在 actor-neutral v2 receipt/state-delta 上接入 exact qualified compile → replay 两步调度；不得复用
  planner-specific v1 receipt 字段冒充外部 Codex/MCP 调用。
- P4：S1 的 `submit/read`、OperationSnapshot 和真实 Registry handler 绑定完成后，接入 MCP 2.2 的
  low-level Server/Client；在同一切片冻结 dependency/runtime lock，不在实验循环中安装依赖。
- P6：以已分类的 `003_plate`、`071_can` 继续 exact-byte staging 纵切；先建立 loader closure，仍不得
  就地修改历史 ledger 或把 byte match 写成 runtime qualification。

## Blocked

- ClawCross/dashboard 的 portfolio、tasks、project 三个状态 URL 均返回 HTTP 404；无法同步 TODO
  控制面，也没有可用 task id 写回进度。
- Autoresearch 的 benchmark/真实 MCP/Genesis 前置尚未实现；设置已确认但尚不能建立诚实 baseline。
- exact MCP adapter 仍缺 `submit/read` 和真实 Registry handler 交集；caller 提供的内部一致
  `RegistrySnapshot` 目前不能单独作为生产 trust root。
- 三份 reader-facing repo-docs 仍把 Harness schema 数写成 17；当前实际为 28。文件已有用户修改，
  本切片不覆盖或暂存，P14 必须在可安全协调时同步。
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
- 建立 `docs/integration-provenance/`，以逐人模块/功能矩阵固定 Bingsheng、Gujie、Yuxin 的具体工作、
  来源版本、整合责任、第三方上游与验证状态，并从根 `AGENTS.md` 建立强制更新入口。
- P0 clean baseline 已完成分类：根套件唯一红项来自旧 replay qualification 把整个 Harness 树当作
  source closure；最终在实现树稳定后真实重签或撤销，不能用更新 expected hash 伪修复。
- P1 首个 S1 纵切：从 canonical CAS user-input evidence 构建可信世界状态、actor-neutral start receipt
  与父 workflow snapshot；3 份公共 schema 已导出，focused 19 tests 通过，两个新增模块语句/分支
  覆盖率均为 100%。
- P1 durable start/idempotency 纵切：跨实例 retry、冲突拒绝和 CAS/SQLite 恢复校验已完成；focused
  29 tests 通过，相关新增模块语句/分支覆盖率 100%。
- P1 Registry/request 闭包纵切：正式 `RegistrySnapshot` strict canonical 解析并交叉绑定 descriptor、
  qualification、report；start request 进入 CAS，receipt head 改为可演进的 versioned workflow receipt
  family。focused 48 tests 通过，3 个相关模块语句/分支覆盖率 100%，25 份 schema snapshot 通过。
- P6 首个 S5 只读纵切：从显式受信、strict-canonical 的 debt-inventory CAS 对 `003_plate`、
  `071_can` 分类，确认 2 份 ledger 的 58 条结构违规与 14 个本机 observed digest matches；输出明确
  `probe_bytes_in_cas=false`、`writes_performed=false`、`runtime_qualification_executed=false`。这只是
  2/162 的 E0 tracer，来源总体与 inventory/selection totals 已分层绑定，不能改 scope 冒充全量；尚未
  覆盖 4,082 条全量清单或建立 portable byte closure。
