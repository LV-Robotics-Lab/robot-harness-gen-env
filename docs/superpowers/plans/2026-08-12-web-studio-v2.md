# Pipeline Studio v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重写 web/ 前端为实时可读的 7 阶段监控台：服务端统一计算阶段时间轴，前端分区增量渲染 + 增量日志。

**Architecture:** Flask 单文件 app.py 只增不改（新增 stage_timeline 计算、/log 增量、/files 清单、runs.current）；index.html 整体重写为零依赖单文件（横向 stepper + 阶段详情面板 + 常驻日志），运行中 1.5s 轮询、按区域签名比对增量更新 DOM。

**Tech Stack:** Python 3.10 / Flask 3.1 / pytest 8.4（远程 conda env `env-gen-yuxin`）；前端 vanilla HTML/CSS/JS 零依赖。

## Global Constraints

- 只改 `web/`（含新建 `web/tests/`）；pipeline/上游代码只读。
- `results/web_runs/` 可写数据（测试回放器目录一律 `_sim_` 前缀）。
- app.py 现有接口字段全部保留，行为不变（`.py` 加入 `/file` 白名单为唯一扩展）。
- 分支 `feat/web-studio-v2`；每任务完成即 commit（消息中文，带 Co-Authored-By/Claude-Session 尾注）。
- 部署 = `pkill -f web/app.py` 后 `nohup bash web/serve.sh > /tmp/pipeline_studio.log 2>&1 &`；页面 http://100.64.0.6:8811。
- 执行环境：代码在 lv-5090，编辑经 scp/ssh；pytest 用 `/home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python -m pytest`。

---

### Task 1: 服务端 stage_timeline（含 log_size/server_now/runs.current）

**Files:**
- Modify: `web/app.py`（`_read_evidence` 之后新增 timeline 段；`api_run_status`、`api_runs` 各加字段）
- Test: `web/tests/test_app.py`（新建，含 conftest 式 fixture）

**Interfaces:**
- Produces: `compute_stage_timeline(run_dir: Path, meta: dict, state: dict, log_text: str) -> list[dict]`，每项 `{"n": int, "key": str, "title": str, "status": "pending|active|done|skipped|failed|blocked", "ended_at": float|None, "duration_s": float|None, "detail": str|None}`；status 响应新增 `stage_timeline`（上述 list）、`log_size: int`、`server_now: float`；`/api/runs` 响应新增顶层 `"current": str|None`。

- [ ] **Step 1: 写失败测试**（fixture 直接造 run 目录文件；monkeypatch `app.GROUP_ROOTS`）

