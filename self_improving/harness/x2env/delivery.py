"""Read-only delivery of a committed completion; not workflow or release authority."""

import hashlib
import json
from pathlib import Path, PurePosixPath

from .artifacts import artifact_closure
from .completion import CompletionResult
from .contracts import WorkflowSnapshot
from .package_loader import verify_package


def _path(root, name):
    relative = PurePosixPath(name)
    if (
        not name
        or relative.is_absolute()
        or ".." in relative.parts
        or "\\" in name
        or ":" in name
        or relative.as_posix() != name
    ):
        raise ValueError("unsafe delivery member")
    target = root / name
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError("symbolic delivery member")
    return target


def _json(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def export_completion(snapshot, store, output=None, reuse_existing=False):
    """Verify committed CAS and explicit files, then copy or exactly compare NEW output.

    Errors raise without rollback/deletion: a partial copy is retained for inspection.
    Source auxiliary files are never copied. No Genesis execution is performed.
    """
    snapshot = WorkflowSnapshot.model_validate(snapshot)
    if store.status(snapshot.workflow_id) != snapshot:
        raise ValueError("stale_delivery_snapshot")
    if snapshot.status != "succeeded" or snapshot.environment_package is None:
        raise ValueError("completion_not_committed")
    operation = snapshot.operations[-1] if snapshot.operations else None
    if (
        operation is None
        or operation.capability != "package.materialize"
        or operation.status != "succeeded"
        or operation.result is None
        or operation.result.status != "succeeded"
        or snapshot.environment_package not in operation.result.outputs
    ):
        raise ValueError("completion_not_committed")
    artifact_closure(store, (snapshot.environment_package,))
    completion = CompletionResult.model_validate_json(
        store.read_artifact(snapshot.environment_package)
    )
    if (
        completion.status != "materialized"
        or completion.error_code is not None
        or completion.manifest is None
        or completion.package_path is None
    ):
        raise ValueError("completion_not_materialized")
    receipt = json.loads(store.read_artifact(completion.receipt))
    if any(
        receipt.get(key) != value
        for key, value in {
            "schema_version": "x2env.completion.v1",
            "workflow_id": snapshot.workflow_id,
            "revision": snapshot.revision - 1,
            "status": "materialized",
            "error_code": None,
            "package_path": completion.package_path,
            "manifest": completion.manifest.model_dump(),
            "physical_profile": "passed",
            "visual_intent": "passed",
            "copy_run": "not_run",
            "release_qualified": False,
            "sim_ready": False,
        }.items()
    ):
        raise ValueError("completion_receipt_binding_mismatch")
    for field in (
        "input_bundle",
        "scene_ir",
        "compiled_scene",
        "replay_result",
        "observation",
        "diagnosis",
        "validation",
    ):
        ref = getattr(snapshot, field)
        if ref is None or receipt.get("evidence", {}).get(field) != ref.model_dump():
            raise ValueError("completion_evidence_binding_mismatch")
    source = Path(completion.package_path)
    if not source.is_absolute():
        raise ValueError("nonabsolute_completion_package")
    manifest_raw = store.read_artifact(completion.manifest)
    if _path(source, "manifest.json").read_bytes() != manifest_raw:
        raise ValueError("completion_manifest_mismatch")
    manifest = verify_package(source)
    destination = source if output is None else Path(output) / "environment"
    if output is not None and (not Path(output).is_absolute() or Path(output) == Path("/")):
        raise ValueError("delivery output must be scoped and absolute")
    result = {
        "workflow_id": snapshot.workflow_id,
        "revision": snapshot.revision,
        "status": "succeeded",
        "environment_package": str(destination),
        "manifest": str(destination / "manifest.json"),
        "manifest_sha256": completion.manifest.sha256,
        "completion_receipt": completion.receipt.model_dump(),
        "physical_profile": receipt["physical_profile"],
        "visual_intent": receipt["visual_intent"],
        "copy_run": "not_run",
        "release_qualified": False,
        "sim_ready": False,
        "robot_policy_evaluated": False,
        "data_collection_evaluated": False,
        "images": [],
        "videos": [],
        "logs": [],
    }
    for member in manifest["members"]:
        suffix = Path(member["path"]).suffix.lower()
        group = (
            "images"
            if suffix == ".png"
            else "videos"
            if suffix == ".mp4"
            else "logs"
            if suffix in {".log", ".txt", ".jsonl"}
            else None
        )
        if group:
            result[group].append(str(destination / member["path"]))
    if not result["images"] or not result["videos"]:
        raise ValueError("completion_missing_review_media")
    if output is None:
        return result
    output = Path(output)
    _path(output, "result.json")
    reuse = output.exists()
    if reuse and not reuse_existing:
        raise FileExistsError(output)
    if not reuse:
        output.mkdir(parents=True, exist_ok=False)
    expected = {
        "environment/manifest.json": manifest_raw,
        "result.json": _json(result),
        "workflow.json": _json(snapshot.model_dump(mode="json")),
    }
    for member in manifest["members"]:
        raw = _path(source, member["path"]).read_bytes()
        if len(raw) != member["size_bytes"] or hashlib.sha256(raw).hexdigest() != member["sha256"]:
            raise ValueError("completion_source_changed")
        expected["environment/" + member["path"]] = raw
    for name, raw in expected.items():
        target = _path(output, name)
        if reuse:
            if not target.is_file() or target.read_bytes() != raw:
                raise ValueError("delivery_existing_bytes_differ")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    if reuse:
        actual = {
            p.relative_to(output).as_posix()
            for p in output.rglob("*")
            if p.is_file() or p.is_symlink()
        }
        if actual != set(expected):
            raise ValueError("delivery_existing_members_differ")
    verify_package(destination)
    if store.status(snapshot.workflow_id) != snapshot:
        raise ValueError("stale_delivery_snapshot")
    return result
