from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable
from uuid import UUID

import pytest
from PIL import Image

import self_improving.harness.handlers.text2env_replay as replay_module
from scene_gen.builder import build_scene_package
from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.handlers.text2env_replay import (
    Text2EnvReplayHandler,
    build_text2env_replay_wiring,
    text2env_replay_descriptor,
    text2env_replay_handler_config_sha256,
)
from self_improving.harness.media_sandbox import (
    NativeSandboxIdentity,
    SandboxMetrics,
    SandboxPolicy,
)
from self_improving.harness.media_verifier import (
    MediaVerificationError,
    MediaVerificationReason,
    PngMediaVerification,
    ReplayMediaToolchainIdentity,
    ReplayMediaVerification,
    ReplayMediaVerifierIdentity,
    VideoMediaVerification,
)
from self_improving.harness.package_store import PackageStore
from self_improving.harness.registry import HandlerResult, SkillBlocked
from self_improving.harness.runtime_capability import (
    RUNTIME_ARTIFACT_PATHS,
    RUNTIME_CAPABILITY_SCHEMA,
    RUNTIME_EVIDENCE_ARTIFACT_PATHS,
    RUNTIME_MEDIA_ARTIFACT_PATHS,
)
from self_improving.harness.runtime_events import RuntimeEvent, RuntimeEventCodec, RuntimeEventKind
from self_improving.harness.runtime_executor import (
    RuntimeCapabilitySnapshot,
    RuntimeExecution,
    RuntimeExecutionStatus,
    RuntimeExecutorIdentity,
    RuntimeFailure,
    RuntimeFailureCode,
    RuntimeJob,
    RuntimeOutputFile,
    RuntimeProbeDiagnostics,
)
from self_improving.harness.schemas import (
    ArtifactRef,
    DependencyRef,
    EnvironmentPackage,
    RuntimeConfig,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)

ROOT = Path(__file__).resolve().parents[3]
CATALOG_FIXTURE = ROOT / "tests/fixtures/asset_catalog.json"
RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _with_partial_output(
    execution: RuntimeExecution,
    *,
    relative_path: str = "runtime_evidence.json",
    sha256: str | None = None,
) -> RuntimeExecution:
    path = execution.attempt_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"partial runtime output")
    output = RuntimeOutputFile(
        relative_path=relative_path,
        path=path,
        sha256=sha256 or _sha256(path),
        bytes=path.stat().st_size,
    )
    return replace(execution, output_files=(output,))


def _with_duplicate_partial_output(execution: RuntimeExecution) -> RuntimeExecution:
    changed = _with_partial_output(execution)
    return replace(changed, output_files=(*changed.output_files, changed.output_files[0]))


def _with_mismatched_partial_path(execution: RuntimeExecution) -> RuntimeExecution:
    changed = _with_partial_output(execution)
    output = replace(changed.output_files[0], path=changed.attempt_root / "elsewhere")
    return replace(changed, output_files=(output,))


def _with_missing_partial_root(execution: RuntimeExecution) -> RuntimeExecution:
    changed = _with_partial_output(execution)
    changed.output_files[0].path.unlink()
    changed.attempt_root.rmdir()
    return changed


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_bytes(value))


def _minimal_glb() -> bytes:
    document = b'{"asset":{"version":"2.0"}}'
    document += b" " * (-len(document) % 4)
    chunk = len(document).to_bytes(4, "little") + b"JSON" + document
    return b"glTF" + (2).to_bytes(4, "little") + (12 + len(chunk)).to_bytes(4, "little") + chunk


def _sandbox_identity() -> NativeSandboxIdentity:
    return NativeSandboxIdentity(
        launcher_sha256="1" * 64,
        launcher_bytes=11,
        launcher_source_sha256="2" * 64,
        launcher_source_bytes=22,
        ffmpeg_sha256="3" * 64,
        ffmpeg_bytes=33,
        implementation_sha256="4" * 64,
        implementation_bytes=44,
        landlock_abi=8,
        kernel_architecture="x86_64",
        kernel_release="7.0.0-test",
        policy=SandboxPolicy(),
    )


def _sandbox_metrics() -> SandboxMetrics:
    return SandboxMetrics(
        memory_peak_bytes=4096,
        memory_events=(("oom", 0), ("oom_kill", 0)),
        pids_peak=1,
        pids_events=(("max", 0),),
        cpu_stats=(("usage_usec", 1),),
    )


def _media_identity(*, implementation_sha256: str = "5" * 64) -> ReplayMediaVerifierIdentity:
    return ReplayMediaVerifierIdentity(
        sandbox_identity=_sandbox_identity(),
        implementation_sha256=implementation_sha256,
        implementation_bytes=55,
        max_media_bytes=1024 * 1024,
        max_png_pixels=1024,
        max_video_pixels=1024,
        max_alloc_bytes=1024 * 1024,
        probesize_bytes=1024,
        analyzeduration_microseconds=1_000_000,
        common_ffmpeg_arguments=("-nostdin",),
        png_ffmpeg_arguments=("-f", "png_pipe"),
        video_ffmpeg_arguments=("-f", "mov"),
    )


def _executor_identity(*, implementation_sha256: str = "8" * 64) -> RuntimeExecutorIdentity:
    return RuntimeExecutorIdentity(
        interpreter_sha256="6" * 64,
        interpreter_bytes=66,
        runner_sha256="7" * 64,
        runner_bytes=77,
        implementation_sha256=implementation_sha256,
        implementation_bytes=88,
        timeout_seconds=10.0,
        capability_timeout_seconds=2.0,
        terminate_grace_seconds=1.0,
        max_stdout_bytes=1024,
        max_stderr_bytes=1024,
        max_event_bytes=1024,
        max_transcript_bytes=4096,
        max_capability_bytes=4096,
    )


@dataclass
class RecordingMediaVerifier:
    identity_value: ReplayMediaVerifierIdentity = field(default_factory=_media_identity)
    failure_reason: MediaVerificationReason | None = None
    calls: list[tuple[dict[str, Path], int, int]] = field(default_factory=list)

    @property
    def identity(self) -> ReplayMediaVerifierIdentity:
        return self.identity_value

    def verify(
        self,
        media: dict[str, Path],
        *,
        expected_video_frames: int,
        expected_fps: int,
    ) -> ReplayMediaVerification:
        self.calls.append((dict(media), expected_video_frames, expected_fps))
        if self.failure_reason is not None:
            raise MediaVerificationError(self.failure_reason)
        pngs: list[PngMediaVerification] = []
        for relative, path in media.items():
            if not relative.endswith(".png"):
                continue
            try:
                with Image.open(path) as image:
                    image.load()
                    width, height = image.size
                    mode = image.mode
            except Exception as error:
                raise MediaVerificationError(MediaVerificationReason.PNG_DECODE_FAILED) from error
            pngs.append(
                PngMediaVerification(
                    relative_path=relative,
                    sha256=_sha256(path),
                    bytes=path.stat().st_size,
                    width=width,
                    height=height,
                    mode=mode,
                    sandbox_metrics=_sandbox_metrics(),
                )
            )
        video = None
        if expected_video_frames:
            path = media["observer_runtime.mp4"]
            video = VideoMediaVerification(
                relative_path="observer_runtime.mp4",
                sha256=_sha256(path),
                bytes=path.stat().st_size,
                frame_count=expected_video_frames,
                unique_frame_count=expected_video_frames,
                fps_numerator=expected_fps,
                fps_denominator=1,
                width=2,
                height=2,
                format_name="iso-bmff/mp4",
                codec_name="h264",
                pixel_format="8bit-420",
                sample_aspect_ratio="1/1",
                square_sample_aspect_ratio_defaulted=False,
                sandbox_metrics=_sandbox_metrics(),
            )
        return ReplayMediaVerification(
            pngs=tuple(pngs),
            video=video,
            toolchain=ReplayMediaToolchainIdentity(
                ffmpeg_sha256=self.identity.sandbox_identity.ffmpeg_sha256,
                ffmpeg_bytes=self.identity.sandbox_identity.ffmpeg_bytes,
                launcher_sha256=self.identity.sandbox_identity.launcher_sha256,
                launcher_bytes=self.identity.sandbox_identity.launcher_bytes,
            ),
            verifier_identity=self.identity,
        )


def _media_contract_case(
    tmp_path: Path,
    *,
    video_frames: int,
) -> tuple[
    ReplayMediaVerification,
    ReplayMediaVerifierIdentity,
    dict[str, RuntimeOutputFile],
    dict[str, object],
    RuntimeConfig,
]:
    verifier = RecordingMediaVerifier()
    paths = RUNTIME_MEDIA_ARTIFACT_PATHS if video_frames else RUNTIME_MEDIA_ARTIFACT_PATHS[:4]
    media: dict[str, Path] = {}
    outputs: dict[str, RuntimeOutputFile] = {}
    for relative in paths:
        path = tmp_path / relative
        if relative.endswith(".png"):
            Image.new("RGB", (2, 2), color=(1, 2, 3)).save(path, format="PNG")
        else:
            path.write_bytes(b"fixture-video")
        media[relative] = path
        outputs[relative] = RuntimeOutputFile(
            relative_path=relative,
            path=path,
            sha256=_sha256(path),
            bytes=path.stat().st_size,
        )
    verification = verifier.verify(
        media,
        expected_video_frames=video_frames,
        expected_fps=12,
    )
    return (
        verification,
        verifier.identity,
        outputs,
        {"video_frame_count": video_frames},
        RuntimeConfig(
            precheck_steps=0,
            settle_steps=5,
            contact_window_steps=3,
            video_frames=video_frames,
            fps=12,
        ),
    )


class RecordingContext:
    def __init__(
        self,
        *,
        run_id: UUID = RUN_ID,
        attempt: int = 1,
        dependencies: tuple[DependencyRef, ...] = (),
    ) -> None:
        self.run_id = run_id
        self.attempt = attempt
        self.dependencies = dependencies
        self.events: list[tuple[str, tuple[ArtifactRef, ...]]] = []

    def emit(
        self,
        stage: str,
        *,
        artifact_refs: tuple[ArtifactRef, ...] = (),
    ) -> None:
        self.events.append((stage, artifact_refs))


