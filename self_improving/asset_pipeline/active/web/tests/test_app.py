"""Pipeline Studio web 层测试 — 只测 web/app.py，不触碰 pipeline 代码。"""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "studio_app", Path(__file__).resolve().parents[1] / "app.py"
)
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    web = tmp_path / "web_runs"
    hist = tmp_path / "hist"
    web.mkdir()
    hist.mkdir()
    monkeypatch.setattr(studio, "WEB_RUNS", web)
    monkeypatch.setattr(studio, "HISTORY_ROOT", hist)
    monkeypatch.setattr(studio, "GROUP_ROOTS", {"web": web, "history": hist})
    return web, hist


def make_covered_run(root):
    d = root / "20260812_000000_demo"
    d.mkdir()
    (d / "run_meta.json").write_text(
        json.dumps(
            {
                "prompt": "put a duck on the table",
                "seed": 42,
                "started_at": "2026-08-12T00:00:00+08:00",
            }
        )
    )
    (d / "run_state.json").write_text(
        json.dumps(
            {
                "phase": "done",
                "outcome": "scene",
                "pipeline_rc": 0,
                "render_rc": 0,
                "finished_at": "2026-08-12T00:03:00+08:00",
            }
        )
    )
    (d / "coverage_report.json").write_text(
        json.dumps(
            {
                "objects": [
                    {"object_id": "duck_1", "category": "duck", "status": "covered"}
                ]
            }
        )
    )
    sc = d / "scenes" / "x"
    sc.mkdir(parents=True)
    (sc / "resolved_scene.json").write_text("{}")
    rt = d / "runtime"
    rt.mkdir()
    (rt / "observer_end.png").write_bytes(b"x")
    (d / "run.log").write_text(
        "=== stage: pipeline ===\nPASS scene_acquire scene=x\n"
        "=== stage: render ===\nPASS scene=x fail=0\n"
    )
    return d


def timeline_of(d):
    meta = (
        json.loads((d / "run_meta.json").read_text())
        if (d / "run_meta.json").exists()
        else {}
    )
    state = (
        json.loads((d / "run_state.json").read_text())
        if (d / "run_state.json").exists()
        else {}
    )
    log = (d / "run.log").read_text() if (d / "run.log").exists() else ""
    return studio.compute_stage_timeline(d, meta, state, log)


def test_covered_run_timeline(roots):
    web, _ = roots
    tl = timeline_of(make_covered_run(web))
    assert [s["status"] for s in tl] == [
        "done",
        "skipped",
        "skipped",
        "skipped",
        "skipped",
        "done",
        "done",
    ]
    assert all(s["duration_s"] is None or s["duration_s"] >= 0 for s in tl)


def test_gap_run_live_active_stage2(roots):
    web, _ = roots
    d = web / "20260812_000001_gap"
    d.mkdir()
    (d / "run_meta.json").write_text(
        json.dumps(
            {"prompt": "x", "seed": 1, "started_at": "2026-08-12T00:00:01+08:00"}
        )
    )
    (d / "run_state.json").write_text(
        json.dumps({"phase": "pipeline", "outcome": None})
    )
    (d / "acquire_categories.json").write_text("[]")
    tl = timeline_of(d)
    assert tl[0]["status"] == "done"
    assert tl[1]["status"] == "active"
    # 渲染阶段在 live 中应是 pending 而不是 skipped
    assert tl[6]["status"] == "pending"


def test_blocker_run(roots):
    web, _ = roots
    d = web / "20260812_000002_blk"
    d.mkdir()
    (d / "run_state.json").write_text(
        json.dumps({"phase": "done", "outcome": "blocker", "pipeline_rc": 1})
    )
    (d / "coverage_report.json").write_text(json.dumps({"objects": []}))
    (d / "asset_gap_blocker.json").write_text("{}")
    tl = timeline_of(d)
    assert tl[5]["status"] == "blocked"
    assert tl[6]["status"] == "skipped"


def test_failed_run_marks_failed_stage(roots):
    web, _ = roots
    d = web / "20260812_000003_fail"
    d.mkdir()
    (d / "run_state.json").write_text(
        json.dumps({"phase": "done", "outcome": "failed", "pipeline_rc": 2})
    )
    (d / "coverage_report.json").write_text(json.dumps({"objects": []}))
    tl = timeline_of(d)
    # 覆盖已写、无 scene：第⑥步失败，其余不再是 pending
    assert tl[5]["status"] == "failed"
    assert all(s["status"] != "pending" for s in tl)


