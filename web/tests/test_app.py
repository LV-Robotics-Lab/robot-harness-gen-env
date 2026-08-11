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
