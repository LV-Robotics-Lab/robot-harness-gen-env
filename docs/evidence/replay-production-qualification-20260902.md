# Text2Env replay production qualification evidence — 2026-09-02

这份记录只证明固定源码和固定依赖下的 `text2env.replay@1.0.0` 资格案例，以及随后由正式
`ReplayApplication` 发起的一次全新回放。资格 bundle 与执行源码都来自 commit
`a28fb7d6b1f4a223bc71bc24f856a36282af9dc2`；资格期间的源码快照没有漂移。它不证明任意机器可直接
复现，也不等于 `text2env.validate@2`、MCP 或发布门已经完成。

## 固定资格 bundle

目标位置是 `self_improving/harness/qualified_skills/text2env.replay/1.0.0`。三个文档的精确身份为：

| 文件 | bytes | SHA-256 |
| --- | ---: | --- |
| `manifest.json` | 9,560 | `0bc3815ecb939b8488f4541ea4343ed5f332f5f130fcb47ea240a5a7f15a9e83` |
| `qualification.json` | 315 | `ae2b4ee1fe9304382805dc8447828758247be4a47364cae22bd9b03418b399be` |
| `report.json` | 13,535 | `0690d305a794b23b3b701379540394f998c06a34d43eb019fc05cdf521dbef53` |

`report.json` 的八项门禁全部为 `pass`：case binding、exact dependencies、event lifecycle、runtime
asset snapshot、media decode、physics validation、source stability，以及 candidate/kernel executions。
证据 closure 包含 63 个精确 artifact refs，并在发布前完成 CAS 回读与身份复核。

资格生成器对同一固定案例执行了两次真实回放：candidate-direct
`71000000-0000-4000-8000-000000000001` 和 production-kernel candidate
`71000000-0000-4000-8000-000000000002`。两次均为单 attempt、终态 `succeeded`，900 个仿真 step、
120-step contact window、120 帧视频和 100 个 source-unique frames；物理报告均为 `pass`，`fail=0`、
`not_run=0`。

源码绑定摘要为：

- Harness tree: `313d7214683c9bbb9c8bb550d154fe832e5bed255c6ca1c282fb48d4d7941f21`
- Implementation: `3ad6aff715baade429e2315d6a1f53ccc432affe57610a1a3653ef51587a0379`
- Scene-gen tree: `e2fe9fd66d917dc6dea40e8990248206e180e3a086c1619b9f40558c3efec330`
- Ledger contract tree: `1d52584d44ca5f5d759f5e72d327fed282f0bdb4adaf763387b2fda2467ef489`

## 正式 application 的全新回放

加载上述 bundle 后，正式 `ReplayApplication` 以新的持久化状态执行 run
`79f58ee8-0d22-4abf-a14e-2c65c4d8a663`。终态为 `succeeded`，单 attempt，ledger 中有 19 条事件和
43 个 artifact refs。运行时物理报告为 `pass`：29 项 `pass`、8 项 `not_applicable`、0 项 `fail`、
0 项 `not_run`；视频为 120 帧、100 个唯一帧。runtime evidence 摘要为
`711096539921bce4cabb08311af3b5216398cc332a668285cf01cf4c2f8c6589`，resolved scene 摘要为
`96e995144468707f0f6e169341ce13e2330b1f42a8acaf73d9c126925e4be3ee`。

本轮媒体复核绑定的当前 FFmpeg 是 4,337,384 bytes，SHA-256
`74f0443e5d684e051726fc39546c77b18b031395c30a5d5764b95af8e3a86780`。2026-08-31 的独立媒体验证历史
证据绑定的是另一份 FFmpeg：
`fe08d0f51873874056abe5be5eb1d1047a1cc3eb3da0c41907047be5581ef02e`。旧证据不覆盖当前 binary；本记录
保留两者的边界，不回写或扩大历史结论。

## 复现入口

资格必须从绑定的干净源码树、同一组外部工具/资产和一个可写的 delegated cgroup scope 中运行。实际入口的
locator-free 命令模板是：

```bash
PYTHONPATH=<clean-source-root> PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
  <qualified-robotwin-python> -m self_improving.harness.qualify_replay \
  --settings <external-replay-qualification-settings.json>
```

settings 文档必须位于仓库外，固定本记录中的 900/120/120/12 案例、bundle/CAS 根、当前 capability、
FFmpeg、launcher、RoboTwin 资产根和 delegated cgroup 根。生成后以
`verify_replay_qualification_bundle(...)` 对三份文档、当前源码树和外部 CAS 做只读深验证。正式 application
回放仍在同一个 scope 中，依次调用 `load_replay_qualification_bundle(...)`、用已验证的 kernel invocation
构造 `Text2EnvReplayInput`、以相同 operator locators 创建 `ReplayApplicationSettings`，最后调用
`create_replay_application(settings).replay(value)`；state root 必须是新的空目录。

## 边界

仓库只保留小型资格文档和本记录；RoboTwin 资产、SQLite 状态、CAS objects、PNG 和 MP4 等批量产物
仍在外部证据存储中。bundle 使用内容摘要标识证据，但完整解析仍依赖部署侧 CAS；operator 配置中的
解释器、工具和资产 locator，以及外部 source manifest 的 distribution/harness roots，仍是该部署的
host-absolute locator。提交的 bundle 和本记录不含这些私有绝对路径，但外部证据 closure 会保留并严格
校验它们。因此这是一份 fail-closed 的部署内资格，不是可移植安装包。

尚未完成的门包括 `text2env.validate@2` 的生产资格与 replay-to-validate 晋升、MCP 接线，以及能在新
机器上重建工具、资产和 CAS closure 的 portable install/provisioning。
