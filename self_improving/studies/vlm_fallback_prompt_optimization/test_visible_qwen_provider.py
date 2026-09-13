from __future__ import annotations

import hashlib
import os
import types
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from self_improving.studies.vlm_fallback_prompt_optimization import (
    model_content,
    protocol,
)
from self_improving.studies.vlm_fallback_prompt_optimization import (
    visible_qwen_provider as candidate,
)
from self_improving.studies.vlm_fallback_prompt_optimization.runner import (
    ArtifactBinding,
    ModelBinding,
    ResourceUsage,
    VisibleProviderRequest,
)
from self_improving.studies.vlm_fallback_prompt_optimization.visible_qwen_provider import (
    BoundVisibleImage,
    LocalQwenVisibleProvider,
    QualifiedVisibleModel,
    QwenRuntimeFingerprint,
    QwenTransformersRuntime,
    QwenVisibleBackendIdentity,
    QwenVisibleGeneration,
    QwenVisibleGenerationRequest,
    TransformersQwenVisibleBackend,
    VisibleProviderIntegrityError,
)

_VISIBLE_CHECKS = (
    "object_presence",
    "object_identity",
    "object_count",
    "orientation",
    "table_contact",
    "visible_penetration_or_floating",
    "spatial_relation",
    "overall_prompt_match",
)
_UNSET = object()


@pytest.fixture(autouse=True)
def _offline_transformers_environment():
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("HF_HUB_OFFLINE", "1")
        environment.setenv("TRANSFORMERS_OFFLINE", "1")
        yield


def _typed_response(
    *,
    status: str = "pass",
    overall: str | None = None,
) -> dict:
    checks = {name: "pass" for name in _VISIBLE_CHECKS}
    checks[_VISIBLE_CHECKS[0]] = status
    return {
        "schema_version": "vlm_fallback.visible_review.v1",
        "checks": checks,
        "overall": overall or ("review_required" if status == "abstain" else status),
        "corrections": [],
    }


class _RecordingBackend:
    def __init__(
        self,
        response: dict | None = None,
        *,
        raw_response: bytes | str | None = None,
        resource: Any = None,
        returned: Any = _UNSET,
        on_generate=None,
    ) -> None:
        self.identity: Any = QwenVisibleBackendIdentity(
            backend_id="fixture-qwen-backend",
            revision="fixture-v1",
            implementation_sha256="b" * 64,
            network_access=False,
        )
        self.response = response
        self.raw_response = raw_response
        self.resource = resource
        self.returned = returned
        self.on_generate = on_generate
        self.requests: list[QwenVisibleGenerationRequest] = []

    def generate(self, request, progress):
        self.requests.append(request)
        if self.on_generate is not None:
            self.on_generate(request)
        progress("fixture_backend_generated")
        if self.returned is not _UNSET:
            return self.returned
        raw_response = self.raw_response
        if raw_response is None:
            assert self.response is not None
            raw_response = protocol.canonical_json_bytes(self.response)
        return QwenVisibleGeneration(
            raw_response=raw_response,  # type: ignore[arg-type]
            resource=self.resource
            or ResourceUsage(
                gpu_time_ms=7,
                peak_vram_mib=13,
                input_tokens=17,
                output_tokens=5,
                visible_vlm_invocations=1,
            ),
        )


def _qualified_model(
    tmp_path: Path,
    *,
    arm: str = "A1_typed_abstaining_critic_3b",
    model_role: str = "primary_local_vlm",
    revision_character: str = "a",
) -> QualifiedVisibleModel:
    revision = revision_character * 40
    repository = tmp_path / "models--Qwen--fixture"
    blobs = repository / "blobs"
    snapshot = repository / "snapshots" / revision
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    payload = b'{"model_type":"fixture"}\n'
    blob_id = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
    (blobs / blob_id).write_bytes(payload)
    (snapshot / "config.json").symlink_to(blobs / blob_id)
    manifest = model_content.build_model_content_manifest(
        snapshot,
        model_id="Qwen/fixture",
        revision=revision,
    )
    manifest_path = tmp_path / f"model_content_manifest_{arm}.json"
    manifest_path.write_bytes(model_content.canonical_manifest_bytes(manifest))
    baseline = model_content.verify_model_content(snapshot, manifest)
    return QualifiedVisibleModel(
        arm=arm,
        model_role=model_role,
        model_id="Qwen/fixture",
        revision=revision,
        snapshot_manifest_sha256=protocol.canonical_sha256(
            protocol.build_hf_snapshot_manifest(snapshot)
        ),
        model_content_manifest_path=manifest_path,
        model_content_manifest_sha256=model_content.manifest_sha256(manifest),
        model_roster_sha256=baseline.roster_sha256,
        local_snapshot_path=snapshot,
        max_new_tokens=768,
    )


def _visible_request(tmp_path: Path, model: QualifiedVisibleModel) -> VisibleProviderRequest:
    image_path = tmp_path / "inputs" / "observer.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3, 2), color=(12, 34, 56)).save(image_path)
    image_bytes = image_path.read_bytes()
    return VisibleProviderRequest(
        invocation_id="invocation-001",
        case_id="case-001",
        arm=model.arm,
        task_context="Place the apple on the plate.",
        prompt="Review the rendered scene using the frozen visible checks.",
        prompt_template_version="visible-review-v1",
        processor_config={"min_pixels": 200704, "max_pixels": 802816},
        seed=31,
        attempt=1,
        repair_index=0,
        repair_of=None,
        resolved_scene_sha256="c" * 64,
        model=ModelBinding(
            role=model.model_role,
            model_id=model.model_id,
            revision=model.revision,
            snapshot_manifest_sha256=model.snapshot_manifest_sha256,
            model_content_manifest_sha256=model.model_content_manifest_sha256,
            model_roster_sha256=model.model_roster_sha256,
            local_snapshot_path=model.local_snapshot_path,
            max_new_tokens=model.max_new_tokens,
        ),
        image_paths=(image_path,),
        artifacts=(
            ArtifactBinding(
                path="inputs/observer.png",
                sha256=hashlib.sha256(image_bytes).hexdigest(),
                size_bytes=len(image_bytes),
            ),
            ArtifactBinding(
                path="inputs/metadata.json",
                sha256="0" * 64,
                size_bytes=0,
            ),
        ),
        resource_reservation=ResourceUsage(visible_vlm_invocations=1),
    )


@pytest.mark.parametrize(
    ("arm", "model_role", "revision_character"),
    [
        ("A1_typed_abstaining_critic_3b", "primary_local_vlm", "a"),
        ("A2_typed_abstaining_critic_7b", "confirmatory_model_size_ceiling", "b"),
    ],
)
def test_typed_visible_review_traces_runner_shaped_a1_a2_requests(
    tmp_path: Path,
    arm: str,
    model_role: str,
    revision_character: str,
) -> None:
    model = _qualified_model(
        tmp_path,
        arm=arm,
        model_role=model_role,
        revision_character=revision_character,
    )
    response = _typed_response()
    backend = _RecordingBackend(response)
    provider = LocalQwenVisibleProvider(models=(model,), backend=backend)
    request = _visible_request(tmp_path, model)
    progress: list[str] = []

    outcome = provider.invoke(request, progress.append)

    assert outcome.decision == "visible_review_complete"
    assert outcome.result == response
    assert outcome.parsed_response == response
    assert outcome.abstained is False
    assert outcome.claims_physical_pass is False
    assert outcome.resource.visible_vlm_invocations == 1
    assert provider.identity.kind == "local_qwen"
    assert provider.identity.production_eligible is False
    assert progress == ["fixture_backend_generated"]
    assert len(backend.requests) == 1
    generation_request = backend.requests[0]
    assert generation_request.arm == arm
    assert generation_request.model == model
    assert generation_request.model.model_role == model_role
    assert generation_request.prompt == request.prompt
    assert generation_request.processor_config == request.processor_config
    assert generation_request.images[0].path == "inputs/observer.png"
    assert generation_request.images[0].content == request.image_paths[0].read_bytes()
    assert generation_request.images[0].content_sha256 == request.artifacts[0].sha256


