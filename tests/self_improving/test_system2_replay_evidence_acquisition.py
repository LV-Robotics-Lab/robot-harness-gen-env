from __future__ import annotations

# These are module-local factory-seam contract tests.  The repository has no
# checked-in deployed replay CAS/runtime/tool fixture, so they are not evidence
# that a production dynamic replay ran on this host.
import hashlib
import inspect
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

import self_improving.system2_replay_evidence_acquisition as module
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import (
    EventJournalCorruptionError,
    EventPage,
    StoredRunEvent,
)
from self_improving.harness.events import RunEvent
from self_improving.harness.handlers.text2env_replay import text2env_replay_descriptor
from self_improving.harness.qualification import QualificationBundleError
from self_improving.harness.registry import RunPersistenceError
from self_improving.harness.replay_application import ReplayApplicationSettings
from self_improving.harness.schemas import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    EnvironmentPackage,
    Event,
    ExecutionReproducibility,
    Invocation,
    RunState,
    RunStatus,
    RuntimeConfig,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)
from self_improving.system2_replay_evidence_acquisition import (
    System2ReplayEvidenceAcquirer,
    System2ReplayEvidenceAcquisitionError,
    System2ReplayEvidenceAcquisitionResult,
)
from self_improving.system2_replay_input_promotion import PromotedSystem2ReplayInput