@dataclass
class RecordingExecutor:
    output_root: Path
    capability_document: dict[str, object]
    identity_value: RuntimeExecutorIdentity = field(default_factory=lambda: _executor_identity())
    mutation: Callable[[RuntimeJob, Path], None] | None = None
    failure_code: RuntimeFailureCode | None = None
    evidence_mutation: Callable[[dict[str, object]], None] | None = None
    validation_mutation: Callable[[dict[str, object]], None] | None = None
    execution_mutation: Callable[[RuntimeExecution], RuntimeExecution] | None = None
    corrupt_precomputed_digest: bool = False
    omit_observer_callbacks: bool = False
    calls: list[RuntimeJob] = field(default_factory=list)

    @property
    def capability_sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.capability_document) + b"\n").hexdigest()

    @property
    def identity(self) -> RuntimeExecutorIdentity:
        return self.identity_value

    def execute(
        self,
        job: RuntimeJob,
        observer: Callable[[RuntimeEvent], None],
    ) -> RuntimeExecution:
        self.calls.append(job)
        attempt_root = self.output_root / job.run_id / f"attempt-{job.attempt}"
        attempt_root.mkdir(parents=True)
        capability = RuntimeCapabilitySnapshot(
            sha256=self.capability_sha256,
            bytes=len(_canonical_bytes(self.capability_document) + b"\n"),
            document=self.capability_document,
        )
        preflight_probe = RuntimeProbeDiagnostics(
            exit_code=0,
            stdout=b"preflight probe stdout",
            stderr=b"preflight probe stderr",
            stdout_truncated=False,
            stderr_truncated=False,
            capability_output=_canonical_bytes(self.capability_document) + b"\n",
            capability_output_truncated=False,
        )
        if self.failure_code is not None:
            execution = RuntimeExecution(
                status=RuntimeExecutionStatus.FAILED,
                failure=RuntimeFailure(self.failure_code, "deliberate executor failure"),
                exit_code=None,
                attempt_root=attempt_root,
                capability=capability,
                postflight_capability_sha256=None,
                events=(),
                transcript=b"",
                stdout=b"diagnostic stdout",
                stderr=b"diagnostic stderr",
                transcript_complete=False,
                transcript_truncated=False,
                stdout_truncated=False,
                stderr_truncated=False,
                output_files=(),
                preflight_probe=preflight_probe,
            )
            return self.execution_mutation(execution) if self.execution_mutation else execution

        resolved = json.loads(job.resolved_scene.read_text(encoding="utf-8"))
        base_steps = max(job.settle_steps, job.video_frames)
        total_steps = job.precheck_steps + base_steps
        if job.video_frames == 0:
            video_indices: list[int] = []
        elif job.video_frames == 1:
            video_indices = [base_steps - 1]
        else:
            video_indices = [
                index * (base_steps - 1) // (job.video_frames - 1)
                for index in range(job.video_frames)
            ]
        evidence: dict[str, object] = {
            "schema_version": "robotwin.scene_runtime_evidence.v2",
            "scene_id": resolved["scene_id"],
            "resolved_scene_sha256": _semantic_resolved_digest(job.resolved_scene),
            "seed": resolved["seed"],
            "status": "pass",
            "precheck_steps": job.precheck_steps,
            "contact_window_steps": min(job.contact_window_steps, base_steps),
            "base_simulation_step_count": base_steps,
            "settle_extra_steps": 0,
            "settle_converge_max": 0,
            "simulation_step_count": base_steps,
            "total_physics_step_count": total_steps,
            "video_frame_count": job.video_frames,
            "unique_video_frame_count": job.video_frames,
            "video_sample_step_indices": video_indices,
            "fps": job.fps,
            "runtime_asset_snapshot_sha256": job.expected_runtime_asset_snapshot_sha256,
            "images": {
                "head": "preview_head.png",
                "world_left": "preview_world_left.png",
                "world_right": "preview_world_right.png",
                "segmentation": "preview_segmentation.png",
                "observer_start": "observer_start.png" if job.video_frames > 0 else None,
                "observer_mid": "observer_mid.png" if job.video_frames > 0 else None,
                "observer_end": "observer_end.png" if job.video_frames > 0 else None,
            },
            "video": "observer_runtime.mp4" if job.video_frames > 0 else None,
        }
        if self.evidence_mutation is not None:
            self.evidence_mutation(evidence)
        validation = {
            "schema_version": "robotwin.scene_validation.v1",
            "scene_id": resolved["scene_id"],
            "resolved_scene_sha256": _semantic_resolved_digest(job.resolved_scene),
            "status": "fail",
            "fail_count": 1,
            "not_run_count": 0,
            "checks": [{"name": "physical_contact", "status": "fail", "evidence": {}}],
        }
        if self.validation_mutation is not None:
            self.validation_mutation(validation)
        media_paths = (
            RUNTIME_MEDIA_ARTIFACT_PATHS
            if job.video_frames > 0
            else RUNTIME_MEDIA_ARTIFACT_PATHS[:4]
        )
        for relative in media_paths:
            path = attempt_root / relative
            if relative.endswith(".png"):
                Image.new("RGB", (2, 2), color=(20, 40, 60)).save(path, format="PNG")
            else:
                path.write_bytes(b"fixture-mp4-decoded-by-recording-verifier")
        _write_json(attempt_root / "runtime_evidence.json", evidence)
        _write_json(attempt_root / "runtime_validation_report.json", validation)
        codec = RuntimeEventCodec(allowed_artifact_paths=RUNTIME_ARTIFACT_PATHS)
        events_list = [
            codec.event(seq=1, kind=RuntimeEventKind.PREFLIGHT_COMPLETED),
            codec.event(seq=2, kind=RuntimeEventKind.SCENE_LOADED),
            codec.event(seq=3, kind=RuntimeEventKind.SIMULATION_STARTED),
        ]
        for completed_steps in range(
            job.checkpoint_steps,
            total_steps + 1,
            job.checkpoint_steps,
        ):
            events_list.append(
                codec.event(
                    seq=len(events_list) + 1,
                    kind=RuntimeEventKind.SIMULATION_CHECKPOINT,
                    completed_steps=completed_steps,
                )
            )
        events_list.extend(
            (
                codec.event(
                    seq=len(events_list) + 1,
                    kind=RuntimeEventKind.SIMULATION_COMPLETED,
                    completed_steps=total_steps,
                ),
                codec.event(
                    seq=len(events_list) + 2,
                    kind=RuntimeEventKind.MEDIA_COMPLETED,
                    artifact_paths=media_paths,
                ),
                codec.event(
                    seq=len(events_list) + 3,
                    kind=RuntimeEventKind.EVIDENCE_COMPLETED,
                    artifact_paths=RUNTIME_EVIDENCE_ARTIFACT_PATHS,
                ),
                codec.event(
                    seq=len(events_list) + 4,
                    kind=RuntimeEventKind.WORKER_COMPLETED,
                ),
            )
        )
        events = tuple(events_list)
        if not self.omit_observer_callbacks:
            for event in events:
                observer(event)
        if self.mutation is not None:
            self.mutation(job, attempt_root)
        output_files = tuple(
            RuntimeOutputFile(
                relative_path=relative,
                path=attempt_root / relative,
                sha256=(
                    "f" * 64
                    if self.corrupt_precomputed_digest and index == 0
                    else _sha256(attempt_root / relative)
                ),
                bytes=(attempt_root / relative).stat().st_size,
            )
            for index, relative in enumerate((*media_paths, *RUNTIME_EVIDENCE_ARTIFACT_PATHS))
        )
        execution = RuntimeExecution(
            status=RuntimeExecutionStatus.SUCCEEDED,
            failure=None,
            exit_code=0,
            attempt_root=attempt_root,
            capability=capability,
            postflight_capability_sha256=self.capability_sha256,
            events=events,
            transcript=b"".join(codec.encode(event) for event in events),
            stdout=b"diagnostic stdout",
            stderr=b"diagnostic stderr",
            transcript_complete=True,
            transcript_truncated=False,
            stdout_truncated=False,
            stderr_truncated=False,
            output_files=output_files,
            preflight_probe=preflight_probe,
            postflight_probe=RuntimeProbeDiagnostics(
                exit_code=0,
                stdout=b"postflight probe stdout",
                stderr=b"postflight probe stderr",
                stdout_truncated=False,
                stderr_truncated=False,
                capability_output=_canonical_bytes(self.capability_document) + b"\n",
                capability_output_truncated=False,
            ),
        )
        return self.execution_mutation(execution) if self.execution_mutation else execution


def _semantic_resolved_digest(path: Path) -> str:
    from scene_gen.schema import ResolvedSceneSpec

    return ResolvedSceneSpec.model_validate_json(path.read_text(encoding="utf-8")).digest()


@dataclass(frozen=True)
class Fixture:
    store: LocalArtifactStore
    value: Text2EnvReplayInput
    robotwin_root: Path
    selected_files: tuple[Path, ...]


def _fixture(tmp_path: Path, *, include_broken_unselected: bool = False) -> Fixture:
    robotwin_root = tmp_path / "RoboTwin"
    objects_root = robotwin_root / "assets/objects"
    task_config = robotwin_root / "task_config/demo_clean.yml"
    task_config.parent.mkdir(parents=True)
    task_config.write_text("task: demo_clean\n", encoding="utf-8")
    source = json.loads(CATALOG_FIXTURE.read_text(encoding="utf-8"))
    entries = []
    selected_files: list[Path] = []
    for entry in source["entries"]:
        selected_entry = entry["asset_id"] in {"003_plate", "071_can"}
        broken_unselected = include_broken_unselected and entry["asset_id"] == "004_fluted-block"
        if not selected_entry and not broken_unselected:
            continue
        if broken_unselected:
            entry["asset_path"] = "/untrusted/unselected"
            for model in entry["models"]:
                model["model_path"] = "/untrusted/unselected"
                model["metadata_path"] = "/untrusted/unselected/model_data0.json"
                model["visual_path"] = "/untrusted/unselected/visual.glb"
                model["collision_path"] = "/untrusted/unselected/collision.glb"
            entries.append(entry)
            continue
        asset_root = objects_root / entry["asset_id"]
        entry["asset_path"] = str(asset_root)
        for model in entry["models"]:
            model["model_path"] = str(asset_root)
            for field_name, relative in (
                ("metadata_path", f"model_data{model['model_id']}.json"),
                ("visual_path", f"visual/base{model['model_id']}.glb"),
                ("collision_path", f"collision/base{model['model_id']}.glb"),
            ):
                path = asset_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(
                    _minimal_glb()
                    if path.suffix == ".glb"
                    else _canonical_bytes({"asset_id": entry["asset_id"], "path": relative})
                )
                model[field_name] = str(path)
                selected_files.append(path)
        entries.append(entry)
    catalog_payload = {
        "schema_version": "robotwin.asset_catalog.v1",
        "robotwin_root": str(robotwin_root),
        "objects_root": str(objects_root),
        "source_commit": "fixture",
        "entries": entries,
    }
    catalog_path = tmp_path / "catalog.json"
    _write_json(catalog_path, catalog_payload)
    catalog: AssetCatalog = load_catalog(catalog_path)
    scene = parse_rule_based("put a can on the plate", seed=7)
    resolved = solve_scene(scene, catalog)
    package_root = tmp_path / "compiled"
    build_scene_package(scene, resolved, package_root)
    store = LocalArtifactStore(tmp_path / "cas")
    published = PackageStore(store).publish(package_root)
    canonical_catalog = tmp_path / "catalog.canonical.json"
    _write_json(canonical_catalog, catalog.canonical_dict())
    catalog_ref = store.put_file(
        canonical_catalog,
        name="asset_catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    package = EnvironmentPackage(
        package_id=resolved.digest(),
        route_id="text2env",
        producer_skill_ref="text2env.compile@1.0.0",
        seed=7,
        scene_spec_sha256=scene.digest(),
        resolved_scene_sha256=resolved.digest(),
        asset_catalog=catalog_ref,
        package_manifest=published.manifest,
    )
    return Fixture(
        store=store,
        value=Text2EnvReplayInput(
            environment_package=package,
            runtime_config=RuntimeConfig(
                precheck_steps=0,
                settle_steps=5,
                contact_window_steps=3,
                video_frames=0,
                fps=12,
            ),
        ),
        robotwin_root=robotwin_root,
        selected_files=tuple(sorted(set(selected_files))),
    )


def _capability_document() -> dict[str, object]:
    return {
        "schema_version": RUNTIME_CAPABILITY_SCHEMA,
        "backend_id": "robotwin.sapien",
        "runner_sha256": "c" * 64,
        "protocol_version": 1,
    }


def _valid_events(steps: int = 5) -> tuple[RuntimeEvent, ...]:
    codec = RuntimeEventCodec(allowed_artifact_paths=RUNTIME_ARTIFACT_PATHS)
    return (
        codec.event(seq=1, kind=RuntimeEventKind.PREFLIGHT_COMPLETED),
        codec.event(seq=2, kind=RuntimeEventKind.SCENE_LOADED),
        codec.event(seq=3, kind=RuntimeEventKind.SIMULATION_STARTED),
        codec.event(
            seq=4,
            kind=RuntimeEventKind.SIMULATION_CHECKPOINT,
            completed_steps=max(1, steps - 1),
        ),
        codec.event(
            seq=5,
            kind=RuntimeEventKind.SIMULATION_COMPLETED,
            completed_steps=steps,
        ),
        codec.event(
            seq=6,
            kind=RuntimeEventKind.MEDIA_COMPLETED,
            artifact_paths=RUNTIME_MEDIA_ARTIFACT_PATHS[:4],
        ),
        codec.event(
            seq=7,
            kind=RuntimeEventKind.EVIDENCE_COMPLETED,
            artifact_paths=RUNTIME_EVIDENCE_ARTIFACT_PATHS,
        ),
        codec.event(seq=8, kind=RuntimeEventKind.WORKER_COMPLETED),
    )