def test_stale_mtime_artifacts_do_not_break_durations(roots):
    """复制来的产物 mtime 早于 run 开始时，不得产生离谱耗时。"""
    import os
    import time

    web, _ = roots
    d = make_covered_run(web)
    old = time.time() - 3600
    sc = d / "scenes" / "x" / "resolved_scene.json"
    os.utime(sc, (old, old))
    tl = timeline_of(d)
    total = 180  # meta 里 started→finished 共 3 分钟
    for s in tl:
        assert s["duration_s"] is None or s["duration_s"] <= total + 1


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


def test_log_incremental(roots):
    web, _ = roots
    d = make_covered_run(web)
    c = studio.app.test_client()
    full = (d / "run.log").read_bytes()
    r0 = c.get("/api/run/web/20260812_000000_demo/log?offset=0").get_json()
    assert r0["chunk"].encode() == full
    assert r0["size"] == len(full)
    assert not r0["more"]
    r1 = c.get(
        f"/api/run/web/20260812_000000_demo/log?offset={len(full) - 5}"
    ).get_json()
    assert r1["chunk"].encode() == full[-5:]
    r2 = c.get("/api/run/web/20260812_000000_demo/log?offset=999999").get_json()
    assert r2["chunk"] == "" and not r2["more"]

    d2 = web / "20260812_000009_nolog"
    d2.mkdir()
    r3 = c.get("/api/run/web/20260812_000009_nolog/log").get_json()
    assert r3 == {"offset": 0, "size": 0, "chunk": "", "more": False}


def make_library(tmp_path):
    cat = {
        "entries": [
            {
                "asset_id": "a1",
                "category": "cup",
                "available": True,
                "load_type": "rigid",
                "asset_path": "/external/rt/a1",
                "models": [{}, {}],
                "materials": ["glass"],
                "aliases": ["cup"],
                "colors": [],
            },
            {
                "asset_id": "a2",
                "category": "cup",
                "available": False,
                "load_type": "rigid",
                "asset_path": "/external/rt/a2",
                "models": [{}],
                "availability_reasons": ["stable_pose", "scale"],
                "aliases": [],
                "colors": [],
            },
            {
                "asset_id": "b1",
                "category": "door",
                "available": False,
                "load_type": "urdf",
                "asset_path": str(tmp_path / "lib" / "b1"),
                "models": [{}],
                "availability_reasons": ["stable_pose"],
                "aliases": [],
                "colors": [],
            },
        ]
    }
    catp = tmp_path / "cat.json"
    catp.write_text(json.dumps(cat))
    lib = tmp_path / "lib"
    (lib / "b1" / "snapshots").mkdir(parents=True)
    (lib / "b1" / "snapshots" / "m0_default.png").write_bytes(b"\x89PNGfake")
    prov = tmp_path / "prov.json"
    prov.write_text(
        json.dumps({"providers": {"robotwin_local": {"enabled": True, "tier": 0}}})
    )
    return catp, lib, prov


def test_library_stats_aggregation(tmp_path):
    catp, lib, prov = make_library(tmp_path)
    s = studio.compute_library_stats(catp, lib, prov)
    assert s["kpis"] == {
        "assets": 3,
        "categories": 2,
        "model_variants": 4,
        "available": 1,
        "imported": 1,
    }
    assert s["availability"]["reasons"][0] == {
        "reason": "stable_pose",
        "count": 2,
        "asset_ids": ["a2", "b1"],
    }
    assert {x["key"]: x["count"] for x in s["sources"]} == {
        "robotwin_native": 2,
        "imported": 1,
    }
    assert s["category_depth"]["singletons"] == 1
    assert s["annotation"]["materials"] == 1
    assert s["assets"]["b1"]["thumb"] is True
    assert s["assets"]["a1"]["thumb"] is False
    assert [t["tier"] for t in s["retrieval"]["tiers"]] == [0]