def _put_bytes(
    store: LocalArtifactStore,
    directory: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / name
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


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


def _settings(tmp_path: Path, artifact_root: Path) -> ReplayApplicationSettings:
    support = tmp_path / "operator"
    support.mkdir()
    files: dict[str, Path] = {}
    for name in ("python", "runner", "capability", "launcher", "ffmpeg"):
        path = support / name
        path.write_text(name, encoding="utf-8")
        files[name] = path
    roots: dict[str, Path] = {}
    for name in ("bundle", "module", "assets", "cgroup"):
        path = support / name
        path.mkdir()
        roots[name] = path
    distribution = Path(module.__file__).resolve().parents[1]
    return ReplayApplicationSettings(
        state_root=tmp_path / "replay-state",
        qualification_bundle_root=roots["bundle"],
        evidence_artifact_root=artifact_root,
        implementation_root=distribution,
        scene_gen_root=distribution / "scene_gen",
        ledger_contract_root=(
            distribution / "self_improving/asset_pipeline/active/asset_reuse/lib"
        ),
        allowed_asset_roots=(roots["assets"],),
        interpreter=files["python"],
        runtime_runner=files["runner"],
        runtime_module_root=roots["module"],
        runtime_capability_path=files["capability"],
        media_launcher=files["launcher"],
        static_ffmpeg=files["ffmpeg"],
        delegated_cgroup_root=roots["cgroup"],
        runtime_timeout_seconds=30.0,
        capability_timeout_seconds=5.0,
    )


def _promoted(
    tmp_path: Path,
) -> tuple[PromotedSystem2ReplayInput, LocalArtifactStore, Text2EnvReplayOutput]:
    artifact_root = tmp_path / "replay-cas"
    store = LocalArtifactStore(artifact_root)
    source = tmp_path / "artifact-sources"
    catalog = _put_bytes(
        store,
        source,
        name="dynamic_catalog",
        payload=b'{"schema_version":"robotwin.asset_catalog.v1"}\n',
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    manifest = _put_bytes(
        store,
        source,
        name="dynamic_manifest",
        payload=b'{"schema_version":"robotwin.generated_scene_package.v1"}\n',
        media_type="application/json",
        schema_version="robotwin.generated_scene_package.v1",
    )
    package = EnvironmentPackage(
        package_id="9" * 64,
        route_id="text2env",
        producer_skill_ref="text2env.compile@1.0.0",
        seed=91,
        scene_spec_sha256="8" * 64,
        resolved_scene_sha256="9" * 64,
        asset_catalog=catalog,
        package_manifest=manifest,
    )
    package_ref = _put_bytes(
        store,
        source,
        name="environment_package",
        payload=_canonical_json_bytes(package.model_dump(mode="json")),
        media_type="application/json",
        schema_version="harness.environment_package.v1",
    )
    replay_input = Text2EnvReplayInput(
        environment_package=package,
        runtime_config=RuntimeConfig(
            precheck_steps=3,
            settle_steps=47,
            contact_window_steps=11,
            video_frames=7,
            fps=9,
        ),
    )
    runtime_evidence = _put_bytes(
        store,
        source,
        name="runtime_evidence",
        payload=b'{"schema_version":"robotwin.scene_runtime_evidence.v2"}\n',
        media_type="application/json",
        schema_version="robotwin.scene_runtime_evidence.v2",
    )
    replay_receipt = _put_bytes(
        store,
        source,
        name="replay_execution_receipt",
        payload=b'{"schema_version":"harness.text2env_replay_execution_receipt.v1"}\n',
        media_type="application/json",
        schema_version="harness.text2env_replay_execution_receipt.v1",
    )
    typed_output = Text2EnvReplayOutput(
        runtime_evidence=runtime_evidence,
        replay_artifacts=(runtime_evidence, replay_receipt),
    )
    return (
        PromotedSystem2ReplayInput(
            replay_input=replay_input,
            environment_package_ref=package_ref,
            destination_artifact_root=artifact_root.resolve(),
        ),
        store,
        typed_output,
    )


class _RecordingReplayApplication:
    def __init__(
        self,
        *,
        artifact_root: Path,
        journal_path: Path,
        promoted: PromotedSystem2ReplayInput,
        typed_output: Text2EnvReplayOutput,
    ) -> None:
        qualification = ArtifactRef(
            name="qualification",
            uri=f"artifact://sha256/{'a' * 64}",
            media_type="application/json",
            sha256="a" * 64,
            bytes=1,
            schema_version="harness.skill_qualification.v1",
        )
        self.skills = (
            text2env_replay_descriptor(
                qualification_artifact=qualification,
                implementation_sha256="b" * 64,
            ),
        )
        assert self.skills[0].reproducibility is (
            ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE
        )
        self.artifact_root = artifact_root
        self.journal_path = journal_path
        self.replay_calls: list[Text2EnvReplayInput] = []
        self.state_reads: list[UUID] = []
        self.invocation_reads: list[UUID] = []
        self.event_reads: list[dict[str, object]] = []
        self.run_id = UUID("94000000-0000-4000-8000-000000000001")
        self.dependencies = tuple(
            DependencyRef(name=name, version="1", sha256=character * 64)
            for name, character in zip(
                sorted(module.REPLAY_DEPENDENCY_NAMES),
                "12345",
                strict=True,
            )
        )
        self.invocation_value = Invocation(
            run_id=self.run_id,
            skill_id="text2env.replay",
            skill_version="1.0.0",
            effective_parameters=promoted.replay_input.model_dump(mode="json"),
            dependencies=self.dependencies,
            max_attempts=2,
            invocation_digest="6" * 64,
        )
        started = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
        ended = datetime(2026, 9, 2, 2, 1, tzinfo=timezone.utc)
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
                artifact_refs=typed_output.replay_artifacts,
            ),
        )
        self.state = RunState(
            run_id=self.run_id,
            invocation_digest=self.invocation_value.invocation_digest,
            skill_id="text2env.replay",
            skill_version="1.0.0",
            status=RunStatus.SUCCEEDED,
            attempt=1,
            max_attempts=2,
            started_at=started,
            ended_at=ended,
            events=events,
            artifacts=typed_output.replay_artifacts,
            output=typed_output.model_dump(mode="json"),
            blocker=None,
        )
        stored = tuple(
            StoredRunEvent(
                event_id=index,
                envelope=RunEvent(
                    run_id=self.run_id,
                    skill_id="text2env.replay",
                    skill_version="1.0.0",
                    event=event,
                ),
            )
            for index, event in enumerate(events, start=71)
        )
        self.page = EventPage(events=stored, last_event_id=72, has_more=False)

    def replay(self, value: Text2EnvReplayInput) -> RunState:
        self.replay_calls.append(value)
        return self.state

    def run_state(self, run_id: UUID) -> RunState:
        self.state_reads.append(run_id)
        return self.state

    def invocation(self, run_id: UUID) -> Invocation:
        self.invocation_reads.append(run_id)
        return self.invocation_value

    def events(self, **kwargs: object) -> EventPage:
        self.event_reads.append(kwargs)
        return self.page


def _factory_result(
    settings: ReplayApplicationSettings,
    application: _RecordingReplayApplication,
    *,
    create_journal: bool = True,
) -> _RecordingReplayApplication:
    settings.state_root.mkdir()
    if create_journal:
        application.journal_path.write_bytes(b"sqlite fixture")
    return application


def _event_page(state: RunState) -> EventPage:
    stored = tuple(
        StoredRunEvent(
            event_id=index,
            envelope=RunEvent(
                run_id=state.run_id,
                skill_id=state.skill_id,
                skill_version=state.skill_version,
                event=event,
            ),
        )
        for index, event in enumerate(state.events, start=71)
    )
    return EventPage(
        events=stored,
        last_event_id=stored[-1].event_id if stored else 0,
        has_more=False,
    )