```python
# web/tests/test_app.py
import importlib.util, json, time
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location(
    "studio_app", Path(__file__).resolve().parents[1] / "app.py")
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    web = tmp_path / "web_runs"; hist = tmp_path / "hist"
    web.mkdir(); hist.mkdir()
    monkeypatch.setattr(studio, "WEB_RUNS", web)
    monkeypatch.setattr(studio, "HISTORY_ROOT", hist)
    monkeypatch.setattr(studio, "GROUP_ROOTS", {"web": web, "history": hist})
    return web, hist


def make_covered_run(root):
    d = root / "20260812_000000_demo"; d.mkdir()
    (d / "run_meta.json").write_text(json.dumps(
        {"prompt": "put a duck on the table", "seed": 42,
         "started_at": "2026-08-12T00:00:00+08:00"}))
    (d / "run_state.json").write_text(json.dumps(
        {"phase": "done", "outcome": "scene", "pipeline_rc": 0, "render_rc": 0,
         "finished_at": "2026-08-12T00:03:00+08:00"}))
    (d / "coverage_report.json").write_text(json.dumps(
        {"objects": [{"object_id": "duck_1", "category": "duck", "status": "covered"}]}))
    sc = d / "scenes" / "x" ; sc.mkdir(parents=True)
    (sc / "resolved_scene.json").write_text("{}")
    rt = d / "runtime"; rt.mkdir()
    (rt / "observer_end.png").write_bytes(b"x")
    (d / "run.log").write_text("=== stage: pipeline ===\nPASS scene_acquire scene=x\n=== stage: render ===\nPASS scene=x fail=0\n")
    return d


def timeline_of(d):
    meta = json.loads((d / "run_meta.json").read_text()) if (d / "run_meta.json").exists() else {}
    state = json.loads((d / "run_state.json").read_text()) if (d / "run_state.json").exists() else {}
    log = (d / "run.log").read_text() if (d / "run.log").exists() else ""
    return studio.compute_stage_timeline(d, meta, state, log)


def test_covered_run_timeline(roots):
    web, _ = roots
    tl = timeline_of(make_covered_run(web))
    assert [s["status"] for s in tl] == [
        "done", "skipped", "skipped", "skipped", "skipped", "done", "done"]
    assert all(s["duration_s"] is None or s["duration_s"] >= 0 for s in tl)


def test_gap_run_live_active_stage2(roots):
    web, _ = roots
    d = web / "20260812_000001_gap"; d.mkdir()
    (d / "run_meta.json").write_text(json.dumps(
        {"prompt": "x", "seed": 1, "started_at": "2026-08-12T00:00:01+08:00"}))
    (d / "run_state.json").write_text(json.dumps({"phase": "pipeline", "outcome": None}))
    (d / "acquire_categories.json").write_text("[]")
    tl = timeline_of(d)
    assert tl[0]["status"] == "done"
    assert tl[1]["status"] == "active"


def test_blocker_run(roots):
    web, _ = roots
    d = web / "20260812_000002_blk"; d.mkdir()
    (d / "run_state.json").write_text(json.dumps(
        {"phase": "done", "outcome": "blocker", "pipeline_rc": 1}))
    (d / "coverage_report.json").write_text(json.dumps({"objects": []}))
    (d / "asset_gap_blocker.json").write_text("{}")
    tl = timeline_of(d)
    assert tl[5]["status"] == "blocked"
    assert tl[6]["status"] == "skipped"


def test_status_endpoint_new_fields(roots):
    web, _ = roots
    make_covered_run(web)
    c = studio.app.test_client()
    r = c.get("/api/run/web/20260812_000000_demo/status").get_json()
    assert len(r["stage_timeline"]) == 7
    assert r["log_size"] > 0
    assert isinstance(r["server_now"], float)


def test_runs_current_field(roots):
    c = studio.app.test_client()
    assert "current" in c.get("/api/runs").get_json()
```

- [ ] **Step 2: 跑测试确认失败**：`cd /home/jingxiang/yuxin/env-gen-dev && /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python -m pytest web/tests/ -v`，预期 `AttributeError: compute_stage_timeline`。

- [ ] **Step 3: 实现**（app.py 新增；核心逻辑）

```python
STAGE_DEFS = [
    (1, "coverage", "覆盖检查"), (2, "gap", "缺口引进"), (3, "convert", "下载转换"),
    (4, "qc", "物理质检"), (5, "rebuild", "catalog 重建"), (6, "scene", "场景生成"),
    (7, "render", "回放渲染"),
]

def _mtime(p):
    try: return p.stat().st_mtime
    except OSError: return None

def _parse_iso(s):
    try: return datetime.fromisoformat(s).timestamp()
    except Exception: return None

LOG_MARKERS = [  # (needle, stage)，后出现者优先
    ("simulation app startup", 3), ("app ready", 3),
    ("accepted ", 4), ("rejected ", 4), ("pass s9", 5),
    ("=== stage: render ===", 7),
]

def compute_stage_timeline(run_dir, meta, state, log_text):
    acq_cat = run_dir / "acquire_categories.json"
    coverage = run_dir / "coverage_report.json"
    evidence = run_dir / "acquire" / "selection_evidence.json"
    if not evidence.exists(): evidence = run_dir / "selection_evidence.json"
    blocker = run_dir / "asset_gap_blocker.json"
    scenes = sorted(glob.glob(str(run_dir / "scenes" / "*" / "resolved_scene.json")))
    shots = []
    for sub in ("acquire/shots", "shots"):
        if (run_dir / sub).is_dir(): shots += sorted((run_dir / sub).glob("*.png"))
    runtime_files = [p for p in (run_dir / "runtime").rglob("*")
                     if p.is_file()] if (run_dir / "runtime").is_dir() else []
    gap_ran = acq_cat.exists() or evidence.exists()
    phase = state.get("phase"); live = phase in ("pipeline", "render")
    # 每阶段 (status, ended_at, detail)，先按产物判 done/skip …
    # ①: acq_cat/coverage/evidence 最早 mtime；②: gap_ran 时 evidence→coverage mtime，否则 skipped
    # ③④: gap_ran 且 shots → done(首/末 shot mtime)，非 gap_ran → skipped
    # ⑤: "pass s9" in log.lower() 或 meta.exclude_category → done，否则 skipped
    # ⑥: scenes → done；blocker → blocked；⑦: runtime 有 png/mp4 或 render_rc==0 → done，blocker/无 scene → skipped
    # live 时：phase=="render" → ⑦ active；否则第一个 pending 为 active，再用 LOG_MARKERS 最后命中修正
    # outcome=="failed" 时第一个非 done/skip 阶段标 failed(detail=rc)
    # duration_s = ended_at - 前一个有 ended_at 的阶段（或 run start），负值置 None
```

