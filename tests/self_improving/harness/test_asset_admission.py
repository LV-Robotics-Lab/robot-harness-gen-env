from __future__ import annotations

import importlib
import json
import os
import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import self_improving.harness.assets as assets_module
from scene_gen import CompileRequest, compile_scene
from scene_gen.asset_generator import ensure_assets_for_scene
from scene_gen.catalog import AssetCatalog
from scene_gen.parser import parse_rule_based
from self_improving.harness.assets import AssetAdmissionError, GeneratedAssetAdmitter


def _empty_catalog(tmp_path: Path) -> Path:
    catalog = AssetCatalog(
        robotwin_root=str(tmp_path / "RoboTwin"),
        objects_root=str(tmp_path / "RoboTwin" / "assets" / "objects"),
        entries=(),
    )
    path = tmp_path / "empty_catalog.json"
    path.write_text(json.dumps(catalog.canonical_dict()), encoding="utf-8")
    return path


def _generated_fixture(tmp_path: Path):
    outcome = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        )
    )
    assert outcome.asset_generation_report is not None
    return outcome


def _catalog_with_local_block_meshes(tmp_path: Path) -> AssetCatalog:
    catalog = AssetCatalog.model_validate_json(
        (Path(__file__).resolve().parents[2] / "fixtures" / "asset_catalog.json").read_text(
            encoding="utf-8"
        )
    )
    asset_dir = tmp_path / "RoboTwin" / "assets" / "objects" / "004_fluted-block"
    visual = asset_dir / "visual" / "base0.glb"
    collision = asset_dir / "collision" / "base0.glb"
    visual.parent.mkdir(parents=True)
    collision.parent.mkdir(parents=True)
    visual.write_bytes(b"test visual mesh")
    collision.write_bytes(b"test collision mesh")
    metadata = asset_dir / "model_data0.json"
    metadata.write_text(
        json.dumps(
            {
                "extents": [0.20505566895008087, 0.16323167085647583, 0.20139166712760928],
                "scale": [0.45, 0.4, 0.45],
            }
        ),
        encoding="utf-8",
    )
    entries = []
    for entry in catalog.entries:
        if entry.asset_id != "004_fluted-block":
            entries.append(entry)
            continue
        model = entry.models[0].model_copy(
            update={
                "model_path": str(asset_dir),
                "metadata_path": str(metadata),
                "visual_path": str(visual),
                "collision_path": str(collision),
            }
        )
        entries.append(entry.model_copy(update={"asset_path": str(asset_dir), "models": (model,)}))
    return catalog.model_copy(
        update={
            "robotwin_root": str(tmp_path / "RoboTwin"),
            "objects_root": str(tmp_path / "RoboTwin" / "assets" / "objects"),
            "entries": tuple(entries),
        }
    )


def test_generated_asset_is_ledger_validated_and_atomically_admitted(
    tmp_path: Path,
) -> None:
    library = tmp_path / "asset_library"
    admitter = GeneratedAssetAdmitter(
        library_root=library,
        admission_date=date(2026, 8, 31),
    )

    outcome = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        ),
        asset_admitter=admitter,
    )

    assert outcome.asset_admission_report is not None
    assert outcome.asset_admission_report["status"] == "admitted"
    admitted = outcome.asset_admission_report["assets"][0]
    asset_dir = library / "generated" / admitted["asset_id"]
    ledger_path = asset_dir / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    contract = importlib.import_module(
        "self_improving.asset_pipeline.active.asset_reuse.lib.ledger"
    )

    assert contract.validate_ledger(ledger, check_files=True) == []
    assert ledger["schema_version"] == "asset_ledger.v3"
    assert ledger["external_ids"]["env_gen"] == admitted["asset_id"]
    model = ledger["models"][0]
    assert (
        model["physical"]["conventions"]["stable_poses"][0]["measured_against"]["backend"]
        == "portable"
    )
    assert all("runtime_default" not in json.dumps(value) for value in model["physical"].values())
    for representation in model["representations"]:
        assert "size_bytes" not in representation
        assert representation["files"]
        assert representation["files"] == sorted(
            representation["files"], key=lambda member: member["uri"]
        )
        assert all(set(member) == {"uri", "sha256", "bytes"} for member in representation["files"])
    assert any(
        representation.get("collision_meta", {}).get("mode") == "explicit_mesh"
        for representation in model["representations"]
    )
    assert model["verification"][0]["check"] == "generation_qc"
    assert (
        model["verification"][0]["report_sha256"]
        == model["source"]["generator"]["params"]["generation_provenance_sha256"]
    )
    assert contract.latest_verification(model, "sapien", "generation_qc") is not None
    assert Path(model["verification"][0]["report_path"]) == (
        asset_dir / "generation_provenance.json"
    )
    assert outcome.resolved_scene.objects[0].source_files[0].startswith(str(asset_dir))
    assert (
        next(
            check
            for check in outcome.static_validation["checks"]
            if check["name"].startswith("real_asset_files:")
        )["status"]
        == "pass"
    )
    assert not list((library / ".incoming").glob("*"))

    repeated = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "repeated_runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "repeated_staging",
        ),
        asset_admitter=admitter,
    )
    assert repeated.asset_admission_report is not None
    assert repeated.asset_admission_report["status"] == "reused"
    assert (
        repeated.asset_admission_report["assets"][0]["ledger_sha256"] == admitted["ledger_sha256"]
    )