def _set_terminal(
    application: _RecordingReplayApplication,
    *,
    status: RunStatus,
    artifacts: tuple[ArtifactRef, ...],
    attempt: int = 1,
) -> None:
    started = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 9, 2, 3, 1, tzinfo=timezone.utc)
    events = (
        Event(
            seq=1,
            timestamp=started,
            stage="preflight",
            attempt=attempt,
            from_status=None,
            to_status=RunStatus.RUNNING,
            artifact_refs=(),
        ),
        Event(
            seq=2,
            timestamp=ended,
            stage="runtime",
            attempt=attempt,
            from_status=RunStatus.RUNNING,
            to_status=status,
            artifact_refs=artifacts,
        ),
    )
    blocker = Blocker(
        code="HARN_RUNTIME_BLOCKED" if status is RunStatus.BLOCKED else "HARN_INTERNAL",
        message="dynamic replay did not produce a typed output",
        stage="runtime",
        retryable=False,
        details={},
        unknowns=(),
        artifact_refs=artifacts,
    )
    application.state = RunState(
        run_id=application.run_id,
        invocation_digest=(application.invocation_value.invocation_digest if attempt else None),
        skill_id="text2env.replay",
        skill_version="1.0.0",
        status=status,
        attempt=attempt,
        max_attempts=2 if attempt else 0,
        started_at=started,
        ended_at=ended,
        events=events,
        artifacts=artifacts,
        output=None,
        blocker=blocker,
    )
    if attempt == 0:
        application.invocation_value = None  # type: ignore[assignment]
    application.page = _event_page(application.state)


def test_dynamic_promoted_input_acquires_one_durable_typed_replay_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    factory_calls: list[ReplayApplicationSettings] = []

    def factory(value: ReplayApplicationSettings) -> _RecordingReplayApplication:
        factory_calls.append(value)
        return _factory_result(value, application)

    monkeypatch.setattr(module, "create_replay_application", factory)

    result = System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert type(result) is System2ReplayEvidenceAcquisitionResult
    assert result.run_state == application.state
    assert result.invocation == application.invocation_value
    assert result.event_page == application.page
    assert result.typed_output == typed_output
    assert result.artifact_root == store.root
    assert result.journal_path == settings.state_root / "harness.sqlite3"
    assert settings.state_root.is_dir()
    assert result.journal_path.is_file()
    assert promoted.replay_input.environment_package.seed == 91
    assert promoted.replay_input.runtime_config.model_dump() == {
        "precheck_steps": 3,
        "settle_steps": 47,
        "contact_window_steps": 11,
        "video_frames": 7,
        "fps": 9,
    }
    assert factory_calls == [settings]
    assert application.replay_calls == [promoted.replay_input]
    assert application.state_reads == [application.run_id, application.run_id]
    assert application.invocation_reads == [application.run_id, application.run_id]
    assert application.event_reads == [
        {"after_event_id": 0, "run_id": application.run_id, "limit": 200}
    ]
    for ref in result.run_state.artifacts:
        store.resolve(ref)
    for ref in (result.typed_output.runtime_evidence, *result.typed_output.replay_artifacts):
        assert ref.uri == f"artifact://sha256/{ref.sha256}"
        store.resolve(ref)


def test_factory_failure_is_one_domain_error_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, _typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    calls: list[ReplayApplicationSettings] = []

    def unavailable(value: ReplayApplicationSettings) -> None:
        calls.append(value)
        raise QualificationBundleError("artifact_missing", "qualification CAS is incomplete")

    monkeypatch.setattr(module, "create_replay_application", unavailable)

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "application_factory_failed"
    assert raised.value.run_state is None
    assert calls == [settings]


def test_persistence_failure_state_is_diagnostic_and_never_returned_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)

    class PersistenceFailureApplication(_RecordingReplayApplication):
        def replay(self, value: Text2EnvReplayInput) -> RunState:
            self.replay_calls.append(value)
            raise RunPersistenceError(
                operation="terminal state",
                run_id=self.run_id,
                state=self.state,
                cause=OSError("database unavailable"),
            )

    application = PersistenceFailureApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "run_persistence_failed"
    assert raised.value.run_state == application.state
    assert application.replay_calls == [promoted.replay_input]
    assert application.state_reads == []


def test_factory_result_without_exact_journal_is_rejected_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application, create_journal=False),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "application_invalid"
    assert raised.value.run_state is None
    assert application.replay_calls == []


@pytest.mark.parametrize("status", [RunStatus.BLOCKED, RunStatus.FAILED])
def test_started_blocked_and_failed_replays_are_normal_durable_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: RunStatus,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    diagnostic = _put_bytes(
        store,
        tmp_path / "diagnostics",
        name="replay_diagnostic",
        payload=b"diagnostic\n",
        media_type="text/plain",
        schema_version=None,
    )
    _set_terminal(application, status=status, artifacts=(diagnostic,))
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    result = System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert result.run_state.status is status
    assert result.invocation == application.invocation_value
    assert result.typed_output is None
    assert result.run_state.blocker is not None
    assert result.run_state.artifacts == (diagnostic,)


