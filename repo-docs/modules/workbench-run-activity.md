# Workbench 已提交运行活动板

Workbench 的活动板是现有 Harness event feed 的只读 DOM 投影。它没有新的后端读取 seam；
`app.js` 先通过 `validateHarnessPage` 核对当前 configured feed，再把已接受的 event envelope 交给：

```text
HarnessRunBoard.mount({root, onInspect})
  -> render({events, selectedRunId})
  -> compile | replay | validate | other
```

默认全局视图只保留最近 200 条 committed events。板块按 run ID 聚合，每张卡片显示固定的
Skill identity、最新 stage/status、当前可见事件数与全局 event ID；精确匹配
`text2env.compile`、`text2env.replay`、`text2env.validate` 的 run 分别进入前三栏，其他 Skill 进入
`other`。

## 点卡片后发生什么

`onInspect` 复用既有 `selectHarnessRun`。它把 run filter 写入 URL 和输入框，清空当前 cursor 与
events，再从 cursor `0` 重读该 run 的 committed journal。这里的 replay 是读取历史，不会调用
`text2env.replay`。筛选后的 run feed 替换全局 feed；板块没有把两个 state root 合并，也不保留一份
旁路历史。

卡片节点以 run ID 为 key：别的 run 收到新事件时，当前卡片不会被无谓替换，因此键盘焦点可以
跨渲染保留。用户已经 blur 后，渲染不会主动把焦点抢回。

## 完整性边界

同一 run 的 `skill_id` 与 `skill_version` 必须在可见历史内稳定；任一漂移都会产生
`harness_event_page_invalid`，由现有 feed 错误路径清空 cursor、events 与缓存。这条检查补在
`validateHarnessPage` 的逐 envelope 结构校验之后，板块不会用最后一条事件静默改写 run 身份。

活动板只从已校验 Event 投影 stage、status、event ID 等运行元数据，不展示完整 Event、未投影字段、
artifact refs 或 artifact 内容。它不执行 replay/validate Skill，不产生物理 validation 结论，也不判断
发布或 `publishable`。实现文件摘要、342 项 demo 回归、65 项浏览器回归、5 个定向 Chrome cases 与静态门见
[`docs/evidence/workbench-run-activity-20260902.md`](../../docs/evidence/workbench-run-activity-20260902.md)。

证据状态：2026-09-02 已由 committed journal 浏览器路径验证；活动板是只读投影，不是执行面。