def test_injected_provider_backend_does_not_require_transformers_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE")
    model = _qualified_model(tmp_path)
    backend = _RecordingBackend(_typed_response())
    provider = LocalQwenVisibleProvider(models=(model,), backend=backend)

    outcome = provider.invoke(_visible_request(tmp_path, model), lambda _stage: None)

    assert outcome.decision == "visible_review_complete"
    assert len(backend.requests) == 1


def test_a0_is_rejected_before_the_typed_backend(tmp_path: Path) -> None:
    model = _qualified_model(tmp_path)
    backend = _RecordingBackend(_typed_response())
    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=backend,
    )
    request = replace(_visible_request(tmp_path, model), arm="A0_current_critic_3b")

    with pytest.raises(
        candidate.UnsupportedVisibleArmError,
        match="A0_current_critic_3b requires the independent frozen baseline provider",
    ):
        provider.invoke(request, lambda _stage: None)

    assert backend.requests == []


@pytest.mark.parametrize(
    "raw_response",
    [
        b"not-json",
        b"\xff",
        b"[]",
        b'{"duplicate":1,"duplicate":2}',
        b'{"nonfinite":NaN}',
        b"```json\n{}\n```",
        b"[" * 1100 + b"0" + b"]" * 1100,
    ],
)
def test_invalid_response_format_preserves_raw_bytes_and_known_usage(
    tmp_path: Path,
    raw_response: bytes,
) -> None:
    model = _qualified_model(tmp_path)
    usage = ResourceUsage(
        gpu_time_ms=3,
        peak_vram_mib=4,
        input_tokens=5,
        output_tokens=6,
        visible_vlm_invocations=1,
    )
    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(raw_response=raw_response, resource=usage),
    )

    outcome = provider.invoke(_visible_request(tmp_path, model), lambda _stage: None)

    assert outcome.decision == "visible_review_format_invalid"
    assert outcome.result == {}
    assert outcome.parsed_response is None
    assert outcome.raw_response == raw_response
    assert outcome.resource == usage
    assert outcome.abstained is True
    assert outcome.claims_physical_pass is False


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_typed_response(status="abstain"), True),
        (_typed_response(overall="review_required"), True),
        ({"checks": [], "overall": "pass"}, False),
    ],
)
def test_typed_abstention_is_derived_without_claiming_physical_authority(
    tmp_path: Path,
    response: dict,
    expected: bool,
) -> None:
    model = _qualified_model(tmp_path)
    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(response),
    )

    outcome = provider.invoke(_visible_request(tmp_path, model), lambda _stage: None)

    assert outcome.abstained is expected
    assert outcome.result == response
    assert outcome.claims_physical_pass is False


@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"arm": None}, "non-empty"),
        ({"arm": "unknown"}, "supported typed visible-study arm"),
        ({"arm": "A0_current_critic_3b"}, "supported typed visible-study arm"),
        ({"model_role": None}, "non-empty"),
        ({"model_role": "confirmatory_model_size_ceiling"}, "does not match"),
        ({"model_id": ""}, "non-empty"),
        ({"revision": "A" * 40}, "lowercase hex"),
        ({"revision": "g" * 40}, "lowercase hex"),
        ({"snapshot_manifest_sha256": "a" * 63}, "lowercase hex"),
        ({"model_content_manifest_path": Path("relative.json")}, "absolute Path"),
        ({"model_content_manifest_sha256": "z" * 64}, "lowercase hex"),
        ({"model_roster_sha256": "B" * 64}, "lowercase hex"),
        ({"local_snapshot_path": "/tmp/not-a-path"}, "absolute Path"),
        ({"local_snapshot_path": Path("/wrong/snapshots/revision")}, "does not match"),
        ({"max_new_tokens": True}, "positive built-in integer"),
        ({"max_new_tokens": 0}, "positive built-in integer"),
        ({"max_new_tokens": 1}, "frozen study"),
    ],
)
def test_qualified_model_rejects_ambiguous_identity_fields(
    tmp_path: Path,
    updates: dict,
    match: str,
) -> None:
    model = _qualified_model(tmp_path)

    with pytest.raises(ValueError, match=match):
        replace(model, **updates)


@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"backend_id": ""}, "non-empty"),
        ({"revision": 1}, "non-empty"),
        ({"implementation_sha256": "F" * 64}, "lowercase hex"),
        ({"implementation_sha256": "q" * 64}, "lowercase hex"),
        ({"network_access": 0}, "built-in boolean"),
        ({"network_access": True}, "forbid network"),
    ],
)
def test_backend_identity_is_exact_and_offline(updates: dict, match: str) -> None:
    values = {
        "backend_id": "fixture",
        "revision": "v1",
        "implementation_sha256": "a" * 64,
        "network_access": False,
    }
    values.update(updates)

    with pytest.raises(ValueError, match=match):
        QwenVisibleBackendIdentity(**values)


def test_runtime_fingerprint_cannot_claim_complete_dependency_closure() -> None:
    fingerprint = candidate.QwenRuntimeFingerprint(
        fingerprint_id="transformers-qwen2.5-vl-selected-runtime-facts",
        version_facts_sha256="1" * 64,
        entrypoint_facts_sha256="2" * 64,
        device_facts_sha256="3" * 64,
        dependency_closure_complete=False,
    )

    assert fingerprint.dependency_closure_complete is False
    with pytest.raises(ValueError, match="built-in boolean"):
        replace(fingerprint, dependency_closure_complete=0)
    with pytest.raises(ValueError, match="not a complete dependency closure"):
        replace(fingerprint, dependency_closure_complete=True)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HF_HUB_OFFLINE", None),
        ("HF_HUB_OFFLINE", "0"),
        ("TRANSFORMERS_OFFLINE", None),
        ("TRANSFORMERS_OFFLINE", "0"),
    ],
)
def test_concrete_backend_requires_exact_offline_environment_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    imported: list[str] = []
    monkeypatch.setattr(
        candidate.importlib,
        "import_module",
        lambda module_name: imported.append(module_name),
    )

    with pytest.raises(
        RuntimeError,
        match="HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1",
    ):
        TransformersQwenVisibleBackend()

    assert imported == []


@pytest.mark.parametrize(
    ("processor_config", "match"),
    [
        (None, "contain exactly"),
        ({"min_pixels": 200704}, "contain exactly"),
        ({"min_pixels": True, "max_pixels": 802816}, "positive built-in integer"),
        ({"min_pixels": 200704, "max_pixels": 0}, "positive built-in integer"),
        ({"min_pixels": 900000, "max_pixels": 800000}, "must not exceed"),
        ({"min_pixels": 200704, "max_pixels": 900000}, "frozen study"),
    ],
)
def test_provider_rejects_non_frozen_processor_configuration(
    tmp_path: Path,
    processor_config: Any,
    match: str,
) -> None:
    model = _qualified_model(tmp_path)

    with pytest.raises(ValueError, match=match):
        LocalQwenVisibleProvider(
            models=(model,),
            backend=_RecordingBackend(_typed_response()),
            processor_config=processor_config,
        )


def test_provider_constructor_rejects_unqualified_model_collections(tmp_path: Path) -> None:
    model = _qualified_model(tmp_path)
    backend = _RecordingBackend(_typed_response())

    for models in ([], (), (object(),), (model, model)):
        with pytest.raises(ValueError):
            LocalQwenVisibleProvider(models=models, backend=backend)  # type: ignore[arg-type]


def test_provider_constructor_rejects_unverified_roster_and_backend_identity(
    tmp_path: Path,
) -> None:
    model = _qualified_model(tmp_path)
    with pytest.raises(VisibleProviderIntegrityError, match="roster"):
        LocalQwenVisibleProvider(
            models=(replace(model, model_roster_sha256="d" * 64),),
            backend=_RecordingBackend(_typed_response()),
        )

    with pytest.raises(VisibleProviderIntegrityError, match="snapshot manifest"):
        LocalQwenVisibleProvider(
            models=(replace(model, snapshot_manifest_sha256="e" * 64),),
            backend=_RecordingBackend(_typed_response()),
        )

    backend = _RecordingBackend(_typed_response())
    backend.identity = object()
    with pytest.raises(VisibleProviderIntegrityError, match="backend identity"):
        LocalQwenVisibleProvider(models=(model,), backend=backend)


