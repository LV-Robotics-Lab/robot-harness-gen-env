# 资产库统计视图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pipeline Studio 增加「资产库」视图：KPI/可用性解锁清单/来源梯队/类别深度/标注覆盖/最近引进 + 缩略图下钻。

**Architecture:** app.py 只增不改（stats 聚合带 mtime 缓存 + thumb 静态解析）；bake 工具一次性离屏渲缩略图到 results/web_thumbs/；index.html 加 hash 路由双视图，图表手写 SVG/CSS。

**Tech Stack:** Flask/pytest（env-gen-yuxin）；sapien 3.0 + trimesh 4.4 离屏渲染；vanilla JS/SVG。

## Global Constraints

- 只改 `web/`；`results/web_thumbs/` 为可写缓存；pipeline/上游只读。
- 现有接口不变；分支 `feat/web-studio-v2` 连续提交（中文 commit + 尾注）。
- 图表实现前先加载 dataviz skill 校准（stat tile/条形/占比条规范）。
- 部署/重启/测试命令与 v2 计划相同（pkill 用 `"bin/python app[.]py"` 正则）。

### Task 1: /api/library/stats + /api/library/thumb（TDD）

**Files:** Modify `web/app.py`；Test `web/tests/test_app.py` 追加。

**Interfaces（Produces）:**
- `compute_library_stats(catalog_path: Path, asset_lib: Path, providers_path: Path) -> dict`，形如 spec JSON（kpis/retrieval/availability/sources/load_types/category_depth/annotation/recent_imports/assets）。
- `GET /api/library/stats` → 上述 dict + `generated_at`；进程内缓存，catalog mtime 变化即失效。
- `GET /api/library/thumb/<asset_id>` → PNG；解析 `results/web_thumbs/<id>.png` → `data/asset_library/<id>/snapshots/m0_default.png` → 404；asset_id 必须过 `ID_RE`。
- 常量 `WEB_THUMBS = DEV / "results" / "web_thumbs"`、`ASSET_LIB = DEV / "data" / "asset_library"`。

- [ ] Step 1 失败测试（fixture 迷你 catalog 3 资产：1 可用 rigid、1 缺 stable_pose+scale、1 urdf；asset_lib 里 1 个引进目录带 snapshots）：

```python
def test_library_stats_aggregation(tmp_path, monkeypatch):
    cat = {"entries": [
        {"asset_id": "a1", "category": "cup", "available": True, "load_type": "rigid",
         "asset_path": "/external/rt/a1", "models": [{}, {}], "materials": ["glass"], "aliases": ["cup"], "colors": []},
        {"asset_id": "a2", "category": "cup", "available": False, "load_type": "rigid",
         "asset_path": "/external/rt/a2", "models": [{}],
         "availability_reasons": ["stable_pose", "scale"], "aliases": [], "colors": []},
        {"asset_id": "b1", "category": "door", "available": False, "load_type": "urdf",
         "asset_path": str(tmp_path / "lib" / "b1"), "models": [{}],
         "availability_reasons": ["stable_pose"], "aliases": [], "colors": []},
    ]}
    catp = tmp_path / "cat.json"; catp.write_text(json.dumps(cat))
    lib = tmp_path / "lib"; (lib / "b1" / "snapshots").mkdir(parents=True)
    (lib / "b1" / "snapshots" / "m0_default.png").write_bytes(b"png")
    prov = tmp_path / "prov.json"
    prov.write_text(json.dumps({"providers": {"robotwin_local": {"enabled": True, "tier": 0}}}))
    s = studio.compute_library_stats(catp, lib, prov)
    assert s["kpis"] == {"assets": 3, "categories": 2, "model_variants": 4,
                         "available": 1, "imported": 1}
    assert s["availability"]["reasons"][0] == {"reason": "stable_pose", "count": 2, "asset_ids": ["a2", "b1"]}
    assert {x["key"]: x["count"] for x in s["sources"]} == {"robotwin_native": 2, "imported": 1}
    assert s["category_depth"]["singletons"] == 1
    assert s["assets"]["b1"]["thumb"] is True
```

  再加 thumb 端点测试：web_thumbs 优先、snapshots 兜底、缺图 404、`../` 拒绝。
- [ ] Step 2 确认失败 → Step 3 实现（聚合纯函数 + 端点 + `_LIB_CACHE = {"mtime": None, "data": None}`）
- [ ] Step 4 全绿 + 真实数据 curl 冒烟（assets=143, available=30, imported=18）
- [ ] Step 5 Commit `feat(web): 资产库统计与缩略图接口`

### Task 2: bake_thumbs 工具 + 全量烘焙

**Files:** Create `web/tools/bake_thumbs.py`（探针脚本工程化：argparse --catalog/--out/--size，幂等跳过已存在，urdf/unsupported/加载失败跳过并计数，末尾打印 baked/skipped/failed）。

- [ ] Step 1 写工具（探针代码为基，逐资产 try/except）
- [ ] Step 2 真实跑：`python web/tools/bake_thumbs.py`，预期 baked≈121（131 rigid 中部分 glb 缺失/失败可容忍），抽查 6 张
- [ ] Step 3 Commit `feat(web): 缩略图烘焙工具`（不提交 PNG，results/ 已忽略）

### Task 3: 前端资产库视图

**Files:** Modify `web/index.html`。

**先加载 dataviz skill**，然后：

- [ ] hash 路由：`#library` ↔ 运行视图；header 页签「运行 │ 资产库」；空态入口卡
- [ ] `renderLibrary(stats)` 六区块（KPI 行 / 可用性+解锁横条 / 来源条+tier 卡 / 类别深度 / 标注覆盖 / 最近引进）；SVG 条形图手写；进入视图 fetch 一次 + 手动刷新按钮
- [ ] 下钻弹层 `openAssetDrill(title, asset_ids)`：缩略图网格（lazy `<img loading=lazy>`，onerror → 占位 SVG），徽章 available/load_type
- [ ] 浏览器验证：数字与 curl 冒烟一致；下钻含图；四组截图审阅迭代
- [ ] Commit `feat(web): 资产库视图`

### Task 4: 收尾

- [ ] README v2 节补 3 行（stats/thumb/bake）；push；简报（本次非通宵，直接会话内交付结论 + 截图）

## Self-Review

- spec 六区块 → Task3 全覆盖；缩略图三级解析 → Task1/2；缓存/防穿越 → Task1。
- 类型一致：`compute_library_stats` 返回键与前端消费键一致（kpis/availability.reasons[].asset_ids/sources[].key/category_depth.buckets/annotation/recent_imports/assets）。
- 无占位符：测试给了完整断言；bake 工具以已验证探针为基。
