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
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any, Protocol

from self_improving.studies.vlm_fallback_prompt_optimization.runner import (
    ArtifactBinding,
    ModelBinding,
    ProviderOutcome,
    ResourceUsage,
    VisibleProviderRequest,
)

_REQUEST_SCHEMA = "harness.replay_vlm_worker_request.v2"
_RESPONSE_SCHEMA = "harness.replay_vlm_worker_response.v2"
_ARM = "A1_typed_abstaining_critic_3b"
_FORMAT_REPAIR_PROMPT_VERSION = "replay_visible_format_repair_v1"
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
        first_request = VisibleProviderRequest(
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
        first_outcome = _invoke(provider, first_request)
        first_raw = _raw_bytes(first_outcome)
        first_status, first_parsed = parse_advisory_response(first_raw)
        attempts = [
            _attempt_record(
                attempt=1,
                status=first_status,
                prompt_version=first_request.prompt_template_version,
                prompt_sha256=hashlib.sha256(first_request.prompt.encode("utf-8")).hexdigest(),
                repair_of_sha256=None,
                raw=first_raw,
                parsed=first_parsed,
                resource=first_outcome.resource,
                format_preservation_receipt=None,
            )
        ]
        outcomes = [first_outcome]
        repair_prompt_bytes: bytes | None = None
        if first_status == "format_invalid":
            repair_prompt = _format_repair_prompt(first_raw)
            repair_prompt_bytes = repair_prompt.encode("utf-8")
            first_raw_sha256 = hashlib.sha256(first_raw).hexdigest()
            second_request = VisibleProviderRequest(
                invocation_id=_text(request, "assessment_run_id"),
                case_id=_text(request, "replay_run_id"),
                arm=_ARM,
                task_context=_text(request, "task_context"),
                prompt=repair_prompt,
                prompt_template_version=_FORMAT_REPAIR_PROMPT_VERSION,
                processor_config=_PROCESSOR_CONFIG,
                seed=0,
                attempt=2,
                repair_index=1,
                repair_of=first_raw_sha256,
                resolved_scene_sha256=_text(request, "resolved_scene_sha256"),
                model=model_binding,
                image_paths=image_paths,
                artifacts=bindings,
                resource_reservation=ResourceUsage(visible_vlm_invocations=1),
            )
            second_outcome = _invoke(provider, second_request)
            second_raw = _raw_bytes(second_outcome)
            second_status, second_parsed = parse_advisory_response(second_raw)
            preservation = build_format_preservation_receipt(
                source_raw=first_raw,
                repaired_raw=second_raw,
                repaired=second_parsed,
            )
            if second_status != "format_invalid" and not preservation["preserved"]:
                second_status = "format_invalid"
                second_parsed = None
            attempts.append(
                _attempt_record(
                    attempt=2,
                    status=second_status,
                    prompt_version=second_request.prompt_template_version,
                    prompt_sha256=hashlib.sha256(repair_prompt_bytes).hexdigest(),
                    repair_of_sha256=first_raw_sha256,
                    raw=second_raw,
                    parsed=second_parsed,
                    resource=second_outcome.resource,
                    format_preservation_receipt=preservation,
                )
            )
            outcomes.append(second_outcome)
    identity = _record(provider.identity, "provider identity")
    response = {
        "schema_version": _RESPONSE_SCHEMA,
        "attempts": attempts,
        "format_repair_prompt_base64": (
            base64.b64encode(repair_prompt_bytes).decode("ascii")
            if repair_prompt_bytes is not None
            else None
        ),
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
        "resource_receipt": asdict(_sum_resources(tuple(item.resource for item in outcomes))),
        "claims_physical_pass": False,
    }
    _write_atomic(response_path, _canonical_json_bytes(response))


def _invoke(provider: _VisibleProvider, request: VisibleProviderRequest) -> ProviderOutcome:
    outcome = provider.invoke(request, lambda _stage: None)
    if type(outcome) is not ProviderOutcome:
        raise ValueError("visible provider must return ProviderOutcome")
    if outcome.claims_physical_pass is not False:
        raise ValueError("visible provider attempted to claim physical authority")
    if type(outcome.resource) is not ResourceUsage:
        raise ValueError("visible provider resource receipt must be ResourceUsage")
    if outcome.resource.visible_vlm_invocations != 1 or outcome.resource.network_calls != 0:
        raise ValueError("each visible provider attempt must be one offline VLM invocation")
    return outcome


def _raw_bytes(outcome: ProviderOutcome) -> bytes:
    raw = (
        outcome.raw_response.encode("utf-8")
        if isinstance(outcome.raw_response, str)
        else outcome.raw_response
    )
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("visible provider returned no raw response bytes")
    return raw


def _attempt_record(
    *,
    attempt: int,
    status: str,
    prompt_version: str,
    prompt_sha256: str,
    repair_of_sha256: str | None,
    raw: bytes,
    parsed: dict[str, Any] | None,
    resource: ResourceUsage,
    format_preservation_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "advisory_status": status,
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_sha256,
        "repair_of_sha256": repair_of_sha256,
        "raw_response_base64": base64.b64encode(raw).decode("ascii"),
        "parsed_response": parsed,
        "resource_receipt": asdict(resource),
        "format_preservation_receipt": format_preservation_receipt,
    }


def _format_repair_prompt(first_raw: bytes) -> str:
    rendered = first_raw.decode("utf-8", errors="replace")
    digest = hashlib.sha256(first_raw).hexdigest()
    recovered = _recover_source_object(first_raw)
    required_output = (
        json.dumps(
            _conservative_flatten(recovered),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if recovered is not None
        else "unavailable because the previous response was not recoverable as one JSON object"
    )
    return f"""Your previous visible assessment had invalid output formatting.
Change formatting only; preserve its visible judgment and uncertainty. Do not add new observations.
Preserve explanation text exactly. Collapse a nested check only when all nested values agree.
For any missing check, output abstain. Preserve overall exactly; if missing, use review_required.
Previous response SHA-256: {digest}
The only acceptable normalized output is below. If it is available, copy it exactly:
<required_output>
{required_output}
</required_output>
Return only one JSON object, with no markdown fence or surrounding prose. It must have exactly:
{{"checks":{{"object_presence":"pass|fail|abstain","penetration_or_floating":"pass|fail|abstain","overall_prompt_match":"pass|fail|abstain"}},"overall":"pass|fail|review_required","explanation":"text"}}
Previous response follows between the delimiters:
<previous_response>
{rendered}
</previous_response>"""


def build_format_preservation_receipt(
    *,
    source_raw: bytes,
    repaired_raw: bytes,
    repaired: dict[str, Any] | None,
) -> dict[str, Any]:
    source = _recover_source_object(source_raw)
    mismatched: list[str] = []
    expected: dict[str, Any] | None = None
    if source is None:
        mismatched.append("source_response.unrecoverable")
    else:
        expected = _conservative_flatten(source)
        if repaired is None:
            mismatched.append("repaired_response.invalid")
        else:
            for name in _CHECK_NAMES:
                observed_checks = repaired.get("checks")
                observed = (
                    observed_checks.get(name) if isinstance(observed_checks, Mapping) else None
                )
                if observed != expected["checks"][name]:
                    mismatched.append(f"checks.{name}")
            for name in ("overall", "explanation"):
                if repaired.get(name) != expected[name]:
                    mismatched.append(name)
    return {
        "verifier_id": "replay_vlm.format_only_preservation.v1",
        "source_response_sha256": hashlib.sha256(source_raw).hexdigest(),
        "repaired_response_sha256": hashlib.sha256(repaired_raw).hexdigest(),
        "source_json_recovered": source is not None,
        "expected_normalized": expected,
        "mismatched_fields": sorted(mismatched),
        "preserved": not mismatched,
    }


def _recover_source_object(raw: bytes) -> dict[str, Any] | None:
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if text.startswith("```") and text.endswith("```"):
        first_newline = text.find("\n")
        if first_newline < 0:
            return None
        text = text[first_newline + 1 : -3].strip()
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _conservative_flatten(source: Mapping[str, Any]) -> dict[str, Any]:
    raw_checks = source.get("checks")
    checks = raw_checks if isinstance(raw_checks, Mapping) else {}
    normalized_checks = {
        name: _conservative_status(checks.get(name)) for name in _CHECK_NAMES
    }
    overall = source.get("overall")
    if overall not in {"pass", "fail", "review_required"}:
        overall = "review_required"
    explanation = source.get("explanation")
    if not isinstance(explanation, str):
        explanation = ""
    return {
        "checks": normalized_checks,
        "overall": overall,
        "explanation": explanation,
    }


def _conservative_status(value: object) -> str:
    allowed = {"pass", "fail", "abstain"}
    if isinstance(value, str) and value in allowed:
        return value
    if isinstance(value, Mapping) and value:
        statuses = set(value.values())
        if len(statuses) == 1 and next(iter(statuses)) in allowed:
            return str(next(iter(statuses)))
    return "abstain"


def _sum_resources(resources: tuple[ResourceUsage, ...]) -> ResourceUsage:
    return ResourceUsage(
        **{
            field.name: (
                max(getattr(resource, field.name) for resource in resources)
                if field.name == "peak_vram_mib"
                else sum(getattr(resource, field.name) for resource in resources)
            )
            for field in fields(ResourceUsage)
        }
    )


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


def parse_advisory_response(raw: bytes) -> tuple[str, dict[str, Any] | None]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        return "format_invalid", None
    if not isinstance(parsed, dict):
        return "format_invalid", None
    copied = json.loads(json.dumps(parsed, allow_nan=False))
    checks = copied.get("checks")
    overall = copied.get("overall")
    if (
        not isinstance(checks, dict)
        or set(checks) != set(_CHECK_NAMES)
        or any(
            not isinstance(value, str) or value not in {"pass", "fail", "abstain"}
            for value in checks.values()
        )
        or overall not in {"pass", "fail", "review_required"}
        or not isinstance(copied.get("explanation"), str)
    ):
        return "format_invalid", None
    statuses = set(checks.values())
    if "fail" in statuses or overall == "fail":
        return "fail", copied
    if "abstain" in statuses or overall == "review_required":
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


__all__ = [
    "ProviderFactory",
    "build_format_preservation_receipt",
    "parse_advisory_response",
    "run_worker",
]
