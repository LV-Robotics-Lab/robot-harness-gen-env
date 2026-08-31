from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, cast

import pytest
from pydantic import ValidationError

from self_improving.harness import system2 as system2_api
from self_improving.harness.system2 import qwen_local as qwen_module
from self_improving.harness.system2.planner import (
    PlannerProviderResponse,
    PlannerUsage,
    provider_identity_sha256,
)
from self_improving.harness.system2.qwen_local import (
    LocalQwenPlannerProvider,
    QwenLocalSettings,
    QwenRuntimeIdentity,
    QwenSnapshotAttestation,
    SnapshotIntegrityError,
    TransformersQwenBackend,
    attest_hf_snapshot,
)

_REVISION = "6" * 40


def test_system2_public_api_exports_the_attested_qwen_boundary() -> None:
    assert system2_api.LocalQwenPlannerProvider is LocalQwenPlannerProvider
    assert system2_api.QwenLocalSettings is QwenLocalSettings
    assert system2_api.attest_hf_snapshot is attest_hf_snapshot


def _git_blob_id(payload: bytes) -> str:
    return hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()


def _snapshot(tmp_path: Path, files: dict[str, bytes] | None = None) -> tuple[Path, str]:
    files = files or {"config.json": b'{"model_type":"qwen2_5_vl"}\n'}
    model_root = tmp_path / "models--Qwen--Qwen2.5-VL-3B-Instruct"
    blobs = model_root / "blobs"
    snapshot = model_root / "snapshots" / _REVISION
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    manifest = []
    for name, payload in sorted(files.items()):
        blob_id = _git_blob_id(payload)
        (blobs / blob_id).write_bytes(payload)
        (snapshot / name).symlink_to(Path("..") / ".." / "blobs" / blob_id)
        manifest.append({"path": name, "blob": blob_id, "size_bytes": len(payload)})
    digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return snapshot, digest


def _settings(snapshot: Path, digest: str, **updates: object) -> QwenLocalSettings:
    values: dict[str, object] = {
        "model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
        "revision": _REVISION,
        "snapshot_path": snapshot,
        "snapshot_manifest_sha256": digest,
        "device_index": 0,
        "dtype": "bfloat16",
        "max_new_tokens": 512,
        "max_prompt_bytes": 256_000,
    }
    values.update(updates)
    return QwenLocalSettings(**values)  # type: ignore[arg-type]


def _runtime_identity(*, device_name: str = "NVIDIA RTX 5090") -> QwenRuntimeIdentity:
    packages = {
        "accelerate": "1.14.0",
        "safetensors": "0.6.2",
        "tokenizers": "0.22.1",
        "torch": "2.11.0",
        "transformers": "4.57.6",
    }
    return QwenRuntimeIdentity(
        python_implementation="CPython",
        python_version="3.10.20",
        python_executable_sha256="1" * 64,
        platform="Linux-x86_64",
        packages=packages,
        package_record_sha256={name: "2" * 64 for name in packages},
        runtime_module_sha256={"torch": "3" * 64, "transformers": "4" * 64},
        torch_cuda_version="12.8",
        device_index=0,
        device_name=device_name,
        compute_capability="12.0",
        hermetic=False,
        dependency_closure_complete=False,
    )


@dataclass
class FakeBackend:
    response: PlannerProviderResponse
    identity_value: QwenRuntimeIdentity = field(default_factory=_runtime_identity)
    drift: bool = False
    calls: int = 0
    identity_reads: int = 0
    prompts: list[bytes] = field(default_factory=list)

    @property
    def runtime_identity(self) -> QwenRuntimeIdentity:
        self.identity_reads += 1
        if self.drift and self.identity_reads > 2:
            return _runtime_identity(device_name="different GPU")
        return self.identity_value

    def generate(
        self,
        *,
        snapshot_path: Path,
        prompt: bytes,
        settings: QwenLocalSettings,
        progress: Callable[[str], None],
    ) -> PlannerProviderResponse:
        self.calls += 1
        self.prompts.append(prompt)
        progress("model_loaded")
        return self.response