（完整实现按上述规则写直；t0 = `_parse_iso(meta.get("started_at"))`，缺则取 run_dir 下最早 mtime。）

`api_run_status` 末尾补：

```python
    log_text_full = ""
    if log_path.exists():
        try: log_text_full = log_path.read_text(errors="replace")
        except Exception: pass
    payload["stage_timeline"] = compute_stage_timeline(
        run_dir, payload["meta"] or {}, payload["state"] or {}, log_text_full)
    payload["log_size"] = log_path.stat().st_size if log_path.exists() else 0
    payload["server_now"] = now_sgt().timestamp()
```

`api_runs` 返回前补 `"current": CURRENT["run_id"]`。

- [ ] **Step 4: 跑测试全绿**；同时跑真实数据冒烟：`curl -s localhost:8811/api/run/web/20260811_234435_put_a_duck_on_the_table/status | python -m json.tool | head -60`（重启服务后）验证 7 项 timeline、耗时合理。
- [ ] **Step 5: Commit** `feat(web): 服务端 stage_timeline + log_size + runs.current`

### Task 2: /log 增量接口

**Files:** Modify `web/app.py`；Test `web/tests/test_app.py` 追加。

**Interfaces:**
- Produces: `GET /api/run/<group>/<id>/log?offset=N` → `{"offset": int, "size": int, "chunk": str, "more": bool}`；chunk 为字节 [offset, offset+262144) 的 utf-8 (errors=replace)；无 run.log → size=0 空 chunk；offset 越界钳制到 size。

- [ ] **Step 1: 失败测试**

```python
def test_log_incremental(roots):
    web, _ = roots
    d = make_covered_run(web)
    c = studio.app.test_client()
    full = (d / "run.log").read_bytes()
    r0 = c.get("/api/run/web/20260812_000000_demo/log?offset=0").get_json()
    assert r0["chunk"].encode() == full and r0["size"] == len(full) and not r0["more"]
    r1 = c.get(f"/api/run/web/20260812_000000_demo/log?offset={len(full)-5}").get_json()
    assert r1["chunk"].encode() == full[-5:]
    r2 = c.get("/api/run/web/20260812_000000_demo/log?offset=999999").get_json()
    assert r2["chunk"] == "" and not r2["more"]
```

- [ ] **Step 2: 确认失败（404）** → **Step 3: 实现**

```python
LOG_CHUNK = 256 * 1024

@app.get("/api/run/<group>/<run_id>/log")
def api_run_log(group, run_id):
    run_dir = resolve_run_dir(group, run_id)
    p = run_dir / "run.log"
    if not p.is_file():
        return jsonify({"offset": 0, "size": 0, "chunk": "", "more": False})
    size = p.stat().st_size
    try: offset = max(0, min(int(request.args.get("offset", 0)), size))
    except ValueError: offset = 0
    with open(p, "rb") as f:
        f.seek(offset); data = f.read(LOG_CHUNK)
    return jsonify({"offset": offset, "size": size,
                    "chunk": data.decode("utf-8", errors="replace"),
                    "more": offset + len(data) < size})
```