def test_library_endpoints(tmp_path, monkeypatch, roots):
    catp, lib, prov = make_library(tmp_path)
    thumbs = tmp_path / "web_thumbs"
    thumbs.mkdir()
    monkeypatch.setattr(studio, "DEFAULT_CATALOG", catp)
    monkeypatch.setattr(studio, "ASSET_LIB", lib)
    monkeypatch.setattr(studio, "DEFAULT_PROVIDERS", prov)
    monkeypatch.setattr(studio, "WEB_THUMBS", thumbs)
    studio._LIB_CACHE.update({"mtime": None, "data": None})
    c = studio.app.test_client()
    r = c.get("/api/library/stats").get_json()
    assert r["kpis"]["assets"] == 3 and "generated_at" in r

    # 来源分层布局（asset_library/<source>/<id>/）同样能找到快照
    nested = lib / "objaverse" / "n1" / "snapshots"
    nested.mkdir(parents=True)
    (nested / "m0_default.png").write_bytes(b"\x89PNGnested")
    assert c.get("/api/library/thumb/n1").status_code == 200
    # thumb 解析顺序：web_thumbs 优先 → snapshots 兜底 → 404
    assert c.get("/api/library/thumb/b1").status_code == 200
    (thumbs / "b1.png").write_bytes(b"\x89PNGoverride")
    r2 = c.get("/api/library/thumb/b1")
    assert r2.status_code == 200 and b"override" in r2.data
    assert c.get("/api/library/thumb/a1").status_code == 404
    assert c.get("/api/library/thumb/..%2Fcat").status_code == 404


def test_tier_scale_dedup_and_objaverse(tmp_path, monkeypatch):
    import gzip

    monkeypatch.setattr(studio, "DEV", tmp_path)
    (tmp_path / "idx.json").write_text(json.dumps({"a": [1, 2], "b": [3]}))
    # 同层两个 provider 指向同一索引 → 只显示一次
    scale = studio._tier_scale(
        [("p1", {"index_path": "idx.json"}), ("p2", {"index_path": "idx.json"})], 5
    )
    assert scale == "索引 3"

    obja = tmp_path / "obja"
    obja.mkdir()
    with gzip.open(obja / "lvis-annotations.json.gz", "wt") as f:
        json.dump({"cat1": ["u1", "u2"], "cat2": ["u3"]}, f)
    scale2 = studio._tier_scale(
        [("objaverse", {"data_dir": "obja", "per_category_cap": 6})], 5
    )
    assert scale2 == "LVIS 3 物体 · 2 类 · 每类取 ≤6"


def test_files_listing_and_py_whitelist(roots):
    web, _ = roots
    d = make_covered_run(web)
    (d / "scenes" / "x" / "generated_scene.py").write_text("print('hi')")
    (d / "evil.exe").write_bytes(b"no")
    c = studio.app.test_client()
    files = c.get("/api/run/web/20260812_000000_demo/files").get_json()["files"]
    paths = [f["p"] for f in files]
    assert "scenes/x/generated_scene.py" in paths
    assert "evil.exe" not in paths
    assert all(set(f) == {"p", "size", "mtime"} for f in files)
    r = c.get("/api/run/web/20260812_000000_demo/file?p=scenes/x/generated_scene.py")
    assert r.status_code == 200


def test_live_stage34_reset_per_candidate(roots):
    """② 进行中时，③④只反映当前候选：上一候选失败后回灰（即使它的
    截图还在盘上），新候选的转换标记出现才重新点亮。"""
    web, _ = roots
    d = web / "20260820_000004_live"
    d.mkdir()
    (d / "run_meta.json").write_text(
        json.dumps({"prompt": "x", "seed": 1, "started_at": "2026-08-20T00:00:01+08:00"})
    )
    (d / "run_state.json").write_text(json.dumps({"phase": "pipeline", "outcome": None}))
    (d / "acquire_categories.json").write_text("[]")
    shots = d / "acquire" / "shots"
    shots.mkdir(parents=True)
    (shots / "cand1.png").write_bytes(b"x")
    (d / "run.log").write_text(
        "Simulation App Startup Complete\naccepted 3xx m0 ok\nREJECTED 3xx m0 (bad)\n"
    )
    tl = timeline_of(d)
    assert tl[1]["status"] == "active"
    assert tl[2]["status"] == "pending"
    assert tl[3]["status"] == "pending"
    (d / "run.log").write_text((d / "run.log").read_text() + "app ready\n")
    tl = timeline_of(d)
    assert tl[2]["status"] == "active" and tl[3]["status"] == "pending"
    (d / "run.log").write_text((d / "run.log").read_text() + "accepted 3xx m1 ok\n")
    tl = timeline_of(d)
    assert tl[3]["status"] == "active"