def test_provider_identity_binds_models_independent_of_configuration_order(
    tmp_path: Path,
) -> None:
    first = _qualified_model(tmp_path / "first")
    second = _qualified_model(
        tmp_path / "second",
        arm="A2_typed_abstaining_critic_7b",
        model_role="confirmatory_model_size_ceiling",
        revision_character="b",
    )
    backend = _RecordingBackend(_typed_response())

    one = LocalQwenVisibleProvider(models=(first, second), backend=backend)
    two = LocalQwenVisibleProvider(models=(second, first), backend=backend)

    assert one.identity == two.identity


def test_provider_detects_source_and_backend_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _qualified_model(tmp_path)
    backend = _RecordingBackend(_typed_response())
    provider = LocalQwenVisibleProvider(models=(model,), backend=backend)
    original = provider.identity

    backend.identity = replace(backend.identity, revision="fixture-v2")
    with pytest.raises(VisibleProviderIntegrityError, match="backend identity changed"):
        _ = provider.identity
    backend.identity = replace(backend.identity, revision="fixture-v1")
    assert provider.identity == original

    backend.identity = object()
    with pytest.raises(VisibleProviderIntegrityError, match="backend identity must use"):
        _ = provider.identity
    backend.identity = QwenVisibleBackendIdentity(
        backend_id="fixture-qwen-backend",
        revision="fixture-v1",
        implementation_sha256="b" * 64,
        network_access=False,
    )
    monkeypatch.setattr(candidate, "_source_sha256", lambda: "0" * 64)
    with pytest.raises(VisibleProviderIntegrityError, match="source changed"):
        _ = provider.identity


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "A2_typed_abstaining_critic_7b"),
        ("model_id", "Qwen/different"),
        ("revision", "b" * 40),
        ("snapshot_manifest_sha256", "d" * 64),
        ("model_content_manifest_sha256", "e" * 64),
        ("model_roster_sha256", None),
        ("local_snapshot_path", Path("/different/snapshot")),
        ("max_new_tokens", 767),
    ],
)
def test_invocation_rejects_each_model_identity_mismatch(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    model = _qualified_model(tmp_path)
    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(_typed_response()),
    )
    request = _visible_request(tmp_path, model)

    with pytest.raises(VisibleProviderIntegrityError, match="model binding"):
        provider.invoke(
            replace(request, model=replace(request.model, **{field: value})),
            lambda _stage: None,
        )


def test_invocation_rejects_wrong_public_types_and_unknown_arm(tmp_path: Path) -> None:
    model = _qualified_model(tmp_path)
    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(_typed_response()),
    )
    request = _visible_request(tmp_path, model)

    with pytest.raises(ValueError, match="VisibleProviderRequest"):
        provider.invoke(object(), lambda _stage: None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="progress"):
        provider.invoke(request, None)  # type: ignore[arg-type]
    with pytest.raises(VisibleProviderIntegrityError, match="no qualified"):
        provider.invoke(replace(request, arm="unknown"), lambda _stage: None)
    with pytest.raises(ValueError, match="ModelBinding"):
        provider.invoke(replace(request, model=object()), lambda _stage: None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="resource_reservation"):
        provider.invoke(
            replace(request, resource_reservation=object()),  # type: ignore[arg-type]
            lambda _stage: None,
        )
    for seed in (True, -1):
        with pytest.raises(ValueError, match="seed"):
            provider.invoke(replace(request, seed=seed), lambda _stage: None)


def test_invocation_rechecks_the_frozen_processor_configuration(tmp_path: Path) -> None:
    model = _qualified_model(tmp_path)
    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(_typed_response()),
    )
    request = _visible_request(tmp_path, model)

    with pytest.raises(ValueError, match="frozen study"):
        provider.invoke(
            replace(
                request,
                processor_config={"min_pixels": 200704, "max_pixels": 900000},
            ),
            lambda _stage: None,
        )


@pytest.mark.parametrize("field", ["prompt", "task_context"])
def test_invocation_requires_nonempty_utf8_prompt_content(
    tmp_path: Path,
    field: str,
) -> None:
    provider, request, _ = _provider_request_backend(tmp_path)
    for value in (None, ""):
        with pytest.raises(ValueError, match="non-empty"):
            provider.invoke(
                replace(request, **{field: value}),  # type: ignore[arg-type]
                lambda _stage: None,
            )
    with pytest.raises(ValueError, match="valid UTF-8"):
        provider.invoke(
            replace(request, **{field: "\ud800"}),
            lambda _stage: None,
        )


@pytest.mark.parametrize(
    ("field", "limit_name"),
    [("prompt", "_MAX_PROMPT_BYTES"), ("task_context", "_MAX_TASK_CONTEXT_BYTES")],
)
def test_invocation_bounds_prompt_content_before_backend_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    limit_name: str,
) -> None:
    provider, request, backend = _provider_request_backend(tmp_path)
    monkeypatch.setattr(candidate, limit_name, 3)

    with pytest.raises(ValueError, match="byte limit"):
        provider.invoke(replace(request, **{field: "four"}), lambda _stage: None)
    assert backend.requests == []


def _provider_request_backend(tmp_path: Path):
    model = _qualified_model(tmp_path)
    backend = _RecordingBackend(_typed_response())
    provider = LocalQwenVisibleProvider(models=(model,), backend=backend)
    return provider, _visible_request(tmp_path, model), backend


def test_images_are_bound_in_artifact_order_with_real_formats(tmp_path: Path) -> None:
    provider, request, backend = _provider_request_backend(tmp_path)
    second_path = tmp_path / "inputs" / "detail.jpg"
    Image.new("RGB", (2, 3), color=(90, 80, 70)).save(second_path, format="JPEG")
    second_bytes = second_path.read_bytes()
    second = ArtifactBinding(
        path="inputs/detail.jpg",
        sha256=hashlib.sha256(second_bytes).hexdigest(),
        size_bytes=len(second_bytes),
    )
    request = replace(
        request,
        image_paths=(request.image_paths[0], second_path),
        artifacts=(request.artifacts[0], request.artifacts[1], second),
    )

    provider.invoke(request, lambda _stage: None)

    assert [image.path for image in backend.requests[0].images] == [
        "inputs/observer.png",
        "inputs/detail.jpg",
    ]


def test_image_collection_types_counts_and_size_limits_fail_before_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, request, _ = _provider_request_backend(tmp_path)
    with pytest.raises(ValueError, match="exact tuples"):
        provider.invoke(
            replace(request, image_paths=list(request.image_paths)),  # type: ignore[arg-type]
            lambda _stage: None,
        )
    with pytest.raises(ValueError, match="ArtifactBinding"):
        provider.invoke(
            replace(request, artifacts=(object(),)),  # type: ignore[arg-type]
            lambda _stage: None,
        )
    with pytest.raises(ValueError, match="match every image"):
        provider.invoke(
            replace(request, image_paths=(), artifacts=(request.artifacts[1],)),
            lambda _stage: None,
        )
    with pytest.raises(ValueError, match="match every image"):
        provider.invoke(replace(request, image_paths=()), lambda _stage: None)
    with pytest.raises(ValueError, match="must be unique"):
        provider.invoke(
            replace(
                request,
                image_paths=(request.image_paths[0], request.image_paths[0]),
                artifacts=(request.artifacts[0], request.artifacts[0]),
            ),
            lambda _stage: None,
        )

    repeated = tuple(
        ArtifactBinding(path=f"inputs/{index}.png", sha256="0" * 64, size_bytes=1)
        for index in range(7)
    )
    with pytest.raises(ValueError, match="image count"):
        provider.invoke(
            replace(
                request,
                image_paths=tuple(tmp_path / item.path for item in repeated),
                artifacts=repeated,
            ),
            lambda _stage: None,
        )

    image_size = request.artifacts[0].size_bytes
    monkeypatch.setattr(candidate, "_MAX_IMAGE_BYTES", image_size - 1)
    with pytest.raises(ValueError, match="per-image"):
        provider.invoke(request, lambda _stage: None)
    monkeypatch.setattr(candidate, "_MAX_IMAGE_BYTES", 32 * 1024 * 1024)
    monkeypatch.setattr(candidate, "_MAX_TOTAL_IMAGE_BYTES", image_size - 1)
    with pytest.raises(ValueError, match="aggregate"):
        provider.invoke(request, lambda _stage: None)