def _response(raw: bytes = b'{"action":"stop"}\n') -> PlannerProviderResponse:
    return PlannerProviderResponse(
        raw=raw,
        usage=PlannerUsage(
            input_tokens=100,
            output_tokens=20,
            wall_time_ms=30,
            peak_vram_bytes=7_500_000_000,
            finish_reason="stop",
        ),
    )


def test_provider_binds_verified_snapshot_runtime_and_greedy_configuration(
    tmp_path: Path,
) -> None:
    snapshot, digest = _snapshot(
        tmp_path,
        {
            "config.json": b"config\n",
            "model.safetensors": b"weights\n",
        },
    )
    backend = FakeBackend(_response())
    provider = LocalQwenPlannerProvider(
        settings=_settings(snapshot, digest),
        backend=backend,
    )
    observed: list[str] = []

    identity = provider.identity
    response = provider.invoke(b'{"schema_version":"harness.planner_prompt.v1"}\n', observed.append)

    assert response == backend.response
    assert backend.calls == 1
    assert observed == ["model_loaded"]
    assert identity.provider_id == "qwen_local"
    assert identity.model_revision == _REVISION
    assert identity.model_snapshot_sha256 == digest
    assert identity.network_access is False
    assert identity.inference["do_sample"] is False
    assert identity.inference["temperature"] is None
    assert identity.inference["top_p"] is None
    assert identity.inference["top_k"] is None
    assert identity.inference["repetition_penalty"] == 1.05
    assert identity.inference["local_files_only"] is True
    assert identity.inference["trust_remote_code"] is False
    assert identity.inference["dtype"] == "bfloat16"
    assert len(identity.inference["system_instruction_sha256"]) == 64
    assert identity.inference["runtime"]["device_name"] == "NVIDIA RTX 5090"
    assert identity.inference["snapshot_file_count"] == 2
    assert provider_identity_sha256(provider.identity) == provider_identity_sha256(identity)


def test_snapshot_attestation_matches_preregistered_manifest_algorithm(tmp_path: Path) -> None:
    snapshot, digest = _snapshot(tmp_path, {"a.json": b"a", "b.txt": b"bb"})

    attestation = attest_hf_snapshot(
        snapshot,
        revision=_REVISION,
        expected_manifest_sha256=digest,
    )
    assert attestation == QwenSnapshotAttestation(
        revision=_REVISION,
        manifest_sha256=digest,
        file_count=2,
        total_bytes=3,
    )


def test_snapshot_supports_sha256_lfs_blobs_and_deduplicates_content_verification(
    tmp_path: Path,
) -> None:
    model_root = tmp_path / "models--Qwen--Qwen2.5-VL-3B-Instruct"
    blobs = model_root / "blobs"
    snapshot = model_root / "snapshots" / _REVISION
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    payload = b"one shared LFS blob"
    blob_id = hashlib.sha256(payload).hexdigest()
    (blobs / blob_id).write_bytes(payload)
    manifest = []
    for name in ("model-1.safetensors", "model-2.safetensors"):
        (snapshot / name).symlink_to(Path("..") / ".." / "blobs" / blob_id)
        manifest.append({"path": name, "blob": blob_id, "size_bytes": len(payload)})
    digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    attestation = attest_hf_snapshot(
        snapshot,
        revision=_REVISION,
        expected_manifest_sha256=digest,
    )

    assert attestation.file_count == 2
    assert attestation.total_bytes == 2 * len(payload)


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_revision",
        "wrong_manifest",
        "empty",
        "regular_entry",
        "directory_entry",
        "outside_blob_root",
        "bad_blob_name",
        "blob_content",
    ],
)
def test_snapshot_attestation_fails_closed_on_layout_or_content_attack(
    tmp_path: Path,
    attack: str,
) -> None:
    snapshot, digest = _snapshot(tmp_path)
    revision = _REVISION
    expected = digest
    if attack == "wrong_revision":
        revision = "7" * 40
    elif attack == "wrong_manifest":
        expected = "0" * 64
    elif attack == "empty":
        next(snapshot.iterdir()).unlink()
    elif attack == "regular_entry":
        entry = next(snapshot.iterdir())
        payload = entry.read_bytes()
        entry.unlink()
        entry.write_bytes(payload)
    elif attack == "directory_entry":
        (snapshot / "nested").mkdir()
    elif attack == "outside_blob_root":
        outside = tmp_path / "outside"
        outside.write_bytes(b"outside")
        (snapshot / "outside.bin").symlink_to(outside)
    elif attack == "bad_blob_name":
        blob = next((snapshot.parent.parent / "blobs").iterdir())
        renamed = blob.with_name("not-a-content-id")
        blob.rename(renamed)
        entry = next(snapshot.iterdir())
        entry.unlink()
        entry.symlink_to(Path("..") / ".." / "blobs" / renamed.name)
    else:
        next((snapshot.parent.parent / "blobs").iterdir()).write_bytes(b"tampered")

    with pytest.raises(SnapshotIntegrityError):
        attest_hf_snapshot(
            snapshot,
            revision=revision,
            expected_manifest_sha256=expected,
        )


