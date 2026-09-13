# Canonical x2env 文档入口

本组文档对应 `worktree/bingsheng` 当前 canonical 实现。用户通过一个 Harness 提交文本、图像、视频或组合输入；内部模型理解与诊断，controller 选择资产并执行 `x2env.compile/replay/validate`，输出场景、资产、媒体及验证证据。

## 按任务阅读

1. 第一次运行：[用户操作指南](walkthroughs/canonical-x2env-user-guide.md)。包含安装、现有本机入口、四种输入、状态/恢复/取包与错误处理。
2. 编写调用程序：[Python 与 CLI API](canonical-x2env-api.md)。以当前函数签名、参数和返回值为准。
3. 配置部署、资产和后端：[接入指南](canonical-x2env-integration.md)。区分安装 Python 包、登记本地资产与配置外部运行资源。
4. 修改代码或复现实验：[测试指南](canonical-x2env-testing.md)。包含定向测试、六组测量、schema/文档检查和真实 replay 的边界。
5. 选择下一阶段工作：[开发方向与计划](canonical-x2env-roadmap.md)。每项列前置条件、交付与验收。

## 已证明的范围

matrix v2 的 text、image、video、image+text 四个简单 workflow 成功，使用本地 mouse/redblock 资产；S01/S04 实际产生不可变颜色子版本。S01 另做一次隔离复制后的 Genesis 双时间步运行与新媒体输出。对应运行绑定各自源码提交，不能因本轮更新文档而改写身份。

web 和 reconstruction adapter 已有实现，本机四例部署未配置它们的外部资源，不能称三来源都已真实通过。复杂容器、关节、多点支撑及机器人 policy/data collection 尚未获得端到端验证。`succeeded` 表示当前开发工作流完成；导出中的 `sim_ready=false`、`release_qualified=false` 与独立 copy-run 结果分别阅读。

## 真实证据和文件位置

- [实施 walkthrough 与审计](../self_improving/golden_e2e_progress/CANONICAL_MATRIX_V2_PUSH_AUDIT_20260913.md)：源码、测试、运行和剩余能力。
- [四例机读索引](../self_improving/golden_e2e_progress/qualification-matrix-v2-results.json)：workflow、输入、seed、场景/资产/报告位置与 hash。
- [开发证据 Release](https://github.com/LV-Robotics-Lab/robot-harness-gen-env/releases/tag/x2env-evidence-f80e2a82efd53fff5822a7cfdaa63e41c65d4af1)：固定 f80e2a82 的开发证据，不是正式产品资格。
- 本机用户入口：`/home/jingxiang/bingsheng/canonical-x2env-user-entry-20260913.AydTBI/USAGE.md`。
- 最终 push 审计：`/home/jingxiang/bingsheng/canonical-matrix-v2-push-audit-20260913.cKTw0K/push-audit.json`。
- [工作区归档与恢复说明](workspace-collaboration.md)：旧路径部分为阅读兼容链接，严格拒绝 symlink 的执行入口不能随意换成归档路径。

自己的请求输出由 `--output` 决定。先读命令 JSON 的 `delivery` 和输出目录 `result.json`，按其中真实成员路径查找图片、MP4、资产和日志；不要假设所有媒体都位于 `media/`。环境包通常把证据置于 `evidence/`，资产位于 `assets/`，精确布局见 API 和测试指南。

## 文档版本与历史

[原收敛计划](../self_improving/golden_e2e_progress/CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md)记录最初目标；[matrix v2](../self_improving/golden_e2e_progress/qualification-matrix-v2.json)及最新审计决定本阶段实际验收范围。旧 12 例、100% 覆盖、三个来源和 MCP 方案不能当成当前运行结果。

稳定 `scene_gen/` 的 RoboTwin/SAPIEN `/gen-env` 是另一条保留路径，使用[原 compile 手册](compile-api-guide.md)。本组 Genesis 文档不替换它的 API 或物理验收标准。