@pytest.mark.parametrize(
    "path",
    [
        None,
        "",
        "inputs\\observer.png",
        "inputs/observer\0.png",
        "inputs/e\u0301.png",
        "/absolute.png",
        "C:/absolute.png",
        "C:drive.png",
        "../escape.png",
        ".",
        "inputs//observer.png",
    ],
)
def test_image_artifact_paths_must_be_canonical_relative_posix(
    tmp_path: Path,
    path: Any,
) -> None:
    provider, request, _ = _provider_request_backend(tmp_path)
    artifact = replace(request.artifacts[0], path=path)

    with pytest.raises(ValueError, match="canonical relative POSIX path"):
        provider.invoke(
            replace(request, artifacts=(artifact, request.artifacts[1])),
            lambda _stage: None,
        )


def test_image_path_and_receipt_scalar_types_are_exact(tmp_path: Path) -> None:
    provider, request, _ = _provider_request_backend(tmp_path)
    image = request.image_paths[0]
    artifact = request.artifacts[0]

    with pytest.raises(ValueError, match="absolute Path"):
        provider.invoke(
            replace(request, image_paths=(Path("inputs/observer.png"),)),
            lambda _stage: None,
        )
    with pytest.raises(ValueError, match="does not match"):
        provider.invoke(
            replace(request, image_paths=(image.with_name("different.png"),)),
            lambda _stage: None,
        )
    with pytest.raises(ValueError, match="lowercase hex"):
        provider.invoke(
            replace(
                request,
                artifacts=(replace(artifact, sha256="g" * 64), request.artifacts[1]),
            ),
            lambda _stage: None,
        )
    for size in (True, -1, "1"):
        with pytest.raises(ValueError, match="non-negative built-in integer"):
            provider.invoke(
                replace(
                    request,
                    artifacts=(replace(artifact, size_bytes=size), request.artifacts[1]),
                ),
                lambda _stage: None,
            )


def test_missing_directory_and_unstable_images_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, request, _ = _provider_request_backend(tmp_path)
    missing = request.image_paths[0].with_name("missing.png")
    missing_artifact = replace(request.artifacts[0], path="inputs/missing.png")
    with pytest.raises(ValueError, match="readable regular file"):
        provider.invoke(
            replace(request, image_paths=(missing,), artifacts=(missing_artifact,)),
            lambda _stage: None,
        )

    directory = tmp_path / "inputs" / "directory.png"
    directory.mkdir()
    directory_artifact = ArtifactBinding(
        path="inputs/directory.png",
        sha256="0" * 64,
        size_bytes=0,
    )
    with pytest.raises(ValueError, match="not a regular file"):
        provider.invoke(
            replace(request, image_paths=(directory,), artifacts=(directory_artifact,)),
            lambda _stage: None,
        )

    calls = 0

    def drifting_fingerprint(_metadata):
        nonlocal calls
        calls += 1
        return (calls,)

    monkeypatch.setattr(candidate, "_stat_fingerprint", drifting_fingerprint)
    with pytest.raises(ValueError, match="changed while being read"):
        provider.invoke(request, lambda _stage: None)


def test_image_size_digest_content_and_extension_are_verified(tmp_path: Path) -> None:
    provider, request, _ = _provider_request_backend(tmp_path)
    artifact = request.artifacts[0]

    with pytest.raises(ValueError, match="size does not match"):
        provider.invoke(
            replace(
                request,
                artifacts=(replace(artifact, size_bytes=artifact.size_bytes + 1),),
            ),
            lambda _stage: None,
        )
    with pytest.raises(ValueError, match="digest does not match"):
        provider.invoke(
            replace(request, artifacts=(replace(artifact, sha256="0" * 64),)),
            lambda _stage: None,
        )

    invalid_path = tmp_path / "inputs" / "invalid.png"
    invalid_path.write_bytes(b"not-an-image")
    invalid_bytes = invalid_path.read_bytes()
    invalid = ArtifactBinding(
        path="inputs/invalid.png",
        sha256=hashlib.sha256(invalid_bytes).hexdigest(),
        size_bytes=len(invalid_bytes),
    )
    with pytest.raises(ValueError, match="valid supported image"):
        provider.invoke(
            replace(request, image_paths=(invalid_path,), artifacts=(invalid,)),
            lambda _stage: None,
        )

    disguised_path = tmp_path / "inputs" / "disguised.jpg"
    disguised_path.write_bytes(request.image_paths[0].read_bytes())
    disguised_bytes = disguised_path.read_bytes()
    disguised = ArtifactBinding(
        path="inputs/disguised.jpg",
        sha256=hashlib.sha256(disguised_bytes).hexdigest(),
        size_bytes=len(disguised_bytes),
    )
    with pytest.raises(ValueError, match="filename extension"):
        provider.invoke(
            replace(request, image_paths=(disguised_path,), artifacts=(disguised,)),
            lambda _stage: None,
        )


def test_declared_image_size_is_checked_before_opening_a_content_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, request, backend = _provider_request_backend(tmp_path)
    artifact = request.artifacts[0]
    stream_opened = False

    def fail_if_stream_is_opened(*_args, **_kwargs):
        nonlocal stream_opened
        stream_opened = True
        raise AssertionError("an oversized file must not be streamed")

    monkeypatch.setattr(candidate.os, "fdopen", fail_if_stream_is_opened)
    with pytest.raises(ValueError, match="size does not match"):
        provider.invoke(
            replace(
                request,
                artifacts=(replace(artifact, size_bytes=artifact.size_bytes - 1),),
            ),
            lambda _stage: None,
        )

    assert stream_opened is False
    assert backend.requests == []


def test_small_compressed_image_with_excessive_decoded_pixels_is_rejected(
    tmp_path: Path,
) -> None:
    provider, request, backend = _provider_request_backend(tmp_path)
    image_path = request.image_paths[0]
    Image.new("1", (4097, 4097), color=0).save(image_path, optimize=True)
    image_bytes = image_path.read_bytes()
    assert len(image_bytes) < 64 * 1024
    artifact = replace(
        request.artifacts[0],
        sha256=hashlib.sha256(image_bytes).hexdigest(),
        size_bytes=len(image_bytes),
    )

    with pytest.raises(ValueError, match="decoded pixel limit"):
        provider.invoke(
            replace(request, artifacts=(artifact, request.artifacts[1])),
            lambda _stage: None,
        )

    assert backend.requests == []


def test_short_read_and_pillow_decompression_bomb_fail_before_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, request, backend = _provider_request_backend(tmp_path)
    original_fdopen = candidate.os.fdopen

    class ShortStream:
        def __init__(self, stream) -> None:
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, limit: int) -> bytes:
            return self.stream.read(limit)[:-1]

        def fileno(self) -> int:
            return self.stream.fileno()

    monkeypatch.setattr(
        candidate.os,
        "fdopen",
        lambda descriptor, mode: ShortStream(original_fdopen(descriptor, mode)),
    )
    with pytest.raises(ValueError, match="size does not match"):
        provider.invoke(request, lambda _stage: None)
    monkeypatch.setattr(candidate.os, "fdopen", original_fdopen)

    def decompression_bomb(*_args, **_kwargs):
        raise Image.DecompressionBombError("fixture bomb")

    monkeypatch.setattr(candidate.Image, "open", decompression_bomb)
    with pytest.raises(ValueError, match="decoded pixel limit"):
        provider.invoke(request, lambda _stage: None)

    assert backend.requests == []


