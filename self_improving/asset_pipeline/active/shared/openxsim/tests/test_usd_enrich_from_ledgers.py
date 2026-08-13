"""enrich_from_ledgers: migration-side entry point that looks up an asset's
isaacsim USD representation from a per-asset ledger (spec §9,
docs/2026-08-08-asset-ingest-metadata-contract-design.md) instead of a
caller-supplied usd_lookup dict.

Fixtures are built with the real lib.ledger builders (upsert_model /
new_model_entry / write_ledger), not hand-written JSON, so they exercise the
same shape enrich_from_ledgers reads in production (backfill_upstream for
upstream ledgers, import_materialize for the external pool).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
OX = HERE.parents[1]  # shared/openxsim
DEV = HERE.parents[3]  # env-gen-dev
for _p in (
    OX / "source/agenticsim",
    OX / "deps/metasim_core",
    OX / "third_party/MetaSim",
    DEV / "2_sim_migration" / "lib",
    DEV / "1_asset_reuse",
):
    sys.path.insert(0, str(_p))

from agenticsim.openxsim.ir import (  # noqa: E402
    AssetBundle,
    AssetRepresentation,
    EnvironmentPackage,
    EnvSpec,
    SceneObject,
    TaskSpec,
)
from lib import ledger  # noqa: E402
from usd_enrich import _parse_ledger_asset_id, enrich_from_ledgers  # noqa: E402


def _pkg(asset_ids):
    """A minimal, valid EnvironmentPackage with one object per asset_id (in
    the given order), each carrying only a sapien representation -- mirrors
    what import_env_gen hands enrich_from_ledgers before Isaac enrichment."""
    assets = tuple(
        AssetBundle(
            asset_id=aid,
            category="object",
            representations=(
                AssetRepresentation(
                    format="glb", uri=f"sapien://{aid}", backend="sapien"
                ),
            ),
        )
        for aid in asset_ids
    )
    objects = tuple(
        SceneObject(instance_id=f"obj_{i}", asset_id=aid, scale=(2.0, 2.0, 2.0))
        for i, aid in enumerate(asset_ids)
    )
    pkg = EnvironmentPackage(
        package_id="test_pkg",
        env=EnvSpec(name="test_pkg", objects=objects),
        assets=assets,
        task=TaskSpec(
            instruction="test",
            intent="test",
            reset={},
            action={},
            observation={},
            plan=(),
            success=({"type": "always"},),
        ),
    )
    pkg.validate()
    return pkg


def _conventions():
    return {
        "is_static": False,
        "z_policy": "origin_on_table",
        "footprint_shape": "circle",
        "stable_poses": [
            {
                "pose_id": "upright",
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "is_default": True,
            }
        ],
        "inherited_from": None,
    }


def _sapien_rep():
    return {
        "format": "glb",
        "uri": "sapien://placeholder.glb",
        "backend": "sapien",
        "role": "visual_and_collision",
        "sha256": "a" * 64,
        "size_bytes": 1024,
        "metadata": {},
    }


def _isaac_rep(uri):
    return {
        "format": "usd",
        "uri": uri,
        "backend": "isaacsim",
        "role": "visual_and_collision",
        "sha256": "b" * 64,
        "size_bytes": 2048,
        "metadata": {"note": "pre-existing"},
    }


def _model_entry(model_id, representations, verification=()):
    return ledger.new_model_entry(
        model=model_id,
        representations=representations,
        mesh_bbox_m=[0.1, 0.1, 0.1],
        mesh_up_axis="Y",
        origin_convention="bottom-center",
        size_resolution={
            "mode": "match_category",
            "actual_max_dim_m": 0.1,
            "scale": 1.0,
            "reference_max_dim_m": 0.1,
            "reference_assets": [],
            "verdict": "ok",
        },
        conventions=_conventions(),
        source={
            "library": "test",
            "group": "test",
            "file": "test.glb",
            "license": {"spdx": None, "status": "unknown", "terms_note": "n/a"},
            "retrieved_at": "2026-08-10",
            "source_manifest_path": "/dev/null",
        },
        verification=list(verification),
    )


def _write_ledger(ledger_dir, asset, asset_id_prefix, model_entry):
    led = ledger.upsert_model(
        None,
        asset=asset,
        category="object",
        kind="rigid",
        profile="sapien_only",
        identity={"basis": "unknown", "evidence": None, "verified": False},
        aliases=[asset],
        colors=[],
        materials=[],
        tags=["test"],
        model_entry=model_entry,
        asset_id_prefix=asset_id_prefix,
    )
    path = ledger.ledger_path(ledger_dir, asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_ledger(path, led)
    return path


def test_parse_ledger_asset_id():
    assert _parse_ledger_asset_id("external_302_can_m3") == ("302_can", 3)
    assert _parse_ledger_asset_id("robotwin_071_can_m0") == ("071_can", 0)
    assert _parse_ledger_asset_id("302_can_m3") == ("302_can", 3)  # no prefix
    assert _parse_ledger_asset_id("302_can") == ("302_can", 0)  # bare dir name


def test_enrich_from_ledgers_classifies_and_registers(tmp_path):
    upstream_dir = tmp_path / "upstream_ledgers"
    pool_dir = tmp_path / "asset_library"

    # Asset A: found in upstream_ledgers, has an isaacsim rep + real file,
    # and a fresh (non-stale) isaacsim pass verification -> enriched, verified.
    usd_path = tmp_path / "071_can.usd"
    usd_path.write_text('#usda 1.0\ndef "root" {}\n')
    reps_a = [_sapien_rep(), _isaac_rep(str(usd_path))]
    model_a = _model_entry(0, reps_a)
    digest_a = ledger.reps_digest(model_a, "isaacsim")
    model_a["verification"] = [
        {
            "backend": "isaacsim",
            "check": "e2e",
            "verdict": "pass",
            "run_id": "run1",
            "timestamp": "2026-08-10T00:00:00",
            "verified_digest": digest_a,
            "report_path": "/dev/null",
        }
    ]
    _write_ledger(upstream_dir, "071_can", "robotwin", model_a)

    # Asset B: found in asset_library, only a sapien representation ->
    # skipped_no_isaac_rep.
    model_b = _model_entry(0, [_sapien_rep()])
    _write_ledger(pool_dir, "302_can", "external", model_b)

    # Asset C: not present in either ledger dir -> skipped_no_ledger.
    asset_ids = [
        "robotwin_071_can_m0",
        "external_302_can_m0",
        "external_999_missing_m0",
    ]
    pkg = _pkg(asset_ids)

    enriched, report = enrich_from_ledgers(pkg, [str(upstream_dir), str(pool_dir)])

    assert report["enriched"] == ["robotwin_071_can_m0"]
    assert report["skipped_no_isaac_rep"] == ["external_302_can_m0"]
    assert report["skipped_no_ledger"] == ["external_999_missing_m0"]
    assert report["warnings"] == []

    by_id = {a.asset_id: a for a in enriched.assets}
    rep = by_id["robotwin_071_can_m0"].representation_for("isaacsim", ("usd",))
    assert rep is not None
    assert rep.uri == str(usd_path)
    assert rep.metadata.get("verified") is True
    assert rep.metadata.get("note") == "pre-existing"  # original metadata preserved

    assert by_id["external_302_can_m0"].representation_for("isaacsim", ("usd",)) is None
    assert (
        by_id["external_999_missing_m0"].representation_for("isaacsim", ("usd",))
        is None
    )

    # scale neutralized only for the enriched object
    obj_by_asset = {o.asset_id: o for o in enriched.env.objects}
    assert obj_by_asset["robotwin_071_can_m0"].scale == (1.0, 1.0, 1.0)
    assert obj_by_asset["external_302_can_m0"].scale == (2.0, 2.0, 2.0)
    assert obj_by_asset["external_999_missing_m0"].scale == (2.0, 2.0, 2.0)


def test_enrich_from_ledgers_no_verification_does_not_set_verified_flag(tmp_path):
    upstream_dir = tmp_path / "upstream_ledgers"
    usd_path = tmp_path / "071_can.usd"
    usd_path.write_text('#usda 1.0\ndef "root" {}\n')
    model = _model_entry(
        0, [_sapien_rep(), _isaac_rep(str(usd_path))]
    )  # no verification[]
    _write_ledger(upstream_dir, "071_can", "robotwin", model)

    pkg = _pkg(["robotwin_071_can_m0"])
    enriched, report = enrich_from_ledgers(pkg, [str(upstream_dir)])

    assert report["enriched"] == ["robotwin_071_can_m0"]
    rep = enriched.assets[0].representation_for("isaacsim", ("usd",))
    assert "verified" not in rep.metadata


def test_enrich_from_ledgers_warns_on_missing_usd_file(tmp_path):
    upstream_dir = tmp_path / "upstream_ledgers"
    missing_usd = tmp_path / "does_not_exist.usd"  # never created
    model = _model_entry(0, [_sapien_rep(), _isaac_rep(str(missing_usd))])
    _write_ledger(upstream_dir, "071_can", "robotwin", model)

    pkg = _pkg(["robotwin_071_can_m0"])
    enriched, report = enrich_from_ledgers(pkg, [str(upstream_dir)])

    assert report["enriched"] == []
    assert report["skipped_no_ledger"] == []
    assert report["skipped_no_isaac_rep"] == []
    assert len(report["warnings"]) == 1
    assert "robotwin_071_can_m0" in report["warnings"][0]
    assert str(missing_usd) in report["warnings"][0]
    assert enriched.assets[0].representation_for("isaacsim", ("usd",)) is None
    assert enriched.env.objects[0].scale == (2.0, 2.0, 2.0)  # not neutralized


def test_enrich_from_ledgers_ledger_dir_order_first_match_wins(tmp_path):
    """Same asset directory name present in both dirs -- the earlier
    ledger_dirs entry wins (upstream before external pool, per spec §9)."""
    upstream_dir = tmp_path / "upstream_ledgers"
    pool_dir = tmp_path / "asset_library"

    usd_upstream = tmp_path / "upstream.usd"
    usd_upstream.write_text('#usda 1.0\ndef "root" {}\n')
    _write_ledger(
        upstream_dir,
        "071_can",
        "robotwin",
        _model_entry(0, [_sapien_rep(), _isaac_rep(str(usd_upstream))]),
    )

    usd_pool = tmp_path / "pool.usd"
    usd_pool.write_text('#usda 1.0\ndef "root" {}\n')
    _write_ledger(
        pool_dir,
        "071_can",
        "robotwin",
        _model_entry(0, [_sapien_rep(), _isaac_rep(str(usd_pool))]),
    )

    pkg = _pkg(["robotwin_071_can_m0"])
    enriched, report = enrich_from_ledgers(pkg, [str(upstream_dir), str(pool_dir)])

    assert report["enriched"] == ["robotwin_071_can_m0"]
    rep = enriched.assets[0].representation_for("isaacsim", ("usd",))
    assert rep.uri == str(usd_upstream)
