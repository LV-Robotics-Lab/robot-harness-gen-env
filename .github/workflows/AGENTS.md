<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# workflows

## Purpose（用途）
GitHub Actions CI 工作流定义。在受支持的 Python 版本上跑基于 fixture 的 pytest 套件，与 `README.md` 中的安装/测试契约保持一致。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `ci.yml` | CI：在 Python 3.11 & 3.12、ubuntu-latest 上初始化 submodule，并跑核心与 self-improving 全量离线测试。 |

## Subdirectories（子目录）
无。

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- CI 没有 RoboTwin/SAPIEN checkout，因此这里只跑基于 fixture 的 `pytest -q` 套件。不要加需要真实物理的步骤。
- 保持 Python 版本与 `pyproject.toml` 对齐（`requires-python = ">=3.11"`）。

### Testing Requirements（测试要求）
- push 会触发 CI 运行；合并前确认工作流为绿。
- 统一入口是 `script/run_self_improving_tests.sh`；新增模块时同步扩展该脚本。
- 提交前用 action 校验器校验 YAML 语法。

### Common Patterns（常见模式）
- 通过 `pip install -e '.[dev,demo]'` 安装；用 `pytest -q` 跑套件。

## Dependencies（依赖）

### Internal（内部）
- 镜像 `pyproject.toml` 可选依赖与 `README.md` 安装说明。

### External（外部）
- GitHub Actions（`ubuntu-latest`，`actions/setup-python`）

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
