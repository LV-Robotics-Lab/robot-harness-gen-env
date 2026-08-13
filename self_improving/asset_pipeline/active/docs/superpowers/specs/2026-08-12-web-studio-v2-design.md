# Pipeline Studio v2 — web 前端重设计 spec

日期：2026-08-12（通宵实施）
授权：用户已确认方案 1（前端重写 + app.py 增量接口）+ 横向 stepper 布局，并授权通宵自主迭代。
边界：只改 `web/`；pipeline/上游代码只读适配；`results/web_runs/` 视为数据可写（回放器测试用）。

## 目标

用户提交一个 prompt 后，能**实时、明确**地看到：
1. 生成进行到 7 步中的哪一步、每步耗时多少；
2. 每一步的输出（表格/图/视频/JSON），运行中可随时翻看且不被刷新打断；
3. 结束后一眼看清结果（成功/缺口阻塞/失败）与产物。

## 现状硬伤（审查结论，按严重度）

1. 运行中每 1.5s 全页 `innerHTML` 重绘：展开态/滚动位置全丢，运行期间无法阅读任何输出。
2. 阶段状态靠产物存在性+日志尾 100 行关键词猜测；`coverage_report.json` 在缺口引进结束后才落盘，带引进的 run 第①步长期显示未完成（误导）。`acquire_categories.json` 这个早期信号完全没被利用。
3. 零时间信息：无开始时间/已运行时长/每阶段耗时。
4. 日志只有尾 100 行、折叠页底、无跟随/过滤；渲染阶段 OIDN 错误刷屏淹没 PASS/FAIL。
5. 渲染图 110px 无标注（观察序列/分割图/世界视角不可分辨）；`runtime_validation_report.json`、`scene_spec.json`、`generated_scene.py` 等产物不展示。
6. 详情页无 run 头信息（prompt/seed/run_id 不显示）。
7. 跳过阶段占整卡且文案矛盾；侧栏无时间/无置顶；表单无标签；tab 标题不反映状态。

## 后端设计（app.py 只增不改）

### 1. `/api/run/<group>/<id>/status` 新增字段（现有字段全部保留）

```json
"stage_timeline": [
  {"n": 1, "key": "coverage", "status": "done|active|pending|skipped|failed|blocked",
   "ended_at": "iso", "duration_s": 12.3, "detail": "可选一句话"}
  , ... 7 项
],
"log_size": 123456,
"server_now": "iso"
```

服务端统一计算，历史 run 也能得到耗时（产物 mtime 差）。信号规则：

| 阶段 | done 信号 | 跳过条件 | active 信号（运行中） |
|---|---|---|---|
| ①覆盖检查 | `acquire_categories.json` 或 `coverage_report.json` 或 evidence 存在（取最早 mtime 为 ended_at） | 从不 | run 开始且无任何信号 |
| ②缺口引进 | `coverage_report.json` 存在（acquire 完才写）或 evidence 全类别 terminal | 无 `acquire_categories.json` 且 coverage 全 covered | `acquire_categories.json` 存在且未 done |
| ③下载+转换 | shots 存在 | 同②跳过 | 日志 `simulation app startup`/`app ready` |
| ④SAPIEN 质检 | shots 存在 | 同②跳过 | 日志 `accepted `/`rejected ` |
| ⑤catalog 重建 | 日志 `PASS s9` 或 meta.exclude_category | 未触发 | — |
| ⑥场景生成 | `scenes/*/resolved_scene.json` | blocker 存在 → blocked | coverage 已写且无 scene |
| ⑦回放渲染 | render_rc==0 或 runtime 有图/视频 | blocker 或无 scene | state.phase=="render" |

- 日志标记解析读**全文** run.log（服务端本地读，成本可忽略），active 取最后出现的标记。
- outcome==failed 时，把当时 active 的阶段标 failed。
- 时间戳来源：run_meta.started_at、各信号文件 mtime、state.finished_at；缺失则省略 duration。

### 2. 新增 `GET /api/run/<group>/<id>/log?offset=N`

返回 `{"offset": N, "size": S, "chunk": "...", "more": bool}`；chunk 为字节 [N, N+256KB) 的 utf-8（errors=replace）文本。前端增量追加。

### 3. 新增 `GET /api/run/<group>/<id>/files`

