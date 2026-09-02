from __future__ import annotations

import json
import shutil
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

import self_improving.system2_replay_snapshot_validation as module
import self_improving.validate_v2_snapshot as snapshot_module
from scene_gen.builder import build_scene_package
from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import EventPage, StoredRunEvent
from self_improving.harness.events import RunEvent
from self_improving.harness.package_store import PackageStore
from self_improving.harness.replay_dependencies import REPLAY_DEPENDENCY_NAMES
from self_improving.harness.runtime_assets import RuntimeAssetStore
from self_improving.harness.schemas import (
    ArtifactRef,
    DependencyRef,
    EnvironmentPackage,
    Event,
    Invocation,
    RunState,
    RunStatus,
    RuntimeConfig,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
    ValidationStatus,
)
from self_improving.system2_replay_evidence_acquisition import (
    System2ReplayEvidenceAcquisitionResult,
)
from self_improving.system2_replay_snapshot_validation import (
    System2ReplaySnapshotValidationError,
    System2ReplaySnapshotValidationResult,
    System2ReplaySnapshotValidator,
)

ROOT = Path(__file__).resolve().parents[2]


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _put_json(
    store: LocalArtifactStore,
    source_root: Path,
    *,
    name: str,
    schema_version: str,
    value: object,
) -> ArtifactRef:
    source_root.mkdir(parents=True, exist_ok=True)
    source = source_root / f"{name}.json"
    source.write_bytes(_canonical_json_bytes(value))
    return store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _package_fixture(
    tmp_path: Path,
) -> tuple[LocalArtifactStore, EnvironmentPackage, ArtifactRef, ArtifactRef]:
    request = "Place a can on the table."
    seed = 19
    spec = parse_rule_based(request, seed=seed)
    base_catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    initial = solve_scene(spec, base_catalog)
    selected = {(item.asset_id, item.model_id) for item in initial.objects}

    source_root = tmp_path / "private-source-root"
    robotwin_root = source_root / "RoboTwin"
    objects_root = robotwin_root / "assets" / "objects"
    entries = []
    sources_by_model: dict[tuple[str, int], tuple[str, ...]] = {}
    for entry in base_catalog.entries:
        selected_models = [
            model for model in entry.models if (entry.asset_id, model.model_id) in selected
        ]
        if not selected_models:
            continue
        asset_root = objects_root / entry.asset_id
        models = []
        for model in selected_models:
            model_id = model.model_id
            metadata = asset_root / f"model_data{model_id}.json"
            visual = asset_root / "visual" / f"base{model_id}.obj"
            collision = asset_root / "collision" / f"base{model_id}.obj"
            material = asset_root / "materials" / f"base{model_id}.mtl"
            texture = asset_root / "textures" / f"base{model_id}.png"
            payloads = {
                metadata: b'{"scale":[1,1,1]}\n',
                visual: f"mtllib ../materials/base{model_id}.mtl\n".encode(),
                collision: b"v 0 0 0\n",
                material: f"map_Kd ../textures/base{model_id}.png\n".encode(),
                texture: f"texture-{entry.asset_id}-{model_id}".encode(),
            }
            for path, payload in payloads.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            source_files = tuple(str(path) for path in (collision, metadata, visual))
            sources_by_model[(entry.asset_id, model_id)] = source_files
            models.append(
                model.model_copy(
                    update={
                        "model_path": str(asset_root),
                        "metadata_path": str(metadata),
                        "visual_path": str(visual),
                        "collision_path": str(collision),
                        "urdf_path": None,
                    }
                )
            )
        entries.append(
            entry.model_copy(
                update={
                    "asset_path": str(asset_root),
                    "models": tuple(models),
                }
            )
        )
    catalog = AssetCatalog(
        robotwin_root=str(robotwin_root),
        objects_root=str(objects_root),
        entries=tuple(entries),
    )
    resolved = initial.model_copy(
        update={
            "asset_catalog_sha256": catalog.digest(),
            "objects": tuple(
                item.model_copy(
                    update={"source_files": sources_by_model[(item.asset_id, item.model_id)]}
                )
                for item in initial.objects
            ),
        }
    )

    store = LocalArtifactStore(tmp_path / "replay-cas")
    package_root = tmp_path / "package"
    build_scene_package(spec, resolved, package_root)
    published = PackageStore(store).publish(package_root)
    catalog_source = tmp_path / "asset_catalog.json"
    catalog_source.write_bytes(_canonical_json_bytes(catalog.canonical_dict())[:-1])
    catalog_ref = store.put_file(
        catalog_source,
        name="asset_catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    snapshot = RuntimeAssetStore(store).snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(source_root,),
    )
    runtime = {
        "schema_version": "robotwin.scene_runtime_evidence.v2",
        "scene_id": resolved.scene_id,
        "seed": resolved.seed,
        "resolved_scene_sha256": resolved.digest(),
        "runtime_asset_snapshot_sha256": snapshot.manifest.sha256,
        "status": "pass",
        "robot_initial_collision_count": 0,
        "video_frame_count": 3,
        "unique_video_frame_count": 3,
        "base_simulation_step_count": 3,
        "simulation_step_count": 3,
        "settle_extra_steps": 0,
        "video_sample_step_indices": [0, 1, 2],
        "relations": {},
        "objects": {
            item.object_id: {
                "translation_drift_m": 0.06,
                "rotation_drift_deg": 45.0,
                "resolved_translation_error_m": 0.06,
                "resolved_rotation_error_deg": 45.0,
                "penetration_count": 0,
                "still_moving": False,
                "support_contact": True,
                "support_contact_fraction": 1.0,
                "unexpected_contact_fraction": 0.0,
                "unexpected_contact_targets": [],
                "support_mode": (
                    "fixed_static_pose"
                    if item.is_static and item.support_relation.value == "on_table"
                    else f"{item.support_relation.value}_contact"
                ),
                "support_target": item.support_target,
                "support_footprint_margin_m": None,
                "inside_contained": None,
                "dropped": False,
                "visible_pixels": 512,
            }
            for item in resolved.objects
        },
    }
    runtime_ref = _put_json(
        store,
        tmp_path / "published",
        name="runtime_evidence",
        schema_version="robotwin.scene_runtime_evidence.v2",
        value=runtime,
    )
    package = EnvironmentPackage(
        package_id=resolved.digest(),
        route_id="text2env",
        producer_skill_ref="text2env.compile@1.0.0",
        seed=resolved.seed,
        scene_spec_sha256=spec.digest(),
        resolved_scene_sha256=resolved.digest(),
        asset_catalog=catalog_ref,
        package_manifest=published.manifest,
    )
    return store, package, runtime_ref, snapshot.manifest


