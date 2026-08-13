# Self-Improving 平台边界

`scene_gen/` 是稳定信任边界，负责把受限文本编译成可验证、可回放、哈希绑定的场景包。`self_improving/` 是消费者和编排层：它可以选择环境、组织采集与训练、评估失败、写诊断和记忆、决定是否晋升，也可以调用资产与仿真适配器；它不能伪造或跳过 `/gen-env` 的物理门控。

| 层 | 目录 | 写什么 | 不写什么 |
| --- | --- | --- | --- |
| 稳定核心 | `scene_gen/` | schema、parser、grounding、solver、builder、validator | 策略、训练循环、仿真器特定编排 |
| 场景编排 | `self_improving/stage5/` | designer/critic/grounding agent、prompt、MCP-lite | 核心物理判定的替代实现 |
| 闭环 | `self_improving/alchedata/` | collect/train/evaluate/diagnose/transfer、失败记忆、promotion gate | 大规模 runs、checkpoint、下载缓存 |
| 资产 | `self_improving/asset_pipeline/` | 发现、ingest、ledger、catalog 对接、迁移 adapter | 第三方 mesh 与渲染产物 |
| 仿真适配 | `self_improving/sim_adapters/` | 薄脚本、schema、可隔离测试 | 完整复制 IsaacLab 或候选仓库 |
| 外部项目 | `external/` | 钉住子模块 commit | vendor copy |
| 历史 | `self_improving/legacy/` | 只读来源快照 | 新功能 |

`python -m self_improving --json` 只检查这些源码是否到位以及子模块是否初始化，不导入 GPU 框架、不启动仿真器。来源工作区、提交、归档分支和排除项在 `self_improving/source_inventory.json`，它是清理旧副本前的审计入口。

当前离线、自包含回归基线是 518 passed、6 skipped。skip 仅对应未纳入 Git 的 Isaac/SceneAgent/媒体/报告原始包或本机未安装的 SAPIEN 物理运行时；源码、schema、ledger、fixture、Web Studio 和 OpenXSim IR/adapter 都有仓库内测试覆盖。完整命令见 `self_improving/README.md`。

AgenticSim 名称有两种历史含义：旧产品仓库已经证明是 TacHarness 的稀疏历史状态，其唯一文件归档进 TacHarness 后本机副本已删除；`sim_adapters/agenticsim_runtime/` 只保留后来非 Git 工作区里的 Isaac 编排脚本，二者不能再混用。
