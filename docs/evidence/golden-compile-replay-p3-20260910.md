# Golden P3 qualified compile → replay — 2026-09-10

本记录证明固定源码
`b51bf5783f9ea5cd8e0e4559bef020d934d15d30` 上，一个 actor-neutral
`GoldenRunHarness` 在同一 workflow 内通过同一 sealed catalog 依次提交
`text2env.compile@1.0.0` 与 `text2env.replay@1.0.0`。它只证明当前部署的 legacy
RoboTwin/SAPIEN compile→replay 纵切，不证明 Genesis、validate Skill、最终
`genesis.robot_policy@1` 门、promotion 或真机能力。

## 输入与真实运行

- prompt：`Place a can on top of a plate.`；seed 7；compile 禁止缺失资产生成。
- replay：`precheck/settle/contact/video/fps = 0/900/120/120/12`。
- compile 与 replay 各有独立 controlled child UUID4，但共用 workflow、CAS、父 SQLite、
  `GoldenRunHarness.submit` 和同一个 idempotency key；exact Skill ref 是幂等域的一部分。
- replay application 由 production factory 组装；解释器、RoboTwin、runner、capability、native
  media launcher、FFmpeg 与 delegated scope 都与当前 checked qualification evidence 对账。
- 新外部工作目录中的 compile CAS 先装入上一轮严格重载通过的 replay qualification closure，之后
  compile/replay 的新 command、Invocation、RunState、ToolResult、receipt、state 与媒体都写回同一
  CAS。仓库没有提交运行时资产或媒体。

第一次尝试在任何 child intent 前被 start 门拒绝：一次性运行脚本把 objective 时间直接编码为
`+00:00`，不符合该模型的规范 JSON 字节。没有调用 compile 或 replay。第二次改由类型化
`WorldFactEvidence` 生成字节后完成全链；该失败没有被隐藏或改写为成功。
外部两轮 attempt log SHA-256 为
`3677f3f1461bc7041b05f92a1d03cf70c95793f41a41e8f88ee1162fa6403a1b`。

运行入口是外部证据目录中的 `run_golden_compile_replay.py`；delegated scope 由以下命令创建，scope 内
脚本再建立 `supervisor/jobs` 子 cgroup 并执行 production Python 入口：

```bash
systemd-run --user --scope --unit=replay-qualification-p3b-xpnsna \
  -p Delegate=yes /bin/sh \
  /home/jingxiang/bingsheng/golden-p3-compile-replay-20260910.6Fylru/run-in-delegated-scope.sh
```

重启读取使用同目录的 `verify_restart.py`。这些脚本和 CAS/SQLite/media 是部署侧运行记录，不是 Git
发布物；checked-in JSON 仅保留不含 operator locator 的结果摘要。

## 同一 workflow 的证据链

| 阶段 | revision | 状态 | 关键内容身份 |
| --- | ---: | --- | --- |
| start | 0 | active | workflow `81000000-0000-4000-8000-000000000001` |
| qualified compile | 1 | succeeded | ToolResult `ea546331…ca2b4`；operation receipt `b5ed99c9…58c5d` |
| qualified replay | 2 | succeeded | ToolResult `59d30901…359d`；operation receipt `63f96f0f…0c3b1` |

replay receipt 的 predecessor 是精确的 compile receipt。revision-1 受信状态 artifact 为
`8eb3911a96f35e2d9f52da59dacd4d35e7244fc6e993ff2f643d80df62168942`；replay parameters 中的
EnvironmentPackage 与 asset catalog 都逐字来自该状态中同一个 compile trusted receipt。revision-2
状态 artifact 为 `469f22440c71c5da62247140f9017f0bdd6e21cbade60adbcef3c39cfb614e36`，内部 state identity 为
`05d5700572ee34504c32017b784182af07b54d76dcb2364a321a4cde7ad6c825`。

replay 的唯一新增受信事实是 `replay.runtime_evidence`；ToolResult 的
`fresh_observations=()`。媒体只作为 supporting artifacts，不晋升为 world fact。

## 物理与媒体结果

- runtime evidence：
  `ddb1cf26bb3d9ad0d904519dbe4f0b506918131b13ab1678854e537e68b7673d`，status `pass`，
  900 simulation steps、120-step contact window。
- runtime validation report：
  `01e85c898631343c46df50ecb2fb26dd0b4e9c2e424424553004f19b2ef09d75`，status `pass`、
  `fail=0`、`not_run=0`。这是 replay handler 的运行时 supporting artifact，不是
  `text2env.validate` operation。
- MP4：`a41d05366050ba0c2af635e69299a12a4b2a0e78ed27490517d27b122fa12a4e`，
  120 sequential frames、100 source-unique、114 decoded-unique、12 fps。
- 可观看副本位于外部证据目录
  `/home/jingxiang/bingsheng/golden-p3-compile-replay-20260910.6Fylru/view/`：1 个 MP4 与 7 个 PNG；
  `summary.json` SHA-256 为
  `bc76b8e8ec021c94a129ba634787f45670c15cd3e2dfdd5544d8443db59b200b`。

## 重启验证

真实运行结束后，新建不含 live application 的 `GoldenRunHarness`，只挂接 compile/replay 两个持久
child SQLite authority。它从父 SQLite/CAS 重读 revision 2，并用原 replay command 再次调用
`submit`；返回 byte-equivalent 的既有 operation，child terminal、runtime evidence 和 MP4 摘要均未
变化。外部 `restart-verification.json` SHA-256 为
`b1ed8fc532333040a73b7163f6e542bf80c0fa23ff70620fcd7d584c65b93837`。

机读摘要见
[`golden-compile-replay-p3-20260910.json`](golden-compile-replay-p3-20260910.json)，其 SHA-256 为
`c47eeea6ab460b639750090f60a61370c7c9aab76b328a63352340ee18d780c3`。Dashboard/ClawCross
三个必读状态入口仍返回 HTTP 404，本轮没有声称控制面同步成功。
