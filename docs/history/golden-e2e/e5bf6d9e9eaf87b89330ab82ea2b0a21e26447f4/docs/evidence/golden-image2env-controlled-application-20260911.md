# Golden image2env controlled application 与资产资格审计 — 2026-09-11

固定 application 实现：`40bfa213c8dace7e99de6d96980d3431f3d87735`。

## Durable controlled application

`ControlledImage2EnvCompileApplication` 使用同一个 CAS、`SQLiteEventJournal` 和 `SQLiteRunStore`。未绑定时
只能执行 unregistered candidate。测试先真实得到 succeeded candidate 的 Invocation、output 与 21-ref
closure，再把这些身份写入明确标注 `controlled-image-exact-reuse-candidate` 的 report/qualification；只有
该材料经 application 重新读取、逐一 resolve 所有 RunState artifacts、重跑 exact compile deep verifier并与
durable candidate run 对账后，caller 才能用 UUID4 执行。

资格 ArtifactRef 还必须精确为 `controlled-qualification.json`、`application/json`、
`harness.skill_qualification.v1` 和 `artifact://sha256/<digest>`。删除 CAS 对象后改用相同字节的外部
`file://` locator，或只漂移 name/media/schema，都会在绑定前拒绝。

同实例重提和删除 live application 后重建都返回同一 terminal；相同 UUID 的 seed 漂移在 handler 前拒绝，
只有 intent、没有 Invocation/terminal 的 durable 状态按 pending 拒绝，SQLite events 与 CAS 不增长。十一项
application 测试通过；新模块 166 statements / 46 branches 全覆盖。该 controlled qualification 不进入
production assembly、Registry snapshot 或 MCP。

两次独立验收分别得到 31 与 40 项 focused pass。正向证据为 23 个 CAS 对象、1 intent、2 invocations、
2 run states、6 events；销毁 live application 后重绑和同 UUID 重提均零增长。完整性轴逐项篡改 21/21
candidate closure artifacts，并覆盖 qualification/report/descriptor/SQLite 与 locator/metadata 漂移，全部
fail closed。独立正向摘要 SHA-256 为
`67c86a2e58fba74ecb19831a47e5c59347546d6354461dea2d21445b676e0a2a`。

固定 `40bfa213...` 的根套件为 4,119 passed / 20 skipped / 1 failed；唯一失败是该项目 `.venv`
未安装 `pip`，packaging 测试无法启动 wheel build。相同 packaging 文件用有 `pip` 的系统 Python 独立运行
为 2 passed，因此这是验收环境缺依赖，不是源码或打包内容失败。

## Gujie legacy evidence 审计

任务账本指定的 exact pass 路径
`/home/jingxiang/gujie/gen-env/output/鼠标_原始平面_物理与渲染` 当前不存在。最接近的历史归档是
`data/genesis_history/output_before_retest_20260908_011810/场景图物理_mouse_001`，但它不能替代指定输出。

只读检查确认其 479/479 manifest members、15-member mouse URDF package 与 CPU static inspect 通过；鼠标是
单 rigid link、无 joint、8 collision parts、1 visual、质量 0.07 kg。可是 TaskOutput 与 workflow verifier
分别因失效绝对目录和 source locator 拒绝，baseline/half-dt evidence 均因当前 threshold contract 拒绝。
更关键的是，旧 workflow 记录的 7 个 runtime module SHA 与固定 Gujie commit `eb0b710...` 为 0/7 匹配，
在其可达 Git 历史中也找不到能闭合这组实现身份的提交。

因此旧资产字节最多只能迁为待资格化 `AssetRepresentationV1` candidate。旧 `physics_status=passed`、截图、
MP4、scene contact/support、退出码或文件名中的 Genesis 都不能签发当前 `GenesisRuntimeLockV1` 或
`GenesisAssetQualificationV1`。

## 下一真实门

必须在当前固定 implementation 上把 15 个鼠标成员写入 fresh CAS，重建并核 URDF/MTL 闭包；随后由新的
sealed Genesis issuer 执行 asset-scoped dynamic load、collision-enabled 正步数 contact/collision probe，
发布 typed raw trace/report，并在第二绝对路径与 fresh CAS strict reload。只有这条门通过后，才能把资产放入
production catalog、签 Skill qualification 并让 MCP 暴露 image compile。