@pytest.mark.parametrize(
    "resource",
    [
        object(),
        ResourceUsage(gpu_time_ms=True, visible_vlm_invocations=1),
        ResourceUsage(input_tokens=-1, visible_vlm_invocations=1),
        ResourceUsage(visible_vlm_invocations=0),
        ResourceUsage(visible_vlm_invocations=2),
        ResourceUsage(network_calls=1, visible_vlm_invocations=1),
        ResourceUsage(remote_paid_calls=1, visible_vlm_invocations=1),
        ResourceUsage(compile_attempts=1, visible_vlm_invocations=1),
        ResourceUsage(fresh_physical_replays=1, visible_vlm_invocations=1),
        ResourceUsage(runtime_steps=1, visible_vlm_invocations=1),
        ResourceUsage(contact_window_steps=1, visible_vlm_invocations=1),
        ResourceUsage(prompt_rewrites=1, visible_vlm_invocations=1),
        ResourceUsage(output_tokens=769, visible_vlm_invocations=1),
    ],
)
def test_backend_usage_receipts_fail_closed(tmp_path: Path, resource: Any) -> None:
    model = _qualified_model(tmp_path)
    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(_typed_response(), resource=resource),
    )

    with pytest.raises(ValueError):
        provider.invoke(_visible_request(tmp_path, model), lambda _stage: None)


def test_backend_output_container_and_raw_byte_contract_are_exact(tmp_path: Path) -> None:
    model = _qualified_model(tmp_path)
    request = _visible_request(tmp_path, model)

    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(returned=None),
    )
    with pytest.raises(ValueError, match="QwenVisibleGeneration"):
        provider.invoke(request, lambda _stage: None)

    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(raw_response="{}"),
    )
    with pytest.raises(ValueError, match="exact bytes"):
        provider.invoke(request, lambda _stage: None)


def test_raw_response_byte_limit_is_enforced_before_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _qualified_model(tmp_path)
    raw = b'{"schema_version":"vlm_fallback.visible_review.v1"}'
    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(raw_response=raw),
    )
    monkeypatch.setattr(candidate, "_MAX_RAW_RESPONSE_BYTES", len(raw) - 1)

    with pytest.raises(ValueError, match="byte limit"):
        provider.invoke(_visible_request(tmp_path, model), lambda _stage: None)


def test_model_roster_is_rechecked_before_and_after_backend_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _qualified_model(tmp_path)
    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(_typed_response()),
    )
    monkeypatch.setattr(
        candidate.model_content,
        "refresh_model_content",
        lambda *_args: SimpleNamespace(roster_sha256="d" * 64),
    )
    with pytest.raises(VisibleProviderIntegrityError, match="roster changed"):
        provider.invoke(_visible_request(tmp_path, model), lambda _stage: None)

    monkeypatch.undo()
    request = _visible_request(tmp_path / "second", model)
    backend = _RecordingBackend(
        _typed_response(),
        on_generate=lambda _request: (
            model.local_snapshot_path.joinpath("config.json")
            .resolve()
            .write_bytes(b"mutated model bytes")
        ),
    )
    provider = LocalQwenVisibleProvider(models=(model,), backend=backend)
    with pytest.raises(ValueError, match="does not match"):
        provider.invoke(request, lambda _stage: None)


def test_finally_rechecks_identity_when_backend_raises_or_drifts(tmp_path: Path) -> None:
    model = _qualified_model(tmp_path)
    request = _visible_request(tmp_path, model)

    def fail(_request):
        raise RuntimeError("backend failed")

    provider = LocalQwenVisibleProvider(
        models=(model,),
        backend=_RecordingBackend(_typed_response(), on_generate=fail),
    )
    with pytest.raises(RuntimeError, match="backend failed"):
        provider.invoke(request, lambda _stage: None)

    backend = _RecordingBackend(_typed_response())

    def drift(_request):
        backend.identity = replace(backend.identity, revision="drifted")

    backend.on_generate = drift
    provider = LocalQwenVisibleProvider(models=(model,), backend=backend)
    with pytest.raises(VisibleProviderIntegrityError, match="backend identity changed"):
        provider.invoke(request, lambda _stage: None)


class _FakeTensor:
    def __init__(self, values: list[int], *, batched: bool = False) -> None:
        self.values = values
        self.shape = (1, len(values)) if batched else (len(values),)

    def __getitem__(self, index):
        return _FakeTensor(self.values[index])


class _FakeBatch(dict):
    devices: list[Any] = []

    def __init__(self) -> None:
        self.input_ids = _FakeTensor([10, 11, 12], batched=True)
        super().__init__(input_ids=self.input_ids)
        self.device: str | None = None

    def to(self, device: Any):
        self.device = device
        type(self).devices.append(device)
        return self


class _FakeModel:
    load_calls: list[dict[str, Any]] = []
    generate_calls: list[dict[str, Any]] = []
    evaluated = 0
    on_generate = None

    @classmethod
    def from_pretrained(cls, path: str, **kwargs):
        cls.load_calls.append({"path": path, **kwargs})
        return cls()

    def eval(self) -> None:
        type(self).evaluated += 1

    def generate(self, **kwargs):
        type(self).generate_calls.append(kwargs)
        if type(self).on_generate is not None:
            type(self).on_generate()
        return [_FakeTensor([10, 11, 12, 20, 21])]


class _FakeProcessor:
    load_calls: list[dict[str, Any]] = []
    messages: list[Any] = []
    input_calls: list[dict[str, Any]] = []
    decode_calls: list[tuple[Any, dict[str, Any]]] = []
    decoded: Any = None

    @classmethod
    def from_pretrained(cls, path: str, **kwargs):
        cls.load_calls.append({"path": path, **kwargs})
        return cls()

    def apply_chat_template(self, messages, **kwargs):
        type(self).messages.append((messages, kwargs))
        return "templated-visible-input"

    def __call__(self, **kwargs):
        type(self).input_calls.append(kwargs)
        return _FakeBatch()

    def batch_decode(self, outputs, **kwargs):
        type(self).decode_calls.append((outputs, kwargs))
        return self.decoded or [protocol.canonical_json_bytes(_typed_response()).decode("utf-8")]


class _FakeEvent:
    records = 0
    recorded_streams: list[Any] = []

    def __init__(self, *, enable_timing: bool) -> None:
        assert enable_timing is True

    def record(self, stream=None) -> None:
        type(self).records += 1
        type(self).recorded_streams.append(stream)

    def elapsed_time(self, other) -> float:
        assert isinstance(other, _FakeEvent)
        return 7.6


@dataclass(frozen=True)
class _FakeDevice:
    name: str

    def __str__(self) -> str:
        return self.name


class _FakeStream:
    synchronizations: list[str] = []

    def __init__(self, device: _FakeDevice) -> None:
        self.device = device

    def synchronize(self) -> None:
        type(self).synchronizations.append(str(self.device))


class _FakeDeviceContext:
    def __init__(self, device: _FakeDevice) -> None:
        self.device = device

    def __enter__(self):
        _FakeCuda.device_entries.append(str(self.device))
        return None

    def __exit__(self, *_args):
        _FakeCuda.device_exits.append(str(self.device))
        return None


class _FakeCuda:
    available = True
    count = 1
    peak_bytes = 1024 * 1024 + 1
    resets: list[Any] = []
    synchronizations: list[Any] = []
    peak_queries: list[Any] = []
    current_stream_calls: list[Any] = []
    device_entries: list[str] = []
    device_exits: list[str] = []
    empty_cache_calls = 0
    manual_seeds: list[int] = []

    @classmethod
    def is_available(cls) -> bool:
        return cls.available

    @classmethod
    def device_count(cls) -> int:
        return cls.count

    @classmethod
    def reset_peak_memory_stats(cls, device: Any) -> None:
        cls.resets.append(device)

    @classmethod
    def synchronize(cls, index: int) -> None:
        cls.synchronizations.append(index)

    @classmethod
    def max_memory_allocated(cls, device: Any) -> int:
        cls.peak_queries.append(device)
        return cls.peak_bytes

    @classmethod
    def device(cls, device: _FakeDevice) -> _FakeDeviceContext:
        return _FakeDeviceContext(device)

    @classmethod
    def current_stream(cls, device: _FakeDevice) -> _FakeStream:
        cls.current_stream_calls.append(device)
        return _FakeStream(device)

    @classmethod
    def empty_cache(cls) -> None:
        cls.empty_cache_calls += 1

    @classmethod
    def manual_seed_all(cls, seed: int) -> None:
        cls.manual_seeds.append(seed)

    @staticmethod
    def get_device_name(index: int) -> str:
        assert index == 0
        return "Fake RTX 5090"

    @staticmethod
    def get_device_capability(index: int) -> tuple[int, int]:
        assert index == 0
        return (12, 0)

    Event = _FakeEvent


