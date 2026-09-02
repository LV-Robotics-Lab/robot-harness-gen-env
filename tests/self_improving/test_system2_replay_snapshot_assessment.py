from __future__ import annotations

import json
from dataclasses import fields, replace
from datetime import datetime, timezone
from inspect import signature
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

import self_improving.validate_v2_snapshot as snapshot_module
from scene_gen.builder import build_scene_package
from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene
from self_improving.harness.artifacts import (
    ArtifactResolutionError,
    LocalArtifactStore,
    ResolvedArtifact,
)
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
from self_improving.system2_replay_snapshot_assessment import (
    System2ReplaySnapshotAssessment,
    System2ReplaySnapshotAssessmentError,
    System2ReplaySnapshotAssessmentRecorder,
    System2ReplaySnapshotAssessmentResult,
    System2ReplaySnapshotAssessmentVerifier,
    VerifiedSystem2ReplaySnapshotAssessment,
)
from self_improving.validate_v2_snapshot import SnapshotValidationResult

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
    """Build local typed CAS evidence; this is not a deployed replay fixture."""

    request = "Place a can on the table."
    seed = 19
    spec = parse_rule_based(request, seed=seed)
    base_catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    initial = solve_scene(spec, base_catalog)
    selected = {(item.asset_id, item.model_id) for item in initial.objects}

    source_root = tmp_path / "local-source-root"
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
    journal_path.write_bytes(b"local typed fixture; not a deployed replay")
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
    new_output = output.model_copy(
        update={
            "runtime_evidence": replacement,
            "replay_artifacts": replay_artifacts,
        }
    )
    return replace(
        acquisition,
        typed_output=new_output,
        run_state=acquisition.run_state.model_copy(
            update={
                "artifacts": artifacts,
                "output": new_output.model_dump(mode="json"),
            }
        ),
    )


def test_public_record_seam_is_exposed(tmp_path: Path) -> None:
    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()
    store = LocalArtifactStore(tmp_path / "cas")

    recorder = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    )
    verifier = System2ReplaySnapshotAssessmentVerifier(
        artifact_store=store,
        scratch_parent=scratch_parent,
    )

    assert callable(recorder.record)
    assert tuple(signature(recorder.record).parameters) == ("acquisition",)
    assert tuple(field.name for field in fields(System2ReplaySnapshotAssessmentResult)) == (
        "assessment",
        "snapshot_validation",
    )
    assert callable(verifier.verify)
    assert tuple(signature(verifier.verify).parameters) == ("assessment",)
    assert tuple(field.name for field in fields(VerifiedSystem2ReplaySnapshotAssessment)) == (
        "assessment",
        "record",
        "snapshot_validation",
    )