def test_preflight_blocked_result_does_not_require_unavailable_diagnostic_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    absent = ArtifactRef(
        name="unavailable_preflight_dependency",
        uri=f"artifact://sha256/{'f' * 64}",
        media_type="application/json",
        sha256="f" * 64,
        bytes=1,
        schema_version=None,
    )
    _set_terminal(
        application,
        status=RunStatus.BLOCKED,
        artifacts=(absent,),
        attempt=0,
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    result = System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert result.run_state.status is RunStatus.BLOCKED
    assert result.run_state.attempt == 0
    assert result.invocation is None
    assert result.typed_output is None


def test_preflight_attempt_zero_cannot_claim_a_succeeded_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    application.state = application.state.model_copy(
        update={
            "attempt": 0,
            "max_attempts": 0,
            "invocation_digest": None,
            "events": tuple(
                event.model_copy(update={"attempt": 0}) for event in application.state.events
            ),
        }
    )
    application.invocation_value = None  # type: ignore[assignment]
    application.page = _event_page(application.state)
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "durable_invocation_mismatch"
    assert raised.value.run_state == application.state


def test_started_run_artifacts_must_be_canonical_destination_cas_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    outside = tmp_path / "outside-diagnostic.json"
    payload = b'{"diagnostic":true}\n'
    outside.write_bytes(payload)
    non_cas = ArtifactRef(
        name="outside_diagnostic",
        uri=outside.resolve().as_uri(),
        media_type="application/json",
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
        schema_version=None,
    )
    _set_terminal(application, status=RunStatus.BLOCKED, artifacts=(non_cas,))
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "run_artifact_invalid"
    assert raised.value.run_state == application.state


def test_persistence_diagnostic_suppresses_a_state_with_non_cas_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    outside = tmp_path / "outside-persistence-diagnostic"
    payload = b"diagnostic\n"
    outside.write_bytes(payload)
    non_cas = ArtifactRef(
        name="outside_persistence_diagnostic",
        uri=outside.resolve().as_uri(),
        media_type="text/plain",
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
        schema_version=None,
    )

    class PersistenceFailureApplication(_RecordingReplayApplication):
        def replay(self, value: Text2EnvReplayInput) -> RunState:
            self.replay_calls.append(value)
            raise RunPersistenceError(
                operation="terminal state",
                run_id=self.run_id,
                state=self.state,
                cause=OSError("database unavailable"),
            )

    application = PersistenceFailureApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    _set_terminal(application, status=RunStatus.FAILED, artifacts=(non_cas,))
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "run_persistence_failed"
    assert raised.value.run_state is None


def test_durable_event_read_failure_is_one_domain_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)

    class CorruptJournalApplication(_RecordingReplayApplication):
        def events(self, **kwargs: object) -> EventPage:
            self.event_reads.append(kwargs)
            raise EventJournalCorruptionError("stored envelope is corrupt")

    application = CorruptJournalApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "durable_read_failed"
    assert raised.value.run_state == application.state


def test_application_paths_are_rechecked_after_durable_and_cas_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    another_root = tmp_path / "another-replay-cas"
    another_root.mkdir()

    class ChangedRootApplication(_RecordingReplayApplication):
        def run_state(self, run_id: UUID) -> RunState:
            value = super().run_state(run_id)
            if len(self.state_reads) == 2:
                self.artifact_root = another_root
            return value

    application = ChangedRootApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "application_invalid"
    assert raised.value.run_state == application.state


def test_started_blocker_references_must_belong_to_terminal_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    diagnostic = _put_bytes(
        store,
        tmp_path / "blocker-diagnostic",
        name="blocker_diagnostic",
        payload=b"blocker\n",
        media_type="text/plain",
        schema_version=None,
    )
    _set_terminal(application, status=RunStatus.BLOCKED, artifacts=(diagnostic,))
    application.state = application.state.model_copy(update={"artifacts": ()})
    application.page = _event_page(application.state)
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "run_artifact_invalid"
    assert raised.value.run_state == application.state