class _InferenceMode:
    def __enter__(self):
        return None

    def __exit__(self, *_args):
        return None


def _reset_fake_transformers() -> None:
    _FakeModel.load_calls.clear()
    _FakeModel.generate_calls.clear()
    _FakeModel.evaluated = 0
    _FakeModel.on_generate = None
    _FakeProcessor.load_calls.clear()
    _FakeProcessor.messages.clear()
    _FakeProcessor.input_calls.clear()
    _FakeProcessor.decode_calls.clear()
    _FakeProcessor.decoded = None
    _FakeBatch.devices.clear()
    _FakeEvent.records = 0
    _FakeEvent.recorded_streams.clear()
    _FakeCuda.available = True
    _FakeCuda.count = 1
    _FakeCuda.peak_bytes = 1024 * 1024 + 1
    _FakeCuda.resets.clear()
    _FakeCuda.synchronizations.clear()
    _FakeCuda.peak_queries.clear()
    _FakeCuda.current_stream_calls.clear()
    _FakeCuda.device_entries.clear()
    _FakeCuda.device_exits.clear()
    _FakeCuda.empty_cache_calls = 0
    _FakeCuda.manual_seeds.clear()
    _FakeStream.synchronizations.clear()


def _fake_transformers_runtime(*, device_index: int = 0) -> QwenTransformersRuntime:
    torch = SimpleNamespace(
        bfloat16="bfloat16",
        device=lambda name: _FakeDevice(name),
        cuda=_FakeCuda,
        inference_mode=_InferenceMode,
        manual_seeds=[],
        deterministic_calls=[],
        backends=SimpleNamespace(
            cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=True)),
            cudnn=SimpleNamespace(allow_tf32=True, benchmark=True),
        ),
    )
    torch.manual_seed = torch.manual_seeds.append
    torch.use_deterministic_algorithms = torch.deterministic_calls.append
    fingerprint = QwenRuntimeFingerprint(
        fingerprint_id="fixture-selected-runtime-facts",
        version_facts_sha256="7" * 64,
        entrypoint_facts_sha256="8" * 64,
        device_facts_sha256="9" * 64,
        dependency_closure_complete=False,
    )
    return QwenTransformersRuntime(
        fingerprint=fingerprint,
        device_index=device_index,
        torch=torch,
        auto_processor=_FakeProcessor,
        model_class=_FakeModel,
        fingerprint_probe=lambda: fingerprint,
    )


def _bound_generation_request(tmp_path: Path) -> QwenVisibleGenerationRequest:
    model = _qualified_model(tmp_path)
    request = _visible_request(tmp_path, model)
    raw = request.image_paths[0].read_bytes()
    return QwenVisibleGenerationRequest(
        arm=request.arm,
        task_context=request.task_context,
        prompt=request.prompt,
        processor_config=request.processor_config,
        seed=request.seed,
        model=model,
        images=(
            BoundVisibleImage(
                path=request.artifacts[0].path,
                content=raw,
                content_sha256=request.artifacts[0].sha256,
                size_bytes=len(raw),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HF_HUB_OFFLINE", None),
        ("HF_HUB_OFFLINE", "0"),
        ("TRANSFORMERS_OFFLINE", None),
        ("TRANSFORMERS_OFFLINE", "0"),
    ],
)
def test_concrete_backend_rechecks_offline_environment_before_each_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str | None,
) -> None:
    _reset_fake_transformers()
    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    stages: list[str] = []

    with pytest.raises(
        RuntimeError,
        match="HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1",
    ):
        backend.generate(_bound_generation_request(tmp_path), stages.append)

    assert stages == []
    assert _FakeCuda.device_entries == []
    assert _FakeModel.load_calls == []
    assert _FakeModel.generate_calls == []


def test_transformers_backend_runs_one_strict_offline_image_generation(
    tmp_path: Path,
) -> None:
    _reset_fake_transformers()
    runtime = _fake_transformers_runtime()
    backend = TransformersQwenVisibleBackend(runtime=runtime)
    model = _qualified_model(tmp_path)
    provider = LocalQwenVisibleProvider(models=(model,), backend=backend)
    stages: list[str] = []

    outcome = provider.invoke(_visible_request(tmp_path, model), stages.append)

    assert outcome.decision == "visible_review_complete"
    assert outcome.resource == ResourceUsage(
        gpu_time_ms=8,
        peak_vram_mib=2,
        input_tokens=3,
        output_tokens=2,
        visible_vlm_invocations=1,
    )
    assert backend.identity.network_access is False
    assert len(_FakeModel.load_calls) == 1
    assert len(_FakeProcessor.load_calls) == 1
    assert len(_FakeModel.generate_calls) == 1
    assert _FakeModel.evaluated == 1
    assert _FakeModel.load_calls[0] == {
        "path": str(model.local_snapshot_path),
        "torch_dtype": "bfloat16",
        "device_map": {"": "cuda:0"},
        "low_cpu_mem_usage": True,
        "local_files_only": True,
        "trust_remote_code": False,
    }
    assert _FakeProcessor.load_calls[0] == {
        "path": str(model.local_snapshot_path),
        "min_pixels": 200704,
        "max_pixels": 802816,
        "local_files_only": True,
        "trust_remote_code": False,
        "use_fast": False,
    }
    generation = _FakeModel.generate_calls[0]
    assert generation["max_new_tokens"] == 768
    assert generation["do_sample"] is False
    assert generation["temperature"] is None
    assert generation["top_p"] is None
    assert generation["top_k"] is None
    assert generation["num_beams"] == 1
    assert generation["use_cache"] is True
    assert _FakeProcessor.messages[0][0][0] == {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "Review the rendered scene using the frozen visible checks.",
            }
        ],
    }
    assert _FakeProcessor.messages[0][0][1]["content"][-1] == {
        "type": "text",
        "text": "Place the apple on the plate.",
    }
    assert len(_FakeProcessor.input_calls[0]["images"]) == 1
    assert _FakeProcessor.input_calls[0]["return_tensors"] == "pt"
    assert stages == [
        "runtime_verified",
        "model_loaded",
        "inputs_encoded",
        "generation_completed",
        "response_decoded",
    ]
    assert _FakeEvent.records == 2
    assert [str(device) for device in _FakeCuda.resets] == ["cuda:0"]
    assert _FakeCuda.synchronizations == []
    assert _FakeStream.synchronizations == ["cuda:0"]
    assert runtime.torch.manual_seeds == []
    assert _FakeCuda.manual_seeds == []
    assert runtime.torch.deterministic_calls == []
    assert runtime.torch.backends.cuda.matmul.allow_tf32 is True
    assert runtime.torch.backends.cudnn.allow_tf32 is True
    assert runtime.torch.backends.cudnn.benchmark is True


def test_transformers_backend_scopes_measurement_to_nonzero_cuda_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_fake_transformers()
    _FakeCuda.count = 2
    preserved_environment = {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "caller-telemetry-setting",
    }
    for key, value in preserved_environment.items():
        monkeypatch.setenv(key, value)
    runtime = _fake_transformers_runtime(device_index=1)
    backend = TransformersQwenVisibleBackend(device_index=1, runtime=runtime)

    generation = backend.generate(_bound_generation_request(tmp_path), lambda _stage: None)

    assert generation.resource.visible_vlm_invocations == 1
    assert {key: os.environ[key] for key in preserved_environment} == preserved_environment
    assert runtime.torch.manual_seeds == []
    assert _FakeCuda.manual_seeds == []
    assert runtime.torch.deterministic_calls == []
    assert runtime.torch.backends.cuda.matmul.allow_tf32 is True
    assert runtime.torch.backends.cudnn.allow_tf32 is True
    assert runtime.torch.backends.cudnn.benchmark is True
    assert _FakeCuda.device_entries == ["cuda:1"]
    assert _FakeCuda.device_exits == ["cuda:1"]
    assert [str(device) for device in _FakeCuda.current_stream_calls] == ["cuda:1"]
    assert [str(device) for device in _FakeCuda.resets] == ["cuda:1"]
    assert [str(device) for device in _FakeCuda.peak_queries] == ["cuda:1"]
    assert [str(device) for device in _FakeBatch.devices] == ["cuda:1"]
    assert [event.device.name for event in _FakeEvent.recorded_streams] == [
        "cuda:1",
        "cuda:1",
    ]
    assert _FakeStream.synchronizations == ["cuda:1"]