`{"files": [{"p": "runtime/observer_end.png", "size": 123, "mtime": 1723...}, ...]}`
递归列 run 目录，白名单后缀（现有集合 + `.py`），上限 500 条。`/file` 接口的白名单同步加 `.py`（text/plain）。

### 4. `/api/runs` 新增顶层 `current`（当前运行的 run_id 或 null）

侧栏用于置顶 + spinner + 禁用提交按钮。

## 前端设计（index.html 重写，零依赖单文件）

布局（自上而下）：

1. **Header + 表单**：标题一行；表单带可见标签（提示词 / seed / 演示缺口）、运行按钮有忙碌与禁用态（有 run 在跑时禁用并提示）。
2. **左侧栏**：本次会话 / 历史两组；条目 = prompt + 相对时间 + 状态徽章；运行中条目置顶带 spinner。
3. **Run 头卡**：prompt 大字、seed/演示缺口/run_id、开始时间（新加坡时）、总耗时（运行中为每秒跳动的已运行时长，本地推进 + server_now 校准）、结果徽章。
4. **横向 stepper**：7 步，图标 ✔/⟳/⏭/✖/－ + 短名 + 耗时；点击选中看详情；运行中默认跟随活动步，手动点选后停跟随（提供「跟随」开关恢复）；blocked 状态用 ⛔。
5. **阶段详情面板**（只渲染选中步）：
   - ① 覆盖表格（object/category/status/asset/score）
   - ② 按类别卡片：status、tiers、attempts、候选 chips（选中绿/拒绝红+原因码，hover 全 id）
   - ③ 状态说明 + 指向日志锚点
   - ④ QC 截图廊（文件名标注 + lightbox）
   - ⑤ 重建信息（exclude_category/catalog 路径或 PASS s9）
   - ⑥ 对象表 + validation 徽章 + scene 文件快捷链接（scene_spec/resolved_scene/generated_scene.py → 文件预览）
   - ⑦ 渲染图按视角分组标注（观察 start/mid/end、俯视 preview_head、分割 segmentation、世界左右）、视频大尺寸、runtime_validation_report 摘要徽章
   - 每步保留「原始 JSON」折叠。
6. **产物文件面板**（折叠）：文件树；json/py/txt/log 弹层预览，图片 lightbox（←/→ 切换），视频播放，均可另开原始链接。
7. **日志面板**（常驻底部可折叠）：offset 增量追加；「跟随」开关；FAIL/ERROR/Traceback 行红、PASS 行绿；连续重复行折叠 `×N`；`=== stage: X ===` 分隔线可点击从 stepper 跳入。

### 实时机制

- 轮询：运行中 status 1.5s 一次，结束后停；log 面板展开时随轮询取增量。
- **分区增量更新**：每区域（头卡/stepper/当前阶段面板/侧栏）对源数据 JSON.stringify 签名比对，变化才重绘该区域；日志只追加。展开态与滚动位置永不丢失。
- tab 标题：运行中 `⏳ <活动阶段短名> · Studio`，完成 `✅/⛔/❌ Studio`。
- 计时：本地 setInterval 1s 推进已运行时长，以 server_now-started_at 校准。

### 视觉

- 沿用现有色彩 token（绿 accent、light/dark 双主题），加大留白与层级；中文 UI。
- 跳过阶段在 stepper 中弱化显示，不再占详情空间（选中时一句话说明）。

## 不做（YAGNI）

构建栈/框架、SSE、多 run 队列、鉴权、移动端专门适配（保持基本 responsive 即可）。

## 测试计划

1. app.py 新接口 curl 冒烟（真实 run + 历史 run + 边界：不存在 id、offset 越界）。
2. 回放器（scratchpad 脚本，仅写 `results/web_runs/_sim_*` 数据）重放历史产物+日志，验证：stepper 状态迁移、耗时、日志追加、跟随、展开态保持。
3. 浏览器提交 1-2 条真实普通 prompt 端到端验证；抽查 2 条历史 run。
4. 自主截图审阅循环：桌面 1440px + 窄窗口、light/dark 双主题截图，发现设计问题即改即验。
5. 兼容：旧字段不删；`/api/runs`、`/status`、`/file` 现有消费方（新前端）行为一致。

## 交付

- 分支 `feat/web-studio-v2`（远程 lv-5090 repo），逐步提交，推送备份，不动 main。
- 值夜汇报 `work/nightwatch/20260812/report.md` + proactive 推送。
