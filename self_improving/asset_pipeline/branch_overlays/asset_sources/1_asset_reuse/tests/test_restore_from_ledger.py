import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import restore_from_ledger as rfl  # noqa: E402


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def make_ledger(asset, model_id, reps, source, kind="rigid"):
    return {
        "schema_version": "asset_ledger.v1",
        "asset_id": f"external_{asset}",
        "category": asset.split("_", 1)[1],
        "semantic_name": asset.split("_", 1)[1],
        "kind": kind,
        "tags": ["rigid", "external", "batch"],
        "semantics": {"aliases": [asset], "colors": [], "materials": []},
        "models": [
            {
                "model_id": model_id,
                "physical": {
                    "mesh_bbox_m": [0.1, 0.1, 0.1],
                    "mesh_up_axis": "Y",
                    "origin_convention": "bottom-center",
                    "scale_applied": 1.0,
                    "size_resolution": {
                        "mode": "match_category",
                        "actual_max_dim_m": 0.1,
                        "scale": 1.0,
                        "reference_max_dim_m": None,
                        "reference_assets": [],
                        "verdict": "no_precedent",
                    },
                    "conventions": {
                        "is_static": False,
                        "z_policy": "origin_on_table",
                        "footprint_shape": "box",
                        "stable_poses": [
                            {
                                "pose_id": "upright",
                                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                                "is_default": True,
                            }
                        ],
                        "inherited_from": None,
                    },
                    "mass_kg": {
                        "value": None,
                        "status": "unknown",
                        "runtime_default_kg": 0.1,
                        "runtime_default_basis": "global_constant",
                    },
                    "friction": {
                        "value": None,
                        "status": "unknown",
                        "runtime_default": None,
                        "runtime_default_basis": "none",
                    },
                },
                "representations": reps,
                "articulation": {},
                "source": source,
                "verification": [],
            }
        ],
    }


def write_ledger(library_dir, asset, ledger):
    d = Path(library_dir) / asset
    d.mkdir(parents=True, exist_ok=True)
    (d / "ledger.json").write_text(json.dumps(ledger, indent=2))
    return d


