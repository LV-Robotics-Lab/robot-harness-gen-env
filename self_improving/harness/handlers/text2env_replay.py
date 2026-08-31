"""Replay Adapter that binds one simulator execution to immutable evidence.

Selected loader trees are snapshotted into CAS before execution and materialized as
an attempt-local read-only tree.  Runtime JSON and media enter CAS as untrusted bytes;
they receive public schema/media types only after lifecycle, hash, and full decode
verification.  Replay success means evidence acquisition succeeded.  Physical gate
failure remains a valid replay result for ``text2env.validate`` to consume.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.schema import ResolvedSceneSpec, SceneSpec

from ..artifacts import ArtifactResolutionError, LocalArtifactStore
from ..media_verifier import (
    MediaVerificationError,
    MediaVerificationReason,
    ReplayMediaVerification,
    ReplayMediaVerifier,
)
from ..package_store import PackageStore, PackageStoreError
from ..registry import DependencyResolutionError, HandlerResult, RunContext, SkillBlocked
from ..replay_dependencies import (
    REPLAY_HANDLER_CONFIG_DEPENDENCY,
    REPLAY_RUNTIME_ASSET_DEPENDENCY,
    Text2EnvReplayDependencyResolver,
    replay_dependency_set,
    require_replay_dependencies,
)
from ..runtime_assets import RuntimeAssetSnapshot, RuntimeAssetSnapshotError, RuntimeAssetStore
from ..runtime_capability import (
    RUNTIME_ARTIFACT_PATHS,
    RUNTIME_CAPABILITY_SCHEMA,
    RUNTIME_EVIDENCE_ARTIFACT_PATHS,
    RUNTIME_MEDIA_ARTIFACT_PATHS,
    canonical_capability_bytes,
)
from ..runtime_events import RuntimeEvent, RuntimeEventCodec, RuntimeEventKind
from ..runtime_executor import (
    RuntimeExecution,
    RuntimeExecutionStatus,
    RuntimeExecutor,
    RuntimeFailureCode,
    RuntimeJob,
    RuntimeOutputFile,
    RuntimeProbeDiagnostics,
)
from ..schemas import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    EnvironmentPackage,
    ExecutionReproducibility,
    RuntimeConfig,
    SkillDescriptorV2,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)

_RUNTIME_EVIDENCE_SCHEMA = "robotwin.scene_runtime_evidence.v2"
_VALIDATION_REPORT_SCHEMA = "robotwin.scene_validation.v1"
_REPLAY_RECEIPT_SCHEMA = "harness.text2env_replay_receipt.v1"
_TRANSCRIPT_SCHEMA = "harness.runtime_event_transcript.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RETRYABLE_EXECUTION_FAILURES = frozenset(
    {
        RuntimeFailureCode.CAPABILITY_TIMEOUT,
        RuntimeFailureCode.INCOMPLETE_TRANSCRIPT,
        RuntimeFailureCode.WORKER_TIMEOUT,
        RuntimeFailureCode.WORKER_CRASH,
    }
)
_INTERNAL_EXECUTION_FAILURES = frozenset(
    {
        RuntimeFailureCode.CAPABILITY_PROTOCOL_ERROR,
        RuntimeFailureCode.EVENT_PROTOCOL_ERROR,
        RuntimeFailureCode.STREAM_LIMIT_EXCEEDED,
        RuntimeFailureCode.OBSERVER_FAILED,
        RuntimeFailureCode.OUTPUT_SECURITY_ERROR,
    }
)
_DEPENDENCY_EXECUTION_FAILURES = frozenset(
    {
        RuntimeFailureCode.CAPABILITY_MISMATCH,
        RuntimeFailureCode.CAPABILITY_PROCESS_FAILED,
        RuntimeFailureCode.CAPABILITY_DRIFT,
        RuntimeFailureCode.RUNTIME_PREFLIGHT_FAILED,
        RuntimeFailureCode.RUNTIME_ASSET_DRIFT,
    }
)
_MEDIA_DEPENDENCY_FAILURES = frozenset(
    {
        MediaVerificationReason.CONFIGURATION_INVALID,
        MediaVerificationReason.BINARY_INVALID,
        MediaVerificationReason.BINARY_DRIFT,
        MediaVerificationReason.DEPENDENCY_INVALID,
        MediaVerificationReason.DEPENDENCY_DRIFT,
        MediaVerificationReason.REQUEST_INVALID,
        MediaVerificationReason.SANDBOX_UNAVAILABLE,
    }
)
_PACKAGE_RUNTIME_ASSET_REASONS = frozenset(
    {
        "catalog_entry_missing",
        "catalog_model_missing",
        "catalog_model_unusable",
        "catalog_entry_unavailable",
        "load_type_mismatch",
        "source_files_empty",
        "source_files_mismatch",
    }
)


class _PackageContentError(ValueError):
    """A materialized compiler package is internally inconsistent."""


class _EvidenceAcquisitionError(ValueError):
    """A completed process did not produce trustworthy replay evidence."""


@dataclass(frozen=True, slots=True)
class _BoundPackage:
    package_root: Path
    scene_spec: SceneSpec
    resolved_scene: ResolvedSceneSpec
    catalog: AssetCatalog
    catalog_path: Path


@dataclass(frozen=True, slots=True)
class _PublishedExecution:
    capability: ArtifactRef
    transcript: ArtifactRef
    stdout: ArtifactRef
    stderr: ArtifactRef
    runtime_evidence: ArtifactRef
    runtime_validation_report: ArtifactRef
    media: tuple[tuple[str, ArtifactRef], ...]
    diagnostic_extras: tuple[ArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class _PublishedDiagnostics:
    capability: ArtifactRef | None
    transcript: ArtifactRef
    stdout: ArtifactRef
    stderr: ArtifactRef
    probe_artifacts: tuple[ArtifactRef, ...]
    partial_outputs: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True)
class Text2EnvReplayHandler:
    """Materialize, execute exactly once, and publish only bound replay artifacts."""

    artifact_store: LocalArtifactStore
    package_store: PackageStore
    runtime_executor: RuntimeExecutor
    work_root: Path
    allowed_asset_roots: tuple[Path, ...]
    expected_capability_sha256: str
    media_verifier: ReplayMediaVerifier
    expected_handler_config_sha256: str
    expected_runtime_asset_snapshot_sha256: str | None = None
    dependency_resolver: Text2EnvReplayDependencyResolver | None = None
    task_config: str = "demo_clean"
    min_visible_pixels: int = 64
    checkpoint_steps: int = 120

    def __post_init__(self) -> None:
        if not self.allowed_asset_roots:
            raise ValueError("allowed_asset_roots must not be empty")
        if _SHA256.fullmatch(self.expected_capability_sha256) is None:
            raise ValueError("expected_capability_sha256 must be 64 lowercase hex characters")
        if not self.task_config or any(character in self.task_config for character in "/\\"):
            raise ValueError("task_config must be one portable name")
        if type(self.min_visible_pixels) is not int or self.min_visible_pixels < 0:
            raise ValueError("min_visible_pixels must be a nonnegative integer")
        if type(self.checkpoint_steps) is not int or self.checkpoint_steps < 1:
            raise ValueError("checkpoint_steps must be a positive integer")
        if _SHA256.fullmatch(self.expected_handler_config_sha256) is None:
            raise ValueError("expected_handler_config_sha256 must be 64 lowercase hex characters")
        if (
            self.expected_runtime_asset_snapshot_sha256 is not None
            and _SHA256.fullmatch(self.expected_runtime_asset_snapshot_sha256) is None
        ):
            raise ValueError(
                "expected_runtime_asset_snapshot_sha256 must be 64 lowercase hex characters"
            )
        actual = text2env_replay_handler_config_sha256(
            allowed_asset_roots=self.allowed_asset_roots,
            expected_capability_sha256=self.expected_capability_sha256,
            media_verifier_identity=self.media_verifier.identity,
            task_config=self.task_config,
            min_visible_pixels=self.min_visible_pixels,
            checkpoint_steps=self.checkpoint_steps,
            expected_runtime_asset_snapshot_sha256=(self.expected_runtime_asset_snapshot_sha256),
        )
        if actual != self.expected_handler_config_sha256:
            raise ValueError("expected_handler_config_sha256 does not bind handler configuration")

    @property
    def dependency_identity(self) -> DependencyRef:
        """Return the exact dependency that a registry resolver must bind."""

        return DependencyRef(
            name=REPLAY_HANDLER_CONFIG_DEPENDENCY,
            version="1",
            sha256=self.expected_handler_config_sha256,
        )

    def __call__(
        self,
        value: Text2EnvReplayInput,
        context: RunContext,
    ) -> HandlerResult:
        self._require_configuration_unchanged()
        attempt_root = self._create_attempt_root(context)
        package_root = self._materialize_package(value.environment_package, attempt_root)
        bound = self._bind_package(value.environment_package, package_root)
        try:
            robotwin_root = _selected_robotwin_root(
                bound.catalog,
                self.allowed_asset_roots,
            )
            asset_store = RuntimeAssetStore(self.artifact_store)
            asset_snapshot = asset_store.snapshot(
                resolved=bound.resolved_scene,
                catalog=bound.catalog,
                allowed_roots=self.allowed_asset_roots,
            )
        except RuntimeAssetSnapshotError as error:
            if error.reason in _PACKAGE_RUNTIME_ASSET_REASONS:
                raise _package_blocker(
                    "package_binding",
                    "resolved scene selection is inconsistent with its bound catalog",
                    details={"reason": error.reason},
                ) from error
            raise _dependency_blocker(
                "runtime_assets",
                "selected runtime assets could not be snapshotted immutably",
                details={"reason": error.reason, "error": str(error)},
            ) from error
        except (OSError, ValueError) as error:
            reason = getattr(error, "reason", type(error).__name__)
            raise _dependency_blocker(
                "runtime_assets",
                "selected runtime assets could not be snapshotted immutably",
                details={"reason": str(reason), "error": str(error)},
            ) from error
        asset_artifacts = _runtime_asset_artifacts(asset_snapshot)
        context.emit("replay.runtime_assets.snapshotted", artifact_refs=asset_artifacts)
        invocation_dependencies = self._require_invocation_dependencies(
            context.dependencies,
            asset_snapshot=asset_snapshot,
            artifact_refs=asset_artifacts,
        )
        if (
            self.expected_runtime_asset_snapshot_sha256 is not None
            and asset_snapshot.manifest.sha256 != self.expected_runtime_asset_snapshot_sha256
        ):
            raise _dependency_blocker(
                "runtime_assets",
                "runtime asset snapshot differs from the invocation dependency",
                details={
                    "expected_sha256": self.expected_runtime_asset_snapshot_sha256,
                    "actual_sha256": asset_snapshot.manifest.sha256,
                },
                artifact_refs=asset_artifacts,
            )
        try:
            materialized_assets = asset_store.materialize(
                asset_snapshot.manifest,
                attempt_root / "runtime-assets",
            )
        except (OSError, RuntimeAssetSnapshotError, ValueError) as error:
            reason = getattr(error, "reason", type(error).__name__)
            raise _dependency_blocker(
                "runtime_assets",
                "runtime asset snapshot could not be materialized",
                details={"reason": str(reason), "error": str(error)},
                artifact_refs=asset_artifacts,
            ) from error

        observed_events: list[RuntimeEvent] = []

        def observe(event: RuntimeEvent) -> None:
            observed_events.append(event)
            context.emit(_progress_stage(event))

        job = RuntimeJob(
            run_id=str(context.run_id),
            attempt=context.attempt,
            robotwin_root=robotwin_root,
            resolved_scene=bound.package_root / "resolved_scene.json",
            asset_catalog=bound.catalog_path,
            expected_capability_sha256=self.expected_capability_sha256,
            runtime_asset_root=materialized_assets.root,
            runtime_asset_manifest=materialized_assets.manifest_path,
            expected_runtime_asset_snapshot_sha256=materialized_assets.manifest_sha256,
            task_config=self.task_config,
            precheck_steps=value.runtime_config.precheck_steps,
            settle_steps=value.runtime_config.settle_steps,
            contact_window_steps=value.runtime_config.contact_window_steps,
            video_frames=value.runtime_config.video_frames,
            fps=value.runtime_config.fps,
            min_visible_pixels=self.min_visible_pixels,
            checkpoint_steps=self.checkpoint_steps,
        )
        execution = self.runtime_executor.execute(job, observe)
        diagnostics = self._publish_diagnostics(execution, attempt_root)
        execution_failed = (
            execution.status is not RuntimeExecutionStatus.SUCCEEDED
            or execution.failure is not None
            or bool(execution.secondary_failures)
        )
        if execution_failed:
            try:
                diagnostics = replace(
                    diagnostics,
                    partial_outputs=self._publish_partial_output_diagnostics(execution),
                )
            except RuntimeError:
                context.emit(
                    "replay.diagnostics.published",
                    artifact_refs=_diagnostic_refs(diagnostics),
                )
                raise
        diagnostic_refs = _diagnostic_refs(diagnostics)
        context.emit("replay.diagnostics.published", artifact_refs=diagnostic_refs)
        failure_refs = _unique_refs((*asset_artifacts, *diagnostic_refs))
        self._require_invocation_dependencies(
            context.dependencies,
            asset_snapshot=asset_snapshot,
            artifact_refs=failure_refs,
        )
        _raise_execution_failure(execution, artifact_refs=failure_refs)
        _verify_success_contract(
            execution,
            observed_events=tuple(observed_events),
            expected_capability_sha256=self.expected_capability_sha256,
            runtime_config=value.runtime_config,
            checkpoint_steps=self.checkpoint_steps,
        )
        output_files = _verify_output_files(execution)
        published = self._publish_execution(execution, output_files, diagnostics)
        try:
            evidence = _load_json_artifact(
                self.artifact_store,
                published.runtime_evidence,
                label="runtime evidence",
            )
            validation = _load_json_artifact(
                self.artifact_store,
                published.runtime_validation_report,
                label="runtime validation report",
            )
            _verify_runtime_evidence(
                evidence,
                resolved=bound.resolved_scene,
                config=value.runtime_config,
                events=execution.events,
                runtime_asset_snapshot_sha256=asset_snapshot.manifest.sha256,
            )
            _verify_runtime_validation(validation, resolved=bound.resolved_scene)
            media_verification = self.media_verifier.verify(
                {
                    relative: output_files[relative].path
                    for relative in RUNTIME_MEDIA_ARTIFACT_PATHS
                    if relative in output_files
                },
                expected_video_frames=value.runtime_config.video_frames,
                expected_fps=value.runtime_config.fps,
            )
            _verify_media_verification(
                media_verification,
                configured_identity=self.media_verifier.identity,
                output_files=output_files,
                evidence=evidence,
                config=value.runtime_config,
            )
        except MediaVerificationError as error:
            evidence_refs = _unique_refs((*asset_artifacts, *_published_refs(published)))
            if error.reason in _MEDIA_DEPENDENCY_FAILURES:
                raise _dependency_blocker(
                    "media_verifier",
                    "replay media verifier dependency is unavailable",
                    details={"reason": error.reason.value},
                    artifact_refs=evidence_refs,
                ) from error
            raise _replay_blocker(
                "runtime_media",
                "runtime media failed complete decode verification",
                details={"reason": error.reason.value},
                artifact_refs=evidence_refs,
            ) from error
        except (OSError, ValueError, _EvidenceAcquisitionError) as error:
            evidence_refs = _unique_refs((*asset_artifacts, *_published_refs(published)))
            raise _replay_blocker(
                "runtime_evidence",
                "runtime evidence was incomplete or hash-unbound",
                details={"error_type": type(error).__name__, "error": str(error)},
                artifact_refs=evidence_refs,
            ) from error

        published = self._promote_verified_outputs(published, output_files)

        receipt_ref = self._publish_receipt(
            attempt_root=attempt_root,
            value=value,
            execution=execution,
            published=published,
            evidence=evidence,
            validation=validation,
            asset_snapshot=asset_snapshot,
            media_verification=media_verification,
            invocation_dependencies=invocation_dependencies,
        )
        replay_artifacts = tuple(
            sorted(
                (*_published_typed_refs(published), *asset_artifacts, receipt_ref),
                key=lambda item: (item.name, item.sha256),
            )
        )
        output = Text2EnvReplayOutput(
            runtime_evidence=published.runtime_evidence,
            replay_artifacts=replay_artifacts,
        )
        typed_artifacts = (published.runtime_evidence, *replay_artifacts)
        context.emit("replay.artifacts.published", artifact_refs=typed_artifacts)
        all_artifacts = _unique_refs((*typed_artifacts, *diagnostic_refs, *asset_artifacts))
        return HandlerResult(output=output, artifacts=all_artifacts)

    def _require_configuration_unchanged(self) -> None:
        try:
            actual = text2env_replay_handler_config_sha256(
                allowed_asset_roots=self.allowed_asset_roots,
                expected_capability_sha256=self.expected_capability_sha256,
                media_verifier_identity=self.media_verifier.identity,
                task_config=self.task_config,
                min_visible_pixels=self.min_visible_pixels,
                checkpoint_steps=self.checkpoint_steps,
                expected_runtime_asset_snapshot_sha256=(
                    self.expected_runtime_asset_snapshot_sha256
                ),
            )
        except (OSError, ValueError, MediaVerificationError) as error:
            raise _dependency_blocker(
                "handler_configuration",
                "replay handler dependency identity is unavailable",
                details={"error_type": type(error).__name__},
            ) from error
        if actual != self.expected_handler_config_sha256:
            raise _dependency_blocker(
                "handler_configuration",
                "replay handler dependency identity changed after registration",
                details={
                    "expected_sha256": self.expected_handler_config_sha256,
                    "actual_sha256": actual,
                },
            )

    def _require_invocation_dependencies(
        self,
        declared: tuple[DependencyRef, ...],
        *,
        asset_snapshot: RuntimeAssetSnapshot,
        artifact_refs: tuple[ArtifactRef, ...],
    ) -> tuple[DependencyRef, ...]:
        try:
            expected = replay_dependency_set(
                runtime_executor_identity=self.runtime_executor.identity,
                handler_config_sha256=self.expected_handler_config_sha256,
                expected_capability_sha256=self.expected_capability_sha256,
                runtime_asset_snapshot_sha256=asset_snapshot.manifest.sha256,
                media_verifier_identity=self.media_verifier.identity,
            )
            return require_replay_dependencies(declared, expected)
        except (AttributeError, ValueError, DependencyResolutionError) as error:
            raise _dependency_blocker(
                "invocation_dependencies",
                "replay invocation dependencies are missing or identity-mismatched",
                details={"error_type": type(error).__name__, "error": str(error)},
                artifact_refs=artifact_refs,
            ) from error

    def _create_attempt_root(self, context: RunContext) -> Path:
        root = self.work_root.expanduser().absolute()
        if root.exists():
            if root.is_symlink() or not root.is_dir():
                raise RuntimeError("replay work_root must be a non-symlink directory")
        else:
            root.mkdir(parents=True)
        root = root.resolve(strict=True)
        run_root = root / str(context.run_id)
        if run_root.exists():
            if run_root.is_symlink() or not run_root.is_dir():
                raise RuntimeError("replay run root is unsafe")
        else:
            run_root.mkdir()
        attempt_root = run_root / f"attempt-{context.attempt}"
        try:
            attempt_root.mkdir()
        except FileExistsError as error:
            raise _package_blocker(
                "package_materialization",
                "replay attempt directory already exists",
            ) from error
        return attempt_root

    def _materialize_package(
        self,
        package: EnvironmentPackage,
        attempt_root: Path,
    ) -> Path:
        try:
            return self.package_store.materialize(
                package.package_manifest,
                attempt_root / "package",
            )
        except PackageStoreError as error:
            raise _package_blocker(
                "package_materialization",
                "environment package could not be reconstructed",
                details={"reason": error.reason, "error": str(error)},
            ) from error

    def _bind_package(
        self,
        package: EnvironmentPackage,
        package_root: Path,
    ) -> _BoundPackage:
        try:
            scene_spec = SceneSpec.model_validate_json(
                (package_root / "scene_spec.json").read_text(encoding="utf-8")
            )
            resolved_scene = ResolvedSceneSpec.model_validate_json(
                (package_root / "resolved_scene.json").read_text(encoding="utf-8")
            )
            manifest = _strict_json_object(package_root / "package_manifest.json")
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
            raise _package_blocker(
                "package_binding",
                "environment package content is invalid",
                details={"error_type": type(error).__name__, "error": str(error)},
            ) from error
        try:
            catalog_path = self.artifact_store.resolve(package.asset_catalog).path
            catalog = load_catalog(catalog_path)
        except (ArtifactResolutionError, OSError, ValueError, ValidationError) as error:
            raise _dependency_blocker(
                "catalog",
                "asset catalog is unavailable or invalid",
                details={"error_type": type(error).__name__, "error": str(error)},
            ) from error
        catalog_problems = _catalog_binding_problems(
            package,
            resolved_scene=resolved_scene,
            catalog=catalog,
            manifest=manifest,
        )
        if catalog_problems:
            raise _dependency_blocker(
                "catalog_binding",
                "resolved package and asset catalog identities disagree",
                details={"problems": catalog_problems},
            )
        problems = _binding_problems(
            package,
            scene_spec=scene_spec,
            resolved_scene=resolved_scene,
            catalog=catalog,
            manifest=manifest,
        )
        if problems:
            raise _package_blocker(
                "package_binding",
                "environment package failed semantic digest binding",
                details={"problems": problems},
            )
        return _BoundPackage(
            package_root=package_root,
            scene_spec=scene_spec,
            resolved_scene=resolved_scene,
            catalog=catalog,
            catalog_path=catalog_path,
        )

    def _publish_execution(
        self,
        execution: RuntimeExecution,
        output_files: Mapping[str, RuntimeOutputFile],
        diagnostics: _PublishedDiagnostics,
    ) -> _PublishedExecution:
        assert diagnostics.capability is not None
        output_refs: dict[str, ArtifactRef] = {}
        for relative, output_file in sorted(output_files.items()):
            artifact = self.artifact_store.put_file(
                output_file.path,
                name=f"untrusted_{Path(relative).stem}",
                media_type="application/octet-stream",
                schema_version=None,
            )
            if artifact.sha256 != output_file.sha256 or artifact.bytes != output_file.bytes:
                raise RuntimeError("runtime output changed while entering CAS")
            output_refs[relative] = artifact
        media = tuple(
            (relative, output_refs[relative])
            for relative in sorted(output_refs)
            if relative in RUNTIME_MEDIA_ARTIFACT_PATHS
        )
        return _PublishedExecution(
            capability=diagnostics.capability,
            transcript=diagnostics.transcript,
            stdout=diagnostics.stdout,
            stderr=diagnostics.stderr,
            runtime_evidence=output_refs["runtime_evidence.json"],
            runtime_validation_report=output_refs["runtime_validation_report.json"],
            media=media,
            diagnostic_extras=diagnostics.probe_artifacts,
        )

    def _publish_partial_output_diagnostics(
        self,
        execution: RuntimeExecution,
    ) -> tuple[ArtifactRef, ...]:
        output_files = _verify_partial_output_files(execution)
        artifacts: list[ArtifactRef] = []
        for relative, output in sorted(output_files.items()):
            artifact = self.artifact_store.put_file(
                output.path,
                name=f"untrusted_{Path(relative).stem}",
                media_type="application/octet-stream",
                schema_version=None,
            )
            if artifact.sha256 != output.sha256 or artifact.bytes != output.bytes:
                raise RuntimeError("partial runtime output changed while entering CAS")
            artifacts.append(artifact)
        return tuple(artifacts)

    def _promote_verified_outputs(
        self,
        published: _PublishedExecution,
        output_files: Mapping[str, RuntimeOutputFile],
    ) -> _PublishedExecution:
        promoted: dict[str, ArtifactRef] = {}
        for relative in sorted(output_files):
            output = output_files[relative]
            media_type, schema_version = _runtime_artifact_type(relative)
            artifact = self.artifact_store.put_file(
                output.path,
                name=Path(relative).stem,
                media_type=media_type,
                schema_version=schema_version,
            )
            if artifact.sha256 != output.sha256 or artifact.bytes != output.bytes:
                raise RuntimeError("verified runtime output changed while entering typed CAS")
            promoted[relative] = artifact
        return replace(
            published,
            runtime_evidence=promoted["runtime_evidence.json"],
            runtime_validation_report=promoted["runtime_validation_report.json"],
            media=tuple(
                (relative, promoted[relative])
                for relative in sorted(promoted)
                if relative in RUNTIME_MEDIA_ARTIFACT_PATHS
            ),
        )

    def _publish_diagnostics(
        self,
        execution: RuntimeExecution,
        attempt_root: Path,
    ) -> _PublishedDiagnostics:
        records_root = attempt_root / "records"
        records_root.mkdir()
        capability = None
        if execution.capability is not None:
            capability = _put_bytes(
                self.artifact_store,
                records_root / "runtime_capability.json",
                canonical_capability_bytes(_plain_json(execution.capability.document)),
                name="runtime_capability",
                media_type="application/json",
                schema_version=RUNTIME_CAPABILITY_SCHEMA,
            )
        transcript = _put_bytes(
            self.artifact_store,
            records_root / "runtime_events.jsonl",
            execution.transcript,
            name="runtime_event_transcript",
            media_type="application/x-ndjson",
            schema_version=_TRANSCRIPT_SCHEMA,
        )
        stdout = _put_bytes(
            self.artifact_store,
            records_root / "stdout.bin",
            execution.stdout,
            name="runtime_stdout",
            media_type="application/octet-stream",
            schema_version=None,
        )
        stderr = _put_bytes(
            self.artifact_store,
            records_root / "stderr.bin",
            execution.stderr,
            name="runtime_stderr",
            media_type="application/octet-stream",
            schema_version=None,
        )
        probes: list[ArtifactRef] = []
        for phase, probe in (
            ("preflight", execution.preflight_probe),
            ("postflight", execution.postflight_probe),
        ):
            if probe is not None:
                probes.extend(
                    self._publish_probe_diagnostics(
                        phase=phase,
                        probe=probe,
                        records_root=records_root,
                    )
                )
        execution_snapshot = {
            "schema_version": "harness.runtime_execution_diagnostics.v1",
            "status": execution.status.value,
            "failure": _failure_record(execution.failure),
            "secondary_failures": [
                _failure_record(failure) for failure in execution.secondary_failures
            ],
            "exit_code": execution.exit_code,
            "capability_sha256": (capability.sha256 if capability is not None else None),
            "postflight_capability_sha256": execution.postflight_capability_sha256,
            "transcript": {
                **_identity(transcript),
                "complete": execution.transcript_complete,
                "truncated": execution.transcript_truncated,
            },
            "stdout": {**_identity(stdout), "truncated": execution.stdout_truncated},
            "stderr": {**_identity(stderr), "truncated": execution.stderr_truncated},
            "probe_artifacts": [
                {"name": artifact.name, **_identity(artifact)} for artifact in probes
            ],
        }
        probes.append(
            _put_bytes(
                self.artifact_store,
                records_root / "runtime_execution_diagnostics.json",
                _canonical_json_bytes(execution_snapshot),
                name="runtime_execution_diagnostics",
                media_type="application/json",
                schema_version="harness.runtime_execution_diagnostics.v1",
            )
        )
        return _PublishedDiagnostics(
            capability=capability,
            transcript=transcript,
            stdout=stdout,
            stderr=stderr,
            probe_artifacts=tuple(probes),
        )

    def _publish_probe_diagnostics(
        self,
        *,
        phase: str,
        probe: RuntimeProbeDiagnostics,
        records_root: Path,
    ) -> tuple[ArtifactRef, ...]:
        raw = (
            (
                "stdout",
                probe.stdout,
                probe.stdout_truncated,
            ),
            (
                "stderr",
                probe.stderr,
                probe.stderr_truncated,
            ),
            (
                "capability_output",
                probe.capability_output,
                probe.capability_output_truncated,
            ),
        )
        refs: list[ArtifactRef] = []
        stream_records: dict[str, Any] = {}
        for label, payload, truncated in raw:
            artifact = _put_bytes(
                self.artifact_store,
                records_root / f"{phase}_probe_{label}.bin",
                payload,
                name=f"{phase}_probe_{label}",
                media_type="application/octet-stream",
                schema_version=None,
            )
            refs.append(artifact)
            stream_records[label] = {**_identity(artifact), "truncated": truncated}
        snapshot = {
            "schema_version": "harness.runtime_probe_diagnostics.v1",
            "phase": phase,
            "exit_code": probe.exit_code,
            "streams": stream_records,
        }
        snapshot_ref = _put_bytes(
            self.artifact_store,
            records_root / f"{phase}_probe_diagnostics.json",
            _canonical_json_bytes(snapshot),
            name=f"{phase}_probe_diagnostics",
            media_type="application/json",
            schema_version="harness.runtime_probe_diagnostics.v1",
        )
        return (*refs, snapshot_ref)

    def _publish_receipt(
        self,
        *,
        attempt_root: Path,
        value: Text2EnvReplayInput,
        execution: RuntimeExecution,
        published: _PublishedExecution,
        evidence: dict[str, Any],
        validation: dict[str, Any],
        asset_snapshot: RuntimeAssetSnapshot,
        media_verification: ReplayMediaVerification,
        invocation_dependencies: tuple[DependencyRef, ...],
    ) -> ArtifactRef:
        assert execution.capability is not None
        receipt = {
            "schema_version": _REPLAY_RECEIPT_SCHEMA,
            "skill_ref": "text2env.replay@1.0.0",
            "environment_package_id": value.environment_package.package_id,
            "package_manifest_sha256": value.environment_package.package_manifest.sha256,
            "asset_catalog_sha256": value.environment_package.asset_catalog.sha256,
            "resolved_scene_sha256": value.environment_package.resolved_scene_sha256,
            "runtime_config": value.runtime_config.model_dump(mode="json"),
            "invocation_dependencies": [
                item.model_dump(mode="json") for item in invocation_dependencies
            ],
            "handler_configuration": {
                "sha256": self.expected_handler_config_sha256,
                "dependency": self.dependency_identity.model_dump(mode="json"),
                "allowed_asset_roots": _allowed_roots_receipt(self.allowed_asset_roots),
                "expected_capability_sha256": self.expected_capability_sha256,
                "expected_runtime_asset_snapshot_sha256": (
                    self.expected_runtime_asset_snapshot_sha256
                ),
                "min_visible_pixels": self.min_visible_pixels,
                "checkpoint_steps": self.checkpoint_steps,
                "task_config": self.task_config,
                "media_verifier": _media_verifier_identity_record(
                    media_verification.verifier_identity
                ),
                "runtime_executor": _canonical_identity_record(
                    self.runtime_executor.identity,
                    scope="runtime executor supervisor identity",
                ),
            },
            "capability": {
                "sha256": published.capability.sha256,
                "bytes": published.capability.bytes,
                "postflight_sha256": execution.postflight_capability_sha256,
            },
            "transcript": _identity(published.transcript),
            "exit_code": execution.exit_code,
            "runtime_evidence": _identity(published.runtime_evidence),
            "runtime_validation_report": {
                **_identity(published.runtime_validation_report),
                "status": validation["status"],
            },
            "media": [
                {"locator": relative, **_identity(artifact)}
                for relative, artifact in published.media
            ],
            "media_verification": _media_verification_record(
                media_verification,
                source_unique_frame_count=evidence["unique_video_frame_count"],
            ),
            "runtime_assets": {
                "self_contained": True,
                "dependency": text2env_replay_runtime_asset_dependency(asset_snapshot).model_dump(
                    mode="json"
                ),
                "expected_sha256": self.expected_runtime_asset_snapshot_sha256,
                "dependency_enforced": True,
                "configured_snapshot_pin_enforced": (
                    self.expected_runtime_asset_snapshot_sha256 is not None
                ),
                "manifest": _named_identity(asset_snapshot.manifest),
                "members": [
                    _named_identity(member)
                    for member in sorted(
                        asset_snapshot.members,
                        key=lambda item: (item.sha256, item.bytes, item.name),
                    )
                ],
            },
        }
        receipt_path = attempt_root / "records/replay_execution_receipt.json"
        return _put_bytes(
            self.artifact_store,
            receipt_path,
            _canonical_json_bytes(receipt),
            name="replay_execution_receipt",
            media_type="application/json",
            schema_version=_REPLAY_RECEIPT_SCHEMA,
        )


def _binding_problems(
    package: EnvironmentPackage,
    *,
    scene_spec: SceneSpec,
    resolved_scene: ResolvedSceneSpec,
    catalog: AssetCatalog,
    manifest: dict[str, Any],
) -> list[str]:
    expected = {
        "environment_package.scene_spec_sha256": (
            package.scene_spec_sha256,
            scene_spec.digest(),
        ),
        "environment_package.resolved_scene_sha256": (
            package.resolved_scene_sha256,
            resolved_scene.digest(),
        ),
        "environment_package.package_id": (package.package_id, resolved_scene.digest()),
        "resolved_scene.source_scene_spec_sha256": (
            resolved_scene.source_scene_spec_sha256,
            scene_spec.digest(),
        ),
        "manifest.source_scene_spec_sha256": (
            manifest.get("source_scene_spec_sha256"),
            scene_spec.digest(),
        ),
        "manifest.resolved_scene_sha256": (
            manifest.get("resolved_scene_sha256"),
            resolved_scene.digest(),
        ),
        "environment_package.seed": (package.seed, scene_spec.seed),
        "resolved_scene.seed": (resolved_scene.seed, scene_spec.seed),
        "manifest.seed": (manifest.get("seed"), scene_spec.seed),
        "resolved_scene.scene_id": (resolved_scene.scene_id, scene_spec.scene_id),
        "manifest.scene_id": (manifest.get("scene_id"), scene_spec.scene_id),
    }
    return [name for name, (actual, wanted) in expected.items() if actual != wanted]


def _catalog_binding_problems(
    package: EnvironmentPackage,
    *,
    resolved_scene: ResolvedSceneSpec,
    catalog: AssetCatalog,
    manifest: Mapping[str, Any],
) -> list[str]:
    expected = {
        "environment_package.asset_catalog.sha256": (
            package.asset_catalog.sha256,
            catalog.digest(),
        ),
        "resolved_scene.asset_catalog_sha256": (
            resolved_scene.asset_catalog_sha256,
            catalog.digest(),
        ),
        "manifest.asset_catalog_sha256": (
            manifest.get("asset_catalog_sha256"),
            catalog.digest(),
        ),
    }
    return [name for name, (actual, wanted) in expected.items() if actual != wanted]


def _normalized_allowed_roots(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    if not isinstance(roots, tuple) or not roots:
        raise ValueError("allowed_asset_roots must not be empty")
    normalized: list[Path] = []
    for root in roots:
        if not isinstance(root, Path):
            raise ValueError("allowed_asset_roots must contain Path values")
        candidate = root.expanduser().absolute()
        try:
            mode = candidate.lstat().st_mode
        except OSError as error:
            raise ValueError("allowed asset root is unavailable") from error
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError("allowed asset root must be a real directory")
        if candidate.resolve(strict=True) != candidate:
            raise ValueError("allowed asset root traverses a symlink")
        if candidate in normalized:
            raise ValueError("allowed asset roots must be unique")
        normalized.append(candidate)
    return tuple(sorted(normalized, key=lambda item: item.as_posix()))


def _selected_robotwin_root(catalog: AssetCatalog, roots: tuple[Path, ...]) -> Path:
    normalized = _normalized_allowed_roots(roots)
    candidate = Path(catalog.robotwin_root)
    if not candidate.is_absolute() or candidate != candidate.absolute():
        raise ValueError("catalog robotwin_root is not canonical absolute")
    if not any(candidate == root or candidate.is_relative_to(root) for root in normalized):
        raise ValueError("catalog robotwin_root escapes allowed asset roots")
    try:
        mode = candidate.lstat().st_mode
    except OSError as error:
        raise ValueError("catalog robotwin_root is unavailable") from error
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode) or candidate.resolve(strict=True) != candidate:
        raise ValueError("catalog robotwin_root must be a real directory")
    return candidate


def _allowed_roots_receipt(roots: tuple[Path, ...]) -> dict[str, Any]:
    normalized = _normalized_allowed_roots(roots)
    payload = _canonical_json_bytes([item.as_posix() for item in normalized])
    return {"count": len(normalized), "configuration_sha256": hashlib.sha256(payload).hexdigest()}


def _runtime_asset_artifacts(snapshot: RuntimeAssetSnapshot) -> tuple[ArtifactRef, ...]:
    return _unique_refs((snapshot.manifest, *snapshot.members))


def text2env_replay_runtime_asset_dependency(
    snapshot: RuntimeAssetSnapshot,
) -> DependencyRef:
    """Return the input-specific dependency identity for one loader snapshot."""

    if not isinstance(snapshot, RuntimeAssetSnapshot):
        raise TypeError("snapshot must be a RuntimeAssetSnapshot")
    return DependencyRef(
        name=REPLAY_RUNTIME_ASSET_DEPENDENCY,
        version="1",
        sha256=snapshot.manifest.sha256,
    )


def _canonical_identity_record(identity: Any, *, scope: str) -> dict[str, Any]:
    identity_sha256, document = _media_identity(identity)
    return {"sha256": identity_sha256, "document": document, "scope": scope}


def _media_verifier_identity_record(
    identity: Any,
) -> dict[str, Any]:
    return {
        **_canonical_identity_record(identity, scope="declared media verifier identity"),
        "handler_scope": (
            "The handler binds this declared identity verbatim; qualification must "
            "separately establish that it covers the complete decoder runtime closure."
        ),
    }


def _media_verification_record(
    verification: ReplayMediaVerification,
    *,
    source_unique_frame_count: int,
) -> dict[str, Any]:
    video = verification.video
    return {
        "verifier_identity_sha256": verification.verifier_identity.sha256,
        "pngs": [
            {
                "locator": item.relative_path,
                "sha256": item.sha256,
                "bytes": item.bytes,
                "width": item.width,
                "height": item.height,
                "mode": item.mode,
                "format": item.format,
                "sandbox_metrics": _sandbox_metrics_record(item.sandbox_metrics),
            }
            for item in verification.pngs
        ],
        "video": (
            None
            if video is None
            else {
                "locator": video.relative_path,
                "sha256": video.sha256,
                "bytes": video.bytes,
                "frame_count": video.frame_count,
                "source_unique_frame_count": source_unique_frame_count,
                "decoded_unique_frame_count": video.unique_frame_count,
                "fps_numerator": video.fps_numerator,
                "fps_denominator": video.fps_denominator,
                "width": video.width,
                "height": video.height,
                "format_name": video.format_name,
                "codec_name": video.codec_name,
                "pixel_format": video.pixel_format,
                "sample_aspect_ratio": video.sample_aspect_ratio,
                "square_sample_aspect_ratio_defaulted": (
                    video.square_sample_aspect_ratio_defaulted
                ),
                "sandbox_metrics": _sandbox_metrics_record(video.sandbox_metrics),
            }
        ),
    }


def _sandbox_metrics_record(metrics: Any) -> dict[str, Any]:
    def records(values: tuple[tuple[str, int], ...]) -> list[dict[str, Any]]:
        return [
            {"name": name, "value": value}
            for name, value in sorted(values, key=lambda item: item[0])
        ]

    return {
        "memory_peak_bytes": metrics.memory_peak_bytes,
        "memory_events": records(metrics.memory_events),
        "pids_peak": metrics.pids_peak,
        "pids_events": records(metrics.pids_events),
        "cpu_stats": records(metrics.cpu_stats),
    }


def _named_identity(artifact: ArtifactRef) -> dict[str, Any]:
    return {"name": artifact.name, **_identity(artifact)}


def text2env_replay_handler_config_sha256(
    *,
    allowed_asset_roots: tuple[Path, ...],
    expected_capability_sha256: str,
    media_verifier_identity: Any,
    task_config: str = "demo_clean",
    min_visible_pixels: int = 64,
    checkpoint_steps: int = 120,
    expected_runtime_asset_snapshot_sha256: str | None = None,
) -> str:
    """Hash every non-public handler input for registry dependency binding."""

    media_identity_sha256, _ = _media_identity(media_verifier_identity)
    roots = _allowed_roots_receipt(allowed_asset_roots)
    document = {
        "schema_version": "harness.text2env_replay_handler_config.v1",
        "allowed_asset_roots": roots,
        "expected_capability_sha256": expected_capability_sha256,
        "media_verifier_identity_sha256": media_identity_sha256,
        "task_config": task_config,
        "min_visible_pixels": min_visible_pixels,
        "checkpoint_steps": checkpoint_steps,
        "expected_runtime_asset_snapshot_sha256": expected_runtime_asset_snapshot_sha256,
    }
    return hashlib.sha256(_canonical_json_bytes(document)).hexdigest()


def _media_identity(identity: Any) -> tuple[str, dict[str, Any]]:
    try:
        canonical_bytes = identity.canonical_bytes
        declared_sha256 = identity.sha256
    except (AttributeError, MediaVerificationError) as error:
        raise ValueError("media verifier identity is unavailable") from error
    if not isinstance(canonical_bytes, bytes):
        raise ValueError("media verifier canonical identity must be bytes")
    if not isinstance(declared_sha256, str) or _SHA256.fullmatch(declared_sha256) is None:
        raise ValueError("media verifier identity sha256 is invalid")
    if hashlib.sha256(canonical_bytes).hexdigest() != declared_sha256:
        raise ValueError("media verifier canonical identity digest is inconsistent")
    try:
        document = json.loads(
            canonical_bytes,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("media verifier canonical identity is invalid JSON") from error
    if not isinstance(document, dict):
        raise ValueError("media verifier identity must be one JSON object")
    return declared_sha256, document


def _raise_execution_failure(
    execution: RuntimeExecution,
    *,
    artifact_refs: tuple[ArtifactRef, ...],
) -> None:
    secondary_failure = next(
        (
            failure
            for failure in execution.secondary_failures
            if not isinstance(failure.code, RuntimeFailureCode)
            or failure.code in _INTERNAL_EXECUTION_FAILURES
            or failure.code in _DEPENDENCY_EXECUTION_FAILURES
        ),
        None,
    )
    if secondary_failure is not None:
        code = secondary_failure.code
        code_value = code.value if isinstance(code, RuntimeFailureCode) else str(code)
        if isinstance(code, RuntimeFailureCode) and code in _DEPENDENCY_EXECUTION_FAILURES:
            raise _dependency_blocker(
                "runtime_preflight",
                "runtime trust dependency failed during execution",
                details={
                    "runtime_failure_code": code.value,
                    "message": secondary_failure.message,
                    "secondary": True,
                },
                artifact_refs=artifact_refs,
            )
        raise RuntimeError(
            "runtime executor returned a secondary trust/protocol failure: "
            f"{code_value}: {secondary_failure.message}"
        )
    if (
        execution.secondary_failures
        and execution.status is RuntimeExecutionStatus.SUCCEEDED
        and execution.failure is None
    ):
        secondary_failure = execution.secondary_failures[0]
        code = secondary_failure.code
        code_value = code.value if isinstance(code, RuntimeFailureCode) else str(code)
        raise RuntimeError(
            "runtime executor returned a secondary failure alongside primary success: "
            f"{code_value}: {secondary_failure.message}"
        )
    if execution.status is RuntimeExecutionStatus.SUCCEEDED and execution.failure is None:
        return
    if execution.status is RuntimeExecutionStatus.SUCCEEDED or execution.failure is None:
        raise RuntimeError("runtime executor returned an inconsistent status and failure")
    code = execution.failure.code
    if code in _INTERNAL_EXECUTION_FAILURES:
        raise RuntimeError(
            f"runtime executor violated its protocol: {code.value}: {execution.failure.message}"
        )
    if code in _DEPENDENCY_EXECUTION_FAILURES:
        raise _dependency_blocker(
            "runtime_preflight",
            "runtime capability or immutable asset identity is unavailable",
            details={
                "runtime_failure_code": code.value,
                "message": execution.failure.message,
            },
            artifact_refs=artifact_refs,
        )
    if code not in _RETRYABLE_EXECUTION_FAILURES:
        raise RuntimeError(f"runtime executor returned an unknown failure taxonomy: {code.value}")
    raise _replay_blocker(
        "runtime_execution",
        "runtime evidence acquisition did not complete",
        details={
            "runtime_failure_code": code.value,
            "message": execution.failure.message,
            "exit_code": execution.exit_code,
        },
        artifact_refs=artifact_refs,
    )


def _verify_success_contract(
    execution: RuntimeExecution,
    *,
    observed_events: tuple[RuntimeEvent, ...],
    expected_capability_sha256: str,
    runtime_config: RuntimeConfig,
    checkpoint_steps: int,
) -> None:
    if execution.exit_code != 0:
        raise RuntimeError("runtime executor reported success with a nonzero exit code")
    if execution.capability is None:
        raise RuntimeError("runtime executor success omitted its capability snapshot")
    capability_payload = canonical_capability_bytes(_plain_json(execution.capability.document))
    if (
        execution.capability.document.get("schema_version") != RUNTIME_CAPABILITY_SCHEMA
        or len(capability_payload) != execution.capability.bytes
        or hashlib.sha256(capability_payload).hexdigest() != execution.capability.sha256
        or execution.capability.sha256 != expected_capability_sha256
        or execution.postflight_capability_sha256 != expected_capability_sha256
    ):
        raise RuntimeError("runtime executor returned an inconsistent capability snapshot")
    if (
        not execution.transcript_complete
        or execution.transcript_truncated
        or execution.stdout_truncated
        or execution.stderr_truncated
    ):
        raise RuntimeError("runtime executor reported success with incomplete bounded streams")
    codec = RuntimeEventCodec(allowed_artifact_paths=RUNTIME_ARTIFACT_PATHS)
    transcript_events = codec.parse_transcript(execution.transcript)
    if transcript_events != execution.events or observed_events != execution.events:
        raise RuntimeError("runtime executor event transcript and live observer disagree")
    _verify_event_lifecycle(
        execution.events,
        runtime_config,
        checkpoint_steps=checkpoint_steps,
    )


def _verify_event_lifecycle(
    events: tuple[RuntimeEvent, ...],
    runtime_config: RuntimeConfig,
    *,
    checkpoint_steps: int,
) -> None:
    if len(events) < 7:
        raise RuntimeError("runtime executor success omitted lifecycle events")
    first = tuple(event.kind for event in events[:3])
    if first != (
        RuntimeEventKind.PREFLIGHT_COMPLETED,
        RuntimeEventKind.SCENE_LOADED,
        RuntimeEventKind.SIMULATION_STARTED,
    ):
        raise RuntimeError("runtime executor lifecycle preamble is invalid")
    completion_index = next(
        (
            index
            for index, event in enumerate(events[3:], start=3)
            if event.kind is RuntimeEventKind.SIMULATION_COMPLETED
        ),
        None,
    )
    if completion_index is None:
        raise RuntimeError("runtime executor omitted simulation.completed")
    checkpoints = events[3:completion_index]
    if any(event.kind is not RuntimeEventKind.SIMULATION_CHECKPOINT for event in checkpoints):
        raise RuntimeError("runtime executor simulation phase contains an invalid event")
    steps = tuple(event.completed_steps for event in checkpoints)
    expected_steps = runtime_config.precheck_steps + max(
        runtime_config.settle_steps,
        runtime_config.video_frames,
    )
    expected_checkpoints = tuple(range(checkpoint_steps, expected_steps + 1, checkpoint_steps))
    if steps != expected_checkpoints:
        raise RuntimeError("runtime checkpoint sequence is incomplete or unexpected")
    completion = events[completion_index]
    if completion.completed_steps != expected_steps:
        raise RuntimeError("runtime simulation steps do not match RuntimeConfig")
    tail = events[completion_index + 1 :]
    if tuple(event.kind for event in tail) != (
        RuntimeEventKind.MEDIA_COMPLETED,
        RuntimeEventKind.EVIDENCE_COMPLETED,
        RuntimeEventKind.WORKER_COMPLETED,
    ):
        raise RuntimeError("runtime executor lifecycle tail is invalid")
    expected_media = (
        RUNTIME_MEDIA_ARTIFACT_PATHS
        if runtime_config.video_frames > 0
        else RUNTIME_MEDIA_ARTIFACT_PATHS[:4]
    )
    if (
        tail[0].artifact_paths != expected_media
        or tail[1].artifact_paths != RUNTIME_EVIDENCE_ARTIFACT_PATHS
    ):
        raise RuntimeError("runtime executor declared unsupported artifact paths")


def _verify_output_files(execution: RuntimeExecution) -> dict[str, RuntimeOutputFile]:
    root = execution.attempt_root.absolute()
    if root.is_symlink() or not root.is_dir() or root.resolve(strict=True) != root:
        raise RuntimeError("runtime executor output root is unsafe")
    declared_paths = {path for event in execution.events for path in event.artifact_paths}
    by_relative: dict[str, RuntimeOutputFile] = {}
    for output in execution.output_files:
        if output.relative_path in by_relative:
            raise RuntimeError("runtime executor returned duplicate output files")
        if output.relative_path not in RUNTIME_ARTIFACT_PATHS:
            raise RuntimeError("runtime executor returned a non-allowlisted output")
        lexical = root / output.relative_path
        if output.path != lexical or lexical.is_symlink() or not lexical.is_file():
            raise RuntimeError("runtime executor output path is unsafe")
        digest, size = _snapshot_output_file(lexical)
        if digest != output.sha256 or size != output.bytes:
            raise RuntimeError("runtime executor output identity is inconsistent")
        by_relative[output.relative_path] = output
    actual_paths: set[str] = set()
    for candidate in root.iterdir():
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError("runtime executor output root contains an unsafe entry")
        actual_paths.add(candidate.name)
    if set(by_relative) != declared_paths or actual_paths != declared_paths:
        raise RuntimeError("runtime output files do not exactly match declared artifacts")
    return by_relative


def _snapshot_output_file(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    size = 0
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise RuntimeError("runtime output is not a regular file")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _verify_partial_output_files(
    execution: RuntimeExecution,
) -> dict[str, RuntimeOutputFile]:
    if not execution.output_files:
        return {}
    root = execution.attempt_root.absolute()
    if root.is_symlink() or not root.is_dir() or root.resolve(strict=True) != root:
        raise RuntimeError("partial runtime output root is unsafe")
    by_relative: dict[str, RuntimeOutputFile] = {}
    for output in execution.output_files:
        if output.relative_path in by_relative:
            raise RuntimeError("runtime executor returned duplicate partial outputs")
        if output.relative_path not in RUNTIME_ARTIFACT_PATHS:
            raise RuntimeError("runtime executor returned a non-allowlisted partial output")
        lexical = root / output.relative_path
        if output.path != lexical or lexical.is_symlink() or not lexical.is_file():
            raise RuntimeError("partial runtime output path is unsafe")
        if (
            _SHA256.fullmatch(output.sha256) is None
            or type(output.bytes) is not int
            or output.bytes < 0
        ):
            raise RuntimeError("partial runtime output identity is invalid")
        digest, size = _snapshot_output_file(lexical)
        if digest != output.sha256 or size != output.bytes:
            raise RuntimeError("partial runtime output identity is inconsistent")
        by_relative[output.relative_path] = output
    return by_relative


def _verify_runtime_evidence(
    evidence: dict[str, Any],
    *,
    resolved: ResolvedSceneSpec,
    config: RuntimeConfig,
    events: tuple[RuntimeEvent, ...],
    runtime_asset_snapshot_sha256: str,
) -> None:
    del events  # lifecycle identity is independently checked before evidence loading
    base_steps = max(config.settle_steps, config.video_frames)
    required_equal = {
        "schema_version": _RUNTIME_EVIDENCE_SCHEMA,
        "scene_id": resolved.scene_id,
        "resolved_scene_sha256": resolved.digest(),
        "seed": resolved.seed,
        "precheck_steps": config.precheck_steps,
        "contact_window_steps": min(config.contact_window_steps, base_steps),
        "base_simulation_step_count": base_steps,
        "settle_converge_max": 0,
        "fps": config.fps,
        "runtime_asset_snapshot_sha256": runtime_asset_snapshot_sha256,
    }
    mismatched = [
        name for name, expected in required_equal.items() if evidence.get(name) != expected
    ]
    if mismatched:
        raise _EvidenceAcquisitionError(
            f"runtime evidence identity/configuration mismatch: {mismatched}"
        )
    if evidence.get("status") != "pass":
        raise _EvidenceAcquisitionError("runtime evidence did not complete with status=pass")
    extra_steps = evidence.get("settle_extra_steps")
    simulation_steps = evidence.get("simulation_step_count")
    total_physics_steps = evidence.get("total_physics_step_count")
    if (
        extra_steps != 0
        or type(simulation_steps) is not int
        or simulation_steps != base_steps
        or type(total_physics_steps) is not int
        or total_physics_steps != config.precheck_steps + base_steps
    ):
        raise _EvidenceAcquisitionError("runtime evidence simulation timeline is inconsistent")
    frame_count = evidence.get("video_frame_count")
    unique_frames = evidence.get("unique_video_frame_count")
    indices = evidence.get("video_sample_step_indices")
    if (
        type(frame_count) is not int
        or frame_count != config.video_frames
        or type(unique_frames) is not int
        or not 0 <= unique_frames <= frame_count
        or not isinstance(indices, list)
        or any(type(index) is not int for index in indices)
        or len(indices) != frame_count
        or any(current >= following for current, following in zip(indices, indices[1:]))
        or any(index < 0 or index >= simulation_steps for index in indices)
        or (bool(indices) and indices[-1] != simulation_steps - 1)
    ):
        raise _EvidenceAcquisitionError("runtime evidence video timeline is inconsistent")
    has_video = config.video_frames > 0
    expected_images = {
        "head": "preview_head.png",
        "world_left": "preview_world_left.png",
        "world_right": "preview_world_right.png",
        "segmentation": "preview_segmentation.png",
        "observer_start": "observer_start.png" if has_video else None,
        "observer_mid": "observer_mid.png" if has_video else None,
        "observer_end": "observer_end.png" if has_video else None,
    }
    expected_video = "observer_runtime.mp4" if has_video else None
    if evidence.get("images") != expected_images or evidence.get("video") != expected_video:
        raise _EvidenceAcquisitionError(
            "runtime evidence media locators are not fixed relative paths"
        )


def _verify_media_verification(
    verification: ReplayMediaVerification,
    *,
    configured_identity: Any,
    output_files: Mapping[str, RuntimeOutputFile],
    evidence: Mapping[str, Any],
    config: RuntimeConfig,
) -> None:
    if not isinstance(verification, ReplayMediaVerification):
        raise _EvidenceAcquisitionError("media verifier returned an invalid success record")
    configured_sha256, configured_document = _media_identity(configured_identity)
    actual_sha256, actual_document = _media_identity(verification.verifier_identity)
    if actual_sha256 != configured_sha256 or actual_document != configured_document:
        raise _EvidenceAcquisitionError("media verifier identity changed during verification")
    expected_pngs = tuple(
        relative
        for relative in RUNTIME_MEDIA_ARTIFACT_PATHS
        if relative.endswith(".png") and relative in output_files
    )
    if tuple(item.relative_path for item in verification.pngs) != expected_pngs:
        raise _EvidenceAcquisitionError("media verifier PNG set is incomplete or unordered")
    for item in verification.pngs:
        output = output_files[item.relative_path]
        if (
            item.sha256 != output.sha256
            or item.bytes != output.bytes
            or type(item.width) is not int
            or type(item.height) is not int
            or item.width < 1
            or item.height < 1
            or item.format != "PNG"
            or not item.mode
        ):
            raise _EvidenceAcquisitionError("media verifier PNG facts are inconsistent")
    video = verification.video
    if config.video_frames == 0:
        if video is not None or evidence.get("video_frame_count") != 0:
            raise _EvidenceAcquisitionError("media verifier unexpectedly returned a video")
        return
    if video is None or video.relative_path != "observer_runtime.mp4":
        raise _EvidenceAcquisitionError("media verifier omitted the runtime video")
    output = output_files.get(video.relative_path)
    if output is None:
        raise _EvidenceAcquisitionError("verified runtime video is absent from executor output")
    if (
        video.sha256 != output.sha256
        or video.bytes != output.bytes
        or video.frame_count != config.video_frames
        or video.frame_count != evidence.get("video_frame_count")
        or type(video.unique_frame_count) is not int
        or not 0 <= video.unique_frame_count <= video.frame_count
        or type(video.fps_numerator) is not int
        or type(video.fps_denominator) is not int
        or video.fps_denominator < 1
        or video.fps_numerator != config.fps * video.fps_denominator
        or type(video.width) is not int
        or type(video.height) is not int
        or video.width < 1
        or video.height < 1
        or video.format_name != "iso-bmff/mp4"
        or video.codec_name != "h264"
        or video.pixel_format != "8bit-420"
        or video.sample_aspect_ratio not in {"0/1", "1/1"}
        or type(video.square_sample_aspect_ratio_defaulted) is not bool
        or video.square_sample_aspect_ratio_defaulted != (video.sample_aspect_ratio == "0/1")
    ):
        raise _EvidenceAcquisitionError("decoded video facts disagree with runtime evidence")
    minimum_unique_frames = min(video.frame_count, 30)
    if video.unique_frame_count < minimum_unique_frames:
        raise _EvidenceAcquisitionError("decoded video unique frames are below the promotion gate")
    observer_dimensions = {
        item.relative_path: (item.width, item.height)
        for item in verification.pngs
        if item.relative_path.startswith("observer_")
    }
    if any(
        observer_dimensions.get(relative) != (video.width, video.height)
        for relative in (
            "observer_start.png",
            "observer_mid.png",
            "observer_end.png",
        )
    ):
        raise _EvidenceAcquisitionError("observer image and video dimensions disagree")


def _verify_runtime_validation(
    report: dict[str, Any],
    *,
    resolved: ResolvedSceneSpec,
) -> None:
    if (
        report.get("schema_version") != _VALIDATION_REPORT_SCHEMA
        or report.get("scene_id") != resolved.scene_id
        or report.get("resolved_scene_sha256") != resolved.digest()
        or report.get("status") not in {"pass", "fail", "incomplete"}
    ):
        raise _EvidenceAcquisitionError("runtime validation report identity is inconsistent")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        raise _EvidenceAcquisitionError("runtime validation report checks are incomplete")
    statuses = [check.get("status") if isinstance(check, dict) else None for check in checks]
    if any(status not in {"pass", "fail", "not_run", "not_applicable"} for status in statuses):
        raise _EvidenceAcquisitionError("runtime validation report contains an invalid check")
    fail_count = report.get("fail_count")
    not_run_count = report.get("not_run_count")
    if (
        type(fail_count) is not int
        or type(not_run_count) is not int
        or fail_count != statuses.count("fail")
        or not_run_count != statuses.count("not_run")
    ):
        raise _EvidenceAcquisitionError("runtime validation report counts are inconsistent")
    expected_status = "fail" if fail_count else "incomplete" if not_run_count else "pass"
    if report["status"] != expected_status:
        raise _EvidenceAcquisitionError("runtime validation report status is inconsistent")


def _progress_stage(event: RuntimeEvent) -> str:
    if event.kind in {
        RuntimeEventKind.SIMULATION_CHECKPOINT,
        RuntimeEventKind.SIMULATION_COMPLETED,
    }:
        return f"replay.{event.kind.value}.steps-{event.completed_steps}"
    return f"replay.{event.kind.value}"


def _runtime_artifact_type(relative: str) -> tuple[str, str | None]:
    if relative == "runtime_evidence.json":
        return "application/json", _RUNTIME_EVIDENCE_SCHEMA
    if relative == "runtime_validation_report.json":
        return "application/json", _VALIDATION_REPORT_SCHEMA
    if relative.endswith(".png"):
        return "image/png", None
    if relative.endswith(".mp4"):
        return "video/mp4", None
    raise RuntimeError(f"runtime output type is not allowlisted: {relative}")


def _load_json_artifact(
    store: LocalArtifactStore,
    artifact: ArtifactRef,
    *,
    label: str,
) -> dict[str, Any]:
    return _strict_json_object(store.resolve(artifact).path, label=label)


def _strict_json_object(path: Path, *, label: str = "JSON document") -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON document has duplicate key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"JSON document has non-standard constant: {value}")


def _put_bytes(
    store: LocalArtifactStore,
    path: Path,
    payload: bytes,
    *,
    name: str,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    artifact = store.put_file(
        path,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )
    expected = hashlib.sha256(payload).hexdigest()
    if artifact.sha256 != expected or artifact.bytes != len(payload):
        raise RuntimeError("CAS changed a replay record's content identity")
    return artifact


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    if isinstance(value, list):
        return [_plain_json(item) for item in value]
    return value


def _identity(artifact: ArtifactRef) -> dict[str, Any]:
    return {
        "sha256": artifact.sha256,
        "bytes": artifact.bytes,
        "media_type": artifact.media_type,
        "schema_version": artifact.schema_version,
    }


def _failure_record(failure: Any) -> dict[str, str] | None:
    if failure is None:
        return None
    return {"code": failure.code.value, "message": failure.message}


def _published_refs(published: _PublishedExecution) -> tuple[ArtifactRef, ...]:
    return (
        published.runtime_evidence,
        published.capability,
        published.transcript,
        published.stdout,
        published.stderr,
        published.runtime_validation_report,
        *published.diagnostic_extras,
        *(artifact for _, artifact in published.media),
    )


def _published_typed_refs(published: _PublishedExecution) -> tuple[ArtifactRef, ...]:
    return (
        published.capability,
        published.transcript,
        published.runtime_validation_report,
        *(artifact for _, artifact in published.media),
    )


def _diagnostic_refs(published: _PublishedDiagnostics) -> tuple[ArtifactRef, ...]:
    capability = (published.capability,) if published.capability is not None else ()
    return (
        *capability,
        published.transcript,
        published.stdout,
        published.stderr,
        *published.probe_artifacts,
        *published.partial_outputs,
    )


def _unique_refs(artifacts: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    unique: list[ArtifactRef] = []
    identities: set[tuple[str, str | None, str]] = set()
    for artifact in artifacts:
        identity = (artifact.media_type, artifact.schema_version, artifact.sha256)
        if identity not in identities:
            identities.add(identity)
            unique.append(artifact)
    return tuple(unique)


def _package_blocker(
    stage: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> SkillBlocked:
    return SkillBlocked(
        Blocker(
            code="T2E_PACKAGE_INVALID",
            message=message,
            stage=stage,
            retryable=False,
            details=details or {},
            unknowns=(),
            artifact_refs=(),
        )
    )


def _dependency_blocker(
    stage: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    artifact_refs: tuple[ArtifactRef, ...] = (),
) -> SkillBlocked:
    return SkillBlocked(
        Blocker(
            code="HARN_DEPENDENCY_UNAVAILABLE",
            message=message,
            stage=stage,
            retryable=False,
            details=details or {},
            unknowns=(),
            artifact_refs=artifact_refs,
        )
    )


def _replay_blocker(
    stage: str,
    message: str,
    *,
    details: dict[str, Any],
    artifact_refs: tuple[ArtifactRef, ...] = (),
) -> SkillBlocked:
    return SkillBlocked(
        Blocker(
            code="T2E_REPLAY_FAILED",
            message=message,
            stage=stage,
            retryable=True,
            details=details,
            unknowns=(),
            artifact_refs=artifact_refs,
        )
    )


@dataclass(frozen=True, slots=True)
class Text2EnvReplayWiring:
    """One handler and the only dependency resolver configured to invoke it."""

    handler: Text2EnvReplayHandler
    dependency_resolver: Text2EnvReplayDependencyResolver


def build_text2env_replay_wiring(
    *,
    artifact_store: LocalArtifactStore,
    package_store: PackageStore,
    runtime_executor: RuntimeExecutor,
    media_verifier: ReplayMediaVerifier,
    work_root: Path,
    dependency_work_root: Path,
    allowed_asset_roots: tuple[Path, ...],
    expected_capability_sha256: str,
    expected_runtime_asset_snapshot_sha256: str | None = None,
    task_config: str = "demo_clean",
    min_visible_pixels: int = 64,
    checkpoint_steps: int = 120,
) -> Text2EnvReplayWiring:
    """Build a replay handler/resolver pair from one immutable configuration."""

    handler_config_sha256 = text2env_replay_handler_config_sha256(
        allowed_asset_roots=allowed_asset_roots,
        expected_capability_sha256=expected_capability_sha256,
        media_verifier_identity=media_verifier.identity,
        task_config=task_config,
        min_visible_pixels=min_visible_pixels,
        checkpoint_steps=checkpoint_steps,
        expected_runtime_asset_snapshot_sha256=expected_runtime_asset_snapshot_sha256,
    )
    resolver = Text2EnvReplayDependencyResolver(
        artifact_store=artifact_store,
        package_store=package_store,
        allowed_asset_roots=allowed_asset_roots,
        runtime_executor_identity=runtime_executor.identity,
        media_verifier_identity=media_verifier.identity,
        handler_config_sha256=handler_config_sha256,
        expected_capability_sha256=expected_capability_sha256,
        work_root=dependency_work_root,
    )
    handler = Text2EnvReplayHandler(
        artifact_store=artifact_store,
        package_store=package_store,
        runtime_executor=runtime_executor,
        work_root=work_root,
        allowed_asset_roots=allowed_asset_roots,
        expected_capability_sha256=expected_capability_sha256,
        media_verifier=media_verifier,
        expected_handler_config_sha256=handler_config_sha256,
        expected_runtime_asset_snapshot_sha256=expected_runtime_asset_snapshot_sha256,
        dependency_resolver=resolver,
        task_config=task_config,
        min_visible_pixels=min_visible_pixels,
        checkpoint_steps=checkpoint_steps,
    )
    return Text2EnvReplayWiring(handler=handler, dependency_resolver=resolver)


def text2env_replay_descriptor(
    *,
    qualification_artifact: ArtifactRef,
    implementation_sha256: str,
) -> SkillDescriptorV2:
    """Build the exact immutable descriptor for the replay Adapter."""

    return SkillDescriptorV2(
        skill_id="text2env.replay",
        version="1.0.0",
        mcp_tool_name="text2env_replay_v1_0_0",
        input_schema="harness.text2env_replay_input.v1",
        output_schema="harness.text2env_replay_output.v1",
        implementation_name="self_improving.harness.handlers.text2env_replay",
        implementation_version="1",
        implementation_sha256=implementation_sha256,
        reproducibility=ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE,
        max_attempts=2,
        qualification_artifact=qualification_artifact,
    )