def _successful_acquisition(
    tmp_path: Path,
) -> tuple[System2ReplayEvidenceAcquisitionResult, LocalArtifactStore]:
    store, package, runtime_evidence, snapshot = _package_fixture(tmp_path)
    replay_input = Text2EnvReplayInput(
        environment_package=package,
        runtime_config=RuntimeConfig(
            precheck_steps=0,
            settle_steps=9,
            contact_window_steps=3,
            video_frames=3,
            fps=3,
        ),
    )
    typed_output = Text2EnvReplayOutput(
        runtime_evidence=runtime_evidence,
        replay_artifacts=(runtime_evidence, snapshot),
    )
    run_id = UUID("95000000-0000-4000-8000-000000000001")
    invocation_digest = "9" * 64
    invocation = Invocation(
        run_id=run_id,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        effective_parameters=replay_input.model_dump(mode="json"),
        dependencies=tuple(
            DependencyRef(
                name=name,
                version="1",
                sha256=(snapshot.sha256 if name.endswith(".runtime_assets") else str(index) * 64),
            )
            for index, name in enumerate(sorted(REPLAY_DEPENDENCY_NAMES))
        ),
        max_attempts=2,
        invocation_digest=invocation_digest,
    )
    started = datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 9, 2, 6, 1, tzinfo=timezone.utc)
    events = (
        Event(
            seq=1,
            timestamp=started,
            stage="preflight",
            attempt=1,
            from_status=None,
            to_status=RunStatus.RUNNING,
            artifact_refs=(),
        ),
        Event(
            seq=2,
            timestamp=ended,
            stage="complete",
            attempt=1,
            from_status=RunStatus.RUNNING,
            to_status=RunStatus.SUCCEEDED,
            artifact_refs=(runtime_evidence, snapshot),
        ),
    )
    state = RunState(
        run_id=run_id,
        invocation_digest=invocation_digest,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        status=RunStatus.SUCCEEDED,
        attempt=1,
        max_attempts=2,
        started_at=started,
        ended_at=ended,
        events=events,
        artifacts=(runtime_evidence, snapshot),
        output=typed_output.model_dump(mode="json"),
        blocker=None,
    )
    stored = tuple(
        StoredRunEvent(
            event_id=index,
            envelope=RunEvent(
                run_id=run_id,
                skill_id="text2env.replay",
                skill_version="1.0.0",
                event=event,
            ),
        )
        for index, event in enumerate(events, start=1)
    )
    journal_path = tmp_path / "replay-state" / "harness.sqlite3"
    journal_path.parent.mkdir()
    journal_path.write_bytes(b"local fixture")
    return (
        System2ReplayEvidenceAcquisitionResult(
            run_state=state,
            invocation=invocation,
            event_page=EventPage(events=stored, last_event_id=2, has_more=False),
            typed_output=typed_output,
            artifact_root=store.root,
            journal_path=journal_path,
        ),
        store,
    )


