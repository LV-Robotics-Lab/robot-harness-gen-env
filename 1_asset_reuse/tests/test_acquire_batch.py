import json
from pathlib import Path

from agenticsim.openxsim.assets import AssetCandidate

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import acquire_batch as ab
from lib.a1_providers import Tier
from lib import a2_selection as a2


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