def _handler(
    tmp_path: Path,
    fixture: Fixture,
    executor: RecordingExecutor,
) -> Text2EnvReplayHandler:
    media_verifier = RecordingMediaVerifier()
    return build_text2env_replay_wiring(
        artifact_store=fixture.store,
        package_store=PackageStore(fixture.store),
        runtime_executor=executor,
        media_verifier=media_verifier,
        work_root=tmp_path / "replay-work",
        dependency_work_root=tmp_path / "dependency-work",
        allowed_asset_roots=(fixture.robotwin_root,),
        expected_capability_sha256=executor.capability_sha256,
        checkpoint_steps=4,
    ).handler


def _invoke(
    handler: Text2EnvReplayHandler,
    value: Text2EnvReplayInput,
    context: RecordingContext,
) -> HandlerResult:
    assert handler.dependency_resolver is not None
    context.dependencies = handler.dependency_resolver.resolve("text2env.replay@1.0.0", value)
    return handler(value, context)  # type: ignore[arg-type]


def _handler_configuration(
    fixture: Fixture,
    executor: RecordingExecutor,
    media_verifier: RecordingMediaVerifier,
    *,
    task_config: str = "demo_clean",
    min_visible_pixels: int = 64,
    checkpoint_steps: int = 120,
    expected_runtime_asset_snapshot_sha256: str | None = None,
) -> str:
    return text2env_replay_handler_config_sha256(
        allowed_asset_roots=(fixture.robotwin_root,),
        expected_capability_sha256=executor.capability_sha256,
        media_verifier_identity=media_verifier.identity,
        task_config=task_config,
        min_visible_pixels=min_visible_pixels,
        checkpoint_steps=checkpoint_steps,
        expected_runtime_asset_snapshot_sha256=expected_runtime_asset_snapshot_sha256,
    )


def _replace_resolved(
    tmp_path: Path,
    fixture: Fixture,
    mutate: Callable[[object], object],
) -> Text2EnvReplayInput:
    from scene_gen.schema import ResolvedSceneSpec, SceneSpec

    materialized = PackageStore(fixture.store).materialize(
        fixture.value.environment_package.package_manifest,
        tmp_path / f"materialized-{len(list(tmp_path.glob('materialized-*')))}",
    )
    scene = SceneSpec.model_validate_json(
        (materialized / "scene_spec.json").read_text(encoding="utf-8")
    )
    resolved = ResolvedSceneSpec.model_validate_json(
        (materialized / "resolved_scene.json").read_text(encoding="utf-8")
    )
    changed = mutate(resolved)
    package_root = tmp_path / f"changed-{len(list(tmp_path.glob('changed-*')))}"
    build_scene_package(scene, changed, package_root)
    published = PackageStore(fixture.store).publish(package_root)
    package = fixture.value.environment_package.model_copy(
        update={
            "package_id": changed.digest(),
            "resolved_scene_sha256": changed.digest(),
            "package_manifest": published.manifest,
        }
    )
    return fixture.value.model_copy(update={"environment_package": package})