- [ ] **Step 4: 测试全绿** → **Step 5: Commit** `feat(web): /log 增量读取接口`

### Task 3: /files 清单 + .py 白名单

**Files:** Modify `web/app.py`；Test 追加。

**Interfaces:**
- Produces: `GET /api/run/<group>/<id>/files` → `{"files": [{"p": str相对路径, "size": int, "mtime": float}, ...]}`，白名单后缀，按路径排序，上限 500；`ALLOWED_FILE_SUFFIXES`/`MIME_BY_SUFFIX` 增加 `.py: text/plain`。

- [ ] **Step 1: 失败测试**

```python
def test_files_listing_and_py_whitelist(roots):
    web, _ = roots
    d = make_covered_run(web)
    (d / "scenes" / "x" / "generated_scene.py").write_text("print('hi')")
    (d / "evil.exe").write_bytes(b"no")
    c = studio.app.test_client()
    files = c.get("/api/run/web/20260812_000000_demo/files").get_json()["files"]
    paths = [f["p"] for f in files]
    assert "scenes/x/generated_scene.py" in paths and "evil.exe" not in paths
    assert c.get("/api/run/web/20260812_000000_demo/file?p=scenes/x/generated_scene.py").status_code == 200
```

- [ ] **Step 2→4: 失败→实现→全绿**（`rglob("*")` 过滤 `is_file` + 后缀白名单，`sorted`，`[:500]`）
- [ ] **Step 5: Commit** `feat(web): /files 产物清单接口 + .py 白名单`

### Task 4: index.html 重写 — 骨架 + 分区增量渲染 + 轮询

**Files:** 重写 `web/index.html`（旧文件先 `git mv web/index.html web/index_v1.html.bak` 保留一版回退，收尾任务删）。

**Interfaces:**
- Produces（后续任务依赖的全局）：状态对象 `S = {group, id, status, files, stageSel, followStage: true, logFollow: true, logOffset: 0, gen: 0}`；`setRegion(el, sig, htmlFn)`（签名比对，未变不渲染）；`pollStatus()`（1.5s 自调度，phase done 停）；`fmtDur(s)`、`fmtTime(epoch)`、`relTime(epoch)`、`escapeHtml(s)`、`fileUrl(p)`；DOM 区域 id：`#runHeader #stepper #stagePanel #filesPanel #logBody #webList #historyList`。
- Consumes: Task1-3 的接口字段。

- [ ] **Step 1: 布局骨架**：header（表单带 label：提示词/seed/演示缺口 + 运行按钮）、左侧栏、主区四段（runHeader/stepper/stagePanel/filesPanel）、底部日志面板。CSS 沿用现有 token 变量（light/dark 双主题原样保留）。
- [ ] **Step 2: 核心渲染机制**

```js
function setRegion(el, sigObj, htmlFn) {
  const sig = JSON.stringify(sigObj);
  if (el.dataset.sig === sig) return false;
  el.dataset.sig = sig; el.innerHTML = htmlFn(); return true;
}
async function pollStatus() {
  const gen = S.gen;
  const st = await api(`/api/run/${S.group}/${S.id}/status`);
  if (gen !== S.gen) return;               // 已切换 run，丢弃
  S.status = st;
  renderRunHeader(); renderStepper(); renderStagePanel(); renderTabTitle();
  await fetchLogDelta();
  const live = st.state && (st.state.phase === "pipeline" || st.state.phase === "render");
  if (live) setTimeout(() => { if (gen === S.gen) pollStatus(); }, 1500);
}
```

- [ ] **Step 3: 验证**：部署后浏览器打开，选一条历史 run，devtools `evaluate_script` 断言 `document.querySelectorAll('#stepper .step').length === 7`；连续两次 poll 后展开的 `<details>` 保持展开（手动 toggle 后触发 poll 验证 dataset.sig 短路）。
- [ ] **Step 4: Commit** `feat(web): v2 骨架 — 分区增量渲染 + 轮询`

