#!/usr/bin/env python3
"""Run the dated compile-chain acceptance campaign and preserve its evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from scene_gen.builder import verify_package
from scene_gen.catalog import AssetCatalog
from scene_gen.schema import ResolvedSceneSpec
from self_improving.compile_acceptance import (
    AcceptanceCase,
    CampaignAcceptanceError,
    build_acceptance_plan,
    validate_campaign_summary,
)
from self_improving.harness.application import (
    CompileApplication,
    CompileApplicationSettings,
    create_compile_application,
)
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.run_receipts import (
    PortableRunReceiptError,
    PortableRunReceiptPublisher,
    load_portable_run_receipt,
)
from self_improving.harness.schemas import ArtifactRef, RunStatus, Text2EnvCompileOutput

SCHEMA = "harness.compile_acceptance_campaign.v1"


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _empty_catalog(path: Path, objects_root: Path) -> Path:
    objects_root.mkdir(parents=True, exist_ok=True)
    robotwin_root = objects_root.parent
    _json(
        path,
        {
            "schema_version": "robotwin.asset_catalog.v1",
            "robotwin_root": str(robotwin_root.resolve()),
            "objects_root": str(objects_root.resolve()),
            "source_commit": None,
            "entries": [],
        },
    )
    return path


def _artifact_json(app: CompileApplication, state: Any, schema: str) -> dict[str, Any] | None:
    refs = [artifact for artifact in state.artifacts if artifact.schema_version == schema]
    if not refs:
        return None
    return json.loads(app.resolve_artifact(refs[-1]).read_text(encoding="utf-8"))


def _events(app: CompileApplication, run_id: Any) -> list[dict[str, Any]]:
    page = app.events(run_id=run_id, limit=200)
    if page.has_more:
        raise RuntimeError("acceptance run emitted more than 200 events")
    return [
        {
            "event_id": item.event_id,
            "run_id": str(item.envelope.run_id),
            "skill_id": item.envelope.skill_id,
            "skill_version": item.envelope.skill_version,
            "event": item.envelope.event.model_dump(mode="json"),
        }
        for item in page.events
    ]


def _artifact_named(state: Any, name: str) -> ArtifactRef:
    matches = [artifact for artifact in state.artifacts if artifact.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {name!r} artifact, got {len(matches)}")
    return matches[0]


def _materialize_environment(
    *,
    app: CompileApplication,
    state: Any,
    output: Text2EnvCompileOutput,
    case_root: Path,
) -> dict[str, Any]:
    """Rebuild the complete scene package from compile CAS references."""

    environment_root = case_root / "environment"
    environment_root.mkdir(parents=True, exist_ok=False)
    artifacts = {
        "request.txt": _artifact_named(state, "request"),
        "scene_spec.json": output.scene_spec,
        "resolved_scene.json": output.resolved_scene,
        "generated_scene.py": _artifact_named(state, "generated_scene"),
        "package_manifest.json": output.environment_package.package_manifest,
        "effective_asset_catalog.json": output.environment_package.asset_catalog,
        "static_validation.json": output.static_validation,
    }
    materialized: dict[str, dict[str, Any]] = {}
    for filename, artifact in artifacts.items():
        source = app.resolve_artifact(artifact)
        target = environment_root / filename
        shutil.copy2(source, target)
        if _sha(target) != artifact.sha256 or target.stat().st_size != artifact.bytes:
            raise RuntimeError(f"materialized artifact drifted: {filename}")
        materialized[filename] = artifact.model_dump(mode="json")

    resolved = ResolvedSceneSpec.model_validate_json(
        (environment_root / "resolved_scene.json").read_text(encoding="utf-8")
    )
    verification = verify_package(environment_root)
    _json(environment_root / "package_verification.json", verification)
    _json(
        environment_root / "environment_package.json",
        output.environment_package.model_dump(mode="json"),
    )
    expected_digest = output.environment_package.resolved_scene_sha256
    if resolved.digest() != expected_digest or verification["status"] != "pass":
        raise RuntimeError("materialized environment package failed digest verification")
    return {
        "package_id": output.environment_package.package_id,
        "resolved_scene_sha256": expected_digest,
        "materialized_root": str(environment_root.resolve()),
        "package_verification": verification,
        "artifacts": materialized,
    }


def _render_environment_media(
    *,
    case: AcceptanceCase,
    environment: dict[str, Any],
    media_root: Path,
    repo_root: Path,
    robotwin_root: Path,
    runtime_python: Path,
    frame_count: int,
) -> dict[str, Any]:
    """Load the compiled scene in SAPIEN and capture non-gating environment media."""

    root = media_root / case.case_id
    root.mkdir(parents=True, exist_ok=False)
    materialized = Path(environment["materialized_root"])
    command = [
        str(runtime_python),
        str(repo_root / "script" / "run_scene_runtime.py"),
        "--robotwin-root",
        str(robotwin_root),
        "--resolved-scene",
        str(materialized / "resolved_scene.json"),
        "--asset-catalog",
        str(materialized / "effective_asset_catalog.json"),
        "--out-dir",
        str(root),
        "--precheck-steps",
        "0",
        "--settle-steps",
        "1",
        "--contact-window-steps",
        "1",
        "--video-frames",
        str(frame_count),
        "--fps",
        "12",
        "--checkpoint-steps",
        str(frame_count),
        "--evidence-only",
    ]
    process_environment = os.environ.copy()
    current_pythonpath = process_environment.get("PYTHONPATH")
    process_environment["PYTHONPATH"] = (
        str(repo_root)
        if not current_pythonpath
        else f"{repo_root}{os.pathsep}{current_pythonpath}"
    )
    completed = subprocess.run(
        command,
        cwd=repo_root,
        env=process_environment,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    (root / "command.json").write_text(
        json.dumps(command, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (root / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    evidence_path = root / "runtime_evidence.json"
    validation_path = root / "runtime_validation_report.json"
    evidence = (
        json.loads(evidence_path.read_text(encoding="utf-8"))
        if evidence_path.is_file()
        else {}
    )
    validation = (
        json.loads(validation_path.read_text(encoding="utf-8"))
        if validation_path.is_file()
        else {}
    )
    loaded = (
        completed.returncode == 0
        and evidence.get("status") == "pass"
        and evidence.get("resolved_scene_sha256") == environment["resolved_scene_sha256"]
    )
    load_smoke = {
        "status": "loaded" if loaded else "failed",
        "returncode": completed.returncode,
        "resolved_scene_sha256": evidence.get("resolved_scene_sha256"),
        "physics_steps_for_preview": evidence.get("total_physics_step_count"),
        "validation_status_not_used_as_compile_gate": validation.get("status"),
        "stdout": str((root / "stdout.log").resolve()),
        "stderr": str((root / "stderr.log").resolve()),
    }
    environment["load_smoke"] = load_smoke
    required = {
        "preview": root / "preview_head.png",
        "world_left": root / "preview_world_left.png",
        "world_right": root / "preview_world_right.png",
        "video": root / "observer_runtime.mp4",
    }
    if loaded and not all(path.is_file() for path in required.values()):
        raise RuntimeError(f"compiled environment media is incomplete for {case.case_id}")
    return {
        "kind": "compiled_environment_preview",
        "source": "sapien_environment_load_smoke",
        "physical_validation_gate_enforced": False,
        "resolved_scene_sha256": environment["resolved_scene_sha256"],
        **{name: str(path.resolve()) for name, path in required.items()},
        "preview_sha256": _sha(required["preview"]) if required["preview"].is_file() else None,
        "video_sha256": _sha(required["video"]) if required["video"].is_file() else None,
        "frame_count": evidence.get("video_frame_count"),
        "unique_frame_count": evidence.get("unique_video_frame_count"),
        "load_smoke": load_smoke,
    }


def _media_type(path: Path) -> str:
    return {
        ".json": "application/json",
        ".obj": "model/obj",
        ".mtl": "model/mtl",
        ".glb": "model/gltf-binary",
        ".urdf": "application/xml",
        ".png": "image/png",
        ".mp4": "video/mp4",
        ".py": "text/x-python",
        ".txt": "text/plain",
    }.get(path.suffix.lower(), "application/octet-stream")


def _publish_environment_evidence(
    *,
    case: AcceptanceCase,
    environment: dict[str, Any],
    media: dict[str, Any],
    main_store: LocalArtifactStore,
    portable_store: LocalArtifactStore,
    case_root: Path,
) -> dict[str, Any]:
    """Put the materialized package, selected assets, and media in both CAS roots."""

    materialized_root = Path(environment["materialized_root"])
    resolved = ResolvedSceneSpec.model_validate_json(
        (materialized_root / "resolved_scene.json").read_text(encoding="utf-8")
    )
    files: list[Path] = [
        path
        for path in sorted(materialized_root.iterdir())
        if path.is_file()
    ]
    for item in resolved.objects:
        files.extend(Path(value) for value in item.source_files if Path(value).is_file())
    for key in ("preview", "world_left", "world_right", "video"):
        value = media.get(key)
        if value and Path(value).is_file():
            files.append(Path(value))
    unique_files = {str(path.resolve()): path.resolve() for path in files}
    main_refs: list[dict[str, Any]] = []
    portable_refs: list[dict[str, Any]] = []
    for index, path in enumerate(unique_files.values()):
        name = f"{case.case_id}-{index:02d}-{path.name}"
        kwargs = {
            "name": name,
            "media_type": _media_type(path),
            "schema_version": None,
        }
        main_refs.append(main_store.put_file(path, **kwargs).model_dump(mode="json"))
        portable_refs.append(portable_store.put_file(path, **kwargs).model_dump(mode="json"))
    receipt = {
        "schema_version": "harness.compiled_environment_evidence.v1",
        "case_id": case.case_id,
        "resolved_scene_sha256": environment["resolved_scene_sha256"],
        "materialized_root": environment["materialized_root"],
        "package_verification_status": environment["package_verification"]["status"],
        "load_smoke": environment["load_smoke"],
        "main_cas_artifacts": main_refs,
        "portable_cas_artifacts": portable_refs,
    }
    receipt_path = case_root / "compiled_environment_evidence.json"
    _json(receipt_path, receipt)
    main_receipt_ref = main_store.put_file(
        receipt_path,
        name=f"{case.case_id}-compiled-environment-evidence",
        media_type="application/json",
        schema_version="harness.compiled_environment_evidence.v1",
    ).model_dump(mode="json")
    portable_receipt_ref = portable_store.put_file(
        receipt_path,
        name=f"{case.case_id}-compiled-environment-evidence",
        media_type="application/json",
        schema_version="harness.compiled_environment_evidence.v1",
    ).model_dump(mode="json")
    refs = {
        "main_cas_receipt": main_receipt_ref,
        "portable_cas_receipt": portable_receipt_ref,
    }
    _json(case_root / "compiled_environment_evidence_refs.json", refs)
    return {**receipt, **refs}


def _attach_environment_evidence(
    *,
    app: CompileApplication,
    state: Any,
    output: Text2EnvCompileOutput | None,
    case: AcceptanceCase,
    record: dict[str, Any],
    root: Path,
    repo_root: Path,
    robotwin_root: Path,
    runtime_python: Path,
    preview_frames: int,
    main_store: LocalArtifactStore,
    portable_store: LocalArtifactStore,
) -> None:
    if output is None:
        return
    case_root = root / "runs" / case.case_id
    environment = _materialize_environment(
        app=app,
        state=state,
        output=output,
        case_root=case_root,
    )
    media = _render_environment_media(
        case=case,
        environment=environment,
        media_root=root / "media",
        repo_root=repo_root,
        robotwin_root=robotwin_root,
        runtime_python=runtime_python,
        frame_count=preview_frames,
    )
    environment_evidence = _publish_environment_evidence(
        case=case,
        environment=environment,
        media=media,
        main_store=main_store,
        portable_store=portable_store,
        case_root=case_root,
    )
    record["environment"] = environment
    record["media"] = media
    record["compiled_environment_evidence"] = environment_evidence
    _json(case_root / "summary.json", record)


def _run_case(
    *,
    app: CompileApplication,
    publisher: PortableRunReceiptPublisher,
    portable_store: LocalArtifactStore,
    case: AcceptanceCase,
    catalog_path: Path,
    generate_missing_assets: bool,
    run_root: Path,
) -> tuple[dict[str, Any], Text2EnvCompileOutput | None, Any]:
    state = app.compile(
        request=case.request,
        seed=case.seed,
        asset_catalog_path=catalog_path,
        generate_missing_assets=generate_missing_assets,
    )
    case_root = run_root / case.case_id
    case_root.mkdir(parents=True, exist_ok=False)
    invocation = app.invocation(state.run_id)
    _json(case_root / "run_state.json", state.model_dump(mode="json"))
    _json(
        case_root / "invocation.json",
        None if invocation is None else invocation.model_dump(mode="json"),
    )
    _json(case_root / "events.json", _events(app, state.run_id))

    receipt_ref = None
    receipt_verified = False
    receipt_error = None
    try:
        receipt_ref = publisher.publish(app, state.run_id)
        loaded = load_portable_run_receipt(portable_store, receipt_ref)
        receipt_verified = loaded.receipt.run_id == state.run_id and loaded.run_state == state
        _json(case_root / "portable_receipt_ref.json", receipt_ref.model_dump(mode="json"))
        _json(
            case_root / "portable_receipt.json",
            loaded.receipt.model_dump(mode="json"),
        )
    except PortableRunReceiptError as error:
        receipt_error = {"reason": error.reason, "message": str(error)}
        _json(case_root / "portable_receipt_error.json", receipt_error)

    output = None
    selected_asset_ids: list[str] = []
    if state.status is RunStatus.SUCCEEDED:
        output = Text2EnvCompileOutput.model_validate(state.output)
        resolved = json.loads(
            app.resolve_artifact(output.resolved_scene).read_text(encoding="utf-8")
        )
        selected_asset_ids = [item["asset_id"] for item in resolved["objects"]]
        _json(case_root / "typed_output.json", output.model_dump(mode="json"))

    generation = _artifact_json(app, state, "robotwin.asset_generation_report.v1")
    admission = _artifact_json(app, state, "harness.generated_asset_admission.v1")
    summary = {
        "case": {
            "case_id": case.case_id,
            "group": case.group,
            "request": case.request,
            "seed": case.seed,
            "expected_origin": case.expected_origin,
            "source_case_id": case.source_case_id,
        },
        "run_id": str(state.run_id),
        "status": state.status.value,
        "blocker": None if state.blocker is None else state.blocker.model_dump(mode="json"),
        "selected_asset_ids": selected_asset_ids,
        "generation": generation,
        "admission": admission,
        "catalog_input": str(catalog_path.resolve()),
        "catalog_input_sha256": _sha(catalog_path),
        "artifact_refs": [item.model_dump(mode="json") for item in state.artifacts],
        "portable_receipt_ref": (
            None if receipt_ref is None else receipt_ref.model_dump(mode="json")
        ),
        "portable_receipt_verified_from_destination_cas_only": receipt_verified,
        "portable_receipt_error": receipt_error,
    }
    _json(case_root / "summary.json", summary)
    return summary, output, state


def _read_obj(path: Path) -> tuple[list[tuple[float, float, float]], list[tuple[int, ...]]]:
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if fields[:1] == ["v"] and len(fields) >= 4:
            vertices.append(tuple(float(value) for value in fields[1:4]))
        elif fields[:1] == ["f"] and len(fields) >= 4:
            faces.append(tuple(int(value.split("/")[0]) - 1 for value in fields[1:]))
    if not vertices or not faces:
        raise ValueError(f"preview renderer requires OBJ vertices and faces: {path}")
    return vertices, faces


def _color(request: str) -> tuple[int, int, int]:
    colors = {
        "blue": (72, 126, 220),
        "green": (70, 160, 104),
        "orange": (225, 139, 57),
        "pink": (220, 116, 157),
        "purple": (139, 95, 191),
        "red": (202, 74, 72),
        "white": (207, 214, 224),
        "yellow": (221, 186, 70),
    }
    return next((rgb for name, rgb in colors.items() if f" {name} " in request), (110, 145, 180))


def _render_frame(
    *,
    obj_path: Path,
    request: str,
    asset_id: str,
    frame: int,
    frame_count: int,
    target: Path,
) -> None:
    vertices, faces = _read_obj(obj_path)
    angle = 2.0 * math.pi * frame / frame_count
    rotated = [
        (
            math.cos(angle) * x - math.sin(angle) * y,
            math.sin(angle) * x + math.cos(angle) * y,
            z,
        )
        for x, y, z in vertices
    ]
    minimum = tuple(min(item[axis] for item in rotated) for axis in range(3))
    maximum = tuple(max(item[axis] for item in rotated) for axis in range(3))
    span = max(maximum[0] - minimum[0], maximum[1] - minimum[1], maximum[2] - minimum[2])
    scale = 245.0 / max(span, 1e-9)
    center = tuple((minimum[axis] + maximum[axis]) / 2.0 for axis in range(3))

    def project(vertex: tuple[float, float, float]) -> tuple[float, float]:
        x, y, z = (vertex[axis] - center[axis] for axis in range(3))
        return 320 + x * scale, 260 - z * scale + y * scale * 0.32

    image = Image.new("RGB", (640, 480), (241, 244, 248))
    draw = ImageDraw.Draw(image)
    draw.ellipse((95, 365, 545, 427), fill=(205, 211, 219), outline=(172, 180, 190), width=2)
    base = _color(request)
    ordered = sorted(faces, key=lambda face: sum(rotated[index][1] for index in face) / len(face))
    for rank, face in enumerate(ordered):
        points = [project(rotated[index]) for index in face]
        factor = 0.72 + 0.25 * (rank + 1) / max(len(ordered), 1)
        fill = tuple(min(255, int(channel * factor)) for channel in base)
        draw.polygon(points, fill=fill, outline=(48, 55, 66))
    font = ImageFont.load_default()
    draw.rounded_rectangle((18, 15, 622, 76), radius=10, fill=(20, 27, 38))
    draw.text((32, 28), asset_id, fill=(255, 255, 255), font=font)
    draw.text(
        (32, 49),
        f"OBJ sha256 {_sha(obj_path)[:16]}… | frame {frame + 1}/{frame_count}",
        fill=(179, 196, 218),
        font=font,
    )
    draw.text(
        (22, 450),
        "Asset turntable preview — NOT physical replay evidence",
        fill=(98, 43, 43),
        font=font,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)


def _render_asset_media(
    *,
    case_id: str,
    request: str,
    asset_id: str,
    obj_path: Path,
    media_root: Path,
) -> dict[str, Any]:
    root = media_root / case_id
    frames = root / "frames"
    frame_count = 36
    for frame in range(frame_count):
        _render_frame(
            obj_path=obj_path,
            request=request,
            asset_id=asset_id,
            frame=frame,
            frame_count=frame_count,
            target=frames / f"frame_{frame:03d}.png",
        )
    preview = root / "preview.png"
    shutil.copy2(frames / "frame_000.png", preview)
    video = root / "turntable.mp4"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-framerate",
            "12",
            "-i",
            str(frames / "frame_%03d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {case_id}: {completed.stderr.strip()}")
    return {
        "kind": "asset_turntable_preview_not_physical_replay",
        "source_obj": str(obj_path.resolve()),
        "source_obj_sha256": _sha(obj_path),
        "preview": str(preview.resolve()),
        "preview_sha256": _sha(preview),
        "video": str(video.resolve()),
        "video_sha256": _sha(video),
        "frame_count": frame_count,
        "unique_frame_sha256_count": len({_sha(path) for path in frames.glob("*.png")}),
    }


def _blocked_card(case: AcceptanceCase, blocker: dict[str, Any], target: Path) -> dict[str, Any]:
    image = Image.new("RGB", (960, 540), (246, 242, 238))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.rounded_rectangle(
        (36, 35, 924, 505), radius=18, fill=(255, 255, 255), outline=(174, 95, 78), width=4
    )
    draw.text(
        (70, 72), f"{case.case_id} — DIGITAL COUSINS NOT AVAILABLE", fill=(140, 47, 36), font=font
    )
    draw.text((70, 125), f"Input: {case.request}", fill=(35, 42, 52), font=font)
    draw.text((70, 175), "compile status: blocked", fill=(35, 42, 52), font=font)
    draw.text((70, 215), f"blocker code: {blocker.get('code')}", fill=(35, 42, 52), font=font)
    draw.text((70, 255), f"stage: {blocker.get('stage')}", fill=(35, 42, 52), font=font)
    draw.text(
        (70, 315),
        "No Digital Cousins asset was substituted or claimed.",
        fill=(140, 47, 36),
        font=font,
    )
    draw.text(
        (70, 360),
        "See run_state.json, events.json and portable receipt for the exact evidence.",
        fill=(75, 82, 92),
        font=font,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return {"kind": "blocked_status_card", "image": str(target.resolve()), "sha256": _sha(target)}


def _contact_sheet(records: list[dict[str, Any]], target: Path, title: str) -> None:
    tile_width, tile_height = 640, 480
    rows = math.ceil(len(records) / 2)
    sheet = Image.new("RGB", (tile_width * 2, tile_height * rows + 70), (25, 32, 43))
    draw = ImageDraw.Draw(sheet)
    draw.text((24, 25), title, fill=(255, 255, 255), font=ImageFont.load_default())
    for index, record in enumerate(records):
        media = record.get("media")
        candidate = None
        if isinstance(media, dict):
            candidate = media.get("preview") or media.get("image")
        if candidate and Path(candidate).is_file():
            image = Image.open(candidate).convert("RGB")
        else:
            image = Image.new("RGB", (tile_width, tile_height), (245, 238, 235))
            tile_draw = ImageDraw.Draw(image)
            case = record.get("case") or {}
            blocker = record.get("blocker") or {}
            tile_draw.text(
                (30, 40),
                f"{case.get('case_id', 'unknown')} — NO ENVIRONMENT MEDIA",
                fill=(140, 47, 36),
                font=ImageFont.load_default(),
            )
            tile_draw.text(
                (30, 90),
                f"status: {record.get('status', 'unknown')}",
                fill=(35, 42, 52),
                font=ImageFont.load_default(),
            )
            tile_draw.text(
                (30, 125),
                f"blocker: {blocker.get('code', 'missing evidence')}",
                fill=(35, 42, 52),
                font=ImageFont.load_default(),
            )
        x = (index % 2) * tile_width
        y = 70 + (index // 2) * tile_height
        sheet.paste(image.resize((tile_width, tile_height)), (x, y))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)


def _digital_cousins_preflight(repo_root: Path) -> dict[str, Any]:
    submodule = repo_root / "external" / "digital-cousins"
    shared = Path("/home/jingxiang/workspace/robot-harness-gen-env/data/external/digital-cousins")
    searched = (submodule, shared)
    catalog_candidates = [
        str(path.resolve())
        for root in searched
        if root.exists()
        for path in root.rglob("*.json")
        if "catalog" in path.name.lower()
        or path.name.startswith("step_2_output_info")
        or path.name.startswith("step_3_output_info")
    ]
    import_probe = subprocess.run(
        [
            "/home/jingxiang/miniconda3/bin/conda",
            "run",
            "-n",
            "acdc",
            "python",
            "-c",
            (
                "import importlib.util,json; "
                "print(json.dumps({n:bool(importlib.util.find_spec(n)) for n in "
                "['digital_cousins','omnigibson','groundingdino','sam2']}))"
            ),
        ],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    try:
        imports = json.loads(import_probe.stdout.strip()) if import_probe.returncode == 0 else {}
    except json.JSONDecodeError:
        imports = {}
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "submodule_exists": submodule.is_dir(),
        "submodule_commit": subprocess.run(
            ["git", "-C", str(submodule), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        ).stdout.strip(),
        "example_images": [
            str(path.resolve())
            for path in sorted((submodule / "examples" / "images").glob("*"))
            if path.is_file()
        ],
        "searched_roots": [str(path) for path in searched],
        "compile_usable_catalog_candidates": catalog_candidates,
        "acdc_import_probe": {
            "returncode": import_probe.returncode,
            "imports": imports,
            "stderr": import_probe.stderr.strip(),
        },
        "usable_for_compile": bool(catalog_candidates) and all(imports.values()),
    }


def _manifest(root: Path) -> dict[str, Any]:
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in {"evidence_manifest.json", "report.md"}:
            records.append(
                {
                    "path": str(path.relative_to(root)),
                    "bytes": path.stat().st_size,
                    "sha256": _sha(path),
                }
            )
    return {
        "schema_version": "harness.compile_acceptance_manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root.resolve()),
        "file_count": len(records),
        "files": records,
    }


def _report(summary: dict[str, Any], root: Path) -> str:
    counts = summary["counts"]
    lines = [
        "# Compile 链路综合验收（2026-09-06）",
        "",
        f"- 用户原始验收口径：**{summary['overall_result']}**",
        f"- 本地 compile 子链：**{summary['local_compile_result']}**",
        f"- 全新生成：{counts['fresh_succeeded']}/10 成功",
        f"- 已有资产直接复用：{counts['reuse_verified']}/5 成功",
        (
            "- 本地任务等价候选复用："
            f"{counts['local_task_affordance_cousins_selected']}/5 选中并编译"
        ),
        f"- 上游 ACDC / 官方 Digital Cousin 资产：{counts['upstream_acdc_assets_used']}/5",
        f"- 完整 EnvironmentPackage 重建：{counts['environment_packages_materialized']}/20",
        f"- SAPIEN 环境加载与预览：{counts['environment_load_smokes']}/20",
        f"- 每条运行均从目标 portable CAS 独立回读验证：{counts['portable_receipts_verified']}/20",
        "",
        (
            "> 图片和 MP4 来自完整 resolved scene 在 RoboTwin/SAPIEN 中的加载预览；"
            "预览只证明环境包可重建、资产可加载并能产出画面，不作为物理 validation gate。"
        ),
        "",
        "## 关键证据",
        "",
        f"- 批次根目录：`{root.resolve()}`",
        f"- 结构化总表：`{(root / 'campaign_summary.json').resolve()}`",
        f"- CAS：`{(root / 'state' / 'cas').resolve()}`",
        f"- 可移植收据 CAS：`{(root / 'portable-cas').resolve()}`",
        f"- 入库资产：`{(root / 'asset-library' / 'generated').resolve()}`",
        f"- 复用 catalog：`{(root / 'catalogs' / 'existing-reuse.catalog.json').resolve()}`",
        "- 本地任务等价候选 catalog："
        f"`{(root / 'catalogs' / 'digital-cousin-reuse.catalog.json').resolve()}`",
        f"- 上游 ACDC 边界预检：`{(root / 'digital_cousins_preflight.json').resolve()}`",
        f"- 全量文件哈希清单：`{(root / 'evidence_manifest.json').resolve()}`",
        "",
        "## 逐条结果",
        "",
        "| 用例 | 分组 | 终态 | 资产编号 | 生成/入库/复用 | 环境加载 | 收据 | 媒体 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for record in summary["cases"]:
        admission = record.get("admission") or {}
        detail = admission.get("status")
        if record.get("digital_cousin_selection"):
            detail = "local-cousin-candidate:" + str(
                record["digital_cousin_selection"].get("source_asset_id")
            )
        elif record.get("reuse_verification"):
            detail = "exact-reuse:" + str(
                record["reuse_verification"].get("source_asset_id")
            )
        if record.get("blocker"):
            detail = f"{record['blocker']['code']}@{record['blocker']['stage']}"
        media = record.get("media") or {}
        media_path = media.get("video") or media.get("image") or "—"
        lines.append(
            (
                "| {case_id} | {group} | {status} | {assets} | {detail} | "
                "{loaded} | {receipt} | `{media}` |"
            ).format(
                case_id=record["case"]["case_id"],
                group=record["case"]["group"],
                status=record["status"],
                assets=", ".join(record["selected_asset_ids"]) or "—",
                detail=detail or "not_needed",
                loaded=(record.get("environment") or {})
                .get("load_smoke", {})
                .get("status", "—"),
                receipt="verified"
                if record["portable_receipt_verified_from_destination_cas_only"]
                else "failed",
                media=media_path,
            )
        )
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            (
                "这批验收证明 compile 的输入快照、生成、catalog、CAS、入库、精确复用、"
                "本地任务等价候选复用和完整环境包重建路径。后五条请求保留类别、几何与"
                "支撑功能，但不指定颜色和材质，因此可诚实地选择已入库资产；它们不是"
                "上游 ACDC 的 RGB→OmniGibson 管线实跑，也不是 BEHAVIOR 官方资产。"
                "因此严格按用户要求的 5 条上游 Digital Cousin 资产验收仍未完成。"
                "环境媒体来自短时 SAPIEN load smoke；"
                "物理稳定、接触和发布资格仍留给正式 replay/validate。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260906)
    parser.add_argument("--robotwin-root", type=Path, required=True)
    parser.add_argument("--runtime-python", type=Path, required=True)
    parser.add_argument("--preview-frames", type=int, default=36)
    args = parser.parse_args()
    root = args.out_root.expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[1]
    robotwin_root = args.robotwin_root.expanduser().resolve()
    runtime_python = args.runtime_python.expanduser().resolve()
    if not robotwin_root.is_dir():
        raise SystemExit(f"RoboTwin root does not exist: {robotwin_root}")
    if not runtime_python.is_file():
        raise SystemExit(f"runtime Python does not exist: {runtime_python}")
    if args.preview_frames < 2:
        raise SystemExit("--preview-frames must be at least 2")
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty evidence root: {root}")
    root.mkdir(parents=True, exist_ok=True)
    for directory in ("catalogs", "trusted-assets", "runs", "media", "asset-library"):
        (root / directory).mkdir(parents=True, exist_ok=True)

    plan = build_acceptance_plan(seed=args.seed)
    _json(
        root / "inputs.json",
        {
            "schema_version": "harness.compile_acceptance_inputs.v1",
            "campaign_seed": args.seed,
            "cases": [asdict(case) for case in plan],
        },
    )
    empty_catalog = _empty_catalog(
        root / "catalogs" / "fresh-empty.catalog.json", root / "trusted-assets" / "fresh"
    )
    settings = CompileApplicationSettings(
        state_root=root / "state",
        external_catalog_roots=(root / "catalogs",),
        allowed_asset_roots=(root / "trusted-assets", root / "asset-library"),
        admission_date=date(2026, 9, 6),
        asset_library_root=root / "asset-library",
    )
    app = create_compile_application(settings)
    main_store = LocalArtifactStore(root / "state" / "cas")
    portable_store = LocalArtifactStore(root / "portable-cas")
    publisher = PortableRunReceiptPublisher(portable_store)
    records: list[dict[str, Any]] = []
    fresh_catalogs: dict[str, AssetCatalog] = {}

    for case in plan[:10]:
        record, output, state = _run_case(
            app=app,
            publisher=publisher,
            portable_store=portable_store,
            case=case,
            catalog_path=empty_catalog,
            generate_missing_assets=True,
            run_root=root / "runs",
        )
        if output is not None:
            catalog_path = app.resolve_artifact(output.environment_package.asset_catalog)
            catalog = AssetCatalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))
            fresh_catalogs[case.case_id] = catalog
            asset_id = record["selected_asset_ids"][0]
            entry = next(item for item in catalog.entries if item.asset_id == asset_id)
        _attach_environment_evidence(
            app=app,
            state=state,
            output=output,
            case=case,
            record=record,
            root=root,
            repo_root=repo_root,
            robotwin_root=robotwin_root,
            runtime_python=runtime_python,
            preview_frames=args.preview_frames,
            main_store=main_store,
            portable_store=portable_store,
        )
        records.append(record)

    reuse_entries = []
    for case in plan[:5]:
        catalog = fresh_catalogs[case.case_id]
        selected_id = records[int(case.case_id[-2:]) - 1]["selected_asset_ids"][0]
        reuse_entries.append(next(item for item in catalog.entries if item.asset_id == selected_id))
    reuse_catalog = AssetCatalog(
        robotwin_root=str((root / "asset-library").resolve()),
        objects_root=str((root / "asset-library").resolve()),
        source_commit=None,
        entries=tuple(reuse_entries),
    )
    reuse_catalog_path = root / "catalogs" / "existing-reuse.catalog.json"
    reuse_catalog_path.write_text(
        json.dumps(
            reuse_catalog.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    for case in plan[10:15]:
        record, output, state = _run_case(
            app=app,
            publisher=publisher,
            portable_store=portable_store,
            case=case,
            catalog_path=reuse_catalog_path,
            generate_missing_assets=False,
            run_root=root / "runs",
        )
        source = next(item for item in records if item["case"]["case_id"] == case.source_case_id)
        record["reuse_verification"] = {
            "source_case_id": case.source_case_id,
            "source_asset_id": source["selected_asset_ids"][0],
            "selected_asset_id": record["selected_asset_ids"][0]
            if record["selected_asset_ids"]
            else None,
            "same_asset_id": record["selected_asset_ids"] == source["selected_asset_ids"],
            "generation_artifact_absent": record["generation"] is None,
            "admission_artifact_absent": record["admission"] is None,
            "catalog_sha256": record["catalog_input_sha256"],
        }
        _attach_environment_evidence(
            app=app,
            state=state,
            output=output,
            case=case,
            record=record,
            root=root,
            repo_root=repo_root,
            robotwin_root=robotwin_root,
            runtime_python=runtime_python,
            preview_frames=args.preview_frames,
            main_store=main_store,
            portable_store=portable_store,
        )
        _json(root / "runs" / case.case_id / "summary.json", record)
        records.append(record)

    preflight = _digital_cousins_preflight(repo_root)
    preflight["used_by_this_campaign"] = False
    preflight["reason"] = (
        "The upstream ACDC pipeline is image-to-OmniGibson and its licensed dataset cannot be "
        "silently repackaged as RoboTwin assets; this text campaign uses explicit task-affordance "
        "cousin selection over the admitted asset library instead."
    )
    _json(root / "digital_cousins_preflight.json", preflight)
    cousin_entries = []
    for case in plan[15:]:
        if case.source_case_id is None:
            raise RuntimeError(f"Digital Cousin case lacks a source case: {case.case_id}")
        catalog = fresh_catalogs[case.source_case_id]
        source = next(item for item in records if item["case"]["case_id"] == case.source_case_id)
        selected_id = source["selected_asset_ids"][0]
        entry = next(item for item in catalog.entries if item.asset_id == selected_id)
        cousin_entries.append(
            entry.model_copy(
                update={
                    "source_notes": tuple(
                        sorted({*entry.source_notes, "digital_cousin_candidate"})
                    )
                }
            )
        )
    cousin_catalog = AssetCatalog(
        robotwin_root=str((root / "asset-library").resolve()),
        objects_root=str((root / "asset-library").resolve()),
        source_commit=None,
        entries=tuple(cousin_entries),
    )
    cousin_catalog_path = root / "catalogs" / "digital-cousin-reuse.catalog.json"
    cousin_catalog_path.write_text(
        json.dumps(
            cousin_catalog.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    for case in plan[15:]:
        record, output, state = _run_case(
            app=app,
            publisher=publisher,
            portable_store=portable_store,
            case=case,
            catalog_path=cousin_catalog_path,
            generate_missing_assets=False,
            run_root=root / "runs",
        )
        source = next(item for item in records if item["case"]["case_id"] == case.source_case_id)
        selected_asset_id = (
            record["selected_asset_ids"][0] if record["selected_asset_ids"] else None
        )
        record["digital_cousin_selection"] = {
            "schema_version": "harness.digital_cousin_selection.v1",
            "status": (
                "selected"
                if selected_asset_id == source["selected_asset_ids"][0]
                else "mismatch"
            ),
            "selection_method": "task_affordance_equivalent_asset_reuse",
            "source_case_id": case.source_case_id,
            "source_request": source["case"]["request"],
            "target_request": case.request,
            "source_asset_id": source["selected_asset_ids"][0],
            "selected_asset_id": selected_asset_id,
            "same_asset_id": selected_asset_id == source["selected_asset_ids"][0],
            "exact_request_match": case.request == source["case"]["request"],
            "task_equivalence": {
                "relation": "on_table",
                "category_and_geometry_preserved": True,
                "appearance_allowed_to_differ": True,
                "loadable_sapien_representation_required": True,
            },
            "asset_generation_artifact_absent": record["generation"] is None,
            "asset_admission_artifact_absent": record["admission"] is None,
            "upstream_acdc_image_pipeline_executed": False,
        }
        record["digital_cousins_preflight"] = str(
            (root / "digital_cousins_preflight.json").resolve()
        )
        _attach_environment_evidence(
            app=app,
            state=state,
            output=output,
            case=case,
            record=record,
            root=root,
            repo_root=repo_root,
            robotwin_root=robotwin_root,
            runtime_python=runtime_python,
            preview_frames=args.preview_frames,
            main_store=main_store,
            portable_store=portable_store,
        )
        _json(root / "runs" / case.case_id / "summary.json", record)
        records.append(record)

    _contact_sheet(
        records[:10], root / "media" / "fresh-contact-sheet.png", "10 fresh compiled environments"
    )
    _contact_sheet(
        records[10:15],
        root / "media" / "reuse-contact-sheet.png",
        "5 environments reusing exact admitted asset IDs",
    )
    _contact_sheet(
        records[15:],
        root / "media" / "digital-cousins-contact-sheet.png",
        "5 environments using local task-affordance cousin candidates (not upstream ACDC)",
    )

    counts = {
        "fresh_succeeded": sum(item["status"] == "succeeded" for item in records[:10]),
        "reuse_verified": sum(
            (item.get("reuse_verification") or {}).get("same_asset_id", False)
            and item["status"] == "succeeded"
            for item in records[10:15]
        ),
        "local_task_affordance_cousins_succeeded": sum(
            item["status"] == "succeeded" for item in records[15:]
        ),
        "local_task_affordance_cousins_selected": sum(
            (item.get("digital_cousin_selection") or {}).get("status") == "selected"
            for item in records[15:]
        ),
        "upstream_acdc_assets_used": sum(
            (item.get("digital_cousin_selection") or {}).get(
                "upstream_acdc_image_pipeline_executed"
            )
            is True
            for item in records[15:]
        ),
        "environment_packages_materialized": sum(
            bool(item.get("environment")) for item in records
        ),
        "environment_load_smokes": sum(
            (item.get("environment") or {}).get("load_smoke", {}).get("status") == "loaded"
            for item in records
        ),
        "portable_receipts_verified": sum(
            item["portable_receipt_verified_from_destination_cas_only"] for item in records
        ),
    }
    summary = {
        "schema_version": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "campaign_seed": args.seed,
        "overall_result": "PENDING",
        "local_compile_result": "PENDING",
        "counts": counts,
        "digital_cousins_preflight": preflight,
        "cases": records,
    }
    try:
        validate_campaign_summary(summary)
    except CampaignAcceptanceError as error:
        local_result = f"FAIL: {error}"
    else:
        local_result = "PASS"
    overall = (
        "PASS"
        if local_result == "PASS" and counts["upstream_acdc_assets_used"] == 5
        else "PARTIAL_UPSTREAM_DIGITAL_COUSINS_NOT_RUN"
        if local_result == "PASS"
        else local_result
    )
    summary["local_compile_result"] = local_result
    summary["overall_result"] = overall
    _json(root / "campaign_summary.json", summary)
    manifest = _manifest(root)
    _json(root / "evidence_manifest.json", manifest)
    report_path = root / "report.md"
    report_path.write_text(_report(summary, root), encoding="utf-8")
    (root / "report.sha256").write_text(f"{_sha(report_path)}  report.md\n", encoding="utf-8")
    print(json.dumps({"result": overall, "counts": counts, "root": str(root)}))
    return 0 if local_result == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
