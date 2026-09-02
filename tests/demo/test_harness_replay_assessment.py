from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

import demo.harness_replay_assessment as workbench_module
import self_improving.validate_v2_snapshot as snapshot_module
from demo.harness_replay_assessment import (
    WorkbenchReplayAssessment,
    WorkbenchReplayAssessmentAuthorityError,
    WorkbenchReplayAssessmentInputError,
    WorkbenchReplayAssessmentNotFoundError,
    WorkbenchReplayAssessmentUnavailableError,
    WorkbenchReplayAssessmentView,
)
from scene_gen.builder import build_scene_package
from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene
from self_improving.harness.artifacts import ArtifactResolutionError, LocalArtifactStore
from self_improving.harness.event_journal import EventPage, SQLiteEventJournal, StoredRunEvent
from self_improving.harness.events import RunEvent
from self_improving.harness.package_store import PackageStore
from self_improving.harness.replay_dependencies import REPLAY_DEPENDENCY_NAMES
from self_improving.harness.run_store import SQLiteRunStore
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
    System2ReplaySnapshotAssessmentRecorder,
)

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = UUID("95000000-0000-4000-8000-000000000001")
INVOCATION_DIGEST = "9" * 64


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
    *,
    runtime_status: str = "pass",
) -> tuple[LocalArtifactStore, EnvironmentPackage, ArtifactRef, ArtifactRef]:
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
        "status": runtime_status,
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
    *,
    runtime_status: str = "pass",
    replay_timezone: timezone = timezone.utc,
    skill_id: str = "text2env.replay",
) -> tuple[System2ReplayEvidenceAcquisitionResult, LocalArtifactStore, Path]:
    store, package, runtime_evidence, snapshot = _package_fixture(
        tmp_path,
        runtime_status=runtime_status,
    )
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
    invocation = Invocation(
        run_id=RUN_ID,
        skill_id=skill_id,
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
        invocation_digest=INVOCATION_DIGEST,
    )
    started = datetime(2026, 9, 2, 6, 0, tzinfo=replay_timezone)
    ended = datetime(2026, 9, 2, 6, 1, tzinfo=replay_timezone)
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
        run_id=RUN_ID,
        invocation_digest=INVOCATION_DIGEST,
        skill_id=skill_id,
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
    journal_path = tmp_path / "replay-state" / "harness.sqlite3"
    run_store = SQLiteRunStore(journal_path)
    run_store.put_invocation(invocation)
    journal = SQLiteEventJournal(journal_path)
    for event in events:
        journal.publish(
            RunEvent(
                run_id=RUN_ID,
                skill_id=skill_id,
                skill_version="1.0.0",
                event=event,
            )
        )
    run_store.put_run_state(state)
    page = journal.read(after_event_id=0, run_id=RUN_ID, limit=200)
    assert page == EventPage(
        events=tuple(
            StoredRunEvent(
                event_id=index,
                envelope=RunEvent(
                    run_id=RUN_ID,
                    skill_id=skill_id,
                    skill_version="1.0.0",
                    event=event,
                ),
            )
            for index, event in enumerate(events, start=1)
        ),
        last_event_id=2,
        has_more=False,
    )
    acquisition = System2ReplayEvidenceAcquisitionResult(
        run_state=state,
        invocation=invocation,
        event_page=page,
        typed_output=typed_output,
        artifact_root=store.root,
        journal_path=journal_path,
    )
    return acquisition, store, journal_path


def _cas_digests(store: LocalArtifactStore) -> frozenset[str]:
    return frozenset(path.name for path in (store.root / "sha256").glob("*/*") if path.is_file())


def _empty_storage(tmp_path: Path) -> tuple[Path, Path, Path]:
    journal_path = tmp_path / "empty-authority.sqlite3"
    SQLiteRunStore(journal_path)
    artifact_root = tmp_path / "empty-cas"
    artifact_root.mkdir()
    scratch_parent = tmp_path / "empty-scratch"
    scratch_parent.mkdir()
    return journal_path, artifact_root, scratch_parent


