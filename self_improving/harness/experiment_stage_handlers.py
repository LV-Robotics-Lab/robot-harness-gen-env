"""Concrete experimental Skill stages; no workflow state or model authority."""

import json
from pathlib import Path
from urllib.error import URLError

from .experiment_assets import AssetAcquisitionError, AssetRequest, AssetVersion, acquire, revise
from .experiment_runtime import observe, replay, validate
from .experiment_scene_compile import compile_scene
from .schemas.common import ArtifactRef
from .schemas.experiment import ExperimentSkillInput


def _setting(settings, name, default=None):
    return (
        settings.get(name, default)
        if isinstance(settings, dict)
        else getattr(settings, name, default)
    )


def _dependency(store, parameters, key):
    reference = parameters.dependencies.get(key)
    if reference is None:
        raise ValueError("missing stage dependency: " + key)
    return json.loads(store.resolve(reference).path.read_bytes())


def _version(value):
    return AssetVersion(value["version_id"], Path(value["root"]), value["receipt"])


def _asset_result(version):
    return {
        "status": "passed",
        "version_id": version.version_id,
        "root": str(version.root),
        "receipt": version.receipt,
        "physics_status": "not_run",
    }


def execute_stage(stage, parameters, *, store, root, settings, backend=None):
    """Execute a single immutable-dependency stage selected by the owning Harness."""
    parameters = ExperimentSkillInput.model_validate(parameters)
    root = Path(root).absolute()
    library = Path(_setting(settings, "asset_library_root", root.parent.parent / "assets"))
    runtime_roots = _setting(settings, "runtime_roots", _setting(settings, "scene_roots", {}))
    denied_roots = _setting(settings, "denied_roots", ())
    if stage == "acquire":
        intent = _dependency(store, parameters, "experiment.intent")["intent"]
        if intent["input_sha256"] != parameters.input_ref.sha256:
            raise ValueError("asset intent input binding mismatch")
        catalog = _setting(settings, "catalog_path")
        if intent["source"] == "local" and catalog is None:
            return {
                "status": "blocked",
                "error_code": "blocked_external_resource",
                "required_resources": ["catalog_path"],
                "reason": "local catalog is not configured",
            }
        request = AssetRequest(
            source=intent["source"],
            category=intent["category"],
            selection=intent["selection"],
            color=intent.get("color"),
            catalog_path=Path(catalog) if catalog else None,
            source_ref=_setting(settings, "source_ref"),
        )
        try:
            return _asset_result(acquire(request, library))
        except AssetAcquisitionError as error:
            return _acquisition_failure(error, store)
    if stage == "revise_asset":
        patch = dict(parameters.proposal)
        if not patch or set(patch) - {"scale", "mass_kg", "friction"}:
            raise ValueError("unsupported asset patch fields")
        existing = _version(_dependency(store, parameters, "experiment.asset"))
        try:
            return _asset_result(revise(existing, patch, library))
        except AssetAcquisitionError as error:
            return _acquisition_failure(error, store)
    if stage == "compile":
        intent = _dependency(store, parameters, "experiment.intent")["intent"]
        asset = _version(_dependency(store, parameters, "experiment.asset"))
        compiled = compile_scene(store, parameters.input_ref, intent, asset)
        return {
            "status": "passed",
            "scene_ref": compiled.scene_ref.model_dump(mode="json"),
            "representation_ref": compiled.representation_ref.model_dump(mode="json"),
            "compile_receipt": compiled.receipt_ref.model_dump(mode="json"),
            "asset_version": asset.version_id,
            "physics_status": "not_run",
        }
    if stage == "replay":
        scene = _dependency(store, parameters, "environment.scene")
        return replay(
            store, ArtifactRef.model_validate(scene["scene_ref"]), root, runtime_roots, denied_roots
        )
    if stage == "observe":
        result = _dependency(store, parameters, "environment.replay")
        return observe(
            store, result, output_root=root, runtime_roots=runtime_roots, denied_roots=denied_roots
        )
    if stage == "validate":
        scene = _dependency(store, parameters, "environment.scene")
        result = _dependency(store, parameters, "environment.replay")
        observation = _dependency(store, parameters, "environment.observation")
        visual = _dependency(store, parameters, "experiment.visual")
        if (
            visual.get("input_sha256") != parameters.input_ref.sha256
            or visual.get("scene_sha256") != scene["scene_ref"]["sha256"]
        ):
            raise ValueError("visual advisory input/scene binding mismatch")
        return validate(
            store,
            input_ref=parameters.input_ref,
            scene_ref=scene["scene_ref"],
            replay_result=result,
            observation=observation,
            visual_advisory=visual,
        )
    raise ValueError("unsupported experimental stage: " + stage)


def _acquisition_failure(error, store):
    cause = error.__cause__
    blocked = isinstance(cause, (ModuleNotFoundError, URLError))
    result = {
        "status": "blocked" if blocked else "failed",
        "error_code": "blocked_external_resource" if blocked else error.code,
        "reason": str(error),
        "partial_root": str(error.partial_root),
    }
    result["diagnostics"] = [
        store.put_file(
            path, name=path.name, media_type="application/json", schema_version=None
        ).model_dump(mode="json")
        for path in sorted(Path(error.partial_root).glob("*.json"))
        if path.is_file() and not path.is_symlink()
    ]
    if blocked:
        result["required_resources"] = [getattr(cause, "name", None) or "network_asset_provider"]
    return result
