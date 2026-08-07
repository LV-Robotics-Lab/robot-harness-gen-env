import json
from pathlib import Path

from agenticsim.openxsim.assets import AssetCandidate

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import acquire_batch as ab
from lib.a1_providers import Tier
from lib import a2_selection as a2
from lib import a3_webfetch as a3w


def cand(key, score=1.0):
    return AssetCandidate(
        candidate_id=f"nvidia:{key}",
        name=key,
        category="x",
        download_url=f"https://x/{key}",
        source_page="https://x",
        format="usd",
        provider="nvidia_server",
        license="unknown",
        score=score,
        metadata={"key": key, "size_bytes": 10},
    )


def gh_cand(name, score=1.0):
    return AssetCandidate(
        candidate_id=f"github:x/{name}",
        name=name,
        category="x",
        download_url=f"https://raw.test/{name}",
        source_page="https://gh",
        format="glb",
        provider="github_tree",
        license="CC0",
        score=score,
        metadata={"path": name},
    )


class FakeProvider:
    def __init__(self, name, results):
        self.name, self.results = name, results

    def search(self, query, limit=20):
        return self.results


def paths(tmp_path):
    return {
        "py_sap": "PY_SAP",
        "py_isa": "PY_ISA",
        "scripts": Path(__file__).resolve().parents[1] / "scripts",
        "library": tmp_path / "library",
        "source": tmp_path / "source",
        "out": tmp_path / "out",
        "manifest": tmp_path / "acquired_manifest.json",
        "fragment_dir": tmp_path / "fragments",
    }


def test_reused_local_makes_no_pipeline_calls(tmp_path):
    calls = []
    tiers = [Tier(0, FakeProvider("t0", [cand("local")]))]
    rec = ab.process_entry(
        {"category": "cup"},
        tiers,
        {},
        paths(tmp_path),
        lambda cmd, env=None: calls.append(cmd) or 0,
    )
    assert rec["status"] == "reused_local" and calls == []


def test_import_with_fallback_on_failed_materialize(tmp_path):
    p = paths(tmp_path)
    calls = []

    def runner(cmd, env=None):
        calls.append([str(c) for c in cmd])
        if (
            "import_materialize.py" in str(cmd[1])
            and len([c for c in calls if "import_materialize.py" in c[1]]) == 2
        ):
            d = p["library"] / "301_pitcher"
            (d / "visual").mkdir(parents=True, exist_ok=True)
            (d / "visual" / "base0.glb").write_bytes(b"x")
            (d / "model_data0.json").write_text("{}")
        return 0

    tiers = [
        Tier(0, FakeProvider("t0", [])),
        Tier(
            1, FakeProvider("t1", [cand("a/first.usd", 2.0), cand("a/second.usd", 1.0)])
        ),
    ]
    rec = ab.process_entry(
        {"category": "pitcher"}, tiers, {"max_fallback": 2}, p, runner
    )
    assert rec["status"] == "imported"
    assert rec["attempts"] == 2
    assert rec["selected"]["candidate_id"].endswith("second.usd")
    failed = [c for c in rec["candidates"] if c["verdict"] == "rejected"]
    assert failed and failed[0]["rejection"]["code"].startswith("validation_failed")
    assert json.loads(p["manifest"].read_text())["groups"]


def test_resolve_catalog_path_prefers_dev_root_when_present(tmp_path):
    rebuilt = tmp_path / "data" / "scene_gen_ext" / "asset_catalog.json"
    rebuilt.parent.mkdir(parents=True)
    rebuilt.write_text("{}")
    got = ab.resolve_catalog_path(
        tmp_path,
        "data/scene_gen_ext/asset_catalog.json",
        "/fallback/asset_catalog.json",
    )
    assert got == rebuilt


def test_resolve_catalog_path_falls_back_when_missing(tmp_path):
    got = ab.resolve_catalog_path(
        tmp_path,
        "data/scene_gen_ext/asset_catalog.json",
        "/fallback/asset_catalog.json",
    )
    assert got == Path("/fallback/asset_catalog.json")


def test_exhausted_when_all_attempts_fail(tmp_path):
    tiers = [
        Tier(0, FakeProvider("t0", [])),
        Tier(
            1,
            FakeProvider("t1", [cand("a/x.usd", 2.0), cand("a/never_tried.usd", 1.0)]),
        ),
    ]
    rec = ab.process_entry(
        {"category": "pitcher"},
        tiers,
        {"max_fallback": 0},
        paths(tmp_path),
        lambda cmd, env=None: 0,
    )
    assert rec["status"] == "exhausted"
    outranked = [c for c in rec["candidates"] if c["verdict"] == "outranked"]
    assert outranked and outranked[0]["candidate_id"].endswith("never_tried.usd")
    assert outranked[0]["rejection"]["code"] == a2.REJ_OUTRANKED
    assert (
        outranked[0]["rejection"]["detail"]
        == "not attempted; fallback budget exhausted"
    )