def test_complete_replay_publishes_bound_receipt_and_keeps_physical_failure(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    context = RecordingContext()

    handler = _handler(tmp_path, fixture, executor)
    result = _invoke(handler, fixture.value, context)
    output = Text2EnvReplayOutput.model_validate(result.output)

    assert len(executor.calls) == 1
    job = executor.calls[0]
    assert job.run_id == str(RUN_ID)
    assert job.attempt == 1
    assert job.robotwin_root == fixture.robotwin_root
    assert job.resolved_scene.name == "resolved_scene.json"
    assert job.runtime_asset_root.name == "objects"
    assert job.runtime_asset_root.is_relative_to(tmp_path / "replay-work")
    assert job.runtime_asset_manifest.name == "runtime_asset_snapshot.json"
    assert _sha256(job.runtime_asset_manifest) == job.expected_runtime_asset_snapshot_sha256
    assert all(
        not bool(path.stat().st_mode & 0o222)
        for path in (job.runtime_asset_root, *job.runtime_asset_root.rglob("*"))
    )
    assert (
        job.asset_catalog
        == fixture.store.resolve(fixture.value.environment_package.asset_catalog).path
    )
    assert job.settle_steps == 5
    assert job.video_frames == 0
    assert output.runtime_evidence.schema_version == "robotwin.scene_runtime_evidence.v2"
    assert [ref.name for ref in output.replay_artifacts] == sorted(
        ref.name for ref in output.replay_artifacts
    )
    names = {ref.name for ref in output.replay_artifacts}
    assert {
        "replay_execution_receipt",
        "runtime_capability",
        "runtime_event_transcript",
        "runtime_validation_report",
        "preview_head",
    }.issubset(names)
    assert "runtime_stdout" not in names
    assert "runtime_stderr" not in names
    result_names = {ref.name for ref in result.artifacts}
    assert {"runtime_stdout", "runtime_stderr"}.issubset(result_names)
    assert {
        "preflight_probe_diagnostics",
        "postflight_probe_diagnostics",
        "runtime_execution_diagnostics",
    }.issubset(result_names)
    preflight_probe_ref = next(
        ref for ref in result.artifacts if ref.name == "preflight_probe_diagnostics"
    )
    preflight_probe = json.loads(
        fixture.store.resolve(preflight_probe_ref).path.read_text(encoding="utf-8")
    )
    assert preflight_probe["schema_version"] == "harness.runtime_probe_diagnostics.v1"
    postflight_probe_ref = next(
        ref for ref in result.artifacts if ref.name == "postflight_probe_diagnostics"
    )
    postflight_probe = json.loads(
        fixture.store.resolve(postflight_probe_ref).path.read_text(encoding="utf-8")
    )
    for probe in (preflight_probe, postflight_probe):
        capability_output = probe["streams"]["capability_output"]
        assert capability_output["sha256"] == executor.capability_sha256
        assert capability_output["media_type"] == "application/json"
        assert capability_output["schema_version"] == RUNTIME_CAPABILITY_SCHEMA
    receipt_ref = next(
        ref for ref in output.replay_artifacts if ref.name == "replay_execution_receipt"
    )
    receipt = json.loads(fixture.store.resolve(receipt_ref).path.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == "harness.text2env_replay_receipt.v1"
    assert receipt["environment_package_id"] == fixture.value.environment_package.package_id
    assert receipt["runtime_config"] == fixture.value.runtime_config.model_dump(mode="json")
    assert receipt["invocation_dependencies"] == [
        item.model_dump(mode="json") for item in context.dependencies
    ]
    assert receipt["runtime_evidence"]["sha256"] == output.runtime_evidence.sha256
    assert receipt["runtime_validation_report"]["status"] == "fail"
    assert receipt["capability"]["sha256"] == executor.capability_sha256
    capability_ref = next(
        ref for ref in output.replay_artifacts if ref.name == "runtime_capability"
    )
    assert capability_ref.sha256 == executor.capability_sha256
    assert fixture.store.resolve(capability_ref).path.read_bytes().endswith(b"\n")
    assert receipt["runtime_assets"]["self_contained"] is True
    assert receipt["runtime_assets"]["dependency_enforced"] is True
    assert receipt["runtime_assets"]["configured_snapshot_pin_enforced"] is False
    assert receipt["runtime_assets"]["expected_sha256"] is None
    assert receipt["handler_configuration"]["expected_runtime_asset_snapshot_sha256"] is None
    assert receipt["runtime_assets"]["manifest"]["sha256"] == (
        job.expected_runtime_asset_snapshot_sha256
    )
    locator_free_named_identity_fields = {
        "name",
        "sha256",
        "bytes",
        "media_type",
        "schema_version",
    }
    assert set(receipt["runtime_assets"]["manifest"]) == locator_free_named_identity_fields
    assert receipt["runtime_assets"]["manifest"]["name"] == "runtime_asset_snapshot"
    assert receipt["runtime_assets"]["manifest"]["media_type"] == "application/json"
    assert (
        receipt["runtime_assets"]["manifest"]["schema_version"]
        == "harness.runtime_asset_snapshot.v1"
    )
    assert receipt["runtime_assets"]["members"]
    assert all(
        set(item) == locator_free_named_identity_fields
        for item in receipt["runtime_assets"]["members"]
    )
    assert all(
        item["name"] == f"runtime_asset_{item['sha256'][:16]}"
        and item["media_type"] == "application/octet-stream"
        and item["schema_version"] is None
        for item in receipt["runtime_assets"]["members"]
    )
    assert all(
        set(item) == {"locator", "sha256", "bytes", "media_type", "schema_version"}
        for item in receipt["media"]
    )
    receipt_media_locators = [item["locator"] for item in receipt["media"]]
    assert receipt_media_locators == sorted(receipt_media_locators)
    assert set(receipt_media_locators).issubset(RUNTIME_MEDIA_ARTIFACT_PATHS)
    assert receipt["handler_configuration"]["dependency"] == (
        _handler(tmp_path / "identity", fixture, executor).dependency_identity.model_dump(
            mode="json"
        )
    )
    assert receipt["media_verification"]["video"] is None
    assert len(receipt["media_verification"]["pngs"]) == 4
    assert receipt["media_verification"]["pngs"][0]["sandbox_metrics"] == {
        "memory_peak_bytes": 4096,
        "memory_events": [
            {"name": "oom", "value": 0},
            {"name": "oom_kill", "value": 0},
        ],
        "pids_peak": 1,
        "pids_events": [{"name": "max", "value": 0}],
        "cpu_stats": [{"name": "usage_usec", "value": 1}],
    }
    assert len(handler.media_verifier.calls) == 1  # type: ignore[attr-defined]
    media_call, expected_frames, expected_fps = handler.media_verifier.calls[0]  # type: ignore[attr-defined]
    assert tuple(media_call) == RUNTIME_MEDIA_ARTIFACT_PATHS[:4]
    assert expected_frames == 0
    assert expected_fps == 12
    assert [item["locator"] for item in receipt["media"]] == sorted(
        item["locator"] for item in receipt["media"]
    )
    assert [stage for stage, _ in context.events[:-1]] == [
        "replay.runtime_assets.snapshotted",
        "replay.preflight.completed",
        "replay.scene.loaded",
        "replay.simulation.started",
        "replay.simulation.checkpoint.steps-4",
        "replay.simulation.completed.steps-5",
        "replay.media.completed",
        "replay.evidence.completed",
        "replay.worker.completed",
        "replay.diagnostics.published",
    ]
    assert context.events[0][1]
    assert all(not refs for _, refs in context.events[1:9])
    assert context.events[9][1]
    diagnostic_names = {ref.name for ref in context.events[9][1]}
    assert "runtime_capability" in diagnostic_names
    assert "preflight_probe_capability_output" not in diagnostic_names
    assert "postflight_probe_capability_output" not in diagnostic_names
    assert context.events[-1][0] == "replay.artifacts.published"
    assert set(context.events[-1][1]) == {
        output.runtime_evidence,
        *output.replay_artifacts,
    }


@pytest.mark.parametrize("mutation", ["payload", "truncated", "exit-code"])
def test_unvalidated_capability_probe_output_remains_raw(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())

    def mutate(execution: RuntimeExecution) -> RuntimeExecution:
        assert execution.preflight_probe is not None
        updates: dict[str, object]
        if mutation == "payload":
            updates = {"capability_output": b"unvalidated capability bytes"}
        elif mutation == "truncated":
            updates = {"capability_output_truncated": True}
        else:
            updates = {"exit_code": 1}
        return replace(
            execution,
            preflight_probe=replace(execution.preflight_probe, **updates),
        )

    executor.execution_mutation = mutate
    result = _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())
    probe_ref = next(ref for ref in result.artifacts if ref.name == "preflight_probe_diagnostics")
    probe = json.loads(fixture.store.resolve(probe_ref).path.read_text(encoding="utf-8"))

    capability_output = probe["streams"]["capability_output"]
    assert capability_output["media_type"] == "application/octet-stream"
    assert capability_output["schema_version"] is None


def test_public_replay_output_is_content_deterministic_across_run_contexts(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    handler = _handler(tmp_path, fixture, executor)

    first = Text2EnvReplayOutput.model_validate(
        _invoke(
            handler,
            fixture.value,
            RecordingContext(
                run_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                attempt=1,
            ),
        ).output
    )
    second = Text2EnvReplayOutput.model_validate(
        _invoke(
            handler,
            fixture.value,
            RecordingContext(
                run_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                attempt=2,
            ),
        ).output
    )

    assert first == second
    receipt = next(ref for ref in first.replay_artifacts if ref.name == "replay_execution_receipt")
    document = json.loads(fixture.store.resolve(receipt).path.read_text(encoding="utf-8"))
    assert "run_id" not in document
    assert "attempt" not in document
    assert "stdout" not in document
    assert "diagnostics" not in document
    assert str(tmp_path) not in json.dumps(document, sort_keys=True)
    assert all(not Path(item["locator"]).is_absolute() for item in document["media"])


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    [("min_visible_pixels", 65), ("checkpoint_steps", 5)],
)
def test_content_receipt_binds_handler_runtime_parameters(
    tmp_path: Path,
    field_name: str,
    changed_value: int,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    baseline_handler = _handler(tmp_path, fixture, executor)
    with pytest.raises(ValueError, match="does not bind"):
        replace(baseline_handler, **{field_name: changed_value})
    verifier = baseline_handler.media_verifier
    changed_config = {
        "min_visible_pixels": baseline_handler.min_visible_pixels,
        "checkpoint_steps": baseline_handler.checkpoint_steps,
        field_name: changed_value,
    }
    changed_handler = build_text2env_replay_wiring(
        artifact_store=fixture.store,
        package_store=PackageStore(fixture.store),
        runtime_executor=executor,
        media_verifier=verifier,
        work_root=tmp_path / "changed-replay-work",
        dependency_work_root=tmp_path / "changed-dependency-work",
        allowed_asset_roots=(fixture.robotwin_root,),
        expected_capability_sha256=executor.capability_sha256,
        min_visible_pixels=changed_config["min_visible_pixels"],
        checkpoint_steps=changed_config["checkpoint_steps"],
    ).handler

    baseline = Text2EnvReplayOutput.model_validate(
        _invoke(
            baseline_handler,
            fixture.value,
            RecordingContext(run_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")),
        ).output
    )
    changed = Text2EnvReplayOutput.model_validate(
        _invoke(
            changed_handler,
            fixture.value,
            RecordingContext(run_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")),
        ).output
    )
    baseline_ref = next(
        ref for ref in baseline.replay_artifacts if ref.name == "replay_execution_receipt"
    )
    changed_ref = next(
        ref for ref in changed.replay_artifacts if ref.name == "replay_execution_receipt"
    )
    baseline_receipt = json.loads(
        fixture.store.resolve(baseline_ref).path.read_text(encoding="utf-8")
    )
    changed_receipt = json.loads(
        fixture.store.resolve(changed_ref).path.read_text(encoding="utf-8")
    )

    assert baseline_receipt["handler_configuration"]["checkpoint_steps"] == 4
    assert baseline_receipt["handler_configuration"]["min_visible_pixels"] == 64
    assert changed_receipt["handler_configuration"][field_name] == changed_value
    assert changed_ref.sha256 != baseline_ref.sha256


def test_package_binding_failure_is_nonretryable_and_skips_executor(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    bad_package = fixture.value.environment_package.model_copy(
        update={"scene_spec_sha256": "f" * 64}
    )

    with pytest.raises(SkillBlocked) as captured:
        _handler(tmp_path, fixture, executor)(
            fixture.value.model_copy(update={"environment_package": bad_package}),
            RecordingContext(),
        )

    assert captured.value.blocker.code == "T2E_PACKAGE_INVALID"
    assert captured.value.blocker.retryable is False
    assert captured.value.blocker.stage == "package_binding"
    assert executor.calls == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"allowed_asset_roots": ()}, "allowed_asset_roots"),
        ({"expected_capability_sha256": "bad"}, "expected_capability_sha256"),
        ({"expected_handler_config_sha256": "bad"}, "expected_handler_config_sha256"),
        (
            {"expected_runtime_asset_snapshot_sha256": "bad"},
            "expected_runtime_asset_snapshot_sha256",
        ),
        ({"task_config": ""}, "task_config"),
        ({"task_config": "bad/name"}, "task_config"),
        ({"min_visible_pixels": -1}, "min_visible_pixels"),
        ({"min_visible_pixels": True}, "min_visible_pixels"),
        ({"checkpoint_steps": 0}, "checkpoint_steps"),
        ({"checkpoint_steps": True}, "checkpoint_steps"),
    ],
)
def test_handler_configuration_is_fail_closed(
    tmp_path: Path,
    change: dict[str, object],
    message: str,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    media_verifier = RecordingMediaVerifier()
    parameters: dict[str, object] = {
        "artifact_store": fixture.store,
        "package_store": PackageStore(fixture.store),
        "runtime_executor": executor,
        "work_root": tmp_path / "work",
        "allowed_asset_roots": (fixture.robotwin_root,),
        "expected_capability_sha256": executor.capability_sha256,
        "media_verifier": media_verifier,
        "expected_handler_config_sha256": _handler_configuration(
            fixture,
            executor,
            media_verifier,
        ),
    }
    parameters.update(change)

    with pytest.raises(ValueError, match=message):
        Text2EnvReplayHandler(**parameters)  # type: ignore[arg-type]


def test_missing_package_manifest_is_typed_materialization_failure(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    missing = fixture.value.environment_package.package_manifest.model_copy(
        update={
            "sha256": "f" * 64,
            "uri": "artifact://sha256/" + "f" * 64,
        }
    )
    package = fixture.value.environment_package.model_copy(update={"package_manifest": missing})

    with pytest.raises(SkillBlocked) as captured:
        _handler(tmp_path, fixture, executor)(
            fixture.value.model_copy(update={"environment_package": package}),
            RecordingContext(),
        )

    assert captured.value.blocker.stage == "package_materialization"
    assert captured.value.blocker.details["reason"] == "manifest_unavailable"


def test_materialized_package_is_parsed_again_after_store_verification(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())

    class CorruptingPackageStore:
        def materialize(self, manifest: ArtifactRef, destination: Path) -> Path:
            root = PackageStore(fixture.store).materialize(manifest, destination)
            (root / "scene_spec.json").write_text("{", encoding="utf-8")
            return root

    handler = Text2EnvReplayHandler(
        artifact_store=fixture.store,
        package_store=CorruptingPackageStore(),  # type: ignore[arg-type]
        runtime_executor=executor,
        work_root=tmp_path / "work",
        allowed_asset_roots=(fixture.robotwin_root,),
        expected_capability_sha256=executor.capability_sha256,
        media_verifier=(media_verifier := RecordingMediaVerifier()),
        expected_handler_config_sha256=_handler_configuration(
            fixture,
            executor,
            media_verifier,
        ),
    )

    with pytest.raises(SkillBlocked) as captured:
        handler(fixture.value, RecordingContext())

    assert captured.value.blocker.stage == "package_binding"
    assert captured.value.blocker.code == "T2E_PACKAGE_INVALID"


def test_missing_catalog_is_typed_dependency_failure(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    missing = fixture.value.environment_package.asset_catalog.model_copy(
        update={
            "sha256": "f" * 64,
            "uri": "artifact://sha256/" + "f" * 64,
        }
    )
    package = fixture.value.environment_package.model_copy(update={"asset_catalog": missing})

    with pytest.raises(SkillBlocked) as captured:
        _handler(tmp_path, fixture, executor)(
            fixture.value.model_copy(update={"environment_package": package}),
            RecordingContext(),
        )

    assert captured.value.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert captured.value.blocker.stage == "catalog"


def test_resolved_and_catalog_digest_disagreement_is_nonretryable_dependency(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    changed = _replace_resolved(
        tmp_path,
        fixture,
        lambda resolved: resolved.model_copy(update={"asset_catalog_sha256": "f" * 64}),
    )
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())

    with pytest.raises(SkillBlocked) as captured:
        _handler(tmp_path, fixture, executor)(changed, RecordingContext())

    assert captured.value.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert captured.value.blocker.retryable is False
    assert captured.value.blocker.stage == "catalog_binding"
    assert "resolved_scene.asset_catalog_sha256" in captured.value.blocker.details["problems"]
    assert executor.calls == []


def test_handler_dependency_identity_drift_blocks_before_materialization(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    handler = _handler(tmp_path, fixture, executor)
    verifier = handler.media_verifier
    verifier.identity_value = _media_identity(implementation_sha256="6" * 64)  # type: ignore[attr-defined]

    with pytest.raises(SkillBlocked) as captured:
        handler(fixture.value, RecordingContext())

    assert captured.value.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert captured.value.blocker.stage == "handler_configuration"
    assert captured.value.blocker.retryable is False
    assert executor.calls == []
    assert not (tmp_path / "replay-work").exists()


def test_unavailable_handler_identity_blocks_before_materialization(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    handler = _handler(tmp_path, fixture, executor)
    handler.media_verifier.identity_value = object()  # type: ignore[attr-defined,assignment]

    with pytest.raises(SkillBlocked) as captured:
        handler(fixture.value, RecordingContext())

    assert captured.value.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert captured.value.blocker.stage == "handler_configuration"
    assert captured.value.blocker.details["error_type"] == "ValueError"
    assert executor.calls == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda dependencies: dependencies[:-1],
        lambda dependencies: (
            *dependencies,
            DependencyRef(name="unexpected", version="1", sha256="a" * 64),
        ),
        lambda dependencies: (*dependencies, dependencies[0]),
        lambda dependencies: (
            dependencies[0].model_copy(update={"sha256": "f" * 64}),
            *dependencies[1:],
        ),
    ],
    ids=["missing", "extra", "duplicate", "identity-mismatch"],
)
def test_handler_rejects_any_inexact_invocation_dependency_set(
    tmp_path: Path,
    mutate: Callable[[tuple[DependencyRef, ...]], tuple[DependencyRef, ...]],
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    handler = _handler(tmp_path, fixture, executor)
    assert handler.dependency_resolver is not None
    expected = handler.dependency_resolver.resolve("text2env.replay@1.0.0", fixture.value)
    context = RecordingContext(dependencies=mutate(expected))

    with pytest.raises(SkillBlocked) as captured:
        handler(fixture.value, context)  # type: ignore[arg-type]

    blocker = captured.value.blocker
    assert blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert blocker.stage == "invocation_dependencies"
    assert blocker.retryable is False
    assert any(ref.name == "runtime_asset_snapshot" for ref in blocker.artifact_refs)
    assert executor.calls == []


def test_handler_rejects_source_asset_drift_after_invocation_resolution(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    handler = _handler(tmp_path, fixture, executor)
    assert handler.dependency_resolver is not None
    dependencies = handler.dependency_resolver.resolve("text2env.replay@1.0.0", fixture.value)
    (fixture.robotwin_root / "assets/objects/071_can/late-bound.bin").write_bytes(b"drift")
    context = RecordingContext(dependencies=dependencies)

    with pytest.raises(SkillBlocked) as captured:
        handler(fixture.value, context)  # type: ignore[arg-type]

    assert captured.value.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert captured.value.blocker.stage == "invocation_dependencies"
    assert executor.calls == []


def test_runtime_root_binding_error_is_typed_dependency_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())

    def fail_root(*_args) -> Path:
        raise OSError("injected root failure")

    monkeypatch.setattr(replay_module, "_selected_robotwin_root", fail_root)

    with pytest.raises(SkillBlocked) as captured:
        _handler(tmp_path, fixture, executor)(fixture.value, RecordingContext())

    assert captured.value.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert captured.value.blocker.stage == "runtime_assets"
    assert captured.value.blocker.details["reason"] == "OSError"
    assert executor.calls == []


def test_expected_runtime_asset_snapshot_mismatch_is_retained_and_blocks_execution(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    handler = _handler(tmp_path, fixture, executor)
    verifier = handler.media_verifier
    expected = "f" * 64
    changed_config_sha256 = _handler_configuration(
        fixture,
        executor,
        verifier,  # type: ignore[arg-type]
        checkpoint_steps=4,
        expected_runtime_asset_snapshot_sha256=expected,
    )
    assert handler.dependency_resolver is not None
    changed_resolver = replace(
        handler.dependency_resolver,
        handler_config_sha256=changed_config_sha256,
    )
    handler = replace(
        handler,
        expected_runtime_asset_snapshot_sha256=expected,
        expected_handler_config_sha256=changed_config_sha256,
        dependency_resolver=changed_resolver,
    )
    context = RecordingContext()

    with pytest.raises(SkillBlocked) as captured:
        _invoke(handler, fixture.value, context)

    blocker = captured.value.blocker
    assert blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert blocker.stage == "runtime_assets"
    assert blocker.retryable is False
    assert blocker.details["expected_sha256"] == expected
    assert blocker.details["actual_sha256"] != expected
    assert any(ref.name == "runtime_asset_snapshot" for ref in blocker.artifact_refs)
    assert context.events == [("replay.runtime_assets.snapshotted", blocker.artifact_refs)]
    assert executor.calls == []


def test_runtime_asset_materialization_failure_retains_snapshot_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())

    def fail_materialize(*_args, **_kwargs):
        from self_improving.harness.runtime_assets import RuntimeAssetSnapshotError

        raise RuntimeAssetSnapshotError("injected_materialize_failure")

    monkeypatch.setattr(replay_module.RuntimeAssetStore, "materialize", fail_materialize)
    context = RecordingContext()

    with pytest.raises(SkillBlocked) as captured:
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, context)

    blocker = captured.value.blocker
    assert blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert blocker.stage == "runtime_assets"
    assert blocker.details["reason"] == "injected_materialize_failure"
    assert blocker.artifact_refs
    assert context.events == [("replay.runtime_assets.snapshotted", blocker.artifact_refs)]
    assert executor.calls == []


@pytest.mark.parametrize("mutation", ["asset", "model", "sources"])
def test_resolved_selection_must_match_catalog(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _fixture(tmp_path)

    def mutate(resolved):
        first = resolved.objects[0]
        updates = {
            "asset": {"asset_id": "absent"},
            "model": {"model_id": 999},
            "sources": {"source_files": (*first.source_files, str(tmp_path / "extra"))},
        }[mutation]
        return resolved.model_copy(
            update={"objects": (first.model_copy(update=updates), *resolved.objects[1:])}
        )

    changed = _replace_resolved(tmp_path, fixture, mutate)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())

    with pytest.raises(SkillBlocked) as captured:
        _handler(tmp_path, fixture, executor)(changed, RecordingContext())

    assert captured.value.blocker.code == "T2E_PACKAGE_INVALID"
    assert captured.value.blocker.stage == "package_binding"
    assert executor.calls == []


def test_generated_asset_provenance_is_included_in_stability_snapshot(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    first_path = fixture.robotwin_root / "assets/objects/071_can/generation_provenance.json"
    first_path.write_text("{}", encoding="utf-8")

    def mutate(resolved):
        first = resolved.objects[0]
        changed = first.model_copy(
            update={
                "asset_provenance": "procedural_generated",
                "generation_metadata_path": str(first_path),
                "source_files": tuple(sorted((*first.source_files, str(first_path)))),
            }
        )
        return resolved.model_copy(update={"objects": (changed, *resolved.objects[1:])})

    changed = _replace_resolved(tmp_path, fixture, mutate)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())

    output = Text2EnvReplayOutput.model_validate(
        _invoke(_handler(tmp_path, fixture, executor), changed, RecordingContext()).output
    )

    receipt_ref = next(
        item for item in output.replay_artifacts if item.name == "replay_execution_receipt"
    )
    receipt = json.loads(fixture.store.resolve(receipt_ref).path.read_text(encoding="utf-8"))
    assert _sha256(first_path) in [item["sha256"] for item in receipt["runtime_assets"]["members"]]
    assert all("path" not in item for item in receipt["runtime_assets"]["members"])


def test_selected_asset_symlink_is_dependency_failure_before_execution(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    selected = fixture.selected_files[0]
    target = selected.with_name(selected.name + ".real")
    selected.rename(target)
    selected.symlink_to(target)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())

    with pytest.raises(SkillBlocked) as captured:
        _handler(tmp_path, fixture, executor)(fixture.value, RecordingContext())

    assert captured.value.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert captured.value.blocker.retryable is False
    assert captured.value.blocker.stage == "runtime_assets"
    assert executor.calls == []


def test_unselected_broken_catalog_entry_does_not_block_replay(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, include_broken_unselected=True)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())

    output = Text2EnvReplayOutput.model_validate(
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext()).output
    )

    assert output.runtime_evidence.sha256
    assert len(executor.calls) == 1


def test_source_asset_mutation_during_execution_cannot_change_staged_loader_tree(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def mutate(_job: RuntimeJob, _attempt_root: Path) -> None:
        fixture.selected_files[0].write_bytes(b"changed")

    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        mutation=mutate,
    )
    output = Text2EnvReplayOutput.model_validate(
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext()).output
    )

    assert output.runtime_evidence.schema_version == "robotwin.scene_runtime_evidence.v2"
    assert fixture.selected_files[0].read_bytes() == b"changed"
    staged = executor.calls[0].runtime_asset_root
    staged_copies = tuple(staged.rglob(fixture.selected_files[0].name))
    assert staged_copies
    assert all(path.read_bytes() != b"changed" for path in staged_copies)


def test_source_asset_removal_during_execution_does_not_remove_staged_loader_input(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def remove(_job: RuntimeJob, _attempt_root: Path) -> None:
        fixture.selected_files[0].unlink()

    executor = RecordingExecutor(tmp_path / "runtime", _capability_document(), mutation=remove)

    output = Text2EnvReplayOutput.model_validate(
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext()).output
    )

    assert output.runtime_evidence.schema_version == "robotwin.scene_runtime_evidence.v2"
    assert not fixture.selected_files[0].exists()
    assert tuple(executor.calls[0].runtime_asset_root.rglob(fixture.selected_files[0].name))


@pytest.mark.parametrize("unsafe", ["work-file", "run-file"])
def test_replay_attempt_roots_reject_unsafe_existing_entries(
    tmp_path: Path,
    unsafe: str,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    work_root = tmp_path / "unsafe-work"
    if unsafe == "work-file":
        work_root.write_text("not a directory", encoding="utf-8")
    else:
        work_root.mkdir()
        (work_root / str(RUN_ID)).write_text("not a directory", encoding="utf-8")
    handler = Text2EnvReplayHandler(
        artifact_store=fixture.store,
        package_store=PackageStore(fixture.store),
        runtime_executor=executor,
        work_root=work_root,
        allowed_asset_roots=(fixture.robotwin_root,),
        expected_capability_sha256=executor.capability_sha256,
        media_verifier=(media_verifier := RecordingMediaVerifier()),
        expected_handler_config_sha256=_handler_configuration(
            fixture,
            executor,
            media_verifier,
        ),
    )

    with pytest.raises(RuntimeError, match="root"):
        handler(fixture.value, RecordingContext())


@pytest.mark.parametrize(
    "failure_code",
    [
        RuntimeFailureCode.CAPABILITY_TIMEOUT,
        RuntimeFailureCode.INCOMPLETE_TRANSCRIPT,
        RuntimeFailureCode.WORKER_TIMEOUT,
        RuntimeFailureCode.WORKER_CRASH,
    ],
)
def test_incomplete_or_crashed_execution_is_retryable(
    tmp_path: Path,
    failure_code: RuntimeFailureCode,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        failure_code=failure_code,
    )
    context = RecordingContext()

    with pytest.raises(SkillBlocked) as captured:
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, context)

    assert len(executor.calls) == 1
    assert captured.value.blocker.code == "T2E_REPLAY_FAILED"
    assert captured.value.blocker.retryable is True
    assert captured.value.blocker.details["runtime_failure_code"] == failure_code.value
    assert captured.value.blocker.artifact_refs
    assert context.events[-1][0] == "replay.diagnostics.published"
    assert set(context.events[-1][1]).issubset(captured.value.blocker.artifact_refs)
    assert any(ref.name == "runtime_asset_snapshot" for ref in captured.value.blocker.artifact_refs)


def test_executor_identity_drift_during_execution_blocks_with_diagnostics(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())

    def drift(execution: RuntimeExecution) -> RuntimeExecution:
        executor.identity_value = _executor_identity(implementation_sha256="9" * 64)
        return execution

    executor.execution_mutation = drift
    handler = _handler(tmp_path, fixture, executor)
    context = RecordingContext()

    with pytest.raises(SkillBlocked) as captured:
        _invoke(handler, fixture.value, context)

    blocker = captured.value.blocker
    assert blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert blocker.stage == "invocation_dependencies"
    assert blocker.retryable is False
    assert "runtime_execution_diagnostics" in {ref.name for ref in blocker.artifact_refs}
    assert len(executor.calls) == 1


def test_worker_failure_publishes_safe_partial_output_as_untrusted_diagnostics(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        failure_code=RuntimeFailureCode.WORKER_CRASH,
        execution_mutation=_with_partial_output,
    )
    context = RecordingContext()

    with pytest.raises(SkillBlocked) as captured:
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, context)

    partial = next(
        ref
        for ref in captured.value.blocker.artifact_refs
        if ref.name == "untrusted_runtime_evidence"
    )
    assert partial.schema_version is None
    assert partial.media_type == "application/octet-stream"
    assert fixture.store.resolve(partial).path.read_bytes() == b"partial runtime output"
    assert context.events[-1][0] == "replay.diagnostics.published"
    assert set(context.events[-1][1]).issubset(captured.value.blocker.artifact_refs)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda execution: _with_partial_output(
            execution,
            relative_path="../escaped-runtime-evidence.json",
        ),
        lambda execution: _with_partial_output(execution, sha256="f" * 64),
        lambda execution: _with_partial_output(execution, sha256="invalid"),
        _with_duplicate_partial_output,
        _with_mismatched_partial_path,
        _with_missing_partial_root,
    ],
    ids=[
        "path-escape",
        "digest-mismatch",
        "invalid-identity",
        "duplicate",
        "path-mismatch",
        "missing-root",
    ],
)
def test_unsafe_partial_runtime_output_is_internal_and_never_promoted(
    tmp_path: Path,
    mutation: Callable[[RuntimeExecution], RuntimeExecution],
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        failure_code=RuntimeFailureCode.WORKER_CRASH,
        execution_mutation=mutation,
    )
    context = RecordingContext()

    with pytest.raises(RuntimeError):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, context)

    assert context.events[-1][0] == "replay.diagnostics.published"
    assert all(not ref.name.startswith("untrusted_") for ref in context.events[-1][1])


