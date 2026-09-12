# Harness通用实验CLI

公共入口：`python -m self_improving.harness.experiment_cli --deployment CONFIG COMMAND`。
`run_cli(argv)`返回退出码并输出一个JSON结果；`parse_request(argv)`只解析，不启动数据库或模型。

- submit接受可选text/text-file（二者互斥）、可重复image、可选video，至少一种输入，且需要
  idempotency-key。调用现有LimitedImagePreviewController.start，由其管理ExperimentService。
- resume需要workflow-id，调用同一controller.resume；不重复submit。
- status直接service.status，成功读取退出0，保留真实workflow状态。
- package需要workflow-id和绝对新目录output，调用package_workflow；不需要模型。

部署JSON明确development、allow_changed_skill_closure=true、state_root、runtime_roots、
codex_executable、approved_executable_sha256、server_python、server_workdir、model、evidence_root。
可选max_attempts/call_budget/attempt_seconds、catalog_path/asset_library_root/source_ref/denied_roots。
所有部署路径绝对且无..，未实现release拒绝；布尔opt-in不能用整数1替代。
CodexBackend的server_settings使用同一部署路径，用户不操作Codex/MCP。配置不是由prompt指定。

退出码：输入/配置错误2，执行错误或非passed结果1，passed及成功status读取0。passed仅指阶段
序列；controller返回的parent_terminal/snapshot不得改写为父workflow已终态。
失败JSON保留error_code/detail。模型/运行日志仍由既有backend/controller保存。

当前验证是解析、无输入、release配置拒绝及显式布尔准入测试；不注入模拟运行成功来冒称
CLI真实E2E。完整submit依赖集成Service、controller实验分支和package adapter，真实案例由
当前实验进度账本记录。