def test_github_candidate_uses_stage_web_candidate_not_fetch_convert(
    tmp_path, monkeypatch
):
    staged = []
    monkeypatch.setattr(
        a3w, "stage_web_candidate", lambda *a, **k: staged.append(a) or {}
    )
    runner_calls = []
    tiers = [
        Tier(0, FakeProvider("t0", [])),
        Tier(1, FakeProvider("t1", [gh_cand("Lantern.glb")])),
    ]
    ab.process_entry(
        {"category": "lantern"},
        tiers,
        {"max_fallback": 0},
        paths(tmp_path),
        lambda cmd, env=None: runner_calls.append([str(c) for c in cmd]) or 0,
    )
    assert len(staged) == 1
    assert any("import_materialize.py" in c[1] for c in runner_calls)
    assert not any("import_fetch_convert.py" in c[1] for c in runner_calls)


def test_github_fetch_failure_records_rejection_and_continues(tmp_path, monkeypatch):
    def fake_stage(candidate, *a, **k):
        if candidate.name == "first.glb":
            raise RuntimeError("boom")
        return {}

    monkeypatch.setattr(a3w, "stage_web_candidate", fake_stage)
    tiers = [
        Tier(0, FakeProvider("t0", [])),
        Tier(
            1,
            FakeProvider("t1", [gh_cand("first.glb", 2.0), gh_cand("second.glb", 1.0)]),
        ),
    ]
    rec = ab.process_entry(
        {"category": "lantern"},
        tiers,
        {"max_fallback": 1},
        paths(tmp_path),
        lambda cmd, env=None: 0,
    )
    assert rec["attempts"] == 2
    codes = {
        c["candidate_id"].rsplit("/", 1)[-1]: c["rejection"]["code"]
        for c in rec["candidates"]
    }
    assert codes["first.glb"] == a2.REJ_FETCH
    assert codes["second.glb"] == "validation_failed:materialize"


def test_pinned_entry_skips_search_but_gates(tmp_path):
    p = paths(tmp_path)

    def runner(cmd, env=None):
        if "import_materialize.py" in str(cmd[1]):
            d = p["library"] / "301_kettle"
            (d / "visual").mkdir(parents=True, exist_ok=True)
            (d / "visual" / "base0.glb").write_bytes(b"x")
            (d / "model_data0.json").write_text("{}")
        return 0

    entry = {
        "category": "kettle",
        "pinned": {
            "prefix": "Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned",
            "usd": "019_pitcher_base.usd",
        },
    }
    rec = ab.process_entry(entry, [], {}, p, runner)
    assert rec["entry_mode"] == "pinned"
    assert rec["status"] == "imported"
    assert rec["attempts"] == 1


def test_local_entry_materializes_from_file(tmp_path):
    p = paths(tmp_path)
    src = tmp_path / "teapot.glb"
    src.write_bytes(b"glTF-bytes")

    def runner(cmd, env=None):
        if "import_materialize.py" in str(cmd[1]):
            d = p["library"] / "301_teapot"
            (d / "visual").mkdir(parents=True, exist_ok=True)
            (d / "visual" / "base0.glb").write_bytes(b"x")
            (d / "model_data0.json").write_text("{}")
        return 0

    entry = {"category": "teapot", "local": {"path": str(src), "up_axis": "Y"}}
    rec = ab.process_entry(entry, [], {}, p, runner)
    assert rec["entry_mode"] == "local"
    assert rec["status"] == "imported"
    staging = list(Path(p["out"]).glob("staging_*/staging_manifest.json"))
    assert staging and json.loads(staging[0].read_text())[0]["up_axis"] == "Y"


def test_local_entry_oversize_rejected_without_materialize(tmp_path):
    p = paths(tmp_path)
    src = tmp_path / "big.glb"
    src.write_bytes(b"x" * 100)
    calls = []

    entry = {"category": "urn", "local": {"path": str(src)}}
    rec = ab.process_entry(
        entry,
        [],
        {"max_size_bytes": 10},
        p,
        lambda cmd, env=None: calls.append(cmd) or 0,
    )
    assert rec["status"] == "exhausted"
    assert rec["candidates"][0]["verdict"] == "rejected"
    assert rec["candidates"][0]["rejection"]["code"] == a2.REJ_OVERSIZE
    assert calls == []