def _canonical_model_bytes(value: RunState) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _rewrite_terminal_state(*, journal_path: Path, state: RunState) -> None:
    state_payload = _canonical_model_bytes(state)
    terminal = state.events[-1]
    envelope = json.dumps(
        {
            "run_id": str(state.run_id),
            "skill_id": state.skill_id,
            "skill_version": state.skill_version,
            "event": terminal.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with closing(sqlite3.connect(journal_path)) as connection, connection:
        connection.execute(
            """
            UPDATE run_events
            SET timestamp = ?, stage = ?, attempt = ?, from_status = ?,
                to_status = ?, envelope_json = ?
            WHERE run_id = ? AND seq = ?
            """,
            (
                terminal.timestamp.isoformat(),
                terminal.stage,
                terminal.attempt,
                terminal.from_status.value if terminal.from_status is not None else None,
                terminal.to_status.value,
                envelope,
                str(state.run_id),
                terminal.seq,
            ),
        )
        connection.execute(
            """
            UPDATE harness_run_states
            SET payload_json = ?, payload_sha256 = ?
            WHERE run_id = ?
            """,
            (state_payload, sha256(state_payload).hexdigest(), str(state.run_id)),
        )


def _rewrite_index_document(
    *,
    journal_path: Path,
    transform: object,
    canonical: bool = True,
) -> None:
    with closing(sqlite3.connect(journal_path)) as connection, connection:
        row = connection.execute(
            """
            SELECT payload_json
            FROM workbench_replay_snapshot_assessments
            WHERE run_id = ?
            """,
            (str(RUN_ID),),
        ).fetchone()
        assert row is not None
        document = json.loads(row[0])
        transform(document)  # type: ignore[operator]
        payload = (
            _canonical_json_bytes(document)
            if canonical
            else json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
        )
        connection.execute(
            """
            UPDATE workbench_replay_snapshot_assessments
            SET payload_json = ?, payload_sha256 = ?
            WHERE run_id = ?
            """,
            (payload, sha256(payload).hexdigest(), str(RUN_ID)),
        )


def _replace_terminal_artifact(
    *,
    journal_path: Path,
    state: RunState,
    previous: ArtifactRef,
    replacement: ArtifactRef,
) -> None:
    typed_output = Text2EnvReplayOutput.model_validate(state.output)
    updated_output = typed_output.model_copy(
        update={
            "runtime_evidence": (
                replacement
                if typed_output.runtime_evidence == previous
                else typed_output.runtime_evidence
            ),
            "replay_artifacts": tuple(
                replacement if ref == previous else ref for ref in typed_output.replay_artifacts
            ),
        }
    )
    events = tuple(
        event.model_copy(
            update={
                "artifact_refs": tuple(
                    replacement if ref == previous else ref for ref in event.artifact_refs
                )
            }
        )
        for event in state.events
    )
    updated = state.model_copy(
        update={
            "events": events,
            "artifacts": tuple(replacement if ref == previous else ref for ref in state.artifacts),
            "output": updated_output.model_dump(mode="json"),
        }
    )
    _rewrite_terminal_state(journal_path=journal_path, state=updated)


def test_recorded_assessment_survives_reopen_and_is_inspectable_by_run_id(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()

    recorded = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    reopened = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )

    inspected = reopened.inspect(run_id=RUN_ID)
    durable_store = SQLiteRunStore(journal_path)
    durable_history = SQLiteEventJournal(journal_path).read(
        after_event_id=0,
        run_id=RUN_ID,
        limit=200,
    )

    assert type(recorded) is WorkbenchReplayAssessmentView
    assert inspected == recorded
    assert durable_store.read_invocation(RUN_ID) == acquisition.invocation
    assert durable_store.read_run_state(RUN_ID) == acquisition.run_state
    assert durable_history == acquisition.event_page
    assert inspected.schema_version == "harness.workbench_replay_snapshot_assessment.v1"
    assert inspected.run_id == RUN_ID
    assert inspected.invocation_digest == INVOCATION_DIGEST
    assert inspected.run_status is RunStatus.SUCCEEDED
    assert inspected.terminal_event_count == 2
    assert inspected.terminal_last_event_id == "2"
    assert len(inspected.assessment_sha256) == 64
    assert int(inspected.assessment_bytes) > 0
    assert len(inspected.validation_report_sha256) == 64
    assert int(inspected.validation_report_bytes) > 0
    assert inspected.validation_status is ValidationStatus.PASS
    assert inspected.fail_count == 0
    assert inspected.not_run_count == 0
    assert list(scratch_parent.iterdir()) == []


def test_stale_event_page_is_rejected_before_the_recorder_writes_to_cas(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    stale = replace(
        acquisition,
        event_page=replace(acquisition.event_page, last_event_id=1),
    )
    before = _cas_digests(store)

    with pytest.raises(WorkbenchReplayAssessmentAuthorityError):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).record(acquisition=stale)

    assert _cas_digests(store) == before
    assert list(scratch_parent.iterdir()) == []


def test_reordered_event_cursors_are_rejected_before_the_recorder_writes_to_cas(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    with closing(sqlite3.connect(journal_path)) as connection, connection:
        connection.execute(
            "UPDATE run_events SET event_id = 1000 - event_id WHERE run_id = ?",
            (str(RUN_ID),),
        )
    reordered = replace(
        acquisition,
        event_page=SQLiteEventJournal(journal_path).read(
            after_event_id=0,
            run_id=RUN_ID,
            limit=200,
        ),
    )
    before = _cas_digests(store)

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="Replay event history differs from terminal RunState",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).record(acquisition=reordered)

    assert _cas_digests(store) == before


@pytest.mark.parametrize("artifact_kind", ("runtime_evidence", "runtime_asset_snapshot"))
def test_inspect_rejects_terminal_output_artifact_drift(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    workbench.record(acquisition=acquisition)
    output = acquisition.typed_output
    assert output is not None
    if artifact_kind == "runtime_evidence":
        previous = output.runtime_evidence
        document = json.loads(store.resolve(previous).path.read_bytes())
        document["status"] = "fail"
        replacement = _put_json(
            store,
            tmp_path / "changed-runtime",
            name="runtime_evidence",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=document,
        )
    else:
        previous = next(
            ref for ref in output.replay_artifacts if ref.name == "runtime_asset_snapshot"
        )
        document = json.loads(store.resolve(previous).path.read_bytes())
        document["asset_catalog_sha256"] = "f" * 64
        replacement = _put_json(
            store,
            tmp_path / "changed-snapshot",
            name="runtime_asset_snapshot",
            schema_version="harness.runtime_asset_snapshot.v1",
            value=document,
        )
    _replace_terminal_artifact(
        journal_path=journal_path,
        state=acquisition.run_state,
        previous=previous,
        replacement=replacement,
    )

    with pytest.raises(WorkbenchReplayAssessmentAuthorityError):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).inspect(run_id=RUN_ID)

    assert list(scratch_parent.iterdir()) == []


def test_overlapping_scratch_and_cas_roots_are_one_input_error(tmp_path: Path) -> None:
    _acquisition, store, journal_path = _successful_acquisition(tmp_path)

    with pytest.raises(
        WorkbenchReplayAssessmentInputError,
        match="scratch_parent and artifact_root must be disjoint",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=store.root,
        )


def test_journal_cannot_be_stored_inside_the_cas_root(tmp_path: Path) -> None:
    artifact_root = tmp_path / "cas"
    journal_path = artifact_root / "authority.sqlite3"
    SQLiteRunStore(journal_path)
    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()

    with pytest.raises(
        WorkbenchReplayAssessmentInputError,
        match="journal_path must be outside artifact_root",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=artifact_root,
            scratch_parent=scratch_parent,
        )


@pytest.mark.parametrize(
    "field",
    ("run_state", "invocation", "event_page", "typed_output"),
)
def test_malformed_acquisition_fields_are_rejected_before_any_write(
    tmp_path: Path,
    field: str,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    malformed = replace(acquisition, **{field: object()})
    before = _cas_digests(store)

    with pytest.raises(
        WorkbenchReplayAssessmentInputError,
        match="acquisition fields are invalid",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).record(acquisition=malformed)

    assert _cas_digests(store) == before


def test_acquisition_output_mismatch_is_rejected_before_any_write(tmp_path: Path) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    output = acquisition.typed_output
    assert output is not None
    mismatched = replace(
        acquisition,
        typed_output=output.model_copy(update={"replay_artifacts": (output.runtime_evidence,)}),
    )
    before = _cas_digests(store)

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="Replay acquisition output differs from terminal RunState",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).record(acquisition=mismatched)

    assert _cas_digests(store) == before


def test_malformed_terminal_output_is_an_authority_error_before_any_write(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    malformed_state = acquisition.run_state.model_copy(
        update={"output": {"unexpected": "replay-output"}},
    )
    _rewrite_terminal_state(journal_path=journal_path, state=malformed_state)
    malformed = replace(acquisition, run_state=malformed_state)
    before = _cas_digests(store)

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="terminal output is not a strict typed replay output",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).record(acquisition=malformed)

    assert _cas_digests(store) == before


def test_non_replay_terminal_history_is_an_authority_error_before_any_write(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(
        tmp_path,
        skill_id="text2env.compile",
    )
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    before = _cas_digests(store)

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="not one complete successful terminal history",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).record(acquisition=acquisition)

    assert _cas_digests(store) == before


def test_duplicate_named_runtime_snapshot_is_rejected_before_any_write(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    duplicate = acquisition.typed_output.runtime_evidence.model_copy(
        update={"name": "runtime_asset_snapshot"},
    )
    typed_output = acquisition.typed_output.model_copy(
        update={
            "replay_artifacts": (*acquisition.typed_output.replay_artifacts, duplicate),
        },
    )
    events = (
        *acquisition.run_state.events[:-1],
        acquisition.run_state.events[-1].model_copy(
            update={
                "artifact_refs": (
                    *acquisition.run_state.events[-1].artifact_refs,
                    duplicate,
                ),
            },
        ),
    )
    state = acquisition.run_state.model_copy(
        update={
            "events": events,
            "artifacts": (*acquisition.run_state.artifacts, duplicate),
            "output": typed_output.model_dump(mode="json"),
        },
    )
    _rewrite_terminal_state(journal_path=journal_path, state=state)
    coherent = replace(
        acquisition,
        run_state=state,
        event_page=SQLiteEventJournal(journal_path).read(
            after_event_id=0,
            run_id=RUN_ID,
            limit=200,
        ),
        typed_output=typed_output,
    )
    before = _cas_digests(store)
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="cannot support a local snapshot assessment",
    ):
        workbench.record(acquisition=coherent)

    assert _cas_digests(store) == before
    with pytest.raises(WorkbenchReplayAssessmentNotFoundError):
        workbench.inspect(run_id=RUN_ID)


def test_failed_snapshot_validation_is_a_normal_inspectable_view(tmp_path: Path) -> None:
    acquisition, store, journal_path = _successful_acquisition(
        tmp_path,
        runtime_status="fail",
    )
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )

    recorded = workbench.record(acquisition=acquisition)

    assert recorded.run_status is RunStatus.SUCCEEDED
    assert recorded.validation_status is ValidationStatus.FAIL
    assert recorded.fail_count == 1
    assert recorded.not_run_count == 0
    assert workbench.inspect(run_id=RUN_ID) == recorded


def test_incomplete_snapshot_validation_is_a_normal_inspectable_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
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
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )

    recorded = workbench.record(acquisition=acquisition)

    assert recorded.run_status is RunStatus.SUCCEEDED
    assert recorded.validation_status is ValidationStatus.INCOMPLETE
    assert recorded.fail_count == 0
    assert recorded.not_run_count == 1
    assert workbench.inspect(run_id=RUN_ID) == recorded