def test_partial_runtime_output_cas_identity_is_rechecked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        failure_code=RuntimeFailureCode.WORKER_CRASH,
        execution_mutation=_with_partial_output,
    )
    original_put = fixture.store.put_file

    def corrupt(source: Path, **kwargs) -> ArtifactRef:
        artifact = original_put(source, **kwargs)
        if kwargs.get("name") == "untrusted_runtime_evidence":
            return artifact.model_copy(update={"bytes": artifact.bytes + 1})
        return artifact

    monkeypatch.setattr(fixture.store, "put_file", corrupt)
    context = RecordingContext()

    with pytest.raises(RuntimeError, match="partial runtime output changed"):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, context)

    assert context.events[-1][0] == "replay.diagnostics.published"
    assert all(not ref.name.startswith("untrusted_") for ref in context.events[-1][1])


@pytest.mark.parametrize(
    "failure_code",
    [
        RuntimeFailureCode.CAPABILITY_MISMATCH,
        RuntimeFailureCode.CAPABILITY_PROCESS_FAILED,
        RuntimeFailureCode.CAPABILITY_DRIFT,
        RuntimeFailureCode.RUNTIME_ASSET_DRIFT,
    ],
)
def test_capability_acquisition_or_drift_is_nonretryable_dependency_failure(
    tmp_path: Path,
    failure_code: RuntimeFailureCode,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        failure_code=failure_code,
    )

    with pytest.raises(SkillBlocked) as captured:
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())

    assert captured.value.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert captured.value.blocker.retryable is False
    assert captured.value.blocker.stage == "runtime_preflight"
    assert captured.value.blocker.details["runtime_failure_code"] == failure_code.value