def test_derived_scaled_provenance_shape_remains_admissible(tmp_path: Path) -> None:
    scene_spec = parse_rule_based("Place a red block on top of a plate.", seed=31)
    effective_catalog, generation_report = ensure_assets_for_scene(
        scene_spec,
        _catalog_with_local_block_meshes(tmp_path),
        objects_root=tmp_path / "generated-staging",
    )
    assert generation_report["generated"][0]["generation_kind"] == "derived_scaled_proxy"

    library = tmp_path / "asset_library"
    admitter = GeneratedAssetAdmitter(library, date(2026, 8, 31))
    _, admission_report = admitter.admit(
        scene_spec=scene_spec,
        asset_catalog=effective_catalog,
        generation_report=generation_report,
    )
    _, reuse_report = admitter.admit(
        scene_spec=scene_spec,
        asset_catalog=effective_catalog,
        generation_report=generation_report,
    )

    assert admission_report["status"] == "admitted"
    assert reuse_report["status"] == "reused"
    asset_id = generation_report["generated"][0]["asset_id"]
    installed = json.loads(
        (library / "generated" / asset_id / "generation_provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(not Path(record["path"]).is_absolute() for record in installed["files"].values())


def test_nested_asset_named_table_remains_admissible(tmp_path: Path) -> None:
    scene_spec = parse_rule_based("Place a red block on top of a plate.", seed=31)
    catalog = _catalog_with_local_block_meshes(tmp_path)
    catalog = catalog.model_copy(
        update={
            "entries": tuple(
                entry.model_copy(update={"asset_id": "table"})
                if entry.asset_id == "003_plate"
                else entry
                for entry in catalog.entries
            )
        }
    )
    effective_catalog, generation_report = ensure_assets_for_scene(
        scene_spec,
        catalog,
        objects_root=tmp_path / "generated-staging",
    )
    provenance = generation_report["generated"][0]
    assert provenance["compatibility"]["target_asset_id"] == "table"
    assert provenance["compatibility"]["target_model_id"] == 0

    _, admission_report = GeneratedAssetAdmitter(
        tmp_path / "asset_library", date(2026, 8, 31)
    ).admit(
        scene_spec=scene_spec,
        asset_catalog=effective_catalog,
        generation_report=generation_report,
    )

    assert admission_report["status"] == "admitted"


def test_admission_rejects_extra_derived_compatibility_locator(tmp_path: Path) -> None:
    scene_spec = parse_rule_based("Place a red block on top of a plate.", seed=31)
    effective_catalog, generation_report = ensure_assets_for_scene(
        scene_spec,
        _catalog_with_local_block_meshes(tmp_path),
        objects_root=tmp_path / "generated-staging",
    )
    provenance = generation_report["generated"][0]
    provenance["compatibility"]["staging_path"] = str(tmp_path / "hidden-stage")
    source = Path(
        next(
            entry.asset_path
            for entry in effective_catalog.entries
            if entry.asset_id == provenance["asset_id"]
        )
    )
    (source / "generation_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(AssetAdmissionError, match="invalid compatibility fields"):
        GeneratedAssetAdmitter(tmp_path / "asset_library", date(2026, 8, 31)).admit(
            scene_spec=scene_spec,
            asset_catalog=effective_catalog,
            generation_report=generation_report,
        )


def test_admission_rejects_absolute_locator_in_derived_semantic_list(tmp_path: Path) -> None:
    scene_spec = parse_rule_based("Place a red block on top of a plate.", seed=31)
    effective_catalog, generation_report = ensure_assets_for_scene(
        scene_spec,
        _catalog_with_local_block_meshes(tmp_path),
        objects_root=tmp_path / "generated-staging",
    )
    provenance = generation_report["generated"][0]
    provenance["aliases"] = [str(tmp_path / "hidden-staging")]
    source = Path(
        next(
            entry.asset_path
            for entry in effective_catalog.entries
            if entry.asset_id == provenance["asset_id"]
        )
    )
    (source / "generation_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(AssetAdmissionError, match="aliases"):
        GeneratedAssetAdmitter(tmp_path / "asset_library", date(2026, 8, 31)).admit(
            scene_spec=scene_spec,
            asset_catalog=effective_catalog,
            generation_report=generation_report,
        )


def test_admission_rejects_absolute_locator_in_derived_semantic_name(tmp_path: Path) -> None:
    scene_spec = parse_rule_based("Place a red block on top of a plate.", seed=31)
    effective_catalog, generation_report = ensure_assets_for_scene(
        scene_spec,
        _catalog_with_local_block_meshes(tmp_path),
        objects_root=tmp_path / "generated-staging",
    )
    provenance = generation_report["generated"][0]
    provenance["semantic_name"] = str(tmp_path / "hidden-staging")
    source = Path(
        next(
            entry.asset_path
            for entry in effective_catalog.entries
            if entry.asset_id == provenance["asset_id"]
        )
    )
    (source / "generation_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(AssetAdmissionError, match="semantic_name"):
        GeneratedAssetAdmitter(tmp_path / "asset_library", date(2026, 8, 31)).admit(
            scene_spec=scene_spec,
            asset_catalog=effective_catalog,
            generation_report=generation_report,
        )


def test_generated_ledger_uses_portable_uris_inside_the_active_asset_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = importlib.import_module(
        "self_improving.asset_pipeline.active.asset_reuse.lib.ledger"
    )
    active_root = tmp_path / "active"
    monkeypatch.setattr(contract, "ACTIVE_ROOT", active_root)
    library = active_root / "data" / "asset_library"
    outcome = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        ),
        asset_admitter=GeneratedAssetAdmitter(
            library_root=library,
            admission_date=date(2026, 8, 31),
        ),
    )
    assert outcome.asset_admission_report is not None
    asset_id = outcome.asset_admission_report["assets"][0]["asset_id"]
    ledger_path = library / "generated" / asset_id / "ledger.json"
    document = json.loads(ledger_path.read_text(encoding="utf-8"))
    model = document["models"][0]
    uris = [model["verification"][0]["report_path"]]
    for representation in model["representations"]:
        uris.append(representation["uri"])
        uris.extend(member["uri"] for member in representation["files"])

    assert all(not Path(uri).is_absolute() for uri in uris)
    assert all(contract.resolve_uri(uri).is_file() for uri in uris)
    assert contract.validate_ledger(document, check_files=True) == []


def test_admitted_generation_provenance_replaces_staging_paths_with_asset_relative_paths(
    tmp_path: Path,
) -> None:
    library = tmp_path / "asset_library"
    outcome = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        ),
        asset_admitter=GeneratedAssetAdmitter(
            library_root=library,
            admission_date=date(2026, 8, 31),
        ),
    )

    assert outcome.asset_admission_report is not None
    asset_id = outcome.asset_admission_report["assets"][0]["asset_id"]
    provenance = json.loads(
        (library / "generated" / asset_id / "generation_provenance.json").read_text(
            encoding="utf-8"
        )
    )

    assert {key: record["path"] for key, record in provenance["files"].items()} == {
        "collision": "collision/textured0.obj",
        "material": "visual/material.mtl",
        "metadata": "model_data0.json",
        "visual": "visual/textured0.obj",
    }


def test_reuse_rejects_existing_provenance_with_absolute_staging_locators(
    tmp_path: Path,
) -> None:
    base = _generated_fixture(tmp_path)
    library = tmp_path / "asset_library"
    admitter = GeneratedAssetAdmitter(library, date(2026, 8, 31))
    assert base.asset_generation_report is not None
    admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )
    entry = base.asset_catalog.entries[0]
    destination = library / "generated" / entry.asset_id
    provenance_path = destination / "generation_provenance.json"
    installed = json.loads(provenance_path.read_text(encoding="utf-8"))
    for record in installed["files"].values():
        record["path"] = str(tmp_path / "deleted-staging" / record["path"])
    provenance_path.write_text(
        json.dumps(installed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    contract = importlib.import_module(
        "self_improving.asset_pipeline.active.asset_reuse.lib.ledger"
    )
    legacy_ledger = assets_module._build_ledger(
        scene_spec=base.scene_spec,
        entry=entry,
        provenance=installed,
        destination=destination,
        source_root=destination,
        admission_date=date(2026, 8, 31),
        contract=contract,
    )
    (destination / "ledger.json").write_text(
        json.dumps(legacy_ledger, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert contract.validate_ledger(legacy_ledger, check_files=True) == []

    with pytest.raises(AssetAdmissionError, match="path mismatch"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )


def test_admission_rejects_unbound_extra_provenance_file_identity(tmp_path: Path) -> None:
    base = _generated_fixture(tmp_path)
    entry = base.asset_catalog.entries[0]
    source = Path(entry.asset_path)
    provenance = json.loads(json.dumps(base.asset_generation_report["generated"][0]))
    provenance["files"]["debug"] = {
        "path": str(tmp_path / "outside-debug.bin"),
        "sha256": "0" * 64,
    }
    (source / "generation_provenance.json").write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )
    report = json.loads(json.dumps(base.asset_generation_report))
    report["generated"][0] = provenance

    with pytest.raises(AssetAdmissionError, match="unexpected payload identities"):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=report,
        )


def test_admission_rejects_unbound_fields_inside_provenance_file_identity(
    tmp_path: Path,
) -> None:
    base = _generated_fixture(tmp_path)
    entry = base.asset_catalog.entries[0]
    source = Path(entry.asset_path)
    provenance = json.loads(json.dumps(base.asset_generation_report["generated"][0]))
    provenance["files"]["visual"]["staging_path"] = str(tmp_path / "hidden-stage.obj")
    (source / "generation_provenance.json").write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )
    report = json.loads(json.dumps(base.asset_generation_report))
    report["generated"][0] = provenance

    with pytest.raises(AssetAdmissionError, match="unexpected fields"):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=report,
        )