def test_local_entry_convert_failure_records_rejection(tmp_path, monkeypatch):
    p = paths(tmp_path)
    src = tmp_path / "bad.obj"
    src.write_bytes(b"not a real mesh")

    def boom(*a, **k):
        raise RuntimeError("bad mesh")

    monkeypatch.setattr(a3w, "to_glb", boom)
    entry = {"category": "gizmo", "local": {"path": str(src)}}
    rec = ab.process_entry(entry, [], {}, p, lambda cmd, env=None: 0)
    assert rec["status"] == "exhausted"
    assert rec["candidates"][0]["rejection"]["code"] == a2.REJ_CONVERT


def test_pinned_entry_with_existing_model_reuses_without_runner_calls(tmp_path):
    p = paths(tmp_path)
    d = p["library"] / "301_kettle"
    (d / "visual").mkdir(parents=True, exist_ok=True)
    (d / "visual" / "base0.glb").write_bytes(b"x")
    (d / "model_data0.json").write_text("{}")
    calls = []

    entry = {
        "category": "kettle",
        "pinned": {
            "prefix": "Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned",
            "usd": "019_pitcher_base.usd",
        },
    }
    rec = ab.process_entry(
        entry, [], {}, p, lambda cmd, env=None: calls.append(cmd) or 0
    )
    assert rec["status"] == "reused_local"
    assert rec["local_reuse"] == {"asset_id": "301_kettle", "reason": a2.ALREADY}
    assert calls == []


def test_local_entry_with_existing_model_reuses_without_runner_calls(tmp_path):
    p = paths(tmp_path)
    d = p["library"] / "301_teapot"
    (d / "visual").mkdir(parents=True, exist_ok=True)
    (d / "visual" / "base0.glb").write_bytes(b"x")
    (d / "model_data0.json").write_text("{}")
    calls = []
    src = tmp_path / "teapot.glb"
    src.write_bytes(b"glTF-bytes")

    entry = {"category": "teapot", "local": {"path": str(src), "up_axis": "Y"}}
    rec = ab.process_entry(
        entry, [], {}, p, lambda cmd, env=None: calls.append(cmd) or 0
    )
    assert rec["status"] == "reused_local"
    assert rec["local_reuse"] == {"asset_id": "301_teapot", "reason": a2.ALREADY}
    assert calls == []


def test_local_entry_success_appends_manifest_group(tmp_path):
    p = paths(tmp_path)
    src = tmp_path / "teapot.glb"
    src.write_bytes(b"glTF-bytes")

    def runner(cmd, env=None):
        if "import_materialize.py" in str(cmd[1]):
            d = p["library"] / "301_teapot"
            (d / "visual").mkdir(parents=True, exist_ok=True)
            (d / "visual" / "base0.glb").write_bytes(b"x")
            (d / "model_data0.json").write_text("{}")
        return 0

    entry = {"category": "teapot", "local": {"path": str(src), "up_axis": "Y"}}
    rec = ab.process_entry(entry, [], {}, p, runner)
    assert rec["status"] == "imported"
    groups = json.loads(p["manifest"].read_text())["groups"]
    assert groups and groups[0]["name"] == "acq_301_teapot"
    item = groups[0]["items"][0]
    assert item["asset"] == "301_teapot"
    assert item["model"] == 0
    assert item["category"] == "teapot"


def test_malformed_entry_isolated_batch_completes(tmp_path, capsys):
    categories = tmp_path / "categories.json"
    categories.write_text(json.dumps([{"no_category": True}, {"category": "cup"}]))
    providers = tmp_path / "providers.json"
    providers.write_text(json.dumps({"globals": {}}))
    tiers = [Tier(0, FakeProvider("t0", [cand("cup_hit")]))]

    rc = ab.main(
        [
            "--categories",
            str(categories),
            "--providers",
            str(providers),
            "--dev-root",
            str(tmp_path),
            "--out",
            str(tmp_path / "out"),
        ],
        runner=lambda cmd, env=None: 0,
        tiers=tiers,
    )
    assert rc != 0
    evidence = json.loads((tmp_path / "out" / "selection_evidence.json").read_text())
    statuses = [c["status"] for c in evidence["categories"]]
    assert statuses == ["entry_error", "reused_local"]
    assert len(evidence["categories"]) == 2
    err = evidence["categories"][0]
    assert err["query"]["category"] == "<invalid>"
    assert err["entry_mode"] == "error"
    assert err["candidates"] == []
    assert err["attempts"] == 0
    assert "KeyError" in err["error"]
    out = capsys.readouterr().out
    assert "FAIL <invalid> status=entry_error" in out
    assert "PASS cup status=reused_local" in out