def _cas_object_names(store: LocalArtifactStore) -> frozenset[str]:
    root = store.root / "sha256"
    return frozenset(path.name for path in root.glob("*/*") if path.is_file())


def _replace_output(
    acquisition: System2ReplayEvidenceAcquisitionResult,
    output: Text2EnvReplayOutput,
    *,
    terminal_artifacts: tuple[ArtifactRef, ...] | None = None,
) -> System2ReplayEvidenceAcquisitionResult:
    artifacts = (
        acquisition.run_state.artifacts if terminal_artifacts is None else terminal_artifacts
    )
    return replace(
        acquisition,
        typed_output=output,
        run_state=acquisition.run_state.model_copy(
            update={
                "artifacts": artifacts,
                "output": output.model_dump(mode="json"),
            }
        ),
    )


def _replace_runtime_evidence(
    acquisition: System2ReplayEvidenceAcquisitionResult,
    replacement: ArtifactRef,
) -> System2ReplayEvidenceAcquisitionResult:
    output = acquisition.typed_output
    assert output is not None
    previous = output.runtime_evidence
    replay_artifacts = tuple(
        replacement if ref == previous else ref for ref in output.replay_artifacts
    )
    artifacts = tuple(
        replacement if ref == previous else ref for ref in acquisition.run_state.artifacts
    )
    return _replace_output(
        acquisition,
        output.model_copy(
            update={
                "runtime_evidence": replacement,
                "replay_artifacts": replay_artifacts,
            }
        ),
        terminal_artifacts=artifacts,
    )


def test_public_replay_snapshot_validation_types_are_importable() -> None:
    assert System2ReplaySnapshotValidationError
    assert System2ReplaySnapshotValidationResult
    assert System2ReplaySnapshotValidator