### Task 5: Run 头卡 + 横向 stepper + 跟随

- [ ] 头卡：prompt 大字 + seed/exclude/run_id/开始时间 + 徽章（成功/阻塞/失败/运行中）+ 总耗时；运行中本地 1s 计时（`server_now - started_at` 校准，`renderRunHeader` 内单独 span 更新不整卡重绘）。
- [ ] stepper：7 步横排（`overflow-x:auto`），每步 = 状态图标（done✔ active⟳(脉冲) skipped⏭ failed✖ blocked⛔ pending○）+ 短名 + 耗时小字；点击 `S.stageSel = n; S.followStage = false`；`S.followStage && active` 时自动选中 active 步；「跟随」小开关恢复。
- [ ] 验证：历史成功 run（duck）应显示 ①✔12s ②⏭ … ⑥✔ ⑦✔ 且默认选中 ⑦（完成态默认选最后一个非 skip 阶段）；blocker run（`20260803_scene_blocker_final`）显示 ⑥⛔。
- [ ] Commit `feat(web): run 头卡 + 横向 stepper`

### Task 6: 七个阶段详情面板

- [ ] `renderStagePanel()` 按 `S.stageSel` 分发到 `stageBody1..7(st)`：
  - ①覆盖表格（object_id/category/status/asset_id/score，status 上色）
  - ②类别卡：status/tiers/attempts + 候选 chips（selected 绿 / rejected 红+code，title=完整 id）；无 evidence 时按 skipped/尚无数据区分文案
  - ③文案 + 「查看日志 ⤵」链接（滚动到日志锚点）
  - ④QC 图廊（文件名截短标注）
  - ⑤exclude_category/catalog 或 PASS s9 说明
  - ⑥对象表 + validation 徽章 + scene 目录文件快捷链（scene_spec.json / resolved_scene.json / generated_scene.py → 文件预览弹层）
  - ⑦渲染图分组标注 + `<video>` 大尺寸（max-width 640px）+ `runtime/runtime_validation_report.json` 状态徽章（fetch `/file` 取 status 字段）
  - 每步末尾「原始 JSON」`<details>`。
- [ ] 图片标签映射：

```js
const VIEW_LABELS = {observer_start: "观察·起始", observer_mid: "观察·中段",
  observer_end: "观察·结束", preview_head: "俯视预览",
  preview_segmentation: "分割图", preview_world_left: "世界·左",
  preview_world_right: "世界·右", observer_runtime: "回放视频"};
```

- [ ] lightbox：点击图放大，←/→ 在当前图集内切换，Esc/点击关闭。
- [ ] 验证：duck run 逐步点 ①-⑦ 截图核对；fallback_drill 看 ②chips。
- [ ] Commit `feat(web): 七阶段详情面板 + 图廊标注 + lightbox`

### Task 7: 日志面板（增量/跟随/高亮/去重/锚点）

- [ ] `fetchLogDelta()`：`/log?offset=S.logOffset`，追加解析；`S.logOffset = r.offset + byteLen(chunk)`（用 `new TextEncoder().encode(chunk).length` 精确回推字节数）。切 run 时清空重置。
- [ ] 行渲染 `appendLogLines(text)`：按行 append `<div class=ll>`；normalize `line.replace(/^\[[^\]]*\]\s*/, "")` 后与上一行相同 → 上一行计数徽章 ×N（治 OIDN 刷屏）；`/traceback|error|fail/i` 红、`/^pass |=== stage/i` 绿/分隔样式；`=== stage: X ===` 行加 `id="log-stage-X"` 锚点。
- [ ] 跟随开关：开启则 append 后 `scrollTop = scrollHeight`；用户手动上滚自动暂停跟随（scroll 事件距底 >40px），回底恢复。
- [ ] 验证：选 duck run 日志应见重复 OIDN 折叠为一行×52；PASS 行绿色。
- [ ] Commit `feat(web): 增量日志面板`