def test_admission_rejects_unbound_top_level_provenance_locator(tmp_path: Path) -> None:
    base = _generated_fixture(tmp_path)
    entry = base.asset_catalog.entries[0]
    source = Path(entry.asset_path)
    provenance = json.loads(json.dumps(base.asset_generation_report["generated"][0]))
    provenance["staging_path"] = str(tmp_path / "hidden-stage")
    (source / "generation_provenance.json").write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )
    report = json.loads(json.dumps(base.asset_generation_report))
    report["generated"][0] = provenance

    with pytest.raises(AssetAdmissionError, match="unexpected provenance fields"):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=report,
        )


def test_admission_rejects_nested_locator_in_typed_provenance_field(tmp_path: Path) -> None:
    base = _generated_fixture(tmp_path)
    entry = base.asset_catalog.entries[0]
    source = Path(entry.asset_path)
    provenance = json.loads(json.dumps(base.asset_generation_report["generated"][0]))
    provenance["requested_material"] = {"staging_path": str(tmp_path / "hidden-stage")}
    (source / "generation_provenance.json").write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )
    report = json.loads(json.dumps(base.asset_generation_report))
    report["generated"][0] = provenance

    with pytest.raises(AssetAdmissionError, match="requested_material"):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=report,
        )


def test_admission_rechecks_provenance_digests_after_the_input_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _generated_fixture(tmp_path)
    source = Path(base.asset_catalog.entries[0].asset_path)
    real_tree_manifest = assets_module._tree_manifest
    source_reads = 0

    def drift_before_staging_manifest(root: Path, *args, **kwargs):
        nonlocal source_reads
        if Path(root) == source:
            source_reads += 1
            if source_reads == 2:
                visual = source / "visual" / "textured0.obj"
                visual.write_text(
                    visual.read_text(encoding="utf-8") + "\n# drift after input gate\n",
                    encoding="utf-8",
                )
        return real_tree_manifest(root, *args, **kwargs)

    monkeypatch.setattr(assets_module, "_tree_manifest", drift_before_staging_manifest)

    with pytest.raises(AssetAdmissionError, match="visual digest mismatch"):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )


@pytest.mark.parametrize("attack", ["provenance-tamper", "unexpected-file"])
def test_admission_rechecks_the_complete_private_tree_after_the_input_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    base = _generated_fixture(tmp_path)
    real_tree_manifest = assets_module._tree_manifest
    private_reads = 0

    def mutate_before_second_private_snapshot(root: Path, *args, **kwargs):
        nonlocal private_reads
        resolved = Path(root).resolve()
        if ".incoming" in resolved.parts:
            private_reads += 1
            if private_reads == 2:
                if attack == "provenance-tamper":
                    (resolved / "generation_provenance.json").write_text(
                        '{"tampered":true}\n', encoding="utf-8"
                    )
                else:
                    (resolved / "unexpected.bin").write_bytes(b"unbound")
        return real_tree_manifest(root, *args, **kwargs)

    monkeypatch.setattr(assets_module, "_tree_manifest", mutate_before_second_private_snapshot)

    with pytest.raises(AssetAdmissionError, match="provenance changed|missing or unexpected files"):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )


def test_admission_reports_noop_mixed_and_missing_catalog_entry(tmp_path: Path) -> None:
    admitter = GeneratedAssetAdmitter(
        library_root=tmp_path / "asset_library",
        admission_date=date(2026, 8, 31),
    )
    base = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        )
    )
    assert base.asset_generation_report is not None

    unchanged, noop = admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report={
            "schema_version": "robotwin.asset_generation_report.v1",
            "scene_id": base.scene_spec.scene_id,
            "generated": [],
        },
    )
    assert noop["status"] == "not_needed"
    assert unchanged.entries == base.asset_catalog.entries

    first_catalog, first = admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )
    assert first["status"] == "admitted"
    original_entry = base.asset_catalog.entries[0]
    copied_id = f"{original_entry.asset_id}_copy"
    copied_root = tmp_path / "copy_staging" / copied_id
    shutil.copytree(Path(original_entry.asset_path), copied_root)
    copied_model = original_entry.models[0].model_copy(
        update={
            "model_path": str(copied_root),
            "metadata_path": str(copied_root / "model_data0.json"),
            "visual_path": str(copied_root / "visual" / "textured0.obj"),
            "collision_path": str(copied_root / "collision" / "textured0.obj"),
        }
    )
    copied_entry = original_entry.model_copy(
        update={
            "asset_id": copied_id,
            "asset_path": str(copied_root),
            "models": (copied_model,),
        }
    )
    two_entries = base.asset_catalog.model_copy(update={"entries": (original_entry, copied_entry)})
    copied_provenance = dict(base.asset_generation_report["generated"][0])
    copied_provenance["asset_id"] = copied_id
    with pytest.raises(AssetAdmissionError, match="escapes|installed provenance|does not match"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=two_entries,
            generation_report={
                **base.asset_generation_report,
                "generated": [
                    base.asset_generation_report["generated"][0],
                    copied_provenance,
                ],
            },
        )
    assert first_catalog.entries[0].asset_path.startswith(str(tmp_path / "asset_library"))

    with pytest.raises(AssetAdmissionError, match="catalog entry is missing"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report={
                **base.asset_generation_report,
                "generated": [{"asset_id": "missing"}],
            },
        )