def test_malformed_event_page_is_reported_as_one_domain_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    application.page = EventPage(
        events=(object(), object()),  # type: ignore[arg-type]
        last_event_id=2,
        has_more=False,
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "durable_events_mismatch"
    assert raised.value.run_state == application.state


def test_public_interface_has_no_application_factory_handler_or_retry_injection(
    tmp_path: Path,
) -> None:
    promoted, store, _typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)

    assert tuple(inspect.signature(System2ReplayEvidenceAcquirer).parameters) == (
        "application_settings",
    )
    assert tuple(inspect.signature(System2ReplayEvidenceAcquirer.acquire).parameters) == (
        "self",
        "promoted",
    )
    with pytest.raises(TypeError, match="ReplayApplicationSettings"):
        System2ReplayEvidenceAcquirer(application_settings=object())  # type: ignore[arg-type]
    acquirer = System2ReplayEvidenceAcquirer(application_settings=settings)
    with pytest.raises(TypeError, match="PromotedSystem2ReplayInput"):
        acquirer.acquire(promoted=object())  # type: ignore[arg-type]
    assert promoted.destination_artifact_root == store.root


def test_acquirer_is_one_shot_because_completed_state_root_is_not_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    factory_calls: list[ReplayApplicationSettings] = []

    def factory(value: ReplayApplicationSettings) -> _RecordingReplayApplication:
        factory_calls.append(value)
        return _factory_result(value, application)

    monkeypatch.setattr(module, "create_replay_application", factory)
    acquirer = System2ReplayEvidenceAcquirer(application_settings=settings)

    first = acquirer.acquire(promoted=promoted)
    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        acquirer.acquire(promoted=promoted)

    assert first.run_state.status is RunStatus.SUCCEEDED
    assert raised.value.reason == "state_root_not_fresh"
    assert first.journal_path.read_bytes() == b"sqlite fixture"
    assert factory_calls == [settings]
    assert application.replay_calls == [promoted.replay_input]


def test_persistence_diagnostic_must_belong_to_the_failed_run_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)

    class MismatchedPersistenceApplication(_RecordingReplayApplication):
        def replay(self, value: Text2EnvReplayInput) -> RunState:
            self.replay_calls.append(value)
            raise RunPersistenceError(
                operation="terminal state",
                run_id=UUID("94000000-0000-4000-8000-000000000099"),
                state=self.state,
                cause=OSError("database unavailable"),
            )

    application = MismatchedPersistenceApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "run_persistence_failed"
    assert raised.value.run_state is None


@pytest.mark.parametrize("damage", ["mismatch", "non_cas"])
def test_preflight_blocker_references_are_exact_canonical_terminal_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    first = ArtifactRef(
        name="first_preflight_ref",
        uri=f"artifact://sha256/{'d' * 64}",
        media_type="application/json",
        sha256="d" * 64,
        bytes=1,
        schema_version=None,
    )
    _set_terminal(application, status=RunStatus.BLOCKED, artifacts=(first,), attempt=0)
    assert application.state.blocker is not None
    if damage == "mismatch":
        second = first.model_copy(
            update={
                "name": "second_preflight_ref",
                "uri": f"artifact://sha256/{'e' * 64}",
                "sha256": "e" * 64,
            }
        )
        blocker = application.state.blocker.model_copy(update={"artifact_refs": (second,)})
        application.state = application.state.model_copy(update={"blocker": blocker})
    else:
        outside = tmp_path / "preflight-diagnostic"
        outside.write_bytes(b"x")
        non_cas = first.model_copy(
            update={
                "uri": outside.resolve().as_uri(),
                "sha256": hashlib.sha256(b"x").hexdigest(),
            }
        )
        blocker = application.state.blocker.model_copy(update={"artifact_refs": (non_cas,)})
        events = (
            application.state.events[0],
            application.state.events[1].model_copy(update={"artifact_refs": (non_cas,)}),
        )
        application.state = application.state.model_copy(
            update={"artifacts": (non_cas,), "blocker": blocker, "events": events}
        )
    application.page = _event_page(application.state)
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "run_artifact_invalid"
    assert raised.value.run_state == application.state


@pytest.mark.parametrize("invalid_event_id", [True, 0, "71"])
def test_event_cursor_ids_are_exact_positive_integers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_event_id: object,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    first, second = application.page.events
    application.page = EventPage(
        events=(
            StoredRunEvent(
                event_id=invalid_event_id,  # type: ignore[arg-type]
                envelope=first.envelope,
            ),
            second,
        ),
        last_event_id=second.event_id,
        has_more=False,
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "durable_events_mismatch"


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("state_type", "state_root_invalid"),
        ("settings_root_type", "artifact_root_invalid"),
        ("settings_root_file", "artifact_root_invalid"),
        ("promoted_root_missing", "artifact_root_invalid"),
        ("root_mismatch", "artifact_root_mismatch"),
        ("replay_input_type", "promoted_input_invalid"),
        ("environment_ref_type", "promoted_input_invalid"),
    ],
)
def test_preflight_rejects_invalid_inputs_before_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    reason: str,
) -> None:
    promoted, store, _typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    if case == "state_type":
        settings = replace(settings, state_root="not-a-path")  # type: ignore[arg-type]
    elif case == "settings_root_type":
        settings = replace(
            settings,
            evidence_artifact_root="not-a-path",  # type: ignore[arg-type]
        )
    elif case == "settings_root_file":
        regular = tmp_path / "regular-file"
        regular.write_text("not a directory", encoding="utf-8")
        settings = replace(settings, evidence_artifact_root=regular)
    elif case == "promoted_root_missing":
        promoted = replace(promoted, destination_artifact_root=tmp_path / "missing-cas")
    elif case == "root_mismatch":
        other = tmp_path / "other-cas"
        other.mkdir()
        promoted = replace(promoted, destination_artifact_root=other)
    elif case == "replay_input_type":
        promoted = replace(promoted, replay_input=object())  # type: ignore[arg-type]
    else:
        promoted = replace(
            promoted,
            environment_package_ref=object(),  # type: ignore[arg-type]
        )
    factory_calls: list[ReplayApplicationSettings] = []
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: factory_calls.append(value),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == reason
    assert raised.value.run_state is None
    assert factory_calls == []


