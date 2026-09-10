# Text2Env replay qualification refresh after P3a — 2026-09-10

本记录只证明固定源码 `8e45bd9d755ca68001f3372286cbaff265bbc461` 上
`text2env.replay@1.0.0` 的 deployment-local 资格。P3a 修改了资格闭包内的
`replay_application.py`；执行前，严格 checked gate 因该文件 identity 漂移拒绝上一份 bundle。
本轮没有手工改写 report 或实现摘要，而是用官方 generator 重新执行完整案例。

## 真实固定案例

官方入口在全新的外部 bundle、scratch/CAS 和临时 delegated user scope 中运行。运行前重新探测
当前 worktree 的 capability，然后固定输入 `Place a can on top of a plate.`、seed 7、task
`demo_clean` 与 `precheck/settle/contact/video/fps = 0/900/120/120/12`。解释器、RoboTwin 资产、
runner、capability、native media launcher 与 FFmpeg 均由 operator manifest 精确绑定。

- candidate-direct `71000000-0000-4000-8000-000000000001`：单 attempt、`succeeded`、14 个完整
  runtime events、900 steps、120-step contact window。
- production-kernel candidate `71000000-0000-4000-8000-000000000002`：单 attempt、`succeeded`，
  同样完成 14 个 runtime events 与 0/900/120/120/12。
- 两者 physics status 均为 `pass`，`fail=0`、`not_run=0`；runtime evidence 为
  `1c74e310fff651ef7ca562f8e57b66fdf6f4e2c7a0e14abc8d2c878435351f1f`，validation report 为
  `01e85c898631343c46df50ecb2fb26dd0b4e9c2e424424553004f19b2ef09d75`。
- 两者视频均为 MP4/H.264、320×240、12 fps、120 sequential frames、100 source-unique frames、
  114 decoded-unique frames；视频摘要为
  `a41d05366050ba0c2af635e69299a12a4b2a0e78ed27490517d27b122fa12a4e`。
- runtime asset snapshot 有 23 个全部验证的 members；evidence closure 有 62 refs。

## 严格加载与三文档

generator 生成后，把原 CAS 的 66 个内容寻址对象逐一按摘要复制到另一个全新 CAS；在新的同名活动
delegated scope 内，`verify_replay_qualification_bundle` 与 `load_replay_qualification_bundle`
均返回 `pass`，加载后的 CAS 为 68 个对象。14 个 replay-bound schema exact-byte gate 与三阶段源码
快照也全部通过。

| 文件 | bytes | SHA-256 |
| --- | ---: | --- |
| `manifest.json` | 7,839 | `099d1ea7639e65aee86eae33518207ee82aa82eb53b676b53d093e74755c3f91` |
| `qualification.json` | 315 | `1023f6c80aea00c17314d5fb670092e18c54b1d4fc5f239b1afc80d68820265d` |
| `report.json` | 13,535 | `8ee738b0109611c3cd6556f550506f25d46e234b3c6341ecd740fd7608665171` |

49-file implementation SHA-256 为
`53b905f90c3da8e28371292b6ea0990f14f7f097f81fe1853c87e06146c3efdf`，Harness tree 为
`3c462d6d99b63a014d726e9b512b071061563f5c883d62698abb0feabb74806e`；scene-gen 与 ledger
contract tree 分别为
`e2fe9fd66d917dc6dea40e8990248206e180e3a086c1619b9f40558c3efec330` / 
`1d52584d44ca5f5d759f5e72d327fed282f0bdb4adaf763387b2fda2467ef489`。

## 边界

外部 CAS、SQLite、媒体、RoboTwin 资产和 operator locators 不进入 Git；checked-in 三文档不含
host-absolute locator。这份资格不证明任意新机器可直接安装，不证明 validate v2、Genesis、promotion
或真机。后续 P3b 若只增加资格闭包外的 Golden replay binding，不应改变本资格 identity；若闭包内源码
再变化，loader 必须再次拒绝。

Dashboard/ClawCross 三个状态入口仍返回 HTTP 404，本轮未声称控制面同步成功。