def test_reuse_ignores_ledger_lock_when_generated_payload_is_otherwise_identical(
    tmp_path: Path,
) -> None:
    base = _generated_fixture(tmp_path)
    admitter = GeneratedAssetAdmitter(
        library_root=tmp_path / "asset_library",
        admission_date=date(2026, 8, 31),
    )
    assert base.asset_generation_report is not None
    admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    destination = tmp_path / "asset_library" / "generated" / asset_id
    (destination / "ledger.lock").write_text("advisory lock\n", encoding="utf-8")

    _, report = admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )

    assert report["status"] == "reused"


def test_reuse_rejects_destination_replaced_after_existing_asset_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _generated_fixture(tmp_path)
    library = tmp_path / "asset_library"
    admitter = GeneratedAssetAdmitter(library, date(2026, 8, 31))
    assert base.asset_generation_report is not None
    admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    destination = library / "generated" / asset_id
    displaced = library / "verified-but-displaced"
    real_verify_existing = assets_module._verify_existing

    def replace_after_verification(**kwargs):
        verified = real_verify_existing(**kwargs)
        destination.rename(displaced)
        destination.mkdir()
        (destination / "ledger.json").write_text('{"unvalidated":true}\n', encoding="utf-8")
        return verified

    monkeypatch.setattr(assets_module, "_verify_existing", replace_after_verification)

    with pytest.raises(AssetAdmissionError, match="changed during admission"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )

    assert displaced.is_dir()
    assert json.loads((destination / "ledger.json").read_text()) == {"unvalidated": True}


def test_reuse_rejects_payload_changed_after_existing_asset_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _generated_fixture(tmp_path)
    library = tmp_path / "asset_library"
    admitter = GeneratedAssetAdmitter(library, date(2026, 8, 31))
    assert base.asset_generation_report is not None
    admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    destination = library / "generated" / asset_id
    visual = destination / "visual" / "textured0.obj"
    real_verify_existing = assets_module._verify_existing

    def mutate_after_verification(**kwargs):
        verified = real_verify_existing(**kwargs)
        visual.write_text("mutated-after-reuse-gate\n", encoding="utf-8")
        return verified

    monkeypatch.setattr(assets_module, "_verify_existing", mutate_after_verification)

    with pytest.raises(AssetAdmissionError, match="changed after its full file gate"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )


def test_reuse_preserves_followup_receipts_but_still_requires_original_generation_qc(
    tmp_path: Path,
) -> None:
    base = _generated_fixture(tmp_path)
    admitter = GeneratedAssetAdmitter(
        library_root=tmp_path / "asset_library",
        admission_date=date(2026, 8, 31),
    )
    assert base.asset_generation_report is not None
    admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    destination = tmp_path / "asset_library" / "generated" / asset_id
    ledger_path = destination / "ledger.json"
    contract = importlib.import_module(
        "self_improving.asset_pipeline.active.asset_reuse.lib.ledger"
    )
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    model = ledger["models"][0]
    generation_qc = json.loads(json.dumps(model["verification"][0]))
    followup = {
        "backend": "sapien",
        "check": "settle",
        "verdict": "pass",
        "run_id": "settle-1",
        "timestamp": "2026-08-31T00:00:01",
        "verified_digest": contract.reps_digest(model, "sapien"),
    }
    contract.append_verification(ledger_path, model["model_id"], followup)

    _, report = admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )

    assert report["status"] == "reused"
    reused = json.loads(ledger_path.read_text(encoding="utf-8"))["models"][0]["verification"]
    assert generation_qc in reused
    assert followup in reused


def test_reuse_rejects_generation_qc_drift_even_when_followup_receipts_are_valid(
    tmp_path: Path,
) -> None:
    base = _generated_fixture(tmp_path)
    admitter = GeneratedAssetAdmitter(
        library_root=tmp_path / "asset_library",
        admission_date=date(2026, 8, 31),
    )
    assert base.asset_generation_report is not None
    admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    destination = tmp_path / "asset_library" / "generated" / asset_id
    ledger_path = destination / "ledger.json"
    contract = importlib.import_module(
        "self_improving.asset_pipeline.active.asset_reuse.lib.ledger"
    )
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    model = ledger["models"][0]
    contract.append_verification(
        ledger_path,
        model["model_id"],
        {
            "backend": "sapien",
            "check": "settle",
            "verdict": "pass",
            "run_id": "settle-1",
            "timestamp": "2026-08-31T00:00:01",
            "verified_digest": contract.reps_digest(model, "sapien"),
        },
    )
    tampered = json.loads(ledger_path.read_text(encoding="utf-8"))
    tampered["models"][0]["verification"][0]["report_sha256"] = "0" * 64
    contract.write_ledger(ledger_path, tampered)

    with pytest.raises(AssetAdmissionError, match="generation_qc does not match"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )


def test_ledger_without_verification_only_rewrites_mapping_models() -> None:
    document = {
        "models": [
            {"model_id": 0, "verification": [{"check": "generation_qc"}]},
            "opaque-model",
        ]
    }

    normalized = assets_module._ledger_without_verification(document)

    assert normalized == {
        "models": [
            {"model_id": 0, "verification": []},
            "opaque-model",
        ]
    }
    assert document["models"][0]["verification"] == [{"check": "generation_qc"}]


