"""Isolated offline worker for one replay-evidence Qwen invocation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Protocol

from self_improving.studies.vlm_fallback_prompt_optimization.runner import (
    ArtifactBinding,
    ModelBinding,
    ProviderOutcome,
    ResourceUsage,
    VisibleProviderRequest,
)

_REQUEST_SCHEMA = "harness.replay_vlm_worker_request.v1"
_RESPONSE_SCHEMA = "harness.replay_vlm_worker_response.v1"
_ARM = "A1_typed_abstaining_critic_3b"
_PROCESSOR_CONFIG = {"min_pixels": 200704, "max_pixels": 802816}
_IMAGE_NAMES = ("observer_start", "observer_mid", "observer_end", "preview_head")
_CHECK_NAMES = (
    "object_presence",
    "penetration_or_floating",
    "overall_prompt_match",
)


class _VisibleProvider(Protocol):
    identity: object

    def invoke(
        self, request: VisibleProviderRequest, progress: Callable[[str], None]
    ) -> ProviderOutcome: ...


ProviderFactory = Callable[[Mapping[str, Any]], _VisibleProvider]


def run_worker(
    request_path: Path,
    response_path: Path,
    *,
    provider_factory: ProviderFactory | None = None,
) -> None:
    request = _read_request(request_path)
    if response_path.exists():
        raise ValueError("response path must not already exist")
    if request["network_allowed"] is not False:
        raise ValueError("worker network_allowed must be false")
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        if provider_factory is None:
            raise RuntimeError("offline Transformers environment is required")
    model = _mapping(request["model"], "model")
    factory = provider_factory or _create_local_provider
    provider = factory(model)
    with tempfile.TemporaryDirectory(prefix="replay-vlm-images-", dir=request_path.parent) as raw:
        image_root = Path(raw)
        image_paths, bindings = _materialize_images(request["images"], image_root)
        model_binding = ModelBinding(
            role="primary_local_vlm",
            model_id=_text(model, "model_id"),
            revision=_text(model, "revision"),
            snapshot_manifest_sha256=_text(model, "snapshot_manifest_sha256"),
            model_content_manifest_sha256=_text(model, "model_content_manifest_sha256"),
            model_roster_sha256=_text(model, "model_roster_sha256"),
            local_snapshot_path=Path(_text(model, "local_snapshot_path")).resolve(),
            max_new_tokens=_integer(model, "max_new_tokens"),
        )
        visible_request = VisibleProviderRequest(
            invocation_id=_text(request, "assessment_run_id"),
            case_id=_text(request, "replay_run_id"),
            arm=_ARM,
            task_context=_text(request, "task_context"),
            prompt=_text(request, "prompt"),
            prompt_template_version=_text(request, "prompt_version"),
            processor_config=_PROCESSOR_CONFIG,
            seed=0,
            attempt=1,
            repair_index=0,
            repair_of=None,
            resolved_scene_sha256=_text(request, "resolved_scene_sha256"),
            model=model_binding,
            image_paths=image_paths,
            artifacts=bindings,
            resource_reservation=ResourceUsage(visible_vlm_invocations=1),
        )
        outcome = provider.invoke(visible_request, lambda _stage: None)
    if type(outcome) is not ProviderOutcome:
        raise ValueError("visible provider must return ProviderOutcome")
    if outcome.claims_physical_pass is not False:
        raise ValueError("visible provider attempted to claim physical authority")
    raw_response = (
        outcome.raw_response.encode("utf-8")
        if isinstance(outcome.raw_response, str)
        else outcome.raw_response
    )
    if not isinstance(raw_response, bytes) or not raw_response:
        raise ValueError("visible provider returned no raw response bytes")
    advisory_status, parsed = _advisory_status(outcome)
    identity = _record(provider.identity, "provider identity")
    response = {
        "schema_version": _RESPONSE_SCHEMA,
        "advisory_status": advisory_status,
        "raw_response_base64": base64.b64encode(raw_response).decode("ascii"),
        "parsed_response": parsed,
        "provider_receipt": identity,
        "model_receipt": {
            "role": model_binding.role,
            "model_id": model_binding.model_id,
            "revision": model_binding.revision,
            "snapshot_manifest_sha256": model_binding.snapshot_manifest_sha256,
            "model_content_manifest_sha256": model_binding.model_content_manifest_sha256,
            "model_roster_sha256": model_binding.model_roster_sha256,
            "max_new_tokens": model_binding.max_new_tokens,
        },
        "resource_receipt": asdict(outcome.resource),
        "claims_physical_pass": False,
    }
    _write_atomic(response_path, _canonical_json_bytes(response))


def _create_local_provider(model: Mapping[str, Any]) -> _VisibleProvider:
    from self_improving.studies.vlm_fallback_prompt_optimization.visible_qwen_provider import (
        LocalQwenVisibleProvider,
        QualifiedVisibleModel,
        TransformersQwenVisibleBackend,
    )

    qualified = QualifiedVisibleModel(
        arm=_ARM,
        model_role="primary_local_vlm",
        model_id=_text(model, "model_id"),
        revision=_text(model, "revision"),
        snapshot_manifest_sha256=_text(model, "snapshot_manifest_sha256"),
        model_content_manifest_path=Path(_text(model, "model_content_manifest_path")).resolve(),
        model_content_manifest_sha256=_text(model, "model_content_manifest_sha256"),
        model_roster_sha256=_text(model, "model_roster_sha256"),
        local_snapshot_path=Path(_text(model, "local_snapshot_path")).resolve(),
        max_new_tokens=_integer(model, "max_new_tokens"),
    )
    backend = TransformersQwenVisibleBackend(
        device_index=_integer(model, "device_index", nonnegative=True)
    )
    return LocalQwenVisibleProvider(models=(qualified,), backend=backend)


def _materialize_images(
    value: object,
    root: Path,
) -> tuple[tuple[Path, ...], tuple[ArtifactBinding, ...]]:
    if not isinstance(value, list) or len(value) != len(_IMAGE_NAMES):
        raise ValueError("worker requires exactly four replay images")
    paths: list[Path] = []
    bindings: list[ArtifactBinding] = []
    for expected_name, raw in zip(_IMAGE_NAMES, value, strict=True):
        record = _mapping(raw, "image")
        if record.get("name") != expected_name or record.get("media_type") != "image/png":
            raise ValueError("worker replay image identity or media type is invalid")
        source = Path(_text(record, "path"))
        if not source.is_absolute() or not source.is_file() or source.is_symlink():
            raise ValueError("worker replay image source must be an absolute regular file")
        sha256 = _text(record, "sha256")
        size = _integer(record, "bytes", nonnegative=True)
        if source.stat().st_size != size or _sha256_file(source) != sha256:
            raise ValueError("worker replay image failed content verification")
        destination = root / f"{expected_name}.png"
        shutil.copyfile(source, destination)
        if destination.stat().st_size != size or _sha256_file(destination) != sha256:
            raise ValueError("worker replay image copy failed content verification")
        paths.append(destination)
        bindings.append(ArtifactBinding(path=destination.name, sha256=sha256, size_bytes=size))
    return tuple(paths), tuple(bindings)


def _advisory_status(outcome: ProviderOutcome) -> tuple[str, dict[str, Any] | None]:
    parsed = outcome.parsed_response
    if not isinstance(parsed, Mapping):
        return "format_invalid", None
    copied = json.loads(json.dumps(dict(parsed), allow_nan=False))
    checks = copied.get("checks")
    overall = copied.get("overall")
    if (
        not isinstance(checks, dict)
        or set(checks) != set(_CHECK_NAMES)
        or any(value not in {"pass", "fail", "abstain"} for value in checks.values())
        or overall not in {"pass", "fail", "review_required"}
        or not isinstance(copied.get("explanation"), str)
    ):
        return "format_invalid", None
    statuses = set(checks.values())
    if "fail" in statuses or overall == "fail":
        return "fail", copied
    if "abstain" in statuses or overall == "review_required" or outcome.abstained:
        return "abstain", copied
    if statuses == {"pass"} and overall == "pass":
        return "pass", copied
    return "format_invalid", None


def _read_request(path: Path) -> dict[str, Any]:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ValueError("request path must be an absolute regular file")
    try:
        value = json.loads(path.read_bytes())
    except json.JSONDecodeError as error:
        raise ValueError("worker request is not valid JSON") from error
    if not isinstance(value, dict) or value.get("schema_version") != _REQUEST_SCHEMA:
        raise ValueError("worker request schema is invalid")
    prompt = _text(value, "prompt")
    if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != _text(value, "prompt_sha256"):
        raise ValueError("worker prompt digest mismatch")
    return value


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be one object")
    return value


def _text(value: Mapping[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{name} must be nonempty text")
    return item


def _integer(value: Mapping[str, Any], name: str, *, nonnegative: bool = False) -> int:
    item = value.get(name)
    minimum = 0 if nonnegative else 1
    if type(item) is not int or item < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return item


def _record(value: object, label: str) -> dict[str, Any]:
    if is_dataclass(value):
        record = asdict(value)
    elif isinstance(value, Mapping):
        record = dict(value)
    elif hasattr(value, "__dict__"):
        record = vars(value)
    else:
        raise ValueError(f"{label} is not record-like")
    copied = json.loads(json.dumps(record, allow_nan=False))
    if not isinstance(copied, dict):
        raise ValueError(f"{label} must be one object")
    return copied


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".response-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    run_worker(args.request.resolve(), args.response.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["ProviderFactory", "run_worker"]
