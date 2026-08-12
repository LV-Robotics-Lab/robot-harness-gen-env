<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# .github

## Purpose（用途）
GitHub 仓库配置容器。仅持有 CI 工作流定义；不放源码或文档。

## Subdirectories（子目录）
| Directory | Purpose |
|-----------|---------|
| `workflows/` | GitHub Actions CI 工作流定义（见 `workflows/AGENTS.md`） |

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- 不要在此添加非 CI 文件。任何不是 GitHub Actions 工作流的东西都应放在仓库其他位置。

### Testing Requirements（测试要求）
- CI 改动由 push 触发的 GitHub Actions 执行。提交前校验工作流 YAML 语法——本地无测试入口。

### Common Patterns（常见模式）
- 工作流在 `pyproject.toml` 声明的受支持 Python 版本上跑 `pytest -q`。

## Dependencies（依赖）

### External（外部）
- GitHub Actions（linux runner，`actions/setup-python`）

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->