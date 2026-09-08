# Golden E2E 进度账本

本目录是 2026-09-09 启动的 x2env → Genesis golden E2E 工作流单一进度事实源。它记录当前
任务、待办、稳定决策、测试 seam、实验参数和已复核结果；不替代 `docs/evidence/` 的不可变运行
证据，也不把 render 当成物理验证。

## 每次工作顺序

1. 先读 `DECISIONS.md`、`TASKS.md` 和 `TODO.md`。
2. 开始或结束一个切片时更新 `TODO.md`。
3. 新的稳定架构决定写入 `DECISIONS.md`。
4. 测试、真实运行、哈希和失败结果写入 `RESULTS.md`；正式运行证据另存带日期的
   `docs/evidence/` 页面并从这里链接。
5. 公共测试 seam 以 `SEAMS.md` 为准；未确认的 seam 不开始 TDD 功能实现。
6. 路由优化实验遵守 `AUTORESEARCH_SETUP.md`，每次尝试先提交、后测量，并保留失败记录。

## 当前阶段

`discovery_and_contract_design`

当前只允许调研、进度账本和契约设计工作。功能测试与实现须在 `SEAMS.md` 和
`AUTORESEARCH_SETUP.md` 经用户确认后开始。
