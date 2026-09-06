"""Offline subprocess adapter for the local typed Qwen visible provider."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .replay_vlm_assessment import (
    REPLAY_VLM_PROMPT,
    REPLAY_VLM_PROMPT_VERSION,
    ReplayVlmProviderAttempt,
    ReplayVlmProviderRequest,
    ReplayVlmProviderResult,
)
from .replay_vlm_worker import build_format_preservation_receipt, parse_advisory_response
from .schemas import DependencyRef

_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_WORKER_RESPONSE_SCHEMA = "harness.replay_vlm_worker_response.v2"
_FORMAT_REPAIR_PROMPT_VERSION = "replay_visible_format_repair_v1"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_RAW_RESPONSE_BYTES = 1024 * 1024
_RESOURCE_FIELDS = (
    "gpu_time_ms",
    "peak_vram_mib",
    "input_tokens",
    "output_tokens",
    "network_calls",
    "remote_paid_calls",
    "compile_attempts",
    "fresh_physical_replays",
    "runtime_steps",
    "contact_window_steps",
    "prompt_rewrites",
    "visible_vlm_invocations",
)


@dataclass(frozen=True, slots=True)
class QwenReplayVlmProviderSettings:
    interpreter: Path
    module_root: Path
    work_root: Path
    model_id: str
    revision: str
    local_snapshot_path: Path
    snapshot_manifest_sha256: str
    model_content_manifest_path: Path
    model_content_manifest_sha256: str
    model_roster_sha256: str
    device_index: int = 0
    timeout_seconds: float = 900.0

    def __post_init__(self) -> None:
        for name in (
            "interpreter",
            "module_root",
            "work_root",
            "local_snapshot_path",
            "model_content_manifest_path",
        ):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{name} must be an absolute Path")
        if not self.interpreter.is_file():
            raise ValueError("interpreter must be an existing file")
        if not self.module_root.is_dir():
            raise ValueError("module_root must be an existing directory")
        if not self.local_snapshot_path.is_dir():
            raise ValueError("local_snapshot_path must be an existing directory")
        if not self.model_content_manifest_path.is_file():
            raise ValueError("model_content_manifest_path must be an existing file")
        if not self.model_id:
            raise ValueError("model_id must not be empty")
        if _REVISION.fullmatch(self.revision) is None:
            raise ValueError("revision must be 40 lowercase hex characters")
        for name in (
            "snapshot_manifest_sha256",
            "model_content_manifest_sha256",
            "model_roster_sha256",
        ):
            if _SHA256.fullmatch(getattr(self, name)) is None:
                raise ValueError(f"{name} must be 64 lowercase hex characters")
        if _sha256_file(self.model_content_manifest_path) != self.model_content_manifest_sha256:
            raise ValueError("model content manifest digest mismatch")
        expected_snapshot = (
            f"models--{self.model_id.replace('/', '--')}",
            "snapshots",
            self.revision,
        )
        if tuple(self.local_snapshot_path.parts[-3:]) != expected_snapshot:
            raise ValueError("local_snapshot_path does not match model_id and revision")
        if type(self.device_index) is not int or self.device_index < 0:
            raise ValueError("device_index must be a nonnegative integer")
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


class ReplayVlmProcessRunner(Protocol):
    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> object: ...


class _SubprocessRunner:
    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            timeout=timeout_seconds,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )


class SubprocessQwenReplayVlmProvider:
    """Execute the heavy local model in its pinned Python environment."""

    def __init__(
        self,
        settings: QwenReplayVlmProviderSettings,
        *,
        process_runner: ReplayVlmProcessRunner | None = None,
    ) -> None:
        if not isinstance(settings, QwenReplayVlmProviderSettings):
            raise TypeError("settings must be QwenReplayVlmProviderSettings")
        self._settings = settings
        self._runner = process_runner or _SubprocessRunner()
        source_root = Path(__file__).resolve().parent
        provider_source = Path(__file__).resolve()
        worker_source = source_root / "replay_vlm_worker.py"
        assessment_source = source_root / "replay_vlm_assessment.py"
        schema_source = source_root / "schemas" / "replay_vlm.py"
        for source in (worker_source, assessment_source, schema_source):
            if not source.is_file():
                raise ValueError(f"replay VLM source file is unavailable: {source.name}")
        model_identity = _canonical_sha256(
            {
                "model_id": settings.model_id,
                "revision": settings.revision,
                "snapshot_manifest_sha256": settings.snapshot_manifest_sha256,
                "model_content_manifest_sha256": settings.model_content_manifest_sha256,
                "model_roster_sha256": settings.model_roster_sha256,
            }
        )
        provider_identity = _canonical_sha256(
            {
                "provider_source_sha256": _sha256_file(provider_source),
                "worker_source_sha256": _sha256_file(worker_source),
                "assessment_source_sha256": _sha256_file(assessment_source),
                "schema_source_sha256": _sha256_file(schema_source),
                "device_index": settings.device_index,
                "offline": True,
            }
        )
        runtime_identity = _canonical_sha256(
            {
                "interpreter_sha256": _sha256_file(settings.interpreter),
                "module_root": str(settings.module_root.resolve()),
            }
        )
        self.dependencies = tuple(
            sorted(
                (
                    DependencyRef(
                        name="replay_vlm.model",
                        version=settings.revision,
                        sha256=model_identity,
                    ),
                    DependencyRef(
                        name="replay_vlm.prompt",
                        version=REPLAY_VLM_PROMPT_VERSION,
                        sha256=hashlib.sha256(REPLAY_VLM_PROMPT.encode("utf-8")).hexdigest(),
                    ),
                    DependencyRef(
                        name="replay_vlm.provider",
                        version="subprocess-qwen-v2",
                        sha256=provider_identity,
                    ),
                    DependencyRef(
                        name="replay_vlm.worker_runtime",
                        version="python",
                        sha256=runtime_identity,
                    ),
                ),
                key=lambda item: item.name,
            )
        )

    def assess(self, request: ReplayVlmProviderRequest) -> ReplayVlmProviderResult:
        if not isinstance(request, ReplayVlmProviderRequest):
            raise TypeError("request must be ReplayVlmProviderRequest")
        if hashlib.sha256(request.prompt.encode("utf-8")).hexdigest() != request.prompt_sha256:
            raise ValueError("prompt digest mismatch")
        if (
            request.prompt != REPLAY_VLM_PROMPT
            or request.prompt_version != REPLAY_VLM_PROMPT_VERSION
        ):
            raise ValueError("request prompt is not the fixed replay assessment prompt")
        root = self._settings.work_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        attempt = root / str(request.assessment_run_id)
        attempt.mkdir(mode=0o700)
        request_path = attempt / "request.json"
        response_path = attempt / "response.json"
        request_document = self._request_document(request)
        request_path.write_bytes(_canonical_json_bytes(request_document))
        environment = dict(os.environ)
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONPATH": str(self._settings.module_root.resolve()),
            }
        )
        command = (
            str(self._settings.interpreter.resolve()),
            "-m",
            "self_improving.harness.replay_vlm_worker",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
        )
        completed = self._runner.run(
            command,
            cwd=self._settings.module_root.resolve(),
            environment=environment,
            timeout_seconds=float(self._settings.timeout_seconds),
        )
        if getattr(completed, "returncode", None) != 0:
            stderr = getattr(completed, "stderr", b"")
            stderr_sha256 = hashlib.sha256(
                stderr if isinstance(stderr, bytes) else str(stderr).encode()
            ).hexdigest()
            raise RuntimeError(f"Qwen worker failed; stderr_sha256={stderr_sha256}")
        if not response_path.is_file() or response_path.is_symlink():
            raise RuntimeError("Qwen worker did not publish a regular response file")
        if response_path.stat().st_size > _MAX_RESPONSE_BYTES:
            raise RuntimeError("Qwen worker response exceeds its byte limit")
        try:
            response = json.loads(response_path.read_bytes())
        except json.JSONDecodeError as error:
            raise RuntimeError("Qwen worker response is not valid JSON") from error
        return _parse_response(response, settings=self._settings, request=request)

    def _request_document(self, request: ReplayVlmProviderRequest) -> dict[str, object]:
        settings = self._settings
        return {
            "schema_version": "harness.replay_vlm_worker_request.v2",
            "assessment_run_id": str(request.assessment_run_id),
            "replay_run_id": str(request.replay_run_id),
            "replay_invocation_digest": request.replay_invocation_digest,
            "task_context": request.task_context,
            "resolved_scene_sha256": request.resolved_scene_sha256,
            "prompt": request.prompt,
            "prompt_version": request.prompt_version,
            "prompt_sha256": request.prompt_sha256,
            "network_allowed": False,
            "model": {
                "model_id": settings.model_id,
                "revision": settings.revision,
                "local_snapshot_path": str(settings.local_snapshot_path.resolve()),
                "snapshot_manifest_sha256": settings.snapshot_manifest_sha256,
                "model_content_manifest_path": str(settings.model_content_manifest_path.resolve()),
                "model_content_manifest_sha256": settings.model_content_manifest_sha256,
                "model_roster_sha256": settings.model_roster_sha256,
                "device_index": settings.device_index,
                "max_new_tokens": 768,
            },
            "images": [
                {
                    "name": item.ref.name,
                    "path": str(item.path.resolve()),
                    "media_type": item.ref.media_type,
                    "sha256": item.ref.sha256,
                    "bytes": item.ref.bytes,
                }
                for item in request.images
            ],
        }


def _parse_response(
    value: object,
    *,
    settings: QwenReplayVlmProviderSettings,
    request: ReplayVlmProviderRequest,
) -> ReplayVlmProviderResult:
    if not isinstance(value, dict) or value.get("schema_version") != _WORKER_RESPONSE_SCHEMA:
        raise RuntimeError("Qwen worker response schema is invalid")
    claims = value.get("claims_physical_pass")
    if claims is not False:
        raise RuntimeError("Qwen worker attempted to claim physical authority")
    raw_attempts = value.get("attempts")
    if not isinstance(raw_attempts, list) or len(raw_attempts) not in {1, 2}:
        raise RuntimeError("Qwen worker must return one or two attempts")
    try:
        repair_prompt = (
            base64.b64decode(value["format_repair_prompt_base64"], validate=True)
            if value.get("format_repair_prompt_base64") is not None
            else None
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError("Qwen worker repair prompt encoding is invalid") from error
    attempts: list[ReplayVlmProviderAttempt] = []
    attempt_resources: list[dict[str, object]] = []
    raw_interpretations: list[tuple[str, dict[str, object] | None]] = []
    for index, item in enumerate(raw_attempts, start=1):
        if not isinstance(item, dict) or item.get("attempt") != index:
            raise RuntimeError("Qwen worker attempt order is invalid")
        status = item.get("advisory_status")
        if status not in {"pass", "fail", "abstain", "format_invalid"}:
            raise RuntimeError("Qwen worker advisory status is invalid")
        try:
            raw = base64.b64decode(item.get("raw_response_base64", ""), validate=True)
        except (TypeError, ValueError) as error:
            raise RuntimeError("Qwen worker raw response encoding is invalid") from error
        if not raw or len(raw) > _MAX_RAW_RESPONSE_BYTES:
            raise RuntimeError("Qwen worker raw response byte count is invalid")
        parsed = item.get("parsed_response")
        if parsed is not None and not isinstance(parsed, dict):
            raise RuntimeError("Qwen worker parsed response must be an object or null")
        preservation = item.get("format_preservation_receipt")
        if preservation is not None and not isinstance(preservation, dict):
            raise RuntimeError("Qwen worker format preservation receipt is invalid")
        prompt_version = item.get("prompt_version")
        prompt_sha256 = item.get("prompt_sha256")
        repair_of_sha256 = item.get("repair_of_sha256")
        resource = item.get("resource_receipt")
        if (
            not isinstance(prompt_version, str)
            or _SHA256.fullmatch(prompt_sha256 or "") is None
            or not isinstance(resource, dict)
        ):
            raise RuntimeError("Qwen worker attempt receipt is invalid")
        _validate_resource(resource, expected_invocations=1)
        attempt_resources.append(resource)
        raw_interpretations.append(parse_advisory_response(raw))
        attempts.append(
            ReplayVlmProviderAttempt(
                attempt=index,  # type: ignore[arg-type]
                advisory_status=status,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                repair_of_sha256=repair_of_sha256,
                raw_response=raw,
                parsed_response=parsed,
                resource_receipt=resource,
                format_preservation_receipt=preservation,
            )
        )
    first = attempts[0]
    if (
        first.prompt_version != request.prompt_version
        or first.prompt_sha256 != request.prompt_sha256
        or first.repair_of_sha256 is not None
        or first.format_preservation_receipt is not None
        or (first.advisory_status, first.parsed_response) != raw_interpretations[0]
    ):
        raise RuntimeError("Qwen worker first attempt is not bound to the requested prompt")
    if len(attempts) == 1:
        if repair_prompt is not None:
            raise RuntimeError("Qwen worker returned an unused format repair prompt")
    else:
        second = attempts[1]
        expected_preservation = build_format_preservation_receipt(
            source_raw=first.raw_response,
            repaired_raw=second.raw_response,
            repaired=raw_interpretations[1][1],
        )
        if expected_preservation["preserved"]:
            expected_second = raw_interpretations[1]
        else:
            expected_second = ("format_invalid", None)
        if (
            first.advisory_status != "format_invalid"
            or not repair_prompt
            or second.prompt_version != _FORMAT_REPAIR_PROMPT_VERSION
            or second.repair_of_sha256 != hashlib.sha256(first.raw_response).hexdigest()
            or second.prompt_sha256 != hashlib.sha256(repair_prompt).hexdigest()
            or second.format_preservation_receipt != expected_preservation
            or (second.advisory_status, second.parsed_response) != expected_second
        ):
            raise RuntimeError("Qwen worker format repair binding is invalid")
    mappings = []
    for name in ("provider_receipt", "model_receipt", "resource_receipt"):
        item = value.get(name)
        if not isinstance(item, dict):
            raise RuntimeError(f"Qwen worker {name} must be an object")
        mappings.append(item)
    provider_receipt, model_receipt, resource = mappings
    if (
        provider_receipt.get("provider_id") != "vlm_fallback.local_qwen_visible"
        or provider_receipt.get("production_eligible") is not False
    ):
        raise RuntimeError("Qwen worker provider receipt is invalid")
    expected_model_receipt = {
        "role": "primary_local_vlm",
        "model_id": settings.model_id,
        "revision": settings.revision,
        "snapshot_manifest_sha256": settings.snapshot_manifest_sha256,
        "model_content_manifest_sha256": settings.model_content_manifest_sha256,
        "model_roster_sha256": settings.model_roster_sha256,
        "max_new_tokens": 768,
    }
    if model_receipt != expected_model_receipt:
        raise RuntimeError("Qwen worker model receipt does not match configured model")
    _validate_resource(resource, expected_invocations=len(attempts))
    expected_resource = {
        name: (
            max(_resource_integer(item, name) for item in attempt_resources)
            if name == "peak_vram_mib"
            else sum(_resource_integer(item, name) for item in attempt_resources)
        )
        for name in _RESOURCE_FIELDS
    }
    if resource != expected_resource:
        raise RuntimeError("Qwen worker aggregate resource receipt does not match attempts")
    return ReplayVlmProviderResult(
        attempts=tuple(attempts),
        format_repair_prompt=repair_prompt,
        provider_receipt=provider_receipt,
        model_receipt=model_receipt,
        resource_receipt=resource,
        claims_physical_pass=False,
    )


def _resource_integer(value: dict[str, object], name: str) -> int:
    item = value.get(name)
    if type(item) is not int or item < 0:
        raise RuntimeError(f"Qwen worker resource {name} must be a nonnegative integer")
    return item


def _validate_resource(value: dict[str, object], *, expected_invocations: int) -> None:
    if set(value) != set(_RESOURCE_FIELDS):
        raise RuntimeError("Qwen worker resource receipt fields are invalid")
    for name in _RESOURCE_FIELDS:
        _resource_integer(value, name)
    if (
        value["visible_vlm_invocations"] != expected_invocations
        or value["network_calls"] != 0
        or value["remote_paid_calls"] != 0
    ):
        raise RuntimeError("Qwen worker resource receipt violates the offline call contract")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "QwenReplayVlmProviderSettings",
    "ReplayVlmProcessRunner",
    "SubprocessQwenReplayVlmProvider",
]
