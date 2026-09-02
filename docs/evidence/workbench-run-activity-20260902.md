# Workbench committed run activity board evidence — 2026-09-02

配套结构化报告为
[`workbench-run-activity-20260902.json`](workbench-run-activity-20260902.json)，文件 SHA-256 为
`55124fa1c33ef6cf7976ed1b13daf87e58ac11d4d1d58986237c52255e8a02a9`。

## 已验证表面

公开 seam 为
`HarnessRunBoard.mount({root,onInspect}) -> {render({events,selectedRunId}),clear()}`。板块自身不读
后端，只投影 Workbench 已经通过 `validateHarnessPage` 的当前 configured feed。默认全局视图最多
保留最近 200 条 committed event envelope，再按 run 聚合到 `compile`、`replay`、`validate`、
`other` 四栏；前三栏匹配精确 Skill ID，其余 Skill 进入 `other`。

卡片点击只调用既有 run filter：当前 cursor 与事件状态清零后，从 cursor `0` 重读该 run 的
committed journal。这是只读 journal replay，不会执行 `text2env.replay`。全局 feed 与筛选后的 run
feed 互相替换，不合并成一个 state root。

卡片 DOM 以 run ID 为 key。重渲染保留仍在板内的焦点节点；用户主动 blur 后，后续渲染不会抢回
焦点。同一 run 的 `skill_id` 或 `skill_version` 在可见历史中漂移时，板块抛出
`harness_event_page_invalid`，外层 feed 路径清空状态并 fail closed。

## 复现

核对本记录绑定的五个文件：

```bash
sha256sum \
  demo/static/workbench_run_board.js \
  demo/static/app.js \
  demo/static/index.html \
  demo/static/styles.css \
  tests/demo/test_workbench_browser.py
wc -c \
  demo/static/workbench_run_board.js \
  demo/static/app.js \
  demo/static/index.html \
  demo/static/styles.css \
  tests/demo/test_workbench_browser.py
```

完整 demo 与浏览器套件：

```bash
pytest -q tests/demo
pytest -q tests/demo/test_workbench_browser.py
```

预期分别为 342 passed 与 65 passed。五个定向 Chrome cases：

```bash
pytest -q \
  tests/demo/test_workbench_browser.py::test_browser_groups_committed_compile_replay_and_validate_run_activity \
  tests/demo/test_workbench_browser.py::test_browser_run_activity_card_selects_and_replays_that_committed_run \
  tests/demo/test_workbench_browser.py::test_browser_run_activity_preserves_focus_without_stealing_it_after_blur \
  tests/demo/test_workbench_browser.py::test_browser_run_activity_fails_closed_when_one_run_changes_skill_identity \
  tests/demo/test_workbench_browser.py::test_browser_revalidates_cached_events_before_rendering_them
```

预期为 5 passed。静态门：

```bash
node --check demo/static/workbench_run_board.js
node --check demo/static/app.js
ruff check tests/demo/test_workbench_browser.py
ruff format --check tests/demo/test_workbench_browser.py
git diff --check
```

两项 Node syntax check、Ruff check/format 与 diff check 均通过。精确文件 bytes 与 SHA-256 见配套
JSON。

## 主张边界

活动板只从已校验 Event 投影 run/Skill、最新 stage/status、可见事件数与 event ID；不展示完整
Event、未投影字段、artifact refs 或 artifact 内容。`validateHarnessPage` 不是
`text2env.validate` 执行。此切片不执行 replay 或 validate Skill，不作物理 validation 判断，也不作
发布或 `publishable` 判断。