@pytest.mark.parametrize(
    "damage",
    [
        "package_type",
        "package_ref_uri",
        "package_ref_media",
        "package_ref_schema",
        "catalog_type",
        "manifest_type",
        "catalog_media",
        "manifest_media",
        "catalog_uri",
        "manifest_uri",
    ],
)
def test_promoted_package_headers_must_be_exact_canonical_cas_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    promoted, store, _typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    if damage == "package_type":
        replay_input = promoted.replay_input.model_copy(update={"environment_package": object()})
        promoted = replace(promoted, replay_input=replay_input)
    elif damage.startswith("package_ref"):
        updates = {
            "package_ref_uri": {"uri": (tmp_path / "outside-package").resolve().as_uri()},
            "package_ref_media": {"media_type": "text/plain"},
            "package_ref_schema": {"schema_version": "harness.other.v1"},
        }
        promoted = replace(
            promoted,
            environment_package_ref=promoted.environment_package_ref.model_copy(
                update=updates[damage]
            ),
        )
    elif damage in {"catalog_type", "manifest_type"}:
        package = promoted.replay_input.environment_package
        field = "asset_catalog" if damage == "catalog_type" else "package_manifest"
        replay_input = promoted.replay_input.model_copy(
            update={"environment_package": package.model_copy(update={field: object()})}
        )
        promoted = replace(promoted, replay_input=replay_input)
    elif damage in {"catalog_media", "manifest_media"}:
        package = promoted.replay_input.environment_package
        field = "asset_catalog" if damage == "catalog_media" else "package_manifest"
        damaged_ref = getattr(package, field).model_copy(update={"media_type": "text/plain"})
        package = package.model_copy(update={field: damaged_ref})
        package_ref = _put_bytes(
            store,
            tmp_path / "forged-environment-package",
            name="forged_environment_package",
            payload=_canonical_json_bytes(package.model_dump(mode="json")),
            media_type="application/json",
            schema_version="harness.environment_package.v1",
        )
        replay_input = promoted.replay_input.model_copy(update={"environment_package": package})
        promoted = replace(
            promoted,
            replay_input=replay_input,
            environment_package_ref=package_ref,
        )
    else:
        package = promoted.replay_input.environment_package
        field = "asset_catalog" if damage == "catalog_uri" else "package_manifest"
        damaged_ref = getattr(package, field).model_copy(
            update={"uri": (tmp_path / f"outside-{field}").resolve().as_uri()}
        )
        replay_input = promoted.replay_input.model_copy(
            update={"environment_package": package.model_copy(update={field: damaged_ref})}
        )
        promoted = replace(promoted, replay_input=replay_input)
    factory_calls: list[ReplayApplicationSettings] = []
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: factory_calls.append(value),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "promoted_package_invalid"
    assert factory_calls == []


@pytest.mark.parametrize("damage", ["environment", "catalog", "manifest", "invalid_json"])
def test_promoted_package_closure_must_resolve_and_parse_before_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    promoted, store, _typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    absent = ArtifactRef(
        name=f"missing_{damage}",
        uri=f"artifact://sha256/{'0' * 64}",
        media_type="application/json",
        sha256="0" * 64,
        bytes=1,
        schema_version=(
            "harness.environment_package.v1"
            if damage == "environment"
            else "robotwin.asset_catalog.v1"
            if damage == "catalog"
            else "robotwin.generated_scene_package.v1"
        ),
    )
    if damage == "environment":
        promoted = replace(promoted, environment_package_ref=absent)
    elif damage in {"catalog", "manifest"}:
        package = promoted.replay_input.environment_package
        field = "asset_catalog" if damage == "catalog" else "package_manifest"
        replay_input = promoted.replay_input.model_copy(
            update={"environment_package": package.model_copy(update={field: absent})}
        )
        promoted = replace(promoted, replay_input=replay_input)
    else:
        invalid = _put_bytes(
            store,
            tmp_path / "invalid-environment",
            name="invalid_environment_package",
            payload=b"{broken",
            media_type="application/json",
            schema_version="harness.environment_package.v1",
        )
        promoted = replace(promoted, environment_package_ref=invalid)
    factory_calls: list[ReplayApplicationSettings] = []
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: factory_calls.append(value),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "promoted_package_invalid"
    assert factory_calls == []


