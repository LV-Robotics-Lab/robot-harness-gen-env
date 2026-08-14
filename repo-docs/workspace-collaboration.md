# 仿真组工作区协作

本页规定 Jingxiang 仿真机上 `robot-harness-gen-env` 的多人协作布局。核心原则是：个人开发只发生在个人名字目录，共享源码和经过明确提升的公共数据才进入统一 `workspace`。

## 目录与分支

```text
/home/jingxiang/
├── bingsheng/
│   └── robot-harness-gen-env/   # branch: worktree/bingsheng
├── gujie/
│   └── robot-harness-gen-env/   # branch: worktree/gujie
├── yeyuxuan/
│   └── robot-harness-gen-env/   # branch: worktree/yeyuxuan
├── hyx/
│   ├── env-gen-dev/             # HYX 自己的独立实验仓库
│   └── robot-harness-gen-env/   # branch: worktree/hyx
└── workspace/
    ├── lerobot/
    └── robot-harness-gen-env/   # shared canonical worktree, branch: main
```

四个 `robot-harness-gen-env` 个人目录都是 canonical 仓库登记的 Git worktree，并共享 Git object store；它们不是复制出来的独立 clone。个人 worktree 固定使用 `worktree/<人名>` 分支，不直接在 canonical `main` 上开发。

## 边界

- 个人源码、临时脚本、日志、实验输出、未提升的数据和 checkpoint 必须留在 `/home/jingxiang/<人名>/` 内。
- `/home/jingxiang/workspace/robot-harness-gen-env` 只用于共享 `main`、公共子模块，以及经过来源和恢复性检查后明确提升的公共材料。
- 公共 RoboTwin 运行时位于 canonical `external/RoboTwin/`；公共但不进 Git 的材料位于 canonical `local_data/` 或已约定的 ignored data 路径。个人 worktree 可以通过显式环境变量只读引用它们，不应再复制一份到个人目录。
- 不在 `/home/jingxiang/workspace/` 下新建第二个个人项目目录，也不把一个人的试验目录软链到另一个人的名字目录。
- 小型源码、配置、实测 metadata、manifest、哈希和结构化证据通过 PR/评审合入 `main`；大 payload 是否提升为公共材料必须单独核验来源、哈希和恢复位置。

## 日常流程

1. 在自己的 `/home/jingxiang/<人名>/robot-harness-gen-env` 和 `worktree/<人名>` 分支开发、提交并推送。
2. 开工前 fetch `origin/main`，按团队约定 merge 或 rebase；不要在 canonical worktree 里临时改代码。
3. 验证遵循根 `AGENTS.md`：普通改动跑 hermetic tests；support、containment、loader 或 validator 契约改动还要在真实 RoboTwin/SAPIEN 环境回放。
4. 通过 PR 或明确的集成操作进入共享 `main`。只有合入 `main` 的内容才算公共实现。
5. 移除个人 worktree 前先确认分支已推送且工作树干净，再使用 `git worktree remove`；不能把删除目录当作已备份。

Git 分支可以从组织远端恢复；ignored 数据、公共 runtime 和本地 checkpoint 不能因此自动恢复。`storage_uri: null` 或只有 manifest/hash 时，仍然只代表本地审计证据。
