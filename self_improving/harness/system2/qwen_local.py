"""Attested, offline Qwen provider for bounded System 2 decisions.

This adapter deliberately makes a narrower claim than a hermetic model service:
it binds the local Hugging Face snapshot, Python/package/GPU facts, and fixed
greedy inference policy, while explicitly recording that the host dependency
closure is incomplete.  Snapshot blob contents are verified once and their
filesystem identities are rechecked before and after every invocation.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform as host_platform
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Callable, Literal, Protocol, cast

from pydantic import Field, model_validator

from ..schemas.base import HarnessModel, Sha256
from .planner import (
    PlannerProviderIdentity,
    PlannerProviderResponse,
    PlannerUsage,
    provider_identity_sha256,
)

QWEN_RUNTIME_IDENTITY_SCHEMA = "harness.qwen_runtime_identity.v1"
QWEN_SNAPSHOT_ATTESTATION_SCHEMA = "harness.qwen_snapshot_attestation.v1"
QWEN_PROVIDER_ADAPTER = "harness.qwen_local_planner_provider.v1"
QWEN_SYSTEM_INSTRUCTION = (
    "You are PEARL's bounded System 2 decision function. The next user message is a "
    "complete JSON contract, not a request for prose or a multi-step plan. Return exactly "
    "one minified JSON object matching its output_schema. Never emit markdown, commentary, "
    "code fences, multiple actions, or a tool name not listed in context.skills. Fill every "
    "one of the nine required top-level keys, including observation_keys and stop_reason even "
    "when their values are [] and null. For invoke_skill, parameters must contain only the "
    "selected Skill input fields, never another decision object. Fill every required Skill "
    "parameter from trusted context; do not copy placeholder text. Do not claim execution."
)

_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_TEXT = Annotated[str, Field(strict=True, min_length=1, max_length=255)]
_REQUIRED_DISTRIBUTIONS = frozenset(
    {"accelerate", "safetensors", "tokenizers", "torch", "transformers"}
)
_REQUIRED_RUNTIME_MODULES = frozenset({"torch", "transformers"})


class SnapshotIntegrityError(ValueError):
    """The declared local model snapshot is unavailable, mutable, or unbound."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_blob_sha1(path: Path, size: int) -> str:
    digest = hashlib.sha1()
    digest.update(f"blob {size}\0".encode())
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_hex(value: object, *, length: int, label: str) -> str:
    pattern = _HEX_40 if length == 40 else _HEX_64
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} must be {length} lowercase hexadecimal characters")
    return value


class QwenRuntimeIdentity(HarnessModel):
    """Path-free facts for the Python/GPU runtime used by the local backend."""

    schema_version: Literal["harness.qwen_runtime_identity.v1"] = QWEN_RUNTIME_IDENTITY_SCHEMA
    python_implementation: _CANONICAL_TEXT
    python_version: _CANONICAL_TEXT
    python_executable_sha256: Sha256
    platform: _CANONICAL_TEXT
    packages: dict[str, _CANONICAL_TEXT]
    package_record_sha256: dict[str, Sha256]
    runtime_module_sha256: dict[str, Sha256]
    torch_cuda_version: _CANONICAL_TEXT
    device_index: Annotated[int, Field(strict=True, ge=0)]
    device_name: _CANONICAL_TEXT
    compute_capability: _CANONICAL_TEXT
    hermetic: Literal[False]
    dependency_closure_complete: Literal[False]

    @model_validator(mode="after")
    def runtime_facts_are_exact_and_canonical(self) -> "QwenRuntimeIdentity":
        if set(self.packages) != _REQUIRED_DISTRIBUTIONS:
            raise ValueError("runtime packages do not match the required distribution set")
        if set(self.package_record_sha256) != _REQUIRED_DISTRIBUTIONS:
            raise ValueError("runtime package RECORD digests are incomplete")
        if set(self.runtime_module_sha256) != _REQUIRED_RUNTIME_MODULES:
            raise ValueError("runtime module source digests are incomplete")
        text_values = (
            self.python_implementation,
            self.python_version,
            self.platform,
            self.torch_cuda_version,
            self.device_name,
            self.compute_capability,
            *self.packages.values(),
        )
        if any(value.strip() != value for value in text_values):
            raise ValueError("runtime identity text must not have surrounding whitespace")
        return self