def test_recording_the_identical_acquisition_is_idempotent(tmp_path: Path) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )

    first = workbench.record(acquisition=acquisition)
    first_cas = _cas_digests(store)
    second = workbench.record(acquisition=acquisition)

    assert second == first
    assert _cas_digests(store) == first_cas
    assert workbench.inspect(run_id=RUN_ID) == first


def test_same_run_with_a_different_assessment_view_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    first = workbench.record(acquisition=acquisition)
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

    with monkeypatch.context() as changed_validation:
        changed_validation.setattr(
            snapshot_module,
            "validate_resolved_scene",
            validate_with_one_not_run,
        )
        with pytest.raises(
            WorkbenchReplayAssessmentAuthorityError,
            match="different replay assessment index",
        ):
            workbench.record(acquisition=acquisition)

    assert workbench.inspect(run_id=RUN_ID) == first


def test_inspect_rejects_a_coordinated_terminal_event_change_hidden_by_the_view(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    workbench.record(acquisition=acquisition)
    events = (
        *acquisition.run_state.events[:-1],
        acquisition.run_state.events[-1].model_copy(update={"stage": "alternate.complete"}),
    )
    _rewrite_terminal_state(
        journal_path=journal_path,
        state=acquisition.run_state.model_copy(update={"events": events}),
    )

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="identity differs from its immutable index",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).inspect(run_id=RUN_ID)


