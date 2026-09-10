# Text2Env replay qualification status — 2026-09-10

本页记录当前源码相对 2026-09-02 历史资格的状态变化，不修改或扩大
[`replay-production-qualification-20260902.md`](replay-production-qualification-20260902.md)
中的历史运行结论。

## 当前状态

- 仓库仍保存 2026-09-02 固定源码下生成的 `text2env.replay@1.0.0` 三份资格文档；该次
  candidate-direct、Registry candidate 与后续 application 900/120 运行仍只属于其原固定源码。
- 当前实现 identity 已变化。严格 loader 对旧 bundle 返回
  `implementation_file_mismatch: self_improving/harness/__init__.py`，因此旧 bundle 不能授权当前
  replay application。
- 本轮没有修改 `qualified_skills/text2env.replay/1.0.0/`、expected hash 或签名，也没有把旧资格标为
  current。
- P2 还会修改 replay closure 内的 `registry.py` 与 `application.py`。新的真实 900/120 执行、完整
  evidence closure、严格 reload 与重签必须在这些实现固定后统一进行；若运行依赖不可用，继续保持
  fail closed。

## 本轮实现与测试证据

replay implementation identity 已收窄为显式 Text2Env compile/replay 执行闭包。稳定 package seam、
production handler/application/Registry、运行时与媒体边界、14 份 replay 绑定 schema snapshot，以及
顶层 `self_improving/registry.py` 都在闭包内；可扩展的全局 facade/schema catalog 不因新增无关 family
改变 replay@1 identity。14 个 schema 的模型和 snapshot 路径由同一个 frozen binding 表派生，并在
qualification 生成及 loader 解析前逐份 exact-byte 对账。

源码快照从绝对 distribution root 开始逐组件 nofollow 打开目录和成员。每轮用单一文件 FD 流式计算
bytes/SHA-256，并比较目录、文件的 device/inode/mode 与文件 link-count/size/mtime/ctime；三轮均从新
root FD 重走完整目录链和成员，最后再次复核 root identity。负向用例覆盖第二轮最后成员打开后替换
`self_improving/` 或 `harness/schemas/` 的精确时序。

fresh-process 测试实际调用 `_execute_fixed_case`，让真实 compile application、compile handler、replay
handler、dependency resolver、direct candidate 与 Registry candidate 执行；只在外部 native sandbox、
媒体 decoder 和 RoboTwin runtime 边界使用确定性测试实现。该测试确认执行期加载的第一方 Python 源要么
属于显式 implementation closure，要么属于由 `ledger_contract_tree_sha256` 独立绑定的 ledger contract
tree。它不是部署仿真或新的物理资格证据。

验证结果以本轮进度台账为准。完整 Harness 与根套件都只有旧 replay bundle 的上述预期拒绝；compile
qualification、29 份 committed schema snapshot、Ruff、diff hygiene 和变更模块 statement/branch
coverage 门通过。

## 能力边界

本状态页不证明当前源码已取得 replay qualification，不证明 Genesis、RoboTwin/SAPIEN 或真机部署可用，
也不把 compile success、可解码媒体或单元测试替代物理 validation、`publishable=true` 或 promotion。