def test_reuse_fails_closed_for_missing_tampered_or_wrong_identity_ledger(
    tmp_path: Path,
) -> None:
    admitter = GeneratedAssetAdmitter(
        library_root=tmp_path / "asset_library",
        admission_date=date(2026, 8, 31),
    )
    base = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        )
    )
    assert base.asset_generation_report is not None
    admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    destination = tmp_path / "asset_library" / "generated" / asset_id
    ledger_path = destination / "ledger.json"
    ledger_text = ledger_path.read_text(encoding="utf-8")

    ledger_path.unlink()
    with pytest.raises(AssetAdmissionError, match="no ledger"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )
    ledger_path.write_text(ledger_text, encoding="utf-8")

    visual = destination / "visual" / "textured0.obj"
    visual_text = visual.read_text(encoding="utf-8")
    visual.write_text("tampered", encoding="utf-8")
    with pytest.raises(AssetAdmissionError, match="sha256_mismatch|file_missing"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )
    visual.write_text(visual_text, encoding="utf-8")

    ledger = json.loads(ledger_text)
    ledger["external_ids"]["env_gen"] = "other_asset"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(AssetAdmissionError, match="identity does not match"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )


@pytest.mark.parametrize("fail_after_promotion", [False, True])
def test_failed_admission_never_leaves_an_unvalidated_asset_in_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_after_promotion: bool,
) -> None:
    base = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        )
    )
    assert base.asset_generation_report is not None
    real_contract = importlib.import_module(
        "self_improving.asset_pipeline.active.asset_reuse.lib.ledger"
    )
    violation = SimpleNamespace(path="models.0", code="forced_failure")

    class FailingContract:
        def __init__(self) -> None:
            self.calls = 0

        def __getattr__(self, name: str):
            return getattr(real_contract, name)

        def validate_ledger(self, ledger, *, check_files):
            self.calls += 1
            if (fail_after_promotion and self.calls == 2) or (
                not fail_after_promotion and self.calls == 1
            ):
                return [violation]
            return []

    monkeypatch.setattr(assets_module, "_ledger_contract", lambda: FailingContract())
    library = tmp_path / "asset_library"
    admitter = GeneratedAssetAdmitter(
        library_root=library,
        admission_date=date(2026, 8, 31),
    )

    with pytest.raises(AssetAdmissionError, match="forced_failure"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )

    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    assert not (library / "generated" / asset_id).exists()
    assert not list((library / ".incoming").glob("*"))
    assert not (library / ".rejected" / asset_id).exists()


def test_complete_file_gate_runs_before_destination_becomes_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        )
    )
    assert base.asset_generation_report is not None
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    library = tmp_path / "asset_library"
    destination = library / "generated" / asset_id
    real_contract = importlib.import_module(
        "self_improving.asset_pipeline.active.asset_reuse.lib.ledger"
    )
    observations = []

    class ObservingContract:
        def __getattr__(self, name: str):
            return getattr(real_contract, name)

        def validate_ledger(self, ledger, *, check_files):
            if check_files:
                observations.append(destination.exists())
            return real_contract.validate_ledger(ledger, check_files=check_files)

    monkeypatch.setattr(assets_module, "_ledger_contract", lambda: ObservingContract())
    admitter = GeneratedAssetAdmitter(
        library_root=library,
        admission_date=date(2026, 8, 31),
    )

    _, report = admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )

    assert report["status"] == "admitted"
    assert observations == [False]
    assert destination.is_dir()


@pytest.mark.parametrize("symlink_component", ["library", "generated", "incoming"])
def test_admission_rejects_symlinked_library_components(
    tmp_path: Path,
    symlink_component: str,
) -> None:
    base = _generated_fixture(tmp_path)
    library = tmp_path / "library"
    outside = tmp_path / "outside"
    outside.mkdir()
    if symlink_component == "library":
        library.symlink_to(outside, target_is_directory=True)
    else:
        library.mkdir()
        (library / ("generated" if symlink_component == "generated" else ".incoming")).symlink_to(
            outside,
            target_is_directory=True,
        )

    with pytest.raises(AssetAdmissionError, match="symlink|safe directory"):
        GeneratedAssetAdmitter(library, date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )

    assert not any(outside.iterdir())


def test_admission_rejects_generated_directory_swap_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _generated_fixture(tmp_path)
    library = tmp_path / "library"
    outside = tmp_path / "outside"
    outside.mkdir()
    real_relocate = assets_module._relocate_ledger

    def swap_generated_parent(document, source, destination, contract):
        relocated = real_relocate(document, source, destination, contract)
        generated = destination.parent
        generated.mkdir(parents=True, exist_ok=True)
        generated.rename(library / "displaced-generated")
        generated.symlink_to(outside, target_is_directory=True)
        return relocated

    monkeypatch.setattr(assets_module, "_relocate_ledger", swap_generated_parent)

    with pytest.raises(AssetAdmissionError, match="changed|safe directory|symlink"):
        GeneratedAssetAdmitter(library, date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )

    assert not any(outside.iterdir())
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    assert not (library / "displaced-generated" / asset_id).exists()


def test_mutation_after_full_file_gate_is_rejected_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        )
    )
    assert base.asset_generation_report is not None
    real_contract = importlib.import_module(
        "self_improving.asset_pipeline.active.asset_reuse.lib.ledger"
    )
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    library = tmp_path / "asset_library"
    destination = library / "generated" / asset_id

    class MutatingContract:
        def __getattr__(self, name: str):
            return getattr(real_contract, name)

        def validate_ledger(self, document, *, check_files):
            result = real_contract.validate_ledger(document, check_files=check_files)
            if check_files and not result:
                visual = next(
                    representation
                    for representation in document["models"][0]["representations"]
                    if representation["role"] == "visual"
                )
                Path(visual["uri"]).write_text("mutated-after-gate", encoding="utf-8")
            return result

    monkeypatch.setattr(assets_module, "_ledger_contract", lambda: MutatingContract())

    with pytest.raises(AssetAdmissionError, match="changed after its full file gate"):
        GeneratedAssetAdmitter(library, date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )

    assert not destination.exists()
    assert not list((library / ".incoming").glob("*"))


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "../escape",
        "bad/name",
        "bad\\name",
        "bad\x00id",
        "x" * 129,
        "a:b",
        "CON",
        "name.",
        "space name",
        "资产",
    ],
)
def test_generated_asset_ids_are_safe_bounded_basenames(tmp_path: Path, unsafe_id: str) -> None:
    base = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        )
    )
    assert base.asset_generation_report is not None
    entry = base.asset_catalog.entries[0].model_copy(update={"asset_id": unsafe_id})
    catalog = base.asset_catalog.model_copy(update={"entries": (entry,)})
    report = {
        **base.asset_generation_report,
        "generated": [{**base.asset_generation_report["generated"][0], "asset_id": unsafe_id}],
    }

    with pytest.raises(AssetAdmissionError, match="unsafe asset_id"):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=catalog,
            generation_report=report,
        )