def test_inspect_rejects_verified_assessment_metadata_for_another_run(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    recorded = workbench.record(acquisition=acquisition)
    original = ArtifactRef(
        name="system2_replay_snapshot_assessment",
        uri=f"artifact://sha256/{recorded.assessment_sha256}",
        media_type="application/json",
        sha256=recorded.assessment_sha256,
        bytes=int(recorded.assessment_bytes),
        schema_version="harness.system2_replay_snapshot_assessment.v1",
    )
    document = json.loads(store.resolve(original).path.read_bytes())
    document["replay_run_id"] = "95000000-0000-4000-8000-000000000002"
    alternate = _put_json(
        store,
        tmp_path / "alternate-assessment",
        name="system2_replay_snapshot_assessment",
        schema_version="harness.system2_replay_snapshot_assessment.v1",
        value=document,
    )

    def replace_assessment(document: dict[str, object]) -> None:
        view = document["view"]
        assert isinstance(view, dict)
        view["assessment_sha256"] = alternate.sha256
        view["assessment_bytes"] = str(alternate.bytes)

    _rewrite_index_document(
        journal_path=journal_path,
        transform=replace_assessment,
    )

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="assessment metadata differs from the run authority",
    ):
        workbench.inspect(run_id=RUN_ID)


def test_inspect_rejects_a_verified_assessment_for_another_runtime_output(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    workbench.record(acquisition=acquisition)
    runtime_payload = json.loads(
        store.resolve(acquisition.typed_output.runtime_evidence).path.read_bytes()
    )
    runtime_payload["status"] = "fail"
    alternate_runtime = _put_json(
        store,
        tmp_path / "alternate-runtime",
        name="runtime_evidence",
        schema_version="robotwin.scene_runtime_evidence.v2",
        value=runtime_payload,
    )
    typed_output = acquisition.typed_output.model_copy(
        update={
            "runtime_evidence": alternate_runtime,
            "replay_artifacts": tuple(
                alternate_runtime if ref == acquisition.typed_output.runtime_evidence else ref
                for ref in acquisition.typed_output.replay_artifacts
            ),
        },
    )
    state = acquisition.run_state.model_copy(
        update={
            "events": tuple(
                event.model_copy(
                    update={
                        "artifact_refs": tuple(
                            alternate_runtime
                            if ref == acquisition.typed_output.runtime_evidence
                            else ref
                            for ref in event.artifact_refs
                        ),
                    },
                )
                for event in acquisition.run_state.events
            ),
            "artifacts": tuple(
                alternate_runtime if ref == acquisition.typed_output.runtime_evidence else ref
                for ref in acquisition.run_state.artifacts
            ),
            "output": typed_output.model_dump(mode="json"),
        },
    )
    alternate = System2ReplaySnapshotAssessmentRecorder(
        scratch_parent=scratch_parent,
    ).record(
        acquisition=replace(
            acquisition,
            run_state=state,
            typed_output=typed_output,
        )
    )
    alternate_validation = alternate.snapshot_validation.snapshot_validation

    def replace_assessment(document: dict[str, object]) -> None:
        view = document["view"]
        assert isinstance(view, dict)
        view.update(
            assessment_sha256=alternate.assessment.sha256,
            assessment_bytes=str(alternate.assessment.bytes),
            validation_report_sha256=alternate_validation.validation_report.sha256,
            validation_report_bytes=str(alternate_validation.validation_report.bytes),
            validation_status=alternate_validation.validation_status.value,
            fail_count=alternate_validation.fail_count,
            not_run_count=alternate_validation.not_run_count,
        )

    _rewrite_index_document(
        journal_path=journal_path,
        transform=replace_assessment,
    )

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="assessment artifacts differ from terminal replay output",
    ):
        workbench.inspect(run_id=RUN_ID)