class QwenSnapshotAttestation(HarnessModel):
    """Stable summary of one verified Hugging Face revision snapshot."""

    schema_version: Literal["harness.qwen_snapshot_attestation.v1"] = (
        QWEN_SNAPSHOT_ATTESTATION_SCHEMA
    )
    revision: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]
    manifest_sha256: Sha256
    file_count: Annotated[int, Field(strict=True, ge=1)]
    total_bytes: Annotated[int, Field(strict=True, ge=1)]


@dataclass(frozen=True, slots=True)
class QwenLocalSettings:
    """Machine-local locator plus path-free qualified inference policy."""

    model_id: str
    revision: str
    snapshot_path: Path
    snapshot_manifest_sha256: str
    device_index: int = 0
    dtype: Literal["bfloat16", "float16"] = "bfloat16"
    max_new_tokens: int = 512
    max_prompt_bytes: int = 256_000

    def __post_init__(self) -> None:
        if (
            type(self.model_id) is not str
            or not self.model_id
            or self.model_id.strip() != self.model_id
        ):
            raise ValueError("model_id must be non-empty without surrounding whitespace")
        _require_hex(self.revision, length=40, label="revision")
        _require_hex(
            self.snapshot_manifest_sha256,
            length=64,
            label="snapshot_manifest_sha256",
        )
        if not isinstance(self.snapshot_path, Path):
            raise TypeError("snapshot_path must be pathlib.Path")
        if type(self.device_index) is not int or self.device_index < 0:
            raise ValueError("device_index must be a non-negative integer")
        if self.dtype not in {"bfloat16", "float16"}:
            raise ValueError("dtype must be bfloat16 or float16")
        if type(self.max_new_tokens) is not int or not 1 <= self.max_new_tokens <= 4096:
            raise ValueError("max_new_tokens must be an integer from 1 through 4096")
        if type(self.max_prompt_bytes) is not int or not 1 <= self.max_prompt_bytes <= 1_048_576:
            raise ValueError("max_prompt_bytes must be an integer from 1 through 1048576")


