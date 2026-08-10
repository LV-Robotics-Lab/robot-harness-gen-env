import hashlib
import json

from lib.a6_embodiedgen_source import EmbodiedGenSourceProvider

ROWS = [
    {
        "uuid": "u1soapdish",
        "primary_category": "bathroom_supplies",
        "secondary_category": "bath_products",
        "category": "soap_dish",
        "description": "curved wooden soap dish with pedestal base",
        "asset_dir": "bathroom_supplies/bath_products/u1soapdish",
        "urdf_path": "bathroom_supplies/bath_products/u1soapdish/soap_dish_003.urdf",
    },
    {
        "uuid": "u2mug",
        "primary_category": "kitchen_supplies",
        "secondary_category": "drinkware",
        "category": "mug",
        "description": "white ceramic mug with handle",
        "asset_dir": "kitchen_supplies/drinkware/u2mug",
        "urdf_path": "kitchen_supplies/drinkware/u2mug/mug_001.urdf",
    },
]


def _page_bytes(rows, offset, total):
    return json.dumps(
        {
            "rows": [{"row_idx": offset + i, "row": r} for i, r in enumerate(rows)],
            "num_rows_total": total,
        }
    ).encode()


def make_index_fetch(rows=ROWS, page_size=1, calls=None):
    total = len(rows)

    def fetch(url, timeout_s=60):
        if calls is not None:
            calls.append(url)
        assert "offset=" in url
        offset = int(url.split("offset=")[1].split("&")[0])
        page_rows = rows[offset : offset + page_size]
        return _page_bytes(page_rows, offset, total)

    return fetch


def make_provider(tmp_path, page_size=1, calls=None, fetch_fn=None):
    return EmbodiedGenSourceProvider(
        tmp_path / "idx.json",
        fetch_fn=fetch_fn or make_index_fetch(page_size=page_size, calls=calls),
        page_size=page_size,
    )


def test_ensure_index_fetches_all_rows_and_caches(tmp_path):
    calls = []
    p = make_provider(tmp_path, page_size=1, calls=calls)
    rows = p.ensure_index()
    assert len(rows) == 2
    assert {r["uuid"] for r in rows} == {"u1soapdish", "u2mug"}
    # cache file written
    cached = json.loads((tmp_path / "idx.json").read_text())
    assert len(cached) == 2
    # second call without refresh must not re-fetch
    n_calls_after_first = len(calls)
    p.ensure_index()
    assert len(calls) == n_calls_after_first


def test_ensure_index_pagination_loop_visits_every_page(tmp_path):
    calls = []
    p = make_provider(tmp_path, page_size=1, calls=calls)
    p.ensure_index()
    offsets = [int(u.split("offset=")[1].split("&")[0]) for u in calls]
    assert offsets == [0, 1]


def test_ensure_index_refresh_forces_refetch(tmp_path):
    calls = []
    p = make_provider(tmp_path, page_size=1, calls=calls)
    p.ensure_index()
    n = len(calls)
    p.ensure_index(refresh=True)
    assert len(calls) > n


def test_search_scores_over_description_and_category(tmp_path):
    p = make_provider(tmp_path)
    got = p.search("soap dish")
    assert len(got) == 1
    c = got[0]
    assert c.candidate_id == "embodiedgen:u1soapdish"
    assert c.category == "soap_dish"
    assert c.provider == "embodiedgen_data"
    assert c.format == "urdf"
    assert c.license == "Apache-2.0 (HorizonRobotics/EmbodiedGenData)"
    assert c.score >= 1
    assert c.metadata["uuid"] == "u1soapdish"
    assert c.metadata["asset_dir"] == ROWS[0]["asset_dir"]
    assert c.metadata["description"] == ROWS[0]["description"]
    assert c.metadata["size_hint"] is None
    assert "resolve/main/dataset/" + ROWS[0]["asset_dir"] in c.download_url


def test_search_miss_returns_empty(tmp_path):
    p = make_provider(tmp_path)
    assert p.search("milktea") == []


def test_search_respects_limit(tmp_path):
    p = make_provider(tmp_path)
    got = p.search("bathroom_supplies kitchen_supplies", limit=1)
    assert len(got) == 1


def _tree_entry(path, size=100):
    return {"type": "file", "size": size, "path": path}


