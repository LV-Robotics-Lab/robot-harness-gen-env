# Autoresearch Setup 草案

状态：`pending_user_confirmation`

| 参数 | 草案 |
| --- | --- |
| Goal | 提高 text/image/video/multimodal 输入经 Codex 路由后完成 Genesis golden E2E 的成功率 |
| Metric command | 待调研后冻结为单一 JSON benchmark 命令 |
| Metric extraction | 主指标 `closed_loop_success_rate`；同时记录 route、Skill、细节阶段成功率和失败分类 |
| Direction | higher is better |
| In scope | `self_improving/**`、必要的 `scene_gen` 合同扩展、相关 tests/scripts/docs/repo-docs |
| Out of scope | 外部项目历史、私有资产/模型、与 golden E2E 无关的用户工作树修改 |
| Constraints | 既有测试不退化；每次实验先提交；无新依赖或环境变更除非用户批准；不夸大真机/仿真能力 |
| Max experiments | 建议首轮 30 次，达到冻结门限可提前结束；待用户确认 |
| Simplicity policy | 同等指标下选择更小 interface、更少分支和更少依赖的实现 |

为保护当前含用户修改的工作树，实验循环应在独立 clean worktree 和
`codex/autoresearch-golden-e2e-routing-20260909` 分支进行；discard 只作用于该实验 worktree。
