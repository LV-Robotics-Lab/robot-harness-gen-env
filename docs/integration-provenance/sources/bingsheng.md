# Bingsheng 来源档案

## 责任边界

Bingsheng 主要负责 Harness 系统、统一合同、证据链、Codex/MCP 编排以及所有来源代码的最终整合。
“最终整合责任”不等于对 Gujie、Yuxin 或第三方实现主张原始作者身份。

## 当前固定锚点

- 仓库：当前 `robot-harness-gen-env`
- 分支：`worktree/bingsheng`
- 本台账建立前提交：`5ac910897542bf78f9f0ed9e269bcf600347abd2`
- Golden E2E 合同/边界提交：`ece6c25`、`55f9ff2`、`2bd5e71`（短 hash）
- 历史说明：`self_improving/contributor_notes/bingsheng-phase2-guidance.md`

## 核对方式

- Harness 原生代码按本仓库 git history 核对具体作者与评审；
- 从其他分支或仓库移植的能力必须另建 `INT-*` 条目；
- Bingsheng 新增的 adapter、schema、receipt、测试和文档与原来源分别记录；
- 共享 dirty worktree 不是稳定来源，只有 commit 或显式内容 manifest 才能进入最终核对。