def rep(uri, sha256, size_bytes, backend="sapien", role="visual", fmt="glb"):
    return {
        "format": fmt,
        "uri": uri,
        "backend": backend,
        "role": role,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def test_verify_reports_ok_when_files_present_and_hashes_match(tmp_path):
    lib = tmp_path / "asset_library"
    content = b"visual-bytes"
    visual = tmp_path / "visual.glb"
    visual.write_bytes(content)
    reps = [rep(str(visual), _sha(content), len(content))]
    write_ledger(
        lib,
        "301_soap_dish",
        make_ledger(
            "301_soap_dish",
            0,
            reps,
            {
                "library": "x",
                "group": "g",
                "file": "f",
                "license": {},
                "retrieved_at": "2026-08-08",
                "source_manifest_path": None,
            },
        ),
    )

    results = rfl.verify_library(lib)
    assert len(results) == 1
    assert results[0]["asset"] == "301_soap_dish"
    assert results[0]["status"] == "OK"
    assert results[0]["problems"] == []


def test_verify_detects_missing_file(tmp_path):
    lib = tmp_path / "asset_library"
    reps = [rep(str(tmp_path / "nope.glb"), "0" * 64, 10)]
    write_ledger(
        lib,
        "301_soap_dish",
        make_ledger(
            "301_soap_dish",
            0,
            reps,
            {
                "library": "x",
                "group": "g",
                "file": "f",
                "license": {},
                "retrieved_at": "2026-08-08",
                "source_manifest_path": None,
            },
        ),
    )

    results = rfl.verify_library(lib)
    assert results[0]["status"] == "MISSING"
    assert results[0]["problems"][0]["status"] == "MISSING"


def test_verify_detects_hash_mismatch(tmp_path):
    lib = tmp_path / "asset_library"
    p = tmp_path / "visual.glb"
    p.write_bytes(b"actual-bytes")
    reps = [rep(str(p), _sha(b"expected-bytes-different"), 10)]
    write_ledger(
        lib,
        "301_soap_dish",
        make_ledger(
            "301_soap_dish",
            0,
            reps,
            {
                "library": "x",
                "group": "g",
                "file": "f",
                "license": {},
                "retrieved_at": "2026-08-08",
                "source_manifest_path": None,
            },
        ),
    )

    results = rfl.verify_library(lib)
    assert results[0]["status"] == "HASH_MISMATCH"


def test_verify_scopes_to_asset_filter(tmp_path):
    lib = tmp_path / "asset_library"
    ok_rep = [rep(str(tmp_path / "nope.glb"), "0" * 64, 10)]
    for asset in ("301_soap_dish", "302_mug"):
        write_ledger(
            lib,
            asset,
            make_ledger(
                asset,
                0,
                ok_rep,
                {
                    "library": "x",
                    "group": "g",
                    "file": "f",
                    "license": {},
                    "retrieved_at": "2026-08-08",
                    "source_manifest_path": None,
                },
            ),
        )

    results = rfl.verify_library(lib, asset_filter="301_soap_dish")
    assert len(results) == 1 and results[0]["asset"] == "301_soap_dish"


def test_verify_exit_code_nonzero_on_problems(tmp_path, capsys):
    lib = tmp_path / "asset_library"
    reps = [rep(str(tmp_path / "nope.glb"), "0" * 64, 10)]
    write_ledger(
        lib,
        "301_soap_dish",
        make_ledger(
            "301_soap_dish",
            0,
            reps,
            {
                "library": "x",
                "group": "g",
                "file": "f",
                "license": {},
                "retrieved_at": "2026-08-08",
                "source_manifest_path": None,
            },
        ),
    )

    code = rfl.main(["--library-dir", str(lib)])
    assert code != 0
    out = capsys.readouterr().out
    assert "MISSING" in out


def test_verify_exit_code_zero_when_all_ok(tmp_path):
    lib = tmp_path / "asset_library"
    content = b"visual-bytes"
    visual = tmp_path / "visual.glb"
    visual.write_bytes(content)
    reps = [rep(str(visual), _sha(content), len(content))]
    write_ledger(
        lib,
        "301_soap_dish",
        make_ledger(
            "301_soap_dish",
            0,
            reps,
            {
                "library": "x",
                "group": "g",
                "file": "f",
                "license": {},
                "retrieved_at": "2026-08-08",
                "source_manifest_path": None,
            },
        ),
    )

    code = rfl.main(["--library-dir", str(lib)])
    assert code == 0


def test_verify_counts_corrupt_ledger_as_a_problem(tmp_path):
    """A ledger.json that fails to parse must not be silently skipped -- it
    has to surface as a problem (LEDGER_CORRUPT) and fail the exit code,
    same as any other asset-integrity failure."""
    lib = tmp_path / "asset_library"
    d = lib / "301_soap_dish"
    d.mkdir(parents=True)
    (d / "ledger.json").write_text("{not valid json")

    results = rfl.verify_library(lib)
    assert len(results) == 1
    assert results[0]["asset"] == "301_soap_dish"
    assert results[0]["status"] == "LEDGER_CORRUPT"
    assert len(results[0]["problems"]) == 1
    assert results[0]["problems"][0]["status"] == "LEDGER_CORRUPT"

    code = rfl.main(["--library-dir", str(lib)])
    assert code != 0


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def _source_with_manifest(tmp_path, prefix, files):
    manifest_path = tmp_path / "SOURCE_MANIFEST.json"
    manifest_path.write_text(json.dumps({"prefix": prefix, "files": files}))
    return manifest_path


def test_restore_refetches_missing_file_via_fake_fetcher(tmp_path):
    lib = tmp_path / "asset_library"
    src_bytes = b"raw-source-bytes"
    manifest_path = _source_with_manifest(
        tmp_path, "https://example.com/assets", {"src.glb": _sha(src_bytes)}
    )
    dest = lib / "301_soap_dish" / "_source" / "src.glb"
    reps = [
        rep(
            str(dest),
            _sha(src_bytes),
            len(src_bytes),
            backend="isaacsim",
            role="visual_and_collision",
            fmt="usd",
        )
    ]
    source = {
        "library": "x",
        "group": "web_301_soap_dish",
        "file": "src.glb",
        "license": {},
        "retrieved_at": "2026-08-08",
        "source_manifest_path": str(manifest_path),
    }
    write_ledger(lib, "301_soap_dish", make_ledger("301_soap_dish", 0, reps, source))
    assert not dest.is_file()

    calls = []

    def fetch(url):
        calls.append(url)
        assert url == "https://example.com/assets/src.glb"
        return src_bytes

    result = rfl.restore_library(lib, fetch_fn=fetch)
    assert dest.is_file()
    assert dest.read_bytes() == src_bytes
    assert len(result["restored"]) == 1
    assert result["unrecoverable"] == []
    assert calls == ["https://example.com/assets/src.glb"]


def test_restore_nvidia_bucket_relative_prefix(tmp_path):
    lib = tmp_path / "asset_library"
    src_bytes = b"usd-bytes"
    manifest_path = _source_with_manifest(
        tmp_path,
        "Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned",
        {"002_x.usd": _sha(src_bytes)},
    )
    dest = lib / "302_can" / "_source" / "002_x.usd"
    reps = [
        rep(
            str(dest),
            _sha(src_bytes),
            len(src_bytes),
            backend="isaacsim",
            role="visual_and_collision",
            fmt="usd",
        )
    ]
    source = {
        "library": "NVIDIA Isaac Assets 5.1",
        "group": "acq_302_can",
        "file": "002_x.usd",
        "license": {},
        "retrieved_at": "2026-08-08",
        "source_manifest_path": str(manifest_path),
    }
    write_ledger(lib, "302_can", make_ledger("302_can", 0, reps, source))

    calls = []

    def fetch(url):
        calls.append(url)
        return src_bytes

    rfl.restore_library(lib, fetch_fn=fetch)
    assert dest.is_file()
    assert calls == [
        rfl.BUCKET + "/Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned/002_x.usd"
    ]


def test_restore_hash_mismatch_quarantines_and_does_not_install(tmp_path):
    lib = tmp_path / "asset_library"
    src_bytes = b"raw-source-bytes"
    wrong_bytes = b"WRONG-bytes-served-by-source"
    manifest_path = _source_with_manifest(
        tmp_path, "https://example.com/assets", {"src.glb": _sha(src_bytes)}
    )
    dest = lib / "301_soap_dish" / "_source" / "src.glb"
    reps = [
        rep(
            str(dest),
            _sha(src_bytes),
            len(src_bytes),
            backend="isaacsim",
            role="visual_and_collision",
            fmt="usd",
        )
    ]
    source = {
        "library": "x",
        "group": "web_301_soap_dish",
        "file": "src.glb",
        "license": {},
        "retrieved_at": "2026-08-08",
        "source_manifest_path": str(manifest_path),
    }
    write_ledger(lib, "301_soap_dish", make_ledger("301_soap_dish", 0, reps, source))

    result = rfl.restore_library(lib, fetch_fn=lambda url: wrong_bytes)
    assert not dest.is_file()
    mismatch = dest.with_name(dest.name + ".mismatch")
    assert mismatch.is_file()
    assert mismatch.read_bytes() == wrong_bytes
    assert len(result["mismatched"]) == 1


def test_restore_unrecoverable_when_no_source_manifest(tmp_path):
    lib = tmp_path / "asset_library"
    dest = lib / "301_soap_dish" / "visual" / "base0.glb"
    reps = [rep(str(dest), "0" * 64, 10)]
    source = {
        "library": "x",
        "group": "web_301_soap_dish",
        "file": "src.glb",
        "license": {},
        "retrieved_at": "2026-08-08",
        "source_manifest_path": None,
    }
    write_ledger(lib, "301_soap_dish", make_ledger("301_soap_dish", 0, reps, source))

    def fetch(url):
        raise AssertionError("fetch must not be called for an unrecoverable source")

    result = rfl.restore_library(lib, fetch_fn=fetch)
    assert not dest.is_file()
    assert len(result["unrecoverable"]) == 1
    assert result["restored"] == []


def test_restore_unrecoverable_when_uri_basename_not_in_manifest_files(tmp_path):
    """Derived representations (e.g. the sapien visual/collision glb, whose
    local filename differs from source.file) have no independent URL and
    must not be "restored" just because a source_manifest_path exists."""
    lib = tmp_path / "asset_library"
    manifest_path = _source_with_manifest(
        tmp_path, "https://example.com/assets", {"src.glb": "a" * 64}
    )
    dest = lib / "301_soap_dish" / "visual" / "base0.glb"
    reps = [rep(str(dest), "b" * 64, 10)]
    source = {
        "library": "x",
        "group": "web_301_soap_dish",
        "file": "src.glb",
        "license": {},
        "retrieved_at": "2026-08-08",
        "source_manifest_path": str(manifest_path),
    }
    write_ledger(lib, "301_soap_dish", make_ledger("301_soap_dish", 0, reps, source))

    def fetch(url):
        raise AssertionError(
            "fetch must not be called; base0.glb has no manifest entry"
        )

    result = rfl.restore_library(lib, fetch_fn=fetch)
    assert result["unrecoverable"] and result["restored"] == []


def test_restore_only_touches_missing_reps_not_ok_ones(tmp_path):
    lib = tmp_path / "asset_library"
    ok_bytes = b"already-here"
    ok_path = tmp_path / "ok.glb"
    ok_path.write_bytes(ok_bytes)
    missing_manifest = _source_with_manifest(
        tmp_path, "https://example.com/assets", {"missing.glb": _sha(b"m")}
    )
    missing_dest = lib / "301_soap_dish" / "_source" / "missing.glb"
    reps = [
        rep(str(ok_path), _sha(ok_bytes), len(ok_bytes)),
        rep(
            str(missing_dest),
            _sha(b"m"),
            1,
            backend="isaacsim",
            role="visual_and_collision",
            fmt="usd",
        ),
    ]
    source = {
        "library": "x",
        "group": "web_301_soap_dish",
        "file": "missing.glb",
        "license": {},
        "retrieved_at": "2026-08-08",
        "source_manifest_path": str(missing_manifest),
    }
    write_ledger(lib, "301_soap_dish", make_ledger("301_soap_dish", 0, reps, source))

    calls = []

    def fetch(url):
        calls.append(url)
        return b"m"

    rfl.restore_library(lib, fetch_fn=fetch)
    assert len(calls) == 1
    assert ok_path.read_bytes() == ok_bytes


def test_restore_scoped_to_asset_flag(tmp_path):
    lib = tmp_path / "asset_library"
    manifest_path = _source_with_manifest(
        tmp_path, "https://example.com/assets", {"src.glb": _sha(b"m")}
    )
    for asset in ("301_soap_dish", "302_mug"):
        dest = lib / asset / "_source" / "src.glb"
        reps = [
            rep(
                str(dest),
                _sha(b"m"),
                1,
                backend="isaacsim",
                role="visual_and_collision",
                fmt="usd",
            )
        ]
        source = {
            "library": "x",
            "group": f"web_{asset}",
            "file": "src.glb",
            "license": {},
            "retrieved_at": "2026-08-08",
            "source_manifest_path": str(manifest_path),
        }
        write_ledger(lib, asset, make_ledger(asset, 0, reps, source))

    result = rfl.restore_library(
        lib, asset_filter="301_soap_dish", fetch_fn=lambda url: b"m"
    )
    assert len(result["restored"]) == 1
    assert result["restored"][0]["asset"] == "301_soap_dish"
    assert not (lib / "302_mug" / "_source" / "src.glb").is_file()


def test_main_restore_prints_catalog_rebuild_hint(tmp_path, capsys):
    lib = tmp_path / "asset_library"
    manifest_path = _source_with_manifest(
        tmp_path, "https://example.com/assets", {"src.glb": _sha(b"m")}
    )
    dest = lib / "301_soap_dish" / "_source" / "src.glb"
    reps = [
        rep(
            str(dest),
            _sha(b"m"),
            1,
            backend="isaacsim",
            role="visual_and_collision",
            fmt="usd",
        )
    ]
    source = {
        "library": "x",
        "group": "web_301_soap_dish",
        "file": "src.glb",
        "license": {},
        "retrieved_at": "2026-08-08",
        "source_manifest_path": str(manifest_path),
    }
    write_ledger(lib, "301_soap_dish", make_ledger("301_soap_dish", 0, reps, source))

    rfl.main(["--library-dir", str(lib), "--restore"], fetch_fn=lambda url: b"m")
    out = capsys.readouterr().out
    assert "s9_build_shadow_root" in out
    assert dest.is_file()


def test_library_dir_defaults_from_dev_root(tmp_path):
    dev_root = tmp_path / "dev"
    (dev_root / "data" / "asset_library").mkdir(parents=True)
    resolved = rfl.resolve_library_dir(dev_root=str(dev_root), library_dir=None)
    assert resolved == dev_root / "data" / "asset_library"
