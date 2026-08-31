"""Generate the fixed empirical qualification for ``text2env.replay@1.0.0``.

The public interface accepts only operator-selected filesystem locators and
timeouts.  Skill code, descriptors, handlers, and qualification receipts are
assembled internally.  No bundle is published until the fixed direct candidate
run and the unregistered production-kernel candidate evaluation satisfy the
strict replay loader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping
from uuid import UUID

from pydantic import BaseModel, ValidationError

from .application import CompileApplication, CompileApplicationSettings, create_compile_application
from .artifacts import LocalArtifactStore
from .event_journal import SQLiteEventJournal
from .events import RunRecorder
from .handlers.text2env_replay import (
    Text2EnvReplayWiring,
    build_text2env_replay_wiring,
)
from .media_sandbox import NativeCgroupSandbox
from .media_verifier import SubprocessReplayMediaVerifier
from .package_store import PackageStore
from .qualification import (
    IMPLEMENTATION_MANIFEST_SCHEMA_ID,
    QUALIFICATION_REPORT_SCHEMA_ID,
    ImplementationFileV1,
    ImplementationManifestV1,
    QualificationCheckV1,
    QualificationReportV1,
    _file_snapshot,
    _implementation_bundle_sha256,
    _source_tree_sha256,
)
from .registry import (
    HandlerResult,
    QualificationCandidate,
    RunContext,
    SkillRegistry,
    _invocation_digest,
)
from .replay_dependencies import REPLAY_DEPENDENCY_NAMES, TEXT2ENV_REPLAY_SKILL_REF
from .replay_qualification import (
    REPLAY_QUALIFICATION_CASE_ID,
    REPLAY_QUALIFICATION_CHECK_NAMES,
    REPLAY_QUALIFICATION_REQUEST,
    ReplayCaseBindingClaim,
    ReplayDirectSupervisorReceipt,
    ReplayEventLifecycleClaim,
    ReplayEvidenceClosureManifest,
    ReplayExactDependenciesClaim,
    ReplayExecutionClaim,
    ReplayExecutionsClaim,
    ReplayHarnessEventTranscript,
    ReplayHarnessSourceManifest,
    ReplayKernelEvaluationReceipt,
    ReplayLifecycleRunClaim,
    ReplayMediaDecodeClaim,
    ReplayMediaDecodeRunClaim,
    ReplayOperatorFileIdentity,
    ReplayOperatorInputsManifest,
    ReplayPhysicsValidationClaim,
    ReplayPhysicsValidationRunClaim,
    ReplayQualificationCandidateExecutionClaim,
    ReplayQualificationError,
    ReplayRuntimeAssetSnapshotClaim,
    _discover_evidence_closure,
    _require_actual_distribution_root,
    _snapshot_harness_source,
    verify_replay_qualification_documents,
)
from .run_store import SQLiteRunStore
from .runtime_capability import (
    RUNTIME_ARTIFACT_PATHS,
    RUNTIME_CAPABILITY_SCHEMA,
    canonical_capability_bytes,
    validate_runtime_capability_document,
)
from .runtime_events import RuntimeEvent, RuntimeEventCodec, RuntimeEventKind
from .runtime_executor import (
    RuntimeExecution,
    RuntimeExecutionStatus,
    RuntimeExecutor,
    RuntimeJob,
    SubprocessRoboTwinRuntimeExecutor,
)
from .schemas import (
    ArtifactRef,
    DependencyRef,
    Event,
    Invocation,
    RunState,
    RunStatus,
    RuntimeConfig,
    SkillQualification,
    Text2EnvCompileOutput,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)

_SKILL_REF = "text2env.replay@1.0.0"
_REGRESSION_COMMAND = (
    "python -m self_improving.harness.qualify_replay --settings replay-qualification-settings.json"
)
_SEED = 7
_TASK_CONFIG = "demo_clean"
_MIN_VISIBLE_PIXELS = 64
_CHECKPOINT_STEPS = 120
_RUNTIME_CONFIG = RuntimeConfig(
    precheck_steps=0,
    settle_steps=900,
    contact_window_steps=120,
    video_frames=120,
    fps=12,
)
_DIRECT_RUN_ID = UUID("71000000-0000-4000-8000-000000000001")
_KERNEL_RUN_ID = UUID("71000000-0000-4000-8000-000000000002")
_QUALIFICATION_DATE = date(2026, 8, 31)
_IMPLEMENTATION_PATHS = (
    "self_improving/harness/application.py",
    "self_improving/harness/artifacts.py",
    "self_improving/harness/assets.py",
    "self_improving/harness/event_journal.py",
    "self_improving/harness/events.py",
    "self_improving/harness/handlers/text2env_compile.py",
    "self_improving/harness/handlers/text2env_compile_dependencies.py",
    "self_improving/harness/handlers/text2env_replay.py",
    "self_improving/harness/media_sandbox.py",
    "self_improving/harness/media_verifier.py",
    "self_improving/harness/native/media_sandbox.c",
    "self_improving/harness/package_store.py",
    "self_improving/harness/qualification.py",
    "self_improving/harness/qualify_replay.py",
    "self_improving/harness/registry.py",
    "self_improving/harness/replay_dependencies.py",
    "self_improving/harness/replay_qualification.py",
    "self_improving/harness/run_store.py",
    "self_improving/harness/runtime_assets.py",
    "self_improving/harness/runtime_capability.py",
    "self_improving/harness/runtime_events.py",
    "self_improving/harness/runtime_executor.py",
    "self_improving/harness/schema_catalog.py",
    "self_improving/harness/schemas/base.py",
    "self_improving/harness/schemas/common.py",
    "self_improving/harness/schemas/text2env.py",
)


class ReplayQualificationGenerationError(RuntimeError):
    """A generator precondition or empirical qualification gate failed."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ReplayQualificationSettings:
    """Operator locators required by the fixed replay qualification."""

    bundle_root: Path
    scratch_root: Path
    distribution_root: Path
    scene_gen_root: Path
    ledger_contract_root: Path
    asset_catalog_path: Path
    allowed_asset_roots: tuple[Path, ...]
    interpreter: Path
    runtime_runner: Path
    runtime_module_root: Path
    runtime_capability_path: Path
    media_launcher: Path
    static_ffmpeg: Path
    delegated_cgroup_root: Path
    runtime_timeout_seconds: float
    capability_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ReplayQualificationResult:
    """The strict records atomically published after every replay gate passes."""

    bundle_root: Path
    qualification: SkillQualification
    report: QualificationReportV1
    manifest: ImplementationManifestV1
    artifact_root: Path
    evidence_closure: tuple[ArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class _FixedCaseExecution:
    assembly: _ReplayAssembly
    artifact_store: LocalArtifactStore
    compile_output: Text2EnvCompileOutput
    replay_input: Text2EnvReplayInput
    direct: _CompletedReplay
    kernel: _CompletedReplay
    source_before: _SourceSnapshot
    source_before_ref: ArtifactRef
    operator_before: ReplayOperatorInputsManifest
    operator_before_ref: ArtifactRef
    direct_supervisor_ref: ArtifactRef
    kernel_evaluation_ref: ArtifactRef


@dataclass(frozen=True, slots=True)
class _RuntimeObservation:
    execution: RuntimeExecution
    delivered_events: tuple[RuntimeEvent, ...]


@dataclass(slots=True)
class _ObservedRuntimeExecutor:
    inner: RuntimeExecutor
    observations: list[_RuntimeObservation] = field(default_factory=list)

    @property
    def identity(self) -> Any:
        return self.inner.identity

    def execute(
        self,
        job: RuntimeJob,
        observer: Callable[[RuntimeEvent], None],
    ) -> RuntimeExecution:
        delivered: list[RuntimeEvent] = []

        def observed(event: RuntimeEvent) -> None:
            delivered.append(event)
            observer(event)

        execution = self.inner.execute(job, observed)
        self.observations.append(
            _RuntimeObservation(
                execution=execution,
                delivered_events=tuple(delivered),
            )
        )
        return execution


@dataclass(slots=True)
class _CountingHandler:
    handler: Callable[[Any, RunContext], HandlerResult]
    calls: int = 0

    def __call__(self, value: Any, context: RunContext) -> HandlerResult:
        self.calls += 1
        return self.handler(value, context)


@dataclass(frozen=True, slots=True)
class _ReplayAssembly:
    compile_application: CompileApplication
    artifact_store: LocalArtifactStore
    package_store: PackageStore
    wiring: Text2EnvReplayWiring
    runtime_executor: _ObservedRuntimeExecutor


@dataclass(frozen=True, slots=True)
class _CompletedReplay:
    output: Text2EnvReplayOutput
    artifacts: tuple[ArtifactRef, ...]
    dependencies: tuple[DependencyRef, ...]
    state: RunState
    handler_call_count: int
    observation: _RuntimeObservation
    invocation_digest: str
    invocation: Invocation
    journal_events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class _InspectedReplay:
    lifecycle: ReplayLifecycleRunClaim
    media: ReplayMediaDecodeRunClaim
    physics: ReplayPhysicsValidationRunClaim
    execution: dict[str, Any]
    runtime_asset_manifest_sha256: str
    runtime_asset_member_count: int
    verifier_identity_sha256: str
    ffmpeg_sha256: str
    launcher_sha256: str


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    harness: ReplayHarnessSourceManifest
    scene_gen_sha256: str
    ledger_sha256: str

    @property
    def files(self) -> tuple[ImplementationFileV1, ...]:
        return self.harness.files


def generate_replay_qualification(
    settings: ReplayQualificationSettings,
) -> ReplayQualificationResult:
    """Execute, strictly reload, then atomically publish one fixed qualification."""

    bundle_root = _new_output_root(settings.bundle_root, label="bundle")
    scratch_root = _new_output_root(settings.scratch_root, label="scratch")
    _positive_timeout(settings.runtime_timeout_seconds, label="runtime_timeout_seconds")
    _positive_timeout(settings.capability_timeout_seconds, label="capability_timeout_seconds")

    scratch_root.mkdir(parents=True)
    execution = _execute_fixed_case(settings, scratch_root)
    after_execution = _snapshot_sources(settings, phase="after_execution")
    after_execution_ref = _publish_source_manifest(
        execution.artifact_store,
        scratch_root,
        after_execution,
    )
    operator_after, operator_after_ref = _snapshot_operator_inputs(
        settings,
        artifact_store=execution.artifact_store,
        scratch_root=scratch_root,
        phase="before_publish",
    )
    before_publish = _snapshot_sources(settings, phase="before_publish")
    before_publish_ref = _publish_source_manifest(
        execution.artifact_store,
        scratch_root,
        before_publish,
    )
    if not _source_snapshots_match(
        execution.source_before,
        after_execution,
        before_publish,
    ):
        raise ReplayQualificationGenerationError(
            "source_changed",
            "implementation or source-contract bytes changed during replay qualification",
        )
    if not _operator_manifests_match(execution.operator_before, operator_after):
        raise ReplayQualificationGenerationError(
            "operator_inputs_changed",
            "operator inputs changed during replay qualification",
        )

    evidence_roots = (
        execution.operator_before_ref,
        operator_after_ref,
        execution.source_before_ref,
        after_execution_ref,
        before_publish_ref,
        execution.direct_supervisor_ref,
        execution.kernel_evaluation_ref,
    )
    try:
        evidence_refs = _discover_evidence_closure(execution.artifact_store, evidence_roots)
    except ReplayQualificationError as error:
        raise ReplayQualificationGenerationError(
            "evidence_closure_invalid",
            f"candidate evidence closure is unavailable or inconsistent: {error.reason}",
        ) from error
    closure = ReplayEvidenceClosureManifest(
        schema_version="harness.replay_qualification.evidence_closure.v1",
        skill_ref=_SKILL_REF,
        case_id=REPLAY_QUALIFICATION_CASE_ID,
        refs=evidence_refs,
    )
    closure_ref = _publish_model_artifact(
        execution.artifact_store,
        scratch_root,
        closure,
        name="replay_qualification_evidence_closure",
        schema_version="harness.replay_qualification.evidence_closure.v1",
    )
    execution_checks = _build_execution_checks(
        execution.assembly,
        compile_output=execution.compile_output,
        replay_input=execution.replay_input,
        direct=execution.direct,
        kernel=execution.kernel,
        operator_before_ref=execution.operator_before_ref,
        operator_after_ref=operator_after_ref,
        direct_supervisor_ref=execution.direct_supervisor_ref,
        kernel_evaluation_ref=execution.kernel_evaluation_ref,
        closure_ref=closure_ref,
    )
    manifest = _build_manifest(before_publish)
    source_check = QualificationCheckV1(
        name="07.source_stability",
        status="pass",
        evidence={
            "schema_version": "harness.replay_qualification.source_stability.v1",
            "changed_during_qualification": False,
            "implementation_sha256": manifest.bundle_sha256,
            "implementation_file_count": len(before_publish.files),
            "scene_gen_tree_sha256": before_publish.scene_gen_sha256,
            "ledger_contract_tree_sha256": before_publish.ledger_sha256,
            "harness_tree_sha256": before_publish.harness.tree_sha256,
            "before_manifest": execution.source_before_ref.model_dump(mode="json"),
            "after_execution_manifest": after_execution_ref.model_dump(mode="json"),
            "before_publish_manifest": before_publish_ref.model_dump(mode="json"),
        },
    )
    checks = tuple(sorted((*execution_checks, source_check), key=lambda item: item.name))
    report = QualificationReportV1(
        schema_version=QUALIFICATION_REPORT_SCHEMA_ID,
        skill_ref=_SKILL_REF,
        status="pass",
        deterministic_case_id=REPLAY_QUALIFICATION_CASE_ID,
        regression_command=_REGRESSION_COMMAND,
        implementation_sha256=manifest.bundle_sha256,
        scene_gen_tree_sha256=before_publish.scene_gen_sha256,
        ledger_contract_tree_sha256=before_publish.ledger_sha256,
        checks=checks,
    )
    report_bytes = _document_bytes(report)
    qualification = SkillQualification(
        skill_ref=_SKILL_REF,
        status="pass",
        deterministic_case_id=REPLAY_QUALIFICATION_CASE_ID,
        regression_command=_REGRESSION_COMMAND,
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )
    documents = {
        "manifest.json": _document_bytes(manifest),
        "qualification.json": _document_bytes(qualification),
        "report.json": report_bytes,
    }
    try:
        verify_replay_qualification_documents(
            documents,
            artifact_store=execution.artifact_store,
            implementation_root=settings.distribution_root,
            scene_gen_root=settings.scene_gen_root,
            ledger_contract_root=settings.ledger_contract_root,
        )
    except ReplayQualificationError as error:
        raise ReplayQualificationGenerationError(
            "qualification_gate_failed",
            f"strict replay qualification loader rejected generated evidence: {error.reason}",
        ) from error
    final_source = _snapshot_sources(settings, phase="before_publish")
    final_operator, final_operator_ref = _snapshot_operator_inputs(
        settings,
        artifact_store=execution.artifact_store,
        scratch_root=scratch_root / "final-rescan",
        phase="before_publish",
    )
    if (
        final_source != before_publish
        or not _operator_manifests_match(
            operator_after,
            final_operator,
        )
        or final_operator_ref != operator_after_ref
    ):
        raise ReplayQualificationGenerationError(
            "prepublish_inputs_changed",
            "source or operator inputs changed after strict verification",
        )
    _write_bundle_atomically(bundle_root, documents)
    return ReplayQualificationResult(
        bundle_root=bundle_root.resolve(),
        qualification=qualification,
        report=report,
        manifest=manifest,
        artifact_root=execution.artifact_store.root,
        evidence_closure=(closure_ref, *evidence_refs),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the real fixed qualification from one strict operator settings document."""

    parser = argparse.ArgumentParser(prog="robot-harness-qualify-replay")
    parser.add_argument("--settings", required=True, type=Path)
    arguments = parser.parse_args(argv)
    result = generate_replay_qualification(_load_settings_manifest(arguments.settings))
    print(
        json.dumps(
            {
                "schema_version": "harness.replay_qualification.application_result.v1",
                "bundle_root": str(result.bundle_root),
                "artifact_root": str(result.artifact_root),
                "qualification_report_sha256": result.qualification.report_sha256,
                "evidence_closure": [
                    item.model_dump(mode="json") for item in result.evidence_closure
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _load_settings_manifest(path: Path) -> ReplayQualificationSettings:
    manifest_path = _regular_file(path, label="settings")
    try:
        value = json.loads(
            manifest_path.read_bytes(),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ReplayQualificationGenerationError(
            "settings_manifest_invalid",
            "replay qualification settings must be strict JSON",
        ) from error
    expected = {
        "bundle_root",
        "scratch_root",
        "distribution_root",
        "scene_gen_root",
        "ledger_contract_root",
        "asset_catalog_path",
        "allowed_asset_roots",
        "interpreter",
        "runtime_runner",
        "runtime_module_root",
        "runtime_capability_path",
        "media_launcher",
        "static_ffmpeg",
        "delegated_cgroup_root",
        "runtime_timeout_seconds",
        "capability_timeout_seconds",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ReplayQualificationGenerationError(
            "settings_manifest_invalid",
            "replay qualification settings have missing or extra fields",
        )
    path_fields = expected - {
        "allowed_asset_roots",
        "runtime_timeout_seconds",
        "capability_timeout_seconds",
    }
    if any(not isinstance(value[name], str) or not value[name] for name in path_fields):
        raise ReplayQualificationGenerationError(
            "settings_manifest_invalid",
            "replay qualification path settings must be nonempty strings",
        )
    roots = value["allowed_asset_roots"]
    if (
        not isinstance(roots, list)
        or not roots
        or any(not isinstance(item, str) or not item for item in roots)
    ):
        raise ReplayQualificationGenerationError(
            "settings_manifest_invalid",
            "allowed_asset_roots must be a nonempty string array",
        )
    return ReplayQualificationSettings(
        **{name: Path(value[name]) for name in path_fields},
        allowed_asset_roots=tuple(Path(item) for item in roots),
        runtime_timeout_seconds=value["runtime_timeout_seconds"],
        capability_timeout_seconds=value["capability_timeout_seconds"],
    )


def _execute_fixed_case(
    settings: ReplayQualificationSettings,
    scratch_root: Path,
) -> _FixedCaseExecution:
    assembly = _assemble_fixed_case(settings, scratch_root)
    source_before = _snapshot_sources(settings, phase="before_execution")
    source_before_ref = _publish_source_manifest(
        assembly.artifact_store,
        scratch_root,
        source_before,
    )
    operator_before, operator_before_ref = _snapshot_operator_inputs(
        settings,
        artifact_store=assembly.artifact_store,
        scratch_root=scratch_root,
        phase="before_execution",
    )
    compile_state = assembly.compile_application.compile(
        request=REPLAY_QUALIFICATION_REQUEST,
        seed=_SEED,
        asset_catalog_path=settings.asset_catalog_path,
        generate_missing_assets=False,
    )
    _gate(
        compile_state.status is RunStatus.SUCCEEDED and compile_state.attempt == 1,
        "compile_failed",
        "the currently qualified compile Skill did not succeed exactly once",
    )
    try:
        compile_output = Text2EnvCompileOutput.model_validate(compile_state.output)
    except ValidationError as error:
        raise ReplayQualificationGenerationError(
            "compile_output_invalid",
            "the qualified compile Skill returned an invalid typed output",
        ) from error
    _verify_compile_artifacts(assembly.artifact_store, compile_output)

    replay_input = Text2EnvReplayInput(
        environment_package=compile_output.environment_package,
        runtime_config=_RUNTIME_CONFIG,
    )
    direct = _run_direct_candidate(assembly, replay_input, scratch_root)
    kernel = _run_production_kernel_candidate(assembly, replay_input, scratch_root)
    direct_supervisor_ref = _publish_run_receipt(
        assembly.artifact_store,
        scratch_root,
        direct,
        mode="candidate_direct",
    )
    kernel_evaluation_ref = _publish_run_receipt(
        assembly.artifact_store,
        scratch_root,
        kernel,
        mode="qualification_candidate",
    )
    return _FixedCaseExecution(
        assembly=assembly,
        artifact_store=assembly.artifact_store,
        compile_output=compile_output,
        replay_input=replay_input,
        direct=direct,
        kernel=kernel,
        source_before=source_before,
        source_before_ref=source_before_ref,
        operator_before=operator_before,
        operator_before_ref=operator_before_ref,
        direct_supervisor_ref=direct_supervisor_ref,
        kernel_evaluation_ref=kernel_evaluation_ref,
    )


def _assemble_fixed_case(
    settings: ReplayQualificationSettings,
    scratch_root: Path,
) -> _ReplayAssembly:
    catalog = _regular_file(settings.asset_catalog_path, label="asset_catalog_path")
    allowed_roots = _trusted_roots(settings.allowed_asset_roots)
    compile_application = create_compile_application(
        CompileApplicationSettings(
            state_root=scratch_root / "compile-application",
            asset_library_root=scratch_root / "compile-asset-library",
            external_catalog_roots=(catalog.parent,),
            allowed_asset_roots=allowed_roots,
            admission_date=_QUALIFICATION_DATE,
        )
    )
    artifact_store = LocalArtifactStore(compile_application.artifact_root)
    package_store = PackageStore(artifact_store)
    expected_capability_sha256 = _load_capability_digest(settings.runtime_capability_path)
    runtime = SubprocessRoboTwinRuntimeExecutor(
        interpreter=settings.interpreter,
        runner=settings.runtime_runner,
        work_root=scratch_root / "runtime-executor",
        timeout_seconds=_positive_timeout(
            settings.runtime_timeout_seconds,
            label="runtime_timeout_seconds",
        ),
        capability_timeout_seconds=_positive_timeout(
            settings.capability_timeout_seconds,
            label="capability_timeout_seconds",
        ),
        module_root=settings.runtime_module_root,
    )
    observed_runtime = _ObservedRuntimeExecutor(runtime)
    sandbox = NativeCgroupSandbox(
        launcher=settings.media_launcher,
        launcher_source=(
            settings.distribution_root / "self_improving/harness/native/media_sandbox.c"
        ),
        ffmpeg=settings.static_ffmpeg,
        delegated_cgroup_root=settings.delegated_cgroup_root,
    )
    media_verifier = SubprocessReplayMediaVerifier(sandbox=sandbox)
    wiring = build_text2env_replay_wiring(
        artifact_store=artifact_store,
        package_store=package_store,
        runtime_executor=observed_runtime,
        media_verifier=media_verifier,
        work_root=scratch_root / "replay-handler",
        dependency_work_root=scratch_root / "replay-dependencies",
        allowed_asset_roots=allowed_roots,
        expected_capability_sha256=expected_capability_sha256,
        task_config=_TASK_CONFIG,
        min_visible_pixels=_MIN_VISIBLE_PIXELS,
        checkpoint_steps=_CHECKPOINT_STEPS,
    )
    return _ReplayAssembly(
        compile_application=compile_application,
        artifact_store=artifact_store,
        package_store=package_store,
        wiring=wiring,
        runtime_executor=observed_runtime,
    )


def _run_direct_candidate(
    assembly: _ReplayAssembly,
    replay_input: Text2EnvReplayInput,
    scratch_root: Path,
) -> _CompletedReplay:
    try:
        dependencies = assembly.wiring.dependency_resolver.resolve(
            TEXT2ENV_REPLAY_SKILL_REF,
            replay_input,
        )
    except Exception as error:
        raise ReplayQualificationGenerationError(
            "dependency_resolution_failed",
            "direct candidate dependency resolution failed",
        ) from error
    invocation_digest = _invocation_digest(
        skill_id="text2env.replay",
        skill_version="1.0.0",
        effective_parameters=replay_input,
        dependencies=dependencies,
        max_attempts=2,
    )
    database = scratch_root / "candidate-direct.sqlite3"
    event_journal = SQLiteEventJournal(database)
    run_store = SQLiteRunStore(database)
    invocation = Invocation(
        run_id=_DIRECT_RUN_ID,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        effective_parameters=replay_input.model_dump(mode="json"),
        dependencies=dependencies,
        max_attempts=2,
        invocation_digest=invocation_digest,
    )
    run_store.put_invocation(invocation)
    recorder = RunRecorder(
        run_id=_DIRECT_RUN_ID,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=_SequenceClock(),
        sink=event_journal,
    )
    recorder.start(stage="preflight", attempt=1)
    counting = _CountingHandler(assembly.wiring.handler)
    observation_count = len(assembly.runtime_executor.observations)
    try:
        result = counting(
            replay_input,
            RunContext(
                run_id=_DIRECT_RUN_ID,
                attempt=1,
                dependencies=dependencies,
                _recorder=recorder,
                _artifact_resolver=assembly.artifact_store,
            ),
        )
        output = Text2EnvReplayOutput.model_validate(result.output)
        artifacts = _verified_artifacts(
            assembly.artifact_store,
            (*_artifact_refs(output), *result.artifacts),
        )
    except Exception as error:
        raise ReplayQualificationGenerationError(
            "candidate_execution_failed",
            f"direct replay candidate failed: {type(error).__name__}",
        ) from error
    recorder.finish(
        status=RunStatus.SUCCEEDED,
        stage="complete",
        artifact_refs=artifacts,
    )
    state = recorder.build_state(
        invocation_digest=invocation_digest,
        max_attempts=2,
        artifacts=artifacts,
        output=output.model_dump(mode="json"),
        blocker=None,
    )
    run_store.put_run_state(state)
    persisted_invocation = run_store.read_invocation(_DIRECT_RUN_ID)
    persisted_state = run_store.read_run_state(_DIRECT_RUN_ID)
    journal_page = event_journal.read(run_id=_DIRECT_RUN_ID, limit=200)
    _gate(
        counting.calls == 1
        and len(assembly.runtime_executor.observations) == observation_count + 1,
        "candidate_execution_count",
        "direct replay candidate must call the handler and runtime exactly once",
    )
    _gate(
        persisted_invocation == invocation
        and persisted_state == state
        and not journal_page.has_more
        and tuple(item.envelope.event for item in journal_page.events) == state.events,
        "candidate_persistence_mismatch",
        "direct replay candidate must be reconstructed from RunStore and SQLite journal",
    )
    assert persisted_invocation is not None
    assert persisted_state is not None
    return _CompletedReplay(
        output=output,
        artifacts=artifacts,
        dependencies=dependencies,
        state=persisted_state,
        handler_call_count=counting.calls,
        observation=assembly.runtime_executor.observations[-1],
        invocation_digest=invocation_digest,
        invocation=persisted_invocation,
        journal_events=tuple(item.envelope.event for item in journal_page.events),
    )


def _run_production_kernel_candidate(
    assembly: _ReplayAssembly,
    replay_input: Text2EnvReplayInput,
    scratch_root: Path,
) -> _CompletedReplay:
    database = scratch_root / "candidate-kernel.sqlite3"
    event_journal = SQLiteEventJournal(database)
    run_store = SQLiteRunStore(database)
    registry = SkillRegistry(
        artifact_resolver=assembly.artifact_store,
        dependency_resolver=assembly.wiring.dependency_resolver,
        event_sink=event_journal,
        clock=_SequenceClock(),
        run_id_factory=lambda: _KERNEL_RUN_ID,
        run_store=run_store,
    )
    candidate = QualificationCandidate(
        skill_id="text2env.replay",
        version="1.0.0",
        input_schema="harness.text2env_replay_input.v1",
        output_schema="harness.text2env_replay_output.v1",
        max_attempts=2,
    )
    counting = _CountingHandler(assembly.wiring.handler)
    observation_count = len(assembly.runtime_executor.observations)
    evaluation = registry.evaluate_candidate(
        candidate,
        counting,
        replay_input.model_dump(mode="json"),
    )
    _gate(
        evaluation.mode == "qualification_candidate"
        and evaluation.state.status is RunStatus.SUCCEEDED
        and evaluation.state.attempt == 1
        and evaluation.invocation is not None
        and counting.calls == 1
        and len(assembly.runtime_executor.observations) == observation_count + 1,
        "kernel_candidate_failed",
        "production-kernel candidate evaluation must succeed exactly once without retry",
    )
    _gate(
        not registry.list()
        and all(
            event.stage.startswith("qualification_candidate.") for event in evaluation.state.events
        ),
        "kernel_candidate_registration",
        "candidate evaluation must remain unregistered and explicitly namespaced",
    )
    assert evaluation.invocation is not None
    persisted_invocation = run_store.read_invocation(_KERNEL_RUN_ID)
    persisted_state = run_store.read_run_state(_KERNEL_RUN_ID)
    journal_page = event_journal.read(run_id=_KERNEL_RUN_ID, limit=200)
    _gate(
        persisted_invocation == evaluation.invocation
        and persisted_state == evaluation.state
        and not journal_page.has_more
        and tuple(item.envelope.event for item in journal_page.events) == evaluation.state.events,
        "kernel_candidate_persistence_mismatch",
        "kernel candidate must be reconstructed from RunStore and SQLite journal",
    )
    assert persisted_invocation is not None
    assert persisted_state is not None
    output = Text2EnvReplayOutput.model_validate(persisted_state.output)
    artifacts = _verified_artifacts(assembly.artifact_store, persisted_state.artifacts)
    return _CompletedReplay(
        output=output,
        artifacts=artifacts,
        dependencies=persisted_invocation.dependencies,
        state=persisted_state,
        handler_call_count=counting.calls,
        observation=assembly.runtime_executor.observations[-1],
        invocation_digest=persisted_invocation.invocation_digest,
        invocation=persisted_invocation,
        journal_events=tuple(item.envelope.event for item in journal_page.events),
    )


def _build_execution_checks(
    assembly: _ReplayAssembly,
    *,
    compile_output: Text2EnvCompileOutput,
    replay_input: Text2EnvReplayInput,
    direct: _CompletedReplay,
    kernel: _CompletedReplay,
    operator_before_ref: ArtifactRef,
    operator_after_ref: ArtifactRef,
    direct_supervisor_ref: ArtifactRef,
    kernel_evaluation_ref: ArtifactRef,
    closure_ref: ArtifactRef,
) -> tuple[QualificationCheckV1, ...]:
    _gate(
        direct.dependencies == kernel.dependencies
        and tuple(item.name for item in direct.dependencies)
        == tuple(sorted(REPLAY_DEPENDENCY_NAMES)),
        "dependency_closure_mismatch",
        "both replay executions must use the same exact five dependencies",
    )
    direct_inspected = _inspect_replay(
        assembly,
        run=direct,
        replay_input=replay_input,
    )
    kernel_inspected = _inspect_replay(
        assembly,
        run=kernel,
        replay_input=replay_input,
    )
    _gate(
        direct_inspected.runtime_asset_manifest_sha256
        == kernel_inspected.runtime_asset_manifest_sha256
        == direct.dependencies[-1].sha256
        and direct_inspected.runtime_asset_member_count
        == kernel_inspected.runtime_asset_member_count
        and direct_inspected.verifier_identity_sha256
        == kernel_inspected.verifier_identity_sha256
        == direct.dependencies[-2].sha256
        and direct_inspected.ffmpeg_sha256 == kernel_inspected.ffmpeg_sha256
        and direct_inspected.launcher_sha256 == kernel_inspected.launcher_sha256,
        "execution_identity_drift",
        "direct and production-kernel evidence identities disagree",
    )
    package = compile_output.environment_package
    package_manifest = _read_json_artifact(
        assembly.artifact_store,
        package.package_manifest,
        label="compile package manifest",
    )
    case_binding = ReplayCaseBindingClaim(
        schema_version="harness.replay_qualification.case_binding.v1",
        case_id=REPLAY_QUALIFICATION_CASE_ID,
        skill_ref=_SKILL_REF,
        request=REPLAY_QUALIFICATION_REQUEST,
        seed=_SEED,
        runtime_config=replay_input.runtime_config.model_dump(mode="json"),
        scene_spec_sha256=package.scene_spec_sha256,
        resolved_scene_sha256=package.resolved_scene_sha256,
        environment_package_id=package.package_id,
        asset_catalog_sha256=package.asset_catalog.sha256,
        package_manifest_sha256=package.package_manifest.sha256,
        operator_before_manifest=operator_before_ref,
        operator_after_manifest=operator_after_ref,
    )
    _gate(
        package_manifest.get("source_scene_spec_sha256") == package.scene_spec_sha256
        and package_manifest.get("resolved_scene_sha256") == package.resolved_scene_sha256
        and package_manifest.get("asset_catalog_sha256") == package.asset_catalog.sha256,
        "compile_package_binding",
        "qualified compile output and package manifest are not hash-bound",
    )
    exact_dependencies = ReplayExactDependenciesClaim(
        schema_version="harness.replay_qualification.exact_dependencies.v1",
        dependencies=direct.dependencies,
    )
    lifecycle = ReplayEventLifecycleClaim(
        schema_version="harness.replay_qualification.event_lifecycle.v1",
        checkpoint_steps=_CHECKPOINT_STEPS,
        candidate_direct=direct_inspected.lifecycle,
        production_kernel_candidate=kernel_inspected.lifecycle,
    )
    runtime_assets = ReplayRuntimeAssetSnapshotClaim(
        schema_version="harness.replay_qualification.runtime_asset_snapshot.v1",
        manifest_sha256=direct_inspected.runtime_asset_manifest_sha256,
        member_count=direct_inspected.runtime_asset_member_count,
        all_members_verified=True,
        self_contained=True,
        candidate_direct_manifest_sha256=direct_inspected.runtime_asset_manifest_sha256,
        production_kernel_candidate_manifest_sha256=(
            kernel_inspected.runtime_asset_manifest_sha256
        ),
    )
    media = ReplayMediaDecodeClaim(
        schema_version="harness.replay_qualification.media_decode.v1",
        verifier_identity_sha256=direct_inspected.verifier_identity_sha256,
        ffmpeg_sha256=direct_inspected.ffmpeg_sha256,
        launcher_sha256=direct_inspected.launcher_sha256,
        candidate_direct=direct_inspected.media,
        production_kernel_candidate=kernel_inspected.media,
    )
    physics = ReplayPhysicsValidationClaim(
        schema_version="harness.replay_qualification.physics_validation.v1",
        candidate_direct=direct_inspected.physics,
        production_kernel_candidate=kernel_inspected.physics,
    )
    executions = ReplayExecutionsClaim(
        schema_version="harness.replay_qualification.candidate_kernel_executions.v1",
        candidate_direct=ReplayExecutionClaim(
            mode="candidate_direct",
            **direct_inspected.execution,
            run_id=direct.state.run_id,
            invocation_digest=direct.invocation_digest,
            supervisor_receipt=direct_supervisor_ref,
        ),
        production_kernel_candidate=ReplayQualificationCandidateExecutionClaim(
            mode="qualification_candidate",
            **kernel_inspected.execution,
            run_id=kernel.state.run_id,
            invocation_digest=kernel.invocation_digest,
            evaluation_receipt=kernel_evaluation_ref,
        ),
        evidence_closure_manifest=closure_ref,
    )
    claims = (
        case_binding,
        exact_dependencies,
        lifecycle,
        runtime_assets,
        media,
        physics,
        executions,
    )
    names = tuple(name for name in REPLAY_QUALIFICATION_CHECK_NAMES if not name.startswith("07."))
    return tuple(
        QualificationCheckV1(
            name=name,
            status="pass",
            evidence=claim.model_dump(mode="json"),
        )
        for name, claim in zip(names, claims, strict=True)
    )


def _inspect_replay(
    assembly: _ReplayAssembly,
    *,
    run: _CompletedReplay,
    replay_input: Text2EnvReplayInput,
) -> _InspectedReplay:
    refs = _verified_artifacts(assembly.artifact_store, run.artifacts)
    receipt_ref = _one_artifact(
        refs,
        schema_version="harness.text2env_replay_receipt.v1",
        label="replay execution receipt",
    )
    transcript_ref = _one_artifact(
        refs,
        schema_version="harness.runtime_event_transcript.v1",
        label="runtime event transcript",
    )
    diagnostics_ref = _one_artifact(
        refs,
        schema_version="harness.runtime_execution_diagnostics.v1",
        label="runtime execution diagnostics",
    )
    runtime_asset_ref = _one_artifact(
        refs,
        schema_version="harness.runtime_asset_snapshot.v1",
        label="runtime asset manifest",
    )
    validation_ref = _one_artifact(
        refs,
        schema_version="robotwin.scene_validation.v1",
        label="runtime validation report",
    )
    evidence_ref = _one_artifact(
        refs,
        schema_version="robotwin.scene_runtime_evidence.v2",
        label="runtime evidence",
    )
    receipt = _read_json_artifact(
        assembly.artifact_store,
        receipt_ref,
        label="replay execution receipt",
    )
    diagnostics = _read_json_artifact(
        assembly.artifact_store,
        diagnostics_ref,
        label="runtime execution diagnostics",
    )
    validation = _read_json_artifact(
        assembly.artifact_store,
        validation_ref,
        label="runtime validation report",
    )
    evidence = _read_json_artifact(
        assembly.artifact_store,
        evidence_ref,
        label="runtime evidence",
    )
    transcript = assembly.artifact_store.resolve(transcript_ref).path.read_bytes()
    parsed_events = RuntimeEventCodec(
        allowed_artifact_paths=RUNTIME_ARTIFACT_PATHS
    ).parse_transcript(transcript)
    observed = run.observation
    execution = observed.execution
    _gate(
        execution.status is RuntimeExecutionStatus.SUCCEEDED
        and execution.failure is None
        and not execution.secondary_failures
        and execution.events == parsed_events == observed.delivered_events
        and execution.transcript == transcript
        and execution.transcript_complete
        and not execution.transcript_truncated
        and not execution.stdout_truncated
        and not execution.stderr_truncated,
        "runtime_lifecycle_invalid",
        "runtime event lifecycle or bounded streams are incomplete",
    )
    transcript_record = _object(diagnostics.get("transcript"), label="diagnostic transcript")
    _gate(
        transcript_record.get("sha256") == transcript_ref.sha256
        and transcript_record.get("complete") is True
        and transcript_record.get("truncated") is False,
        "runtime_diagnostics_invalid",
        "runtime diagnostics do not bind a complete transcript",
    )
    checkpoint_steps = tuple(
        event.completed_steps
        for event in parsed_events
        if event.kind is RuntimeEventKind.SIMULATION_CHECKPOINT
    )
    completed = next(
        (
            event.completed_steps
            for event in parsed_events
            if event.kind is RuntimeEventKind.SIMULATION_COMPLETED
        ),
        None,
    )
    lifecycle = ReplayLifecycleRunClaim(
        transcript_sha256=transcript_ref.sha256,
        event_count=len(parsed_events),
        event_kinds=tuple(event.kind.value for event in parsed_events),
        checkpoint_completed_steps=checkpoint_steps,
        simulation_completed_steps=completed,
        transcript_complete=True,
        live_observer_matched=True,
        streams_complete=True,
    )

    receipt_dependencies = tuple(
        DependencyRef.model_validate(value)
        for value in _array(
            receipt.get("invocation_dependencies"),
            label="receipt dependencies",
        )
    )
    receipt_runtime_config = receipt.get("runtime_config")
    _gate(
        receipt.get("skill_ref") == _SKILL_REF
        and receipt.get("environment_package_id") == replay_input.environment_package.package_id
        and receipt.get("package_manifest_sha256")
        == replay_input.environment_package.package_manifest.sha256
        and receipt.get("asset_catalog_sha256")
        == replay_input.environment_package.asset_catalog.sha256
        and receipt.get("resolved_scene_sha256")
        == replay_input.environment_package.resolved_scene_sha256
        and receipt_runtime_config == replay_input.runtime_config.model_dump(mode="json")
        and receipt_dependencies == run.dependencies,
        "execution_receipt_binding",
        "replay execution receipt is not input, package, config, and dependency bound",
    )

    runtime_assets = _object(receipt.get("runtime_assets"), label="receipt runtime assets")
    manifest_record = _object(
        runtime_assets.get("manifest"),
        label="receipt runtime asset manifest",
    )
    member_records = _array(
        runtime_assets.get("members"),
        label="receipt runtime asset members",
    )
    member_refs = tuple(
        ArtifactRef.model_validate(value)
        for value in _artifact_records(member_records, refs, label="runtime asset member")
    )
    _verified_artifacts(assembly.artifact_store, member_refs)
    _gate(
        runtime_assets.get("self_contained") is True
        and runtime_assets.get("dependency_enforced") is True
        and manifest_record.get("sha256") == runtime_asset_ref.sha256
        and len(member_refs) == len(member_records) > 0,
        "runtime_asset_evidence_invalid",
        "runtime asset receipt is incomplete or not CAS-verifiable",
    )

    media_verification = _object(
        receipt.get("media_verification"),
        label="receipt media verification",
    )
    video = _object(media_verification.get("video"), label="decoded video facts")
    pngs = _array(media_verification.get("pngs"), label="decoded PNG facts")
    media_records = _array(receipt.get("media"), label="receipt media")
    video_ref = _one_named_artifact(refs, name="observer_runtime", label="runtime video")
    _gate(
        video.get("sha256") == video_ref.sha256
        and len(pngs) == 7
        and len(media_records) == 8
        and all(_object(item, label="decoded PNG").get("format") == "PNG" for item in pngs),
        "media_evidence_invalid",
        "media receipt does not prove the fixed completely decoded media set",
    )
    media = ReplayMediaDecodeRunClaim(
        video_sha256=video_ref.sha256,
        fully_decoded=True,
        frame_count=video.get("frame_count"),
        source_unique_frame_count=video.get("source_unique_frame_count"),
        decoded_unique_frame_count=video.get("decoded_unique_frame_count"),
        fps_numerator=video.get("fps_numerator"),
        fps_denominator=video.get("fps_denominator"),
        width=video.get("width"),
        height=video.get("height"),
        format_name=video.get("format_name"),
        codec_name=video.get("codec_name"),
        pixel_format=video.get("pixel_format"),
        sample_aspect_ratio=video.get("sample_aspect_ratio"),
        square_sample_aspect_ratio_defaulted=video.get("square_sample_aspect_ratio_defaulted"),
        decoded_png_count=len(pngs),
        all_pngs_decoded=True,
    )

    _gate(
        validation.get("status") == "pass"
        and validation.get("fail_count") == 0
        and validation.get("not_run_count") == 0
        and validation.get("resolved_scene_sha256")
        == replay_input.environment_package.resolved_scene_sha256
        and evidence.get("resolved_scene_sha256")
        == replay_input.environment_package.resolved_scene_sha256
        and evidence.get("simulation_step_count") == _RUNTIME_CONFIG.settle_steps
        and evidence.get("contact_window_steps") == _RUNTIME_CONFIG.contact_window_steps
        and evidence.get("video_frame_count") == _RUNTIME_CONFIG.video_frames
        and evidence.get("unique_video_frame_count") == media.source_unique_frame_count,
        "physics_evidence_invalid",
        "runtime physics validation is not a complete fixed-case pass",
    )
    physics = ReplayPhysicsValidationRunClaim(
        runtime_evidence_sha256=evidence_ref.sha256,
        validation_report_sha256=validation_ref.sha256,
        status="pass",
        fail_count=0,
        not_run_count=0,
        resolved_scene_sha256=replay_input.environment_package.resolved_scene_sha256,
        settle_steps=_RUNTIME_CONFIG.settle_steps,
        contact_window_steps=_RUNTIME_CONFIG.contact_window_steps,
        video_frame_count=_RUNTIME_CONFIG.video_frames,
        unique_video_frame_count=media.source_unique_frame_count,
    )
    media_identity = assembly.wiring.handler.media_verifier.identity
    verifier_identity_sha256 = media_identity.sha256
    sandbox_identity = media_identity.sandbox_identity
    handler_configuration = _object(
        receipt.get("handler_configuration"),
        label="receipt handler configuration",
    )
    media_identity_record = _object(
        handler_configuration.get("media_verifier"),
        label="receipt media verifier identity",
    )
    _gate(
        media_verification.get("verifier_identity_sha256") == verifier_identity_sha256
        and media_identity_record.get("sha256") == verifier_identity_sha256,
        "media_identity_mismatch",
        "receipt and configured media verifier identities disagree",
    )
    return _InspectedReplay(
        lifecycle=lifecycle,
        media=media,
        physics=physics,
        execution={
            "status": "succeeded",
            "attempt_count": run.state.attempt,
            "handler_call_count": run.handler_call_count,
            "execution_receipt_sha256": receipt_ref.sha256,
            "output_sha256": _canonical_sha256(run.output.model_dump(mode="json")),
            "runtime_evidence_sha256": evidence_ref.sha256,
            "validation_report_sha256": validation_ref.sha256,
            "event_transcript_sha256": transcript_ref.sha256,
            "runtime_asset_manifest_sha256": runtime_asset_ref.sha256,
            "video_sha256": video_ref.sha256,
            "environment_package_id": replay_input.environment_package.package_id,
            "dependencies": run.dependencies,
            "cas_reread_verified": True,
            "all_artifacts_verified": True,
        },
        runtime_asset_manifest_sha256=runtime_asset_ref.sha256,
        runtime_asset_member_count=len(member_refs),
        verifier_identity_sha256=verifier_identity_sha256,
        ffmpeg_sha256=sandbox_identity.ffmpeg_sha256,
        launcher_sha256=sandbox_identity.launcher_sha256,
    )


def _verify_compile_artifacts(
    store: LocalArtifactStore,
    output: Text2EnvCompileOutput,
) -> None:
    refs = (
        output.scene_spec,
        output.resolved_scene,
        output.environment_package.asset_catalog,
        output.environment_package.package_manifest,
        output.static_validation,
    )
    _verified_artifacts(store, refs)


def _verified_artifacts(
    store: LocalArtifactStore,
    artifacts: tuple[ArtifactRef, ...],
) -> tuple[ArtifactRef, ...]:
    unique: list[ArtifactRef] = []
    identities: set[tuple[str, str | None, str]] = set()
    for artifact in artifacts:
        store.resolve(artifact)
        identity = (artifact.media_type, artifact.schema_version, artifact.sha256)
        if identity not in identities:
            identities.add(identity)
            unique.append(artifact)
    return tuple(unique)


def _artifact_refs(value: Any) -> tuple[ArtifactRef, ...]:
    if isinstance(value, ArtifactRef):
        return (value,)
    if isinstance(value, BaseModel):
        return tuple(
            artifact
            for name in value.__class__.model_fields
            for artifact in _artifact_refs(getattr(value, name))
        )
    if isinstance(value, Mapping):
        return tuple(artifact for nested in value.values() for artifact in _artifact_refs(nested))
    if isinstance(value, (tuple, list)):
        return tuple(artifact for nested in value for artifact in _artifact_refs(nested))
    return ()


def _one_artifact(
    artifacts: tuple[ArtifactRef, ...],
    *,
    schema_version: str,
    label: str,
) -> ArtifactRef:
    matches = tuple(item for item in artifacts if item.schema_version == schema_version)
    _gate(len(matches) == 1, "artifact_set_invalid", f"expected exactly one {label}")
    return matches[0]


def _one_named_artifact(
    artifacts: tuple[ArtifactRef, ...],
    *,
    name: str,
    label: str,
) -> ArtifactRef:
    matches = tuple(item for item in artifacts if item.name == name)
    _gate(len(matches) == 1, "artifact_set_invalid", f"expected exactly one {label}")
    return matches[0]


def _artifact_records(
    records: list[Any],
    artifacts: tuple[ArtifactRef, ...],
    *,
    label: str,
) -> tuple[dict[str, Any], ...]:
    values: list[dict[str, Any]] = []
    for raw in records:
        record = _object(raw, label=label)
        matches = tuple(
            artifact
            for artifact in artifacts
            if artifact.name == record.get("name")
            and artifact.sha256 == record.get("sha256")
            and artifact.bytes == record.get("bytes")
            and artifact.media_type == record.get("media_type")
            and artifact.schema_version == record.get("schema_version")
        )
        _gate(len(matches) == 1, "artifact_receipt_mismatch", f"{label} is not in CAS output")
        values.append(matches[0].model_dump(mode="json"))
    return tuple(values)


def _read_json_artifact(
    store: LocalArtifactStore,
    artifact: ArtifactRef,
    *,
    label: str,
) -> dict[str, Any]:
    path = store.resolve(artifact).path
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ReplayQualificationGenerationError(
            "evidence_unreadable",
            f"{label} is not strict JSON",
        ) from error
    return _object(value, label=label)


def _object(value: Any, *, label: str) -> dict[str, Any]:
    _gate(isinstance(value, dict), "evidence_shape", f"{label} must be an object")
    return value


def _array(value: Any, *, label: str) -> list[Any]:
    _gate(isinstance(value, list), "evidence_shape", f"{label} must be an array")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _regular_file(value: Path, *, label: str) -> Path:
    if not isinstance(value, Path):
        raise ReplayQualificationGenerationError(
            "operator_locator_invalid",
            f"{label} must be a Path",
        )
    candidate = value.expanduser().absolute()
    try:
        path = value.expanduser().resolve(strict=True)
    except OSError as error:
        raise ReplayQualificationGenerationError(
            "operator_locator_invalid",
            f"{label} is missing or inaccessible",
        ) from error
    if candidate != path or value.is_symlink() or not path.is_file():
        raise ReplayQualificationGenerationError(
            "operator_locator_invalid",
            f"{label} must identify a regular file",
        )
    return path


def _trusted_roots(values: tuple[Path, ...]) -> tuple[Path, ...]:
    _gate(
        type(values) is tuple and bool(values),
        "allowed_asset_roots_invalid",
        "allowed_asset_roots must be a nonempty tuple",
    )
    roots: list[Path] = []
    for value in values:
        if not isinstance(value, Path):
            raise ReplayQualificationGenerationError(
                "allowed_asset_roots_invalid",
                "allowed_asset_roots must contain only Paths",
            )
        candidate = value.expanduser().absolute()
        try:
            root = value.expanduser().resolve(strict=True)
        except OSError as error:
            raise ReplayQualificationGenerationError(
                "allowed_asset_roots_invalid",
                "allowed_asset_roots contains a missing root",
            ) from error
        if candidate != root or value.is_symlink() or not root.is_dir():
            raise ReplayQualificationGenerationError(
                "allowed_asset_roots_invalid",
                "allowed_asset_roots must contain only directories",
            )
        roots.append(root)
    result = tuple(roots)
    if result != tuple(sorted(set(result), key=lambda item: str(item))):
        raise ReplayQualificationGenerationError(
            "allowed_asset_roots_invalid",
            "allowed_asset_roots must be unique and sorted",
        )
    return result


def _load_capability_digest(path: Path) -> str:
    capability_path = _regular_file(path, label="runtime_capability_path")
    try:
        payload = capability_path.read_bytes()
        value = json.loads(
            payload,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        validated = validate_runtime_capability_document(value)
        canonical = canonical_capability_bytes(validated)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ReplayQualificationGenerationError(
            "runtime_capability_invalid",
            "runtime capability locator does not contain a strict valid document",
        ) from error
    _gate(
        payload == canonical,
        "runtime_capability_invalid",
        "runtime capability document must use canonical bytes",
    )
    return hashlib.sha256(canonical).hexdigest()


def _gate(condition: bool, reason: str, message: str) -> None:
    if not condition:
        raise ReplayQualificationGenerationError(reason, message)


class _SequenceClock:
    def __init__(self) -> None:
        self._value = datetime(2026, 8, 31, 4, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self._value
        self._value += timedelta(milliseconds=1)
        return value


def _new_output_root(value: Path, *, label: str) -> Path:
    if not isinstance(value, Path):
        raise ReplayQualificationGenerationError(
            f"invalid_{label}_root",
            f"{label}_root must be a Path",
        )
    root = value.expanduser().absolute()
    if root.exists() or root.is_symlink():
        raise ReplayQualificationGenerationError(
            f"{label}_exists",
            f"qualification {label} root must be new: {root}",
        )
    return root


def _positive_timeout(value: float, *, label: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
        raise ReplayQualificationGenerationError(
            "invalid_timeout",
            f"{label} must be a positive finite number",
        )
    return float(value)


def _snapshot_operator_inputs(
    settings: ReplayQualificationSettings,
    *,
    artifact_store: LocalArtifactStore,
    scratch_root: Path,
    phase: Literal["before_execution", "before_publish"],
) -> tuple[ReplayOperatorInputsManifest, ArtifactRef]:
    file_values = {
        "asset_catalog": _regular_file(settings.asset_catalog_path, label="asset_catalog_path"),
        "interpreter": _regular_file(settings.interpreter, label="interpreter"),
        "media_launcher": _regular_file(settings.media_launcher, label="media_launcher"),
        "runtime_capability": _regular_file(
            settings.runtime_capability_path,
            label="runtime_capability_path",
        ),
        "runtime_runner": _regular_file(settings.runtime_runner, label="runtime_runner"),
        "static_ffmpeg": _regular_file(settings.static_ffmpeg, label="static_ffmpeg"),
    }
    roots = _trusted_roots(settings.allowed_asset_roots)
    runtime_module_root = _operator_directory(
        settings.runtime_module_root,
        label="runtime_module_root",
    )
    delegated_cgroup_root = _operator_directory(
        settings.delegated_cgroup_root,
        label="delegated_cgroup_root",
    )
    catalog_ref = artifact_store.put_file(
        file_values["asset_catalog"],
        name="replay_qualification_input_asset_catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    capability_ref = artifact_store.put_file(
        file_values["runtime_capability"],
        name="replay_qualification_runtime_capability",
        media_type="application/json",
        schema_version=RUNTIME_CAPABILITY_SCHEMA,
    )
    identity_values: list[ReplayOperatorFileIdentity] = []
    for label, path in sorted(file_values.items()):
        size, sha256 = _file_snapshot(path)
        identity_values.append(
            ReplayOperatorFileIdentity(
                label=label,
                path=str(path),
                bytes=size,
                sha256=sha256,
            )
        )
    identities = tuple(identity_values)
    identities_by_label = {item.label: item for item in identities}
    _gate(
        catalog_ref.sha256 == identities_by_label["asset_catalog"].sha256
        and catalog_ref.bytes == identities_by_label["asset_catalog"].bytes
        and capability_ref.sha256 == identities_by_label["runtime_capability"].sha256
        and capability_ref.bytes == identities_by_label["runtime_capability"].bytes,
        "operator_inputs_changed",
        "operator catalog or capability changed while its manifest was captured",
    )
    provisional = ReplayOperatorInputsManifest(
        schema_version="harness.replay_qualification.operator_inputs.v1",
        phase=phase,
        machine_local=True,
        skill_ref=_SKILL_REF,
        case_id=REPLAY_QUALIFICATION_CASE_ID,
        request=REPLAY_QUALIFICATION_REQUEST,
        seed=_SEED,
        runtime_config=_RUNTIME_CONFIG.model_dump(mode="json"),
        task_config=_TASK_CONFIG,
        min_visible_pixels=_MIN_VISIBLE_PIXELS,
        checkpoint_steps=_CHECKPOINT_STEPS,
        allowed_asset_roots=tuple(str(item) for item in roots),
        runtime_module_root=str(runtime_module_root),
        delegated_cgroup_root=str(delegated_cgroup_root),
        files=identities,
        input_asset_catalog=catalog_ref,
        runtime_capability=capability_ref,
        inputs_sha256="0" * 64,
    )
    inputs_sha256 = _canonical_sha256(
        provisional.model_dump(mode="json", exclude={"phase", "inputs_sha256"})
    )
    manifest = provisional.model_copy(update={"inputs_sha256": inputs_sha256})
    ref = _publish_model_artifact(
        artifact_store,
        scratch_root,
        manifest,
        name=f"replay_qualification_operator_{phase}",
        schema_version="harness.replay_qualification.operator_inputs.v1",
    )
    return manifest, ref


def _operator_manifests_match(
    first: ReplayOperatorInputsManifest,
    second: ReplayOperatorInputsManifest,
) -> bool:
    return first.inputs_sha256 == second.inputs_sha256 and first.model_dump(
        mode="json", exclude={"phase"}
    ) == second.model_dump(mode="json", exclude={"phase"})


def _source_snapshots_match(first: _SourceSnapshot, *others: _SourceSnapshot) -> bool:
    return all(
        first.files == item.files
        and first.harness.tree_sha256 == item.harness.tree_sha256
        and first.scene_gen_sha256 == item.scene_gen_sha256
        and first.ledger_sha256 == item.ledger_sha256
        for item in others
    )


def _operator_directory(value: Path, *, label: str) -> Path:
    if not isinstance(value, Path):
        raise ReplayQualificationGenerationError(
            "operator_locator_invalid",
            f"{label} must be a Path",
        )
    candidate = value.expanduser().absolute()
    try:
        root = value.expanduser().resolve(strict=True)
    except OSError as error:
        raise ReplayQualificationGenerationError(
            "operator_locator_invalid",
            f"{label} is missing or inaccessible",
        ) from error
    if candidate != root or value.is_symlink() or not root.is_dir():
        raise ReplayQualificationGenerationError(
            "operator_locator_invalid",
            f"{label} must identify a directory",
        )
    return root


def _publish_model_artifact(
    store: LocalArtifactStore,
    scratch_root: Path,
    value: BaseModel,
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    root = scratch_root / "qualification-evidence-documents"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.json"
    payload = _document_bytes(value)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    ref = store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )
    _gate(
        ref.sha256 == hashlib.sha256(payload).hexdigest() and ref.bytes == len(payload),
        "evidence_publish_mismatch",
        f"published evidence differs from canonical {name} bytes",
    )
    return ref


def _publish_source_manifest(
    store: LocalArtifactStore,
    scratch_root: Path,
    snapshot: _SourceSnapshot,
) -> ArtifactRef:
    return _publish_model_artifact(
        store,
        scratch_root,
        snapshot.harness,
        name=f"replay_qualification_source_{snapshot.harness.phase}",
        schema_version="harness.replay_qualification.harness_source_manifest.v1",
    )


def _publish_run_receipt(
    store: LocalArtifactStore,
    scratch_root: Path,
    run: _CompletedReplay,
    *,
    mode: Literal["candidate_direct", "qualification_candidate"],
) -> ArtifactRef:
    harness_transcript = ReplayHarnessEventTranscript(
        schema_version="harness.replay_qualification.harness_event_transcript.v1",
        mode=mode,
        run_id=run.state.run_id,
        events=run.journal_events,
    )
    harness_ref = _publish_model_artifact(
        store,
        scratch_root,
        harness_transcript,
        name=f"replay_qualification_{mode}_harness_events",
        schema_version="harness.replay_qualification.harness_event_transcript.v1",
    )
    runtime_transcript = _one_artifact(
        run.artifacts,
        schema_version="harness.runtime_event_transcript.v1",
        label=f"{mode} runtime event transcript",
    )
    common = {
        "skill_ref": _SKILL_REF,
        "run_id": run.state.run_id,
        "invocation": run.invocation,
        "run_state": run.state,
        "harness_event_transcript": harness_ref,
        "runtime_event_transcript": runtime_transcript,
        "handler_call_count": run.handler_call_count,
        "runtime_call_count": 1,
        "typed_output": run.output,
        "exact_dependencies": run.dependencies,
        "artifact_closure": run.artifacts,
    }
    if mode == "candidate_direct":
        receipt: BaseModel = ReplayDirectSupervisorReceipt(
            schema_version="harness.replay_qualification.direct_supervisor_receipt.v1",
            mode=mode,
            **common,
        )
        schema_version = "harness.replay_qualification.direct_supervisor_receipt.v1"
        name = "replay_qualification_candidate_direct_supervisor_receipt"
    else:
        receipt = ReplayKernelEvaluationReceipt(
            schema_version="harness.replay_qualification.kernel_evaluation_receipt.v1",
            mode=mode,
            **common,
        )
        schema_version = "harness.replay_qualification.kernel_evaluation_receipt.v1"
        name = "replay_qualification_production_kernel_evaluation_receipt"
    return _publish_model_artifact(
        store,
        scratch_root,
        receipt,
        name=name,
        schema_version=schema_version,
    )


def _snapshot_sources(
    settings: ReplayQualificationSettings,
    *,
    phase: Literal["before_execution", "after_execution", "before_publish"],
) -> _SourceSnapshot:
    try:
        distribution_root = _require_actual_distribution_root(settings.distribution_root)
        harness = _snapshot_harness_source(distribution_root, phase=phase)
    except (AttributeError, OSError, ReplayQualificationError) as error:
        raise ReplayQualificationGenerationError(
            "implementation_root_invalid",
            "distribution_root must be the repository containing qualify_replay.py",
        ) from error
    return _SourceSnapshot(
        harness=harness,
        scene_gen_sha256=_source_tree_sha256(settings.scene_gen_root, label="scene_gen"),
        ledger_sha256=_source_tree_sha256(
            settings.ledger_contract_root,
            label="ledger contract",
        ),
    )


def _build_manifest(snapshot: _SourceSnapshot) -> ImplementationManifestV1:
    provisional = ImplementationManifestV1(
        schema_version=IMPLEMENTATION_MANIFEST_SCHEMA_ID,
        skill_ref=_SKILL_REF,
        files=snapshot.files,
        bundle_sha256="0" * 64,
        scene_gen_tree_sha256=snapshot.scene_gen_sha256,
        ledger_contract_tree_sha256=snapshot.ledger_sha256,
    )
    return provisional.model_copy(
        update={"bundle_sha256": _implementation_bundle_sha256(provisional)}
    )


def _document_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_bundle_atomically(bundle_root: Path, documents: dict[str, bytes]) -> None:
    bundle_root.parent.mkdir(parents=True, exist_ok=True)
    if bundle_root.exists() or bundle_root.is_symlink():
        raise FileExistsError(f"qualification bundle already exists: {bundle_root}")
    staging = Path(tempfile.mkdtemp(prefix=f".{bundle_root.name}.", dir=bundle_root.parent))
    published = False
    try:
        for name in sorted(documents):
            with (staging / name).open("xb") as stream:
                stream.write(documents[name])
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(staging, bundle_root)
        published = True
        directory_fd = os.open(bundle_root.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        shutil.rmtree(bundle_root if published else staging, ignore_errors=True)
        raise


__all__ = [
    "ReplayQualificationGenerationError",
    "ReplayQualificationResult",
    "ReplayQualificationSettings",
    "generate_replay_qualification",
    "main",
]


if __name__ == "__main__":  # pragma: no cover - exercised through the module CLI
    raise SystemExit(main())
