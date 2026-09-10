# Text2Env replay qualification refresh — 2026-09-10

本次只为固定 P0+P2 集成源码恢复 `text2env.replay@1.0.0` 的 deployment-local
资格，不回写 2026-09-02 历史证据。资格 distribution 起点是干净提交
`13f654e72e0204993355fa098e240698e1d8b085`；执行前旧 checked-in bundle 先以
`implementation_file_mismatch: self_improving/harness/__init__.py` 拒绝，同时依赖 compile
资格的 production import tracer 已为 pass。

## 入口与固定输入

解释器为 `/home/jingxiang/miniconda3/envs/robotwin-5090/bin/python3.10`
（Python 3.10.20，SHA-256
`ff0d9976054ea10aee690d9f04293889907ddfe84e41f9ab262af8810e537b5e`）。
运行使用当前 worktree 的 `script/run_scene_runtime.py`（SHA-256
`2c21b18668d080297919d6dbc8b767c0f5aa1de84799cd57d331a7b960046392`）、
重新探测的 canonical capability
`c9e32cc356b6c01ee4daa5691e97e87b43bf021ac5ac452dda25703978ea0d0f`、
固定 native launcher
`092597d14624759011d8c3f53d4f203ea6a345541bac1e2075b263247694d7ec`
和 FFmpeg
`74f0443e5d684e051726fc39546c77b18b031395c30a5d5764b95af8e3a86780`。

官方入口在全新的外部 bundle、scratch/CAS 和临时 delegated user scope 中运行：

```bash
systemd-run --user --scope \
  --unit=replay-qualification-13f654e-lrgwcd-r2 \
  --property=Delegate=yes --expand-environment=no \
  /bin/bash <external-run-root>/inputs/run-in-delegated-scope.sh
```

scope 内建立 `supervisor/jobs` 拓扑后，以干净 environment 执行：

```bash
PYTHONPATH=<distribution-root> PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
  /home/jingxiang/miniconda3/envs/robotwin-5090/bin/python3.10 \
  -m self_improving.harness.qualify_replay \
  --settings <external-run-root>/inputs/replay-qualification-settings.json
```

settings 固定请求 `Place a can on top of a plate.`、seed 7、task
`demo_clean`、`precheck/settle/contact/video/fps = 0/900/120/120/12`，并绑定当前
distribution、scene-gen、ledger contract、catalog、RoboTwin root、解释器、runner、capability、
launcher、FFmpeg 和 delegated scope。生成器在执行前先通过 14 个 replay-bound
model↔committed-schema exact-byte 门。

## 两次真实执行

- candidate-direct `71000000-0000-4000-8000-000000000001`：单 attempt、
  `succeeded`、14 个完整 runtime events、900 steps、120-step contact window。
- production-kernel candidate `71000000-0000-4000-8000-000000000002`：单 attempt、
  `succeeded`、同样 14 个完整 runtime events 和 900/120 参数。
- 两者 runtime evidence 均为
  `1c74e310fff651ef7ca562f8e57b66fdf6f4e2c7a0e14abc8d2c878435351f1f`，
  validation report 均为
  `01e85c898631343c46df50ecb2fb26dd0b4e9c2e424424553004f19b2ef09d75`，
  status `pass`、`fail=0`、`not_run=0`。
- 两者视频均为 MP4/H.264、320×240、12 fps、120 sequential frames、100 source-unique
  frames、114 decoded-unique frames；video SHA-256 为
  `a41d05366050ba0c2af635e69299a12a4b2a0e78ed27490517d27b122fa12a4e`。
- runtime asset snapshot 有 23 个全部复核的 members，SHA-256
  `43df37804b8e8c6dcb807f2f803e0d591dee18b7d5bbb8863b476f12144b43d4`；
  evidence closure manifest 包含 62 refs，连同 manifest 共 63 refs。

## 身份、reload 与三文档