def test_view_rejects_impossible_zero_identity_counts(tmp_path: Path) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    view = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)

    for field in (
        "terminal_event_count",
        "terminal_last_event_id",
        "assessment_bytes",
        "validation_report_bytes",
    ):
        payload = view.model_dump(mode="json")
        payload[field] = 0
        with pytest.raises(ValidationError) as raised:
            WorkbenchReplayAssessmentView.model_validate(payload)
        assert {error["loc"] for error in raised.value.errors()} == {(field,)}


def test_busy_timeout_outside_sqlite_integer_range_is_an_input_error(tmp_path: Path) -> None:
    journal_path = tmp_path / "authority.sqlite3"
    SQLiteRunStore(journal_path)
    artifact_root = tmp_path / "cas"
    artifact_root.mkdir()
    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()

    with pytest.raises(
        WorkbenchReplayAssessmentInputError,
        match="busy_timeout_ms must fit a positive SQLite integer",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=artifact_root,
            scratch_parent=scratch_parent,
            busy_timeout_ms=2**31,
        )


def test_non_utc_replay_times_map_to_authority_error_without_an_index(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(
        tmp_path,
        replay_timezone=timezone(timedelta(hours=8)),
    )
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="Replay assessment record is invalid for the durable authority",
    ):
        workbench.record(acquisition=acquisition)

    with pytest.raises(WorkbenchReplayAssessmentNotFoundError):
        workbench.inspect(run_id=RUN_ID)