def test_successful_replay_recomputes_snapshot_report_in_the_same_local_cas(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    result = System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
        acquisition=acquisition
    )

    assert type(result) is System2ReplaySnapshotValidationResult
    assert result.replay_run_id == acquisition.run_state.run_id
    assert result.replay_invocation_digest == acquisition.invocation.invocation_digest
    assert result.runtime_evidence == acquisition.typed_output.runtime_evidence
    assert result.runtime_asset_snapshot_manifest.name == "runtime_asset_snapshot"
    assert result.snapshot_validation.validation_status is ValidationStatus.PASS
    assert result.snapshot_validation.fail_count == 0
    assert result.snapshot_validation.not_run_count == 0
    report_bytes = store.resolve(result.snapshot_validation.validation_report).path.read_bytes()
    report = json.loads(report_bytes)
    assert report_bytes == _canonical_json_bytes(report)
    assert report["status"] == "pass"
    assert {"decision", "publishable", "validation_decision_receipt"}.isdisjoint(report)
    assert list(scratch_parent.iterdir()) == []
    assert acquisition.invocation.max_attempts == 2
    assert tuple(item.name for item in acquisition.invocation.dependencies) == tuple(
        sorted(REPLAY_DEPENDENCY_NAMES)
    )


def test_runtime_snapshot_must_match_the_complete_upstream_dependency_set(
    tmp_path: Path,
) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    invocation = acquisition.invocation
    assert invocation is not None
    changed = tuple(
        item.model_copy(update={"sha256": "f" * 64})
        if item.name == "text2env.replay.runtime_assets"
        else item
        for item in invocation.dependencies
    )
    acquisition = replace(
        acquisition,
        invocation=invocation.model_copy(update={"dependencies": changed}),
    )
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
            acquisition=acquisition
        )

    assert raised.value.reason == "runtime_asset_snapshot_invalid"
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.parametrize("status", [RunStatus.BLOCKED, RunStatus.FAILED])
def test_non_successful_replay_stops_before_snapshot_recomputation(
    tmp_path: Path,
    status: RunStatus,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    state = acquisition.run_state.model_copy(update={"status": status})
    acquisition = replace(acquisition, run_state=state, invocation=None, typed_output=None)
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()
    before = _cas_object_names(store)

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
            acquisition=acquisition
        )

    assert raised.value.reason == "replay_not_succeeded"
    assert _cas_object_names(store) == before
    assert list(scratch_parent.iterdir()) == []


def test_interface_requires_the_exact_acquisition_record(tmp_path: Path) -> None:
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()
    validator = System2ReplaySnapshotValidator(scratch_parent=scratch_parent)

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        validator.recompute(acquisition={})  # type: ignore[arg-type]

    assert raised.value.reason == "acquisition_invalid"


