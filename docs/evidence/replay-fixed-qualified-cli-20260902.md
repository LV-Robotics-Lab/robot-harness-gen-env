# 固定资格 Replay CLI 真实运行证据（2026-09-02）

## 结论

提交 `8ea46f850688acf41bfea5a20b59b99d2c092f03` 新增的 operator-only 固定资格
replay CLI 已在 `text2env.replay@1.0.0` 的证据绑定部署中真实运行。最终 run 为
`succeeded`：执行 900 个主物理步，产出 120 帧 H.264 视频，其中 100 帧互异；运行时
validation 为 `pass`，`fail_count=0`、`not_run_count=0`。CLI 只输出一行 locator-free
终态摘要，不把这次成功扩大为 validate decision、portable receipt 或 publishability 证明。
配套结构化报告为
[`replay-fixed-qualified-cli-20260902.json`](replay-fixed-qualified-cli-20260902.json)，文件 SHA-256 为
`10891aac0f3933c387485014a5d8becfd60d3898e7527dc28687bd76fe21084e`。

## 资格与实现绑定

- fixed case：`can-on-plate-seed-7-900-120-120-12`
- Skill：`text2env.replay@1.0.0`
- implementation SHA-256：
  `3ad6aff715baade429e2315d6a1f53ccc432affe57610a1a3653ef51587a0379`
- qualification report SHA-256：
  `0690d305a794b23b3b701379540394f998c06a34d43eb019fc05cdf521dbef53`
- Invocation digest：
  `7bed94e1b63e083d1719513db3b8a2cf8de7de8990cbbc80f2c4df7fd270569d`
- EnvironmentPackage / resolved scene digest：
  `96e995144468707f0f6e169341ce13e2330b1f42a8acaf73d9c126925e4be3ee`

新增的深模块和薄脚本位于 replay source snapshot 之外；在干净的提交副本中，checked-in
qualification bundle 与 CLI 联合测试为 65 passed，完整仓库测试为 2477 passed、19 skipped。
因此本切片没有改变资格所绑定的 Harness、`scene_gen` 或 ledger contract bytes。

## Fail-closed 前置验证

第一次调用使用了当前会话的环境，CLI 正确返回 `blocked`（exit 10），没有进入仿真：

- run id：`618756a5-5c4e-4c53-b6a1-846571a8e87c`
- blocker：`HARN_DEPENDENCY_UNAVAILABLE`
- durable events / artifacts：4 / 29
- qualified runtime capability SHA-256：
  `ee3a155c4077d9bbe0abe97b3b9df45c90c4bb409d397beee196a87251b4f511`
- observed preflight capability SHA-256：
  `e8a44adfb51ab48e61e1365c66910b020e4f2e4fcd06f300bf9c21bab675f543`

两份 capability 的唯一语义差异是 allowlisted `PATH` value digest：资格值为
`33f0a9ae5571020617b6eb14e9ee827833f8fa0534cdfcaa36141b3461ea7185`，当前会话值为
`b8f77299ded6453cbff7246f66f38793656c0a40b5ff2e249f0909eb0893a0db`。部署侧构建记录保留了
资格时的精确环境；恢复其已记录值后，新 state root 的第二次调用才进入真实回放。这里记录摘要，
不提交原始环境变量或任何私有绝对 locator。

## 成功运行

- run id：`0000f972-15e7-4ada-aec6-707e8973acde`
- CLI exit / terminal status / attempt：`0` / `succeeded` / `1`
- committed events / artifacts：19 / 43
- simulation / total physics / contact-window steps：900 / 900 / 120
- video frames / unique frames：120 / 100
- runtime evidence SHA-256：
  `1c74e310fff651ef7ca562f8e57b66fdf6f4e2c7a0e14abc8d2c878435351f1f`
- runtime validation report SHA-256：
  `01e85c898631343c46df50ecb2fb26dd0b4e9c2e424424553004f19b2ef09d75`
- observer video SHA-256：
  `a41d05366050ba0c2af635e69299a12a4b2a0e78ed27490517d27b122fa12a4e`
- validation：`pass`，`fail_count=0`，`not_run_count=0`

CLI 在输出摘要前已对 returned state、两次持久化 RunState/Invocation、完整 EventPage 和
类型化 replay output 做一致性检查。Registry 在提交终态前解析并重验每个 ArtifactRef；本记录不把
CLI 摘要另称为 emission-time CAS closure 或 portable receipt。

## 复现模板

先在资格记录绑定的同名 delegated scope 中建立 `supervisor` / `jobs` 拓扑，并让调用进程位于
`supervisor`；root 与 `jobs` 必须为空，`cpu`、`memory`、`pids` controller 必须在两层启用。
随后使用一个新的、从未存在过的 state root：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
  <qualified-python> <qualified-source-root>/script/run_qualified_replay.py \
  --settings <external-fixed-replay-settings.json>
```

settings 使用 `harness.qualified_replay_fixed_case_launch.v1` 的精确 11 字段契约；资格 bundle、
源码根、runner 与 replay input 均由 checkout 固定，不能由 settings 覆盖。运行前还必须恢复资格记录
绑定的 allowlisted 环境摘要；任一漂移都应先得到 exit 78 或 durable exit 10/20，而不是降级运行。

提交级离线复现：

```bash
pytest -q -p no:cacheprovider \
  tests/self_improving/harness/test_qualified_replay_bundle.py \
  tests/self_improving/test_qualified_replay_cli.py
pytest -q -p no:cacheprovider
```

## 边界

这仍是 deployment-local 证据：外部 CAS、SQLite、媒体、RoboTwin 资产、工具、capability、settings
和 delegated cgroup locator 不进入 Git。active same-UID caller 在 application 交接后替换 state pathname
不在 CLI 的 cooperative claim 防护主张内。独立 validate v2、portable run receipt、promotion 和通用
Workbench replay 仍未完成。
