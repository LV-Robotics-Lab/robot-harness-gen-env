"""Export the currently validated experimental workflow; never update its authority."""

import json
from pathlib import Path

from .portable_genesis_package import export_package
from .schemas.common import ArtifactRef


def _read(store, ref):
    return json.loads(store.resolve(ArtifactRef.model_validate(ref)).path.read_bytes())


def package_workflow(service, workflow_id, output):
    """Materialize current input, revisions, model evidence and runtime outputs for review/run."""
    status = service.status(workflow_id=workflow_id)
    history = status["history"]
    if not history or history[-1]["stage"] != "validate" or history[-1]["status"] != "passed":
        raise ValueError("workflow requires a latest passed validation")
    latest = history[-1]
    if latest["operation"].get("committed_revision") != status["snapshot"]["revision"]:
        raise ValueError("validation is not the current committed revision")
    facts, store = status["facts"], service.store
    required = {
        "environment.scene",
        "environment.replay",
        "environment.observation",
        "experiment.visual",
        "environment.validation",
        "experiment.intent",
        "experiment.asset",
    }
    if not required <= facts.keys():
        raise ValueError("validated workflow lacks required evidence facts")
    input_ref = facts.get("request.experiment_input", facts.get("request.input"))
    if input_ref is None or facts["environment.validation"] != latest["receipt"]:
        raise ValueError("validation does not identify the current input/receipt")
    records = {key: _read(store, facts[key]) for key in required}
    validation = records["environment.validation"]
    scene_ref = records["environment.scene"]["scene_ref"]
    if (
        validation.get("status") != "passed"
        or validation.get("input") != input_ref
        or validation.get("scene") != scene_ref
        or validation.get("replay") != records["environment.replay"].get("receipt")
        or validation.get("observation") != records["environment.observation"].get("receipt")
        or records["experiment.visual"].get("status") != "passed"
    ):
        raise ValueError("validation lineage differs from current trusted facts")

    # Follow the bounded product evidence graph, not runtime manifests or source directories.
    refs = {}

    def collect(value):
        if isinstance(value, dict):
            if value.get("uri", "").startswith("artifact://sha256/"):
                ref = ArtifactRef.model_validate(value)
                key = (ref.sha256, ref.name, ref.media_type, ref.schema_version)
                if key in refs:
                    return
                refs[key] = ref
                path = store.resolve(ref).path
                if ref.media_type == "application/json":
                    collect(json.loads(path.read_bytes()))
            else:
                for item in value.values():
                    collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    collect(input_ref)
    for key in required:
        collect(facts[key])
    audit_history = []
    for item in history:
        entry = {
            "stage": item["stage"],
            "status": item["status"],
            "operation_id": item["operation"].get("operation_id"),
            "committed_revision": item["operation"].get("committed_revision"),
            "receipt": item.get("receipt"),
        }
        audit_history.append(entry)
        if item.get("receipt") is not None:
            collect(item["receipt"])
        child_id = item["operation"].get("child_run_id")
        if child_id and hasattr(service, "root"):
            operation_root = Path(service.root) / "operations" / child_id
            if Path(child_id).name != child_id:
                raise ValueError("invalid operation identity in validation history")
            logs = [operation_root / "error.log", *(operation_root / "stage/model").glob("*")]
            for path in logs:
                if path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)):
                    ref = store.put_file(
                        path,
                        name=path.name,
                        media_type="application/json" if path.suffix == ".json" else "text/plain",
                        schema_version=None,
                    )
                    collect(ref.model_dump(mode="json"))
    scene = _read(store, scene_ref)
    representation = _read(store, scene["representation"])
    asset_hashes = {member["artifact"]["sha256"] for member in representation["members"]}
    # Asset files retain their logical resource layout in assets/, not broken report aliases.
    ordered = [
        refs[key]
        for key in sorted(refs, key=lambda key: tuple(str(v) for v in key))
        if key[0] not in asset_hashes
    ]
    media = [ref for ref in ordered if ref.media_type.startswith(("image/", "video/"))]
    if not any(ref.media_type == "image/png" for ref in media) or not any(
        ref.media_type == "video/mp4" for ref in media
    ):
        raise ValueError("validated package requires a PNG and continuous MP4 artifact")
    reports = [
        ref.model_copy(update={"name": ref.name + ".evidence"})
        if Path(ref.name).suffix.lower() in {".urdf", ".obj", ".mtl", ".glb"}
        else ref
        for ref in ordered
        if ref not in media
    ]
    provenance = {
        "workflow_id": str(workflow_id),
        "revision": status["snapshot"]["revision"],
        "qualification_status": "development_unqualified",
        "input": input_ref,
        "facts": {key: facts[key] for key in sorted(required)},
        "history": audit_history,
        "artifact_mapping": {
            f"reports/{i}-{Path(ref.name).name}": ref.model_dump(mode="json")
            for i, ref in enumerate(reports)
        },
    }
    provenance["artifact_mapping"].update(
        {
            f"media/{i}-{Path(ref.name).name}": ref.model_dump(mode="json")
            for i, ref in enumerate(media)
        }
    )
    # A concurrent new stage must not make the selected validation look current.
    after = service.status(workflow_id=workflow_id)
    if after["snapshot"] != status["snapshot"]:
        raise ValueError("workflow changed during validation package collection")
    manifest = export_package(
        store,
        scene_ref=scene_ref,
        output=output,
        provenance=provenance,
        reports=reports,
        media=media,
    )
    if service.status(workflow_id=workflow_id)["snapshot"] != status["snapshot"]:
        raise ValueError("workflow changed while exporting validated package; candidate retained")
    return {
        "status": "passed",
        "workflow_id": str(workflow_id),
        "manifest_path": str(Path(output).absolute() / "environment.json"),
        "package_path": str(Path(output).absolute()),
        "checks": manifest["checks"],
        "qualification_status": "development_unqualified",
        "robot_policy_evaluated": False,
        "data_collection_evaluated": False,
    }
