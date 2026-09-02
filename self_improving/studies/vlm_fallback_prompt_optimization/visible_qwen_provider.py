"""Content-bound visible-Qwen provider with a strict local Transformers backend."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import stat
import sys
import unicodedata
import warnings
from dataclasses import asdict, dataclass, replace
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from PIL import Image, UnidentifiedImageError

from self_improving.studies.vlm_fallback_prompt_optimization import (
    model_content,
    protocol,
)
from self_improving.studies.vlm_fallback_prompt_optimization.runner import (
    ArtifactBinding,
    ModelBinding,
    ProviderIdentity,
    ProviderOutcome,
    ProviderProgress,
    ResourceUsage,
    VisibleProviderRequest,
)

_PROVIDER_ID = "vlm_fallback.local_qwen_visible"
_PROVIDER_REVISION = "v1"
_PROCESSOR_CONFIG = MappingProxyType({"min_pixels": 200704, "max_pixels": 802816})
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_IMAGE_BYTES = 64 * 1024 * 1024
_MAX_INPUT_IMAGES = 6
_MAX_IMAGE_WIDTH = 16_384
_MAX_IMAGE_HEIGHT = 16_384
_MAX_IMAGE_PIXELS = 16_000_000
_MAX_RAW_RESPONSE_BYTES = 1024 * 1024
_MAX_PROMPT_BYTES = 256 * 1024
_MAX_TASK_CONTEXT_BYTES = 64 * 1024
_IMAGE_FORMATS = {
    ".jpeg": "JPEG",
    ".jpg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}
_TYPED_ARM_MODEL_ROLES = MappingProxyType(
    {
        "A1_typed_abstaining_critic_3b": "primary_local_vlm",
        "A2_typed_abstaining_critic_7b": "confirmatory_model_size_ceiling",
    }
)
_A0_UNSUPPORTED_MESSAGE = "A0_current_critic_3b requires the independent frozen baseline provider"
_OFFLINE_ENVIRONMENT_ERROR = (
    "concrete Transformers backend requires HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1"
)
_STAT_FIELDS = tuple(
    "st_dev st_ino st_mode st_nlink st_uid st_gid st_size st_mtime_ns st_ctime_ns".split()
)


class VisibleProviderIntegrityError(ValueError):
    """The configured local provider no longer matches its qualified identity."""


class UnsupportedVisibleArmError(ValueError):
    """The requested arm belongs to a different visible-provider interface."""


def _require_offline_transformers_environment() -> None:
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        raise RuntimeError(_OFFLINE_ENVIRONMENT_ERROR)


def _require_nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field} must be a non-empty built-in string")
    return value


def _require_bounded_utf8_text(value: Any, *, field: str, max_bytes: int) -> str:
    text = _require_nonempty_string(value, field)
    try:
        size_bytes = len(text.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError(f"{field} must be valid UTF-8 text") from error
    if size_bytes > max_bytes:
        raise ValueError(f"{field} exceeds its byte limit")
    return text


def _require_lower_hex(value: Any, length: int, field: str) -> str:
    if type(value) is not str or len(value) != length or value != value.lower():
        raise ValueError(f"{field} must be {length} lowercase hex characters")
    try:
        int(value, 16)
    except ValueError as error:
        raise ValueError(f"{field} must be {length} lowercase hex characters") from error
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive built-in integer")
    return value


def _require_absolute_path(value: Any, field: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError(f"{field} must be an absolute Path")
    return value


def _canonical_relative_path(value: Any, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or "\\" in value
        or "\0" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ValueError(f"{field} must be a canonical relative POSIX path")
    relative = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        relative.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in relative.parts
        or relative == PurePosixPath(".")
        or relative.as_posix() != value
    ):
        raise ValueError(f"{field} must be a canonical relative POSIX path")
    return value


def _processor_config_copy(value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(_PROCESSOR_CONFIG):
        raise ValueError("processor_config must contain exactly min_pixels and max_pixels")
    copied: dict[str, int] = {}
    for key in ("min_pixels", "max_pixels"):
        item = value[key]
        if type(item) is not int or item <= 0:
            raise ValueError(f"processor_config {key} must be a positive built-in integer")
        copied[key] = item
    if copied["min_pixels"] > copied["max_pixels"]:
        raise ValueError("processor_config min_pixels must not exceed max_pixels")
    if copied != _PROCESSOR_CONFIG:
        raise ValueError("processor_config does not match the frozen study configuration")
    return MappingProxyType(copied)


@dataclass(frozen=True)
class QualifiedVisibleModel:
    """Exact model and content receipts accepted by this provider candidate."""

    arm: str
    model_role: str
    model_id: str
    revision: str
    snapshot_manifest_sha256: str
    model_content_manifest_path: Path
    model_content_manifest_sha256: str
    model_roster_sha256: str
    local_snapshot_path: Path
    max_new_tokens: int

    def __post_init__(self) -> None:
        arm = _require_nonempty_string(self.arm, "arm")
        model_role = _require_nonempty_string(self.model_role, "model_role")
        expected_model_role = _TYPED_ARM_MODEL_ROLES.get(arm)
        if expected_model_role is None:
            raise ValueError("arm is not a supported typed visible-study arm")
        if model_role != expected_model_role:
            raise ValueError("model_role does not match the typed visible-study arm")
        _require_nonempty_string(self.model_id, "model_id")
        _require_lower_hex(self.revision, 40, "revision")
        _require_lower_hex(self.snapshot_manifest_sha256, 64, "snapshot_manifest_sha256")
        _require_absolute_path(self.model_content_manifest_path, "model_content_manifest_path")
        _require_lower_hex(
            self.model_content_manifest_sha256,
            64,
            "model_content_manifest_sha256",
        )
        _require_lower_hex(self.model_roster_sha256, 64, "model_roster_sha256")
        snapshot = _require_absolute_path(self.local_snapshot_path, "local_snapshot_path")
        expected_root = f"models--{self.model_id.replace('/', '--')}"
        if (
            snapshot.name != self.revision
            or snapshot.parent.name != "snapshots"
            or snapshot.parent.parent.name != expected_root
        ):
            raise ValueError("local_snapshot_path does not match model_id and revision")
        if _require_positive_int(self.max_new_tokens, "max_new_tokens") != 768:
            raise ValueError("max_new_tokens does not match the frozen study configuration")


@dataclass(frozen=True)
class QwenVisibleBackendIdentity:
    """Content identity of one injected local inference implementation."""

    backend_id: str
    revision: str
    implementation_sha256: str
    network_access: bool

    def __post_init__(self) -> None:
        _require_nonempty_string(self.backend_id, "backend_id")
        _require_nonempty_string(self.revision, "backend revision")
        _require_lower_hex(self.implementation_sha256, 64, "backend implementation_sha256")
        if type(self.network_access) is not bool:
            raise ValueError("backend network_access must be a built-in boolean")
        if self.network_access:
            raise ValueError("visible Qwen backend must forbid network access")


@dataclass(frozen=True)
class QwenRuntimeFingerprint:
    """Digests of selected runtime facts, never a complete dependency closure."""

    fingerprint_id: str
    version_facts_sha256: str
    entrypoint_facts_sha256: str
    device_facts_sha256: str
    dependency_closure_complete: bool

    def __post_init__(self) -> None:
        _require_nonempty_string(self.fingerprint_id, "runtime fingerprint_id")
        _require_lower_hex(
            self.version_facts_sha256,
            64,
            "runtime version_facts_sha256",
        )
        _require_lower_hex(
            self.entrypoint_facts_sha256,
            64,
            "runtime entrypoint_facts_sha256",
        )
        _require_lower_hex(
            self.device_facts_sha256,
            64,
            "runtime device_facts_sha256",
        )
        if type(self.dependency_closure_complete) is not bool:
            raise ValueError("runtime dependency_closure_complete must be a built-in boolean")
        if self.dependency_closure_complete:
            raise ValueError("runtime fingerprint is not a complete dependency closure")


@dataclass(frozen=True)
class BoundVisibleImage:
    """Verified immutable bytes passed across the external backend seam."""

    path: str
    content: bytes
    content_sha256: str
    size_bytes: int


@dataclass(frozen=True)
class QwenVisibleGenerationRequest:
    """Fully bound input given to the injected inference backend."""

    arm: str
    task_context: str
    prompt: str
    processor_config: Mapping[str, int]
    seed: int
    model: QualifiedVisibleModel
    images: tuple[BoundVisibleImage, ...]


@dataclass(frozen=True)
class QwenVisibleGeneration:
    """Raw backend response plus measured resource usage."""

    raw_response: bytes
    resource: ResourceUsage


class QwenVisibleBackend(Protocol):
    """True external side-effect seam; implementations must remain offline."""

    @property
    def identity(self) -> QwenVisibleBackendIdentity: ...

    def generate(
        self,
        request: QwenVisibleGenerationRequest,
        progress: ProviderProgress,
    ) -> QwenVisibleGeneration: ...


@dataclass
class _QualifiedModelState:
    model: QualifiedVisibleModel
    manifest: dict[str, Any]
    baseline: model_content.ModelContentSnapshot


def _source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _copy_backend_identity(value: Any) -> QwenVisibleBackendIdentity:
    if type(value) is not QwenVisibleBackendIdentity:
        raise VisibleProviderIntegrityError("backend identity must use QwenVisibleBackendIdentity")
    return QwenVisibleBackendIdentity(
        backend_id=value.backend_id,
        revision=value.revision,
        implementation_sha256=value.implementation_sha256,
        network_access=value.network_access,
    )


def _provider_implementation_sha256(
    *,
    source_sha256: str,
    backend: QwenVisibleBackendIdentity,
    models: tuple[QualifiedVisibleModel, ...],
    processor_config: Mapping[str, int],
) -> str:
    return protocol.canonical_sha256(
        {
            "provider_id": _PROVIDER_ID,
            "provider_revision": _PROVIDER_REVISION,
            "source_sha256": source_sha256,
            "backend": asdict(backend),
            "models": [
                {
                    "arm": item.arm,
                    "model_role": item.model_role,
                    "model_id": item.model_id,
                    "revision": item.revision,
                    "snapshot_manifest_sha256": item.snapshot_manifest_sha256,
                    "model_content_manifest_sha256": item.model_content_manifest_sha256,
                    "model_roster_sha256": item.model_roster_sha256,
                    "max_new_tokens": item.max_new_tokens,
                }
                for item in sorted(models, key=lambda model: model.arm)
            ],
            "processor_config": dict(processor_config),
        }
    )


def _stat_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(metadata, field) for field in _STAT_FIELDS)


def _read_stable_regular_file(path: Path, expected_size: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor: int | None = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"image is not a readable regular file: {path}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"image is not a regular file: {path}")
        if before.st_size != expected_size:
            raise ValueError("image size does not match its artifact binding")
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        with stream:
            raw = stream.read(expected_size + 1)
            after = os.fstat(stream.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if _stat_fingerprint(before) != _stat_fingerprint(after):
        raise ValueError(f"image changed while being read: {path}")
    if len(raw) != expected_size:
        raise ValueError("image size does not match its artifact binding")
    return raw


def _bind_images(request: VisibleProviderRequest) -> tuple[BoundVisibleImage, ...]:
    if type(request.image_paths) is not tuple or type(request.artifacts) is not tuple:
        raise ValueError("image_paths and artifacts must be exact tuples")
    image_artifacts: list[ArtifactBinding] = []
    for artifact in request.artifacts:
        if type(artifact) is not ArtifactBinding:
            raise ValueError("artifacts must contain ArtifactBinding values")
        path = _canonical_relative_path(artifact.path, "artifact path")
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in _IMAGE_FORMATS:
            _require_lower_hex(artifact.sha256, 64, "artifact sha256")
            if type(artifact.size_bytes) is not int or artifact.size_bytes < 0:
                raise ValueError("artifact size_bytes must be a non-negative built-in integer")
            image_artifacts.append(artifact)
    if not image_artifacts or len(request.image_paths) != len(image_artifacts):
        raise ValueError("image_paths must match every image artifact exactly once")
    paths = [artifact.path for artifact in image_artifacts]
    if len(paths) != len(set(paths)):
        raise ValueError("image artifact paths must be unique")
    if len(image_artifacts) > _MAX_INPUT_IMAGES:
        raise ValueError("image count exceeds the visible provider limit")
    if any(artifact.size_bytes > _MAX_IMAGE_BYTES for artifact in image_artifacts):
        raise ValueError("image size exceeds the visible provider per-image limit")
    if sum(artifact.size_bytes for artifact in image_artifacts) > _MAX_TOTAL_IMAGE_BYTES:
        raise ValueError("image bytes exceed the visible provider aggregate limit")
    bound: list[BoundVisibleImage] = []
    for image_path, artifact in zip(request.image_paths, image_artifacts, strict=True):
        if not isinstance(image_path, Path) or not image_path.is_absolute():
            raise ValueError("image_paths must contain absolute Path values")
        relative = PurePosixPath(artifact.path)
        if tuple(image_path.parts[-len(relative.parts) :]) != relative.parts:
            raise ValueError("image path does not match its artifact binding")
        raw = _read_stable_regular_file(image_path, artifact.size_bytes)
        observed_sha256 = hashlib.sha256(raw).hexdigest()
        if observed_sha256 != artifact.sha256:
            raise ValueError("image digest does not match its artifact binding")
        expected_format = _IMAGE_FORMATS[relative.suffix.lower()]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(raw)) as image:
                    width, height = image.size
                    if (
                        width <= 0
                        or height <= 0
                        or width > _MAX_IMAGE_WIDTH
                        or height > _MAX_IMAGE_HEIGHT
                        or width * height > _MAX_IMAGE_PIXELS
                    ):
                        raise ValueError("image dimensions exceed the decoded pixel limit")
                    observed_format = image.format
                    image.verify()
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
            raise ValueError("image dimensions exceed the decoded pixel limit") from error
        except (OSError, UnidentifiedImageError) as error:
            raise ValueError("image bytes are not a valid supported image") from error
        if observed_format != expected_format:
            raise ValueError("image format does not match its filename extension")
        bound.append(
            BoundVisibleImage(
                path=artifact.path,
                content=raw,
                content_sha256=observed_sha256,
                size_bytes=len(raw),
            )
        )
    return tuple(bound)


def _strict_json_object(raw: bytes) -> dict[str, Any] | None:
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
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        return None
    return decoded if type(decoded) is dict else None


def _validate_usage(value: Any, *, max_new_tokens: int) -> ResourceUsage:
    if type(value) is not ResourceUsage:
        raise ValueError("backend resource usage must use ResourceUsage")
    fields = asdict(value)
    if any(type(item) is not int or item < 0 for item in fields.values()):
        raise ValueError("backend resource usage must contain non-negative built-in integers")
    if value.visible_vlm_invocations != 1:
        raise ValueError("backend must report exactly one visible VLM invocation")
    forbidden = (
        value.network_calls,
        value.remote_paid_calls,
        value.compile_attempts,
        value.fresh_physical_replays,
        value.runtime_steps,
        value.contact_window_steps,
        value.prompt_rewrites,
    )
    if any(forbidden):
        raise ValueError("visible provider reported a forbidden side effect")
    if value.output_tokens > max_new_tokens:
        raise ValueError("backend output_tokens exceed the qualified max_new_tokens")
    return value


@dataclass(frozen=True)
class QwenTransformersRuntime:
    """Injected bindings plus an explicitly partial runtime fingerprint."""

    fingerprint: QwenRuntimeFingerprint
    device_index: int
    torch: Any
    auto_processor: Any
    model_class: Any
    fingerprint_probe: Callable[[], QwenRuntimeFingerprint]


def _module_sha256(module: Any, label: str) -> str:
    module_file = getattr(module, "__file__", None)
    if type(module_file) is not str:
        raise RuntimeError(f"{label} module has no exact source path")
    try:
        path = Path(module_file).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f"{label} module source is unavailable") from error
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_selected_runtime_fingerprint(
    *,
    device_index: int,
    torch: Any,
    transformers: Any,
    auto_processor: Any,
    model_class: Any,
) -> QwenRuntimeFingerprint:
    """Hash the same explicitly partial runtime facts at every integrity seam."""

    try:
        accelerate_version = importlib.metadata.version("accelerate")
    except importlib.metadata.PackageNotFoundError as error:
        raise RuntimeError("required local Qwen runtime dependency is unavailable") from error
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable for the local visible Qwen backend")
    if device_index >= torch.cuda.device_count():
        raise RuntimeError("configured CUDA device index is unavailable")
    torch_version = str(getattr(torch, "__version__", ""))
    transformers_version = str(getattr(transformers, "__version__", ""))
    cuda_version = str(getattr(torch.version, "cuda", ""))
    device_name = str(torch.cuda.get_device_name(device_index))
    capability = torch.cuda.get_device_capability(device_index)
    auto_processor_entrypoint = ".".join(
        (
            str(getattr(auto_processor, "__module__", "")),
            str(getattr(auto_processor, "__qualname__", "")),
        )
    ).strip(".")
    model_class_entrypoint = ".".join(
        (
            str(getattr(model_class, "__module__", "")),
            str(getattr(model_class, "__qualname__", "")),
        )
    ).strip(".")
    if (
        not torch_version
        or not transformers_version
        or not accelerate_version
        or not cuda_version
        or not device_name
        or type(capability) is not tuple
        or len(capability) != 2
        or any(type(item) is not int or item < 0 for item in capability)
        or not auto_processor_entrypoint
        or not model_class_entrypoint
    ):
        raise RuntimeError("local Qwen runtime fingerprint is incomplete")
    version_facts = {
        "python": sys.version,
        "torch": torch_version,
        "transformers": transformers_version,
        "accelerate": accelerate_version,
        "cuda": cuda_version,
    }
    entrypoint_facts = {
        "python_executable_sha256": hashlib.sha256(
            Path(sys.executable).resolve(strict=True).read_bytes()
        ).hexdigest(),
        "torch_module": str(getattr(torch, "__name__", "")),
        "torch_module_sha256": _module_sha256(torch, "torch"),
        "transformers_module": str(getattr(transformers, "__name__", "")),
        "transformers_module_sha256": _module_sha256(transformers, "transformers"),
        "auto_processor_entrypoint": auto_processor_entrypoint,
        "model_class_entrypoint": model_class_entrypoint,
    }
    device_facts = {
        "device_index": device_index,
        "device_name": device_name,
        "compute_capability": f"{capability[0]}.{capability[1]}",
    }
    return QwenRuntimeFingerprint(
        fingerprint_id="transformers-qwen2.5-vl-selected-runtime-facts",
        version_facts_sha256=protocol.canonical_sha256(version_facts),
        entrypoint_facts_sha256=protocol.canonical_sha256(entrypoint_facts),
        device_facts_sha256=protocol.canonical_sha256(device_facts),
        dependency_closure_complete=False,
    )


def _discover_transformers_runtime(device_index: int) -> QwenTransformersRuntime:
    """Discover local bindings and attach their repeatable selected-facts probe."""

    try:
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
    except ImportError as error:
        raise RuntimeError("required local Qwen runtime dependency is unavailable") from error
    auto_processor = getattr(transformers, "AutoProcessor", None)
    model_class = getattr(transformers, "Qwen2_5_VLForConditionalGeneration", None)
    if auto_processor is None or model_class is None:
        raise RuntimeError("installed Transformers lacks Qwen2.5-VL support")

    def fingerprint_probe() -> QwenRuntimeFingerprint:
        return _collect_selected_runtime_fingerprint(
            device_index=device_index,
            torch=torch,
            transformers=transformers,
            auto_processor=auto_processor,
            model_class=model_class,
        )

    fingerprint = fingerprint_probe()
    return QwenTransformersRuntime(
        fingerprint=fingerprint,
        device_index=device_index,
        torch=torch,
        auto_processor=auto_processor,
        model_class=model_class,
        fingerprint_probe=fingerprint_probe,
    )


def _copy_runtime_fingerprint(value: Any) -> QwenRuntimeFingerprint:
    if type(value) is not QwenRuntimeFingerprint:
        raise ValueError("runtime fingerprint must use QwenRuntimeFingerprint")
    return QwenRuntimeFingerprint(
        fingerprint_id=value.fingerprint_id,
        version_facts_sha256=value.version_facts_sha256,
        entrypoint_facts_sha256=value.entrypoint_facts_sha256,
        device_facts_sha256=value.device_facts_sha256,
        dependency_closure_complete=value.dependency_closure_complete,
    )


def _copy_runtime(value: Any) -> QwenTransformersRuntime:
    if type(value) is not QwenTransformersRuntime:
        raise ValueError("runtime must use QwenTransformersRuntime")
    if type(value.device_index) is not int or value.device_index < 0:
        raise ValueError("runtime device_index must be a non-negative built-in integer")
    fingerprint = _copy_runtime_fingerprint(value.fingerprint)
    if (
        value.torch is None
        or value.auto_processor is None
        or value.model_class is None
        or not callable(value.fingerprint_probe)
    ):
        raise ValueError("runtime dependency bindings must be complete")
    return QwenTransformersRuntime(
        fingerprint=fingerprint,
        device_index=value.device_index,
        torch=value.torch,
        auto_processor=value.auto_processor,
        model_class=value.model_class,
        fingerprint_probe=value.fingerprint_probe,
    )


class TransformersQwenVisibleBackend:
    """Strict local Qwen adapter; greedy settings fixed, GPU bitwise parity unqualified."""

    def __init__(
        self,
        *,
        device_index: int = 0,
        runtime: QwenTransformersRuntime | None = None,
    ) -> None:
        if type(device_index) is not int or device_index < 0:
            raise ValueError("device_index must be a non-negative built-in integer")
        _require_offline_transformers_environment()
        discovered = _discover_transformers_runtime(device_index) if runtime is None else runtime
        self._runtime = _copy_runtime(discovered)
        if self._runtime.device_index != device_index:
            raise ValueError("runtime device index does not match backend configuration")
        self._runtime_fingerprint = self._runtime.fingerprint
        try:
            live_fingerprint = _copy_runtime_fingerprint(self._runtime.fingerprint_probe())
        except Exception as error:
            raise ValueError("runtime live fingerprint cannot be collected") from error
        if live_fingerprint != self._runtime_fingerprint:
            raise ValueError("runtime fingerprint does not match live selected facts")
        self._runtime_components = (
            self._runtime.torch,
            self._runtime.auto_processor,
            self._runtime.model_class,
            self._runtime.fingerprint_probe,
        )
        self._source_digest = _source_sha256()
        self._identity = QwenVisibleBackendIdentity(
            backend_id="vlm_fallback.transformers_qwen_visible",
            revision="v1",
            implementation_sha256=protocol.canonical_sha256(
                {
                    "source_sha256": self._source_digest,
                    "runtime_fingerprint": asdict(self._runtime_fingerprint),
                    "device_index": device_index,
                    "dtype": "bfloat16",
                    "loader": {
                        "local_files_only": True,
                        "trust_remote_code": False,
                        "use_fast": False,
                    },
                    "generation": {
                        "do_sample": False,
                        "temperature": None,
                        "top_p": None,
                        "top_k": None,
                        "num_beams": 1,
                        "use_cache": True,
                    },
                    "request_seed_binding": "receipt input only; provider does not set runtime RNG",
                    "supported_arm_model_roles": dict(_TYPED_ARM_MODEL_ROLES),
                    "messages": "system(frozen_prompt), user(labeled_images, task_context)",
                }
            ),
            network_access=False,
        )
        self._model: Any = None
        self._processor: Any = None
        self._cache_key: tuple[Path, str, tuple[tuple[str, int], ...]] | None = None

    @property
    def identity(self) -> QwenVisibleBackendIdentity:
        self._verify_integrity()
        return self._identity

    def _verify_integrity(self) -> None:
        if _source_sha256() != self._source_digest:
            raise VisibleProviderIntegrityError("Transformers backend source changed")
        try:
            observed = _copy_runtime(self._runtime)
            live_fingerprint = _copy_runtime_fingerprint(observed.fingerprint_probe())
        except Exception as error:
            raise VisibleProviderIntegrityError(
                "Transformers runtime fingerprint changed"
            ) from error
        if (
            observed.fingerprint != self._runtime_fingerprint
            or live_fingerprint != self._runtime_fingerprint
            or observed.torch is not self._runtime_components[0]
            or observed.auto_processor is not self._runtime_components[1]
            or observed.model_class is not self._runtime_components[2]
            or observed.fingerprint_probe is not self._runtime_components[3]
        ):
            raise VisibleProviderIntegrityError("Transformers runtime fingerprint changed")

    def _load(self, request: QwenVisibleGenerationRequest) -> tuple[Any, Any]:
        runtime = self._runtime
        torch = runtime.torch
        snapshot_path = request.model.local_snapshot_path
        cache_key = (
            snapshot_path,
            request.model.revision,
            tuple(sorted(request.processor_config.items())),
        )
        if self._cache_key is not None and self._cache_key != cache_key:
            self._model = None
            self._processor = None
            self._cache_key = None
            torch.cuda.empty_cache()
        if self._model is None or self._processor is None:
            self._model = runtime.model_class.from_pretrained(
                str(snapshot_path),
                torch_dtype=torch.bfloat16,
                device_map={"": f"cuda:{runtime.device_index}"},
                low_cpu_mem_usage=True,
                local_files_only=True,
                trust_remote_code=False,
            )
            self._processor = runtime.auto_processor.from_pretrained(
                str(snapshot_path),
                min_pixels=request.processor_config["min_pixels"],
                max_pixels=request.processor_config["max_pixels"],
                local_files_only=True,
                trust_remote_code=False,
                use_fast=False,
            )
            self._model.eval()
            self._cache_key = cache_key
        return self._model, self._processor

    def generate(
        self,
        request: QwenVisibleGenerationRequest,
        progress: ProviderProgress,
    ) -> QwenVisibleGeneration:
        if type(request) is not QwenVisibleGenerationRequest:
            raise ValueError("request must use QwenVisibleGenerationRequest")
        if request.arm == "A0_current_critic_3b":
            raise UnsupportedVisibleArmError(_A0_UNSUPPORTED_MESSAGE)
        if request.arm not in _TYPED_ARM_MODEL_ROLES:
            raise UnsupportedVisibleArmError("backend supports only typed visible-study arms")
        _require_offline_transformers_environment()
        if not callable(progress):
            raise ValueError("progress must be callable")
        if (
            type(request.images) is not tuple
            or not request.images
            or any(type(image) is not BoundVisibleImage for image in request.images)
        ):
            raise ValueError("runtime request must contain bound visible images")
        if type(request.seed) is not int or request.seed < 0:
            raise ValueError("seed must be a non-negative built-in integer")
        _require_bounded_utf8_text(
            request.prompt,
            field="prompt",
            max_bytes=_MAX_PROMPT_BYTES,
        )
        _require_bounded_utf8_text(
            request.task_context,
            field="task_context",
            max_bytes=_MAX_TASK_CONTEXT_BYTES,
        )
        processor_config = _processor_config_copy(request.processor_config)
        request = replace(request, processor_config=processor_config)
        self._verify_integrity()
        runtime = self._runtime
        torch = runtime.torch
        if not torch.cuda.is_available() or runtime.device_index >= torch.cuda.device_count():
            raise RuntimeError("qualified CUDA device became unavailable")
        device = torch.device(f"cuda:{runtime.device_index}")
        with torch.cuda.device(device):
            torch.cuda.reset_peak_memory_stats(device)
            stream = torch.cuda.current_stream(device)
            start_event = torch.cuda.Event(enable_timing=True)
            end_event = torch.cuda.Event(enable_timing=True)
            start_event.record(stream)
            progress("runtime_verified")
            model, processor = self._load(request)
            progress("model_loaded")
            images: list[Image.Image] = []
            try:
                for bound in request.images:
                    with Image.open(BytesIO(bound.content)) as opened:
                        opened.load()
                        images.append(opened.convert("RGB"))
                image_content: list[dict[str, Any]] = []
                for index, (bound, image) in enumerate(
                    zip(request.images, images, strict=True),
                    start=1,
                ):
                    image_content.extend(
                        [
                            {
                                "type": "text",
                                "text": (
                                    f"Rendered view {index}: {PurePosixPath(bound.path).name}"
                                ),
                            },
                            {"type": "image", "image": image},
                        ]
                    )
                messages = [
                    {
                        "role": "system",
                        "content": [{"type": "text", "text": request.prompt}],
                    },
                    {
                        "role": "user",
                        "content": [
                            *image_content,
                            {"type": "text", "text": request.task_context},
                        ],
                    },
                ]
                chat = processor.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
                inputs = processor(
                    text=[chat],
                    images=images,
                    padding=True,
                    return_tensors="pt",
                ).to(device)
            finally:
                for image in images:
                    image.close()
            progress("inputs_encoded")
            with torch.inference_mode():
                generated = model.generate(
                    **inputs,
                    max_new_tokens=request.model.max_new_tokens,
                    do_sample=False,
                    temperature=None,
                    top_p=None,
                    top_k=None,
                    num_beams=1,
                    use_cache=True,
                )
            end_event.record(stream)
            stream.synchronize()
            progress("generation_completed")
            input_tokens = int(inputs.input_ids.shape[-1])
            output = generated[0][input_tokens:]
            output_tokens = int(output.shape[-1])
            decoded = processor.batch_decode(
                [output],
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )
            if type(decoded) is not list or len(decoded) != 1 or type(decoded[0]) is not str:
                raise RuntimeError("Transformers decoder returned an invalid response")
            raw_response = decoded[0].encode("utf-8")
            if len(raw_response) > _MAX_RAW_RESPONSE_BYTES:
                raise RuntimeError("Transformers response exceeds the visible provider byte limit")
            peak_vram_bytes = int(torch.cuda.max_memory_allocated(device))
            if peak_vram_bytes < 0:
                raise RuntimeError("CUDA reported negative peak memory")
            gpu_time_ms = max(0, int(round(float(start_event.elapsed_time(end_event)))))
        self._verify_integrity()
        progress("response_decoded")
        return QwenVisibleGeneration(
            raw_response=raw_response,
            resource=ResourceUsage(
                gpu_time_ms=gpu_time_ms,
                peak_vram_mib=(peak_vram_bytes + (1024 * 1024 - 1)) // (1024 * 1024),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                visible_vlm_invocations=1,
            ),
        )


class LocalQwenVisibleProvider:
    """Deep provider module implementing the runner's visible critic protocol."""

    def __init__(
        self,
        *,
        models: tuple[QualifiedVisibleModel, ...],
        backend: QwenVisibleBackend,
        processor_config: Mapping[str, Any] = _PROCESSOR_CONFIG,
    ) -> None:
        if type(models) is not tuple or not models:
            raise ValueError("models must be a non-empty exact tuple")
        if any(type(model) is not QualifiedVisibleModel for model in models):
            raise ValueError("models must contain QualifiedVisibleModel values")
        arms = [model.arm for model in models]
        if len(arms) != len(set(arms)):
            raise ValueError("qualified model arms must be unique")
        self._processor_config = _processor_config_copy(processor_config)
        self._backend = backend
        self._backend_identity = _copy_backend_identity(backend.identity)
        self._states: dict[str, _QualifiedModelState] = {}
        for model in models:
            observed_snapshot_sha256 = protocol.canonical_sha256(
                protocol.build_hf_snapshot_manifest(model.local_snapshot_path)
            )
            if observed_snapshot_sha256 != model.snapshot_manifest_sha256:
                raise VisibleProviderIntegrityError("qualified snapshot manifest digest mismatch")
            manifest = model_content.load_model_content_manifest(
                model.model_content_manifest_path,
                model.model_content_manifest_sha256,
                model_id=model.model_id,
                revision=model.revision,
            )
            baseline = model_content.verify_model_content(model.local_snapshot_path, manifest)
            if baseline.roster_sha256 != model.model_roster_sha256:
                raise VisibleProviderIntegrityError(
                    "qualified model roster does not match verified local content"
                )
            self._states[model.arm] = _QualifiedModelState(model, manifest, baseline)
        self._source_digest = _source_sha256()
        implementation_sha256 = _provider_implementation_sha256(
            source_sha256=self._source_digest,
            backend=self._backend_identity,
            models=models,
            processor_config=self._processor_config,
        )
        self._identity = ProviderIdentity(
            provider_id=_PROVIDER_ID,
            revision=_PROVIDER_REVISION,
            implementation_sha256=implementation_sha256,
            kind="local_qwen",
            production_eligible=False,
        )

    @property
    def identity(self) -> ProviderIdentity:
        self._verify_provider_integrity()
        return self._identity

    def _verify_provider_integrity(self) -> None:
        if _source_sha256() != self._source_digest:
            raise VisibleProviderIntegrityError("provider source changed after qualification")
        if _copy_backend_identity(self._backend.identity) != self._backend_identity:
            raise VisibleProviderIntegrityError("backend identity changed after qualification")

    @staticmethod
    def _require_exact_model_binding(
        binding: Any,
        qualified: QualifiedVisibleModel,
    ) -> None:
        if type(binding) is not ModelBinding:
            raise ValueError("request model must use ModelBinding")
        expected = (
            qualified.model_role,
            qualified.model_id,
            qualified.revision,
            qualified.snapshot_manifest_sha256,
            qualified.model_content_manifest_sha256,
            qualified.model_roster_sha256,
            qualified.local_snapshot_path,
            qualified.max_new_tokens,
        )
        observed = (
            binding.role,
            binding.model_id,
            binding.revision,
            binding.snapshot_manifest_sha256,
            binding.model_content_manifest_sha256,
            binding.model_roster_sha256,
            binding.local_snapshot_path,
            binding.max_new_tokens,
        )
        if observed != expected:
            raise VisibleProviderIntegrityError(
                "request model binding does not match the qualified model"
            )

    def _refresh_model(self, state: _QualifiedModelState) -> None:
        refreshed = model_content.refresh_model_content(
            state.model.local_snapshot_path,
            state.manifest,
            state.baseline,
        )
        if refreshed.roster_sha256 != state.model.model_roster_sha256:
            raise VisibleProviderIntegrityError("qualified model content roster changed")
        state.baseline = refreshed

    def invoke(
        self,
        request: VisibleProviderRequest,
        progress: ProviderProgress,
    ) -> ProviderOutcome:
        if type(request) is not VisibleProviderRequest:
            raise ValueError("request must use VisibleProviderRequest")
        if request.arm == "A0_current_critic_3b":
            raise UnsupportedVisibleArmError(_A0_UNSUPPORTED_MESSAGE)
        if not callable(progress):
            raise ValueError("progress must be callable")
        if type(request.seed) is not int or request.seed < 0:
            raise ValueError("seed must be a non-negative built-in integer")
        _require_bounded_utf8_text(
            request.prompt,
            field="prompt",
            max_bytes=_MAX_PROMPT_BYTES,
        )
        _require_bounded_utf8_text(
            request.task_context,
            field="task_context",
            max_bytes=_MAX_TASK_CONTEXT_BYTES,
        )
        self._verify_provider_integrity()
        if request.arm not in self._states:
            raise VisibleProviderIntegrityError("request arm has no qualified local model")
        state = self._states[request.arm]
        self._require_exact_model_binding(request.model, state.model)
        processor_config = _processor_config_copy(request.processor_config)
        if type(request.resource_reservation) is not ResourceUsage:
            raise ValueError("resource_reservation must use ResourceUsage")
        self._refresh_model(state)
        images = _bind_images(request)
        backend_request = QwenVisibleGenerationRequest(
            arm=request.arm,
            task_context=request.task_context,
            prompt=request.prompt,
            processor_config=processor_config,
            seed=request.seed,
            model=state.model,
            images=images,
        )
        generation: Any = None
        try:
            generation = self._backend.generate(backend_request, progress)
        finally:
            self._refresh_model(state)
            self._verify_provider_integrity()
        if type(generation) is not QwenVisibleGeneration:
            raise ValueError("backend must return QwenVisibleGeneration")
        if type(generation.raw_response) is not bytes:
            raise ValueError("backend raw_response must be exact bytes")
        if len(generation.raw_response) > _MAX_RAW_RESPONSE_BYTES:
            raise ValueError("backend raw_response exceeds the visible provider byte limit")
        usage = _validate_usage(generation.resource, max_new_tokens=state.model.max_new_tokens)
        parsed = _strict_json_object(generation.raw_response)
        if parsed is None:
            return ProviderOutcome(
                decision="visible_review_format_invalid",
                result={},
                raw_response=generation.raw_response,
                parsed_response=None,
                abstained=True,
                resource=usage,
                claims_physical_pass=False,
            )
        result = parsed
        checks = result.get("checks")
        abstained = (isinstance(checks, Mapping) and "abstain" in checks.values()) or result.get(
            "overall"
        ) == "review_required"
        return ProviderOutcome(
            decision="visible_review_complete",
            result=result,
            raw_response=generation.raw_response,
            parsed_response=result,
            abstained=abstained,
            resource=usage,
            claims_physical_pass=False,
        )


__all__ = [
    "BoundVisibleImage",
    "LocalQwenVisibleProvider",
    "QualifiedVisibleModel",
    "QwenVisibleBackend",
    "QwenVisibleBackendIdentity",
    "QwenVisibleGeneration",
    "QwenVisibleGenerationRequest",
    "QwenRuntimeFingerprint",
    "QwenTransformersRuntime",
    "TransformersQwenVisibleBackend",
    "UnsupportedVisibleArmError",
    "VisibleProviderIntegrityError",
]