def test_transformers_backend_reuses_then_rebinds_local_model_cache(tmp_path: Path) -> None:
    _reset_fake_transformers()
    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())
    first = _qualified_model(tmp_path / "first")
    second = _qualified_model(
        tmp_path / "second",
        arm="A2_typed_abstaining_critic_7b",
        model_role="confirmatory_model_size_ceiling",
        revision_character="b",
    )
    provider = LocalQwenVisibleProvider(models=(first, second), backend=backend)

    provider.invoke(_visible_request(tmp_path / "first-input", first), lambda _stage: None)
    provider.invoke(_visible_request(tmp_path / "repeat-input", first), lambda _stage: None)
    provider.invoke(_visible_request(tmp_path / "second-input", second), lambda _stage: None)

    assert len(_FakeModel.generate_calls) == 3
    assert len(_FakeModel.load_calls) == 2
    assert len(_FakeProcessor.load_calls) == 2
    assert _FakeCuda.empty_cache_calls == 1


def test_transformers_backend_rejects_a0_before_loading_a_model(
    tmp_path: Path,
) -> None:
    _reset_fake_transformers()
    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())
    request = _bound_generation_request(tmp_path)
    request = replace(
        request,
        arm="A0_current_critic_3b",
    )

    with pytest.raises(
        candidate.UnsupportedVisibleArmError,
        match="A0_current_critic_3b requires the independent frozen baseline provider",
    ):
        backend.generate(request, lambda _stage: None)

    with pytest.raises(
        candidate.UnsupportedVisibleArmError,
        match="backend supports only typed visible-study arms",
    ):
        backend.generate(replace(request, arm="unknown"), lambda _stage: None)

    assert _FakeModel.load_calls == []
    assert _FakeProcessor.load_calls == []


def _install_discovery_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cuda_available: bool = True,
    device_count: int = 1,
    torch_version: str = "2.11.0",
    transformers_version: str = "4.57.6",
    accelerate_version: str = "1.14.0",
    cuda_version: str = "12.8",
    device_name: str = "Fake RTX 5090",
    capability: Any = (12, 0),
    auto_processor: Any = _FakeProcessor,
    model_class: Any = _FakeModel,
) -> tuple[types.ModuleType, types.ModuleType]:
    torch_path = tmp_path / "torch.py"
    transformers_path = tmp_path / "transformers.py"
    torch_path.write_text("# exact fake torch module\n", encoding="utf-8")
    transformers_path.write_text("# exact fake transformers module\n", encoding="utf-8")
    torch = types.ModuleType("torch")
    torch.__file__ = str(torch_path)
    torch.__version__ = torch_version
    torch.version = SimpleNamespace(cuda=cuda_version)
    torch.bfloat16 = "bfloat16"
    torch.inference_mode = _InferenceMode
    torch._fixture_runtime_facts = {
        "device_name": device_name,
        "capability": capability,
    }
    torch.cuda = SimpleNamespace(
        is_available=lambda: cuda_available,
        device_count=lambda: device_count,
        get_device_name=lambda _index: torch._fixture_runtime_facts["device_name"],
        get_device_capability=lambda _index: torch._fixture_runtime_facts["capability"],
        empty_cache=lambda: None,
        reset_peak_memory_stats=lambda _index: None,
        Event=_FakeEvent,
        synchronize=lambda _index: None,
        max_memory_allocated=lambda _index: 0,
    )
    transformers = types.ModuleType("transformers")
    transformers.__file__ = str(transformers_path)
    transformers.__version__ = transformers_version
    transformers.AutoProcessor = auto_processor
    transformers.Qwen2_5_VLForConditionalGeneration = model_class

    def import_module(name: str):
        return {"torch": torch, "transformers": transformers}[name]

    monkeypatch.setattr(candidate.importlib, "import_module", import_module)
    monkeypatch.setattr(
        candidate.importlib.metadata,
        "version",
        lambda name: accelerate_version if name == "accelerate" else "unexpected",
    )
    return torch, transformers


def test_default_transformers_runtime_binds_local_packages_modules_and_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    torch, _ = _install_discovery_runtime(tmp_path, monkeypatch)

    backend = TransformersQwenVisibleBackend(device_index=0)

    assert backend.identity.backend_id == "vlm_fallback.transformers_qwen_visible"
    assert backend.identity.network_access is False
    assert backend._runtime.fingerprint.fingerprint_id == (
        "transformers-qwen2.5-vl-selected-runtime-facts"
    )
    assert backend._runtime.fingerprint.entrypoint_facts_sha256 != "0" * 64
    assert backend._runtime.fingerprint.dependency_closure_complete is False
    assert backend._runtime.torch is torch


@pytest.mark.parametrize("mutation", ["device_name", "torch_version", "entrypoint_bytes"])
def test_default_runtime_identity_recomputes_live_selected_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    _reset_fake_transformers()
    torch, _ = _install_discovery_runtime(tmp_path, monkeypatch)
    backend = TransformersQwenVisibleBackend(device_index=0)
    if mutation == "device_name":
        torch._fixture_runtime_facts["device_name"] = "Drifted GPU"
    elif mutation == "torch_version":
        torch.__version__ = "drifted"
    else:
        Path(torch.__file__).write_text("# drifted torch entrypoint\n", encoding="utf-8")

    with pytest.raises(VisibleProviderIntegrityError, match="runtime fingerprint"):
        _ = backend.identity

    assert _FakeModel.load_calls == []
    assert _FakeModel.generate_calls == []


def test_default_runtime_generation_rechecks_live_device_capability_before_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_fake_transformers()
    torch, _ = _install_discovery_runtime(tmp_path, monkeypatch)
    backend = TransformersQwenVisibleBackend(device_index=0)
    torch._fixture_runtime_facts["capability"] = (9, 9)
    stages: list[str] = []

    with pytest.raises(VisibleProviderIntegrityError, match="runtime fingerprint"):
        backend.generate(_bound_generation_request(tmp_path / "request"), stages.append)

    assert stages == []
    assert _FakeModel.load_calls == []
    assert _FakeModel.generate_calls == []


@pytest.mark.parametrize("failure", ["import", "metadata"])
def test_default_runtime_fails_closed_when_local_dependencies_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    _install_discovery_runtime(tmp_path, monkeypatch)
    if failure == "import":
        monkeypatch.setattr(
            candidate.importlib,
            "import_module",
            lambda _name: (_ for _ in ()).throw(ImportError("missing")),
        )
    else:
        monkeypatch.setattr(
            candidate.importlib.metadata,
            "version",
            lambda _name: (_ for _ in ()).throw(
                candidate.importlib.metadata.PackageNotFoundError("accelerate")
            ),
        )

    with pytest.raises(RuntimeError, match="dependency is unavailable"):
        TransformersQwenVisibleBackend()


@pytest.mark.parametrize("missing", ["processor", "model"])
def test_default_runtime_requires_installed_qwen_transformers_classes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    _install_discovery_runtime(
        tmp_path,
        monkeypatch,
        auto_processor=None if missing == "processor" else _FakeProcessor,
        model_class=None if missing == "model" else _FakeModel,
    )

    with pytest.raises(RuntimeError, match="lacks Qwen2.5-VL"):
        TransformersQwenVisibleBackend()


@pytest.mark.parametrize(
    ("available", "count", "match"),
    [(False, 1, "CUDA is unavailable"), (True, 0, "device index")],
)
def test_default_runtime_requires_the_configured_cuda_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
    count: int,
    match: str,
) -> None:
    _install_discovery_runtime(
        tmp_path,
        monkeypatch,
        cuda_available=available,
        device_count=count,
    )

    with pytest.raises(RuntimeError, match=match):
        TransformersQwenVisibleBackend()


