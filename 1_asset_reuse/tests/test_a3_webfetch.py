import json
from pathlib import Path
from types import SimpleNamespace

from agenticsim.openxsim.assets import AssetCandidate

from lib import a3_webfetch as a3


def gh_cand(name="Lantern.glb"):
    return AssetCandidate(
        candidate_id=f"github:x/{name}",
        name=name,
        category="lantern",
        download_url=f"https://raw.test/{name}",
        source_page="https://gh",
        format="glb",
        provider="github_tree",
        license="CC0",
        score=1.0,
        metadata={"path": name},
    )


def test_synth_record_has_materialize_fields(tmp_path):
    glb = tmp_path / "a.glb"
    glb.write_bytes(b"glTF")
    rec = a3.synth_staging_record(
        glb,
        tmp_path / "src.glb",
        "ab" * 32,
        "301_lantern",
        0,
        {"category": "lantern", "aliases": ["lantern"]},
    )
    for field in (
        "group",
        "usd",
        "usd_local",
        "usd_sha256",
        "asset",
        "model",
        "category",
        "aliases",
        "glb",
        "glb_sha256",
        "up_axis",
        "status",
    ):
        assert field in rec
    assert rec["status"] == "converted" and rec["up_axis"] == "Y"


def test_stage_web_candidate_writes_staging_manifest(tmp_path):
    src = tmp_path / "cache" / "Lantern.glb"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"glTF-binary-bytes")

    def fake_fetch(candidate, cache_dir):
        return SimpleNamespace(path=str(src), sha256="cd" * 32)

    rec = a3.stage_web_candidate(
        gh_cand(),
        {"category": "lantern", "aliases": ["lantern"]},
        "301_lantern",
        0,
        tmp_path / "staging",
        tmp_path / "cache",
        fetch_fn=fake_fetch,
    )
    manifest = json.loads((tmp_path / "staging" / "staging_manifest.json").read_text())
    assert manifest == [rec]
    assert Path(rec["glb"]).is_file()