@pytest.mark.parametrize(
    ("validation_status", "fail_count", "not_run_count"),
    (
        ("pass", 1, 0),
        ("pass", 0, 1),
        ("incomplete", 0, 0),
        ("fail", 0, 0),
    ),
)
def test_view_rejects_a_validation_status_that_disagrees_with_counts(
    tmp_path: Path,
    validation_status: str,
    fail_count: int,
    not_run_count: int,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    view = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    payload = view.model_dump(mode="json")
    payload.update(
        validation_status=validation_status,
        fail_count=fail_count,
        not_run_count=not_run_count,
    )

    with pytest.raises(ValidationError, match="validation status and counts disagree"):
        WorkbenchReplayAssessmentView.model_validate(payload)


@pytest.mark.parametrize("field", ("fail_count", "not_run_count"))
def test_view_rejects_a_validation_count_outside_browser_integer_range(
    tmp_path: Path,
    field: str,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    view = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    payload = view.model_dump(mode="json")
    payload.update(
        validation_status=("fail" if field == "fail_count" else "incomplete"),
        **{field: 2**53},
    )

    with pytest.raises(ValidationError) as raised:
        WorkbenchReplayAssessmentView.model_validate(payload)

    assert {error["loc"] for error in raised.value.errors()} == {(field,)}


@pytest.mark.parametrize(
    ("event_count", "last_event_id"),
    ((201, "201"), (2, "1")),
)
def test_view_rejects_impossible_terminal_event_identity(
    tmp_path: Path,
    event_count: int,
    last_event_id: str,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    view = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    payload = view.model_dump(mode="json")
    payload.update(
        terminal_event_count=event_count,
        terminal_last_event_id=last_event_id,
    )

    with pytest.raises(ValidationError, match="terminal event identity is impossible"):
        WorkbenchReplayAssessmentView.model_validate(payload)


@pytest.mark.parametrize(
    "last_event_id",
    (1, "0", "01", "-1", str(2**63)),
)
def test_view_requires_a_browser_exact_terminal_event_cursor(
    tmp_path: Path,
    last_event_id: object,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    view = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    payload = view.model_dump(mode="json")
    payload["terminal_last_event_id"] = last_event_id

    with pytest.raises(ValidationError):
        WorkbenchReplayAssessmentView.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("assessment_bytes", 1),
        ("assessment_bytes", "0"),
        ("assessment_bytes", "01"),
        ("validation_report_bytes", -1),
        ("validation_report_bytes", str(2**63)),
    ),
)
def test_view_requires_browser_exact_positive_artifact_byte_counts(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    view = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    ).record(acquisition=acquisition)
    payload = view.model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError):
        WorkbenchReplayAssessmentView.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("journal_path", "authority.sqlite3"),
        ("artifact_root", "cas"),
        ("scratch_parent", "scratch"),
        ("busy_timeout_ms", False),
        ("busy_timeout_ms", 0),
    ),
)
def test_constructor_rejects_wrong_public_input_types(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    journal_path, artifact_root, scratch_parent = _empty_storage(tmp_path)
    arguments: dict[str, object] = {
        "journal_path": journal_path,
        "artifact_root": artifact_root,
        "scratch_parent": scratch_parent,
    }
    arguments[field] = value

    with pytest.raises(WorkbenchReplayAssessmentInputError):
        WorkbenchReplayAssessment(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "invalid_resource",
    ("missing", "journal_directory", "artifact_file", "scratch_file"),
)
def test_constructor_maps_unavailable_local_resources(
    tmp_path: Path,
    invalid_resource: str,
) -> None:
    journal_path, artifact_root, scratch_parent = _empty_storage(tmp_path)
    if invalid_resource == "missing":
        journal_path = tmp_path / "missing.sqlite3"
    elif invalid_resource == "journal_directory":
        journal_path = tmp_path / "journal-directory"
        journal_path.mkdir()
    elif invalid_resource == "artifact_file":
        artifact_root = tmp_path / "artifact-file"
        artifact_root.write_bytes(b"not a directory")
    else:
        scratch_parent = tmp_path / "scratch-file"
        scratch_parent.write_bytes(b"not a directory")

    with pytest.raises(
        WorkbenchReplayAssessmentUnavailableError,
        match="storage is unavailable",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=artifact_root,
            scratch_parent=scratch_parent,
        )


def test_constructor_maps_an_unresolvable_home_locator_to_unavailable(
    tmp_path: Path,
) -> None:
    _, artifact_root, scratch_parent = _empty_storage(tmp_path)

    with pytest.raises(
        WorkbenchReplayAssessmentUnavailableError,
        match="storage is unavailable",
    ):
        WorkbenchReplayAssessment(
            journal_path=Path("~codex_missing_user_93047/authority.sqlite3"),
            artifact_root=artifact_root,
            scratch_parent=scratch_parent,
        )


def test_record_and_inspect_reject_wrong_argument_types(tmp_path: Path) -> None:
    journal_path, artifact_root, scratch_parent = _empty_storage(tmp_path)
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=artifact_root,
        scratch_parent=scratch_parent,
    )

    with pytest.raises(WorkbenchReplayAssessmentInputError, match="acquisition must be"):
        workbench.record(acquisition=object())  # type: ignore[arg-type]
    with pytest.raises(WorkbenchReplayAssessmentInputError, match="run_id must be"):
        workbench.inspect(run_id=str(RUN_ID))  # type: ignore[arg-type]