def test_unsuccessful_replay_maps_to_snapshot_validation_failed(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    acquisition = replace(
        acquisition,
        run_state=acquisition.run_state.model_copy(update={"status": RunStatus.BLOCKED}),
    )
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    before = _cas_object_names(store)

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentRecorder(
            scratch_parent=scratch_parent,
        ).record(acquisition=acquisition)

    assert raised.value.reason == "snapshot_validation_failed"
    assert str(raised.value) == (
        "System 2 replay snapshot assessment stopped: snapshot_validation_failed"
    )
    assert _cas_object_names(store) == before
    assert list(scratch_parent.iterdir()) == []


def test_assessment_cas_write_failure_maps_to_assessment_publish_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    real_put_file = LocalArtifactStore.put_file

    def fail_assessment_write(
        target: LocalArtifactStore,
        source: Path,
        *,
        name: str,
        media_type: str,
        schema_version: str | None,
    ) -> ArtifactRef:
        if name == "system2_replay_snapshot_assessment":
            raise OSError("local CAS write unavailable")
        return real_put_file(
            target,
            source,
            name=name,
            media_type=media_type,
            schema_version=schema_version,
        )

    monkeypatch.setattr(LocalArtifactStore, "put_file", fail_assessment_write)

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentRecorder(
            scratch_parent=scratch_parent,
        ).record(acquisition=acquisition)

    assert raised.value.reason == "assessment_publish_failed"
    assert list(scratch_parent.iterdir()) == []


def test_assessment_readback_failure_maps_to_assessment_verification_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    real_resolve = LocalArtifactStore.resolve

    def fail_assessment_readback(
        target: LocalArtifactStore,
        ref: ArtifactRef,
    ) -> ResolvedArtifact:
        if ref.name == "system2_replay_snapshot_assessment":
            raise ArtifactResolutionError("not_found", "assessment unavailable")
        return real_resolve(target, ref)

    monkeypatch.setattr(LocalArtifactStore, "resolve", fail_assessment_readback)

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentRecorder(
            scratch_parent=scratch_parent,
        ).record(acquisition=acquisition)

    assert raised.value.reason == "assessment_verification_failed"
    assert list(scratch_parent.iterdir()) == []


def test_assessment_ref_header_mismatch_maps_to_assessment_verification_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    real_put_file = LocalArtifactStore.put_file

    def publish_wrong_assessment_header(
        target: LocalArtifactStore,
        source: Path,
        *,
        name: str,
        media_type: str,
        schema_version: str | None,
    ) -> ArtifactRef:
        ref = real_put_file(
            target,
            source,
            name=name,
            media_type=media_type,
            schema_version=schema_version,
        )
        if name == "system2_replay_snapshot_assessment":
            return ref.model_copy(update={"schema_version": "harness.wrong.v1"})
        return ref

    monkeypatch.setattr(LocalArtifactStore, "put_file", publish_wrong_assessment_header)

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentRecorder(
            scratch_parent=scratch_parent,
        ).record(acquisition=acquisition)

    assert raised.value.reason == "assessment_verification_failed"
    assert list(scratch_parent.iterdir()) == []


def test_assessment_changed_readback_maps_to_assessment_verification_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, _store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    changed = tmp_path / "changed-assessment.json"
    changed.write_bytes(b"{}\n")
    real_resolve = LocalArtifactStore.resolve

    def resolve_changed_assessment(
        target: LocalArtifactStore,
        ref: ArtifactRef,
    ) -> ResolvedArtifact:
        if ref.name == "system2_replay_snapshot_assessment":
            return ResolvedArtifact(ref=ref, path=changed)
        return real_resolve(target, ref)

    monkeypatch.setattr(LocalArtifactStore, "resolve", resolve_changed_assessment)

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentRecorder(
            scratch_parent=scratch_parent,
        ).record(acquisition=acquisition)

    assert raised.value.reason == "assessment_verification_failed"
    assert list(scratch_parent.iterdir()) == []


def test_successful_local_replay_records_a_canonical_snapshot_assessment(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()

    result = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)

    assert type(result) is System2ReplaySnapshotAssessmentResult
    assert result.snapshot_validation.replay_run_id == acquisition.run_state.run_id
    assert result.snapshot_validation.snapshot_validation.validation_status is ValidationStatus.PASS
    assert result.assessment.name == "system2_replay_snapshot_assessment"
    assert result.assessment.schema_version == "harness.system2_replay_snapshot_assessment.v1"
    assert result.assessment.media_type == "application/json"
    assert result.assessment.uri == f"artifact://sha256/{result.assessment.sha256}"
    payload = store.resolve(result.assessment).path.read_bytes()
    assessment = json.loads(payload)
    assert payload == _canonical_json_bytes(assessment)
    assert set(assessment) == {
        "dependencies",
        "fail_count",
        "not_run_count",
        "replay_ended_at",
        "replay_input",
        "replay_invocation_digest",
        "replay_run_id",
        "replay_skill_ref",
        "replay_started_at",
        "runtime_asset_snapshot_manifest",
        "runtime_evidence",
        "schema_version",
        "scope",
        "max_attempts",
        "validation_report",
        "validation_status",
    }
    assert assessment["schema_version"] == "harness.system2_replay_snapshot_assessment.v1"
    assert assessment["scope"] == "local_cas_snapshot_recomputation"
    assert assessment["replay_run_id"] == str(acquisition.run_state.run_id)
    assert assessment["replay_invocation_digest"] == acquisition.invocation.invocation_digest
    assert assessment["replay_skill_ref"] == "text2env.replay@1.0.0"
    assert assessment["max_attempts"] == 2
    assert assessment["replay_started_at"] == "2026-09-02T06:00:00Z"
    assert assessment["replay_ended_at"] == "2026-09-02T06:01:00Z"
    assert assessment["replay_input"] == acquisition.invocation.effective_parameters
    assert assessment["dependencies"] == [
        dependency.model_dump(mode="json") for dependency in acquisition.invocation.dependencies
    ]
    assert assessment["runtime_evidence"] == (
        result.snapshot_validation.runtime_evidence.model_dump(mode="json")
    )
    assert assessment["runtime_asset_snapshot_manifest"] == (
        result.snapshot_validation.runtime_asset_snapshot_manifest.model_dump(mode="json")
    )
    assert assessment["validation_report"] == (
        result.snapshot_validation.snapshot_validation.validation_report.model_dump(mode="json")
    )
    assert assessment["validation_status"] == "pass"
    assert assessment["fail_count"] == 0
    assert assessment["not_run_count"] == 0
    serialized = json.dumps(assessment, sort_keys=True)
    assert all(
        forbidden not in serialized
        for forbidden in (
            "source_kind",
            "world_state",
            "state_delta",
            "receipt",
            "decision",
            "publishable",
        )
    )
    assert list(scratch_parent.iterdir()) == []


def test_recording_the_same_acquisition_is_content_addressed_and_idempotent(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorder = System2ReplaySnapshotAssessmentRecorder(scratch_parent=scratch_parent)

    first = recorder.record(acquisition=acquisition)
    first_bytes = store.resolve(first.assessment).path.read_bytes()
    second = recorder.record(acquisition=acquisition)

    assert second.assessment == first.assessment
    assert store.resolve(second.assessment).path.read_bytes() == first_bytes
    assert second.snapshot_validation == first.snapshot_validation
    assert list(scratch_parent.iterdir()) == []


def test_committed_adapter_fail_is_recorded_as_a_normal_local_assessment(
    tmp_path: Path,
) -> None:
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
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()

    result = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    verified = System2ReplaySnapshotAssessmentVerifier(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).verify(assessment=result.assessment)

    assessment = json.loads(store.resolve(result.assessment).path.read_bytes())
    assert result.snapshot_validation.snapshot_validation.validation_status is ValidationStatus.FAIL
    assert result.snapshot_validation.snapshot_validation.fail_count == 1
    assert result.snapshot_validation.snapshot_validation.not_run_count == 0
    assert assessment["validation_status"] == "fail"
    assert assessment["fail_count"] == 1
    assert assessment["not_run_count"] == 0
    assert assessment["runtime_evidence"] == replacement.model_dump(mode="json")
    assert verified.record.validation_status is ValidationStatus.FAIL
    assert verified.snapshot_validation.validation_status is ValidationStatus.FAIL
    assert verified.snapshot_validation.fail_count == 1
    assert verified.snapshot_validation.not_run_count == 0
    assert list(scratch_parent.iterdir()) == []


def test_committed_adapter_incomplete_is_recorded_as_a_normal_local_assessment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
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
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()

    result = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    verified = System2ReplaySnapshotAssessmentVerifier(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).verify(assessment=result.assessment)

    assessment = json.loads(store.resolve(result.assessment).path.read_bytes())
    assert (
        result.snapshot_validation.snapshot_validation.validation_status
        is ValidationStatus.INCOMPLETE
    )
    assert result.snapshot_validation.snapshot_validation.fail_count == 0
    assert result.snapshot_validation.snapshot_validation.not_run_count == 1
    assert assessment["validation_status"] == "incomplete"
    assert assessment["fail_count"] == 0
    assert assessment["not_run_count"] == 1
    assert verified.record.validation_status is ValidationStatus.INCOMPLETE
    assert verified.snapshot_validation.validation_status is ValidationStatus.INCOMPLETE
    assert verified.snapshot_validation.fail_count == 0
    assert verified.snapshot_validation.not_run_count == 1
    assert list(scratch_parent.iterdir()) == []


def test_assessment_ref_alone_recomputes_and_verifies_the_recorded_snapshot(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    assessment_ref = recorded.assessment
    del acquisition

    verified = System2ReplaySnapshotAssessmentVerifier(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).verify(assessment=assessment_ref)

    assert type(verified) is VerifiedSystem2ReplaySnapshotAssessment
    assert verified.assessment == assessment_ref
    assert type(verified.record) is System2ReplaySnapshotAssessment
    assert type(verified.snapshot_validation) is SnapshotValidationResult
    assert verified.record.validation_report == verified.snapshot_validation.validation_report
    assert verified.record.validation_status is verified.snapshot_validation.validation_status
    assert verified.record.validation_status is ValidationStatus.PASS
    assert verified.record.fail_count == verified.snapshot_validation.fail_count
    assert verified.record.not_run_count == verified.snapshot_validation.not_run_count
    assert list(scratch_parent.iterdir()) == []


def test_repeated_artifact_only_verification_is_deterministic(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    verifier = System2ReplaySnapshotAssessmentVerifier(
        artifact_store=store,
        scratch_parent=scratch_parent,
    )

    first = verifier.verify(assessment=recorded.assessment)
    second = verifier.verify(assessment=recorded.assessment)

    assert second == first
    assert second.assessment == recorded.assessment
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("name", "wrong_assessment"),
        ("media_type", "text/plain"),
        ("schema_version", "harness.wrong.v1"),
        ("uri", f"artifact://sha256/{'0' * 64}"),
    ),
)
def test_verifier_rejects_an_assessment_ref_with_the_wrong_header(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    wrong_header = recorded.assessment.model_copy(update={field: value})

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).verify(assessment=wrong_header)

    assert raised.value.reason == "assessment_invalid"
    assert list(scratch_parent.iterdir()) == []


def test_verifier_rejects_noncanonical_assessment_bytes(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    document = json.loads(store.resolve(recorded.assessment).path.read_bytes())
    source = tmp_path / "noncanonical-assessment.json"
    source.write_text(json.dumps(document, indent=2), encoding="utf-8")
    noncanonical = store.put_file(
        source,
        name="system2_replay_snapshot_assessment",
        media_type="application/json",
        schema_version="harness.system2_replay_snapshot_assessment.v1",
    )

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).verify(assessment=noncanonical)

    assert raised.value.reason == "assessment_invalid"
    assert list(scratch_parent.iterdir()) == []


def test_verifier_rejects_a_payload_that_fails_strict_model_validation(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    document = json.loads(store.resolve(recorded.assessment).path.read_bytes())
    document["fail_count"] = True
    invalid = _put_json(
        store,
        tmp_path / "strict-invalid-assessment",
        name="system2_replay_snapshot_assessment",
        schema_version="harness.system2_replay_snapshot_assessment.v1",
        value=document,
    )

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).verify(assessment=invalid)

    assert raised.value.reason == "assessment_invalid"
    assert list(scratch_parent.iterdir()) == []


def test_verifier_rejects_an_incomplete_recorded_dependency_closure(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    document = json.loads(store.resolve(recorded.assessment).path.read_bytes())
    document["dependencies"] = document["dependencies"][:-1]
    incomplete = _put_json(
        store,
        tmp_path / "incomplete-assessment",
        name="system2_replay_snapshot_assessment",
        schema_version="harness.system2_replay_snapshot_assessment.v1",
        value=document,
    )

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).verify(assessment=incomplete)

    assert raised.value.reason == "assessment_invalid"
    assert list(scratch_parent.iterdir()) == []


def test_verifier_requires_the_recorded_report_to_remain_in_the_local_cas(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    report = recorded.snapshot_validation.snapshot_validation.validation_report
    store.resolve(report).path.unlink()

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).verify(assessment=recorded.assessment)

    assert raised.value.reason == "assessment_recompute_failed"
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.parametrize("missing", ("package", "runtime", "snapshot"))
def test_verifier_maps_a_missing_recomputation_input_to_one_stable_reason(
    tmp_path: Path,
    missing: str,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    record = System2ReplaySnapshotAssessment.model_validate_json(
        store.resolve(recorded.assessment).path.read_bytes(),
        strict=True,
    )
    refs = {
        "package": record.replay_input.environment_package.package_manifest,
        "runtime": record.runtime_evidence,
        "snapshot": record.runtime_asset_snapshot_manifest,
    }
    store.resolve(refs[missing]).path.unlink()

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).verify(assessment=recorded.assessment)

    assert raised.value.reason == "assessment_recompute_failed"
    assert str(raised.value) == (
        "System 2 replay snapshot assessment stopped: assessment_recompute_failed"
    )
    assert list(scratch_parent.iterdir()) == []


def test_verifier_maps_adapter_configuration_failure_to_recompute_failed(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    recorder_scratch = tmp_path / "recorder-scratch"
    recorder_scratch.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=recorder_scratch,
    ).record(acquisition=acquisition)

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=tmp_path / "missing-verifier-scratch",
        ).verify(assessment=recorded.assessment)

    assert raised.value.reason == "assessment_recompute_failed"
    assert list(recorder_scratch.iterdir()) == []


@pytest.mark.parametrize("relation", ("same", "scratch_ancestor", "scratch_descendant"))
def test_verifier_rejects_overlapping_scratch_and_cas_roots(
    tmp_path: Path,
    relation: str,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    recorder_scratch = tmp_path / "recorder-scratch"
    recorder_scratch.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=recorder_scratch,
    ).record(acquisition=acquisition)
    if relation == "same":
        verifier_scratch = store.root
    elif relation == "scratch_ancestor":
        verifier_scratch = store.root.parent
    else:
        verifier_scratch = store.root / "verifier-scratch"
        verifier_scratch.mkdir()

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=verifier_scratch,
        ).verify(assessment=recorded.assessment)

    assert raised.value.reason == "assessment_recompute_failed"
    assert list(recorder_scratch.iterdir()) == []


