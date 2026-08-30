from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

import self_improving.harness.handlers.text2env_compile as handler_module
import self_improving.harness.handlers.text2env_compile_dependencies as dependency_module
from scene_gen import CompileFailure
from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.schema import ResolvedSceneSpec
from self_improving.harness import (
    ArtifactRef,
    AssetAdmissionError,
    CompileConfig,
    DependencyRef,
    DependencyResolutionError,
    LocalArtifactStore,
    RunStatus,
    SkillRegistry,
    SQLiteEventJournal,
    StaticDependencyResolver,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
)
from self_improving.harness.handlers.text2env_compile import (
    Text2EnvCompileHandler,
    text2env_compile_descriptor,
)
from self_improving.harness.handlers.text2env_compile_dependencies import (
    Text2EnvCompileDependencyResolver,
)

ROOT = Path(__file__).resolve().parents[3]


class SequenceClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 31, 6, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


def _put_json(
    store: LocalArtifactStore,
    path: Path,
    payload: dict,
    *,
    name: str,
    schema_version: str,
):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _materialized_fixture_catalog(tmp_path: Path) -> Path:
    catalog = load_catalog(ROOT / "tests/fixtures/asset_catalog.json")
    objects_root = tmp_path / "fixture_assets"
    entries = []
    for entry in catalog.entries:
        asset_root = objects_root / entry.asset_id
        models = []
        for model in entry.models:
            metadata = asset_root / f"model_data{model.model_id}.json"
            visual = asset_root / "visual" / f"base{model.model_id}.glb"
            collision = asset_root / "collision" / f"base{model.model_id}.glb"
            metadata.parent.mkdir(parents=True, exist_ok=True)
            visual.parent.mkdir(parents=True, exist_ok=True)
            collision.parent.mkdir(parents=True, exist_ok=True)
            metadata.write_text("{}\n", encoding="utf-8")
            visual.write_bytes(b"fixture visual")
            collision.write_bytes(b"fixture collision")
            models.append(
                model.model_copy(
                    update={
                        "model_path": str(asset_root.resolve()),
                        "metadata_path": str(metadata.resolve()),
                        "visual_path": str(visual.resolve()),
                        "collision_path": str(collision.resolve()),
                    }
                )
            )
        entries.append(
            entry.model_copy(
                update={
                    "asset_path": str(asset_root.resolve()),
                    "models": tuple(models),
                }
            )
        )
    materialized = catalog.model_copy(
        update={
            "robotwin_root": str((tmp_path / "RoboTwin").resolve()),
            "objects_root": str(objects_root.resolve()),
            "entries": tuple(entries),
        }
    )
    path = tmp_path / "materialized_catalog.json"
    path.write_text(json.dumps(materialized.canonical_dict()), encoding="utf-8")
    return path


def _registry(
    tmp_path: Path,
    *,
    store: LocalArtifactStore,
    handler: Text2EnvCompileHandler,
    dependency_resolver=None,
    run_ids=None,
) -> tuple[SkillRegistry, SQLiteEventJournal]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    qualification_report = tmp_path / "qualification_report.json"
    qualification_report.write_text(
        json.dumps({"case": "fixture-can-on-plate", "status": "pass"}, sort_keys=True),
        encoding="utf-8",
    )
    qualification = _put_json(
        store,
        tmp_path / "qualification.json",
        {
            "skill_ref": "text2env.compile@1.0.0",
            "status": "pass",
            "deterministic_case_id": "fixture-can-on-plate",
            "regression_command": (
                "pytest -q tests/self_improving/harness/test_text2env_compile_handler.py"
            ),
            "report_sha256": hashlib.sha256(qualification_report.read_bytes()).hexdigest(),
        },
        name="text2env_compile_qualification",
        schema_version="harness.skill_qualification.v1",
    )
    descriptor = text2env_compile_descriptor(
        qualification_artifact=qualification,
        implementation_sha256=hashlib.sha256(
            (ROOT / "self_improving/harness/handlers/text2env_compile.py").read_bytes()
        ).hexdigest(),
    )
    journal = SQLiteEventJournal(tmp_path / "events.sqlite3")
    selected_run_ids = run_ids or iter(
        (UUID("12345678-1234-4234-9234-123456789abc"),)
    )
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=dependency_resolver
        or StaticDependencyResolver(
            {
                "text2env.compile@1.0.0": (
                    DependencyRef(
                        name="scene_gen",
                        version="0.1.0",
                        sha256="d" * 64,
                    ),
                )
            }
        ),
        event_sink=journal,
        clock=SequenceClock(),
        run_id_factory=lambda: next(selected_run_ids),
    )
    registry.register(descriptor, handler)
    return registry, journal


