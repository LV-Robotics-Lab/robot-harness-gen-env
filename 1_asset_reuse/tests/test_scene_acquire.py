import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
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
