"""Explicit failure materialization from existing evidence; never fabricates a scene."""

import hashlib
import json
from pathlib import Path

from .artifacts import artifact_closure


def materialize_failure(snapshot, store, output, *, reuse_existing=False):
    if snapshot.status not in {"failed", "blocked", "cancelled"}:
        raise ValueError("failure bundle requires stopped workflow")
    output = Path(output)
    if not output.is_absolute() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("failure output must be absolute non-symbolic new directory")
    roots = [
        ref
        for ref in (
            snapshot.input_bundle,
            snapshot.proposal,
            snapshot.scene_ir,
            snapshot.asset_resolution,
            snapshot.resolved_assets,
            snapshot.compiled_scene,
            snapshot.replay_result,
            snapshot.observation,
            snapshot.diagnosis,
            snapshot.validation,
            *snapshot.revisions,
        )
        if ref is not None
    ]
    roots.extend(ref for op in snapshot.operations if op.result for ref in op.result.outputs)
    refs = artifact_closure(store, roots)
    reuse = reuse_existing and output.is_dir()
    if not reuse:
        output.mkdir(parents=True, exist_ok=False)
    failure = output / "failure"
    if not reuse:
        failure.mkdir()
    members, images, videos, logs = [], [], [], []

    def put(name, raw):
        path = failure / name
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("unsafe failure member")
        if reuse:
            if path.read_bytes() != raw:
                raise ValueError("existing failure bundle differs")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        members.append(
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        )
        return str(path)

    for ref in refs:
        extension = {
            "application/json": ".json",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "video/mp4": ".mp4",
            "video/x-matroska": ".mkv",
            "text/plain": ".txt",
        }.get(ref.media_type, ".bin")
        path = put(f"partial/{ref.sha256}{extension}", store.read_artifact(ref))
        if ref.media_type.startswith("image/"):
            images.append(path)
        elif ref.media_type.startswith("video/"):
            videos.append(path)
        elif ref.media_type == "text/plain":
            logs.append(path)
    scene = {"status": "not_produced"}
    if snapshot.scene_ir:
        scene = {
            "status": "produced",
            "path": put("scene-ir.json", store.read_artifact(snapshot.scene_ir)),
        }
    put("workflow.json", snapshot.model_dump_json().encode())
    put(
        "error.json",
        json.dumps(
            {
                "status": snapshot.status,
                "stop_reason": snapshot.stop_reason,
                "required_resources": snapshot.required_resources,
            }
        ).encode(),
    )
    put(
        "human-readable.md",
        (
            f"# x2env workflow {snapshot.status}\n\nStop reason: {snapshot.stop_reason}\n\n"
            "Only completed evidence is included. "
            "No usable environment or qualification is granted.\n"
        ).encode(),
    )
    manifest = {
        "schema_version": "x2env.failure_bundle.v1",
        "workflow_id": snapshot.workflow_id,
        "status": snapshot.status,
        "members": members,
        "environment_package": None,
        "scene_ir": scene,
    }
    raw = json.dumps(manifest, sort_keys=True, indent=2).encode()
    if reuse:
        if (failure / "manifest.json").is_symlink() or (
            failure / "manifest.json"
        ).read_bytes() != raw:
            raise ValueError("existing failure bundle differs")
    else:
        (failure / "manifest.json").write_bytes(raw)
    result = {
        "workflow_id": snapshot.workflow_id,
        "revision": snapshot.revision,
        "status": snapshot.status,
        "stop_reason": snapshot.stop_reason,
        "scene_ir": scene,
        "images": images,
        "videos": videos,
        "logs": logs,
        "environment_package": None,
        "failure_bundle": str(failure),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }
    serialized = json.dumps(result, sort_keys=True, indent=2).encode()
    if reuse:
        if (output / "result.json").is_symlink() or (
            output / "result.json"
        ).read_bytes() != serialized:
            raise ValueError("existing failure bundle differs")
    else:
        (output / "result.json").write_bytes(serialized)
    return result