def test_fetch_asset_downloads_urdf_and_mesh_files_with_hashes(tmp_path):
    p = make_provider(tmp_path)
    candidate = p.search("soap dish")[0]

    urdf_bytes = b"<robot name='soap_dish_003'></robot>"
    obj_bytes = b"o soap_dish_003\nv 0 0 0\n"
    mtl_bytes = b"newmtl m\n"

    mesh_dir = "dataset/bathroom_supplies/bath_products/u1soapdish/mesh"
    tree_listing = json.dumps(
        [
            _tree_entry(f"{mesh_dir}/soap_dish_003.obj", len(obj_bytes)),
            _tree_entry(f"{mesh_dir}/soap_dish_003.mtl", len(mtl_bytes)),
        ]
    ).encode()

    calls = []

    def fetch(url, timeout_s=60):
        calls.append(url)
        if "/api/datasets/" in url and url.endswith("/mesh"):
            return tree_listing
        if url.endswith("soap_dish_003.urdf"):
            return urdf_bytes
        if url.endswith("soap_dish_003.obj"):
            return obj_bytes
        if url.endswith("soap_dish_003.mtl"):
            return mtl_bytes
        raise AssertionError(f"unexpected url {url}")

    p._fetch = fetch
    dest = tmp_path / "fetched"
    result = p.fetch_asset(candidate, dest)

    assert len(result["files"]) == 3
    for f in result["files"]:
        assert (dest / f).is_file()
    written = {str(f) for f in result["files"]}
    assert any(w.endswith("soap_dish_003.urdf") for w in written)
    assert any(w.endswith("soap_dish_003.obj") for w in written)
    assert any(w.endswith("soap_dish_003.mtl") for w in written)

    for relpath, expected_hash in result["sha256"].items():
        content = (dest / relpath).read_bytes()
        assert hashlib.sha256(content).hexdigest() == expected_hash


def test_fetch_asset_skips_video_and_ply(tmp_path):
    p = make_provider(tmp_path)
    candidate = p.search("soap dish")[0]

    mesh_dir = "dataset/bathroom_supplies/bath_products/u1soapdish/mesh"
    tree_listing = json.dumps(
        [
            _tree_entry(f"{mesh_dir}/soap_dish_003.obj", 10),
            _tree_entry(f"{mesh_dir}/gs_model.ply", 10),
            _tree_entry(f"{mesh_dir}/video.mp4", 10),
        ]
    ).encode()

    def fetch(url, timeout_s=60):
        if "/api/datasets/" in url and url.endswith("/mesh"):
            return tree_listing
        if url.endswith(".urdf"):
            return b"<robot></robot>"
        if url.endswith(".ply"):
            raise AssertionError(".ply must not be fetched")
        if url.endswith(".mp4"):
            raise AssertionError(".mp4 must not be fetched")
        return b"data"

    p._fetch = fetch
    result = p.fetch_asset(candidate, tmp_path / "fetched2")
    assert not any(str(f).endswith(".ply") for f in result["files"])
    assert not any(str(f).endswith(".mp4") for f in result["files"])


def test_fetch_asset_pins_mesh_files_inside_dest_dir(tmp_path):
    """A malicious/malformed tree-listing entry with a path-traversal
    filename (e.g. from a compromised or misbehaving HF API response) must
    not be able to write outside dest_dir -- only the basename is used for
    the local path, never the dataset-supplied directory structure."""
    p = make_provider(tmp_path)
    candidate = p.search("soap dish")[0]

    mesh_dir = "dataset/bathroom_supplies/bath_products/u1soapdish/mesh"
    tree_listing = json.dumps(
        [_tree_entry(f"{mesh_dir}/../../../../evil.obj", 10)]
    ).encode()

    def fetch(url, timeout_s=60):
        if "/api/datasets/" in url and url.endswith("/mesh"):
            return tree_listing
        return b"payload"

    p._fetch = fetch
    dest = tmp_path / "fetched3"
    result = p.fetch_asset(candidate, dest)

    for f in result["files"]:
        local_path = (dest / f).resolve()
        assert dest.resolve() in local_path.parents or local_path == dest.resolve()
    assert not (tmp_path / "evil.obj").exists()