@pytest.mark.parametrize(
    "attack",
    [
        "missing_root",
        "root_file",
        "root_symlink",
        "blobs_missing",
        "blobs_symlink",
        "broken_entry",
        "entries_unreadable",
        "invalid_revision",
        "invalid_expected_digest",
    ],
)
def test_snapshot_attestation_normalizes_io_and_identity_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    snapshot, digest = _snapshot(tmp_path)
    revision = _REVISION
    expected = digest
    target = snapshot
    if attack == "missing_root":
        target = tmp_path / "missing"
    elif attack == "root_file":
        target = tmp_path / "snapshot-file"
        target.write_bytes(b"not a directory")
    elif attack == "root_symlink":
        target = tmp_path / "snapshot-link"
        target.symlink_to(snapshot, target_is_directory=True)
    elif attack == "blobs_missing":
        entry = next(snapshot.iterdir())
        entry.unlink()
        for blob in (snapshot.parent.parent / "blobs").iterdir():
            blob.unlink()
        (snapshot.parent.parent / "blobs").rmdir()
    elif attack == "blobs_symlink":
        blobs = snapshot.parent.parent / "blobs"
        external = tmp_path / "external-blobs"
        blobs.rename(external)
        blobs.symlink_to(external, target_is_directory=True)
    elif attack == "broken_entry":
        next((snapshot.parent.parent / "blobs").iterdir()).unlink()
    elif attack == "entries_unreadable":
        original = Path.iterdir

        def unreadable(path: Path):
            if path == snapshot:
                raise OSError("injected iterdir failure")
            return original(path)

        monkeypatch.setattr(Path, "iterdir", unreadable)
    elif attack == "invalid_revision":
        revision = "invalid"
    else:
        expected = "INVALID"

    with pytest.raises((SnapshotIntegrityError, ValueError)):
        attest_hf_snapshot(
            target,
            revision=revision,
            expected_manifest_sha256=expected,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"model_id": " padded "},
        {"snapshot_path": "not-a-path"},
        {"revision": "not-a-revision"},
        {"snapshot_manifest_sha256": "BAD"},
        {"device_index": True},
        {"device_index": -1},
        {"max_new_tokens": 0},
        {"max_new_tokens": True},
        {"max_prompt_bytes": 0},
        {"max_prompt_bytes": 1.5},
        {"dtype": "auto"},
    ],
)
def test_settings_reject_noncanonical_or_unbounded_configuration(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    snapshot, digest = _snapshot(tmp_path)
    with pytest.raises((TypeError, ValueError)):
        _settings(snapshot, digest, **updates)


@pytest.mark.parametrize(
    "mutation",
    [
        {"packages": {"torch": "2.11.0"}},
        {"package_record_sha256": {"torch": "2" * 64}},
        {"runtime_module_sha256": {"torch": "3" * 64}},
        {"device_name": " padded "},
        {"hermetic": True},
        {"dependency_closure_complete": True},
    ],
)
def test_runtime_identity_rejects_incomplete_or_overclaimed_facts(
    mutation: dict[str, object],
) -> None:
    payload = _runtime_identity().model_dump(mode="python")
    payload.update(mutation)
    with pytest.raises(ValidationError):
        QwenRuntimeIdentity.model_validate(payload)


@pytest.mark.parametrize("prompt", [bytearray(b"{}"), b"", b"x" * 256_001, b"\xff"])
def test_provider_rejects_invalid_prompt_before_generation(
    tmp_path: Path,
    prompt: object,
) -> None:
    snapshot, digest = _snapshot(tmp_path)
    backend = FakeBackend(_response())
    provider = LocalQwenPlannerProvider(
        settings=_settings(snapshot, digest),
        backend=backend,
    )

    with pytest.raises((TypeError, ValueError)):
        provider.invoke(cast(bytes, prompt), lambda stage: None)

    assert backend.calls == 0


def test_provider_rejects_backend_response_type_and_runtime_drift(tmp_path: Path) -> None:
    snapshot, digest = _snapshot(tmp_path)
    wrong = FakeBackend(cast(PlannerProviderResponse, {"raw": b"{}"}))
    provider = LocalQwenPlannerProvider(settings=_settings(snapshot, digest), backend=wrong)
    with pytest.raises(TypeError, match="PlannerProviderResponse"):
        provider.invoke(b"{}", lambda stage: None)

    drifting = FakeBackend(_response(), drift=True)
    provider = LocalQwenPlannerProvider(settings=_settings(snapshot, digest), backend=drifting)
    with pytest.raises(RuntimeError, match="runtime identity changed"):
        provider.invoke(b"{}", lambda stage: None)


@pytest.mark.parametrize("attack", ["blob", "symlink", "implementation"])
def test_provider_detects_snapshot_or_implementation_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    snapshot, digest = _snapshot(tmp_path)
    backend = FakeBackend(_response())
    provider = LocalQwenPlannerProvider(settings=_settings(snapshot, digest), backend=backend)
    if attack == "blob":
        next((snapshot.parent.parent / "blobs").iterdir()).write_bytes(b"changed")
    elif attack == "symlink":
        entry = next(snapshot.iterdir())
        target = os.readlink(entry)
        entry.unlink()
        entry.symlink_to(target)
    else:
        monkeypatch.setattr(provider, "_implementation_sha256", "0" * 64)

    with pytest.raises(SnapshotIntegrityError):
        _ = provider.identity


def test_runtime_device_must_match_qualified_settings(tmp_path: Path) -> None:
    snapshot, digest = _snapshot(tmp_path)
    payload = _runtime_identity().model_dump(mode="python")
    payload["device_index"] = 1
    backend = FakeBackend(_response(), identity_value=QwenRuntimeIdentity.model_validate(payload))

    with pytest.raises(ValueError, match="device index"):
        LocalQwenPlannerProvider(settings=_settings(snapshot, digest), backend=backend)


def test_provider_rejects_a_model_label_that_does_not_match_the_snapshot_root(
    tmp_path: Path,
) -> None:
    snapshot, digest = _snapshot(tmp_path)

    with pytest.raises(SnapshotIntegrityError, match="model_id"):
        LocalQwenPlannerProvider(
            settings=_settings(snapshot, digest, model_id="Qwen/Another-Model"),
            backend=FakeBackend(_response()),
        )


class _FakeTensor:
    def __init__(self, values: list[int]) -> None:
        self.values = values
        self.shape = (1, len(values))

    def __getitem__(self, value: slice) -> "_FakeTensor":
        return _FakeTensor(self.values[value])


class _FakeBatch(dict[str, object]):
    def __init__(self) -> None:
        self.input_ids = _FakeTensor([10, 11, 12])
        super().__init__(input_ids=self.input_ids)
        self.device: str | None = None

    def to(self, device: str) -> "_FakeBatch":
        self.device = device
        return self


class _FakeModel:
    load_calls: list[dict[str, object]] = []
    generate_calls: list[dict[str, object]] = []

    @classmethod
    def from_pretrained(cls, path: str, **kwargs: object) -> "_FakeModel":
        cls.load_calls.append({"path": path, **kwargs})
        return cls()

    def eval(self) -> None:
        self.evaluated = True

    def generate(self, **kwargs: object) -> list[_FakeTensor]:
        type(self).generate_calls.append(kwargs)
        return [_FakeTensor([10, 11, 12, 20, 21])]


class _FakeProcessor:
    load_calls: list[dict[str, object]] = []
    messages_seen: list[object] = []

    @classmethod
    def from_pretrained(cls, path: str, **kwargs: object) -> "_FakeProcessor":
        cls.load_calls.append({"path": path, **kwargs})
        return cls()

    def apply_chat_template(self, messages: object, **kwargs: object) -> str:
        self.messages = messages
        type(self).messages_seen.append(messages)
        return "templated"

    def __call__(self, **kwargs: object) -> _FakeBatch:
        self.inputs = kwargs
        return _FakeBatch()

    def batch_decode(self, outputs: object, **kwargs: object) -> list[str]:
        self.decoded = (outputs, kwargs)
        return ['{"schema_version":"harness.planner_decision.v1"}']


def _install_fake_qwen_modules(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cuda_available: bool = True,
    device_count: int = 1,
) -> tuple[types.ModuleType, types.ModuleType]:
    torch_file = tmp_path / "torch.py"
    transformers_file = tmp_path / "transformers.py"
    torch_file.write_text("# fake torch\n", encoding="utf-8")
    transformers_file.write_text("# fake transformers\n", encoding="utf-8")
    torch = types.ModuleType("torch")
    torch.__file__ = str(torch_file)
    torch.bfloat16 = "bf16"
    torch.float16 = "fp16"
    torch.version = SimpleNamespace(cuda="12.8")
    torch.backends = SimpleNamespace(
        cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=True)),
        cudnn=SimpleNamespace(allow_tf32=True, benchmark=True),
    )
    torch.use_deterministic_algorithms = lambda enabled: None

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return cuda_available

        @staticmethod
        def device_count() -> int:
            return device_count

        @staticmethod
        def get_device_capability(index: int) -> tuple[int, int]:
            return (12, 0)

        @staticmethod
        def get_device_name(index: int) -> str:
            return "Fake RTX 5090"

        @staticmethod
        def reset_peak_memory_stats(index: int) -> None:
            return None

        @staticmethod
        def synchronize(index: int) -> None:
            return None

        @staticmethod
        def max_memory_allocated(index: int) -> int:
            return 1234

    torch.cuda = FakeCuda

    class InferenceMode:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: object) -> None:
            return None

    torch.inference_mode = InferenceMode
    transformers = types.ModuleType("transformers")
    transformers.__file__ = str(transformers_file)
    transformers.AutoProcessor = _FakeProcessor
    transformers.Qwen2_5_VLForConditionalGeneration = _FakeModel
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    return torch, transformers


