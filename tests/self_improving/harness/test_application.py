from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

import self_improving.harness.application as application_module
from scene_gen.catalog import AssetCatalog
from self_improving.harness.application import (
    CompileApplicationConfigurationError,
    CompileApplicationInputError,
    CompileApplicationSettings,
    ExternalCatalogError,
    create_compile_application,
)
from self_improving.harness.schemas import (
    ArtifactRef,
    CompileConfig,
    RunStatus,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
)

ROOT = Path(__file__).resolve().parents[3]
SKILL_REF = "text2env.compile@1.0.0"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha(value: object) -> str:
    return _sha(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _tree_sha(root: Path) -> str:
    files: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_file():
            payload = path.read_bytes()
            files.append(
                {
                    "path": relative.as_posix(),
                    "bytes": len(payload),
                    "sha256": _sha(payload),
                }
            )
    return _canonical_sha({"exists": True, "files": files})


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _qualification_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "qualified_skills" / "text2env.compile" / "1.0.0"
    bundle.mkdir(parents=True)
    scene_gen_sha = _tree_sha(ROOT / "scene_gen")
    ledger_sha = _tree_sha(ROOT / "self_improving/asset_pipeline/active/1_asset_reuse/lib")
    implementation_files = []
    for relative in (
        "self_improving/harness/handlers/text2env_compile.py",
        "self_improving/harness/handlers/text2env_compile_dependencies.py",
    ):
        payload = (ROOT / relative).read_bytes()
        implementation_files.append(
            {"path": relative, "bytes": len(payload), "sha256": _sha(payload)}
        )
    manifest = {
        "schema_version": "harness.skill_implementation_manifest.v1",
        "skill_ref": SKILL_REF,
        "files": implementation_files,
        "bundle_sha256": "0" * 64,
        "scene_gen_tree_sha256": scene_gen_sha,
        "ledger_contract_tree_sha256": ledger_sha,
    }
    manifest["bundle_sha256"] = _canonical_sha(
        {
            "schema_version": manifest["schema_version"],
            "skill_ref": manifest["skill_ref"],
            "files": sorted(implementation_files, key=lambda item: str(item["path"])),
            "scene_gen_tree_sha256": scene_gen_sha,
            "ledger_contract_tree_sha256": ledger_sha,
        }
    )
    _write_json(bundle / "manifest.json", manifest)
    report = {
        "schema_version": "harness.skill_qualification_report.v1",
        "skill_ref": SKILL_REF,
        "status": "pass",
        "deterministic_case_id": "compile-application-empty-catalog-77",
        "regression_command": ("pytest -q tests/self_improving/harness/test_application.py"),
        "implementation_sha256": manifest["bundle_sha256"],
        "scene_gen_tree_sha256": scene_gen_sha,
        "ledger_contract_tree_sha256": ledger_sha,
        "checks": [
            {
                "name": "compile.application",
                "status": "pass",
                "evidence": {"fixture_only": True, "run_count": 3},
            }
        ],
    }
    _write_json(bundle / "report.json", report)
    qualification = {
        "skill_ref": SKILL_REF,
        "status": "pass",
        "deterministic_case_id": report["deterministic_case_id"],
        "regression_command": report["regression_command"],
        "report_sha256": _sha((bundle / "report.json").read_bytes()),
    }
    _write_json(bundle / "qualification.json", qualification)
    return bundle


def _settings(tmp_path: Path) -> CompileApplicationSettings:
    catalogs = tmp_path / "catalogs"
    assets = tmp_path / "external-assets"
    catalogs.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    return CompileApplicationSettings(
        state_root=tmp_path / "state",
        external_catalog_roots=(catalogs,),
        allowed_asset_roots=(assets,),
        admission_date=date(2026, 8, 31),
    )


@pytest.fixture
def fixed_qualification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    bundle = _qualification_bundle(tmp_path)
    monkeypatch.setattr(application_module, "_COMPILE_QUALIFICATION_ROOT", bundle)
    return bundle


def _empty_catalog(path: Path, objects_root: Path) -> Path:
    catalog = AssetCatalog(
        robotwin_root=str(objects_root.parent / "RoboTwin"),
        objects_root=str(objects_root),
        entries=(),
    )
    _write_json(path, catalog.canonical_dict())
    return path


def test_factory_exposes_only_the_fixed_qualified_compile_skill_and_real_authorities(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    settings = _settings(tmp_path)

    app = create_compile_application(settings)

    assert [f"{item.skill_id}@{item.version}" for item in app.skills] == [SKILL_REF]
    assert app.skills[0].qualification_artifact.uri.startswith("artifact://sha256/")
    assert app.resolve_artifact(app.skills[0].qualification_artifact).is_file()
    assert app.artifact_root == (settings.state_root / "cas").resolve()
    assert app.journal_path == (settings.state_root / "harness.sqlite3").resolve()
    assert app.journal_path.is_file()
    assert app.durable_run_state is True
    assert "qualification" not in inspect.signature(create_compile_application).parameters
    assert "handler" not in inspect.signature(create_compile_application).parameters


def test_compile_application_runs_admit_reuse_reuse_and_replays_real_events(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    settings = _settings(tmp_path)
    catalog_path = _empty_catalog(
        settings.external_catalog_roots[0] / "empty.json",
        settings.allowed_asset_roots[0] / "objects",
    )
    app = create_compile_application(settings)

    states = tuple(
        app.compile(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
            asset_catalog_path=catalog_path,
            generate_missing_assets=True,
        )
        for _ in range(3)
    )

    assert [state.status for state in states] == [RunStatus.SUCCEEDED] * 3
    assert states[0].invocation_digest != states[1].invocation_digest
    assert states[1].invocation_digest == states[2].invocation_digest
    outputs = tuple(Text2EnvCompileOutput.model_validate(state.output) for state in states)
    assert outputs[1] == outputs[2]
    assert outputs[0].environment_package.package_id == outputs[1].environment_package.package_id
    admission_records = []
    for state in states:
        admission_reports = [
            artifact
            for artifact in state.artifacts
            if artifact.schema_version == "harness.generated_asset_admission.v1"
        ]
        assert len(admission_reports) == 1
        admission_records.append(
            json.loads(app.resolve_artifact(admission_reports[0]).read_text(encoding="utf-8"))
        )
    assert [record["status"] for record in admission_records] == [
        "admitted",
        "reused",
        "reused",
    ]
    asset_id = admission_records[0]["assets"][0]["asset_id"]
    ledger_path = settings.state_root / "asset-library/generated" / asset_id / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    contract = importlib.import_module(
        "self_improving.asset_pipeline.active.1_asset_reuse.lib.ledger"
    )
    assert contract.validate_ledger(ledger, check_files=True) == []

    page = app.events(run_id=states[2].run_id, limit=100)
    assert [stored.envelope.event for stored in page.events] == list(states[2].events)
    assert page.has_more is False
    for state in states:
        assert all(app.resolve_artifact(artifact).is_file() for artifact in state.artifacts)

    reopened = create_compile_application(settings)
    replayed = reopened.events(run_id=states[2].run_id, limit=100)
    assert [stored.envelope.event for stored in replayed.events] == list(states[2].events)
    assert reopened.durable_run_state is True
    invocation = reopened.invocation(states[2].run_id)
    assert invocation is not None
    assert invocation.invocation_digest == states[2].invocation_digest
    assert reopened.run_state(states[2].run_id) == states[2]
    assert reopened.invocation(states[0].run_id) is not None
    assert reopened.run_state(states[0].run_id) == states[0]


def test_application_returns_none_for_unknown_durable_records(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    settings = _settings(tmp_path)
    app = create_compile_application(settings)
    unknown = UUID("22345678-1234-4234-9234-123456789abc")

    assert app.invocation(unknown) is None
    assert app.run_state(unknown) is None


def test_external_catalog_is_snapshotted_before_use(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    settings = _settings(tmp_path)
    source = _empty_catalog(
        settings.external_catalog_roots[0] / "catalog.json",
        settings.allowed_asset_roots[0] / "objects",
    )
    original = source.read_bytes()
    app = create_compile_application(settings)

    snapshot = app.snapshot_asset_catalog(source)
    source.write_text('{"mutated":true}', encoding="utf-8")

    resolved = app.resolve_artifact(snapshot)
    assert resolved.read_bytes() == original
    assert snapshot.sha256 == _sha(original)
    assert snapshot.schema_version == "robotwin.asset_catalog.v1"


def test_typed_compile_input_invokes_only_from_the_application_cas(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    settings = _settings(tmp_path)
    catalog_path = _empty_catalog(
        settings.external_catalog_roots[0] / "typed.json",
        settings.allowed_asset_roots[0] / "objects",
    )
    app = create_compile_application(settings)
    snapshot = app.snapshot_asset_catalog(catalog_path)
    parameters = Text2EnvCompileInput(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
        asset_catalog=snapshot,
        config=CompileConfig(generate_missing_assets=True),
    )

    state = app.invoke_typed(parameters)

    assert state.status is RunStatus.SUCCEEDED
    invocation = app.invocation(state.run_id)
    assert invocation is not None
    assert invocation.effective_parameters == parameters.model_dump(mode="json")
    assert app.resolve_artifact(snapshot).is_relative_to(app.artifact_root)


def test_typed_compile_input_rejects_file_locator_even_when_bytes_match(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    settings = _settings(tmp_path)
    catalog_path = _empty_catalog(
        settings.external_catalog_roots[0] / "file-ref.json",
        settings.allowed_asset_roots[0] / "objects",
    )
    payload = catalog_path.read_bytes()
    app = create_compile_application(settings)
    parameters = Text2EnvCompileInput(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
        asset_catalog=ArtifactRef(
            name="mutable_catalog",
            uri=catalog_path.resolve().as_uri(),
            media_type="application/json",
            sha256=_sha(payload),
            bytes=len(payload),
            schema_version="robotwin.asset_catalog.v1",
        ),
        config=CompileConfig(generate_missing_assets=False),
    )

    with pytest.raises(CompileApplicationInputError, match="application CAS"):
        app.invoke_typed(parameters)


def test_typed_compile_input_rejects_missing_foreign_cas_object(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    settings = _settings(tmp_path)
    app = create_compile_application(settings)
    missing_sha = "f" * 64
    parameters = Text2EnvCompileInput(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
        asset_catalog=ArtifactRef(
            name="foreign_catalog",
            uri=f"artifact://sha256/{missing_sha}",
            media_type="application/json",
            sha256=missing_sha,
            bytes=2,
            schema_version="robotwin.asset_catalog.v1",
        ),
        config=CompileConfig(generate_missing_assets=False),
    )

    with pytest.raises(CompileApplicationInputError, match="unavailable or corrupt"):
        app.invoke_typed(parameters)


def test_typed_compile_input_revalidates_a_retained_model_before_execution(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    settings = _settings(tmp_path)
    catalog_path = _empty_catalog(
        settings.external_catalog_roots[0] / "retained.json",
        settings.allowed_asset_roots[0] / "objects",
    )
    app = create_compile_application(settings)
    snapshot = app.snapshot_asset_catalog(catalog_path)
    parameters = Text2EnvCompileInput(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
        asset_catalog=snapshot,
        config=CompileConfig(generate_missing_assets=False),
    )
    object.__setattr__(parameters, "request", "")

    with pytest.raises(CompileApplicationInputError, match="invalid"):
        app.invoke_typed(parameters)


def test_typed_compile_input_rejects_an_untyped_mapping(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    app = create_compile_application(_settings(tmp_path))

    with pytest.raises(CompileApplicationInputError, match="Text2EnvCompileInput"):
        app.invoke_typed({})  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["external_catalog_roots", "allowed_asset_roots"])
def test_settings_require_explicit_nonempty_trust_roots(
    tmp_path: Path,
    field: str,
) -> None:
    values = {
        "state_root": tmp_path / "state",
        "external_catalog_roots": (tmp_path,),
        "allowed_asset_roots": (tmp_path,),
        "admission_date": date(2026, 8, 31),
    }
    values[field] = ()
    with pytest.raises(CompileApplicationConfigurationError, match="must not be empty"):
        create_compile_application(CompileApplicationSettings(**values))


@pytest.mark.parametrize("field", ["external_catalog_roots", "allowed_asset_roots"])
@pytest.mark.parametrize("kind", ["missing", "file"])
def test_settings_reject_missing_or_non_directory_trust_roots(
    tmp_path: Path,
    fixed_qualification: Path,
    field: str,
    kind: str,
) -> None:
    settings = _settings(tmp_path)
    invalid = tmp_path / f"{field}-{kind}"
    if kind == "file":
        invalid.write_text("not a directory", encoding="utf-8")
    values = {
        "state_root": settings.state_root,
        "external_catalog_roots": settings.external_catalog_roots,
        "allowed_asset_roots": settings.allowed_asset_roots,
        "admission_date": settings.admission_date,
    }
    values[field] = (invalid,)
    with pytest.raises(CompileApplicationConfigurationError, match="missing|directory"):
        create_compile_application(CompileApplicationSettings(**values))


def test_settings_reject_state_root_that_is_a_file(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.state_root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(CompileApplicationConfigurationError, match="state_root"):
        create_compile_application(settings)


def test_settings_reject_non_date_admission_value(
    tmp_path: Path,
    fixed_qualification: Path,
) -> None:
    settings = _settings(tmp_path)
    invalid = CompileApplicationSettings(
        state_root=settings.state_root,
        external_catalog_roots=settings.external_catalog_roots,
        allowed_asset_roots=settings.allowed_asset_roots,
        admission_date="2026-08-31",  # type: ignore[arg-type]
    )

    with pytest.raises(CompileApplicationConfigurationError, match="admission_date"):
        create_compile_application(invalid)


@pytest.mark.parametrize("kind", ["outside", "missing", "directory"])
def test_snapshot_rejects_untrusted_or_non_file_catalogs(
    tmp_path: Path,
    fixed_qualification: Path,
    kind: str,
) -> None:
    settings = _settings(tmp_path)
    app = create_compile_application(settings)
    if kind == "outside":
        candidate = tmp_path / "outside.json"
        candidate.write_text("{}", encoding="utf-8")
    elif kind == "missing":
        candidate = settings.external_catalog_roots[0] / "missing.json"
    else:
        candidate = settings.external_catalog_roots[0]

    with pytest.raises(ExternalCatalogError, match="trusted root|missing|regular file"):
        app.snapshot_asset_catalog(candidate)


def test_factory_fails_closed_when_fixed_qualification_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        application_module,
        "_COMPILE_QUALIFICATION_ROOT",
        tmp_path / "not-packaged",
    )

    with pytest.raises(ValueError, match="qualification bundle"):
        create_compile_application(settings)
