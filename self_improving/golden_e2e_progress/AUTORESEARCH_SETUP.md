# Autoresearch Setup 草案

状态：`accepted_2026-09-09`

按 `autoresearch` skill，以下参数在循环开始前必须由用户整体确认。功能/物理 bug 先按普通 TDD 修复；
只有基线可执行后才启动评分实验，避免把“修到能跑”误计成模型路由提升。

## Goal

提高外部真实 Codex 只经 Harness MCP 对 text/image/video/multimodal 输入进行路由、观察、诊断和
fallback 后，完成 Genesis robot-policy golden E2E 的稳定成功率，同时保持零不安全晋升和完整证据
绑定。

## Frozen suites

实现时提交两份不可由实验修改的 suite：

1. `self_improving/golden_e2e/evals/codex-routing-v1.json`
   - 36 个 family-grouped case：text/image/video/multimodal 各 9 个。
   - 平衡 exact reuse、digital cousin、fresh generation、unsupported/abstain、conflicting modality 和
     video→image fallback。
   - dev 24、held-out 12；同一输入 family 的 seed/变体不得跨 split。
2. `self_improving/golden_e2e/evals/genesis-golden-v1.json`
   - 12 个真实 execution case：text/image/video × exact/cousin/fresh 共 9 个，加 3 个 multimodal
     conflict/diagnosis/prompt-revision case。
   - dev 8、held-out 4；每个候选版本对 dev case 重复 3 次，held-out 只在最终候选运行。

所有输入 bytes、expected route、允许 fallback、profile 和 gate 以 CAS/hash 固定。无法合法提交的大
媒体以 source manifest + expected SHA 固定；缺失时 case 为明确 blocker，不从分母静默删除。

## Metric command

```bash
python script/run_golden_e2e_benchmark.py \
  --routing-suite self_improving/golden_e2e/evals/codex-routing-v1.json \
  --execution-suite self_improving/golden_e2e/evals/genesis-golden-v1.json \
  --split dev \
  --repeats 3 \
  --agent codex-mcp \
  --workspace-mode ephemeral \
  --json
```

该命令必须只向 stdout 写一个 canonical JSON object；进度写 stderr。每个 case 的全部成功/失败、
Codex/MCP/Genesis receipts 和资源计数保存在 run-owned evidence 目录，stdout 不输出秘密或本机路径。

## Metric extraction and direction

- Primary JSON pointer：`/metrics/closed_loop_success_rate`
- 定义：`eligible attempts` 中同时满足 route、exact qualified Skill chain、Genesis robot-policy replay、
  validate publishable 和 ephemeral promotion commit 的 attempts 比例。
- Direction：higher is better。
- crash、timeout、early stop、缺失 ToolResult、未晋升和证据无效均计 0；case 不得静默跳过。

同时记录：

- `route_accuracy`、每模态 route accuracy、abstention accuracy；
- compile/replay/observe/diagnose/revise/validate/promote 及其内部细节阶段成功率；
- fallback recovery、3-run repeatability、median/p95 latency、Codex input/output tokens、Genesis steps；
- 每种 failure code、首个失败阶段、全部原始失败 receipt；
- `unsafe_publication_count`、`evidence_binding_failure_count`、`unlogged_failure_count`。

## Acceptance / protection gates

候选只有全部满足才 keep：

- primary 相对当前 best 有提升；同分选更简单版本；
- `unsafe_publication_count == 0`；
- `evidence_binding_failure_count == 0`；
- `unlogged_failure_count == 0`；
- deterministic scripted golden lines 为 100%；
- root tests 和新模块 statement/branch 100% coverage gate 不退化；
- route accuracy >= 0.95，per-modality >= 0.90；
- closed-loop success >= 0.90，per-modality >= 0.80；
- fallback recovery >= 0.80；
- successful attempt 的 receipt/CAS/portable replay verification 为 100%。

最终候选再运行 held-out 一次（3 repeats）。held-out 不达到同一门限，结论为未达标并继续普通修复或
申请下一轮实验预算；不得调低 validator/expected label 或把失败 case 移出 suite。

## In scope

- `self_improving/golden_e2e/codex/**` 的 agent instructions、context projection 和 typed response schema。
- MCP tool/resource descriptions、eligible-tool presentation 和无语义默认值的 schema ergonomics。
- 显式 route/fallback policy 及其 tests。
- 为测量准确性所需的 benchmark/logging 修复。

运行中发现 simulator、asset、MCP transport 或 Harness 证据 bug 时，暂停实验，在正常 TDD feature
branch 修复、全量验证并重建 baseline；该修复不作为一次 prompt/routing experiment 的提升。

## Out of scope

- 修改 frozen eval inputs、labels、split、阈值、物理 validator、promotion gate 或 qualification 证据。
- 修改 Gujie/第三方项目历史，复制私有资产/模型，或提交 secrets/runtime bulk output。
- 用 fake Codex、fake Genesis、缓存成功结果或手工编辑 run report 计分。
- 与 golden E2E 无关的现有用户工作树修改。

## Constraints and budget

- 独立 clean worktree：建议
  `/home/jingxiang/bingsheng/worktrees/golden-e2e-autoresearch-20260909`。
- 专用 branch：`codex/autoresearch-golden-e2e-routing-20260909`。
- 每个 experiment 先提交、后测量；`results.tsv` 追加 keep/discard/crash，保留失败摘要和 evidence ref。
- discard 只回退专用实验 worktree 的提交，不碰当前 dirty worktree。
- 最多 30 个 candidate experiments 或 12 小时实际循环时间，先到者为止；达到所有门可提前结束。
- baseline 与每个 candidate 使用相同 Codex 配置、MCP server、runtime profile、case order 和资源限额。
- 实验阶段不新增依赖、不改系统环境、不下载模型；依赖与真实 runtime 在 baseline 前冻结。
- 简单性：同分优先更少规则、更短 instructions、更少 token 和更少专用分支的候选。

## 待确认事项

- [x] 确认 Goal、两个 suite 的规模与 split。
- [x] 确认 metric command、primary extraction 和 higher-is-better。
- [x] 确认 in/out scope 与保护门。
- [x] 确认 30 次 / 12 小时首轮预算和独立 worktree。