@pytest.mark.parametrize("damage", ["different_package", "noncanonical_bytes"])
def test_environment_package_bytes_must_exactly_bind_the_embedded_typed_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    promoted, store, _typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    package = promoted.replay_input.environment_package
    stored_package = (
        package.model_copy(update={"seed": 92}) if damage == "different_package" else package
    )
    payload = (
        _canonical_json_bytes(stored_package.model_dump(mode="json"))
        if damage == "different_package"
        else (json.dumps(package.model_dump(mode="json"), indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    )
    package_ref = _put_bytes(
        store,
        tmp_path / "alternate-environment",
        name="alternate_environment_package",
        payload=payload,
        media_type="application/json",
        schema_version="harness.environment_package.v1",
    )
    promoted = replace(promoted, environment_package_ref=package_ref)
    factory_calls: list[ReplayApplicationSettings] = []
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: factory_calls.append(value),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "promoted_package_invalid"
    assert factory_calls == []


def test_replay_runtime_exception_is_one_domain_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)

    class FailedReplayApplication(_RecordingReplayApplication):
        def replay(self, value: Text2EnvReplayInput) -> RunState:
            self.replay_calls.append(value)
            raise RuntimeError("runtime adapter failed before returning a state")

    application = FailedReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "replay_failed"
    assert raised.value.run_state is None
    assert application.replay_calls == [promoted.replay_input]


@pytest.mark.parametrize("returned_kind", ["wrong_type", "running"])
def test_replay_must_return_an_exact_terminal_run_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returned_kind: str,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    if returned_kind == "wrong_type":
        application.state = object()  # type: ignore[assignment]
        expected_diagnostic = None
    else:
        application.state = application.state.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "ended_at": None,
                "events": (application.state.events[0],),
                "artifacts": (),
                "output": None,
            }
        )
        expected_diagnostic = application.state
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "returned_state_invalid"
    assert raised.value.run_state is expected_diagnostic


def test_returned_state_must_equal_both_durable_state_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)

    class MissingStateApplication(_RecordingReplayApplication):
        def run_state(self, run_id: UUID) -> None:
            self.state_reads.append(run_id)
            return None

    application = MissingStateApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "durable_state_mismatch"
    assert raised.value.run_state == application.state
    assert application.state_reads == [application.run_id, application.run_id]


