from __future__ import annotations

import importlib
import json
import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import self_improving.harness.assets as assets_module
from scene_gen import CompileRequest, compile_scene
from scene_gen.catalog import AssetCatalog
from self_improving.harness import AssetAdmissionError, GeneratedAssetAdmitter


def _empty_catalog(tmp_path: Path) -> Path:
    catalog = AssetCatalog(
        robotwin_root=str(tmp_path / "RoboTwin"),
        objects_root=str(tmp_path / "RoboTwin" / "assets" / "objects"),
        entries=(),
    )
    path = tmp_path / "empty_catalog.json"
    path.write_text(json.dumps(catalog.canonical_dict()), encoding="utf-8")
    return path


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
        "self_improving.asset_pipeline.active.1_asset_reuse.lib.ledger"
    )

    assert contract.validate_ledger(ledger, check_files=True) == []
    assert ledger["schema_version"] == "asset_ledger.v3"
    assert ledger["external_ids"]["env_gen"] == admitted["asset_id"]
    model = ledger["models"][0]
    assert model["physical"]["conventions"]["stable_poses"][0][
        "measured_against"
    ]["backend"] == "portable"
    assert all("runtime_default" not in json.dumps(value) for value in model["physical"].values())
    assert all(representation["files"] for representation in model["representations"])
    assert any(
        representation.get("collision_meta", {}).get("mode") == "explicit_mesh"
        for representation in model["representations"]
    )
    assert model["verification"][0]["check"] == "generation_qc"
    assert outcome.resolved_scene.objects[0].source_files[0].startswith(str(asset_dir))
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
    assert repeated.asset_admission_report["assets"][0]["ledger_sha256"] == admitted[
        "ledger_sha256"
    ]


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
        generation_report={"generated": []},
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
    two_entries = base.asset_catalog.model_copy(
        update={"entries": (original_entry, copied_entry)}
    )
    copied_provenance = dict(base.asset_generation_report["generated"][0])
    copied_provenance["asset_id"] = copied_id
    _, mixed = admitter.admit(
        scene_spec=base.scene_spec,
        asset_catalog=two_entries,
        generation_report={
            "generated": [
                base.asset_generation_report["generated"][0],
                copied_provenance,
            ]
        },
    )
    assert mixed["status"] == "mixed"
    assert {item["disposition"] for item in mixed["assets"]} == {"admitted", "reused"}
    assert first_catalog.entries[0].asset_path.startswith(str(tmp_path / "asset_library"))

    with pytest.raises(AssetAdmissionError, match="catalog entry is missing"):
        admitter.admit(
            scene_spec=base.scene_spec,
            asset_catalog=base.asset_catalog,
            generation_report={"generated": [{"asset_id": "missing"}]},
        )


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
        "self_improving.asset_pipeline.active.1_asset_reuse.lib.ledger"
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
    if fail_after_promotion:
        assert (library / ".rejected" / asset_id).is_dir()