def test_duplicate_catalog_and_generation_ids_are_rejected(tmp_path: Path) -> None:
    base = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        )
    )
    assert base.asset_generation_report is not None
    entry = base.asset_catalog.entries[0]
    admitter = GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31))

    with pytest.raises(AssetAdmissionError, match="catalog duplicates"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog.model_copy(update={"entries": (entry, entry)}),
            generation_report=base.asset_generation_report,
        )
    with pytest.raises(AssetAdmissionError, match="report duplicates"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report={
                **base.asset_generation_report,
                "generated": base.asset_generation_report["generated"] * 2,
            },
        )


def test_reuse_rejects_metadata_provenance_and_catalog_dimension_drift(tmp_path: Path) -> None:
    base = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=_empty_catalog(tmp_path),
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=tmp_path / "staging",
        )
    )
    assert base.asset_generation_report is not None
    library = tmp_path / "library"
    admitter = GeneratedAssetAdmitter(library, date(2026, 8, 31))
    admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    destination = library / "generated" / asset_id
    metadata = destination / "model_data0.json"
    original_metadata = metadata.read_text(encoding="utf-8")
    metadata.write_text('{"extents":[9,9,9]}\n', encoding="utf-8")
    with pytest.raises(AssetAdmissionError, match="metadata digest mismatch"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )
    metadata.write_text(original_metadata, encoding="utf-8")

    extra = destination / "unexpected.bin"
    extra.write_bytes(b"unexpected")
    with pytest.raises(AssetAdmissionError, match="payload does not match"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )
    extra.unlink()

    installed = destination / "generation_provenance.json"
    provenance_text = installed.read_text(encoding="utf-8")
    provenance = json.loads(provenance_text)
    provenance["semantic_category"] = "tampered"
    installed.write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(AssetAdmissionError, match="provenance does not match"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )
    installed.write_text(provenance_text, encoding="utf-8")

    entry = base.asset_catalog.entries[0]
    changed_model = entry.models[0].model_copy(update={"dimensions_m": (9.0, 9.0, 9.0)})
    changed_catalog = base.asset_catalog.model_copy(
        update={"entries": (entry.model_copy(update={"models": (changed_model,)}),)}
    )
    with pytest.raises(AssetAdmissionError, match="dimensions mismatch"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=changed_catalog,
            generation_report=base.asset_generation_report,
        )


@pytest.mark.parametrize("attack", ["schema", "scene", "shape", "missing_id"])
def test_generation_report_contract_is_fail_closed(tmp_path: Path, attack: str) -> None:
    base = _generated_fixture(tmp_path)
    report = dict(base.asset_generation_report)
    if attack == "schema":
        report["schema_version"] = "unknown"
    elif attack == "scene":
        report["scene_id"] = "different"
    elif attack == "shape":
        report["generated"] = ["not-an-object"]
    else:
        report["generated"] = [{}]

    with pytest.raises(AssetAdmissionError):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=report,
        )


@pytest.mark.parametrize(
    "attack",
    [
        "provenance_schema",
        "provenance_asset",
        "source_not_directory",
        "source_symlink",
        "model_count",
        "model_path",
        "installed_drift",
        "unexpected_file",
        "files_shape",
        "missing_identity",
        "identity_path",
        "identity_digest",
    ],
)
def test_generation_input_contract_rejects_unbound_evidence(tmp_path: Path, attack: str) -> None:
    base = _generated_fixture(tmp_path)
    entry = base.asset_catalog.entries[0]
    provenance = json.loads(json.dumps(base.asset_generation_report["generated"][0]))
    source = Path(entry.asset_path)
    if attack == "provenance_schema":
        provenance["schema_version"] = "unknown"
    elif attack == "provenance_asset":
        provenance["asset_id"] = "different"
    elif attack == "source_not_directory":
        shutil.rmtree(source)
        source.write_bytes(b"not-a-directory")
    elif attack == "source_symlink":
        relocated = source.parent / "nested" / source.name
        relocated.parent.mkdir()
        source.rename(relocated)
        source.symlink_to(relocated, target_is_directory=True)
    elif attack == "model_count":
        entry = entry.model_copy(update={"models": ()})
    elif attack == "model_path":
        model = entry.models[0].model_copy(update={"model_path": str(tmp_path)})
        entry = entry.model_copy(update={"models": (model,)})
    elif attack == "installed_drift":
        provenance["requested_color"] = "orange"
    elif attack == "unexpected_file":
        (source / "unexpected.bin").write_bytes(b"unexpected")
    elif attack == "files_shape":
        provenance["files"] = []
        (source / "generation_provenance.json").write_text(json.dumps(provenance))
    elif attack == "missing_identity":
        provenance["files"].pop("metadata")
        (source / "generation_provenance.json").write_text(json.dumps(provenance))
    elif attack == "identity_path":
        provenance["files"]["metadata"]["path"] = str(tmp_path / "outside.json")
        (source / "generation_provenance.json").write_text(json.dumps(provenance))
    elif attack == "identity_digest":
        provenance["files"]["metadata"]["sha256"] = "0" * 64
        (source / "generation_provenance.json").write_text(json.dumps(provenance))

    with pytest.raises(AssetAdmissionError):
        assets_module._validate_generation_inputs(
            scene_spec=base.scene_spec,
            entry=entry,
            provenance=provenance,
            catalog_root=Path(base.asset_catalog.objects_root),
        )