def test_transformers_runtime_probe_binds_packages_interpreter_modules_and_gpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_qwen_modules(monkeypatch, tmp_path)

    class FakeDistribution:
        def __init__(self, name: str) -> None:
            self.version = f"{name}-version"
            self.name = name

        def read_text(self, filename: str) -> str:
            assert filename == "RECORD"
            return self.name

    monkeypatch.setattr(
        importlib.metadata,
        "distribution",
        lambda name: FakeDistribution(name),
    )

    identity = TransformersQwenBackend(device_index=0).runtime_identity

    assert identity.device_name == "Fake RTX 5090"
    assert identity.compute_capability == "12.0"
    assert identity.torch_cuda_version == "12.8"
    assert identity.packages["transformers"] == "transformers-version"
    assert identity.runtime_module_sha256["torch"] == hashlib.sha256(b"# fake torch\n").hexdigest()


@pytest.mark.parametrize("attack", ["cuda_missing", "device_missing"])
def test_transformers_runtime_probe_fails_closed_without_qualified_gpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    _install_fake_qwen_modules(
        monkeypatch,
        tmp_path,
        cuda_available=attack != "cuda_missing",
        device_count=0 if attack == "device_missing" else 1,
    )
    with pytest.raises(RuntimeError):
        _ = TransformersQwenBackend(device_index=0).runtime_identity