@dataclass(frozen=True, slots=True)
class _EntryFingerprint:
    name: str
    link_target: str
    link_stat: tuple[int, int, int, int, int, int]
    blob_name: str
    blob_stat: tuple[int, int, int, int, int, int]


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _snapshot_layout(
    snapshot_path: Path,
    *,
    revision: str,
) -> tuple[Path, Path, list[tuple[Path, Path, str, os.stat_result, os.stat_result]]]:
    _require_hex(revision, length=40, label="revision")
    expanded = snapshot_path.expanduser()
    if expanded.is_symlink():
        raise SnapshotIntegrityError("snapshot root must not be a symlink")
    try:
        snapshot = expanded.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SnapshotIntegrityError("snapshot root is unavailable") from exc
    if not snapshot.is_dir():
        raise SnapshotIntegrityError("snapshot root must be a real directory")
    if snapshot.name != revision or snapshot.parent.name != "snapshots":
        raise SnapshotIntegrityError("snapshot path is not bound to the declared revision")
    model_root = snapshot.parent.parent
    blobs = model_root / "blobs"
    if blobs.is_symlink() or not blobs.is_dir():
        raise SnapshotIntegrityError("snapshot model/blobs roots must be real directories")
    expected_blob_root = blobs.resolve(strict=True)
    rows: list[tuple[Path, Path, str, os.stat_result, os.stat_result]] = []
    try:
        entries = sorted(snapshot.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise SnapshotIntegrityError("snapshot entries are unreadable") from exc
    if not entries:
        raise SnapshotIntegrityError("snapshot must contain at least one file")
    for entry in entries:
        try:
            if not entry.is_symlink():
                raise SnapshotIntegrityError("every snapshot entry must be a symlinked blob")
            link_target = os.readlink(entry)
            resolved = entry.resolve(strict=True)
            if resolved.parent != expected_blob_root or not resolved.is_file():
                raise SnapshotIntegrityError("snapshot entry escapes its exact blobs directory")
            if (
                _HEX_40.fullmatch(resolved.name) is None
                and _HEX_64.fullmatch(resolved.name) is None
            ):
                raise SnapshotIntegrityError("snapshot blob has no recognized content identity")
            rows.append((entry, resolved, link_target, entry.lstat(), resolved.stat()))
        except SnapshotIntegrityError:
            raise
        except (OSError, RuntimeError) as exc:
            raise SnapshotIntegrityError("snapshot entry is unreadable or broken") from exc
    return snapshot, expected_blob_root, rows


def _attest_hf_snapshot(
    snapshot_path: Path,
    *,
    revision: str,
    expected_manifest_sha256: str,
) -> tuple[QwenSnapshotAttestation, tuple[_EntryFingerprint, ...]]:
    expected = _require_hex(
        expected_manifest_sha256,
        length=64,
        label="expected_manifest_sha256",
    )
    _, _, rows = _snapshot_layout(snapshot_path, revision=revision)
    manifest: list[dict[str, object]] = []
    fingerprints: list[_EntryFingerprint] = []
    verified_blobs: set[tuple[int, int]] = set()
    total_bytes = 0
    for entry, blob, link_target, link_stat, blob_stat in rows:
        blob_key = (blob_stat.st_dev, blob_stat.st_ino)
        if blob_key not in verified_blobs:
            if len(blob.name) == 64:
                observed_blob_id = _sha256_file(blob)
            else:
                observed_blob_id = _git_blob_sha1(blob, blob_stat.st_size)
            if observed_blob_id != blob.name:
                raise SnapshotIntegrityError(
                    "snapshot blob bytes do not match its content identity"
                )
            verified_blobs.add(blob_key)
        manifest.append(
            {
                "path": entry.name,
                "blob": blob.name,
                "size_bytes": blob_stat.st_size,
            }
        )
        total_bytes += blob_stat.st_size
        fingerprints.append(
            _EntryFingerprint(
                name=entry.name,
                link_target=link_target,
                link_stat=_stat_identity(link_stat),
                blob_name=blob.name,
                blob_stat=_stat_identity(blob_stat),
            )
        )
    observed_manifest = hashlib.sha256(_canonical_json_bytes(manifest)).hexdigest()
    if observed_manifest != expected:
        raise SnapshotIntegrityError(
            "snapshot manifest digest does not match the declared identity"
        )
    return (
        QwenSnapshotAttestation(
            revision=revision,
            manifest_sha256=observed_manifest,
            file_count=len(manifest),
            total_bytes=total_bytes,
        ),
        tuple(fingerprints),
    )


def attest_hf_snapshot(
    snapshot_path: Path,
    *,
    revision: str,
    expected_manifest_sha256: str,
) -> QwenSnapshotAttestation:
    """Verify blob contents and return the preregistration-compatible identity."""

    attestation, _ = _attest_hf_snapshot(
        snapshot_path,
        revision=revision,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    return attestation


@dataclass(frozen=True, slots=True)
class _SnapshotGuard:
    snapshot_path: Path
    revision: str
    attestation: QwenSnapshotAttestation
    fingerprints: tuple[_EntryFingerprint, ...]

    def verify_unchanged(self) -> None:
        _, _, rows = _snapshot_layout(self.snapshot_path, revision=self.revision)
        observed = tuple(
            _EntryFingerprint(
                name=entry.name,
                link_target=link_target,
                link_stat=_stat_identity(link_stat),
                blob_name=blob.name,
                blob_stat=_stat_identity(blob_stat),
            )
            for entry, blob, link_target, link_stat, blob_stat in rows
        )
        if observed != self.fingerprints:
            raise SnapshotIntegrityError("snapshot filesystem identity changed after attestation")


class QwenGenerationBackend(Protocol):
    """Heavy runtime seam used by the attested provider."""

    @property
    def runtime_identity(self) -> QwenRuntimeIdentity: ...

    def generate(
        self,
        *,
        snapshot_path: Path,
        prompt: bytes,
        settings: QwenLocalSettings,
        progress: Callable[[str], None],
    ) -> PlannerProviderResponse: ...


def _distribution_record_sha256(name: str) -> tuple[str, str]:
    try:
        distribution = importlib.metadata.distribution(name)
        record = distribution.read_text("RECORD")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"required distribution is unavailable: {name}") from exc
    if record is None:
        raise RuntimeError(f"required distribution has no RECORD: {name}")
    return distribution.version, hashlib.sha256(record.encode("utf-8")).hexdigest()


def _probe_transformers_runtime(device_index: int) -> QwenRuntimeIdentity:
    import torch
    import transformers

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable for the qualified Qwen provider")
    if device_index >= torch.cuda.device_count():
        raise RuntimeError("configured CUDA device index is unavailable")
    packages: dict[str, str] = {}
    records: dict[str, str] = {}
    for name in sorted(_REQUIRED_DISTRIBUTIONS):
        version, record_sha256 = _distribution_record_sha256(name)
        packages[name] = version
        records[name] = record_sha256
    torch_module = Path(cast(str, torch.__file__)).resolve(strict=True)
    transformers_module = Path(cast(str, transformers.__file__)).resolve(strict=True)
    capability = torch.cuda.get_device_capability(device_index)
    return QwenRuntimeIdentity(
        python_implementation=host_platform.python_implementation(),
        python_version=host_platform.python_version(),
        python_executable_sha256=_sha256_file(Path(sys.executable).resolve(strict=True)),
        platform=host_platform.platform(),
        packages=packages,
        package_record_sha256=records,
        runtime_module_sha256={
            "torch": _sha256_file(torch_module),
            "transformers": _sha256_file(transformers_module),
        },
        torch_cuda_version=str(torch.version.cuda),
        device_index=device_index,
        device_name=torch.cuda.get_device_name(device_index),
        compute_capability=f"{capability[0]}.{capability[1]}",
        hermetic=False,
        dependency_closure_complete=False,
    )


class TransformersQwenBackend:
    """In-process Transformers adapter; production assembly may later isolate it."""

    def __init__(self, *, device_index: int) -> None:
        self._device_index = device_index
        self._model: object | None = None
        self._processor: object | None = None
        self._cache_key: tuple[Path, int, str] | None = None

    @property
    def runtime_identity(self) -> QwenRuntimeIdentity:
        return _probe_transformers_runtime(self._device_index)

    def generate(
        self,
        *,
        snapshot_path: Path,
        prompt: bytes,
        settings: QwenLocalSettings,
        progress: Callable[[str], None],
    ) -> PlannerProviderResponse:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        started_ns = time.perf_counter_ns()
        device = f"cuda:{settings.device_index}"
        dtype = torch.bfloat16 if settings.dtype == "bfloat16" else torch.float16
        cache_key = (
            snapshot_path.expanduser().resolve(strict=True),
            settings.device_index,
            settings.dtype,
        )
        if self._cache_key is not None and self._cache_key != cache_key:
            raise RuntimeError("Qwen backend is already bound to a different model runtime")
        torch.use_deterministic_algorithms(True)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.backends.cudnn.benchmark = False
        torch.cuda.reset_peak_memory_stats(settings.device_index)
        progress("runtime_imported")
        if self._model is None or self._processor is None:
            progress("model_loading")
            self._model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                str(snapshot_path),
                dtype=dtype,
                device_map={"": device},
                low_cpu_mem_usage=True,
                local_files_only=True,
                trust_remote_code=False,
            )
            self._processor = AutoProcessor.from_pretrained(
                str(snapshot_path),
                local_files_only=True,
                trust_remote_code=False,
                use_fast=False,
            )
            self._model.eval()
            self._cache_key = cache_key
        progress("model_loaded")
        processor = self._processor
        model = self._model
        prompt_text = prompt.decode("utf-8", errors="strict")
        messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": QWEN_SYSTEM_INSTRUCTION}],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt_text}],
            },
        ]
        chat = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = processor(
            text=[chat],
            padding=True,
            return_tensors="pt",
        ).to(device)
        progress("prompt_tokenized")
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=settings.max_new_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None,
                repetition_penalty=1.05,
                use_cache=True,
            )
        torch.cuda.synchronize(settings.device_index)
        progress("generation_completed")
        input_tokens = int(inputs.input_ids.shape[-1])
        output = generated[0][input_tokens:]
        output_tokens = int(output.shape[-1])
        decoded = processor.batch_decode(
            [output],
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
        progress("response_decoded")
        ended_ns = time.perf_counter_ns()
        return PlannerProviderResponse(
            raw=decoded.encode("utf-8"),
            usage=PlannerUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                wall_time_ms=max(0, (ended_ns - started_ns) // 1_000_000),
                peak_vram_bytes=int(torch.cuda.max_memory_allocated(settings.device_index)),
                finish_reason=("length" if output_tokens >= settings.max_new_tokens else "stop"),
            ),
        )


class LocalQwenPlannerProvider:
    """Planner provider bound to one attested local snapshot and runtime."""

    def __init__(
        self,
        *,
        settings: QwenLocalSettings,
        backend: QwenGenerationBackend | None = None,
    ) -> None:
        self._settings = settings
        attestation, fingerprints = _attest_hf_snapshot(
            settings.snapshot_path,
            revision=settings.revision,
            expected_manifest_sha256=settings.snapshot_manifest_sha256,
        )
        self._guard = _SnapshotGuard(
            snapshot_path=settings.snapshot_path.expanduser().resolve(strict=True),
            revision=settings.revision,
            attestation=attestation,
            fingerprints=fingerprints,
        )
        expected_model_root = f"models--{settings.model_id.replace('/', '--')}"
        if self._guard.snapshot_path.parent.parent.name != expected_model_root:
            raise SnapshotIntegrityError(
                "model_id does not match the attested Hugging Face snapshot root"
            )
        self._backend = backend or TransformersQwenBackend(device_index=settings.device_index)
        self._runtime_identity = QwenRuntimeIdentity.model_validate(
            self._backend.runtime_identity.model_dump(mode="python")
        )
        if self._runtime_identity.device_index != settings.device_index:
            raise ValueError("runtime device index does not match qualified settings")
        self._implementation_sha256 = _sha256_file(Path(__file__).resolve(strict=True))

    def _verified_runtime_identity(self) -> QwenRuntimeIdentity:
        self._guard.verify_unchanged()
        if _sha256_file(Path(__file__).resolve(strict=True)) != self._implementation_sha256:
            raise SnapshotIntegrityError("provider implementation changed after construction")
        identity = QwenRuntimeIdentity.model_validate(
            self._backend.runtime_identity.model_dump(mode="python")
        )
        if identity != self._runtime_identity:
            raise RuntimeError("Qwen runtime identity changed after qualification")
        return identity

    @property
    def identity(self) -> PlannerProviderIdentity:
        runtime = self._verified_runtime_identity()
        attestation = self._guard.attestation
        return PlannerProviderIdentity(
            provider_id="qwen_local",
            model_id=self._settings.model_id,
            model_revision=self._settings.revision,
            model_snapshot_sha256=attestation.manifest_sha256,
            implementation_sha256=self._implementation_sha256,
            inference={
                "adapter": QWEN_PROVIDER_ADAPTER,
                "device_index": self._settings.device_index,
                "dtype": self._settings.dtype,
                "max_new_tokens": self._settings.max_new_tokens,
                "max_prompt_bytes": self._settings.max_prompt_bytes,
                "do_sample": False,
                "temperature": None,
                "top_p": None,
                "top_k": None,
                "repetition_penalty": 1.05,
                "local_files_only": True,
                "trust_remote_code": False,
                "use_fast_processor": False,
                "deterministic_algorithms": True,
                "allow_tf32": False,
                "system_instruction_sha256": hashlib.sha256(
                    QWEN_SYSTEM_INSTRUCTION.encode("utf-8")
                ).hexdigest(),
                "snapshot_file_count": attestation.file_count,
                "snapshot_total_bytes": attestation.total_bytes,
                "runtime": runtime.model_dump(mode="json"),
            },
            network_access=False,
        )

    def invoke(
        self,
        prompt: bytes,
        progress: Callable[[str], None],
    ) -> PlannerProviderResponse:
        if type(prompt) is not bytes:
            raise TypeError("Qwen planner prompt must be immutable bytes")
        if not 1 <= len(prompt) <= self._settings.max_prompt_bytes:
            raise ValueError("Qwen planner prompt violates its byte limit")
        try:
            prompt.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Qwen planner prompt is not UTF-8") from exc
        provider_identity_sha256(self.identity)
        response = self._backend.generate(
            snapshot_path=self._guard.snapshot_path,
            prompt=prompt,
            settings=self._settings,
            progress=progress,
        )
        if type(response) is not PlannerProviderResponse:
            raise TypeError("Qwen backend must return PlannerProviderResponse")
        provider_identity_sha256(self.identity)
        return response


__all__ = [
    "LocalQwenPlannerProvider",
    "QwenGenerationBackend",
    "QwenLocalSettings",
    "QwenRuntimeIdentity",
    "QwenSnapshotAttestation",
    "QWEN_SYSTEM_INSTRUCTION",
    "SnapshotIntegrityError",
    "TransformersQwenBackend",
    "attest_hf_snapshot",
]