@pytest.mark.parametrize(
    "drift",
    ("validation_report", "validation_status", "fail_count", "not_run_count"),
)
def test_verifier_maps_recorded_snapshot_summary_drift_to_assessment_mismatch(
    tmp_path: Path,
    drift: str,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    document = json.loads(store.resolve(recorded.assessment).path.read_bytes())
    if drift == "validation_report":
        report = json.loads(
            store.resolve(
                recorded.snapshot_validation.snapshot_validation.validation_report
            ).path.read_bytes()
        )
        report["status"] = "fail"
        report["fail_count"] = 1
        changed_report = _put_json(
            store,
            tmp_path / "changed-report",
            name="snapshot_validation_report",
            schema_version="robotwin.scene_validation.v1",
            value=report,
        )
        document["validation_report"] = changed_report.model_dump(mode="json")
        document["validation_status"] = "fail"
        document["fail_count"] = 1
    elif drift == "validation_status":
        document["validation_status"] = "fail"
    else:
        document[drift] = 1
    changed_assessment = _put_json(
        store,
        tmp_path / f"{drift}-assessment",
        name="system2_replay_snapshot_assessment",
        schema_version="harness.system2_replay_snapshot_assessment.v1",
        value=document,
    )

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).verify(assessment=changed_assessment)

    assert raised.value.reason == "assessment_mismatch"
    assert str(raised.value) == ("System 2 replay snapshot assessment stopped: assessment_mismatch")
    assert list(scratch_parent.iterdir()) == []