def test_scratch_must_be_canonical_existing_and_disjoint_from_the_cas(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    missing = tmp_path / "missing-scratch"
    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=missing)
    assert raised.value.reason == "scratch_invalid"

    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()
    noncanonical = scratch_parent / ".." / "snapshot-scratch"
    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=noncanonical)
    assert raised.value.reason == "scratch_invalid"

    before = _cas_object_names(store)
    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=store.root).recompute(acquisition=acquisition)
    assert raised.value.reason == "scratch_invalid"
    assert _cas_object_names(store) == before

    with pytest.raises(TypeError, match="scratch_parent must be Path"):
        System2ReplaySnapshotValidator(scratch_parent="scratch")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "case",
    [
        "missing_dependency",
        "unexpected_dependency",
        "wrong_dependency_type",
        "wrong_dependency_version",
        "invalid_dependency_digest",
        "nonstandard_attempt_limit",
    ],
)
def test_replay_invocation_must_keep_the_factory_owned_shape(
    tmp_path: Path,
    case: str,
) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    invocation = acquisition.invocation
    assert invocation is not None
    state = acquisition.run_state
    if case == "missing_dependency":
        invocation = invocation.model_copy(update={"dependencies": invocation.dependencies[:-1]})
    elif case == "unexpected_dependency":
        invocation = invocation.model_copy(
            update={
                "dependencies": (
                    *invocation.dependencies,
                    DependencyRef(
                        name="text2env.replay.unexpected",
                        version="1",
                        sha256="e" * 64,
                    ),
                )
            }
        )
    elif case == "wrong_dependency_type":
        invocation = invocation.model_copy(
            update={"dependencies": (*invocation.dependencies[:-1], object())}
        )
    elif case == "wrong_dependency_version":
        changed = invocation.dependencies[0].model_copy(update={"version": "2"})
        invocation = invocation.model_copy(
            update={"dependencies": (changed, *invocation.dependencies[1:])}
        )
    elif case == "invalid_dependency_digest":
        changed = invocation.dependencies[0].model_copy(update={"sha256": "invalid"})
        invocation = invocation.model_copy(
            update={"dependencies": (changed, *invocation.dependencies[1:])}
        )
    else:
        invocation = invocation.model_copy(update={"max_attempts": 3})
        state = state.model_copy(update={"max_attempts": 3})
    acquisition = replace(acquisition, invocation=invocation, run_state=state)
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
            acquisition=acquisition
        )

    assert raised.value.reason == "replay_binding_invalid"
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.parametrize(
    "case",
    [
        "run_id",
        "digest",
        "skill",
        "attempt",
        "effective_parameters",
        "state_output",
        "typed_output",
    ],
)
def test_invocation_state_and_typed_output_must_remain_mutually_bound(
    tmp_path: Path,
    case: str,
) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    invocation = acquisition.invocation
    output = acquisition.typed_output
    assert invocation is not None
    assert output is not None
    state = acquisition.run_state
    if case == "run_id":
        invocation = invocation.model_copy(
            update={"run_id": UUID("95000000-0000-4000-8000-000000000002")}
        )
    elif case == "digest":
        invocation = invocation.model_copy(update={"invocation_digest": "7" * 64})
    elif case == "skill":
        state = state.model_copy(update={"skill_version": "2.0.0"})
    elif case == "attempt":
        state = state.model_copy(update={"attempt": 0})
    elif case == "effective_parameters":
        invocation = invocation.model_copy(
            update={"effective_parameters": {"environment_package": {}}}
        )
    elif case == "state_output":
        state = state.model_copy(update={"output": {"runtime_evidence": {}}})
    else:
        output = output.model_copy(update={"replay_artifacts": ()})
    acquisition = replace(
        acquisition,
        invocation=invocation,
        run_state=state,
        typed_output=output,
    )
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
            acquisition=acquisition
        )

    assert raised.value.reason == "replay_binding_invalid"
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "duplicate",
        "wrong_schema",
        "wrong_media",
        "noncanonical",
        "not_terminal",
    ],
)
def test_snapshot_ref_must_be_one_exact_typed_terminal_artifact(
    tmp_path: Path,
    case: str,
) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    output = acquisition.typed_output
    assert output is not None
    snapshot = next(ref for ref in output.replay_artifacts if ref.name == "runtime_asset_snapshot")
    runtime = output.runtime_evidence
    terminal = acquisition.run_state.artifacts
    if case == "missing":
        replay_artifacts = tuple(ref for ref in output.replay_artifacts if ref != snapshot)
    elif case == "duplicate":
        replay_artifacts = (*output.replay_artifacts, snapshot)
    elif case == "wrong_schema":
        changed = snapshot.model_copy(update={"schema_version": "wrong.snapshot.v1"})
        replay_artifacts = tuple(
            changed if ref == snapshot else ref for ref in output.replay_artifacts
        )
        terminal = tuple(changed if ref == snapshot else ref for ref in terminal)
    elif case == "wrong_media":
        changed = snapshot.model_copy(update={"media_type": "application/octet-stream"})
        replay_artifacts = tuple(
            changed if ref == snapshot else ref for ref in output.replay_artifacts
        )
        terminal = tuple(changed if ref == snapshot else ref for ref in terminal)
    elif case == "noncanonical":
        changed = snapshot.model_copy(update={"uri": "file:///copied/runtime_asset_snapshot.json"})
        replay_artifacts = tuple(
            changed if ref == snapshot else ref for ref in output.replay_artifacts
        )
        terminal = tuple(changed if ref == snapshot else ref for ref in terminal)
    else:
        replay_artifacts = output.replay_artifacts
        terminal = (runtime,)
    acquisition = _replace_output(
        acquisition,
        output.model_copy(update={"replay_artifacts": replay_artifacts}),
        terminal_artifacts=terminal,
    )
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
            acquisition=acquisition
        )

    assert raised.value.reason == "runtime_asset_snapshot_invalid"
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.parametrize(
    "case",
    ["wrong_name", "wrong_schema", "wrong_media", "noncanonical", "not_terminal"],
)
def test_runtime_evidence_ref_must_be_exact_typed_and_terminal(
    tmp_path: Path,
    case: str,
) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    output = acquisition.typed_output
    assert output is not None
    runtime = output.runtime_evidence
    updates = {
        "wrong_name": {"name": "copied_runtime_evidence"},
        "wrong_schema": {"schema_version": "wrong.runtime.v1"},
        "wrong_media": {"media_type": "application/octet-stream"},
        "noncanonical": {"uri": "file:///copied/runtime_evidence.json"},
    }
    if case == "not_terminal":
        changed = runtime
    else:
        changed = runtime.model_copy(update=updates[case])
        acquisition = _replace_runtime_evidence(acquisition, changed)
    if case == "not_terminal":
        snapshot = next(
            ref for ref in output.replay_artifacts if ref.name == "runtime_asset_snapshot"
        )
        acquisition = _replace_output(
            acquisition,
            output,
            terminal_artifacts=(snapshot,),
        )
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
            acquisition=acquisition
        )

    assert raised.value.reason == "runtime_evidence_invalid"
    assert list(scratch_parent.iterdir()) == []


