import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "1_search"))
import scene_acquire as sa

FIX = str(Path(__file__).parent / "fixtures" / "mini_catalog.json")


def run_main(tmp_path, prompt, runner):
    return sa.main(
        [
            "--prompt",
            prompt,
            "--seed",
            "42",
            "--catalog",
            FIX,
            "--providers",
            str(tmp_path / "p.json"),
            "--dev-root",
            str(tmp_path),
            "--out",
            str(tmp_path / "out"),
        ],
        runner=runner,
    )


def test_tier0_covered_generates_without_acquire(tmp_path):
    (tmp_path / "p.json").write_text("{}")
    calls = []

    def runner(cmd, cwd=None, env=None):
        calls.append([str(c) for c in cmd])
        scene = tmp_path / "out" / "scenes" / "s1"
        scene.mkdir(parents=True, exist_ok=True)
        (scene / "resolved_scene.json").write_text("{}")
        return 0

    rc = run_main(tmp_path, "Place a red mug on the table.", runner)
    assert rc == 0
    assert len(calls) == 1 and "generate_scene.py" in calls[0][1]
    report = json.loads((tmp_path / "out" / "coverage_report.json").read_text())
    assert report["objects"][0]["status"] == "covered"


def test_gap_triggers_acquire_then_blocker_when_still_missing(tmp_path):
    (tmp_path / "p.json").write_text("{}")
    calls = []
    rc = run_main(
        tmp_path,
        "Place a hammer on the table.",
        lambda cmd, cwd=None, env=None: calls.append([str(c) for c in cmd]) or 0,
    )
    assert rc == 1
    assert any("acquire_batch.py" in c[1] for c in calls)
    assert not any("generate_scene.py" in c[1] for c in calls)
    blocker = json.loads((tmp_path / "out" / "asset_gap_blocker.json").read_text())
    assert blocker["schema"] == "envgen.asset_gap_blocker.v1"
    assert blocker["unmet"][0]["category"] == "hammer"


def test_stale_scene_with_runner_failure_still_fails(tmp_path):
    # Reused --out dir has a stale resolved_scene.json from a prior run; the
    # generate_scene subprocess this time fails (rc=1) and creates nothing new.
    (tmp_path / "p.json").write_text("{}")
    stale = tmp_path / "out" / "scenes" / "old"
    stale.mkdir(parents=True)
    (stale / "resolved_scene.json").write_text("{}")

    rc = run_main(
        tmp_path,
        "Place a red mug on the table.",
        lambda cmd, cwd=None, env=None: 1,
    )
    assert rc == 1


def test_stale_scene_with_no_new_output_still_fails(tmp_path):
    # rc==0 but the subprocess didn't actually create a new resolved_scene.json;
    # the stale file from a reused --out must not false-PASS this run.
    (tmp_path / "p.json").write_text("{}")
    stale = tmp_path / "out" / "scenes" / "old"
    stale.mkdir(parents=True)
    (stale / "resolved_scene.json").write_text("{}")

    rc = run_main(
        tmp_path,
        "Place a red mug on the table.",
        lambda cmd, cwd=None, env=None: 0,
    )
    assert rc == 1


def test_gap_acquired_marks_status_acquired(tmp_path):
    # Simulate a successful gap-driven acquire: the fake runner "imports" a
    # hammer by writing a rebuilt catalog at the dev-root path scene_acquire
    # checks after invoking acquire_batch, so the re-check finds it covered.
    (tmp_path / "p.json").write_text("{}")
    hammer_catalog = json.dumps(
        {
            "schema_version": "robotwin.asset_catalog.v1",
            "robotwin_root": "/x",
            "objects_root": "/x",
            "entries": [
                {
                    "asset_id": "301_hammer",
                    "semantic_name": "hammer",
                    "category": "hammer",
                    "aliases": ["hammer"],
                    "load_type": "rigid",
                    "asset_path": "/x/301_hammer",
                    "models": [{"model_id": 0, "usable": True}],
                    "available": True,
                }
            ],
        }
    )

    def runner(cmd, cwd=None, env=None):
        cmd = [str(c) for c in cmd]
        if "acquire_batch.py" in cmd[1]:
            rebuilt = tmp_path / "data" / "scene_gen_ext" / "asset_catalog.json"
            rebuilt.parent.mkdir(parents=True, exist_ok=True)
            rebuilt.write_text(hammer_catalog)
            return 0
        scene = tmp_path / "out" / "scenes" / "s1"
        scene.mkdir(parents=True, exist_ok=True)
        (scene / "resolved_scene.json").write_text("{}")
        return 0

    rc = run_main(tmp_path, "Place a hammer on the table.", runner)
    assert rc == 0
    report = json.loads((tmp_path / "out" / "coverage_report.json").read_text())
    assert report["objects"][0]["status"] == "acquired"
    assert report["objects"][0]["asset_id"] == "301_hammer"


def test_generate_scene_receives_absolute_paths(tmp_path, monkeypatch):
    # Regression test: generate_scene.py runs with cwd=UP (a different directory),
    # so relative --catalog/--out values must be absolutized before being handed
    # to that subprocess, or they resolve against the wrong cwd.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "p.json").write_text("{}")
    (tmp_path / "catalog.json").write_text(Path(FIX).read_text())
    calls = []

    def runner(cmd, cwd=None, env=None):
        calls.append([str(c) for c in cmd])
        scene = Path("out") / "scenes" / "s1"
        scene.mkdir(parents=True, exist_ok=True)
        (scene / "resolved_scene.json").write_text("{}")
        return 0

    rc = sa.main(
        [
            "--prompt",
            "Place a red mug on the table.",
            "--seed",
            "42",
            "--catalog",
            "catalog.json",
            "--providers",
            "p.json",
            "--dev-root",
            ".",
            "--out",
            "out",
        ],
        runner=runner,
    )
    assert rc == 0
    cmd = calls[0]
    catalog_arg = cmd[cmd.index("--asset-catalog") + 1]
    out_root_arg = cmd[cmd.index("--out-root") + 1]
    assert Path(catalog_arg).is_absolute()
    assert Path(out_root_arg).is_absolute()


def test_acquire_batch_invocation_includes_absolute_tier0_catalog(
    tmp_path, monkeypatch
):
    # scene_acquire must pass its own --catalog through to acquire_batch as
    # --tier0-catalog (absolutized), so tier-0 dedup and coverage read the
    # same catalog instead of two independent sources of truth.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "p.json").write_text("{}")
    (tmp_path / "catalog.json").write_text(Path(FIX).read_text())
    calls = []

    rc = sa.main(
        [
            "--prompt",
            "Place a hammer on the table.",
            "--seed",
            "42",
            "--catalog",
            "catalog.json",
            "--providers",
            "p.json",
            "--dev-root",
            ".",
            "--out",
            "out",
        ],
        runner=lambda cmd, cwd=None, env=None: calls.append([str(c) for c in cmd]) or 0,
    )
    assert rc == 1
    ab_call = next(c for c in calls if "acquire_batch.py" in c[1])
    idx = ab_call.index("--tier0-catalog")
    assert Path(ab_call[idx + 1]).is_absolute()
    assert ab_call[idx + 1] == str((tmp_path / "catalog.json").resolve())