def test_runtime_bootstrap_failure_is_nonretryable_and_keeps_diagnostics(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        failure_code=RuntimeFailureCode.RUNTIME_PREFLIGHT_FAILED,
    )
    context = RecordingContext()

    with pytest.raises(SkillBlocked) as captured:
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, context)

    blocker = captured.value.blocker
    assert blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert blocker.stage == "runtime_preflight"
    assert blocker.retryable is False
    assert {ref.name for ref in blocker.artifact_refs}.issuperset(
        {"runtime_capability", "runtime_event_transcript", "runtime_stdout", "runtime_stderr"}
    )
    assert context.events[-1][0] == "replay.diagnostics.published"
    assert set(context.events[-1][1]).issubset(blocker.artifact_refs)


def test_failed_capability_probe_without_snapshot_still_persists_all_available_bytes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def remove_capability(execution: RuntimeExecution) -> RuntimeExecution:
        return replace(
            execution,
            capability=None,
            secondary_failures=(
                RuntimeFailure(RuntimeFailureCode.WORKER_CRASH, "secondary worker crash"),
            ),
        )

    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        failure_code=RuntimeFailureCode.CAPABILITY_PROCESS_FAILED,
        execution_mutation=remove_capability,
    )

    with pytest.raises(SkillBlocked) as captured:
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())

    refs = captured.value.blocker.artifact_refs
    assert "runtime_capability" not in {ref.name for ref in refs}
    raw_capability = next(ref for ref in refs if ref.name == "preflight_probe_capability_output")
    assert raw_capability.media_type == "application/octet-stream"
    assert raw_capability.schema_version is None
    diagnostic_ref = next(ref for ref in refs if ref.name == "runtime_execution_diagnostics")
    diagnostic = json.loads(fixture.store.resolve(diagnostic_ref).path.read_text(encoding="utf-8"))
    assert diagnostic["failure"]["code"] == "capability_process_failed"
    assert diagnostic["secondary_failures"] == [
        {"code": "worker_crash", "message": "secondary worker crash"}
    ]
    assert diagnostic["capability_sha256"] is None


@pytest.mark.parametrize(
    "failure_code",
    [
        RuntimeFailureCode.CAPABILITY_PROTOCOL_ERROR,
        RuntimeFailureCode.EVENT_PROTOCOL_ERROR,
        RuntimeFailureCode.STREAM_LIMIT_EXCEEDED,
        RuntimeFailureCode.OBSERVER_FAILED,
        RuntimeFailureCode.OUTPUT_SECURITY_ERROR,
    ],
)
def test_protocol_limits_and_executor_defects_raise_internal(
    tmp_path: Path,
    failure_code: RuntimeFailureCode,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        failure_code=failure_code,
    )
    context = RecordingContext()

    with pytest.raises(RuntimeError, match="runtime executor"):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, context)

    assert context.events[-1][0] == "replay.diagnostics.published"
    assert context.events[-1][1]


@pytest.mark.parametrize(
    "secondary_code",
    [
        RuntimeFailureCode.EVENT_PROTOCOL_ERROR,
        RuntimeFailureCode.OUTPUT_SECURITY_ERROR,
        RuntimeFailureCode.CAPABILITY_DRIFT,
        RuntimeFailureCode.RUNTIME_ASSET_DRIFT,
    ],
)
def test_secondary_trust_failure_cannot_be_hidden_by_retryable_worker_failure(
    tmp_path: Path,
    secondary_code: RuntimeFailureCode,
) -> None:
    fixture = _fixture(tmp_path)

    def add_secondary(execution: RuntimeExecution) -> RuntimeExecution:
        return replace(
            execution,
            secondary_failures=(RuntimeFailure(secondary_code, "secondary trust failure"),),
        )

    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        failure_code=RuntimeFailureCode.WORKER_CRASH,
        execution_mutation=add_secondary,
    )
    context = RecordingContext()

    if secondary_code in {
        RuntimeFailureCode.CAPABILITY_DRIFT,
        RuntimeFailureCode.RUNTIME_ASSET_DRIFT,
    }:
        with pytest.raises(SkillBlocked) as captured:
            _invoke(_handler(tmp_path, fixture, executor), fixture.value, context)
        assert captured.value.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
        assert captured.value.blocker.retryable is False
        assert captured.value.blocker.details["runtime_failure_code"] == secondary_code.value
    else:
        with pytest.raises(RuntimeError, match="secondary"):
            _invoke(_handler(tmp_path, fixture, executor), fixture.value, context)

    assert context.events[-1][0] == "replay.diagnostics.published"
    snapshot_ref = next(
        ref for ref in context.events[-1][1] if ref.name == "runtime_execution_diagnostics"
    )
    snapshot = json.loads(fixture.store.resolve(snapshot_ref).path.read_text(encoding="utf-8"))
    assert snapshot["failure"]["code"] == "worker_crash"
    assert snapshot["secondary_failures"] == [
        {"code": secondary_code.value, "message": "secondary trust failure"}
    ]


@pytest.mark.parametrize(
    "secondary_code",
    [RuntimeFailureCode.WORKER_TIMEOUT, RuntimeFailureCode.WORKER_CRASH],
)
def test_retryable_secondary_failure_cannot_be_hidden_by_primary_success(
    tmp_path: Path,
    secondary_code: RuntimeFailureCode,
) -> None:
    fixture = _fixture(tmp_path)

    def add_secondary(execution: RuntimeExecution) -> RuntimeExecution:
        return replace(
            execution,
            secondary_failures=(RuntimeFailure(secondary_code, "secondary worker failure"),),
        )

    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        execution_mutation=add_secondary,
    )
    context = RecordingContext()

    with pytest.raises(RuntimeError, match="secondary"):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, context)

    assert len(executor.calls) == 1
    assert context.events[-1][0] == "replay.diagnostics.published"


def test_unknown_future_executor_failure_cannot_silently_become_retryable(
    tmp_path: Path,
) -> None:
    class FutureFailureCode(str, Enum):
        FUTURE = "future_failure"

    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        failure_code=FutureFailureCode.FUTURE,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="unknown failure taxonomy"):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())


@pytest.mark.parametrize(
    "change",
    [
        lambda execution: replace(
            execution,
            status=RuntimeExecutionStatus.FAILED,
            failure=None,
        ),
        lambda execution: replace(execution, exit_code=2),
        lambda execution: replace(execution, capability=None),
        lambda execution: replace(
            execution,
            capability=replace(execution.capability, bytes=1),
        ),
        lambda execution: replace(execution, transcript_complete=False),
        lambda execution: replace(execution, transcript_truncated=True),
        lambda execution: replace(execution, stdout_truncated=True),
        lambda execution: replace(execution, stderr_truncated=True),
    ],
    ids=[
        "inconsistent-status",
        "nonzero-exit",
        "missing-capability",
        "capability-identity",
        "incomplete-transcript",
        "truncated-transcript",
        "truncated-stdout",
        "truncated-stderr",
    ],
)
def test_success_record_must_be_internally_consistent(
    tmp_path: Path,
    change: Callable[[RuntimeExecution], RuntimeExecution],
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        execution_mutation=change,
    )

    with pytest.raises(RuntimeError):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())


@pytest.mark.parametrize(
    "change",
    [
        lambda execution: replace(
            execution,
            output_files=(*execution.output_files, execution.output_files[0]),
        ),
        lambda execution: replace(
            execution,
            output_files=(
                replace(execution.output_files[0], relative_path="not-allowlisted.bin"),
                *execution.output_files[1:],
            ),
        ),
        lambda execution: replace(
            execution,
            output_files=(
                replace(execution.output_files[0], path=execution.attempt_root / "elsewhere"),
                *execution.output_files[1:],
            ),
        ),
        lambda execution: replace(execution, output_files=execution.output_files[:-1]),
    ],
    ids=["duplicate", "not-allowlisted", "path-mismatch", "missing-declared"],
)
def test_runtime_output_collection_is_fail_closed(
    tmp_path: Path,
    change: Callable[[RuntimeExecution], RuntimeExecution],
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        execution_mutation=change,
    )

    with pytest.raises(RuntimeError, match="runtime"):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())


def test_runtime_output_root_rejects_unreported_directory(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    def add_directory(execution: RuntimeExecution) -> RuntimeExecution:
        (execution.attempt_root / "unreported").mkdir()
        return execution

    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        execution_mutation=add_directory,
    )

    with pytest.raises(RuntimeError, match="unsafe entry"):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())


def test_runtime_output_root_must_exist(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    def remove_root(execution: RuntimeExecution) -> RuntimeExecution:
        for path in execution.attempt_root.iterdir():
            path.unlink()
        execution.attempt_root.rmdir()
        return execution

    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        execution_mutation=remove_root,
    )

    with pytest.raises(RuntimeError, match="output root"):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())


@pytest.mark.parametrize(
    ("target_name", "message"),
    [
        ("preview_head.png", "runtime output changed"),
        ("runtime_capability.json", "CAS changed"),
        ("runtime_evidence", "verified runtime output changed"),
    ],
)
def test_cas_publication_rechecks_precomputed_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_name: str,
    message: str,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    original_put = fixture.store.put_file

    def corrupt(source: Path, **kwargs) -> ArtifactRef:
        artifact = original_put(source, **kwargs)
        if source.name == target_name or kwargs.get("name") == target_name:
            return artifact.model_copy(update={"bytes": artifact.bytes + 1})
        return artifact

    monkeypatch.setattr(fixture.store, "put_file", corrupt)

    with pytest.raises(RuntimeError, match=message):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())


def test_video_media_type_is_explicitly_allowlisted(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        {**_capability_document(), "nested": ({"values": [1]},)},
        evidence_mutation=lambda evidence: evidence.update(unique_video_frame_count=4),
    )
    value = fixture.value.model_copy(
        update={
            "runtime_config": fixture.value.runtime_config.model_copy(update={"video_frames": 5})
        }
    )

    handler = _handler(tmp_path, fixture, executor)
    output = Text2EnvReplayOutput.model_validate(_invoke(handler, value, RecordingContext()).output)

    video = next(item for item in output.replay_artifacts if item.name == "observer_runtime")
    assert video.media_type == "video/mp4"
    receipt_ref = next(
        item for item in output.replay_artifacts if item.name == "replay_execution_receipt"
    )
    receipt = json.loads(fixture.store.resolve(receipt_ref).path.read_text(encoding="utf-8"))
    verified_video = receipt["media_verification"]["video"]
    assert verified_video["frame_count"] == 5
    assert verified_video["source_unique_frame_count"] == 4
    assert verified_video["decoded_unique_frame_count"] == 5
    assert (verified_video["width"], verified_video["height"]) == (2, 2)
    assert verified_video["sample_aspect_ratio"] == "1/1"
    assert verified_video["square_sample_aspect_ratio_defaulted"] is False
    assert verified_video["sandbox_metrics"]["memory_peak_bytes"] == 4096
    assert handler.media_verifier.calls[0][1:] == (5, 12)  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="type is not allowlisted"):
        replay_module._runtime_artifact_type("future-format.bin")