def test_missing_cas_bytes_stop_before_the_snapshot_adapter(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    output = acquisition.typed_output
    assert output is not None
    missing = store.resolve(output.runtime_evidence).path
    missing.unlink()
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
            acquisition=acquisition
        )

    assert raised.value.reason == "replay_artifact_invalid"
    assert list(scratch_parent.iterdir()) == []


def test_real_snapshot_adapter_physical_failure_is_a_normal_result(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    output = acquisition.typed_output
    assert output is not None
    runtime = json.loads(store.resolve(output.runtime_evidence).path.read_bytes())
    runtime["status"] = "fail"
    replacement = _put_json(
        store,
        tmp_path / "failed-runtime",
        name="runtime_evidence",
        schema_version="robotwin.scene_runtime_evidence.v2",
        value=runtime,
    )
    acquisition = _replace_runtime_evidence(acquisition, replacement)
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    result = System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
        acquisition=acquisition
    )

    assert result.snapshot_validation.validation_status is ValidationStatus.FAIL
    assert result.snapshot_validation.fail_count == 1
    assert result.snapshot_validation.not_run_count == 0
    assert list(scratch_parent.iterdir()) == []


def test_real_snapshot_adapter_incomplete_report_is_a_normal_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_with_one_not_run(*args: object, **kwargs: object) -> dict[str, object]:
        report = real_validate(*args, **kwargs)
        check = next(
            value for value in report["checks"] if value["name"].startswith("workspace_bounds:")
        )
        check["status"] = "not_run"
        report["fail_count"] = sum(value["status"] == "fail" for value in report["checks"])
        report["not_run_count"] = sum(value["status"] == "not_run" for value in report["checks"])
        report["status"] = "incomplete"
        return report

    monkeypatch.setattr(snapshot_module, "validate_resolved_scene", validate_with_one_not_run)
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    result = System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
        acquisition=acquisition
    )

    assert result.snapshot_validation.validation_status is ValidationStatus.INCOMPLETE
    assert result.snapshot_validation.fail_count == 0
    assert result.snapshot_validation.not_run_count == 1
    assert list(scratch_parent.iterdir()) == []