def test_started_invocation_must_match_the_dynamic_input_and_terminal_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    application.invocation_value = application.invocation_value.model_copy(
        update={"max_attempts": 1}
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "durable_invocation_mismatch"
    assert raised.value.run_state == application.state


@pytest.mark.parametrize("damage", ["untyped", "noncanonical_dump", "missing_from_state"])
def test_succeeded_output_must_be_strictly_typed_and_bound_to_terminal_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    if damage == "untyped":
        application.state = application.state.model_copy(update={"output": {}})
    elif damage == "noncanonical_dump":
        output = typed_output.model_dump(mode="json")
        output["replay_artifacts"] = tuple(output["replay_artifacts"])
        application.state = application.state.model_copy(update={"output": output})
    else:
        application.state = application.state.model_copy(
            update={"artifacts": (typed_output.replay_artifacts[-1],)}
        )
    application.page = _event_page(application.state)
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "typed_output_invalid"
    assert raised.value.run_state == application.state


@pytest.mark.parametrize("journal_kind", ["wrong_type", "directory"])
def test_application_journal_is_an_exact_canonical_regular_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    journal_kind: str,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    application.journal_path = "not-a-path" if journal_kind == "wrong_type" else settings.state_root  # type: ignore[assignment]

    def factory(value: ReplayApplicationSettings) -> _RecordingReplayApplication:
        value.state_root.mkdir()
        (value.state_root / "harness.sqlite3").write_bytes(b"sqlite fixture")
        return application

    monkeypatch.setattr(module, "create_replay_application", factory)

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "application_invalid"
    assert raised.value.run_state is None
    assert application.replay_calls == []


def test_malformed_dynamic_dependency_records_are_one_domain_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    application.invocation_value = application.invocation_value.model_copy(
        update={"dependencies": (object(),) * 5}
    )
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "durable_invocation_mismatch"
    assert raised.value.run_state == application.state


def test_non_succeeded_terminal_state_cannot_carry_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    _set_terminal(
        application,
        status=RunStatus.BLOCKED,
        artifacts=typed_output.replay_artifacts,
    )
    application.state = application.state.model_copy(update={"output": {"forged": True}})
    application.page = _event_page(application.state)
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "typed_output_invalid"
    assert raised.value.run_state == application.state


@pytest.mark.parametrize(
    "damage",
    [
        "skills_type",
        "skills_count",
        "descriptor_type",
        "skill_id",
        "version",
        "input_schema",
        "output_schema",
        "reproducibility",
        "max_attempts",
        "artifact_root",
    ],
)
def test_application_descriptor_and_artifact_root_are_exact_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    if damage == "skills_type":
        application.skills = list(application.skills)  # type: ignore[assignment]
    elif damage == "skills_count":
        application.skills = ()
    elif damage == "descriptor_type":
        application.skills = (object(),)  # type: ignore[assignment]
    elif damage == "artifact_root":
        other_root = tmp_path / "other-application-cas"
        other_root.mkdir()
        application.artifact_root = other_root
    else:
        updates = {
            "skill_id": {"skill_id": "text2env.validate"},
            "version": {"version": "2.0.0"},
            "input_schema": {"input_schema": "harness.other_input.v1"},
            "output_schema": {"output_schema": "harness.other_output.v1"},
            "reproducibility": {
                "reproducibility": ExecutionReproducibility.CONTENT_BITWISE_DETERMINISTIC
            },
            "max_attempts": {"max_attempts": 1},
        }
        application.skills = (application.skills[0].model_copy(update=updates[damage]),)
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "application_invalid"
    assert raised.value.run_state is None
    assert application.replay_calls == []


@pytest.mark.parametrize("damage", ["noncanonical", "missing"])
def test_started_event_only_artifacts_must_resolve_from_destination_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    event_only_ref = ArtifactRef(
        name="event_only_evidence",
        uri=f"artifact://sha256/{'0' * 64}",
        media_type="application/json",
        sha256="0" * 64,
        bytes=1,
        schema_version=None,
    )
    if damage == "noncanonical":
        outside = tmp_path / "outside-event-evidence"
        outside.write_bytes(b"x")
        event_only_ref = event_only_ref.model_copy(
            update={
                "uri": outside.resolve().as_uri(),
                "sha256": hashlib.sha256(b"x").hexdigest(),
            }
        )
    events = (
        application.state.events[0],
        application.state.events[1].model_copy(update={"artifact_refs": (event_only_ref,)}),
    )
    application.state = application.state.model_copy(update={"events": events})
    application.page = _event_page(application.state)
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    with pytest.raises(System2ReplayEvidenceAcquisitionError) as raised:
        System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert raised.value.reason == "run_artifact_invalid"
    assert raised.value.run_state == application.state


def test_valid_event_only_artifact_need_not_belong_to_terminal_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    event_only_ref = _put_bytes(
        store,
        tmp_path / "event-only-source",
        name="event_only_evidence",
        payload=b'{"event_only":true}\n',
        media_type="application/json",
        schema_version=None,
    )
    events = (
        application.state.events[0],
        application.state.events[1].model_copy(update={"artifact_refs": (event_only_ref,)}),
    )
    application.state = application.state.model_copy(update={"events": events})
    application.page = _event_page(application.state)
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    result = System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert result.run_state == application.state
    assert event_only_ref not in result.run_state.artifacts
    assert result.typed_output == typed_output


def test_duplicate_exact_state_and_event_artifact_refs_resolve_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, store, typed_output = _promoted(tmp_path)
    settings = _settings(tmp_path, store.root)
    application = _RecordingReplayApplication(
        artifact_root=store.root,
        journal_path=settings.state_root / "harness.sqlite3",
        promoted=promoted,
        typed_output=typed_output,
    )
    resolved_run_refs: list[ArtifactRef] = []
    real_resolve = LocalArtifactStore.resolve

    def record_resolve(self: LocalArtifactStore, ref: ArtifactRef) -> object:
        if ref in typed_output.replay_artifacts:
            resolved_run_refs.append(ref)
        return real_resolve(self, ref)

    monkeypatch.setattr(LocalArtifactStore, "resolve", record_resolve)
    monkeypatch.setattr(
        module,
        "create_replay_application",
        lambda value: _factory_result(value, application),
    )

    result = System2ReplayEvidenceAcquirer(application_settings=settings).acquire(promoted=promoted)

    assert result.typed_output == typed_output
    assert resolved_run_refs == list(typed_output.replay_artifacts)