def test_corrupt_png_is_never_published_with_a_typed_media_claim(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    def corrupt_png(_job: RuntimeJob, attempt_root: Path) -> None:
        (attempt_root / "preview_head.png").write_bytes(b"not-a-png")

    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        mutation=corrupt_png,
    )

    with pytest.raises(SkillBlocked) as captured:
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())

    blocker = captured.value.blocker
    assert blocker.code == "T2E_REPLAY_FAILED"
    assert blocker.stage == "runtime_media"
    assert blocker.details["reason"] == "png_decode_failed"
    corrupt = next(ref for ref in blocker.artifact_refs if ref.name == "untrusted_preview_head")
    assert corrupt.media_type == "application/octet-stream"
    assert corrupt.schema_version is None
    assert not any(
        ref.name == "preview_head" and ref.media_type == "image/png"
        for ref in blocker.artifact_refs
    )


@pytest.mark.parametrize(
    ("reason", "code", "stage", "retryable"),
    [
        (
            MediaVerificationReason.DEPENDENCY_DRIFT,
            "HARN_DEPENDENCY_UNAVAILABLE",
            "media_verifier",
            False,
        ),
        (
            MediaVerificationReason.SANDBOX_UNAVAILABLE,
            "HARN_DEPENDENCY_UNAVAILABLE",
            "media_verifier",
            False,
        ),
        (
            MediaVerificationReason.DECODE_FAILED,
            "T2E_REPLAY_FAILED",
            "runtime_media",
            True,
        ),
    ],
)
def test_media_verifier_failure_taxonomy_is_typed_before_promotion(
    tmp_path: Path,
    reason: MediaVerificationReason,
    code: str,
    stage: str,
    retryable: bool,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    handler = _handler(tmp_path, fixture, executor)
    handler.media_verifier.failure_reason = reason  # type: ignore[attr-defined]

    with pytest.raises(SkillBlocked) as captured:
        _invoke(handler, fixture.value, RecordingContext())

    blocker = captured.value.blocker
    assert blocker.code == code
    assert blocker.stage == stage
    assert blocker.retryable is retryable
    assert blocker.details["reason"] == reason.value
    assert all(
        ref.media_type == "application/octet-stream"
        for ref in blocker.artifact_refs
        if ref.name.startswith("untrusted_")
    )


def test_media_verifier_success_record_must_match_executor_bytes_and_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    handler = _handler(tmp_path, fixture, executor)
    verifier = handler.media_verifier
    original_verify = verifier.verify

    def lie(*args, **kwargs) -> ReplayMediaVerification:
        verified = original_verify(*args, **kwargs)
        first = replace(verified.pngs[0], sha256="f" * 64)
        return replace(verified, pngs=(first, *verified.pngs[1:]))

    monkeypatch.setattr(verifier, "verify", lie)

    with pytest.raises(SkillBlocked) as captured:
        _invoke(handler, fixture.value, RecordingContext())

    assert captured.value.blocker.code == "T2E_REPLAY_FAILED"
    assert captured.value.blocker.stage == "runtime_evidence"
    assert "PNG facts" in captured.value.blocker.details["error"]


def test_replay_rejects_decoded_video_below_the_promotion_uniqueness_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    handler = _handler(tmp_path, fixture, executor)
    verifier = handler.media_verifier
    original_verify = verifier.verify

    def repeat_frames(*args, **kwargs) -> ReplayMediaVerification:
        verified = original_verify(*args, **kwargs)
        assert verified.video is not None
        return replace(verified, video=replace(verified.video, unique_frame_count=1))

    monkeypatch.setattr(verifier, "verify", repeat_frames)
    value = fixture.value.model_copy(
        update={
            "runtime_config": fixture.value.runtime_config.model_copy(update={"video_frames": 31})
        }
    )

    with pytest.raises(SkillBlocked) as captured:
        _invoke(handler, value, RecordingContext())

    blocker = captured.value.blocker
    assert blocker.code == "T2E_REPLAY_FAILED"
    assert blocker.retryable is True
    assert "unique" in blocker.details["error"]
    assert not any(
        ref.name == "observer_runtime" and ref.media_type == "video/mp4"
        for ref in blocker.artifact_refs
    )


def test_media_verification_rejects_non_record_and_identity_drift(tmp_path: Path) -> None:
    verified, identity, outputs, evidence, config = _media_contract_case(
        tmp_path,
        video_frames=0,
    )
    with pytest.raises(ValueError, match="invalid success record"):
        replay_module._verify_media_verification(
            object(),  # type: ignore[arg-type]
            configured_identity=identity,
            output_files=outputs,
            evidence=evidence,
            config=config,
        )
    with pytest.raises(ValueError, match="identity changed"):
        replay_module._verify_media_verification(
            replace(
                verified,
                verifier_identity=_media_identity(implementation_sha256="6" * 64),
            ),
            configured_identity=identity,
            output_files=outputs,
            evidence=evidence,
            config=config,
        )


def test_media_verification_rejects_incomplete_png_set_and_unexpected_video(
    tmp_path: Path,
) -> None:
    verified, identity, outputs, evidence, config = _media_contract_case(
        tmp_path,
        video_frames=0,
    )
    with pytest.raises(ValueError, match="PNG set"):
        replay_module._verify_media_verification(
            replace(verified, pngs=tuple(reversed(verified.pngs))),
            configured_identity=identity,
            output_files=outputs,
            evidence=evidence,
            config=config,
        )
    fake_video = VideoMediaVerification(
        relative_path="observer_runtime.mp4",
        sha256="a" * 64,
        bytes=1,
        frame_count=1,
        unique_frame_count=1,
        fps_numerator=12,
        fps_denominator=1,
        width=2,
        height=2,
        format_name="iso-bmff/mp4",
        codec_name="h264",
        pixel_format="8bit-420",
        sample_aspect_ratio="1/1",
        square_sample_aspect_ratio_defaulted=False,
        sandbox_metrics=_sandbox_metrics(),
    )
    with pytest.raises(ValueError, match="unexpectedly returned a video"):
        replay_module._verify_media_verification(
            replace(verified, video=fake_video),
            configured_identity=identity,
            output_files=outputs,
            evidence=evidence,
            config=config,
        )


def test_media_verification_rejects_omitted_or_unbound_video(tmp_path: Path) -> None:
    verified, identity, outputs, evidence, config = _media_contract_case(
        tmp_path,
        video_frames=5,
    )
    with pytest.raises(ValueError, match="omitted"):
        replay_module._verify_media_verification(
            replace(verified, video=None),
            configured_identity=identity,
            output_files=outputs,
            evidence=evidence,
            config=config,
        )
    outputs.pop("observer_runtime.mp4")
    with pytest.raises(ValueError, match="absent"):
        replay_module._verify_media_verification(
            verified,
            configured_identity=identity,
            output_files=outputs,
            evidence=evidence,
            config=config,
        )


def test_media_verification_rejects_false_video_facts_and_dimension_mismatch(
    tmp_path: Path,
) -> None:
    verified, identity, outputs, evidence, config = _media_contract_case(
        tmp_path,
        video_frames=5,
    )
    assert verified.video is not None
    with pytest.raises(ValueError, match="video facts"):
        replay_module._verify_media_verification(
            replace(verified, video=replace(verified.video, frame_count=4)),
            configured_identity=identity,
            output_files=outputs,
            evidence=evidence,
            config=config,
        )
    changed_png = replace(verified.pngs[4], width=3)
    with pytest.raises(ValueError, match="dimensions disagree"):
        replay_module._verify_media_verification(
            replace(
                verified,
                pngs=(*verified.pngs[:4], changed_png, *verified.pngs[5:]),
            ),
            configured_identity=identity,
            output_files=outputs,
            evidence=evidence,
            config=config,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("format_name", "not-mp4"),
        ("codec_name", "not-h264"),
        ("pixel_format", "not-8bit-420"),
        ("sample_aspect_ratio", "9/7"),
        ("square_sample_aspect_ratio_defaulted", True),
        ("square_sample_aspect_ratio_defaulted", 1),
    ],
)
def test_media_verification_rejects_false_decoder_metadata(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    verified, identity, outputs, evidence, config = _media_contract_case(
        tmp_path,
        video_frames=5,
    )
    assert verified.video is not None

    with pytest.raises(ValueError, match="video facts"):
        replay_module._verify_media_verification(
            replace(
                verified,
                video=replace(verified.video, **{field_name: value}),
            ),
            configured_identity=identity,
            output_files=outputs,
            evidence=evidence,
            config=config,
        )


def test_media_verification_accepts_undeclared_square_sample_aspect_ratio(
    tmp_path: Path,
) -> None:
    verified, identity, outputs, evidence, config = _media_contract_case(
        tmp_path,
        video_frames=5,
    )
    assert verified.video is not None

    replay_module._verify_media_verification(
        replace(
            verified,
            video=replace(
                verified.video,
                sample_aspect_ratio="0/1",
                square_sample_aspect_ratio_defaulted=True,
            ),
        ),
        configured_identity=identity,
        output_files=outputs,
        evidence=evidence,
        config=config,
    )


def test_precheck_steps_are_counted_in_events_but_not_video_indices(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    value = fixture.value.model_copy(
        update={
            "runtime_config": fixture.value.runtime_config.model_copy(update={"precheck_steps": 2})
        }
    )
    context = RecordingContext()

    output = Text2EnvReplayOutput.model_validate(
        _invoke(_handler(tmp_path, fixture, executor), value, context).output
    )

    evidence = json.loads(
        fixture.store.resolve(output.runtime_evidence).path.read_text(encoding="utf-8")
    )
    assert evidence["base_simulation_step_count"] == 5
    assert evidence["simulation_step_count"] == 5
    assert evidence["total_physics_step_count"] == 7
    assert "replay.simulation.completed.steps-7" in [stage for stage, _ in context.events]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda evidence: evidence.update(schema_version="wrong"),
        lambda evidence: evidence.update(resolved_scene_sha256="f" * 64),
        lambda evidence: evidence.pop("runtime_asset_snapshot_sha256"),
        lambda evidence: evidence.update(base_simulation_step_count=4),
        lambda evidence: evidence.update(simulation_step_count=4),
        lambda evidence: evidence.update(video_sample_step_indices=[5]),
        lambda evidence: evidence.update(status="incomplete"),
        lambda evidence: evidence.update(status="fail"),
        lambda evidence: evidence.update(settle_extra_steps=-1),
        lambda evidence: evidence.update(settle_extra_steps=True),
        lambda evidence: evidence.update(settle_converge_max=1),
        lambda evidence: evidence.update(simulation_step_count=True),
        lambda evidence: evidence.update(total_physics_step_count=4),
        lambda evidence: evidence.update(total_physics_step_count=True),
        lambda evidence: evidence.update(video_frame_count=True),
        lambda evidence: evidence.update(unique_video_frame_count=True),
        lambda evidence: evidence.update(video_sample_step_indices={}),
        lambda evidence: evidence.update(images={"head": "/tmp/preview_head.png"}),
        lambda evidence: evidence.update(video="/tmp/observer_runtime.mp4"),
    ],
    ids=[
        "schema",
        "digest",
        "runtime-asset-snapshot",
        "event-timeline",
        "count-timeline",
        "video-timeline",
        "status",
        "physical-failure-status",
        "negative-extra-steps",
        "boolean-extra-steps",
        "adaptive-settle-enabled",
        "boolean-simulation-steps",
        "total-physics-count",
        "boolean-total-physics-count",
        "boolean-frame-count",
        "boolean-unique-frames",
        "indices-not-list",
        "absolute-image-locator",
        "absolute-video-locator",
    ],
)
def test_incomplete_or_unbound_runtime_evidence_is_retryable(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        evidence_mutation=mutation,
    )

    with pytest.raises(SkillBlocked) as captured:
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())

    assert captured.value.blocker.code == "T2E_REPLAY_FAILED"
    assert captured.value.blocker.stage == "runtime_evidence"
    assert captured.value.blocker.retryable is True
    untrusted = next(
        ref
        for ref in captured.value.blocker.artifact_refs
        if ref.name == "untrusted_runtime_evidence"
    )
    assert untrusted.schema_version is None
    assert all(ref.name != "runtime_evidence" for ref in captured.value.blocker.artifact_refs)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report.update(schema_version="wrong"),
        lambda report: report.update(checks=[]),
        lambda report: report.update(checks=["bad"]),
        lambda report: report.update(fail_count=True),
        lambda report: report.update(not_run_count=1),
        lambda report: report.update(status="pass"),
    ],
    ids=["identity", "empty-checks", "invalid-check", "typed-counts", "count", "status"],
)
def test_invalid_runtime_validation_report_is_retryable_evidence_failure(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        validation_mutation=mutation,
    )

    with pytest.raises(SkillBlocked) as captured:
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())

    assert captured.value.blocker.code == "T2E_REPLAY_FAILED"
    assert captured.value.blocker.stage == "runtime_evidence"
    assert captured.value.blocker.artifact_refs


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b'{"schema_version":"a","schema_version":"b"}',
        b'{"schema_version":NaN}',
    ],
    ids=["not-object", "duplicate-key", "non-standard-number"],
)
def test_malformed_runtime_evidence_is_retained_as_retryable_diagnostic(
    tmp_path: Path,
    payload: bytes,
) -> None:
    fixture = _fixture(tmp_path)

    def replace_evidence(_job: RuntimeJob, attempt_root: Path) -> None:
        (attempt_root / "runtime_evidence.json").write_bytes(payload)

    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        mutation=replace_evidence,
    )

    with pytest.raises(SkillBlocked) as captured:
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())

    assert captured.value.blocker.code == "T2E_REPLAY_FAILED"
    assert captured.value.blocker.artifact_refs


