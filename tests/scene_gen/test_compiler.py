from __future__ import annotations

import json
from pathlib import Path

import pytest

from scene_gen import CompileFailure, CompileRequest, compile_scene
from scene_gen.catalog import AssetCatalog
from scene_gen.schema import SceneSpecError
from scene_gen.solver import SceneSolveError

ROOT = Path(__file__).resolve().parents[2]


def test_compile_scene_runs_one_explicit_pipeline_and_reports_real_stages(
    tmp_path: Path,
) -> None:
    observed = []
    outcome = compile_scene(
        CompileRequest(
            request="Place a can on top of a plate.",
            seed=42,
            asset_catalog_path=ROOT / "tests" / "fixtures" / "asset_catalog.json",
            out_root=tmp_path / "runs",
        ),
        observer=observed.append,
    )

    assert outcome.scene_spec.request == "Place a can on top of a plate."
    assert outcome.resolved_scene.source_scene_spec_sha256 == outcome.scene_spec.digest()
    assert outcome.manifest["resolved_scene_sha256"] == outcome.resolved_scene.digest()
    assert outcome.static_validation["status"] == "fail"
    source_checks = {
        check["name"]: check["status"] for check in outcome.static_validation["checks"]
    }
    assert source_checks["real_asset_files:can_1"] == "fail"
    assert source_checks["real_asset_files:plate_1"] == "fail"
    assert outcome.output_dir == (tmp_path / "runs" / outcome.scene_spec.scene_id).resolve()
    assert (outcome.output_dir / "package_manifest.json").is_file()
    assert [(event.stage, event.phase) for event in observed] == [
        ("parse", "started"),
        ("parse", "completed"),
        ("catalog", "started"),
        ("catalog", "completed"),
        ("solve", "started"),
        ("solve", "completed"),
        ("package", "started"),
        ("package", "completed"),
        ("static_validation", "started"),
        ("static_validation", "completed"),
    ]


def test_compile_scene_generates_missing_asset_into_explicit_workspace(
    tmp_path: Path,
) -> None:
    objects_root = tmp_path / "RoboTwin" / "assets" / "objects"
    catalog = AssetCatalog(
        robotwin_root=str(tmp_path / "RoboTwin"),
        objects_root=str(objects_root),
        entries=(),
    )
    catalog_path = tmp_path / "empty_catalog.json"
    catalog_path.write_text(
        json.dumps(catalog.canonical_dict()),
        encoding="utf-8",
    )
    observed = []

    outcome = compile_scene(
        CompileRequest(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=catalog_path,
            out_root=tmp_path / "runs",
            generate_missing_assets=True,
            generated_objects_root=objects_root,
        ),
        observer=observed.append,
    )

    assert outcome.asset_generation_report is not None
    assert outcome.asset_generation_report["generated_count"] == 1
    assert outcome.manifest["asset_catalog_sha256"] == outcome.asset_catalog.digest()
    assert (outcome.output_dir / "effective_asset_catalog.json").is_file()
    assert (objects_root / outcome.asset_catalog.entries[0].asset_id).is_dir()
    assert ("asset_generation", "completed") in [(event.stage, event.phase) for event in observed]


def test_compile_scene_maps_bounded_request_rejection_without_creating_a_run(
    tmp_path: Path,
) -> None:
    with pytest.raises(CompileFailure) as error:
        compile_scene(
            CompileRequest(
                request="x",
                seed=0,
                asset_catalog_path=ROOT / "tests" / "fixtures" / "asset_catalog.json",
                out_root=tmp_path / "runs",
            )
        )

    assert error.value.code == "T2E_REQUEST_REJECTED"
    assert error.value.stage == "parse"
    assert not (tmp_path / "runs").exists()


def test_compile_scene_emits_entered_stage_then_stops_on_typed_catalog_failure(
    tmp_path: Path,
) -> None:
    observed = []

    with pytest.raises(CompileFailure) as error:
        compile_scene(
            CompileRequest(
                request="Place a can on top of a plate.",
                seed=42,
                asset_catalog_path=tmp_path / "missing.json",
                out_root=tmp_path / "runs",
            ),
            observer=observed.append,
        )

    assert error.value.code == "T2E_CATALOG_INVALID"
    assert error.value.stage == "catalog"
    assert [(event.stage, event.phase) for event in observed] == [
        ("parse", "started"),
        ("parse", "completed"),
        ("catalog", "started"),
    ]


def test_compile_scene_distinguishes_asset_miss_from_solver_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = AssetCatalog(
        robotwin_root=str(tmp_path / "RoboTwin"),
        objects_root=str(tmp_path / "RoboTwin" / "assets" / "objects"),
        entries=(),
    )
    catalog_path = tmp_path / "empty_catalog.json"
    catalog_path.write_text(json.dumps(catalog.canonical_dict()), encoding="utf-8")
    request = CompileRequest(
        request="Place a purple hexagonal pedestal on the table.",
        seed=7,
        asset_catalog_path=catalog_path,
        out_root=tmp_path / "runs",
    )

    with pytest.raises(CompileFailure) as asset_error:
        compile_scene(request)
    assert asset_error.value.code == "T2E_ASSET_UNAVAILABLE"
    assert asset_error.value.stage == "solve"

    monkeypatch.setattr(
        "scene_gen.compiler.ensure_assets_for_scene",
        lambda spec, loaded_catalog, objects_root: (_ for _ in ()).throw(
            SceneSpecError("generator rejected asset")
        ),
    )
    with pytest.raises(CompileFailure) as generation_error:
        compile_scene(
            request.__class__(
                request=request.request,
                seed=request.seed,
                asset_catalog_path=request.asset_catalog_path,
                out_root=tmp_path / "generated_runs",
                generate_missing_assets=True,
                generated_objects_root=tmp_path / "generated_objects",
            )
        )
    assert generation_error.value.code == "T2E_ASSET_UNAVAILABLE"
    assert generation_error.value.stage == "asset_generation"

    report = {"status": "fail", "blocker": "bounded solver exhausted"}
    monkeypatch.setattr(
        "scene_gen.compiler.solve_scene",
        lambda spec, loaded_catalog: (_ for _ in ()).throw(SceneSolveError(report)),
    )
    with pytest.raises(CompileFailure) as solver_error:
        compile_scene(
            request.__class__(
                request="Place a can on top of a plate.",
                seed=7,
                asset_catalog_path=ROOT / "tests" / "fixtures" / "asset_catalog.json",
                out_root=tmp_path / "other_runs",
            )
        )
    assert solver_error.value.code == "T2E_SOLVER_EXHAUSTED"
    assert solver_error.value.details == report