def test_adapter_input_failure_maps_to_one_stable_public_reason(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    output = acquisition.typed_output
    assert output is not None
    runtime = json.loads(store.resolve(output.runtime_evidence).path.read_bytes())
    runtime["scene_id"] = "different-scene"
    replacement = _put_json(
        store,
        tmp_path / "invalid-runtime-binding",
        name="runtime_evidence",
        schema_version="robotwin.scene_runtime_evidence.v2",
        value=runtime,
    )
    acquisition = _replace_runtime_evidence(acquisition, replacement)
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()
    before = _cas_object_names(store)

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
            acquisition=acquisition
        )

    assert raised.value.reason == "snapshot_recompute_failed"
    assert raised.value.__cause__ is not None
    assert _cas_object_names(store) == before
    assert list(scratch_parent.iterdir()) == []


def test_two_independent_cas_copies_produce_identical_report_bytes(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    first_root = tmp_path / "first-cas"
    second_root = tmp_path / "second-cas"
    shutil.copytree(store.root, first_root)
    shutil.copytree(store.root, second_root)
    first_scratch = tmp_path / "first-scratch"
    second_scratch = tmp_path / "second-scratch"
    first_scratch.mkdir()
    second_scratch.mkdir()

    first = System2ReplaySnapshotValidator(scratch_parent=first_scratch).recompute(
        acquisition=replace(acquisition, artifact_root=first_root)
    )
    second = System2ReplaySnapshotValidator(scratch_parent=second_scratch).recompute(
        acquisition=replace(acquisition, artifact_root=second_root)
    )

    first_store = LocalArtifactStore(first_root)
    second_store = LocalArtifactStore(second_root)
    first_bytes = first_store.resolve(first.snapshot_validation.validation_report).path.read_bytes()
    second_bytes = second_store.resolve(
        second.snapshot_validation.validation_report
    ).path.read_bytes()
    assert first.snapshot_validation.validation_report.sha256 == (
        second.snapshot_validation.validation_report.sha256
    )
    assert first_bytes == second_bytes
    assert list(first_scratch.iterdir()) == []
    assert list(second_scratch.iterdir()) == []


def test_result_surface_and_report_make_no_validate_v2_or_publication_claim(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    result = System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
        acquisition=acquisition
    )

    assert tuple(field.name for field in fields(result)) == (
        "replay_run_id",
        "replay_invocation_digest",
        "runtime_evidence",
        "runtime_asset_snapshot_manifest",
        "snapshot_validation",
    )
    report = json.loads(
        store.resolve(result.snapshot_validation.validation_report).path.read_bytes()
    )
    serialized = json.dumps(report, sort_keys=True)
    assert "publishable" not in serialized
    assert "decision" not in serialized
    assert "validation_decision_receipt" not in serialized
    binding = next(
        check for check in report["checks"] if check["name"] == "snapshot_validation_binding"
    )
    replay_input = Text2EnvReplayInput.model_validate(
        acquisition.invocation.effective_parameters,
        strict=True,
    )
    assert binding["evidence"] == {
        "asset_catalog_sha256": replay_input.environment_package.asset_catalog.sha256,
        "environment_package_id": replay_input.environment_package.package_id,
        "gate_profile": "robotwin.scene_validation.v1",
        "package_manifest_sha256": replay_input.environment_package.package_manifest.sha256,
        "runtime_asset_snapshot_sha256": result.runtime_asset_snapshot_manifest.sha256,
        "runtime_evidence_sha256": result.runtime_evidence.sha256,
    }


@pytest.mark.parametrize(
    "case",
    [
        "wrong_type",
        "wrong_ref",
        "boolean_count",
        "mismatched_count",
        "missing_report",
        "invalid_report_json",
    ],
)
def test_malformed_snapshot_adapter_result_is_not_returned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    real_adapter = module.ValidateV2SnapshotAdapter

    class AlteredResultAdapter:
        def __init__(self, **kwargs: object) -> None:
            self._inner = real_adapter(**kwargs)

        def recompute(self, request: object) -> object:
            result = self._inner.recompute(request)
            if case == "wrong_type":
                return object()
            if case == "wrong_ref":
                return replace(
                    result,
                    validation_report=result.validation_report.model_copy(
                        update={"name": "other_report"}
                    ),
                )
            if case == "boolean_count":
                return replace(result, fail_count=False)
            if case == "mismatched_count":
                return replace(result, fail_count=result.fail_count + 1)
            if case == "invalid_report_json":
                source = tmp_path / "invalid-report.json"
                source.write_bytes(b"{")
                invalid_ref = store.put_file(
                    source,
                    name="snapshot_validation_report",
                    media_type="application/json",
                    schema_version="robotwin.scene_validation.v1",
                )
                return replace(result, validation_report=invalid_ref)
            absent = ArtifactRef(
                name="snapshot_validation_report",
                uri=f"artifact://sha256/{'f' * 64}",
                media_type="application/json",
                sha256="f" * 64,
                bytes=1,
                schema_version="robotwin.scene_validation.v1",
            )
            return replace(result, validation_report=absent)

    monkeypatch.setattr(module, "ValidateV2SnapshotAdapter", AlteredResultAdapter)
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
            acquisition=acquisition
        )

    assert raised.value.reason == "snapshot_result_invalid"
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.parametrize("case", ["not_path", "missing", "noncanonical", "not_directory"])
def test_acquisition_artifact_root_must_be_one_canonical_local_cas(
    tmp_path: Path,
    case: str,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    if case == "not_path":
        root: object = str(store.root)
    elif case == "missing":
        root = tmp_path / "missing-cas"
    elif case == "noncanonical":
        root = store.root / ".." / store.root.name
    else:
        root = tmp_path / "cas-file"
        root.write_text("not a directory", encoding="utf-8")
    acquisition = replace(acquisition, artifact_root=root)  # type: ignore[arg-type]
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        System2ReplaySnapshotValidator(scratch_parent=scratch_parent).recompute(
            acquisition=acquisition
        )

    assert raised.value.reason == "artifact_root_invalid"
    assert list(scratch_parent.iterdir()) == []


def test_constructed_acquisition_fields_are_rechecked(tmp_path: Path) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "snapshot-scratch"
    scratch_parent.mkdir()
    validator = System2ReplaySnapshotValidator(scratch_parent=scratch_parent)

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        validator.recompute(acquisition=replace(acquisition, run_state=object()))  # type: ignore[arg-type]
    assert raised.value.reason == "acquisition_invalid"

    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        validator.recompute(acquisition=replace(acquisition, typed_output=None))
    assert raised.value.reason == "replay_binding_invalid"

    output = acquisition.typed_output
    assert output is not None
    invalid_output = output.model_copy(update={"replay_artifacts": (object(),)})
    invalid = replace(
        acquisition,
        typed_output=invalid_output,
    )
    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        validator.recompute(acquisition=invalid)
    assert raised.value.reason == "replay_binding_invalid"

    list_output = output.model_copy(update={"replay_artifacts": list(output.replay_artifacts)})
    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        validator.recompute(acquisition=replace(acquisition, typed_output=list_output))
    assert raised.value.reason == "replay_binding_invalid"

    class DerivedArtifactRef(ArtifactRef):
        pass

    derived = DerivedArtifactRef.model_validate(
        output.replay_artifacts[0].model_dump(mode="python")
    )
    derived_output = output.model_copy(
        update={"replay_artifacts": (derived, *output.replay_artifacts[1:])}
    )
    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        validator.recompute(acquisition=replace(acquisition, typed_output=derived_output))
    assert raised.value.reason == "replay_binding_invalid"

    invalid_state = acquisition.run_state.model_copy(
        update={"artifacts": (*acquisition.run_state.artifacts, object())}
    )
    with pytest.raises(System2ReplaySnapshotValidationError) as raised:
        validator.recompute(acquisition=replace(acquisition, run_state=invalid_state))
    assert raised.value.reason == "replay_binding_invalid"