@pytest.mark.parametrize("attack", ["missing_distribution", "missing_record"])
def test_transformers_runtime_probe_fails_closed_on_distribution_identity_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    _install_fake_qwen_modules(monkeypatch, tmp_path)

    class FakeDistribution:
        version = "1.0"

        def read_text(self, name: str) -> str | None:
            return None if attack == "missing_record" else "record"

    def distribution(name: str):
        if attack == "missing_distribution":
            raise importlib.metadata.PackageNotFoundError(name)
        return FakeDistribution()

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)
    with pytest.raises(RuntimeError):
        _ = TransformersQwenBackend(device_index=0).runtime_identity


@pytest.mark.parametrize(
    "dtype,max_new_tokens,expected_finish",
    [("bfloat16", 2, "length"), ("float16", 3, "stop")],
)
def test_transformers_backend_runs_fixed_text_only_greedy_contract_and_reuses_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dtype: str,
    max_new_tokens: int,
    expected_finish: str,
) -> None:
    torch, _ = _install_fake_qwen_modules(monkeypatch, tmp_path)
    _FakeModel.load_calls.clear()
    _FakeModel.generate_calls.clear()
    _FakeProcessor.load_calls.clear()
    _FakeProcessor.messages_seen.clear()
    snapshot, digest = _snapshot(tmp_path / "model")
    settings = _settings(
        snapshot,
        digest,
        dtype=dtype,
        max_new_tokens=max_new_tokens,
    )
    backend = TransformersQwenBackend(device_index=0)
    stages: list[str] = []

    first = backend.generate(
        snapshot_path=snapshot,
        prompt=b'{"prompt":1}',
        settings=settings,
        progress=stages.append,
    )
    second = backend.generate(
        snapshot_path=snapshot,
        prompt=b'{"prompt":2}',
        settings=settings,
        progress=stages.append,
    )
    conflicting = _settings(
        snapshot,
        digest,
        dtype="float16" if dtype == "bfloat16" else "bfloat16",
        max_new_tokens=max_new_tokens,
    )
    with pytest.raises(RuntimeError, match="different model runtime"):
        backend.generate(
            snapshot_path=snapshot,
            prompt=b'{"prompt":3}',
            settings=conflicting,
            progress=stages.append,
        )

    assert first.raw.startswith(b"{")
    assert first.usage.input_tokens == 3
    assert first.usage.output_tokens == 2
    assert first.usage.peak_vram_bytes == 1234
    assert first.usage.finish_reason == expected_finish
    assert second.usage.finish_reason == expected_finish
    assert len(_FakeModel.load_calls) == 1
    assert len(_FakeModel.generate_calls) == 2
    assert len(_FakeProcessor.load_calls) == 1
    assert _FakeModel.load_calls[0]["local_files_only"] is True
    assert _FakeModel.load_calls[0]["trust_remote_code"] is False
    assert _FakeProcessor.load_calls[0]["use_fast"] is False
    assert _FakeModel.generate_calls[0]["do_sample"] is False
    assert _FakeModel.generate_calls[0]["temperature"] is None
    assert _FakeModel.generate_calls[0]["top_p"] is None
    assert _FakeModel.generate_calls[0]["top_k"] is None
    assert _FakeModel.generate_calls[0]["repetition_penalty"] == 1.05
    assert _FakeProcessor.messages_seen[0][0]["role"] == "system"
    system_instruction = _FakeProcessor.messages_seen[0][0]["content"][0]["text"]
    assert "one minified JSON object" in system_instruction
    assert "observation_keys" in system_instruction
    assert "stop_reason" in system_instruction
    assert "parameters must contain only" in system_instruction
    assert torch.backends.cuda.matmul.allow_tf32 is False
    assert torch.backends.cudnn.allow_tf32 is False
    assert torch.backends.cudnn.benchmark is False
    assert stages.count("model_loading") == 1
    assert stages[-1] == "response_decoded"
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_provider_default_backend_factory_is_used_when_not_injected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, digest = _snapshot(tmp_path)
    backend = FakeBackend(_response())
    monkeypatch.setattr(qwen_module, "TransformersQwenBackend", lambda **kwargs: backend)

    provider = LocalQwenPlannerProvider(settings=_settings(snapshot, digest))

    assert provider.identity.inference["runtime"]["device_name"] == "NVIDIA RTX 5090"
