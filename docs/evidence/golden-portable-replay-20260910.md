# Golden replay 可移植安装路径证据（2026-09-10）

这份证据固定在起点 `2bdeafd836d641b4f14aef316a21fbcd736f55e1` 之后的实现提交
`487bcfc189e31784461ae0dc43fae5955dbf0d19`。机读摘要在
[`golden-portable-replay-20260910.json`](golden-portable-replay-20260910.json)。

## 结论

同一份 `text2env.replay@1.0.0` qualification bundle 未重签地在两个不同绝对源码路径完成 strict
verify/load，第二路径随后构造 production `ReplayApplication`，在全新 evidence root 中真实执行
`GoldenRunHarness` 的 compile revision 1 → replay revision 2。replay child 为 `succeeded`，物理报告
为 `pass`，参数为 0 precheck / 900 settle / 120 contact / 120 video frames / 12 fps。

源码资格身份不再含安装绝对路径。它以逻辑根 `replay_distribution`、50 个相对路径成员的 exact
bytes/SHA-256 和整树摘要 `65352d16…b4b35a` 绑定；loader 在调用方提供本地 root mapping 后仍按 P0
语义对每条目录链和文件做三轮独立 nofollow 重走、最终 root 重开与完整成员集对账。源码字节漂移、
缺/多成员、目录或链接异常、不同 tree、schema 漂移仍拒绝，没有靠忽略 source identity 获得可移植性。

`runtime_module` 与 `runtime_runner` 也使用同一逻辑根；只有当 worker 的 `PYTHONPATH` 精确等于已声明
本地源码根时，capability 才把它归一为逻辑根摘要。任意其他 `PYTHONPATH` 继续按原值哈希并造成能力
不匹配。RoboTwin 资产根、Python 解释器、media launcher、静态 FFmpeg、runtime capability 文件和
delegated cgroup 仍是明确的 deployment binding，逐文件或目录复验；这与代码安装路径已经分账。

## 固定身份与运行结果

- bundle 三文档：manifest `9553bf10…041a`，qualification `9315394c…cf03`，report
  `feb70567…c681`；implementation `990b9dbf…5779`。
- fresh-CAS strict load：A/B 两路径均 pass，68 个 CAS 对象，62-ref evidence closure
  `92457468…9110`。
- direct 与 production-kernel 各单 attempt succeeded；runtime evidence 都是
  `71109653…6589`，validation report `01e85c89…9d75`，23-member runtime asset manifest
  `43df3780…43d4`。
- relocated Golden workflow 为 revision `0→1→2`；compile/replay ToolResult 分别为
  `4160f12f…4b27` / `da0f650f…288c`，最终 runtime evidence 为 `ddb1cf26…673d`。
- MP4 `a41d0536…12a4e` 为 61,285 bytes。独立解码得到 120 帧、114 个 decoded unique、
  320×240、12 fps；可观看文件：
  `/home/jingxiang/bingsheng/golden-portable-replay-20260910.m9L950/view/observer_runtime-a41d05366050.mp4`。
- 无 live application 的新进程重读 revision 2，再提交原 command 返回同一 terminal operation；没有
  再次执行 runtime。

最终门禁为 focused 431 passed、Harness 2671 passed / 20 skipped、root 3684 passed / 20 skipped、
39 份 schema snapshot、全部变更 Python 文件 Ruff 与 `git diff --check` 通过。仓库级 `ruff check .`
另报告 2,463 个既有问题，集中在旧 demo、scene_gen 与归档 validation evidence；本切片未修改或
批量格式化这些无关文件。

## 边界

本结论证明的是同一 exact 源码字节和同一资格包可跨“代码安装绝对路径”执行，不声称跨机器自动配置
外部 runtime/operator 依赖。它没有调用 `text2env.validate`，也不证明 Genesis native、
`genesis.robot_policy@1` 或 promotion/publishability。ClawCross dashboard 的 portfolio、tasks、project
三个状态 URL 本次仍返回 HTTP 404，因此没有伪报 dashboard 状态已同步。