def test_verifier_rejects_reversed_recorded_replay_times(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    document = json.loads(store.resolve(recorded.assessment).path.read_bytes())
    document["replay_started_at"] = "2026-09-02T06:02:00Z"
    reversed_times = _put_json(
        store,
        tmp_path / "reversed-times-assessment",
        name="system2_replay_snapshot_assessment",
        schema_version="harness.system2_replay_snapshot_assessment.v1",
        value=document,
    )

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).verify(assessment=reversed_times)

    assert raised.value.reason == "assessment_invalid"
    assert list(scratch_parent.iterdir()) == []


def test_public_assessment_model_rejects_reversed_replay_times(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    document = json.loads(store.resolve(recorded.assessment).path.read_bytes())
    document["replay_started_at"] = "2026-09-02T06:02:00Z"

    with pytest.raises(ValidationError, match="replay times"):
        System2ReplaySnapshotAssessment.model_validate_json(
            _canonical_json_bytes(document),
            strict=True,
        )


@pytest.mark.parametrize(
    "case",
    ("non_utc_times", "dependency_order", "dependency_version", "runtime_asset_digest"),
)
def test_public_assessment_model_rejects_other_contradictory_metadata(
    tmp_path: Path,
    case: str,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    document = json.loads(store.resolve(recorded.assessment).path.read_bytes())
    if case == "non_utc_times":
        document["replay_started_at"] = "2026-09-02T14:00:00+08:00"
        document["replay_ended_at"] = "2026-09-02T14:01:00+08:00"
    elif case == "dependency_order":
        document["dependencies"] = list(reversed(document["dependencies"]))
    elif case == "dependency_version":
        document["dependencies"][0]["version"] = "2"
    else:
        runtime_assets = next(
            dependency
            for dependency in document["dependencies"]
            if dependency["name"] == "text2env.replay.runtime_assets"
        )
        runtime_assets["sha256"] = "f" * 64

    with pytest.raises(ValidationError):
        System2ReplaySnapshotAssessment.model_validate_json(
            _canonical_json_bytes(document),
            strict=True,
        )


def test_public_assessment_model_rejects_boolean_counts_without_strict_call_mode(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    record = System2ReplaySnapshotAssessment.model_validate_json(
        store.resolve(recorded.assessment).path.read_bytes(),
        strict=True,
    )
    invalid = record.model_dump(mode="python")
    invalid["fail_count"] = True

    with pytest.raises(ValidationError):
        System2ReplaySnapshotAssessment.model_validate(invalid)


def test_verifier_rejects_a_record_with_the_wrong_runtime_header(tmp_path: Path) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    document = json.loads(store.resolve(recorded.assessment).path.read_bytes())
    document["runtime_evidence"]["name"] = "wrong_runtime"
    wrong_runtime = _put_json(
        store,
        tmp_path / "wrong-runtime-assessment",
        name="system2_replay_snapshot_assessment",
        schema_version="harness.system2_replay_snapshot_assessment.v1",
        value=document,
    )

    with pytest.raises(System2ReplaySnapshotAssessmentError) as raised:
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).verify(assessment=wrong_runtime)

    assert raised.value.reason == "assessment_invalid"
    assert list(scratch_parent.iterdir()) == []


def test_verifier_constructor_rejects_wrong_dependency_types(tmp_path: Path) -> None:
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()

    with pytest.raises(TypeError, match="artifact_store must be LocalArtifactStore"):
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=object(),  # type: ignore[arg-type]
            scratch_parent=scratch_parent,
        )
    derived_store_type = type("DerivedStore", (LocalArtifactStore,), {})
    with pytest.raises(TypeError, match="artifact_store must be LocalArtifactStore"):
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=derived_store_type(tmp_path / "derived-cas"),
            scratch_parent=scratch_parent,
        )
    with pytest.raises(TypeError, match="scratch_parent must be Path"):
        System2ReplaySnapshotAssessmentVerifier(
            artifact_store=LocalArtifactStore(tmp_path / "cas"),
            scratch_parent="wrong",  # type: ignore[arg-type]
        )


def test_verifier_accepts_the_compile_producers_effective_catalog_name(
    tmp_path: Path,
) -> None:
    acquisition, store = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    recorded = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    document = json.loads(store.resolve(recorded.assessment).path.read_bytes())
    document["replay_input"]["environment_package"]["asset_catalog"]["name"] = (
        "effective_asset_catalog"
    )
    producer_shaped = _put_json(
        store,
        tmp_path / "producer-shaped-assessment",
        name="system2_replay_snapshot_assessment",
        schema_version="harness.system2_replay_snapshot_assessment.v1",
        value=document,
    )

    verified = System2ReplaySnapshotAssessmentVerifier(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).verify(assessment=producer_shaped)

    assert verified.assessment == producer_shaped
    assert verified.record.replay_input.environment_package.asset_catalog.name == (
        "effective_asset_catalog"
    )
    assert list(scratch_parent.iterdir()) == []
