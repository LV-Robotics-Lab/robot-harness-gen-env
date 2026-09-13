<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# workflows

## Purpose（用途）
GitHub Actions CI 工作流定义。在受支持的 Python 版本上跑基于 fixture 的 pytest 套件，与 `README.md` 中的安装/测试契约保持一致。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `ci.yml` | CI：Python 3.11/3.12 的六组与独立 root 测试，以及同版本覆盖归并；保留限时日志与测量。 |

## Subdirectories（子目录）
无。

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- CI 没有 RoboTwin/SAPIEN checkout，因此这里只跑基于 fixture 的 `pytest -q` 套件。不要加需要真实物理的步骤。
- 保持 Python 版本与 `pyproject.toml` 对齐（`requires-python = ">=3.11"`）。

### Testing Requirements（测试要求）
- push 会触发 CI 运行；合并前确认工作流为绿。
- 统一入口是 `script/run_self_improving_tests.sh`；新增 canonical 测试须更新 `x2env_test_groups.py` 显式归属。
- 验收使用同源六组的成功测量；当前matrix v2按用户授权报告语句/分支覆盖与缺口，不要求精确100%。
  测试/lint/测量完整性仍阻断；不扩大排除。检查 `pending-gates.json`，未执行门不计通过。
- CI 制品只上传日志、JUnit、结果与覆盖测量；隔离临时环境和fixtures不上传。
- 第五组额外保留reader-docs.json，本地链接/anchor、历史bytes与有界公共HTTP分别报告；
  未执行远端不是通过。源码身份也覆盖这些读者文档与归档manifest，不能跨docs变更复用测量。
- 提交前用 action 校验器校验 YAML 语法。

### Common Patterns（常见模式）
- 安装含 `platform` 的开发依赖；每测试组、独立 `pytest -q` 与覆盖归并各有1770秒加30秒清理预算。
- matrix v2本次人工执行额外收紧为1170秒加30秒；不将此称为上述CI配置默认已改变。

## Dependencies（依赖）

### Internal（内部）
- 镜像 `pyproject.toml` 可选依赖与 `README.md` 安装说明。

### External（外部）
- GitHub Actions（`ubuntu-latest`，`actions/setup-python`）

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