@pytest.mark.parametrize(
    "updates",
    [
        {"torch_version": ""},
        {"transformers_version": ""},
        {"accelerate_version": ""},
        {"cuda_version": ""},
        {"device_name": ""},
        {"capability": [12, 0]},
        {"capability": (12,)},
        {"capability": (-1, 0)},
        {"capability": (True, 0)},
    ],
)
def test_default_runtime_rejects_incomplete_runtime_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    updates: dict[str, Any],
) -> None:
    _install_discovery_runtime(tmp_path, monkeypatch, **updates)

    with pytest.raises(RuntimeError, match="fingerprint is incomplete"):
        TransformersQwenVisibleBackend()


def test_default_runtime_requires_readable_exact_module_source_paths(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="no exact source path"):
        candidate._module_sha256(SimpleNamespace(__file__=None), "fixture")
    with pytest.raises(RuntimeError, match="source is unavailable"):
        candidate._module_sha256(
            SimpleNamespace(__file__=str(tmp_path / "missing.py")),
            "fixture",
        )


def test_transformers_runtime_and_device_injection_contract_is_exact() -> None:
    _reset_fake_transformers()
    runtime = _fake_transformers_runtime()
    for device_index in (True, -1):
        with pytest.raises(ValueError, match="device_index"):
            TransformersQwenVisibleBackend(device_index=device_index, runtime=runtime)
    with pytest.raises(ValueError, match="QwenTransformersRuntime"):
        TransformersQwenVisibleBackend(runtime=object())  # type: ignore[arg-type]
    for invalid in (
        replace(runtime, device_index=True),
        replace(runtime, device_index=-1),
    ):
        with pytest.raises(ValueError, match="runtime device_index"):
            TransformersQwenVisibleBackend(runtime=invalid)
    with pytest.raises(ValueError, match="runtime fingerprint"):
        TransformersQwenVisibleBackend(runtime=replace(runtime, fingerprint=object()))  # type: ignore[arg-type]
    for field in ("torch", "auto_processor", "model_class", "fingerprint_probe"):
        with pytest.raises(ValueError, match="bindings must be complete"):
            TransformersQwenVisibleBackend(runtime=replace(runtime, **{field: None}))
    with pytest.raises(ValueError, match="does not match"):
        TransformersQwenVisibleBackend(device_index=1, runtime=runtime)


def test_transformers_backend_rejects_uncollectable_or_mismatched_live_fingerprint() -> None:
    _reset_fake_transformers()
    runtime = _fake_transformers_runtime()

    def fail_probe():
        raise RuntimeError("probe failed")

    with pytest.raises(ValueError, match="live fingerprint cannot be collected"):
        TransformersQwenVisibleBackend(runtime=replace(runtime, fingerprint_probe=fail_probe))
    mismatched = replace(runtime.fingerprint, device_facts_sha256="0" * 64)
    with pytest.raises(ValueError, match="does not match live selected facts"):
        TransformersQwenVisibleBackend(
            runtime=replace(runtime, fingerprint_probe=lambda: mismatched)
        )

    backend = TransformersQwenVisibleBackend(runtime=runtime)
    object.__setattr__(backend._runtime, "fingerprint_probe", fail_probe)
    with pytest.raises(VisibleProviderIntegrityError, match="runtime fingerprint"):
        _ = backend.identity


def test_transformers_backend_rechecks_live_fingerprint_after_generation(
    tmp_path: Path,
) -> None:
    _reset_fake_transformers()
    runtime = _fake_transformers_runtime()
    live = {"fingerprint": runtime.fingerprint}
    runtime = replace(runtime, fingerprint_probe=lambda: live["fingerprint"])
    backend = TransformersQwenVisibleBackend(runtime=runtime)
    _FakeModel.on_generate = lambda: live.update(
        fingerprint=replace(runtime.fingerprint, device_facts_sha256="0" * 64)
    )
    stages: list[str] = []

    with pytest.raises(VisibleProviderIntegrityError, match="runtime fingerprint"):
        backend.generate(_bound_generation_request(tmp_path), stages.append)

    assert len(_FakeModel.load_calls) == 1
    assert len(_FakeModel.generate_calls) == 1
    assert stages[-1] == "generation_completed"
    assert "response_decoded" not in stages


def test_transformers_backend_detects_source_runtime_and_component_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_fake_transformers()
    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())
    monkeypatch.setattr(candidate, "_source_sha256", lambda: "0" * 64)
    with pytest.raises(VisibleProviderIntegrityError, match="backend source"):
        _ = backend.identity
    monkeypatch.undo()

    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())
    object.__setattr__(
        backend._runtime,
        "fingerprint",
        replace(backend._runtime.fingerprint, version_facts_sha256="0" * 64),
    )
    with pytest.raises(VisibleProviderIntegrityError, match="runtime fingerprint"):
        _ = backend.identity

    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())
    object.__setattr__(backend._runtime, "model_class", object())
    with pytest.raises(VisibleProviderIntegrityError, match="runtime fingerprint"):
        _ = backend.identity


def test_transformers_backend_generation_boundary_rejects_wrong_types(tmp_path: Path) -> None:
    _reset_fake_transformers()
    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())
    request = _bound_generation_request(tmp_path)

    with pytest.raises(ValueError, match="QwenVisibleGenerationRequest"):
        backend.generate(object(), lambda _stage: None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="progress"):
        backend.generate(request, None)  # type: ignore[arg-type]
    for seed in (True, -1):
        with pytest.raises(ValueError, match="seed"):
            backend.generate(replace(request, seed=seed), lambda _stage: None)
    for images in (list(request.images), (), (object(),)):
        with pytest.raises(ValueError, match="bound visible images"):
            backend.generate(
                replace(request, images=images),  # type: ignore[arg-type]
                lambda _stage: None,
            )
    with pytest.raises(ValueError, match="frozen study"):
        backend.generate(
            replace(
                request,
                processor_config={"min_pixels": 200704, "max_pixels": 900000},
            ),
            lambda _stage: None,
        )


@pytest.mark.parametrize(("available", "count"), [(False, 1), (True, 0)])
def test_transformers_backend_rechecks_cuda_immediately_before_loading(
    tmp_path: Path,
    available: bool,
    count: int,
) -> None:
    _reset_fake_transformers()
    _FakeCuda.available = available
    _FakeCuda.count = count
    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())

    with pytest.raises(RuntimeError, match="became unavailable"):
        backend.generate(_bound_generation_request(tmp_path), lambda _stage: None)


@pytest.mark.parametrize("decoded", ["not-a-list", ["one", "two"], [1]])
def test_transformers_backend_requires_one_exact_decoded_string(
    tmp_path: Path,
    decoded: Any,
) -> None:
    _reset_fake_transformers()
    _FakeProcessor.decoded = decoded
    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())

    with pytest.raises(RuntimeError, match="decoder returned an invalid response"):
        backend.generate(_bound_generation_request(tmp_path), lambda _stage: None)


def test_transformers_backend_bounds_raw_bytes_and_validates_gpu_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_fake_transformers()
    _FakeProcessor.decoded = ["response-too-large"]
    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())
    request = _bound_generation_request(tmp_path)
    monkeypatch.setattr(candidate, "_MAX_RAW_RESPONSE_BYTES", 3)
    with pytest.raises(RuntimeError, match="response exceeds"):
        backend.generate(request, lambda _stage: None)

    monkeypatch.undo()
    _reset_fake_transformers()
    _FakeCuda.peak_bytes = -1
    backend = TransformersQwenVisibleBackend(runtime=_fake_transformers_runtime())
    with pytest.raises(RuntimeError, match="negative peak memory"):
        backend.generate(request, lambda _stage: None)


def test_transformers_backend_fails_deterministically_when_local_model_load_fails(
    tmp_path: Path,
) -> None:
    _reset_fake_transformers()

    class MissingLocalModel:
        @staticmethod
        def from_pretrained(_path: str, **_kwargs):
            raise RuntimeError("qualified local model is unavailable")

    runtime = replace(_fake_transformers_runtime(), model_class=MissingLocalModel)
    backend = TransformersQwenVisibleBackend(runtime=runtime)

    with pytest.raises(RuntimeError, match="qualified local model is unavailable"):
        backend.generate(_bound_generation_request(tmp_path), lambda _stage: None)
