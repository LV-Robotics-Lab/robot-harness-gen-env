import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from scene_gen.catalog import AssetCatalog, CatalogEntry, CatalogModel

from .test_gen_fragment import _write, gen_fragment, make_valid

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "5_catalog" / "s9_build_shadow_root.py"
REPO = Path(__file__).resolve().parents[5]


def _catalog_entry(asset_id, asset_path, model_ids, *, ledger_document=None, primary_override=None):
    models = []
    for model_id in model_ids:
        values = {"model_id": model_id, "usable": True}
        if ledger_document is not None:
            model = next(item for item in ledger_document["models"] if item["model_id"] == model_id)
            primary = next(
                representation
                for representation in model["representations"]
                if representation["backend"] == "sapien" and representation["role"] != "snapshot"
            )["uri"]
            if primary_override is not None:
                primary = str(primary_override)
            pose = next(
                pose
                for pose in model["physical"]["conventions"]["stable_poses"]
                if pose.get("is_default") is True
            )
            values.update(
                model_path=str(Path(asset_path).resolve()),
                visual_path=primary,
                collision_path=primary,
                stable_pose_id=pose["pose_id"],
                stable_orientation_wxyz=pose["orientation_wxyz"],
                z_policy=model["physical"]["conventions"]["z_policy"],
            )
        models.append(CatalogModel(**values))
    return CatalogEntry(
        asset_id=asset_id,
        semantic_name=asset_id,
        category=asset_id.split("_", 1)[-1],
        aliases=(asset_id,),
        load_type="rigid",
        asset_path=str(Path(asset_path).resolve()),
        models=tuple(models),
        available=True,
    )


@pytest.mark.parametrize(
    ("copied_primary", "invalidate_during_scan"),
    [(False, False), (True, False), (False, True)],
)
def test_s9_links_only_hash_bound_library_assets_and_keeps_native_fallback(
    tmp_path, monkeypatch, copied_primary, invalidate_during_scan
):
    library = tmp_path / "library"
    provider = library / "github"
    qualified = make_valid()
    qualified["models"][0]["representations"][0].update(
        role="visual_and_collision",
        collision_meta={"mode": "explicit_mesh"},
    )
    second = json.loads(json.dumps(qualified["models"][0]))
    second["model_id"] = 1
    qualified["models"].append(second)
    qualified = _write(provider, "315_shears", qualified, trusted_model_ids={0})
    authoritative_primary = Path(qualified["models"][0]["representations"][0]["uri"])
    copied = provider / "315_shears" / "files" / "copied-primary.ply"
    copied.write_bytes(authoritative_primary.read_bytes())
    _write(provider, "316_native", make_valid(), trusted_model_ids=set())
    (provider / "317_lock_only").mkdir(parents=True)
    (provider / "317_lock_only" / "ledger.lock").write_text("")

    robotwin = tmp_path / "robotwin"
    upstream_native = robotwin / "assets" / "objects" / "316_native"
    upstream_native.mkdir(parents=True)
    shadow = tmp_path / "shadow"
    ext = tmp_path / "ext"
    overrides = tmp_path / "qualified.yml"
    fragment, _ = gen_fragment.generate(library)
    gen_fragment.write_yaml(fragment, overrides)

    def fake_run(command, **kwargs):
        if command[:3] == ["git", "-C", str(robotwin)]:
            return subprocess.CompletedProcess(command, 0, stdout="deadbeef\n", stderr="")
        assert command[1:4] == ["-m", "scene_gen.catalog", "--robotwin-root"]
        objects = shadow / "assets" / "objects"
        assert (objects / "315_shears").resolve() == (provider / "315_shears").resolve()
        assert (objects / "316_native").resolve() == upstream_native.resolve()
        assert not (objects / "317_lock_only").exists()
        if invalidate_during_scan:
            receipt = qualified["models"][0]["verification"][0]
            receipt.update(
                verdict="fail",
                run_id="newer-failed-settle",
                timestamp="2026-09-01T00:00:00",
            )
            _write(provider, "315_shears", qualified, trusted_model_ids={0})
        output = Path(command[command.index("--out") + 1])
        missing = Path(command[command.index("--missing-out") + 1])
        catalog = AssetCatalog(
            robotwin_root=str(shadow),
            objects_root=str(objects.resolve()),
            source_commit="deadbeef+ext301",
            entries=(
                _catalog_entry(
                    "315_shears",
                    provider / "315_shears",
                    (0, 1),
                    ledger_document=qualified,
                    primary_override=copied if copied_primary else None,
                ),
                _catalog_entry("316_native", upstream_native, (0,)),
            ),
        )
        output.write_text(json.dumps(catalog.canonical_dict()))
        missing.write_text("{}")
        return subprocess.CompletedProcess(command, 0, stdout="PASS fake scanner\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--robotwin-root",
            str(robotwin),
            "--library-dir",
            str(library),
            "--shadow",
            str(shadow),
            "--ext-dir",
            str(ext),
            "--upstream",
            str(REPO),
            "--extra-overrides",
            str(overrides),
        ],
    )

    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert stopped.value.code == (1 if copied_primary or invalidate_during_scan else 0)
    catalog = AssetCatalog.model_validate_json((ext / "asset_catalog.json").read_text())
    by_asset = {entry.asset_id: entry for entry in catalog.entries}
    if copied_primary or invalidate_during_scan:
        assert "315_shears" not in by_asset
        assert Path(by_asset["316_native"].asset_path).resolve() == upstream_native.resolve()
        return
    assert [model.model_id for model in by_asset["315_shears"].models] == [0]
    assert Path(by_asset["316_native"].asset_path).resolve() == upstream_native.resolve()
    missing = json.loads((ext / "missing_assets.json").read_text())
    assert missing == {
        "schema_version": "robotwin.asset_catalog_missing.v1",
        "asset_catalog_sha256": catalog.digest(),
        "entry_count": 2,
        "available_entry_count": 2,
        "unavailable_entry_count": 0,
        "entries": [],
    }
    external = AssetCatalog.model_validate_json(
        (ext / "asset_catalog_external_only.json").read_text()
    )
    assert [entry.asset_id for entry in external.entries] == ["315_shears"]