def test_generated_evidence_and_tree_shape_helpers_fail_closed(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{")
    with pytest.raises(AssetAdmissionError, match="invalid generated evidence"):
        assets_module._load_json_object(invalid)
    invalid.write_text("[]")
    with pytest.raises(AssetAdmissionError, match="not an object"):
        assets_module._load_json_object(invalid)

    tree = tmp_path / "tree"
    tree.mkdir()
    target = tree / "target"
    target.write_bytes(b"target")
    (tree / "link").symlink_to(target)
    with pytest.raises(AssetAdmissionError, match="symlink"):
        assets_module._tree_manifest(tree)
    (tree / "link").unlink()
    fifo = tree / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(AssetAdmissionError, match="non-file"):
        assets_module._tree_manifest(tree)

    assert assets_module._provenance_identity({"files": []}) == {"files": []}
    assert assets_module._provenance_identity(
        {"files": {"opaque": "value", "record": {"path": "/tmp/file.obj"}}}
    ) == {"files": {"opaque": "value", "record": {"path": "file.obj"}}}


def test_copy_and_post_publish_drift_never_leave_selectable_asset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _generated_fixture(tmp_path)
    library = tmp_path / "library"
    real_copytree = shutil.copytree

    def drifting_copytree(source, destination, *args, **kwargs):
        result = real_copytree(source, destination, *args, **kwargs)
        if Path(destination).resolve().parent.name == ".incoming":
            (Path(destination) / "model_data0.json").write_text("{}")
        return result

    monkeypatch.setattr(assets_module.shutil, "copytree", drifting_copytree)
    with pytest.raises(AssetAdmissionError, match="changed while it was staged"):
        GeneratedAssetAdmitter(library, date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )

    monkeypatch.setattr(assets_module.shutil, "copytree", real_copytree)
    real_require = assets_module._require_tree_manifest
    calls = 0

    def fail_post_publish(root, expected, *, ignore=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AssetAdmissionError("post-publish drift")
        return real_require(root, expected, ignore=ignore)

    monkeypatch.setattr(assets_module, "_require_tree_manifest", fail_post_publish)
    with pytest.raises(AssetAdmissionError, match="post-publish drift"):
        GeneratedAssetAdmitter(library, date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    assert not (library / "generated" / asset_id).exists()


def test_publish_rejects_destination_replaced_after_atomic_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _generated_fixture(tmp_path)
    library = tmp_path / "library"
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    real_rename = assets_module.os.rename
    swapped = False

    def replace_published_child(source, destination, *args, **kwargs):
        nonlocal swapped
        result = real_rename(source, destination, *args, **kwargs)
        if destination == asset_id and not swapped:
            swapped = True
            generated_fd = kwargs["dst_dir_fd"]
            real_rename(
                asset_id,
                f".{asset_id}.relocated",
                src_dir_fd=generated_fd,
                dst_dir_fd=generated_fd,
            )
            os.mkdir(asset_id, 0o755, dir_fd=generated_fd)
            attacker = Path(f"/proc/self/fd/{generated_fd}") / asset_id
            (attacker / "ledger.json").write_text('{"attacker":true}')
        return result

    monkeypatch.setattr(assets_module.os, "rename", replace_published_child)

    with pytest.raises(AssetAdmissionError, match="published destination identity changed"):
        GeneratedAssetAdmitter(library, date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )

    assert swapped is True


def test_reuse_rejects_structurally_valid_ledger_metadata_drift(tmp_path: Path) -> None:
    base = _generated_fixture(tmp_path)
    library = tmp_path / "library"
    admitter = GeneratedAssetAdmitter(library, date(2026, 8, 31))
    admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=base.asset_catalog,
        generation_report=base.asset_generation_report,
    )
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    ledger_path = library / "generated" / asset_id / "ledger.json"
    document = json.loads(ledger_path.read_text())
    document["semantics"]["identity"]["evidence"] = "unrelated.json"
    ledger_path.write_text(json.dumps(document))

    with pytest.raises(AssetAdmissionError, match="ledger does not match"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("generation_kind", 1, "unsupported generation provenance kind"),
        ("generation_kind", "unknown", "unsupported generation provenance kind"),
        ("generator", "other", "generation provenance generator mismatch"),
        ("semantic_category", "", "semantic_category must be a non-empty string"),
        ("semantic_category", "Not-A-Token", "semantic_category is invalid"),
        ("geometry_family", "sphere", "geometry_family is invalid"),
        ("geometry_fidelity", "other", "geometry_fidelity mismatch"),
        ("generated_license", "unknown", "generated_license mismatch"),
        ("dimensions_m", [1.0, 2.0], "dimensions_m must have three values"),
        ("dimensions_m", [1.0, "2", 3.0], "dimensions_m must be finite"),
        ("dimensions_m", [1.0, 10**10_000, 3.0], "dimensions_m must be finite"),
        ("dimensions_m", [1.0, float("nan"), 3.0], "dimensions_m must be finite"),
        ("dimensions_m", [1.0, 0.0, 3.0], "dimensions_m must be positive"),
    ),
)
def test_admission_rejects_each_invalid_common_provenance_value(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    base = _generated_fixture(tmp_path)
    provenance = dict(base.asset_generation_report["generated"][0])
    provenance[field] = value
    report = {**base.asset_generation_report, "generated": [provenance]}

    with pytest.raises(AssetAdmissionError, match=message):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=report,
        )


def test_admission_requires_an_exact_provenance_object(tmp_path: Path) -> None:
    base = _generated_fixture(tmp_path)

    class ProvenanceSubclass(dict):
        pass

    provenance = ProvenanceSubclass(base.asset_generation_report["generated"][0])
    report = {**base.asset_generation_report, "generated": [provenance]}

    with pytest.raises(AssetAdmissionError, match="must be an exact object"):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=report,
        )


def test_provenance_shape_rejects_a_wrong_schema_after_dispatch(tmp_path: Path) -> None:
    provenance = dict(_generated_fixture(tmp_path).asset_generation_report["generated"][0])
    provenance["schema_version"] = "unknown"

    with pytest.raises(AssetAdmissionError, match="unsupported generation provenance schema"):
        assets_module._require_provenance_shape(provenance)


@pytest.mark.parametrize(
    ("member", "value", "message"),
    (
        ("path", "", "visual path mismatch"),
        ("sha256", "not-a-digest", "visual digest is invalid"),
    ),
)
def test_admission_rejects_malformed_payload_identity_values(
    tmp_path: Path,
    member: str,
    value: str,
    message: str,
) -> None:
    base = _generated_fixture(tmp_path)
    provenance = json.loads(json.dumps(base.asset_generation_report["generated"][0]))
    provenance["files"]["visual"][member] = value

    with pytest.raises(AssetAdmissionError, match=message):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report={**base.asset_generation_report, "generated": [provenance]},
        )