@pytest.mark.parametrize("locator", ("journal_path", "artifact_root"))
def test_record_rejects_a_malformed_acquisition_locator(
    tmp_path: Path,
    locator: str,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    malformed = replace(acquisition, **{locator: object()})

    with pytest.raises(
        WorkbenchReplayAssessmentInputError,
        match="acquisition storage locators are invalid",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).record(acquisition=malformed)


def test_record_maps_an_unresolvable_acquisition_home_locator_to_input_error(
    tmp_path: Path,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    malformed = replace(
        acquisition,
        journal_path=Path("~codex_missing_user_93047/authority.sqlite3"),
    )

    with pytest.raises(
        WorkbenchReplayAssessmentInputError,
        match="acquisition storage locators are invalid",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).record(acquisition=malformed)


@pytest.mark.parametrize("locator", ("journal_path", "artifact_root"))
def test_record_rejects_an_acquisition_from_a_different_authority(
    tmp_path: Path,
    locator: str,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    if locator == "journal_path":
        replacement = tmp_path / "different.sqlite3"
        SQLiteRunStore(replacement)
    else:
        replacement = tmp_path / "different-cas"
        replacement.mkdir()
    foreign = replace(acquisition, **{locator: replacement})

    with pytest.raises(
        WorkbenchReplayAssessmentInputError,
        match="different Workbench authority",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).record(acquisition=foreign)


def test_unindexed_run_is_not_found(tmp_path: Path) -> None:
    journal_path, artifact_root, scratch_parent = _empty_storage(tmp_path)

    with pytest.raises(
        WorkbenchReplayAssessmentNotFoundError,
        match="Replay snapshot assessment was not found",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=artifact_root,
            scratch_parent=scratch_parent,
        ).inspect(run_id=RUN_ID)


def test_index_initialization_failure_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path, artifact_root, scratch_parent = _empty_storage(tmp_path)

    def unavailable_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("local SQLite unavailable")

    monkeypatch.setattr(workbench_module.sqlite3, "connect", unavailable_connect)

    with pytest.raises(
        WorkbenchReplayAssessmentUnavailableError,
        match="Replay assessment index is unavailable",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=artifact_root,
            scratch_parent=scratch_parent,
        )


def test_recorder_storage_failure_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    real_put_file = LocalArtifactStore.put_file

    def fail_assessment_publish(
        target: LocalArtifactStore,
        source: Path,
        *,
        name: str,
        media_type: str,
        schema_version: str | None,
    ) -> ArtifactRef:
        if name == "system2_replay_snapshot_assessment":
            raise OSError("assessment CAS unavailable")
        return real_put_file(
            target,
            source,
            name=name,
            media_type=media_type,
            schema_version=schema_version,
        )

    monkeypatch.setattr(LocalArtifactStore, "put_file", fail_assessment_publish)

    with pytest.raises(
        WorkbenchReplayAssessmentUnavailableError,
        match="could not be recorded",
    ):
        WorkbenchReplayAssessment(
            journal_path=journal_path,
            artifact_root=store.root,
            scratch_parent=scratch_parent,
        ).record(acquisition=acquisition)


def test_index_insert_failure_is_unavailable_and_leaves_no_row(tmp_path: Path) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    with closing(sqlite3.connect(journal_path)) as connection, connection:
        connection.execute(
            """
            CREATE TRIGGER reject_replay_assessment_index
            BEFORE INSERT ON workbench_replay_snapshot_assessments
            BEGIN
                SELECT RAISE(ABORT, 'index unavailable');
            END
            """
        )

    with pytest.raises(
        WorkbenchReplayAssessmentUnavailableError,
        match="Replay assessment index is unavailable",
    ):
        workbench.record(acquisition=acquisition)
    with pytest.raises(WorkbenchReplayAssessmentNotFoundError):
        workbench.inspect(run_id=RUN_ID)


def test_index_read_failure_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    workbench.record(acquisition=acquisition)

    def unavailable_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("local SQLite unavailable")

    monkeypatch.setattr(workbench_module.sqlite3, "connect", unavailable_connect)

    with pytest.raises(
        WorkbenchReplayAssessmentUnavailableError,
        match="Replay assessment index is unavailable",
    ):
        workbench.inspect(run_id=RUN_ID)


def test_run_authority_read_failure_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )

    def unavailable_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("local SQLite unavailable")

    monkeypatch.setattr(workbench_module.sqlite3, "connect", unavailable_connect)

    with pytest.raises(
        WorkbenchReplayAssessmentUnavailableError,
        match="Replay run authority is unavailable",
    ):
        workbench.record(acquisition=acquisition)


def test_missing_indexed_run_state_is_an_authority_error(tmp_path: Path) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    workbench.record(acquisition=acquisition)
    with closing(sqlite3.connect(journal_path)) as connection, connection:
        connection.execute(
            "DELETE FROM harness_run_states WHERE run_id = ?",
            (str(RUN_ID),),
        )

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="incomplete run authority",
    ):
        workbench.inspect(run_id=RUN_ID)


def test_corrupt_indexed_run_state_is_an_authority_error(tmp_path: Path) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    workbench.record(acquisition=acquisition)
    with closing(sqlite3.connect(journal_path)) as connection, connection:
        connection.execute(
            "UPDATE harness_run_states SET payload_sha256 = ? WHERE run_id = ?",
            ("0" * 64, str(RUN_ID)),
        )

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="run authority failed integrity checks",
    ):
        workbench.inspect(run_id=RUN_ID)


