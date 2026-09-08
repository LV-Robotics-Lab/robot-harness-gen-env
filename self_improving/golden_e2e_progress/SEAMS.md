# TDD 公共 seam 草案

状态：`pending_user_confirmation`

以下 seam 来自用户指定的调用路径，具体 interface 将在 `worktree/gujie` 和当前 Harness 调研后细化。

1. `X2EnvApplication.execute(request) -> X2EnvResult`
   - 可观察结果：规范化输入身份、选定路径、环境包、资产入库结果、阻塞/失败分类。
2. `CodexHarnessTurn.execute(turn_request) -> CodexHarnessTurnResult`
   - 可观察结果：planner proposal、qualified Skill invocation、ToolResult、fresh observations、状态版本、
     diagnostics、promotion decision 和完整 receipt refs。
3. `HarnessMCPAdapter.list_tools()/call_tool(...)`
   - 可观察结果：版本化 JSON schema、精确 Skill identity、typed ToolResult，以及稳定 MCP 错误映射。
4. `GenesisEnvironmentVerifier.verify(package) -> GenesisVerificationResult`
   - 可观察结果：原生加载、仿真步进、资产闭包、图像/连续视频、物理检查和可移植 receipt。
5. `GoldenE2ERunner.run(case) -> GoldenE2EReport`
   - 可观察结果：单 case 的输入到入库、replay、validate、fallback、晋升全过程和分阶段成功率。

测试只跨这些公共 seam；Genesis/Codex 属于系统边界，测试使用明确 adapter，真实验收使用真实 adapter。