@pytest.mark.parametrize(
    ("attack", "message"),
    (
        ("scale_too_large", "uniform_scale_factor exceeds one"),
        ("source_model", "source_model_id must be a non-negative integer"),
        ("target_model", "target_model_id must be a non-negative integer or null"),
        ("reasons_type", "adaptation_reasons must be a locator-free string list"),
        ("reasons_empty", "adaptation_reasons must be a locator-free string list"),
        ("reasons_value", "adaptation_reasons are invalid"),
        ("relation", "compatibility.relation is invalid"),
        ("table_asset", "table compatibility is invalid"),
        ("table_model", "table compatibility is invalid"),
        ("nested_model", "target compatibility is invalid"),
        ("headroom", "compatibility headroom mismatch"),
        ("runtime_probe", "source_runtime_probe mismatch"),
    ),
)
def test_admission_rejects_each_invalid_derived_provenance_value(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    scene_spec = parse_rule_based("Place a red block on top of a plate.", seed=31)
    catalog, report = ensure_assets_for_scene(
        scene_spec,
        _catalog_with_local_block_meshes(tmp_path),
        objects_root=tmp_path / "generated-staging",
    )
    provenance = json.loads(json.dumps(report["generated"][0]))
    compatibility = provenance["compatibility"]
    if attack == "scale_too_large":
        provenance["uniform_scale_factor"] = 1.1
    elif attack == "source_model":
        provenance["source_model_id"] = -1
    elif attack == "target_model":
        compatibility["target_model_id"] = -1
    elif attack == "reasons_type":
        provenance["adaptation_reasons"] = "reason"
    elif attack == "reasons_empty":
        provenance["adaptation_reasons"] = []
    elif attack == "reasons_value":
        provenance["adaptation_reasons"] = ["unknown"]
    elif attack == "relation":
        compatibility["relation"] = "beside"
    elif attack == "table_asset":
        compatibility.update(relation="on_table", target_asset_id="plate", target_model_id=None)
    elif attack == "table_model":
        compatibility.update(relation="on_table", target_asset_id="table", target_model_id=0)
    elif attack == "nested_model":
        compatibility["target_model_id"] = None
    elif attack == "headroom":
        compatibility["headroom_fraction"] = 0.5
    else:
        compatibility["source_runtime_probe"] = "unexpected"

    with pytest.raises(AssetAdmissionError, match=message):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=scene_spec,
            asset_catalog=catalog,
            generation_report={**report, "generated": [provenance]},
        )


def test_valid_table_compatibility_accepts_a_null_target_model(tmp_path: Path) -> None:
    scene_spec = parse_rule_based("Place a red block on top of a plate.", seed=31)
    catalog, report = ensure_assets_for_scene(
        scene_spec,
        _catalog_with_local_block_meshes(tmp_path),
        objects_root=tmp_path / "generated-staging",
    )
    provenance = json.loads(json.dumps(report["generated"][0]))
    provenance["compatibility"].update(
        relation="on_table",
        target_asset_id="table",
        target_model_id=None,
    )

    assets_module._require_provenance_shape(provenance)


@pytest.mark.parametrize(
    ("attack", "message"),
    (
        ("files", "has no payload identities"),
        ("keys", "unexpected payload identities"),
        ("record", "is missing visual identity"),
        ("fields", "visual identity has unexpected fields"),
        ("path", "visual path mismatch"),
    ),
)
def test_payload_file_validation_rejects_each_malformed_defensive_input(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    source = tmp_path / "asset"
    (source / "visual").mkdir(parents=True)
    (source / "collision").mkdir()
    for relative in (
        "visual/textured0.obj",
        "collision/textured0.obj",
        "visual/material.mtl",
        "model_data0.json",
    ):
        (source / relative).write_bytes(relative.encode("ascii"))
    manifest = assets_module._tree_manifest(source)
    files: object = {
        "visual": {
            "path": str((source / "visual/textured0.obj").resolve()),
            "sha256": manifest["visual/textured0.obj"]["sha256"],
        },
        "collision": {
            "path": str((source / "collision/textured0.obj").resolve()),
            "sha256": manifest["collision/textured0.obj"]["sha256"],
        },
        "material": {
            "path": str((source / "visual/material.mtl").resolve()),
            "sha256": manifest["visual/material.mtl"]["sha256"],
        },
        "metadata": {
            "path": str((source / "model_data0.json").resolve()),
            "sha256": manifest["model_data0.json"]["sha256"],
        },
    }
    if attack == "files":
        files = []
    elif attack == "keys":
        files.pop("metadata")
    elif attack == "record":
        files["visual"] = []
    elif attack == "fields":
        files["visual"]["extra"] = "value"
    else:
        files["visual"]["path"] = 7

    with pytest.raises(AssetAdmissionError, match=message):
        assets_module._validate_provenance_files({"files": files}, source, manifest)


def test_admission_rejects_a_non_directory_destination(tmp_path: Path) -> None:
    base = _generated_fixture(tmp_path)
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    destination = tmp_path / "library" / "generated" / asset_id
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"not a directory")

    with pytest.raises(AssetAdmissionError, match="destination is not a safe directory"):
        GeneratedAssetAdmitter(tmp_path / "library", date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )


def test_second_post_publish_identity_change_is_rejected_and_cleaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _generated_fixture(tmp_path)
    library = tmp_path / "library"
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    destination = library / "generated" / asset_id
    original = assets_module._require_directory_identity
    destination_checks = 0

    def fail_second_destination_check(path: Path, descriptor: int) -> None:
        nonlocal destination_checks
        if path == destination:
            destination_checks += 1
            if destination_checks == 2:
                raise AssetAdmissionError("changed")
        original(path, descriptor)

    monkeypatch.setattr(
        assets_module,
        "_require_directory_identity",
        fail_second_destination_check,
    )

    with pytest.raises(AssetAdmissionError, match="published destination identity changed"):
        GeneratedAssetAdmitter(library, date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )

    assert destination_checks == 2
    assert not destination.exists()


def test_directory_identity_and_cleanup_tolerate_missing_paths(tmp_path: Path) -> None:
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(AssetAdmissionError, match="changed during admission"):
            assets_module._require_directory_identity(tmp_path / "missing", descriptor)
        assets_module._remove_child_directory_if_identity(descriptor, "missing", expected_fd=-1)
    finally:
        os.close(descriptor)


def test_private_incoming_name_exhaustion_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _generated_fixture(tmp_path)
    library = tmp_path / "library"
    asset_id = base.asset_generation_report["generated"][0]["asset_id"]
    collision = library / ".incoming" / f"{asset_id}.fixed"
    collision.mkdir(parents=True)
    monkeypatch.setattr(assets_module.secrets, "token_hex", lambda count: "fixed")

    with pytest.raises(AssetAdmissionError, match="could not create private incoming directory"):
        GeneratedAssetAdmitter(library, date(2026, 8, 31)).admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report=base.asset_generation_report,
        )


def test_generation_input_rejects_a_directory_that_changes_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _generated_fixture(tmp_path)
    source = Path(base.asset_catalog.entries[0].asset_path)
    original = Path.is_dir

    def report_not_a_directory(path: Path) -> bool:
        if path == source:
            return False
        return original(path)

    monkeypatch.setattr(Path, "is_dir", report_not_a_directory)
    with pytest.raises(AssetAdmissionError, match="not a regular directory"):
        assets_module._validate_generation_inputs(
            scene_spec=base.scene_spec,
            entry=base.asset_catalog.entries[0],
            provenance=base.asset_generation_report["generated"][0],
            catalog_root=Path(base.asset_catalog.objects_root),
        )
