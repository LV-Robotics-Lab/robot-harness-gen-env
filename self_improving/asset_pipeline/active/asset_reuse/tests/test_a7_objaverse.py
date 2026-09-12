"""Objaverse LVIS provider, offline: injected fetch, fixture shards."""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.a7_objaverse import SPDX_BY_SLUG, ObjaverseLvisProvider  # noqa: E402

UID = "79f66456163749c0a8ed733698ca5a16"


def make_provider(tmp_path, ann=None, thumb=b"\xff\xd8jpg"):
    d = tmp_path / "objaverse"
    d.mkdir()
    (d / "lvis-annotations.json.gz").write_bytes(
        gzip.compress(json.dumps({"hammer": [UID], "teddy_bear": ["x" * 32]}).encode())
    )
    (d / "object-paths.json.gz").write_bytes(
        gzip.compress(
            json.dumps(
                {UID: f"glbs/000-118/{UID}.glb", "x" * 32: "glbs/000-001/" + "x" * 32 + ".glb"}
            ).encode()
        )
    )
    shard = {
        UID: {
            "name": "Old Claw Hammer",
            "license": "by",
            "uri": "https://sketchfab.com/models/xyz",
            "thumbnails": {"images": [{"url": "https://cdn/img256.jpg", "width": 256}]},
        }
    }
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("000-118.json.gz"):
            return gzip.compress(json.dumps(shard).encode())
        if url.endswith("000-001.json.gz"):
            return gzip.compress(json.dumps({}).encode())
        if url.endswith(".jpg"):
            return thumb
        raise AssertionError(url)

    return ObjaverseLvisProvider(d, fetch=fetch), calls


def test_exact_category_match_hydrates_license_and_thumbnail(tmp_path):
    p, calls = make_provider(tmp_path)
    out = p.search("hammer", limit=5)
    assert len(out) == 1
    c = out[0]
    assert c.provider == "objaverse" and c.format == "glb"
    assert c.download_url.endswith(f"glbs/000-118/{UID}.glb")
    # machine-readable licence, straight from the metadata shard
    assert c.metadata["license_spdx"] == "CC-BY-4.0"
    # thumbnail landed locally so the pre-download gate has a picture
    assert Path(c.metadata["thumbnail"]).read_bytes().startswith(b"\xff\xd8")
    assert "Old Claw Hammer" in c.name


def test_category_vocabulary_not_substrings(tmp_path):
    """LVIS matching is against category NAMES: "bear" must not surface
    teddy_bear the way substring matching once surfaced caster_bearing."""
    p, _ = make_provider(tmp_path)
    assert p.search("teddy bear", limit=5) != []  # full name mentioned
    assert p.search("hammer time", limit=5) != []  # query mentions category
    assert p.search("mmer", limit=5) == []  # substring never matches


def test_shard_fetches_are_cached(tmp_path):
    p, calls = make_provider(tmp_path)
    p.search("hammer")
    n = len([c for c in calls if c.endswith(".json.gz")])
    p2 = ObjaverseLvisProvider(tmp_path / "objaverse", fetch=lambda u: (_ for _ in ()).throw(AssertionError(u)))
    out = p2.search("hammer")  # 磁盘缓存命中，零网络
    assert out and n == 1


def test_unknown_slug_stays_unknown(tmp_path):
    p, _ = make_provider(tmp_path)
    p.search("hammer")  # prime the on-disk shard cache before mutating it
    # 改 shard 缓存为 nc 许可（不在自动 declared 白名单，但 SPDX 仍如实记录）
    shard_file = tmp_path / "objaverse" / "metadata" / "000-118.json.gz"
    data = json.load(gzip.open(shard_file))
    data[UID]["license"] = "by-nc"
    shard_file.write_bytes(gzip.compress(json.dumps(data).encode()))
    out = p.search("hammer")
    assert out[0].metadata["license_spdx"] == "CC-BY-NC-4.0"
    assert SPDX_BY_SLUG["by-nc"] == "CC-BY-NC-4.0"
