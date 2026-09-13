# ASPIRE 本地复现报告

## 结论

本机完成了**上游 harness 机制的部分复现**，没有完成、也不声称完成论文规模的机器人基准复现。
在官方提交 `7ba73d3bcac8f6b6d4a7d67ed4040988f768d282` 上，使用 ASPIRE 锁定的
NumPy/SciPy 版本后，所选无服务测试为 `20 passed, 4 skipped`。四个 skip 分别是未安装的
PyRoKi、Contact-GraspNet，以及需要显式真实集成服务的 LIBERO/Robosuite smoke。

官方十任务 LIBERO-Pro Goal-Swap Fix Loop 没有启动，因为当前机器不满足其完整实验身份：参考
runbook 要求八张 GPU（SAM3、GraspNet、PyRoKi 与五个 task slot）、三个感知服务、gated 权重、
模型 endpoint、专用环境/子模块，以及固定 51–65 development / 1–50 held-out 分区。当前只有一张
RTX 5090，相关服务、权重凭据、专用环境和子模块均未就绪。缩小问题规模后运行不能升级为论文
复现，所以没有这样做。

## Source receipt

| 项目 | 观测 |
| --- | --- |
| 上游 | `https://github.com/NVlabs/ASPIRE.git` |
| 本地形态 | `external/ASPIRE` Git submodule |
| 固定提交 | `7ba73d3bcac8f6b6d4a7d67ed4040988f768d282` |
| 上游工作树 | clean |
| 论文版本 | arXiv `2607.00272v1` |
| 论文原始 run outputs | 官方仓库未提交，无法从仓库 artifact 独立重算论文表格 |

## 尝试 1：当前开发环境

从 `external/ASPIRE/aspire/sim` 运行：

```bash
PYTHONPATH=../.. python -m pytest -q \
  tests/test_gen_progress.py \
  tests/test_libero_fix_loop_pipeline.py \
  tests/test_record_skill_promotion.py \
  tests/test_dep_compat.py \
  tests/test_libero.py \
  tests/test_robosuite_setup.py
```

结果为 `18 passed, 4 skipped, 2 failed`。两个 failure 仅来自精确依赖门禁：当前环境是
NumPy 2.4.4 / SciPy 1.17.1，上游要求 NumPy 1.26.4 / SciPy 1.15.3。JUnit receipt 位于
`artifacts/upstream_selected_tests.xml`。这一步证明失败可归因于环境身份，不应记作上游机制回归。

## 尝试 2：最小锁定环境

在研究 artifact 下创建隔离 Python 3.12 环境，只安装运行这组无服务测试所需的 pytest 与上游
明确锁定版本：

```bash
uv venv --python 3.12 artifacts/upstream-unit-venv
uv pip install --python artifacts/upstream-unit-venv/bin/python \
  pytest numpy==1.26.4 scipy==1.15.3
```

同一测试集合得到：

```text
20 passed, 4 skipped
```

Skip 原因被显式保留：

- `pyroki` 未安装；
- `contact_graspnet_pytorch` 未安装；
- LIBERO smoke 要求 `ASPIRE_INTEGRATION_REAL=1` 和真实服务；
- Robosuite smoke 要求 `ASPIRE_INTEGRATION_REAL=1` 和真实服务。

JUnit receipt 为 `artifacts/upstream_selected_tests_pinned.xml`。通过的路径覆盖 generation progress、
Fix Loop immutable validation identity/progress、skill-promotion receipt，以及 dependency lock/import
检查。这不覆盖 TraceLogger 多模态落盘、agent 生成代码、soft/hard timeout、感知/规划服务、
simulator task success、evolutionary candidate quality 或论文成功率。

## 与本项目真实回放的边界

本研究另在本项目 `robotwin-5090` 环境执行了一次真实 can-on-plate SAPIEN 回放，并由改进后的
validator 得到 pass。它验证的是本项目 runtime evidence 与 exact resolved scene 的集成，**不是**
ASPIRE LIBERO/Robosuite/BEHAVIOR 任务复现。对应证据在
`artifacts/real_replay_can_on_plate_seed7/`。

## 复现等级

| 层级 | 状态 | 能支持的结论 |
| --- | --- | --- |
| 官方源码/协议获取 | 完成 | 可审计固定提交与 runbook |
| 无服务 harness 单测 | 完成（20 pass / 4 explicit skip） | 部分机制代码可在锁定环境复现 |
| 本项目真实 SAPIEN integration | 完成（1/1） | producer-declared identity equality gate 可消费 runner 自声明字段；非完整 provenance |
| ASPIRE perception/planning services | 未运行 | 无服务/权重/完整 GPU 拓扑 |
| 官方 simulator benchmark | 未运行 | 不支持本地成功率比较 |
| 论文表格/消融独立重算 | 不可完成 | 官方仓库无原始输出，且完整设施未满足 |
| sim-to-real | 未运行 | 不作任何 real-robot 提升主张 |

因此，后续结论严格分为：论文作者报告、上游源码事实、本地机制复现、本项目合成契约实验、
本项目真实 SAPIEN 单例验证。五者不可互相替代。