### Task 8: 产物文件面板 + 预览弹层

- [ ] 选 run 后 fetch `/files` 一次（live 时随 poll 每 5 轮刷一次）；按目录分组树形列表：名称/大小/相对时间；png/jpg → lightbox；mp4 → 弹层播放；json/py/txt/log/yml → 弹层 `<pre>` 预览（fetch `/file`，>512KB 截断提示 + 「原始链接」）。
- [ ] 验证：duck run 应列出 runtime/scenes 全部白名单文件，点开 `generated_scene.py` 可读。
- [ ] Commit `feat(web): 产物文件面板`

### Task 9: 表单/侧栏/空态/tab 标题

- [ ] 表单：label 显式（提示词/seed/演示缺口·hover 提示保留）；`/api/runs.current` 非空 → 运行按钮禁用 + 文案「有运行进行中」；提交成功即 `loadRun`。
- [ ] 侧栏：条目 = 状态点 + prompt + 相对时间（`mtime`）；`current` run 置顶 spinner；分组标题保留。
- [ ] 空态：未选 run 时主区给引导 + 3 个示例 prompt chip（点击填入表单）。
- [ ] tab 标题：运行中 `⏳<活动阶段短名> · Studio`、完成 `✅/⛔/❌ · Studio`、空闲 `Pipeline Studio`。
- [ ] Commit `feat(web): 表单/侧栏/空态/标题`

### Task 10: 回放器验证实时行为（不碰项目代码）

- [ ] scratchpad 写 `sim_replay.py`（scp 到 lv-5090 `/tmp/`，python 直跑）：把 duck run 复制成 `results/web_runs/_sim_live_demo`，先清空产物，再按脚本节奏（每 3-8s）依序落盘：run_meta → acquire_categories → (逐段追加 run.log) → coverage → scenes → run_state(phase=render) → runtime 逐图 → run_state(done)。
- [ ] 浏览器全程观察：stepper ①→②→⑥→⑦ 状态迁移、耗时累计、日志滚动追加、头卡计时跳动、展开的 JSON 不被打断、tab 标题变化。逐一截图，发现问题当场修（修完补 commit）。
- [ ] 结束删除 `_sim_live_demo` 目录。
- [ ] Commit（如有修复）`fix(web): 回放器发现的实时问题`

### Task 11: 真实 E2E + 历史回归 + 双主题截图审阅

- [ ] 浏览器提交真实 prompt「place a red mug on the table.」端到端跑完，截图 stepper 全程 + 最终 ⑦ 视频可播。
- [ ] 抽查历史 run ×2（`20260803_scene_blocker_final`、`20260808_fallback_drill`）显示正常（无 state 的 history 不应出现「运行中」）。
- [ ] 1440px + 900px 窄窗、light + dark 四组截图自审：对齐/溢出/对比度/中文断行，发现设计问题即改即验（自主循环，不设次数上限，直到自己挑不出毛病）。
- [ ] Commit `fix(web): 截图审阅修正`（如有）

### Task 12: 收尾

- [ ] 删除 `web/index_v1.html.bak`；更新 `web/README.md`（新接口 3 行说明）。
- [ ] `git push origin feat/web-studio-v2`（deploy key）。
- [ ] 值夜汇报 `work/nightwatch/20260812/report.md`（一句话结论/运行画像/事件三段式/交付物/待拍板），SendUserFile proactive 推送 + 最终页面截图。

## Self-Review

- Spec 覆盖：后端 4 项接口→Task1-3；前端 7 项布局→Task4-9；实时机制→Task4/7/10；测试计划→Task1-3(步内)/10/11。无缺口。
- 占位符：Task1 Step3 的 compute_stage_timeline 以规则注释 + 签名给出（完整实现在执行时写直，规则已逐条枚举无歧义）；其余步骤均带真实代码/命令/断言。
- 类型一致：`stage_timeline` 字段名在 Task1 定义、Task5/6 消费一致；`S.logOffset` 字节语义 Task2/7 一致（TextEncoder 回推）。