def test_missing_assessment_dependency_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    workbench.record(acquisition=acquisition)
    real_resolve = LocalArtifactStore.resolve

    def unavailable_report(target: LocalArtifactStore, ref: ArtifactRef):
        if ref.name == "snapshot_validation_report":
            raise ArtifactResolutionError("not_found", "validation report unavailable")
        return real_resolve(target, ref)

    monkeypatch.setattr(LocalArtifactStore, "resolve", unavailable_report)

    with pytest.raises(
        WorkbenchReplayAssessmentUnavailableError,
        match="could not be recomputed",
    ):
        workbench.inspect(run_id=RUN_ID)


def test_indexed_missing_assessment_is_an_authority_error(tmp_path: Path) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    workbench.record(acquisition=acquisition)

    def change_assessment_digest(document: dict[str, object]) -> None:
        document["view"]["assessment_sha256"] = "0" * 64  # type: ignore[index]

    _rewrite_index_document(
        journal_path=journal_path,
        transform=change_assessment_digest,
    )

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="assessment failed authority checks",
    ):
        workbench.inspect(run_id=RUN_ID)


def test_indexed_view_drift_is_an_authority_error(tmp_path: Path) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    workbench.record(acquisition=acquisition)

    def change_report_digest(document: dict[str, object]) -> None:
        document["view"]["validation_report_sha256"] = "0" * 64  # type: ignore[index]

    _rewrite_index_document(
        journal_path=journal_path,
        transform=change_report_digest,
    )

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="Indexed replay assessment differs from its durable authority",
    ):
        workbench.inspect(run_id=RUN_ID)


@pytest.mark.parametrize("corruption", ("digest", "text_payload", "noncanonical", "invalid"))
def test_corrupt_index_row_is_an_authority_error(
    tmp_path: Path,
    corruption: str,
) -> None:
    acquisition, store, journal_path = _successful_acquisition(tmp_path)
    scratch_parent = tmp_path / "assessment-scratch"
    scratch_parent.mkdir()
    workbench = WorkbenchReplayAssessment(
        journal_path=journal_path,
        artifact_root=store.root,
        scratch_parent=scratch_parent,
    )
    workbench.record(acquisition=acquisition)
    if corruption == "digest":
        with closing(sqlite3.connect(journal_path)) as connection, connection:
            connection.execute(
                """
                UPDATE workbench_replay_snapshot_assessments
                SET payload_sha256 = ? WHERE run_id = ?
                """,
                ("0" * 64, str(RUN_ID)),
            )
    elif corruption == "text_payload":
        with closing(sqlite3.connect(journal_path)) as connection, connection:
            row = connection.execute(
                "SELECT payload_json FROM workbench_replay_snapshot_assessments"
            ).fetchone()
            assert row is not None
            payload = bytes(row[0]).decode("utf-8")
            connection.execute(
                """
                UPDATE workbench_replay_snapshot_assessments
                SET payload_json = ?, payload_sha256 = ? WHERE run_id = ?
                """,
                (payload, sha256(payload.encode()).hexdigest(), str(RUN_ID)),
            )
    elif corruption == "noncanonical":
        _rewrite_index_document(
            journal_path=journal_path,
            transform=lambda document: None,
            canonical=False,
        )
    else:

        def make_count_invalid(document: dict[str, object]) -> None:
            document["view"]["fail_count"] = True  # type: ignore[index]

        _rewrite_index_document(
            journal_path=journal_path,
            transform=make_count_invalid,
        )

    with pytest.raises(
        WorkbenchReplayAssessmentAuthorityError,
        match="index failed integrity checks",
    ):
        workbench.inspect(run_id=RUN_ID)
