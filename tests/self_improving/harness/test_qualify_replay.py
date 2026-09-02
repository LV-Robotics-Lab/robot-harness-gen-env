from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

import self_improving.harness.qualify_replay as module
import self_improving.harness.replay_application as replay_application_module
import self_improving.harness.replay_qualification as replay_qualification_module
from scene_gen import CompileRequest, compile_scene
from scene_gen.builder import build_scene_package
from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.schema import ResolvedSceneSpec
from scene_gen.validator import validate_resolved_scene
from self_improving.harness import (
    LocalArtifactStore,
    load_replay_qualification_bundle,
    verify_replay_qualification_bundle,
)
from self_improving.harness.artifacts import ArtifactResolutionError
from self_improving.harness.handlers.text2env_replay import (
    Text2EnvReplayHandler,
    Text2EnvReplayWiring,
)
from self_improving.harness.qualification import QualificationCheckV1
from self_improving.harness.qualify_replay import (
    ReplayQualificationGenerationError,
    ReplayQualificationSettings,
    generate_replay_qualification,
)
from self_improving.harness.registry import HandlerResult
from self_improving.harness.replay_application import (
    ReplayApplicationConfigurationError,
    ReplayApplicationSettings,
    create_replay_application,
)
from self_improving.harness.replay_dependencies import Text2EnvReplayDependencyResolver
from self_improving.harness.runtime_capability import (
    EXPECTED_RUNTIME_PARAMETERS,
    REQUIRED_RUNTIME_DISTRIBUTIONS,
    RUNTIME_ARTIFACT_PATHS,
    RUNTIME_ASSET_SNAPSHOT_PROTOCOL,
    RUNTIME_CAPABILITY_SCHEMA,
    RUNTIME_ENVIRONMENT_KEYS,
    RUNTIME_EVIDENCE_ARTIFACT_PATHS,
    RUNTIME_MEDIA_ARTIFACT_PATHS,
    canonical_capability_bytes,
)
from self_improving.harness.runtime_events import RuntimeEventCodec, RuntimeEventKind
from self_improving.harness.runtime_executor import RuntimeExecution, RuntimeExecutionStatus
from self_improving.harness.schemas import (
    ArtifactRef,
    DependencyRef,
    EnvironmentPackage,
    Event,
    Invocation,
    RunState,
    RunStatus,
    Text2EnvCompileOutput,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)

ROOT = Path(__file__).resolve().parents[3]
SCENE_GEN = ROOT / "scene_gen"
LEDGER = ROOT / "self_improving/asset_pipeline/active/1_asset_reuse/lib"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
SHA_1 = "1" * 64
SHA_2 = "2" * 64
SHA_3 = "3" * 64
SHA_4 = "4" * 64
SHA_5 = "5" * 64
SHA_6 = "6" * 64
SHA_7 = "7" * 64
SHA_8 = "8" * 64
EVENT_KINDS = (
    "preflight.completed",
    "scene.loaded",
    "simulation.started",
    *("simulation.checkpoint",) * 7,
    "simulation.completed",
    "media.completed",
    "evidence.completed",
    "worker.completed",
)
CHECKPOINTS = (120, 240, 360, 480, 600, 720, 840)
MEDIA_IDENTITY_DOCUMENT = {"schema_version": "test.media_verifier_identity.v1"}
EXECUTOR_IDENTITY_DOCUMENT = {"schema_version": "test.runtime_executor_identity.v1"}