def _handler(tmp_path: Path, store: LocalArtifactStore) -> Text2EnvCompileHandler:
    return Text2EnvCompileHandler(
        artifact_store=store,
        work_root=tmp_path / "runs",
        generated_staging_root=tmp_path / "generated_staging",
        asset_library_root=tmp_path / "asset_library",
        admission_date=date(2026, 8, 31),
        allowed_asset_roots=(tmp_path,),
    )


def test_registry_compile_handler_runs_real_pipeline_into_cas_and_event_journal(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    catalog_path = _materialized_fixture_catalog(tmp_path)
    catalog_payload = catalog_path.read_bytes()
    catalog = ArtifactRef(
        name="fixture_catalog",
        uri=catalog_path.resolve().as_uri(),
        media_type="application/json",
        sha256=hashlib.sha256(catalog_payload).hexdigest(),
        bytes=len(catalog_payload),
        schema_version="robotwin.asset_catalog.v1",
    )
    handler = _handler(tmp_path, store)
    registry, journal = _registry(tmp_path, store=store, handler=handler)

    state = registry.invoke(
        "text2env.compile",
        "1.0.0",
        {
            "request": "Place a can on top of a plate.",
            "seed": 42,
            "asset_catalog": catalog.model_dump(mode="json"),
            "config": CompileConfig(generate_missing_assets=False).model_dump(mode="json"),
        },
    )

    assert state.status == RunStatus.SUCCEEDED
    output = Text2EnvCompileOutput.model_validate(state.output)
    assert output.environment_package.producer_skill_ref == "text2env.compile@1.0.0"
    assert output.environment_package.package_id == output.environment_package.resolved_scene_sha256
    assert all(artifact.uri.startswith("artifact://sha256/") for artifact in state.artifacts)
    assert all(store.resolve(artifact).path.is_file() for artifact in state.artifacts)

    canonical_catalog = load_catalog(store.resolve(output.environment_package.asset_catalog).path)
    assert output.environment_package.asset_catalog.sha256 == canonical_catalog.digest()
    resolved = ResolvedSceneSpec.model_validate_json(
        store.resolve(output.resolved_scene).path.read_text(encoding="utf-8")
    )
    manifest = json.loads(
        store.resolve(output.environment_package.package_manifest).path.read_text(
            encoding="utf-8"
        )
    )
    assert resolved.digest() == output.environment_package.resolved_scene_sha256
    assert manifest["resolved_scene_sha256"] == resolved.digest()
    assert manifest["asset_catalog_sha256"] == canonical_catalog.digest()
    static_validation = json.loads(
        store.resolve(output.static_validation).path.read_text(encoding="utf-8")
    )
    assert static_validation["status"] == "incomplete"

    expected_stages = [
        "preflight",
        "compile.started",
        "compile.parse.started",
        "compile.parse.completed",
        "compile.catalog.started",
        "compile.catalog.completed",
        "compile.solve.started",
        "compile.solve.completed",
        "compile.package.started",
        "compile.package.completed",
        "compile.static_validation.started",
        "compile.static_validation.completed",
        "compile.completed",
        "complete",
    ]
    assert [event.stage for event in state.events] == expected_stages
    replayed = journal.read(run_id=state.run_id, limit=100)
    assert [item.envelope.event for item in replayed.events] == list(state.events)


def test_compile_handler_executes_the_cas_snapshot_after_external_catalog_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    catalog_path = _materialized_fixture_catalog(tmp_path)
    original_bytes = catalog_path.read_bytes()
    original_digest = AssetCatalog.model_validate_json(original_bytes).digest()
    catalog = ArtifactRef(
        name="mutable_catalog",
        uri=catalog_path.resolve().as_uri(),
        media_type="application/json",
        sha256=hashlib.sha256(original_bytes).hexdigest(),
        bytes=len(original_bytes),
        schema_version="robotwin.asset_catalog.v1",
    )
    real_validate = handler_module._validate_catalog_trust

    def validate_then_mutate(value, *, allowed_roots):
        real_validate(value, allowed_roots=allowed_roots)
        catalog_path.write_text('{"not":"the verified catalog"}', encoding="utf-8")

    monkeypatch.setattr(handler_module, "_validate_catalog_trust", validate_then_mutate)
    handler = _handler(tmp_path, store)
    registry, _ = _registry(tmp_path, store=store, handler=handler)

    state = registry.invoke(
        "text2env.compile",
        "1.0.0",
        {
            "request": "Place a can on top of a plate.",
            "seed": 42,
            "asset_catalog": catalog.model_dump(mode="json"),
            "config": {"generate_missing_assets": False},
        },
    )

    assert state.status == RunStatus.SUCCEEDED
    output = Text2EnvCompileOutput.model_validate(state.output)
    effective_catalog = load_catalog(
        store.resolve(output.environment_package.asset_catalog).path
    )
    assert effective_catalog.digest() == original_digest
    assert catalog_path.read_bytes() != original_bytes


def test_compile_handler_generates_and_admits_missing_asset_with_honest_receipt(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    empty_catalog = AssetCatalog(
        robotwin_root=str(tmp_path / "RoboTwin"),
        objects_root=str(tmp_path / "RoboTwin/assets/objects"),
        entries=(),
    )
    catalog = _put_json(
        store,
        tmp_path / "empty_catalog.json",
        empty_catalog.canonical_dict(),
        name="empty_catalog",
        schema_version="robotwin.asset_catalog.v1",
    )
    handler = _handler(tmp_path, store)
    registry, _ = _registry(tmp_path, store=store, handler=handler)

    state = registry.invoke(
        "text2env.compile",
        "1.0.0",
        {
            "request": "Place a purple hexagonal pedestal on the table.",
            "seed": 77,
            "asset_catalog": catalog.model_dump(mode="json"),
            "config": {"generate_missing_assets": True},
        },
    )

    assert state.status == RunStatus.SUCCEEDED
    assert "compile.asset_generation.started" in [event.stage for event in state.events]
    assert "compile.asset_admission.completed" in [event.stage for event in state.events]
    admission_refs = [
        artifact
        for artifact in state.artifacts
        if artifact.schema_version == "harness.generated_asset_admission.v1"
    ]
    assert len(admission_refs) == 1
    admission = json.loads(store.resolve(admission_refs[0]).path.read_text(encoding="utf-8"))
    assert admission["status"] == "admitted"
    assert admission["physical_qualification"] == "pending_settle"
    asset_id = admission["assets"][0]["asset_id"]
    assert (tmp_path / "asset_library/generated" / asset_id / "ledger.json").is_file()

    output = Text2EnvCompileOutput.model_validate(state.output)
    resolved = ResolvedSceneSpec.model_validate_json(
        store.resolve(output.resolved_scene).path.read_text(encoding="utf-8")
    )
    assert str(tmp_path / "asset_library/generated" / asset_id) in resolved.objects[
        0
    ].source_files[0]


@pytest.mark.parametrize(
    ("catalog_kind", "expected_code", "expected_stage"),
    [
        ("empty", "T2E_ASSET_UNAVAILABLE", "solve"),
        ("malformed", "HARN_DEPENDENCY_UNAVAILABLE", "catalog"),
    ],
)
def test_compile_handler_returns_typed_blocker_and_stops_later_stages(
    tmp_path: Path,
    catalog_kind: str,
    expected_code: str,
    expected_stage: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    catalog_payload = (
        AssetCatalog(
            robotwin_root=str(tmp_path / "RoboTwin"),
            objects_root=str(tmp_path / "objects"),
            entries=(),
        ).canonical_dict()
        if catalog_kind == "empty"
        else {"not": "a catalog"}
    )
    catalog = _put_json(
        store,
        tmp_path / "catalog.json",
        catalog_payload,
        name="catalog",
        schema_version="robotwin.asset_catalog.v1",
    )
    handler = _handler(tmp_path, store)
    registry, _ = _registry(tmp_path, store=store, handler=handler)

    state = registry.invoke(
        "text2env.compile",
        "1.0.0",
        {
            "request": "Place a purple hexagonal pedestal on the table.",
            "seed": 7,
            "asset_catalog": catalog.model_dump(mode="json"),
            "config": {"generate_missing_assets": False},
        },
    )

    assert state.status == RunStatus.BLOCKED
    assert state.blocker is not None
    assert state.blocker.code == expected_code
    assert state.blocker.stage == expected_stage
    stages = [event.stage for event in state.events]
    if expected_stage == "solve":
        assert "compile.solve.started" in stages
    else:
        assert "compile.started" not in stages
    assert "compile.package.started" not in stages


def test_compile_handler_maps_admission_and_internal_catalog_failures_to_v1_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    catalog = _put_json(
        store,
        tmp_path / "empty.json",
        AssetCatalog(
            robotwin_root=str(tmp_path / "RoboTwin"),
            objects_root=str(tmp_path / "objects"),
            entries=(),
        ).canonical_dict(),
        name="empty",
        schema_version="robotwin.asset_catalog.v1",
    )

    def invoke_with_failure(error: Exception, suffix: str):
        handler_root = tmp_path / suffix
        handler = Text2EnvCompileHandler(
            artifact_store=store,
            work_root=handler_root / "runs",
            generated_staging_root=handler_root / "generated_staging",
            asset_library_root=handler_root / "asset_library",
            admission_date=date(2026, 8, 31),
            allowed_asset_roots=(tmp_path,),
        )
        registry, _ = _registry(handler_root, store=store, handler=handler)

        def fail_compile(*args, **kwargs):
            raise error

        monkeypatch.setattr(handler_module, "compile_scene", fail_compile)
        return registry.invoke(
            "text2env.compile",
            "1.0.0",
            {
                "request": "Place a purple hexagonal pedestal on the table.",
                "seed": 7,
                "asset_catalog": catalog.model_dump(mode="json"),
                "config": {"generate_missing_assets": True},
            },
        )

    admission = invoke_with_failure(AssetAdmissionError("ledger rejected"), "admission")
    assert admission.blocker is not None
    assert admission.blocker.code == "T2E_ASSET_UNAVAILABLE"
    assert admission.blocker.stage == "asset_admission"

    catalog_failure = invoke_with_failure(
        CompileFailure(
            code="T2E_CATALOG_INVALID",
            stage="catalog",
            message="invalid catalog",
            details={"reason": object()},
        ),
        "catalog_failure",
    )
    assert catalog_failure.blocker is not None
    assert catalog_failure.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"


def test_compile_handler_blocks_any_package_binding_or_member_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    catalog_path = _materialized_fixture_catalog(tmp_path)
    catalog = store.put_file(
        catalog_path,
        name="catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    handler = _handler(tmp_path, store)
    registry, _ = _registry(tmp_path, store=store, handler=handler)
    real_compile = handler_module.compile_scene

    def corrupt_compile(*args, **kwargs):
        outcome = real_compile(*args, **kwargs)
        manifest = dict(outcome.manifest)
        manifest.update(
            {
                "source_scene_spec_sha256": "a" * 64,
                "resolved_scene_sha256": "b" * 64,
                "asset_catalog_sha256": "c" * 64,
            }
        )
        static_validation = dict(outcome.static_validation)
        static_validation["resolved_scene_sha256"] = "d" * 64
        (outcome.output_dir / "request.txt").write_text("tampered\n", encoding="utf-8")
        return replace(
            outcome,
            manifest=manifest,
            static_validation=static_validation,
        )

    monkeypatch.setattr(handler_module, "compile_scene", corrupt_compile)
    state = registry.invoke(
        "text2env.compile",
        "1.0.0",
        {
            "request": "Place a can on top of a plate.",
            "seed": 42,
            "asset_catalog": catalog.model_dump(mode="json"),
            "config": {"generate_missing_assets": False},
        },
    )

    assert state.status == RunStatus.BLOCKED
    assert state.blocker is not None
    assert state.blocker.code == "T2E_PACKAGE_INVALID"
    assert set(state.blocker.details["problems"]) == {
        "manifest.source_scene_spec_sha256",
        "manifest.resolved_scene_sha256",
        "manifest.asset_catalog_sha256",
        "static_validation.resolved_scene_sha256",
        "package_verification",
    }


@pytest.mark.parametrize(
    "corrupt_name",
    ["input_asset_catalog", "effective_asset_catalog"],
)
def test_compile_handler_detects_cas_identity_corruption(
    tmp_path: Path,
    corrupt_name: str,
) -> None:
    class CorruptingStore(LocalArtifactStore):
        def put_file(self, source, *, name, media_type, schema_version):
            ref = super().put_file(
                source,
                name=name,
                media_type=media_type,
                schema_version=schema_version,
            )
            return ref.model_copy(update={"sha256": "f" * 64}) if name == corrupt_name else ref

    store = CorruptingStore(tmp_path / "cas")
    catalog_path = _materialized_fixture_catalog(tmp_path)
    catalog_payload = catalog_path.read_bytes()
    catalog = ArtifactRef(
        name="catalog",
        uri=catalog_path.as_uri(),
        media_type="application/json",
        sha256=hashlib.sha256(catalog_payload).hexdigest(),
        bytes=len(catalog_payload),
        schema_version="robotwin.asset_catalog.v1",
    )
    handler = _handler(tmp_path, store)
    registry, _ = _registry(tmp_path, store=store, handler=handler)

    state = registry.invoke(
        "text2env.compile",
        "1.0.0",
        {
            "request": "Place a can on top of a plate.",
            "seed": 42,
            "asset_catalog": catalog.model_dump(mode="json"),
            "config": {"generate_missing_assets": False},
        },
    )

    assert state.status == RunStatus.FAILED
    assert state.blocker is not None
    assert state.blocker.code == "HARN_INTERNAL"


def test_compile_handler_enforces_catalog_roots_and_usable_model_files(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="allowed_asset_roots"):
        Text2EnvCompileHandler(
            artifact_store=LocalArtifactStore(tmp_path / "cas"),
            work_root=tmp_path / "runs",
            generated_staging_root=tmp_path / "staging",
            asset_library_root=tmp_path / "library",
            admission_date=date(2026, 8, 31),
            allowed_asset_roots=(),
        )

    catalog_path = _materialized_fixture_catalog(tmp_path)
    catalog = load_catalog(catalog_path)
    handler_module._validate_catalog_trust(catalog, allowed_roots=(tmp_path,))
    first = catalog.entries[0]
    first_model = first.models[0]

    unusable = first.model_copy(
        update={
            "available": False,
            "asset_path": str(tmp_path / "not_created"),
            "models": (first_model.model_copy(update={"usable": False}),),
        }
    )
    handler_module._validate_catalog_trust(
        catalog.model_copy(update={"entries": (unusable,)}),
        allowed_roots=(tmp_path,),
    )

    missing_model = first.model_copy(
        update={"models": (first_model.model_copy(update={"model_path": None}),)}
    )
    with pytest.raises(ValueError, match="model_path"):
        handler_module._validate_catalog_trust(
            catalog.model_copy(update={"entries": (missing_model,)}),
            allowed_roots=(tmp_path,),
        )

    missing_visual = first.model_copy(
        update={
            "models": (
                first_model.model_copy(update={"visual_path": str(tmp_path / "missing.glb")}),
            )
        }
    )
    with pytest.raises(ValueError, match="files missing"):
        handler_module._validate_catalog_trust(
            catalog.model_copy(update={"entries": (missing_visual,)}),
            allowed_roots=(tmp_path,),
        )

    urdf_path = Path(first.asset_path) / "mobility.urdf"
    urdf_path.write_text("<robot name='fixture'/>", encoding="utf-8")
    urdf_entry = first.model_copy(
        update={
            "load_type": "urdf",
            "models": (first_model.model_copy(update={"urdf_path": str(urdf_path)}),),
        }
    )
    handler_module._validate_catalog_trust(
        catalog.model_copy(update={"entries": (urdf_entry,)}),
        allowed_roots=(tmp_path,),
    )

    with pytest.raises(ValueError, match="escapes configured roots"):
        handler_module._trusted_path(
            Path("/outside/asset"),
            (tmp_path.resolve(),),
            require="optional",
        )
    with pytest.raises(ValueError, match="directory is missing"):
        handler_module._trusted_path(
            tmp_path / "missing_directory",
            (tmp_path.resolve(),),
            require="directory",
        )


def test_compile_handler_consumes_catalog_snapshot_after_original_locator_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed_root = tmp_path / "allowed"
    outside_root = tmp_path / "outside"
    allowed_catalog = _materialized_fixture_catalog(allowed_root)
    outside_catalog = _materialized_fixture_catalog(outside_root)
    store = LocalArtifactStore(tmp_path / "cas")
    payload = allowed_catalog.read_bytes()
    catalog_ref = ArtifactRef(
        name="mutable_catalog",
        uri=allowed_catalog.resolve().as_uri(),
        media_type="application/json",
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
        schema_version="robotwin.asset_catalog.v1",
    )
    handler = Text2EnvCompileHandler(
        artifact_store=store,
        work_root=tmp_path / "runs",
        generated_staging_root=tmp_path / "generated_staging",
        asset_library_root=tmp_path / "asset_library",
        admission_date=date(2026, 8, 31),
        allowed_asset_roots=(allowed_root,),
    )
    registry, _ = _registry(tmp_path, store=store, handler=handler)
    real_compile = handler_module.compile_scene

    def mutate_original_then_compile(request, **kwargs):
        allowed_catalog.write_bytes(outside_catalog.read_bytes())
        return real_compile(request, **kwargs)

    monkeypatch.setattr(handler_module, "compile_scene", mutate_original_then_compile)
    state = registry.invoke(
        "text2env.compile",
        "1.0.0",
        {
            "request": "Place a can on top of a plate.",
            "seed": 42,
            "asset_catalog": catalog_ref.model_dump(mode="json"),
            "config": {"generate_missing_assets": False},
        },
    )

    assert state.status == RunStatus.SUCCEEDED
    output = Text2EnvCompileOutput.model_validate(state.output)
    resolved = ResolvedSceneSpec.model_validate_json(
        store.resolve(output.resolved_scene).path.read_text(encoding="utf-8")
    )
    source_files = [
        Path(path).resolve()
        for item in resolved.objects
        for path in item.source_files
    ]
    assert source_files
    assert all(path.is_relative_to(allowed_root.resolve()) for path in source_files)
    assert not any(path.is_relative_to(outside_root.resolve()) for path in source_files)


def test_compile_artifact_classifier_rejects_untyped_json_and_handles_binary(
    tmp_path: Path,
) -> None:
    untyped = tmp_path / "untyped.json"
    untyped.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="no schema_version"):
        handler_module._artifact_type(untyped)
    binary = tmp_path / "mesh.glb"
    binary.write_bytes(b"mesh")
    assert handler_module._artifact_type(binary) == ("application/octet-stream", None)


def test_compile_dependencies_bind_asset_library_state_and_stabilize_after_reuse(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    catalog = _put_json(
        store,
        tmp_path / "empty_catalog.json",
        AssetCatalog(
            robotwin_root=str(tmp_path / "RoboTwin"),
            objects_root=str(tmp_path / "objects"),
            entries=(),
        ).canonical_dict(),
        name="empty_catalog",
        schema_version="robotwin.asset_catalog.v1",
    )
    handler = _handler(tmp_path, store)
    resolver = Text2EnvCompileDependencyResolver(
        artifact_store=store,
        handler=handler,
        scene_gen_root=ROOT / "scene_gen",
        ledger_contract_root=(
            ROOT
            / "self_improving/asset_pipeline/active/1_asset_reuse/lib"
        ),
    )
    run_ids = iter(
        (
            UUID("12345678-1234-4234-9234-123456789abc"),
            UUID("22345678-1234-4234-9234-123456789abc"),
            UUID("32345678-1234-4234-9234-123456789abc"),
        )
    )
    registry, _ = _registry(
        tmp_path,
        store=store,
        handler=handler,
        dependency_resolver=resolver,
        run_ids=run_ids,
    )
    parameters = {
        "request": "Place a purple hexagonal pedestal on the table.",
        "seed": 77,
        "asset_catalog": catalog.model_dump(mode="json"),
        "config": {"generate_missing_assets": True},
    }

    first = registry.invoke("text2env.compile", "1.0.0", parameters)
    second = registry.invoke("text2env.compile", "1.0.0", parameters)
    third = registry.invoke("text2env.compile", "1.0.0", parameters)

    assert first.status == second.status == third.status == RunStatus.SUCCEEDED
    assert first.output == second.output == third.output
    assert first.invocation_digest != second.invocation_digest
    assert second.invocation_digest == third.invocation_digest
    first_invocation = registry.invocation(first.run_id)
    second_invocation = registry.invocation(second.run_id)
    assert first_invocation is not None
    assert second_invocation is not None
    assert [dependency.name for dependency in first_invocation.dependencies] == [
        "asset-library-state",
        "catalog-selected-assets",
        "ledger-contract",
        "scene-gen",
        "text2env-compile-config",
    ]
    assert first_invocation.dependencies[0].sha256 != second_invocation.dependencies[0].sha256


def test_compile_dependencies_detect_selected_asset_byte_changes(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    catalog_path = _materialized_fixture_catalog(tmp_path)
    catalog = store.put_file(
        catalog_path,
        name="catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    handler = _handler(tmp_path, store)
    resolver = Text2EnvCompileDependencyResolver(
        artifact_store=store,
        handler=handler,
        scene_gen_root=ROOT / "scene_gen",
        ledger_contract_root=(
            ROOT
            / "self_improving/asset_pipeline/active/1_asset_reuse/lib"
        ),
    )
    registry, _ = _registry(
        tmp_path,
        store=store,
        handler=handler,
        dependency_resolver=resolver,
        run_ids=iter(
            (
                UUID("12345678-1234-4234-9234-123456789abc"),
                UUID("22345678-1234-4234-9234-123456789abc"),
            )
        ),
    )
    parameters = {
        "request": "Place a can on top of a plate.",
        "seed": 42,
        "asset_catalog": catalog.model_dump(mode="json"),
        "config": {"generate_missing_assets": False},
    }

    first = registry.invoke("text2env.compile", "1.0.0", parameters)
    visual = tmp_path / "fixture_assets/071_can/visual/base0.glb"
    visual.write_bytes(visual.read_bytes() + b" changed")
    second = registry.invoke("text2env.compile", "1.0.0", parameters)

    assert first.status == second.status == RunStatus.SUCCEEDED
    assert first.output == second.output
    assert first.invocation_digest != second.invocation_digest


def test_compile_dependency_resolver_rejects_wrong_inputs_and_wraps_artifact_errors(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    catalog = _put_json(
        store,
        tmp_path / "empty_catalog.json",
        AssetCatalog(
            robotwin_root=str(tmp_path / "RoboTwin"),
            objects_root=str(tmp_path / "objects"),
            entries=(),
        ).canonical_dict(),
        name="empty_catalog",
        schema_version="robotwin.asset_catalog.v1",
    )
    handler = _handler(tmp_path, store)
    resolver = Text2EnvCompileDependencyResolver(
        artifact_store=store,
        handler=handler,
        scene_gen_root=ROOT / "scene_gen",
        ledger_contract_root=(
            ROOT / "self_improving/asset_pipeline/active/1_asset_reuse/lib"
        ),
    )

    assert resolver.resolve("text2env.replay@1.0.0", CompileConfig()) == ()
    with pytest.raises(DependencyResolutionError, match="require Text2EnvCompileInput"):
        resolver.resolve("text2env.compile@1.0.0", CompileConfig())

    parameters = {
        "request": "Place a can on the table.",
        "seed": 42,
        "asset_catalog": catalog.model_copy(update={"sha256": "0" * 64}),
        "config": {"generate_missing_assets": False},
    }
    with pytest.raises(DependencyResolutionError, match="dependency resolution failed"):
        resolver.resolve(
            "text2env.compile@1.0.0",
            Text2EnvCompileInput.model_validate(parameters),
        )


def test_dependency_tree_digest_rejects_missing_non_directory_and_symlink_escape(
    tmp_path: Path,
) -> None:
    with pytest.raises(DependencyResolutionError, match="root is missing"):
        dependency_module._tree_digest(tmp_path / "missing", allow_missing=False)

    plain_file = tmp_path / "plain.txt"
    plain_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(DependencyResolutionError, match="not a directory"):
        dependency_module._tree_digest(plain_file, allow_missing=False)

    root = tmp_path / "tree"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("external bytes", encoding="utf-8")
    (root / "escape.txt").symlink_to(outside)
    with pytest.raises(DependencyResolutionError, match="symlink escapes root"):
        dependency_module._tree_digest(root, allow_missing=False)