@pytest.mark.parametrize(
    "executor_change",
    [
        {"corrupt_precomputed_digest": True},
        {"omit_observer_callbacks": True},
    ],
    ids=["precomputed-digest", "missing-live-callbacks"],
)
def test_executor_contract_inconsistency_raises_internal(
    tmp_path: Path,
    executor_change: dict[str, bool],
) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(
        tmp_path / "runtime",
        _capability_document(),
        **executor_change,
    )

    with pytest.raises(RuntimeError):
        _invoke(_handler(tmp_path, fixture, executor), fixture.value, RecordingContext())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda events: events[:6],
        lambda events: (replace(events[0], kind=RuntimeEventKind.SCENE_LOADED), *events[1:]),
        lambda events: tuple(
            replace(event, kind=RuntimeEventKind.SIMULATION_CHECKPOINT)
            if event.kind is RuntimeEventKind.SIMULATION_COMPLETED
            else event
            for event in events
        ),
        lambda events: (
            *events[:3],
            replace(events[3], kind=RuntimeEventKind.SCENE_LOADED),
            *events[4:],
        ),
        lambda events: (
            *events[:4],
            replace(events[4], completed_steps=4),
            *events[5:],
        ),
        lambda events: (*events[:5], events[6], events[5], events[7]),
        lambda events: (
            *events[:5],
            replace(events[5], artifact_paths=()),
            *events[6:],
        ),
        lambda events: (
            *events[:6],
            replace(events[6], artifact_paths=("runtime_evidence.json",)),
            *events[7:],
        ),
    ],
    ids=[
        "too-short",
        "preamble",
        "no-completion",
        "simulation-phase",
        "completion-steps",
        "tail-order",
        "artifact-declaration",
        "evidence-declaration",
    ],
)
def test_semantic_event_lifecycle_rejects_false_progress(
    mutate: Callable[[tuple[RuntimeEvent, ...]], tuple[RuntimeEvent, ...]],
) -> None:
    config = RuntimeConfig(
        precheck_steps=0,
        settle_steps=5,
        contact_window_steps=3,
        video_frames=0,
        fps=12,
    )

    with pytest.raises(RuntimeError, match="runtime"):
        replay_module._verify_event_lifecycle(
            mutate(_valid_events()),
            config,
            checkpoint_steps=4,
        )


def test_checkpoint_steps_must_strictly_increase() -> None:
    events = _valid_events()
    duplicate = RuntimeEvent(
        seq=5,
        kind=RuntimeEventKind.SIMULATION_CHECKPOINT,
        completed_steps=events[3].completed_steps,
    )
    changed = (*events[:4], duplicate, *events[4:])

    with pytest.raises(RuntimeError, match="checkpoint"):
        replay_module._verify_event_lifecycle(
            changed,
            RuntimeConfig(
                precheck_steps=0,
                settle_steps=5,
                contact_window_steps=3,
                video_frames=0,
                fps=12,
            ),
            checkpoint_steps=4,
        )


def test_checkpoint_sequence_includes_exact_divisible_total() -> None:
    codec = RuntimeEventCodec(allowed_artifact_paths=RUNTIME_ARTIFACT_PATHS)
    checkpoint_steps = tuple(range(120, 961, 120))
    events = [
        codec.event(seq=1, kind=RuntimeEventKind.PREFLIGHT_COMPLETED),
        codec.event(seq=2, kind=RuntimeEventKind.SCENE_LOADED),
        codec.event(seq=3, kind=RuntimeEventKind.SIMULATION_STARTED),
    ]
    events.extend(
        codec.event(
            seq=len(events) + 1,
            kind=RuntimeEventKind.SIMULATION_CHECKPOINT,
            completed_steps=completed_steps,
        )
        for completed_steps in checkpoint_steps
    )
    events.extend(
        (
            codec.event(
                seq=len(events) + 1,
                kind=RuntimeEventKind.SIMULATION_COMPLETED,
                completed_steps=960,
            ),
            codec.event(
                seq=len(events) + 2,
                kind=RuntimeEventKind.MEDIA_COMPLETED,
                artifact_paths=RUNTIME_MEDIA_ARTIFACT_PATHS[:4],
            ),
            codec.event(
                seq=len(events) + 3,
                kind=RuntimeEventKind.EVIDENCE_COMPLETED,
                artifact_paths=RUNTIME_EVIDENCE_ARTIFACT_PATHS,
            ),
            codec.event(seq=len(events) + 4, kind=RuntimeEventKind.WORKER_COMPLETED),
        )
    )

    replay_module._verify_event_lifecycle(
        tuple(events),
        RuntimeConfig(
            precheck_steps=60,
            settle_steps=900,
            contact_window_steps=120,
            video_frames=0,
            fps=12,
        ),
        checkpoint_steps=120,
    )


def test_catalog_robotwin_root_must_be_canonical_and_allowlisted(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    catalog = load_catalog(
        fixture.store.resolve(fixture.value.environment_package.asset_catalog).path
    )

    assert replay_module._selected_robotwin_root(catalog, (fixture.robotwin_root,)) == (
        fixture.robotwin_root
    )
    with pytest.raises(ValueError, match="escapes"):
        replay_module._selected_robotwin_root(catalog, (tmp_path / "cas",))
    with pytest.raises(ValueError, match="canonical"):
        replay_module._selected_robotwin_root(
            catalog.model_copy(update={"robotwin_root": "relative"}),
            (fixture.robotwin_root,),
        )

    missing = fixture.robotwin_root / "missing"
    with pytest.raises(ValueError, match="unavailable"):
        replay_module._selected_robotwin_root(
            catalog.model_copy(update={"robotwin_root": str(missing)}),
            (fixture.robotwin_root,),
        )
    linked = fixture.robotwin_root / "linked"
    linked.symlink_to(fixture.robotwin_root, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        replay_module._selected_robotwin_root(
            catalog.model_copy(update={"robotwin_root": str(linked)}),
            (fixture.robotwin_root,),
        )


def test_output_snapshot_requires_a_regular_file(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()

    with pytest.raises(RuntimeError, match="regular file"):
        replay_module._snapshot_output_file(directory)


def test_allowed_asset_root_must_not_be_missing_or_traverse_symlinks(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        replay_module._normalized_allowed_roots(())
    with pytest.raises(ValueError, match="Path values"):
        replay_module._normalized_allowed_roots(("bad",))  # type: ignore[arg-type]
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="allowed asset root"):
        replay_module._normalized_allowed_roots((missing,))

    real_parent = tmp_path / "real"
    child = real_parent / "child"
    child.mkdir(parents=True)
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="traverses a symlink"):
        replay_module._normalized_allowed_roots((linked_parent / "child",))
    regular = tmp_path / "regular"
    regular.write_bytes(b"not a directory")
    with pytest.raises(ValueError, match="real directory"):
        replay_module._normalized_allowed_roots((regular,))
    with pytest.raises(ValueError, match="unique"):
        replay_module._normalized_allowed_roots((child, child))


@pytest.mark.parametrize(
    ("identity", "message"),
    [
        (object(), "unavailable"),
        (
            type("TextIdentity", (), {"canonical_bytes": "not-bytes", "sha256": "a" * 64})(),
            "must be bytes",
        ),
        (
            type("BadDigestIdentity", (), {"canonical_bytes": b"{}", "sha256": "bad"})(),
            "sha256 is invalid",
        ),
        (
            type("MismatchIdentity", (), {"canonical_bytes": b"{}", "sha256": "a" * 64})(),
            "digest is inconsistent",
        ),
        (
            type(
                "MalformedIdentity",
                (),
                {
                    "canonical_bytes": b"{",
                    "sha256": hashlib.sha256(b"{").hexdigest(),
                },
            )(),
            "invalid JSON",
        ),
        (
            type(
                "ListIdentity",
                (),
                {
                    "canonical_bytes": b"[]",
                    "sha256": hashlib.sha256(b"[]").hexdigest(),
                },
            )(),
            "one JSON object",
        ),
    ],
)
def test_media_identity_contract_is_fail_closed(identity: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        replay_module._media_identity(identity)


def test_runtime_asset_dependency_requires_a_snapshot_record() -> None:
    with pytest.raises(TypeError, match="snapshot"):
        replay_module.text2env_replay_runtime_asset_dependency(object())  # type: ignore[arg-type]


def test_attempt_directory_is_immutable(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    executor = RecordingExecutor(tmp_path / "runtime", _capability_document())
    handler = _handler(tmp_path, fixture, executor)
    _invoke(handler, fixture.value, RecordingContext())

    with pytest.raises(SkillBlocked) as captured:
        _invoke(handler, fixture.value, RecordingContext())

    assert captured.value.blocker.code == "T2E_PACKAGE_INVALID"
    assert captured.value.blocker.stage == "package_materialization"
    assert len(executor.calls) == 1


def test_descriptor_is_exact_and_retries_one_failed_execution() -> None:
    qualification = ArtifactRef(
        name="qualification",
        uri="artifact://sha256/" + "a" * 64,
        media_type="application/json",
        sha256="a" * 64,
        bytes=10,
        schema_version="harness.skill_qualification.v1",
    )

    descriptor = text2env_replay_descriptor(
        qualification_artifact=qualification,
        implementation_sha256="b" * 64,
    )

    assert descriptor.skill_id == "text2env.replay"
    assert descriptor.version == "1.0.0"
    assert descriptor.max_attempts == 2
    assert descriptor.input_schema == "harness.text2env_replay_input.v1"
    assert descriptor.output_schema == "harness.text2env_replay_output.v1"