执行前、执行后、发布前三阶段 source snapshot 相同：49 个显式 implementation files，
implementation SHA-256
`f6bc59e628bd72ff3b5195915a6bea53c7656154d6c71bd135eb6dd39347c604`，
Harness tree
`973a358e592093e9cbe346671c42c6e5e7a1f4fa8517ebc7020ecf00bd964780`。
scene-gen / ledger-contract 分别为
`e2fe9fd66d917dc6dea40e8990248206e180e3a086c1619b9f40558c3efec330` /
`1d52584d44ca5f5d759f5e72d327fed282f0bdb4adaf763387b2fda2467ef489`。

把本轮新 CAS 的 66 个对象逐一按内容摘要复制到另一个全新 CAS 后，在同名活动 delegated scope
内依次调用 `verify_replay_qualification_bundle` 和
`load_replay_qualification_bundle`；二者均为 `pass`，loader 最终 CAS 有 68 个对象。
三份 generator 原始输出为：

| 文件 | bytes | SHA-256 |
| --- | ---: | --- |
| `manifest.json` | 7,839 | `9c2df8b0da668917df4ba3c870bc553c1b842188f3eba0c875374fdab8d0b69f` |
| `qualification.json` | 315 | `8da49335dfbee9040ab5c93df7e45cea30d84a15d0a95b450b6e4d0173ff4bdb` |
| `report.json` | 13,535 | `003e6e01ac51bf1deafb73a21511a1bd68252749a9cf419cd7f4e9ba68550c73` |

三文档不含 host absolute locator。它们不是只改 expected hash：八项报告门绑定两次真实执行、
物理与顺序媒体、五个精确 dependencies、完整 CAS closure、14-schema gate、三阶段源码和 operator
输入，再由 fresh-CAS strict loader 重算。

## 失效模式与边界

第一次 scope 命令在启动生成器前因外层换行转义失败，没有创建 bundle/scratch 或执行候选。
生成成功后，第一次在已退出 scope 外尝试 strict reload，按设计以
`operator directory locator is unavailable` 拒绝；随后才在同名活动 scope 与另一个全新 CAS
完成严格 reload。两次失败均未修改资格文档。

## 回归门禁

三个原先被精确排除的资格节点不排除运行为 `3 passed`；replay/loader/schema/checked-bundle 专项为
`172 passed`，32-schema snapshot、Ruff、replay 相关文件 format 与 `git diff --check` 均通过。把
已有非格式化的 `test_golden_run.py` 也纳入整文件 format check 时仍返回 1；对 `HEAD` 原文件的同一
check 也返回 1，因此本切片没有机械重排其中与两个 expected digest 无关的基线代码。完整 Harness 首轮
发现 Golden start request/receipt 的两个固定摘要仍引用 compile 重签前的 Registry snapshot；只把
规范化重算的 expected refs 更新为
`a0d30998afdd7da2e1f3c572e2ac36ffaec73383209d2d86833dfd4e9333ae1b` /
`0aada891737d2a397afe2ebbb231cf38f16dbd99f8426bf431dddbdc1fe7d457` 后，完整 Harness 为
`2505 passed, 20 skipped`。

完整 root 为 `3516 passed, 20 skipped, 2 failed`。两项失败都位于 `tests/demo/test_harness_compile.py`：
负向用例先给 `harness_run_states` 安装 trigger，P2 的当前 RunStore layout gate 在 Invocation 持久化前
即拒绝含 trigger 的 authority，因此 Workbench 返回 unavailable；旧测试仍期待 trigger 运行后的
terminal reload/history 对账错误。本 replay 切片没有修改该实现或弱化两项负向用例，按实际结果保留。

本证据不资格化 `text2env.validate@2`、MCP、Genesis、promotion、跨机器可移植安装或真机。
Dashboard/ClawCross 三个状态入口仍返回 HTTP 404，未声称已同步控制面。