def _identity_sha256(document: dict[str, object]) -> str:
    return hashlib.sha256(
        (
            json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
    ).hexdigest()


MEDIA_IDENTITY_SHA256 = _identity_sha256(MEDIA_IDENTITY_DOCUMENT)
EXECUTOR_IDENTITY_SHA256 = _identity_sha256(EXECUTOR_IDENTITY_DOCUMENT)


def _settings(tmp_path: Path, *, suffix: str = "") -> ReplayQualificationSettings:
    delegated = tmp_path / f"delegated{suffix}"
    delegated.mkdir()
    capability = tmp_path / f"capability{suffix}.json"
    launcher = tmp_path / f"launcher{suffix}"
    launcher.write_bytes(b"launcher")
    ffmpeg = tmp_path / f"ffmpeg{suffix}"
    ffmpeg.write_bytes(b"ffmpeg")
    capability.write_bytes(
        canonical_capability_bytes(
            _capability_document(
                runner=ROOT / "script/run_scene_runtime.py",
                interpreter=Path(sys.executable),
                ffmpeg=ffmpeg,
            )
        )
    )
    return ReplayQualificationSettings(
        bundle_root=tmp_path / f"bundle{suffix}",
        scratch_root=tmp_path / f"scratch{suffix}",
        distribution_root=ROOT,
        scene_gen_root=SCENE_GEN,
        ledger_contract_root=LEDGER,
        asset_catalog_path=ROOT / "tests/fixtures/asset_catalog.json",
        allowed_asset_roots=(ROOT / "tests/fixtures",),
        interpreter=Path(sys.executable).resolve(),
        runtime_runner=ROOT / "script/run_scene_runtime.py",
        runtime_module_root=ROOT,
        runtime_capability_path=capability,
        media_launcher=launcher,
        static_ffmpeg=ffmpeg,
        delegated_cgroup_root=delegated,
        runtime_timeout_seconds=1800.0,
        capability_timeout_seconds=30.0,
    )


def _write_settings_manifest(path: Path, settings: ReplayQualificationSettings) -> None:
    value = {
        field.name: (
            [str(item) for item in getattr(settings, field.name)]
            if field.name == "allowed_asset_roots"
            else (
                str(getattr(settings, field.name))
                if isinstance(getattr(settings, field.name), Path)
                else getattr(settings, field.name)
            )
        )
        for field in fields(settings)
    }
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _capability_document(
    *,
    runner: Path,
    interpreter: Path,
    ffmpeg: Path,
) -> dict[str, object]:
    return {
        "schema_version": RUNTIME_CAPABILITY_SCHEMA,
        "protocol_version": 1,
        "backend_id": "robotwin.sapien",
        "runner_sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
        "python": {
            "implementation": "CPython",
            "version": "3.13.0",
            "executable_sha256": hashlib.sha256(interpreter.resolve().read_bytes()).hexdigest(),
        },
        "packages": {
            name: {
                "version": "1.0",
                "record_sha256": SHA_7,
                "direct_url_sha256": None,
            }
            for name in REQUIRED_RUNTIME_DISTRIBUTIONS
        },
        "runtime_binaries": {
            "ffmpeg": {
                "sha256": hashlib.sha256(ffmpeg.read_bytes()).hexdigest(),
                "bytes": ffmpeg.stat().st_size,
            }
        },
        "accelerator": {
            "probe": {"kind": "nvidia-smi.query-gpu.v1", "sha256": SHA_8, "bytes": 1},
            "gpus": [{"uuid": "GPU-1", "name": "fixture", "driver_version": "1"}],
            "selection_environment": {
                name: {
                    "present": name in os.environ,
                    "value_sha256": (
                        hashlib.sha256(os.environ[name].encode()).hexdigest()
                        if name in os.environ
                        else None
                    ),
                }
                for name in RUNTIME_ENVIRONMENT_KEYS
            },
        },
        "bootstrap": {
            "kind": "python.import_base_task.v1",
            "source_sha256": SHA_D,
            "command_sha256": SHA_E,
        },
        "source_modules": {
            "scene_gen": {"tree_sha256": SHA_5},
            "runtime_events": {"sha256": SHA_6},
            "runtime_capability": {"sha256": SHA_7},
            "runtime_assets": {"sha256": SHA_8},
        },
        "robotwin": {
            "commit": "a" * 40,
            "dirty": False,
            "tracked_diff_sha256": SHA_1,
            "untracked_manifest_sha256": SHA_2,
            "tree_state_sha256": SHA_3,
        },
        "task_config": {
            "path": "task_config/demo_clean.yml",
            "sha256": SHA_4,
            "registry": {
                "path": "env_cfg/task_config/_embodiment_config.yml",
                "sha256": SHA_5,
            },
            "import_resources": [
                {
                    "path": "assets/objects/objaverse/list.json",
                    "sha256": SHA_6,
                    "bytes": 1,
                },
                {"path": "assets/objects/same.json", "sha256": SHA_7, "bytes": 1},
            ],
            "embodiment_closure": {
                "strategy": "selected_embodiment_tree.v1",
                "selection": ["panda"],
                "resources": [
                    {
                        "name": "panda",
                        "path": "assets/embodiments/panda",
                        "tree_sha256": SHA_8,
                        "file_count": 1,
                        "bytes": 1,
                        "required_files": [{"path": "config.yml", "sha256": SHA_A}],
                    }
                ],
            },
        },
        "supported": {
            "resolved_scene_schemas": ["robotwin.resolved_scene.v1"],
            "asset_catalog_schemas": ["robotwin.asset_catalog.v1"],
            "runtime_evidence_schemas": ["robotwin.scene_runtime_evidence.v2"],
            "validation_report_schemas": ["robotwin.scene_validation.v1"],
            "parameters": EXPECTED_RUNTIME_PARAMETERS,
            "event_protocol": {
                "schema_version": "harness.runtime_event.v1",
                "transport": "dedicated_fd_jsonl",
                "kinds": [kind.value for kind in RuntimeEventKind],
                "artifact_paths": list(RUNTIME_ARTIFACT_PATHS),
            },
            "asset_snapshot": RUNTIME_ASSET_SNAPSHOT_PROTOCOL,
        },
    }


def _dependencies() -> list[dict[str, object]]:
    return [
        {"name": "text2env.replay.capability", "version": "1", "sha256": SHA_A},
        {"name": "text2env.replay.executor", "version": "1", "sha256": SHA_C},
        {"name": "text2env.replay.handler_config", "version": "1", "sha256": SHA_D},
        {"name": "text2env.replay.media_verifier", "version": "1", "sha256": SHA_F},
        {"name": "text2env.replay.runtime_assets", "version": "1", "sha256": SHA_E},
    ]


def _lifecycle(transcript: str) -> dict[str, object]:
    return {
        "transcript_sha256": transcript,
        "event_count": 14,
        "event_kinds": list(EVENT_KINDS),
        "checkpoint_completed_steps": list(CHECKPOINTS),
        "simulation_completed_steps": 900,
        "transcript_complete": True,
        "live_observer_matched": True,
        "streams_complete": True,
    }


def _media(video: str, unique: int) -> dict[str, object]:
    return {
        "video_sha256": video,
        "fully_decoded": True,
        "frame_count": 120,
        "source_unique_frame_count": unique,
        "decoded_unique_frame_count": unique,
        "fps_numerator": 12,
        "fps_denominator": 1,
        "width": 320,
        "height": 240,
        "format_name": "iso-bmff/mp4",
        "codec_name": "h264",
        "pixel_format": "8bit-420",
        "sample_aspect_ratio": "0/1",
        "square_sample_aspect_ratio_defaulted": True,
        "decoded_png_count": 7,
        "all_pngs_decoded": True,
    }


def _physics(evidence: str, report: str, unique: int) -> dict[str, object]:
    return {
        "runtime_evidence_sha256": evidence,
        "validation_report_sha256": report,
        "status": "pass",
        "fail_count": 0,
        "not_run_count": 0,
        "resolved_scene_sha256": SHA_B,
        "settle_steps": 900,
        "contact_window_steps": 120,
        "video_frame_count": 120,
        "unique_video_frame_count": unique,
    }


def _execution(
    *,
    mode: str,
    evidence: str,
    receipt: str,
    output: str,
    report: str,
    transcript: str,
    video: str,
) -> dict[str, object]:
    return {
        "mode": mode,
        "status": "succeeded",
        "attempt_count": 1,
        "handler_call_count": 1,
        "execution_receipt_sha256": receipt,
        "output_sha256": output,
        "runtime_evidence_sha256": evidence,
        "validation_report_sha256": report,
        "event_transcript_sha256": transcript,
        "runtime_asset_manifest_sha256": SHA_E,
        "video_sha256": video,
        "environment_package_id": SHA_B,
        "dependencies": _dependencies(),
        "cas_reread_verified": True,
        "all_artifacts_verified": True,
    }


def _valid_execution(
    tmp_path: Path,
    settings: ReplayQualificationSettings,
) -> module._FixedCaseExecution:
    return _fixed_execution_fixture(tmp_path, settings)


def _install_execution(
    monkeypatch: pytest.MonkeyPatch,
    execution: module._FixedCaseExecution,
) -> None:
    monkeypatch.setattr(module, "_execute_fixed_case", lambda settings, scratch_root: execution)


def _put_bytes(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    path = root / f"{name}-{hashlib.sha256(payload).hexdigest()[:8]}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return store.put_file(
        path,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


def _put_json(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    value: object,
    schema_version: str,
) -> ArtifactRef:
    return _put_bytes(
        store,
        root,
        name=name,
        payload=(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode(),
        media_type="application/json",
        schema_version=schema_version,
    )


def _compile_fixture(
    store: LocalArtifactStore,
    root: Path,
) -> Text2EnvCompileOutput:
    compiled = compile_scene(
        CompileRequest(
            request="Place a can on top of a plate.",
            seed=7,
            asset_catalog_path=ROOT / "tests/fixtures/asset_catalog.json",
            out_root=root / "fixture-compile",
        )
    )
    scene_model = compiled.scene_spec
    catalog_model = load_catalog(ROOT / "tests/fixtures/asset_catalog.json")
    resolved_model = compiled.resolved_scene.model_copy(
        update={"asset_catalog_sha256": catalog_model.digest()}
    )
    source_root = root / "runtime-asset-sources"
    resolved_objects = []
    for item in resolved_model.objects:
        source_files = []
        for index, _source in enumerate(item.source_files):
            path = source_root / item.object_id / f"source-{index}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{item.object_id}-{index}".encode())
            source_files.append(str(path.resolve()))
        resolved_objects.append(item.model_copy(update={"source_files": tuple(source_files)}))
    resolved_model = resolved_model.model_copy(update={"objects": tuple(resolved_objects)})
    package_root = root / "package"
    build_scene_package(scene_model, resolved_model, package_root)

    catalog_path = root / "effective_asset_catalog.canonical.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(
            catalog_model.canonical_dict(),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    catalog = store.put_file(
        catalog_path,
        name="effective_asset_catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    published_members: dict[str, ArtifactRef] = {}
    for relative, media_type, schema_version in (
        ("request.txt", "text/plain", None),
        ("scene_spec.json", "application/json", "robotwin.scene_spec.v1"),
        ("resolved_scene.json", "application/json", "robotwin.resolved_scene.v1"),
        ("generated_scene.py", "text/x-python", None),
    ):
        published_members[relative] = store.put_file(
            package_root / relative,
            name=Path(relative).stem,
            media_type=media_type,
            schema_version=schema_version,
        )
    scene = published_members["scene_spec.json"]
    resolved = published_members["resolved_scene.json"]
    manifest = store.put_file(
        package_root / "package_manifest.json",
        name="package_manifest",
        media_type="application/json",
        schema_version="robotwin.generated_scene_package.v1",
    )
    static_validation = _put_json(
        store,
        root,
        name="static_validation",
        value={"schema_version": "robotwin.scene_validation.v1", "status": "incomplete"},
        schema_version="robotwin.scene_validation.v1",
    )
    package = EnvironmentPackage(
        package_id=resolved_model.digest(),
        route_id="text2env",
        producer_skill_ref="text2env.compile@1.0.0",
        seed=7,
        scene_spec_sha256=scene_model.digest(),
        resolved_scene_sha256=resolved_model.digest(),
        asset_catalog=catalog,
        package_manifest=manifest,
    )
    return Text2EnvCompileOutput(
        scene_spec=scene,
        resolved_scene=resolved,
        environment_package=package,
        static_validation=static_validation,
    )


def _runtime_events() -> tuple[tuple[object, ...], bytes]:
    codec = RuntimeEventCodec(allowed_artifact_paths=RUNTIME_ARTIFACT_PATHS)
    events = [
        codec.event(seq=1, kind=RuntimeEventKind.PREFLIGHT_COMPLETED),
        codec.event(seq=2, kind=RuntimeEventKind.SCENE_LOADED),
        codec.event(seq=3, kind=RuntimeEventKind.SIMULATION_STARTED),
    ]
    for step in CHECKPOINTS:
        events.append(
            codec.event(
                seq=len(events) + 1,
                kind=RuntimeEventKind.SIMULATION_CHECKPOINT,
                completed_steps=step,
            )
        )
    events.extend(
        (
            codec.event(
                seq=len(events) + 1,
                kind=RuntimeEventKind.SIMULATION_COMPLETED,
                completed_steps=900,
            ),
            codec.event(
                seq=len(events) + 2,
                kind=RuntimeEventKind.MEDIA_COMPLETED,
                artifact_paths=RUNTIME_MEDIA_ARTIFACT_PATHS,
            ),
            codec.event(
                seq=len(events) + 3,
                kind=RuntimeEventKind.EVIDENCE_COMPLETED,
                artifact_paths=RUNTIME_EVIDENCE_ARTIFACT_PATHS,
            ),
            codec.event(seq=len(events) + 4, kind=RuntimeEventKind.WORKER_COMPLETED),
        )
    )
    typed = tuple(events)
    return typed, b"".join(codec.encode(event) for event in typed)


def _harness_state(
    *,
    run_id: UUID,
    invocation_digest: str,
    output: Text2EnvReplayOutput,
    artifacts: tuple[ArtifactRef, ...],
    stage_prefix: str = "",
) -> RunState:
    timestamp = datetime(2026, 8, 31, 4, tzinfo=timezone.utc)
    return RunState(
        run_id=run_id,
        invocation_digest=invocation_digest,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        status=RunStatus.SUCCEEDED,
        attempt=1,
        max_attempts=2,
        started_at=timestamp,
        ended_at=timestamp,
        events=(
            Event(
                seq=1,
                timestamp=timestamp,
                stage=f"{stage_prefix}preflight",
                attempt=1,
                from_status=None,
                to_status=RunStatus.RUNNING,
                artifact_refs=(),
            ),
            Event(
                seq=2,
                timestamp=timestamp,
                stage=f"{stage_prefix}complete",
                attempt=1,
                from_status=RunStatus.RUNNING,
                to_status=RunStatus.SUCCEEDED,
                artifact_refs=artifacts,
            ),
        ),
        artifacts=artifacts,
        output=output.model_dump(mode="json"),
        blocker=None,
    )


def _completed_fixture(
    store: LocalArtifactStore,
    root: Path,
    *,
    package: EnvironmentPackage,
    runtime_asset: ArtifactRef,
    runtime_member: ArtifactRef,
    unique_frames: int,
    run_id: UUID,
    stage_prefix: str = "",
    capability_sha256: str = SHA_A,
    capability_artifact: ArtifactRef | None = None,
) -> module._CompletedReplay:
    package_manifest = json.loads(
        store.resolve(package.package_manifest).path.read_text(encoding="utf-8")
    )
    resolved_record = next(
        item for item in package_manifest["files"] if item["path"] == "resolved_scene.json"
    )
    resolved_model = ResolvedSceneSpec.model_validate_json(
        store.resolve_digest(resolved_record["sha256"]).read_bytes()
    )
    catalog_model = AssetCatalog.model_validate_json(
        store.resolve(package.asset_catalog).path.read_bytes()
    )
    events, transcript_bytes = _runtime_events()
    transcript = _put_bytes(
        store,
        root,
        name="runtime_event_transcript",
        payload=transcript_bytes,
        media_type="application/x-ndjson",
        schema_version="harness.runtime_event_transcript.v1",
    )
    stdout = _put_bytes(
        store,
        root,
        name="runtime_stdout",
        payload=b"",
        media_type="application/octet-stream",
        schema_version=None,
    )
    stderr = _put_bytes(
        store,
        root,
        name="runtime_stderr",
        payload=b"",
        media_type="application/octet-stream",
        schema_version=None,
    )
    probe_records: list[dict[str, object]] = []
    probe_diagnostics: list[ArtifactRef] = []
    if capability_artifact is not None:
        for phase in ("preflight", "postflight"):
            streams: dict[str, dict[str, object]] = {}
            for label, artifact in (
                ("stdout", stdout),
                ("stderr", stderr),
                ("capability_output", capability_artifact),
            ):
                identity = {
                    key: value
                    for key, value in artifact.model_dump(mode="json").items()
                    if key not in {"name", "uri"}
                }
                streams[label] = {**identity, "truncated": False}
                probe_records.append({"name": f"{phase}_probe_{label}", **identity})
            phase_diagnostics = _put_json(
                store,
                root,
                name=f"{phase}_probe_diagnostics",
                value={
                    "schema_version": "harness.runtime_probe_diagnostics.v1",
                    "phase": phase,
                    "exit_code": 0,
                    "streams": streams,
                },
                schema_version="harness.runtime_probe_diagnostics.v1",
            )
            probe_diagnostics.append(phase_diagnostics)
            probe_records.append(
                {
                    "name": phase_diagnostics.name,
                    **{
                        key: value
                        for key, value in phase_diagnostics.model_dump(mode="json").items()
                        if key not in {"name", "uri"}
                    },
                }
            )
    diagnostics = _put_json(
        store,
        root,
        name="runtime_execution_diagnostics",
        value={
            "schema_version": "harness.runtime_execution_diagnostics.v1",
            "status": "succeeded",
            "failure": None,
            "secondary_failures": [],
            "exit_code": 0,
            "capability_sha256": capability_sha256,
            "postflight_capability_sha256": capability_sha256,
            "transcript": {
                **{
                    key: value
                    for key, value in transcript.model_dump(mode="json").items()
                    if key not in {"name", "uri"}
                },
                "sha256": transcript.sha256,
                "complete": True,
                "truncated": False,
            },
            "stdout": {
                **{
                    key: value
                    for key, value in stdout.model_dump(mode="json").items()
                    if key not in {"name", "uri"}
                },
                "truncated": False,
            },
            "stderr": {
                **{
                    key: value
                    for key, value in stderr.model_dump(mode="json").items()
                    if key not in {"name", "uri"}
                },
                "truncated": False,
            },
            "probe_artifacts": probe_records,
        },
        schema_version="harness.runtime_execution_diagnostics.v1",
    )
    runtime_evidence = {
        "schema_version": "robotwin.scene_runtime_evidence.v2",
        "scene_id": resolved_model.scene_id,
        "resolved_scene_sha256": package.resolved_scene_sha256,
        "seed": resolved_model.seed,
        "status": "pass",
        "precheck_steps": 0,
        "base_simulation_step_count": 900,
        "settle_extra_steps": 0,
        "settle_converge_max": 0,
        "simulation_step_count": 900,
        "total_physics_step_count": 900,
        "contact_window_steps": 120,
        "fps": 12,
        "runtime_asset_snapshot_sha256": runtime_asset.sha256,
        "robot_initial_collision_count": 0,
        "video_frame_count": 120,
        "unique_video_frame_count": unique_frames,
        "video_sample_step_indices": [(index * 899) // 119 for index in range(120)],
        "images": {
            "head": "preview_head.png",
            "world_left": "preview_world_left.png",
            "world_right": "preview_world_right.png",
            "segmentation": "preview_segmentation.png",
            "observer_start": "observer_start.png",
            "observer_mid": "observer_mid.png",
            "observer_end": "observer_end.png",
        },
        "video": "observer_runtime.mp4",
        "objects": {
            item.object_id: {
                "translation_drift_m": 0.0,
                "rotation_drift_deg": 0.0,
                "visible_pixels": 128,
                "penetration_count": 0,
                "still_moving": False,
                "support_contact": True,
                "support_mode": "dynamic_contact",
                "support_target": item.support_target,
                "support_contact_fraction": 1.0,
                "unexpected_contact_fraction": 0.0,
                "unexpected_contact_targets": [],
                "support_footprint_margin_m": 1.0,
                "inside_contained": True,
                "dropped": False,
                "articulation_max_abs_error": 0.0,
                "resolved_translation_error_m": 0.0,
                "resolved_rotation_error_deg": 0.0,
            }
            for item in resolved_model.objects
        },
        "relations": {},
    }
    evidence = _put_json(
        store,
        root,
        name="runtime_evidence",
        value=runtime_evidence,
        schema_version="robotwin.scene_runtime_evidence.v2",
    )
    runtime_validation = validate_resolved_scene(
        resolved_model,
        catalog=catalog_model,
        runtime_evidence=runtime_evidence,
        require_runtime=True,
        min_visible_pixels=64,
    )
    assert runtime_validation["status"] == "pass"
    validation = _put_json(
        store,
        root,
        name="runtime_validation_report",
        value=runtime_validation,
        schema_version="robotwin.scene_validation.v1",
    )
    video = _put_bytes(
        store,
        root,
        name="observer_runtime",
        payload=f"video-{run_id}".encode(),
        media_type="video/mp4",
        schema_version=None,
    )
    pngs = tuple(
        _put_bytes(
            store,
            root,
            name=Path(relative).stem,
            payload=f"png-{run_id}-{relative}".encode(),
            media_type="image/png",
            schema_version=None,
        )
        for relative in RUNTIME_MEDIA_ARTIFACT_PATHS[:-1]
    )
    dependencies = (
        DependencyRef(
            name="text2env.replay.capability",
            version="1",
            sha256=capability_sha256,
        ),
        DependencyRef(
            name="text2env.replay.executor", version="1", sha256=EXECUTOR_IDENTITY_SHA256
        ),
        DependencyRef(name="text2env.replay.handler_config", version="1", sha256=SHA_D),
        DependencyRef(
            name="text2env.replay.media_verifier", version="1", sha256=MEDIA_IDENTITY_SHA256
        ),
        DependencyRef(
            name="text2env.replay.runtime_assets",
            version="1",
            sha256=runtime_asset.sha256,
        ),
    )
    media_records = [
        {"locator": f"{item.name}.png", **item.model_dump(mode="json")} for item in pngs
    ]
    media_records.append({"locator": "observer_runtime.mp4", **video.model_dump(mode="json")})
    receipt = _put_json(
        store,
        root,
        name="replay_execution_receipt",
        value={
            "schema_version": "harness.text2env_replay_receipt.v1",
            "skill_ref": "text2env.replay@1.0.0",
            "environment_package_id": package.package_id,
            "package_manifest_sha256": package.package_manifest.sha256,
            "asset_catalog_sha256": package.asset_catalog.sha256,
            "resolved_scene_sha256": package.resolved_scene_sha256,
            "runtime_config": {
                "precheck_steps": 0,
                "settle_steps": 900,
                "contact_window_steps": 120,
                "video_frames": 120,
                "fps": 12,
            },
            "invocation_dependencies": [item.model_dump(mode="json") for item in dependencies],
            "handler_configuration": {
                "sha256": SHA_D,
                "dependency": next(
                    item.model_dump(mode="json")
                    for item in dependencies
                    if item.name.endswith("handler_config")
                ),
                "allowed_asset_roots": {"count": 1, "configuration_sha256": SHA_1},
                "expected_capability_sha256": capability_sha256,
                "expected_runtime_asset_snapshot_sha256": None,
                "min_visible_pixels": 64,
                "checkpoint_steps": 120,
                "task_config": "demo_clean",
                "media_verifier": {
                    "sha256": MEDIA_IDENTITY_SHA256,
                    "document": MEDIA_IDENTITY_DOCUMENT,
                    "scope": "declared media verifier identity",
                    "handler_scope": "qualification fixture",
                },
                "runtime_executor": {
                    "sha256": EXECUTOR_IDENTITY_SHA256,
                    "document": EXECUTOR_IDENTITY_DOCUMENT,
                    "scope": "runtime executor supervisor identity",
                },
            },
            "capability": {
                "sha256": capability_sha256,
                "bytes": capability_artifact.bytes if capability_artifact is not None else 1,
                "postflight_sha256": capability_sha256,
            },
            "transcript": {
                key: value
                for key, value in transcript.model_dump(mode="json").items()
                if key not in {"name", "uri"}
            },
            "exit_code": 0,
            "runtime_evidence": {
                key: value
                for key, value in evidence.model_dump(mode="json").items()
                if key not in {"name", "uri"}
            },
            "runtime_validation_report": {
                **{
                    key: value
                    for key, value in validation.model_dump(mode="json").items()
                    if key not in {"name", "uri"}
                },
                "status": "pass",
            },
            "runtime_assets": {
                "self_contained": True,
                "dependency": next(
                    item.model_dump(mode="json")
                    for item in dependencies
                    if item.name.endswith("runtime_assets")
                ),
                "expected_sha256": None,
                "dependency_enforced": True,
                "configured_snapshot_pin_enforced": False,
                "manifest": runtime_asset.model_dump(mode="json"),
                "members": [runtime_member.model_dump(mode="json")],
            },
            "media": media_records,
            "media_verification": {
                "verifier_identity_sha256": MEDIA_IDENTITY_SHA256,
                "pngs": [
                    {
                        "locator": f"{item.name}.png",
                        "sha256": item.sha256,
                        "bytes": item.bytes,
                        "width": 320,
                        "height": 240,
                        "mode": "RGBA",
                        "format": "PNG",
                        "sandbox_metrics": {
                            "memory_peak_bytes": 1,
                            "memory_events": [],
                            "pids_peak": 1,
                            "pids_events": [],
                            "cpu_stats": [],
                        },
                    }
                    for item in pngs
                ],
                "video": {
                    "locator": "observer_runtime.mp4",
                    "sha256": video.sha256,
                    "bytes": video.bytes,
                    "frame_count": 120,
                    "source_unique_frame_count": unique_frames,
                    "decoded_unique_frame_count": unique_frames,
                    "fps_numerator": 12,
                    "fps_denominator": 1,
                    "width": 320,
                    "height": 240,
                    "format_name": "iso-bmff/mp4",
                    "codec_name": "h264",
                    "pixel_format": "8bit-420",
                    "sample_aspect_ratio": "0/1",
                    "square_sample_aspect_ratio_defaulted": True,
                    "sandbox_metrics": {
                        "memory_peak_bytes": 1,
                        "memory_events": [],
                        "pids_peak": 1,
                        "pids_events": [],
                        "cpu_stats": [],
                    },
                },
            },
        },
        schema_version="harness.text2env_replay_receipt.v1",
    )
    artifacts = (
        *((capability_artifact,) if capability_artifact is not None else ()),
        evidence,
        receipt,
        transcript,
        diagnostics,
        runtime_asset,
        runtime_member,
        validation,
        stdout,
        *probe_diagnostics,
        *pngs,
        video,
    )
    output = Text2EnvReplayOutput(runtime_evidence=evidence, replay_artifacts=artifacts)
    execution = RuntimeExecution(
        status=RuntimeExecutionStatus.SUCCEEDED,
        failure=None,
        exit_code=0,
        attempt_root=root,
        capability=None,
        postflight_capability_sha256=capability_sha256,
        events=events,  # type: ignore[arg-type]
        transcript=transcript_bytes,
        stdout=b"",
        stderr=b"",
        transcript_complete=True,
        transcript_truncated=False,
        stdout_truncated=False,
        stderr_truncated=False,
        output_files=(),
    )
    replay_input = Text2EnvReplayInput(
        environment_package=package,
        runtime_config=module._RUNTIME_CONFIG,
    )
    effective_parameters = replay_input.model_dump(mode="json")
    invocation_digest = module._invocation_digest(
        skill_id="text2env.replay",
        skill_version="1.0.0",
        effective_parameters=replay_input,
        dependencies=dependencies,
        max_attempts=2,
    )
    state = _harness_state(
        run_id=run_id,
        invocation_digest=invocation_digest,
        output=output,
        artifacts=artifacts,
        stage_prefix=stage_prefix,
    )
    invocation = Invocation(
        run_id=run_id,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        effective_parameters=effective_parameters,
        dependencies=dependencies,
        max_attempts=2,
        invocation_digest=invocation_digest,
    )
    return module._CompletedReplay(
        output=output,
        artifacts=artifacts,
        dependencies=dependencies,
        state=state,
        handler_call_count=1,
        observation=module._RuntimeObservation(execution=execution, delivered_events=events),
        invocation_digest=invocation_digest,
        invocation=invocation,
        journal_events=state.events,
    )


def _inspection_case(
    tmp_path: Path,
    *,
    capability_sha256: str = SHA_A,
    capability_path: Path | None = None,
    ffmpeg_sha256: str = SHA_3,
    launcher_sha256: str = SHA_4,
):
    store = LocalArtifactStore(tmp_path / "cas")
    capability_artifact = (
        None
        if capability_path is None
        else store.put_file(
            capability_path,
            name="runtime_capability",
            media_type="application/json",
            schema_version=RUNTIME_CAPABILITY_SCHEMA,
        )
    )
    if capability_artifact is not None:
        assert capability_artifact.sha256 == capability_sha256
    compile_output = _compile_fixture(store, tmp_path / "source")
    package_manifest = json.loads(
        store.resolve(compile_output.environment_package.package_manifest).path.read_text(
            encoding="utf-8"
        )
    )
    resolved_record = next(
        item for item in package_manifest["files"] if item["path"] == "resolved_scene.json"
    )
    resolved_model = ResolvedSceneSpec.model_validate_json(
        store.resolve_digest(resolved_record["sha256"]).read_bytes()
    )
    runtime_member = _put_bytes(
        store,
        tmp_path / "source",
        name="runtime_asset_member",
        payload=b"asset member",
        media_type="application/octet-stream",
        schema_version=None,
    )
    runtime_file = {
        "path": "asset.bin",
        "sha256": runtime_member.sha256,
        "bytes": runtime_member.bytes,
    }
    runtime_tree_sha256 = hashlib.sha256(
        (
            json.dumps(
                {"directories": ["."], "files": [runtime_file]},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
    ).hexdigest()
    runtime_asset = _put_json(
        store,
        tmp_path / "source",
        name="runtime_asset_snapshot",
        value={
            "schema_version": "harness.runtime_asset_snapshot.v1",
            "resolved_scene_sha256": compile_output.environment_package.resolved_scene_sha256,
            "asset_catalog_sha256": compile_output.environment_package.asset_catalog.sha256,
            "assets": [
                {
                    "asset_id": asset_id,
                    "selected_model_ids": [0],
                    "tree_sha256": runtime_tree_sha256,
                    "file_count": 1,
                    "bytes": runtime_member.bytes,
                    "directories": ["."],
                    "required_files": ["asset.bin"],
                    "files": [runtime_file],
                }
                for asset_id in sorted({item.asset_id for item in resolved_model.objects})
            ],
        },
        schema_version="harness.runtime_asset_snapshot.v1",
    )
    direct = _completed_fixture(
        store,
        tmp_path / "direct",
        package=compile_output.environment_package,
        runtime_asset=runtime_asset,
        runtime_member=runtime_member,
        unique_frames=114,
        run_id=module._DIRECT_RUN_ID,
        capability_sha256=capability_sha256,
        capability_artifact=capability_artifact,
    )
    kernel = _completed_fixture(
        store,
        tmp_path / "kernel",
        package=compile_output.environment_package,
        runtime_asset=runtime_asset,
        runtime_member=runtime_member,
        unique_frames=113,
        run_id=module._KERNEL_RUN_ID,
        stage_prefix="qualification_candidate.",
        capability_sha256=capability_sha256,
        capability_artifact=capability_artifact,
    )
    identity = SimpleNamespace(
        sha256=MEDIA_IDENTITY_SHA256,
        sandbox_identity=SimpleNamespace(
            ffmpeg_sha256=ffmpeg_sha256,
            launcher_sha256=launcher_sha256,
        ),
    )
    assembly = module._ReplayAssembly(
        compile_application=SimpleNamespace(),
        artifact_store=store,
        package_store=SimpleNamespace(),
        wiring=SimpleNamespace(
            handler=SimpleNamespace(media_verifier=SimpleNamespace(identity=identity))
        ),
        runtime_executor=SimpleNamespace(observations=[]),
    )
    replay_input = Text2EnvReplayInput(
        environment_package=compile_output.environment_package,
        runtime_config=module._RUNTIME_CONFIG,
    )
    return assembly, compile_output, replay_input, direct, kernel


def _fixed_execution_fixture(
    tmp_path: Path,
    settings: ReplayQualificationSettings,
) -> module._FixedCaseExecution:
    capability_sha256 = hashlib.sha256(settings.runtime_capability_path.read_bytes()).hexdigest()
    assembly, compile_output, replay_input, direct, kernel = _inspection_case(
        tmp_path,
        capability_sha256=capability_sha256,
        capability_path=settings.runtime_capability_path,
        ffmpeg_sha256=hashlib.sha256(settings.static_ffmpeg.read_bytes()).hexdigest(),
        launcher_sha256=hashlib.sha256(settings.media_launcher.read_bytes()).hexdigest(),
    )
    evidence_root = tmp_path / "qualification"
    evidence_root.mkdir(parents=True)
    source_before = module._snapshot_sources(settings, phase="before_execution")
    source_before_ref = module._publish_source_manifest(
        assembly.artifact_store,
        evidence_root,
        source_before,
    )
    operator_before, operator_before_ref = module._snapshot_operator_inputs(
        settings,
        artifact_store=assembly.artifact_store,
        scratch_root=evidence_root,
        phase="before_execution",
    )
    direct_supervisor_ref = module._publish_run_receipt(
        assembly.artifact_store,
        evidence_root,
        direct,
        mode="candidate_direct",
    )
    kernel_evaluation_ref = module._publish_run_receipt(
        assembly.artifact_store,
        evidence_root,
        kernel,
        mode="qualification_candidate",
    )
    return module._FixedCaseExecution(
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


def test_public_settings_expose_only_operator_locators_and_timeouts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert {field.name for field in fields(settings)} == {
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
    values = {field.name: getattr(settings, field.name) for field in fields(settings)}
    assert not ({"handler", "descriptor", "qualification", "artifact_store"} & set(values))
    with pytest.raises(TypeError, match="unexpected keyword"):
        ReplayQualificationSettings(**values, handler=object())


def test_real_cli_settings_manifest_is_strict_and_main_reports_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings(tmp_path)
    manifest = tmp_path / "replay-qualification-settings.json"
    _write_settings_manifest(manifest, settings)
    assert module._load_settings_manifest(manifest) == settings

    marker_store = LocalArtifactStore(tmp_path / "marker-cas")
    marker = _put_json(
        marker_store,
        tmp_path,
        name="closure",
        value={"closure": True},
        schema_version="harness.replay_qualification.evidence_closure.v1",
    )
    result = SimpleNamespace(
        bundle_root=settings.bundle_root,
        artifact_root=marker_store.root,
        qualification=SimpleNamespace(report_sha256=SHA_A),
        evidence_closure=(marker,),
    )
    monkeypatch.setattr(module, "generate_replay_qualification", lambda value: result)

    assert module.main(["--settings", str(manifest)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == "harness.replay_qualification.application_result.v1"
    assert output["evidence_closure"] == [marker.model_dump(mode="json")]


@pytest.mark.parametrize(
    "payload",
    [
        b'{"bundle_root":"a","bundle_root":"b"}\n',
        b'{"unexpected":true}\n',
        b"[]\n",
    ],
)
def test_cli_settings_manifest_rejects_duplicate_extra_and_wrong_shape(
    tmp_path: Path,
    payload: bytes,
) -> None:
    manifest = tmp_path / "settings.json"
    manifest.write_bytes(payload)

    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._load_settings_manifest(manifest)

    assert captured.value.reason == "settings_manifest_invalid"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("bundle_root", ""),
        ("runtime_runner", 7),
        ("allowed_asset_roots", []),
        ("allowed_asset_roots", [""]),
    ],
)
def test_cli_settings_manifest_rejects_invalid_path_and_root_values(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    settings = _settings(tmp_path)
    manifest = tmp_path / "settings.json"
    _write_settings_manifest(manifest, settings)
    value = json.loads(manifest.read_bytes())
    value[field] = replacement
    manifest.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._load_settings_manifest(manifest)

    assert captured.value.reason == "settings_manifest_invalid"


def test_cli_generation_failure_writes_no_final_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    manifest = tmp_path / "settings.json"
    _write_settings_manifest(manifest, settings)

    def fail(_settings):
        raise ReplayQualificationGenerationError("candidate_failed", "deliberate")

    monkeypatch.setattr(module, "generate_replay_qualification", fail)
    with pytest.raises(ReplayQualificationGenerationError):
        module.main(["--settings", str(manifest)])

    assert not settings.bundle_root.exists()


def test_generates_strict_loadable_three_document_bundle_from_fixed_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _install_execution(monkeypatch, _valid_execution(tmp_path / "execution", settings))

    result = generate_replay_qualification(settings)

    assert {path.name for path in result.bundle_root.iterdir()} == {
        "qualification.json",
        "report.json",
        "manifest.json",
    }
    assert tuple(check.name for check in result.report.checks) == (
        "01.case_binding",
        "02.exact_dependencies",
        "03.event_lifecycle",
        "04.runtime_asset_snapshot",
        "05.media_decode",
        "06.physics_validation",
        "07.source_stability",
        "08.candidate_kernel_executions",
    )
    assert result.qualification.deterministic_case_id == ("can-on-plate-seed-7-900-120-120-12")
    manifest_paths = {item.path for item in result.manifest.files}
    assert {
        "self_improving/harness/__init__.py",
        "self_improving/harness/handlers/__init__.py",
        "self_improving/harness/schemas/__init__.py",
        "self_improving/harness/qualify_compile.py",
        "self_improving/harness/qualify_replay.py",
        "self_improving/harness/replay_qualification.py",
        "self_improving/harness/handlers/text2env_validate.py",
    } <= manifest_paths
    assert not any("qualified_skills" in path or "__pycache__" in path for path in manifest_paths)
    for item in result.manifest.files:
        payload = (ROOT / item.path).read_bytes()
        assert item.bytes == len(payload)
        assert item.sha256 == hashlib.sha256(payload).hexdigest()
    loaded = load_replay_qualification_bundle(
        result.bundle_root,
        artifact_store=LocalArtifactStore(result.artifact_root),
        implementation_root=ROOT,
        scene_gen_root=SCENE_GEN,
        ledger_contract_root=LEDGER,
    )
    assert loaded.generic.report == result.report
    assert loaded.executions.production_kernel_candidate.mode == "qualification_candidate"
    closure_schemas = {item.schema_version for item in loaded.evidence_closure.refs}
    closure_names = {item.name for item in loaded.evidence_closure.refs}
    assert {
        "robotwin.scene_spec.v1",
        "robotwin.resolved_scene.v1",
    } <= closure_schemas
    assert {"request", "generated_scene"} <= closure_names


def test_success_never_writes_temporary_pass_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _install_execution(monkeypatch, _valid_execution(tmp_path / "execution", settings))

    generate_replay_qualification(settings)

    temporary_pass_documents = tuple(
        path
        for path in settings.scratch_root.rglob("*")
        if path.name in {"qualification.json", "report.json", "manifest.json"}
    )
    assert temporary_pass_documents == ()


def test_generated_true_evidence_issues_the_exact_production_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    execution = _valid_execution(tmp_path / "execution", settings)
    _install_execution(monkeypatch, execution)
    result = generate_replay_qualification(settings)
    dependencies = execution.kernel.dependencies

    def identity(document: dict[str, object]) -> SimpleNamespace:
        payload = (
            json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
        return SimpleNamespace(canonical_bytes=payload, sha256=hashlib.sha256(payload).hexdigest())

    def assemble(*, artifact_store, **_kwargs):
        resolver = object.__new__(Text2EnvReplayDependencyResolver)
        object.__setattr__(resolver, "artifact_store", artifact_store)
        handler = object.__new__(Text2EnvReplayHandler)
        object.__setattr__(handler, "artifact_store", artifact_store)
        object.__setattr__(handler, "dependency_resolver", resolver)
        object.__setattr__(
            handler,
            "runtime_executor",
            SimpleNamespace(identity=identity(EXECUTOR_IDENTITY_DOCUMENT)),
        )
        object.__setattr__(
            handler,
            "media_verifier",
            SimpleNamespace(identity=identity(MEDIA_IDENTITY_DOCUMENT)),
        )
        object.__setattr__(
            handler,
            "expected_capability_sha256",
            next(item.sha256 for item in dependencies if item.name.endswith("capability")),
        )
        object.__setattr__(
            handler,
            "expected_handler_config_sha256",
            next(item.sha256 for item in dependencies if item.name.endswith("handler_config")),
        )
        return Text2EnvReplayWiring(handler=handler, dependency_resolver=resolver)

    monkeypatch.setattr(replay_application_module, "_assemble_live_wiring", assemble)
    monkeypatch.setattr(
        Text2EnvReplayHandler,
        "_require_configuration_unchanged",
        lambda self: None,
    )
    monkeypatch.setattr(
        Text2EnvReplayDependencyResolver,
        "resolve",
        lambda self, skill_ref, value: dependencies,
    )

    application_settings = ReplayApplicationSettings(
        state_root=tmp_path / "application-state",
        qualification_bundle_root=result.bundle_root,
        evidence_artifact_root=result.artifact_root,
        implementation_root=ROOT,
        scene_gen_root=SCENE_GEN,
        ledger_contract_root=LEDGER,
        allowed_asset_roots=settings.allowed_asset_roots,
        interpreter=settings.interpreter.resolve(),
        runtime_runner=settings.runtime_runner.resolve(),
        runtime_module_root=settings.runtime_module_root.resolve(),
        runtime_capability_path=settings.runtime_capability_path.resolve(),
        media_launcher=settings.media_launcher.resolve(),
        static_ffmpeg=settings.static_ffmpeg.resolve(),
        delegated_cgroup_root=settings.delegated_cgroup_root.resolve(),
        runtime_timeout_seconds=settings.runtime_timeout_seconds,
        capability_timeout_seconds=settings.capability_timeout_seconds,
    )
    application = create_replay_application(application_settings)

    assert tuple(f"{item.skill_id}@{item.version}" for item in application.skills) == (
        "text2env.replay@1.0.0",
    )
    assert (
        application.skills[0].qualification_artifact.sha256
        == hashlib.sha256((result.bundle_root / "qualification.json").read_bytes()).hexdigest()
    )

    with pytest.raises(ReplayApplicationConfigurationError, match="file locators"):
        create_replay_application(
            replace(
                application_settings,
                state_root=tmp_path / "application-state-file-drift",
                interpreter=settings.runtime_runner.resolve(),
            )
        )
    with pytest.raises(ReplayApplicationConfigurationError, match="operator roots"):
        create_replay_application(
            replace(
                application_settings,
                state_root=tmp_path / "application-state-root-drift",
                allowed_asset_roots=(settings.runtime_module_root.resolve(),),
            )
        )

    evidence_store = LocalArtifactStore(result.artifact_root)
    verified = verify_replay_qualification_bundle(
        result.bundle_root,
        artifact_store=evidence_store,
        implementation_root=ROOT,
        scene_gen_root=SCENE_GEN,
        ledger_contract_root=LEDGER,
    )
    with pytest.raises(ReplayApplicationConfigurationError, match="concrete wiring"):
        replay_application_module._verify_live_wiring(
            inspected=verified,
            wiring=SimpleNamespace(handler=lambda *_args: None),  # type: ignore[arg-type]
            artifact_store=evidence_store,
        )

    mismatched_handler_wiring = assemble(artifact_store=evidence_store)
    mismatched_identity = identity({"schema_version": "test.other_executor.v1"})
    object.__setattr__(
        mismatched_handler_wiring.handler,
        "runtime_executor",
        SimpleNamespace(identity=mismatched_identity),
    )
    with pytest.raises(ReplayApplicationConfigurationError, match="handler execution wiring"):
        replay_application_module._verify_live_wiring(
            inspected=verified,
            wiring=mismatched_handler_wiring,
            artifact_store=evidence_store,
        )

    incomplete_handler_wiring = assemble(artifact_store=evidence_store)
    object.__setattr__(
        incomplete_handler_wiring.handler,
        "runtime_executor",
        SimpleNamespace(),
    )
    with pytest.raises(ReplayApplicationConfigurationError, match="wiring is incomplete"):
        replay_application_module._verify_live_wiring(
            inspected=verified,
            wiring=incomplete_handler_wiring,
            artifact_store=evidence_store,
        )

    original_dependency_identity = Text2EnvReplayHandler.dependency_identity
    monkeypatch.setattr(
        Text2EnvReplayHandler,
        "dependency_identity",
        property(
            lambda self: DependencyRef(
                name="text2env.replay.handler_config",
                version="1",
                sha256="0" * 64,
            )
        ),
    )
    with pytest.raises(ReplayApplicationConfigurationError, match="dependency proof"):
        replay_application_module._verify_live_wiring(
            inspected=verified,
            wiring=assemble(artifact_store=evidence_store),
            artifact_store=evidence_store,
        )
    monkeypatch.setattr(
        Text2EnvReplayHandler,
        "dependency_identity",
        original_dependency_identity,
    )

    failing_wiring = assemble(artifact_store=evidence_store)

    def fail_configuration(_self):
        raise ValueError("configuration drift")

    monkeypatch.setattr(
        Text2EnvReplayHandler,
        "_require_configuration_unchanged",
        fail_configuration,
    )
    with pytest.raises(ReplayApplicationConfigurationError, match="complete production"):
        replay_application_module._verify_live_wiring(
            inspected=verified,
            wiring=failing_wiring,
            artifact_store=evidence_store,
        )
    monkeypatch.setattr(
        Text2EnvReplayHandler,
        "_require_configuration_unchanged",
        lambda self: None,
    )

    resolver_failure_wiring = assemble(artifact_store=evidence_store)
    monkeypatch.setattr(
        Text2EnvReplayDependencyResolver,
        "resolve",
        lambda self, skill_ref, value: (_ for _ in ()).throw(RuntimeError("resolver failed")),
    )
    with pytest.raises(ReplayApplicationConfigurationError, match="cannot reproduce"):
        replay_application_module._verify_live_wiring(
            inspected=verified,
            wiring=resolver_failure_wiring,
            artifact_store=evidence_store,
        )

    drifted_wiring = assemble(artifact_store=evidence_store)
    monkeypatch.setattr(
        Text2EnvReplayDependencyResolver,
        "resolve",
        lambda self, skill_ref, value: dependencies[:-1],
    )
    with pytest.raises(ReplayApplicationConfigurationError, match="differs"):
        replay_application_module._verify_live_wiring(
            inspected=verified,
            wiring=drifted_wiring,
            artifact_store=evidence_store,
        )


@pytest.mark.parametrize("attack", ["fake_report", "empty_closure"])
def test_public_application_reverifies_raw_bundle_and_leaves_no_pass_or_db_on_attack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    settings = _settings(tmp_path)
    _install_execution(monkeypatch, _valid_execution(tmp_path / "execution", settings))
    result = generate_replay_qualification(settings)
    attacked_bundle = tmp_path / "attacked-bundle"
    shutil.copytree(result.bundle_root, attacked_bundle)
    report = json.loads((attacked_bundle / "report.json").read_bytes())
    store = LocalArtifactStore(result.artifact_root)
    if attack == "fake_report":
        report["checks"][0]["evidence"]["seed"] = 8
    else:
        empty = _put_json(
            store,
            tmp_path,
            name="empty_evidence_closure",
            value={
                "schema_version": "harness.replay_qualification.evidence_closure.v1",
                "skill_ref": "text2env.replay@1.0.0",
                "case_id": "can-on-plate-seed-7-900-120-120-12",
                "refs": [],
            },
            schema_version="harness.replay_qualification.evidence_closure.v1",
        )
        report["checks"][7]["evidence"]["evidence_closure_manifest"] = empty.model_dump(mode="json")
    report_bytes = (
        json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()
    (attacked_bundle / "report.json").write_bytes(report_bytes)
    qualification = json.loads((attacked_bundle / "qualification.json").read_bytes())
    qualification["report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
    qualification_bytes = (
        json.dumps(
            qualification,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode()
    (attacked_bundle / "qualification.json").write_bytes(qualification_bytes)
    state_root = tmp_path / f"attacked-state-{attack}"
    application_settings = ReplayApplicationSettings(
        state_root=state_root,
        qualification_bundle_root=attacked_bundle,
        evidence_artifact_root=result.artifact_root,
        implementation_root=ROOT,
        scene_gen_root=SCENE_GEN,
        ledger_contract_root=LEDGER,
        allowed_asset_roots=settings.allowed_asset_roots,
        interpreter=settings.interpreter.resolve(),
        runtime_runner=settings.runtime_runner.resolve(),
        runtime_module_root=settings.runtime_module_root.resolve(),
        runtime_capability_path=settings.runtime_capability_path.resolve(),
        media_launcher=settings.media_launcher.resolve(),
        static_ffmpeg=settings.static_ffmpeg.resolve(),
        delegated_cgroup_root=settings.delegated_cgroup_root.resolve(),
        runtime_timeout_seconds=settings.runtime_timeout_seconds,
        capability_timeout_seconds=settings.capability_timeout_seconds,
    )

    with pytest.raises(ReplayApplicationConfigurationError):
        create_replay_application(application_settings)

    assert not state_root.exists()
    for payload in (report_bytes, qualification_bytes):
        with pytest.raises(ArtifactResolutionError):
            store.resolve_digest(hashlib.sha256(payload).hexdigest())


def test_invalid_typed_dependency_in_execution_receipt_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    execution = _valid_execution(tmp_path / "execution", settings)
    _install_execution(monkeypatch, execution)
    result = generate_replay_qualification(settings)
    store = LocalArtifactStore(result.artifact_root)
    verified = verify_replay_qualification_bundle(
        result.bundle_root,
        artifact_store=store,
        implementation_root=ROOT,
        scene_gen_root=SCENE_GEN,
        ledger_contract_root=LEDGER,
    )
    dependency_model = DependencyRef

    class RejectingDependency:
        @staticmethod
        def model_validate(_value):
            return dependency_model.model_validate({"name": "invalid"})

    monkeypatch.setattr(replay_qualification_module, "DependencyRef", RejectingDependency)

    with pytest.raises(replay_qualification_module.ReplayQualificationError) as captured:
        replay_qualification_module._verify_run_evidence(
            store,
            receipt=verified.evidence.direct_supervisor,
            execution=verified.executions.candidate_direct,
            lifecycle=verified.event_lifecycle.candidate_direct,
            media=verified.media_decode.candidate_direct,
            physics=verified.physics_validation.candidate_direct,
            runtime_asset_snapshot=verified.runtime_asset_snapshot,
            exact_dependencies=verified.exact_dependencies,
            case_binding=verified.case_binding,
        )

    assert captured.value.reason == "invalid_replay_evidence"


def test_strict_evidence_deep_failure_branches_are_all_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    execution_fixture = _valid_execution(tmp_path / "execution", settings)
    _install_execution(monkeypatch, execution_fixture)
    result = generate_replay_qualification(settings)
    store = LocalArtifactStore(result.artifact_root)
    verified = verify_replay_qualification_bundle(
        result.bundle_root,
        artifact_store=store,
        implementation_root=ROOT,
        scene_gen_root=SCENE_GEN,
        ledger_contract_root=LEDGER,
    )
    evidence = verified.evidence
    receipt = evidence.direct_supervisor
    execution = verified.executions.candidate_direct
    lifecycle = verified.event_lifecycle.candidate_direct
    media = verified.media_decode.candidate_direct
    physics = verified.physics_validation.candidate_direct

    def reject_run(
        *,
        attacked_receipt=receipt,
        attacked_execution=execution,
        attacked_lifecycle=lifecycle,
        attacked_media=media,
        attacked_physics=physics,
        attacked_case=verified.case_binding,
    ) -> None:
        with pytest.raises(replay_qualification_module.ReplayQualificationError):
            replay_qualification_module._verify_run_evidence(
                store,
                receipt=attacked_receipt,
                execution=attacked_execution,
                lifecycle=attacked_lifecycle,
                media=attacked_media,
                physics=attacked_physics,
                runtime_asset_snapshot=verified.runtime_asset_snapshot,
                exact_dependencies=verified.exact_dependencies,
                case_binding=attacked_case,
            )

    replay_input = Text2EnvReplayInput.model_validate(receipt.invocation.effective_parameters)
    package = replay_input.environment_package
    package_manifest_path = store.resolve(package.package_manifest).path
    package_manifest = json.loads(package_manifest_path.read_bytes())
    catalog_path = store.resolve(package.asset_catalog).path

    original_read_bytes = Path.read_bytes
    catalog_reads = 0

    def fail_catalog_reread(self: Path) -> bytes:
        nonlocal catalog_reads
        if self == catalog_path:
            catalog_reads += 1
            if catalog_reads == 2:
                raise OSError("catalog disappeared before authoritative validation")
        return original_read_bytes(self)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "read_bytes", fail_catalog_reread)
        reject_run()

    invalid_scene = _put_json(
        store,
        tmp_path,
        name="invalid_scene",
        value={},
        schema_version="robotwin.scene_spec.v1",
    )
    invalid_manifest = json.loads(json.dumps(package_manifest))
    invalid_manifest["files"][1].update(
        {"sha256": invalid_scene.sha256, "bytes": invalid_scene.bytes}
    )
    with pytest.raises(replay_qualification_module.ReplayQualificationError):
        replay_qualification_module._verify_environment_package(
            store,
            package=package,
            manifest=invalid_manifest,
            case_binding=verified.case_binding,
        )
    with pytest.raises(replay_qualification_module.ReplayQualificationError):
        replay_qualification_module._verify_environment_package(
            store,
            package=package,
            manifest=package_manifest,
            case_binding=verified.case_binding.model_copy(update={"request": "wrong"}),
        )
    request_ref = replay_qualification_module._package_member_refs(package_manifest)[0]
    request_path = store.resolve(request_ref).path

    def fail_request_read(self: Path) -> bytes:
        if self == request_path:
            raise OSError("request disappeared")
        return original_read_bytes(self)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "read_bytes", fail_request_read)
        with pytest.raises(replay_qualification_module.ReplayQualificationError):
            replay_qualification_module._verify_environment_package(
                store,
                package=package,
                manifest=package_manifest,
                case_binding=verified.case_binding,
            )
    with monkeypatch.context() as scoped:
        scoped.setattr(replay_qualification_module, "generated_module_source", lambda value: "bad")
        with pytest.raises(replay_qualification_module.ReplayQualificationError):
            replay_qualification_module._verify_environment_package(
                store,
                package=package,
                manifest=package_manifest,
                case_binding=verified.case_binding,
            )

    operator_args = {
        "store": store,
        "before": evidence.operator_before,
        "after": evidence.operator_after,
        "case_binding": verified.case_binding,
        "exact_dependencies": verified.exact_dependencies,
        "media_decode": verified.media_decode,
    }

    def reject_operator(**updates) -> None:
        arguments = {**operator_args, **updates}
        with pytest.raises(replay_qualification_module.ReplayQualificationError):
            replay_qualification_module._verify_operator_manifests(**arguments)

    reject_operator(before=evidence.operator_before.model_copy(update={"phase": "before_publish"}))
    reject_operator(after=evidence.operator_after.model_copy(update={"inputs_sha256": SHA_A}))

    def paired_operator_update(**updates):
        before = evidence.operator_before.model_copy(update=updates)
        after = evidence.operator_after.model_copy(update=updates)
        digest = replay_qualification_module._canonical_sha256(
            before.model_dump(mode="json", exclude={"phase", "inputs_sha256"})
        )
        return (
            before.model_copy(update={"inputs_sha256": digest}),
            after.model_copy(update={"inputs_sha256": digest}),
        )

    before, after = paired_operator_update(request="wrong")
    reject_operator(before=before, after=after)
    before, after = paired_operator_update(files=tuple(reversed(evidence.operator_before.files)))
    reject_operator(before=before, after=after)
    with monkeypatch.context() as scoped:
        scoped.setattr(replay_qualification_module, "_file_identity", lambda path: (0, SHA_A))
        reject_operator()

    catalog_model = replay_qualification_module.AssetCatalog

    class InvalidCatalog:
        @staticmethod
        def model_validate(_value):
            return catalog_model.model_validate({})

    with monkeypatch.context() as scoped:
        scoped.setattr(replay_qualification_module, "AssetCatalog", InvalidCatalog)
        reject_operator()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            replay_qualification_module,
            "validate_runtime_capability_document",
            lambda value: (_ for _ in ()).throw(
                replay_qualification_module.RuntimeCapabilityError("invalid")
            ),
        )
        reject_operator()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            replay_qualification_module,
            "canonical_capability_bytes",
            lambda value: b"bad",
        )
        reject_operator()
    wrong_dependencies = list(verified.exact_dependencies.dependencies)
    wrong_dependencies[0] = wrong_dependencies[0].model_copy(update={"sha256": SHA_A})
    reject_operator(
        exact_dependencies=verified.exact_dependencies.model_copy(
            update={"dependencies": tuple(wrong_dependencies)}
        )
    )

    source_args = {
        "implementation_root": ROOT,
        "generic": verified.generic,
        "claim": verified.source_stability,
        "before": evidence.source_before,
        "after_execution": evidence.source_after_execution,
        "before_publish": evidence.source_before_publish,
    }

    def reject_source(**updates) -> None:
        with pytest.raises(replay_qualification_module.ReplayQualificationError):
            replay_qualification_module._verify_source_manifests(**{**source_args, **updates})

    reject_source(before=evidence.source_before.model_copy(update={"phase": "before_publish"}))
    reject_source(
        before=evidence.source_before.model_copy(update={"distribution_root": str(tmp_path)})
    )
    reject_source(
        after_execution=evidence.source_after_execution.model_copy(update={"tree_sha256": SHA_A})
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            replay_qualification_module,
            "_snapshot_harness_source",
            lambda root, phase: evidence.source_before.model_copy(update={"tree_sha256": SHA_A}),
        )
        reject_source()

    reject_run(attacked_receipt=receipt.model_copy(update={"run_id": module._KERNEL_RUN_ID}))
    reject_run(
        attacked_receipt=receipt.model_copy(
            update={
                "invocation": receipt.invocation.model_copy(update={"effective_parameters": {}})
            }
        )
    )
    wrong_digest = "0" * 64
    reject_run(
        attacked_receipt=receipt.model_copy(
            update={
                "invocation": receipt.invocation.model_copy(
                    update={"invocation_digest": wrong_digest}
                ),
                "run_state": receipt.run_state.model_copy(
                    update={"invocation_digest": wrong_digest}
                ),
            }
        ),
        attacked_execution=execution.model_copy(update={"invocation_digest": wrong_digest}),
    )
    reject_run(attacked_case=verified.case_binding.model_copy(update={"scene_spec_sha256": SHA_A}))
    reject_run(attacked_receipt=receipt.model_copy(update={"mode": "qualification_candidate"}))
    bad_transcript = _put_bytes(
        store,
        tmp_path,
        name="bad_runtime_transcript",
        payload=b"not-jsonl",
        media_type="application/x-ndjson",
        schema_version="harness.runtime_event_transcript.v1",
    )
    reject_run(
        attacked_receipt=receipt.model_copy(update={"runtime_event_transcript": bad_transcript})
    )
    reject_run(attacked_lifecycle=lifecycle.model_copy(update={"simulation_completed_steps": 899}))
    duplicated_artifacts = (*receipt.artifact_closure, receipt.artifact_closure[0])
    reject_run(
        attacked_receipt=receipt.model_copy(
            update={
                "artifact_closure": duplicated_artifacts,
                "run_state": receipt.run_state.model_copy(
                    update={"artifacts": duplicated_artifacts}
                ),
            }
        )
    )
    reject_run(attacked_execution=execution.model_copy(update={"execution_receipt_sha256": SHA_A}))
    reject_run(attacked_execution=execution.model_copy(update={"output_sha256": SHA_A}))

    real_load_json = replay_qualification_module._load_strict_json

    def reject_handler_json(mutator, *, label="handler execution receipt") -> None:
        def attacked(path, *, label: str):
            value = real_load_json(path, label=label)
            if label == target_label:
                value = json.loads(json.dumps(value))
                mutator(value)
            return value

        target_label = label
        with monkeypatch.context() as scoped:
            scoped.setattr(replay_qualification_module, "_load_strict_json", attacked)
            reject_run()

    reject_handler_json(lambda value: value.update(extra=True))
    reject_handler_json(lambda value: value.update(exit_code=1))
    reject_handler_json(lambda value: value["handler_configuration"].update(extra=True))
    reject_handler_json(lambda value: value["handler_configuration"].update(dependency={}))
    reject_handler_json(lambda value: value["handler_configuration"].update(task_config="wrong"))
    reject_handler_json(lambda value: value["runtime_validation_report"].update(status="fail"))
    reject_handler_json(
        lambda value: value.update(extra=True),
        label="runtime execution diagnostics",
    )
    reject_handler_json(
        lambda value: value.update(status="failed"),
        label="runtime execution diagnostics",
    )
    reject_handler_json(
        lambda value: value["runtime_assets"]["members"][0].update(sha256="invalid")
    )
    reject_handler_json(lambda value: value["runtime_assets"].update(self_contained=False))
    reject_handler_json(lambda value: value["media_verification"]["video"].update(frame_count=119))
    reject_handler_json(lambda value: value["media"][0].update(sha256="invalid"))
    reject_handler_json(lambda value: value["media"][0].update(locator="wrong.png"))
    reject_handler_json(lambda value: value["media_verification"].update(pngs=[]))
    reject_handler_json(lambda value: value["media_verification"]["pngs"][0].update(format="JPEG"))
    reject_handler_json(lambda value: value["media_verification"]["video"].update(bytes=0))
    reject_handler_json(
        lambda value: value.update(simulation_step_count=899),
        label="runtime physics evidence",
    )
    reject_handler_json(
        lambda value: value.update(objects={}),
        label="runtime physics evidence",
    )
    reject_handler_json(
        lambda value: value.update(relations={"invented:can_1:plate_1": {"pass": True}}),
        label="runtime physics evidence",
    )
    reject_handler_json(
        lambda value: value["objects"]["can_1"].update(penetration_count=1),
        label="runtime physics evidence",
    )
    reject_handler_json(
        lambda value: value.update(
            checks=[{"name": "fixed_case", "status": "pass", "evidence": {}}]
        ),
        label="runtime validation",
    )
    reject_run(attacked_physics=physics.model_copy(update={"settle_steps": 899}))
    with monkeypatch.context() as scoped:
        scoped.setattr(
            replay_qualification_module,
            "validate_resolved_scene",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid facts")),
        )
        reject_run()
    reject_handler_json(
        lambda value: value.update(status="fail"),
        label="runtime validation",
    )

    with pytest.raises(replay_qualification_module.ReplayQualificationError):
        replay_qualification_module.verify_replay_qualification_documents(
            {},
            artifact_store=store,
            implementation_root=ROOT,
            scene_gen_root=SCENE_GEN,
            ledger_contract_root=LEDGER,
        )

    def reject_closure(attacked_executions, *, discovered=None) -> None:
        with monkeypatch.context() as scoped:
            if discovered is not None:
                scoped.setattr(
                    replay_qualification_module,
                    "_discover_evidence_closure",
                    lambda artifact_store, roots: discovered,
                )
            with pytest.raises(replay_qualification_module.ReplayQualificationError):
                replay_qualification_module._load_and_verify_evidence_closure(
                    artifact_store=store,
                    generic=verified.generic,
                    case_binding=verified.case_binding,
                    exact_dependencies=verified.exact_dependencies,
                    event_lifecycle=verified.event_lifecycle,
                    runtime_asset_snapshot=verified.runtime_asset_snapshot,
                    media_decode=verified.media_decode,
                    physics_validation=verified.physics_validation,
                    source_stability=verified.source_stability,
                    executions=attacked_executions,
                    implementation_root=ROOT,
                )

    reversed_closure = evidence.closure.model_copy(
        update={"refs": tuple(reversed(evidence.closure.refs))}
    )
    reversed_closure_ref = _put_json(
        store,
        tmp_path,
        name="reversed_closure",
        value=reversed_closure.model_dump(mode="json"),
        schema_version="harness.replay_qualification.evidence_closure.v1",
    )
    reject_closure(
        verified.executions.model_copy(update={"evidence_closure_manifest": reversed_closure_ref})
    )
    without_runtime = tuple(
        item
        for item in evidence.closure.refs
        if item.sha256 != verified.runtime_asset_snapshot.manifest_sha256
    )
    missing_runtime_closure = evidence.closure.model_copy(update={"refs": without_runtime})
    missing_runtime_ref = _put_json(
        store,
        tmp_path,
        name="missing_runtime_closure",
        value=missing_runtime_closure.model_dump(mode="json"),
        schema_version="harness.replay_qualification.evidence_closure.v1",
    )
    reject_closure(
        verified.executions.model_copy(update={"evidence_closure_manifest": missing_runtime_ref}),
        discovered=without_runtime,
    )

    class DifferentTypedOutput:
        @staticmethod
        def model_validate(_value):
            return receipt.typed_output.model_copy(
                update={"replay_artifacts": receipt.typed_output.replay_artifacts[:-1]}
            )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            replay_qualification_module,
            "Text2EnvReplayOutput",
            DifferentTypedOutput,
        )
        reject_run()

    capability_sha256 = next(
        item.sha256
        for item in verified.exact_dependencies.dependencies
        if item.name == "text2env.replay.capability"
    )

    class MissingCapabilityStore:
        def resolve(self, ref):
            return store.resolve(ref)

        def resolve_digest(self, digest):
            if digest == capability_sha256:
                raise replay_qualification_module.ArtifactResolutionError(
                    "missing",
                    "capability missing",
                )
            return store.resolve_digest(digest)

    with pytest.raises(replay_qualification_module.ReplayQualificationError):
        replay_qualification_module._verify_run_evidence(
            MissingCapabilityStore(),  # type: ignore[arg-type]
            receipt=receipt,
            execution=execution,
            lifecycle=lifecycle,
            media=media,
            physics=physics,
            runtime_asset_snapshot=verified.runtime_asset_snapshot,
            exact_dependencies=verified.exact_dependencies,
            case_binding=verified.case_binding,
        )

    reject_handler_json(
        lambda value: value["media"][1].update(locator=value["media"][0]["locator"])
    )
    reject_handler_json(
        lambda value: value.update(extra=True),
        label="runtime asset manifest",
    )
    reject_handler_json(
        lambda value: value.update(assets=[]),
        label="runtime asset manifest",
    )
    reject_handler_json(
        lambda value: value["assets"][0].update(extra=True),
        label="runtime asset manifest",
    )
    reject_handler_json(
        lambda value: value["assets"][0].update(selected_model_ids=[]),
        label="runtime asset manifest",
    )
    reject_handler_json(
        lambda value: value["assets"][0]["files"][0].update(extra=True),
        label="runtime asset manifest",
    )
    reject_handler_json(
        lambda value: value["assets"][0]["files"][0].update(path=1),
        label="runtime asset manifest",
    )
    reject_handler_json(
        lambda value: value["assets"][0]["files"][0].update(sha256="invalid"),
        label="runtime asset manifest",
    )

    def conflicting_runtime_member(value):
        asset = value["assets"][0]
        duplicate = {**asset["files"][0], "path": "z.bin", "bytes": 2}
        asset["files"].append(duplicate)
        asset["file_count"] = 2

    reject_handler_json(conflicting_runtime_member, label="runtime asset manifest")
    reject_handler_json(
        lambda value: value["assets"][0].update(bytes=0),
        label="runtime asset manifest",
    )
    reject_handler_json(
        lambda value: value["assets"][0].update(asset_id="002_wrong"),
        label="runtime asset manifest",
    )

    handler_receipt_ref = next(
        item
        for item in receipt.artifact_closure
        if item.sha256 == execution.execution_receipt_sha256
    )
    handler_receipt = real_load_json(
        store.resolve(handler_receipt_ref).path,
        label="handler execution receipt",
    )
    runtime_assets = handler_receipt["runtime_assets"]
    runtime_manifest_ref = ArtifactRef.model_validate(runtime_assets["manifest"])
    resolved_scene = replay_qualification_module._verify_environment_package(
        store,
        package=package,
        manifest=package_manifest,
        case_binding=verified.case_binding,
    )
    with pytest.raises(replay_qualification_module.ReplayQualificationError):
        replay_qualification_module._verify_runtime_asset_manifest(
            store,
            manifest=runtime_manifest_ref,
            members=(),
            resolved_scene=resolved_scene,
            asset_catalog_sha256=package.asset_catalog.sha256,
        )


def test_real_check_builder_rereads_receipts_transcripts_media_physics_and_cas(
    tmp_path: Path,
) -> None:
    assembly, compile_output, replay_input, direct, kernel = _inspection_case(tmp_path)
    marker = _put_json(
        assembly.artifact_store,
        tmp_path,
        name="qualification_marker",
        value={"marker": True},
        schema_version="test.marker.v1",
    )

    checks = module._build_execution_checks(
        assembly,
        compile_output=compile_output,
        replay_input=replay_input,
        direct=direct,
        kernel=kernel,
        operator_before_ref=marker,
        operator_after_ref=marker,
        direct_supervisor_ref=marker,
        kernel_evaluation_ref=marker,
        closure_ref=marker,
    )

    assert tuple(check.name for check in checks) == (
        "01.case_binding",
        "02.exact_dependencies",
        "03.event_lifecycle",
        "04.runtime_asset_snapshot",
        "05.media_decode",
        "06.physics_validation",
        "08.candidate_kernel_executions",
    )
    evidence = {check.name: check.evidence for check in checks}
    assert evidence["03.event_lifecycle"]["candidate_direct"]["event_count"] == 14
    assert evidence["04.runtime_asset_snapshot"]["member_count"] == 1
    assert evidence["05.media_decode"]["candidate_direct"]["decoded_png_count"] == 7
    assert evidence["06.physics_validation"]["production_kernel_candidate"]["status"] == "pass"
    assert (
        evidence["08.candidate_kernel_executions"]["production_kernel_candidate"]["mode"]
        == "qualification_candidate"
    )


def test_fixed_case_orchestrates_compile_direct_and_kernel_on_one_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    settings.scratch_root.mkdir()
    assembly, compile_output, replay_input, direct, kernel = _inspection_case(
        tmp_path / "inspection"
    )

    class CompileFacade:
        def compile(self, **parameters):
            assert parameters == {
                "request": "Place a can on top of a plate.",
                "seed": 7,
                "asset_catalog_path": settings.asset_catalog_path,
                "generate_missing_assets": False,
            }
            return _harness_state(
                run_id=UUID("72000000-0000-4000-8000-000000000001"),
                invocation_digest=SHA_A,
                output=Text2EnvReplayOutput(
                    runtime_evidence=direct.output.runtime_evidence,
                    replay_artifacts=(),
                ),
                artifacts=(),
            ).model_copy(update={"output": compile_output.model_dump(mode="json")})

    assembly = replace(assembly, compile_application=CompileFacade())
    received_inputs: list[Text2EnvReplayInput] = []

    def direct_run(_assembly, value, scratch):
        assert scratch == settings.scratch_root
        received_inputs.append(value)
        return direct

    def kernel_run(_assembly, value, scratch):
        assert scratch == settings.scratch_root
        received_inputs.append(value)
        return kernel

    monkeypatch.setattr(module, "_assemble_fixed_case", lambda *_args: assembly)
    monkeypatch.setattr(module, "_run_direct_candidate", direct_run)
    monkeypatch.setattr(module, "_run_production_kernel_candidate", kernel_run)

    execution = module._execute_fixed_case(settings, settings.scratch_root)

    assert received_inputs == [replay_input, replay_input]
    assert execution.artifact_store is assembly.artifact_store
    assert execution.direct_supervisor_ref.schema_version == (
        "harness.replay_qualification.direct_supervisor_receipt.v1"
    )
    assert execution.kernel_evaluation_ref.schema_version == (
        "harness.replay_qualification.kernel_evaluation_receipt.v1"
    )


def test_direct_and_production_kernel_paths_execute_same_handler_without_registration(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    compile_output = _compile_fixture(store, tmp_path / "source")
    runtime_asset = _put_json(
        store,
        tmp_path / "source",
        name="runtime_asset_snapshot",
        value={"schema_version": "harness.runtime_asset_snapshot.v1"},
        schema_version="harness.runtime_asset_snapshot.v1",
    )
    runtime_member = _put_bytes(
        store,
        tmp_path / "source",
        name="runtime_asset_member",
        payload=b"asset member",
        media_type="application/octet-stream",
        schema_version=None,
    )
    observations: list[module._RuntimeObservation] = []
    calls: list[UUID] = []

    def handler(_value, context):
        calls.append(context.run_id)
        completed = _completed_fixture(
            store,
            tmp_path / str(context.run_id),
            package=compile_output.environment_package,
            runtime_asset=runtime_asset,
            runtime_member=runtime_member,
            unique_frames=114,
            run_id=context.run_id,
        )
        observations.append(completed.observation)
        return HandlerResult(output=completed.output, artifacts=completed.artifacts)

    dependencies = _completed_fixture(
        store,
        tmp_path / "dependency-template",
        package=compile_output.environment_package,
        runtime_asset=runtime_asset,
        runtime_member=runtime_member,
        unique_frames=114,
        run_id=UUID("73000000-0000-4000-8000-000000000001"),
    ).dependencies

    class Resolver:
        def resolve(self, skill_ref, value):
            assert skill_ref == "text2env.replay@1.0.0"
            assert isinstance(value, Text2EnvReplayInput)
            return dependencies

    assembly = module._ReplayAssembly(
        compile_application=SimpleNamespace(),
        artifact_store=store,
        package_store=SimpleNamespace(),
        wiring=SimpleNamespace(handler=handler, dependency_resolver=Resolver()),
        runtime_executor=SimpleNamespace(observations=observations),
    )
    replay_input = Text2EnvReplayInput(
        environment_package=compile_output.environment_package,
        runtime_config=module._RUNTIME_CONFIG,
    )

    direct = module._run_direct_candidate(assembly, replay_input, tmp_path / "direct-run")
    kernel = module._run_production_kernel_candidate(assembly, replay_input, tmp_path)

    assert calls == [module._DIRECT_RUN_ID, module._KERNEL_RUN_ID]
    assert direct.state.status is kernel.state.status is RunStatus.SUCCEEDED
    assert direct.dependencies == kernel.dependencies
    assert direct.invocation_digest == kernel.invocation_digest
    assert all(event.stage.startswith("qualification_candidate.") for event in kernel.state.events)


def test_observed_runtime_executor_records_exact_delivered_events(tmp_path: Path) -> None:
    events, transcript = _runtime_events()
    execution = RuntimeExecution(
        status=RuntimeExecutionStatus.SUCCEEDED,
        failure=None,
        exit_code=0,
        attempt_root=tmp_path,
        capability=None,
        postflight_capability_sha256=SHA_A,
        events=events,  # type: ignore[arg-type]
        transcript=transcript,
        stdout=b"",
        stderr=b"",
        transcript_complete=True,
        transcript_truncated=False,
        stdout_truncated=False,
        stderr_truncated=False,
        output_files=(),
    )

    class Executor:
        identity = SimpleNamespace(sha256=SHA_C)

        def execute(self, job, observer):
            for event in events:
                observer(event)
            return execution

    wrapper = module._ObservedRuntimeExecutor(Executor())
    delivered = []

    assert wrapper.identity.sha256 == SHA_C
    assert wrapper.execute(SimpleNamespace(), delivered.append) is execution
    assert delivered == list(events)
    assert wrapper.observations == [
        module._RuntimeObservation(execution=execution, delivered_events=events)
    ]


def test_production_assembly_uses_only_settings_and_fixed_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    scratch = tmp_path / "scratch-real"
    scratch.mkdir()
    app_root = tmp_path / "app-cas"
    app_root.mkdir()
    app = SimpleNamespace(artifact_root=app_root)
    runtime = SimpleNamespace(identity=SimpleNamespace(sha256=SHA_C))
    sandbox = SimpleNamespace(identity=SimpleNamespace(sha256=SHA_D))
    verifier = SimpleNamespace(identity=SimpleNamespace(sha256=SHA_F))
    wiring = SimpleNamespace(handler=object(), dependency_resolver=object())
    captured: dict[str, object] = {}

    def compile_factory(value):
        captured["compile"] = value
        return app

    def runtime_factory(**values):
        captured["runtime"] = values
        return runtime

    def sandbox_factory(**values):
        captured["sandbox"] = values
        return sandbox

    def verifier_factory(**values):
        captured["verifier"] = values
        return verifier

    def wiring_factory(**values):
        captured["wiring"] = values
        return wiring

    monkeypatch.setattr(module, "create_compile_application", compile_factory)
    monkeypatch.setattr(module, "SubprocessRoboTwinRuntimeExecutor", runtime_factory)
    monkeypatch.setattr(module, "NativeCgroupSandbox", sandbox_factory)
    monkeypatch.setattr(module, "SubprocessReplayMediaVerifier", verifier_factory)
    monkeypatch.setattr(module, "build_text2env_replay_wiring", wiring_factory)
    monkeypatch.setattr(module, "_load_capability_digest", lambda _path: SHA_A)

    assembly = module._assemble_fixed_case(settings, scratch)

    assert assembly.compile_application is app
    assert assembly.artifact_store.root == app_root.resolve()
    assert captured["runtime"] == {
        "interpreter": settings.interpreter,
        "runner": settings.runtime_runner,
        "work_root": scratch / "runtime-executor",
        "timeout_seconds": 1800.0,
        "capability_timeout_seconds": 30.0,
        "module_root": settings.runtime_module_root,
    }
    assert captured["wiring"]["expected_capability_sha256"] == SHA_A
    assert captured["wiring"]["task_config"] == "demo_clean"
    assert captured["wiring"]["min_visible_pixels"] == 64
    assert captured["wiring"]["checkpoint_steps"] == 120


def test_compile_dependency_and_direct_handler_failures_are_typed_and_publish_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    scratch = tmp_path / "scratch-errors"
    scratch.mkdir()
    assembly, _, replay_input, direct, _ = _inspection_case(tmp_path / "inspection")

    invalid_state = direct.state.model_copy(update={"output": {}})
    invalid_compile = replace(
        assembly,
        compile_application=SimpleNamespace(compile=lambda **_values: invalid_state),
    )
    monkeypatch.setattr(module, "_assemble_fixed_case", lambda *_args: invalid_compile)
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._execute_fixed_case(settings, scratch)
    assert captured.value.reason == "compile_output_invalid"

    class BrokenResolver:
        def resolve(self, *_args):
            raise RuntimeError("dependency failed")

    broken_dependencies = replace(
        assembly,
        wiring=SimpleNamespace(
            dependency_resolver=BrokenResolver(),
            handler=assembly.wiring.handler,
        ),
    )
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._run_direct_candidate(
            broken_dependencies,
            replay_input,
            tmp_path / "broken-dependencies",
        )
    assert captured.value.reason == "dependency_resolution_failed"

    class FixedResolver:
        def resolve(self, *_args):
            return direct.dependencies

    def broken_handler(*_args):
        raise RuntimeError("handler failed")

    broken_execution = replace(
        assembly,
        wiring=SimpleNamespace(
            dependency_resolver=FixedResolver(),
            handler=broken_handler,
        ),
    )
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._run_direct_candidate(
            broken_execution,
            replay_input,
            tmp_path / "broken-execution",
        )
    assert captured.value.reason == "candidate_execution_failed"


def test_json_artifact_and_recursive_reference_helpers_fail_closed(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    artifact = _put_bytes(
        store,
        tmp_path,
        name="broken_json",
        payload=b"{broken",
        media_type="application/json",
        schema_version="test.broken.v1",
    )
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._read_json_artifact(store, artifact, label="broken")
    assert captured.value.reason == "evidence_unreadable"

    scalar = _put_json(
        store,
        tmp_path,
        name="scalar_json",
        value=[],
        schema_version="test.scalar.v1",
    )
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._read_json_artifact(store, scalar, label="scalar")
    assert captured.value.reason == "evidence_shape"

    assert module._artifact_refs({"items": [artifact, None]}) == (artifact,)
    with pytest.raises(ReplayQualificationGenerationError):
        module._one_artifact((artifact,), schema_version="missing.v1", label="missing")
    with pytest.raises(ReplayQualificationGenerationError):
        module._one_named_artifact((artifact,), name="missing", label="missing")
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._artifact_records(
            [artifact.model_copy(update={"sha256": SHA_A}).model_dump(mode="json")],
            (artifact,),
            label="attacked record",
        )
    assert captured.value.reason == "artifact_receipt_mismatch"
    with pytest.raises(ReplayQualificationGenerationError):
        module._array({}, label="not array")
    with pytest.raises(ReplayQualificationGenerationError):
        module._gate(False, "deliberate", "deliberate gate")


def test_capability_locator_requires_strict_canonical_valid_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "validate_runtime_capability_document", lambda value: value)
    valid = tmp_path / "valid.json"
    valid.write_bytes(b"{}\n")
    assert module._load_capability_digest(valid) == hashlib.sha256(b"{}\n").hexdigest()

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_bytes(b"{ }\n")
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._load_capability_digest(noncanonical)
    assert captured.value.reason == "runtime_capability_invalid"

    for name, payload in (
        ("broken", b"{broken"),
        ("duplicate", b'{"a":1,"a":2}\n'),
        ("constant", b'{"a":NaN}\n'),
    ):
        path = tmp_path / f"{name}.json"
        path.write_bytes(payload)
        with pytest.raises(ReplayQualificationGenerationError) as captured:
            module._load_capability_digest(path)
        assert captured.value.reason == "runtime_capability_invalid"


def test_operator_locator_root_and_timeout_validation_is_strict(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    directory = tmp_path / "directory"
    directory.mkdir()
    file_path = tmp_path / "file"
    file_path.write_text("x", encoding="utf-8")
    linked_file = tmp_path / "linked-file"
    linked_file.symlink_to(file_path)
    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(directory, target_is_directory=True)

    for value in ("not-a-path", missing, directory, linked_file):
        with pytest.raises(ReplayQualificationGenerationError) as captured:
            module._regular_file(value, label="test")  # type: ignore[arg-type]
        assert captured.value.reason == "operator_locator_invalid"
    assert module._regular_file(file_path, label="test") == file_path.resolve()

    second_directory = tmp_path / "a-directory"
    second_directory.mkdir()
    for roots in (
        (),
        ("not-a-path",),
        (missing,),
        (file_path,),
        (linked_directory,),
        (directory, directory),
        (directory, second_directory),
    ):
        with pytest.raises(ReplayQualificationGenerationError) as captured:
            module._trusted_roots(roots)  # type: ignore[arg-type]
        assert captured.value.reason == "allowed_asset_roots_invalid"
    assert module._trusted_roots((directory,)) == (directory.resolve(),)

    for value in ("not-a-path", missing, file_path, linked_directory):
        with pytest.raises(ReplayQualificationGenerationError) as captured:
            module._operator_directory(value, label="directory")  # type: ignore[arg-type]
        assert captured.value.reason == "operator_locator_invalid"
    assert module._operator_directory(directory, label="directory") == directory.resolve()

    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._new_output_root("bad", label="bundle")  # type: ignore[arg-type]
    assert captured.value.reason == "invalid_bundle_root"
    for timeout in (True, 0, float("inf")):
        with pytest.raises(ReplayQualificationGenerationError) as captured:
            module._positive_timeout(timeout, label="timeout")
        assert captured.value.reason == "invalid_timeout"


def test_source_snapshot_rejects_invalid_root_symlink_missing_and_directory(
    tmp_path: Path,
) -> None:
    invalid_root = replace(_settings(tmp_path, suffix="-invalid"), distribution_root="bad")
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._snapshot_sources(invalid_root, phase="before_execution")  # type: ignore[arg-type]
    assert captured.value.reason == "implementation_root_invalid"

    aliased_root = tmp_path / "distribution-alias"
    aliased_root.symlink_to(ROOT, target_is_directory=True)
    alias_settings = replace(
        _settings(tmp_path, suffix="-alias"),
        distribution_root=aliased_root,
    )
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._snapshot_sources(alias_settings, phase="before_execution")
    assert captured.value.reason == "implementation_root_invalid"

    missing_root = replace(
        _settings(tmp_path, suffix="-missing-root"),
        distribution_root=tmp_path / "missing-distribution",
    )
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        module._snapshot_sources(missing_root, phase="before_execution")
    assert captured.value.reason == "implementation_root_invalid"

    distribution = tmp_path / "distribution"
    harness = distribution / "self_improving/harness"
    harness.mkdir(parents=True)
    first = harness / "linked.py"
    outside = tmp_path / "outside.py"
    outside.write_text("outside = True\n", encoding="utf-8")
    first.symlink_to(outside)
    with pytest.raises(module.ReplayQualificationError) as captured:
        module._snapshot_harness_source(distribution, phase="before_execution")
    assert captured.value.reason == "implementation_path_invalid"

    first.unlink()
    with pytest.raises(module.ReplayQualificationError) as captured:
        module._snapshot_harness_source(distribution, phase="before_execution")
    assert captured.value.reason == "implementation_path_invalid"

    first.mkdir()
    regular = harness / "__init__.py"
    regular.write_text("\n", encoding="utf-8")
    snapshot = module._snapshot_harness_source(distribution, phase="before_execution")
    assert tuple(item.path for item in snapshot.files) == ("self_improving/harness/__init__.py",)


def test_atomic_writer_rejects_existing_and_removes_post_publish_uncertainty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = {
        "manifest.json": b"{}\n",
        "qualification.json": b"{}\n",
        "report.json": b"{}\n",
    }
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "marker"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        module._write_bundle_atomically(existing, documents)
    assert marker.read_text(encoding="utf-8") == "keep"

    bundle = tmp_path / "bundle"
    real_fsync = module.os.fsync
    calls = 0

    def fail_directory_sync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("directory sync failed")
        real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_directory_sync)
    with pytest.raises(OSError, match="directory sync failed"):
        module._write_bundle_atomically(bundle, documents)
    assert not bundle.exists()


def test_existing_bundle_or_scratch_is_rejected_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("execution must not start")

    monkeypatch.setattr(module, "_execute_fixed_case", forbidden)
    bundle_settings = _settings(tmp_path, suffix="-bundle")
    bundle_settings.bundle_root.mkdir()
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        generate_replay_qualification(bundle_settings)
    assert captured.value.reason == "bundle_exists"

    scratch_settings = _settings(tmp_path, suffix="-scratch")
    scratch_settings.scratch_root.mkdir()
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        generate_replay_qualification(scratch_settings)
    assert captured.value.reason == "scratch_exists"
    assert calls == 0


def test_execution_failure_and_source_drift_publish_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = _settings(tmp_path, suffix="-failed")

    def fail(*_args, **_kwargs):
        raise ReplayQualificationGenerationError("candidate_failed", "deliberate")

    monkeypatch.setattr(module, "_execute_fixed_case", fail)
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        generate_replay_qualification(failed)
    assert captured.value.reason == "candidate_failed"
    assert not failed.bundle_root.exists()

    drift = _settings(tmp_path, suffix="-drift")
    _install_execution(
        monkeypatch,
        _valid_execution(tmp_path / "drift-execution", drift),
    )
    original = module._snapshot_sources
    calls = 0

    def changing(settings, *, phase):
        nonlocal calls
        calls += 1
        snapshot = original(settings, phase=phase)
        if calls == 2:
            return replace(snapshot, scene_gen_sha256=SHA_A)
        return snapshot

    monkeypatch.setattr(module, "_snapshot_sources", changing)
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        generate_replay_qualification(drift)
    assert captured.value.reason == "source_changed"
    assert not drift.bundle_root.exists()


def test_operator_closure_and_final_rescan_drift_publish_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_drift = _settings(tmp_path, suffix="-operator")
    _install_execution(
        monkeypatch,
        _valid_execution(tmp_path / "operator-execution", operator_drift),
    )
    real_operator_snapshot = module._snapshot_operator_inputs

    def changed_operator(*args, **kwargs):
        manifest, ref = real_operator_snapshot(*args, **kwargs)
        return manifest.model_copy(update={"inputs_sha256": SHA_A}), ref

    monkeypatch.setattr(module, "_snapshot_operator_inputs", changed_operator)
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        generate_replay_qualification(operator_drift)
    assert captured.value.reason == "operator_inputs_changed"
    assert not operator_drift.bundle_root.exists()

    monkeypatch.setattr(module, "_snapshot_operator_inputs", real_operator_snapshot)
    invalid_closure = _settings(tmp_path, suffix="-closure")
    _install_execution(
        monkeypatch,
        _valid_execution(tmp_path / "closure-execution", invalid_closure),
    )

    def fail_closure(*_args, **_kwargs):
        raise module.ReplayQualificationError("closure_attack", "deliberate")

    monkeypatch.setattr(module, "_discover_evidence_closure", fail_closure)
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        generate_replay_qualification(invalid_closure)
    assert captured.value.reason == "evidence_closure_invalid"
    assert not invalid_closure.bundle_root.exists()

    monkeypatch.undo()
    final_drift = _settings(tmp_path, suffix="-final")
    _install_execution(
        monkeypatch,
        _valid_execution(tmp_path / "final-execution", final_drift),
    )
    real_source_snapshot = module._snapshot_sources
    calls = 0

    def final_source_changed(settings, *, phase):
        nonlocal calls
        calls += 1
        snapshot = real_source_snapshot(settings, phase=phase)
        if calls == 3:
            return replace(snapshot, scene_gen_sha256=SHA_A)
        return snapshot

    monkeypatch.setattr(module, "_snapshot_sources", final_source_changed)
    with pytest.raises(ReplayQualificationGenerationError) as captured:
        generate_replay_qualification(final_drift)
    assert captured.value.reason == "prepublish_inputs_changed"
    assert not final_drift.bundle_root.exists()


@pytest.mark.parametrize(
    ("check_index", "field", "replacement"),
    [
        (0, "seed", 8),
        (1, "dependencies", []),
        (2, "checkpoint_steps", 119),
        (3, "self_contained", False),
        (4, "verifier_identity_sha256", SHA_A),
        (5, "candidate_direct", {}),
        (6, "candidate_direct", {}),
    ],
)
def test_each_execution_gate_attack_fails_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    check_index: int,
    field: str,
    replacement: object,
) -> None:
    settings = _settings(tmp_path, suffix=f"-{check_index}")
    execution = _valid_execution(tmp_path / f"execution-{check_index}", settings)
    _install_execution(monkeypatch, execution)
    real_builder = module._build_execution_checks

    def attacked_builder(*args, **kwargs):
        checks = list(real_builder(*args, **kwargs))
        payload = checks[check_index].model_dump(mode="json")
        payload["evidence"][field] = replacement
        checks[check_index] = QualificationCheckV1.model_validate(payload)
        return tuple(checks)

    monkeypatch.setattr(module, "_build_execution_checks", attacked_builder)

    with pytest.raises(ReplayQualificationGenerationError) as captured:
        generate_replay_qualification(settings)

    assert captured.value.reason == "qualification_gate_failed"
    assert not settings.bundle_root.exists()


def test_atomic_publish_failure_leaves_no_bundle_or_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _install_execution(monkeypatch, _valid_execution(tmp_path / "execution", settings))

    real_replace = module.os.replace

    def fail_replace(source: Path, destination: Path) -> None:
        if Path(destination) == settings.bundle_root.absolute():
            raise OSError("injected rename failure")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="rename failure"):
        generate_replay_qualification(settings)

    assert not settings.bundle_root.exists()
    assert not any(path.name.startswith(".bundle.") for path in tmp_path.iterdir())
