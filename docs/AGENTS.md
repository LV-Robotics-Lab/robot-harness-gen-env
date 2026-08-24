<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# docs

## Purpose（用途）
已验证、计算得到的证据笔记与结构化运行时报告的文档容器。这些产物支撑 `README.md` 中陈述的验收主张与阈值。

## Subdirectories（子目录）
| Directory | Purpose |
|-----------|---------|
| `evidence/` | 计算得到的物理验收笔记与 prompt-matrix 报告（见 `evidence/AGENTS.md`） |
| `harness-skill-walkthrough/` | 面向项目组成员的 Harness compile/replay/validate 互动导览网站及其本地依赖 |

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- 这里的文件是验收契约的证据，非一般性叙述。不要在此添加叙事文档——把架构/fixture 文档放在它们所描述的代码旁边。
- 证据笔记与某次真实运行按哈希和时间戳绑定；不要回溯编辑历史笔记，应另起一份带日期的新笔记。

### Testing Requirements（测试要求）
- 本目录不跑测试。若某证据笔记被测试或 README 断言引用，编辑时保持被引用的事实不变。

### Common Patterns（常见模式）
- 文件名编码证据类型 + ISO 日期（如 `prompt-matrix-20260719.md`）。
- 每份笔记记录复现该主张所需的命令、阈值、报告哈希与逐用例结果。

## Dependencies（依赖）

### Internal（内部）
- 被 `README.md` 引用，并遵循 `script/run_prompt_matrix.py` / `script/build_stage5_report.py` 的输出约定。

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
