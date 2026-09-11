from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

import self_improving.harness as harness
from self_improving.harness import (
    ArtifactRef,
    DependencyRef,
    Image2EnvCompileV2Input,
    Image2EnvCompileV2Output,
    LocalArtifactStore,
    QualificationCandidate,
    RecordingEventSink,
    RunStatus,
    SkillRegistry,
    StaticDependencyResolver,
)
from self_improving.harness.schemas import x2env_compile_evidence as evidence
from self_improving.harness.x2env_compile_closure import (
    ExactReuseCompileClosureVerifier,
    VerifiedExactReuseCompileClosure,
)
from tests.self_improving.harness.test_x2env_compile_closure import (
    _build,
    _case,
    _publish_model,
)

try:
    from self_improving.harness.image2env_compile import Image2EnvExactReuseCompileHandler
except ImportError:
    Image2EnvExactReuseCompileHandler = None  # type: ignore[assignment,misc]


_requires_handler = pytest.mark.skipif(
    Image2EnvExactReuseCompileHandler is None,
    reason="controlled image2env exact-reuse compile handler is absent",
)

_DERIVED_SCHEMAS = {
    "harness.scene_ir.v1",
    "harness.task_ir.v1",
    "harness.x2env_asset_selection.v1",
    "harness.asset_closure_manifest.v1",
    "harness.x2env_binding_manifest.v1",
    "harness.x2env_static_validation.v1",
    "harness.x2env_environment_package.v1",
    "harness.x2env_compile_receipt.v2",
}


class SequenceClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


def _refs(value: object) -> Iterator[ArtifactRef]:
    if isinstance(value, ArtifactRef):
        yield value
    elif hasattr(value, "model_fields"):
        for name in value.__class__.model_fields:  # type: ignore[attr-defined]
            yield from _refs(getattr(value, name))
    elif isinstance(value, dict):
        required = {"name", "uri", "media_type", "sha256", "bytes", "schema_version"}
        if required.issubset(value):
            yield ArtifactRef.model_validate(value)
        else:
            for child in value.values():
                yield from _refs(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _refs(child)


def _copy_input_closure(
    source: LocalArtifactStore,
    target: LocalArtifactStore,
    compile_input: Image2EnvCompileV2Input,
) -> tuple[ArtifactRef, ...]:
    pending = list(_refs(compile_input))
    copied: dict[tuple[str, str | None, str], ArtifactRef] = {}
    while pending:
        ref = pending.pop(0)
        identity = (ref.media_type, ref.schema_version, ref.sha256)
        if identity in copied:
            continue
        resolved = source.resolve(ref)
        copied_ref = target.put_file(
            resolved.path,
            name=ref.name,
            media_type=ref.media_type,
            schema_version=ref.schema_version,
        )
        assert copied_ref == ref
        copied[identity] = ref
        if ref.media_type == "application/json":
            payload = json.loads(resolved.path.read_bytes())
            pending.extend(_refs(payload))
    return tuple(copied[key] for key in sorted(copied))


def _cas_manifest(store: LocalArtifactStore) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (
            str(path.relative_to(store.root)),
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(store.root.rglob("*"))
        if path.is_file()
    )


def _read_model(store: LocalArtifactStore, ref: ArtifactRef, model: type[Any]) -> Any:
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _candidate() -> QualificationCandidate:
    return QualificationCandidate(
        skill_id="image2env.compile",
        version="1.0.0",
        input_schema="harness.image2env_compile_input.v2",
        output_schema="harness.image2env_compile_output.v2",
        max_attempts=1,
    )


def _registry(store: LocalArtifactStore, run_ids: Iterator[UUID]) -> SkillRegistry:
    return SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=StaticDependencyResolver(
            {
                "image2env.compile@1.0.0": (
                    DependencyRef(name="controlled_exact_reuse", version="1", sha256="9" * 64),
                )
            }
        ),
        event_sink=RecordingEventSink(),
        clock=SequenceClock(),
        run_id_factory=lambda: next(run_ids),
    )


def _handler(store: LocalArtifactStore, verifier: ExactReuseCompileClosureVerifier):
    assert Image2EnvExactReuseCompileHandler is not None
    return Image2EnvExactReuseCompileHandler(
        artifact_store=store,
        max_source_bytes=verifier.max_source_bytes,
        max_decoded_pixels=verifier.max_decoded_pixels,
        expected_implementation_sha256=verifier.expected_implementation_sha256,
        trusted_qualification_issuer_sha256=verifier.trusted_qualification_issuer_sha256,
        expected_runtime_lock=verifier.expected_runtime_lock,
        trusted_asset_qualifications=verifier.trusted_asset_qualifications,
    )


def _fresh_case(tmp_path: Path):
    source_case = _case(tmp_path / "source")
    store = LocalArtifactStore(tmp_path / "fresh-cas")
    supporting = _copy_input_closure(source_case.store, store, source_case.compile_input)
    verifier = ExactReuseCompileClosureVerifier(
        artifact_store=store,
        max_source_bytes=source_case.verifier.max_source_bytes,
        max_decoded_pixels=source_case.verifier.max_decoded_pixels,
        expected_implementation_sha256=source_case.verifier.expected_implementation_sha256,
        trusted_qualification_issuer_sha256=(
            source_case.verifier.trusted_qualification_issuer_sha256
        ),
        expected_runtime_lock=source_case.verifier.expected_runtime_lock,
        trusted_asset_qualifications=source_case.verifier.trusted_asset_qualifications,
    )
    return store, source_case.compile_input, verifier, supporting


def _evaluate(
    store: LocalArtifactStore,
    compile_input: Image2EnvCompileV2Input,
    verifier: ExactReuseCompileClosureVerifier,
    *,
    run_ids: tuple[UUID, ...] = (UUID("12345678-1234-4234-9234-123456789abc"),),
):
    registry = _registry(store, iter(run_ids))
    return registry.evaluate_candidate(
        _candidate(),
        _handler(store, verifier),
        compile_input.model_dump(mode="json"),
    )


def test_candidate_compiles_one_on_table_object_into_a_complete_v2_closure(
    tmp_path: Path,
) -> None:
    assert Image2EnvExactReuseCompileHandler is not None, (
        "Image2EnvExactReuseCompileHandler is not implemented"
    )
    assert harness.Image2EnvExactReuseCompileHandler is Image2EnvExactReuseCompileHandler
    store, compile_input, verifier, supporting = _fresh_case(tmp_path)
    before = _cas_manifest(store)
    registry = _registry(
        store,
        iter(
            (
                UUID("12345678-1234-4234-9234-123456789abc"),
                UUID("22345678-1234-4234-9234-123456789abc"),
            )
        ),
    )
    handler = _handler(store, verifier)
    result = registry.evaluate_candidate(
        _candidate(), handler, compile_input.model_dump(mode="json")
    )

    assert result.state.status is RunStatus.SUCCEEDED
    assert result.invocation is not None
    assert result.invocation.effective_parameters == compile_input.model_dump(mode="json")
    output = Image2EnvCompileV2Output.model_validate(result.state.output)
    assert output.simulator_executed is False
    assert output.publishable is False
    assert output.environment_package.simulator_ready is False
    assert output.environment_package.collision_qualified is False
    assert output.environment_package.source_media == compile_input.source_media
    assert output.asset_decisions[0].object_id == "can"
    assert output.asset_decisions[0].lane.value == "exact_asset_reuse"

    receipt = _read_model(store, output.compile_receipt, evidence.X2EnvCompileReceiptV2)
    canonical_input = _read_model(store, receipt.compile_input, Image2EnvCompileV2Input)
    assert canonical_input == compile_input
    assert receipt.skill_ref == "image2env.compile@1.0.0"
    assert receipt.source_media_sha256 == compile_input.source_media.source_media_sha256
    assert receipt.interpretation_proposal == compile_input.interpretation_proposal
    assert receipt.asset_catalog == compile_input.asset_catalog
    assert receipt.predecessor_package == compile_input.predecessor_package
    assert receipt.simulator_executed is False
    assert receipt.publishable is False

    package_refs = tuple(
        ref
        for ref in result.state.artifacts
        if ref.schema_version == "harness.x2env_environment_package.v1"
    )
    assert package_refs == (receipt.environment_package,)
    derived = tuple(ref for ref in result.state.artifacts if ref.schema_version in _DERIVED_SCHEMAS)
    assert {ref.schema_version for ref in derived} == _DERIVED_SCHEMAS
    assert len(derived) == 8

    verified = verifier.verify(compile_input, output)
    assert isinstance(verified, VerifiedExactReuseCompileClosure)
    assert verified.compile_receipt == receipt

    recursively_consumed = {
        (ref.media_type, ref.schema_version, ref.sha256)
        for ref in _copy_input_closure(store, LocalArtifactStore(tmp_path / "closure-copy"), output)
    }
    state_artifacts = {
        (ref.media_type, ref.schema_version, ref.sha256) for ref in result.state.artifacts
    }
    assert recursively_consumed.issubset(state_artifacts)
    assert {(ref.media_type, ref.schema_version, ref.sha256) for ref in supporting}.issubset(
        state_artifacts
    )

    added_digests = {row[2] for row in _cas_manifest(store)} - {row[2] for row in before}
    assert added_digests == {receipt.compile_input.sha256, *(ref.sha256 for ref in derived)}
    after_first = _cas_manifest(store)
    repeated = registry.evaluate_candidate(
        _candidate(), handler, compile_input.model_dump(mode="json")
    )
    assert repeated.state.status is RunStatus.SUCCEEDED
    assert repeated.state.run_id != result.state.run_id
    assert repeated.state.output == result.state.output
    assert repeated.state.artifacts == result.state.artifacts
    assert _cas_manifest(store) == after_first


def _replace_proposal_category(tmp_path: Path, category: str):
    source_case = _case(tmp_path / "source")
    proposal = _read_model(
        source_case.store,
        source_case.compile_input.interpretation_proposal,
        evidence.SceneInterpretationProposalV1,
    ).model_dump(mode="json")
    proposal["objects"][0]["category"] = category
    changed = _build(evidence.SceneInterpretationProposalV1, "proposal_sha256", proposal)
    changed_ref = _publish_model(source_case.store, tmp_path / "changed", "proposal", changed)
    compile_input = source_case.compile_input.model_copy(
        update={"interpretation_proposal": changed_ref}
    )
    return source_case, compile_input


@_requires_handler
def test_missing_category_is_blocked_before_any_derived_cas_publish(tmp_path: Path) -> None:
    source_case, compile_input = _replace_proposal_category(tmp_path, "unmatched")
    source_verifier = source_case.verifier
    store = LocalArtifactStore(tmp_path / "fresh-cas")
    _copy_input_closure(source_case.store, store, compile_input)
    verifier = ExactReuseCompileClosureVerifier(
        artifact_store=store,
        max_source_bytes=source_verifier.max_source_bytes,
        max_decoded_pixels=source_verifier.max_decoded_pixels,
        expected_implementation_sha256=source_verifier.expected_implementation_sha256,
        trusted_qualification_issuer_sha256=(source_verifier.trusted_qualification_issuer_sha256),
        expected_runtime_lock=source_verifier.expected_runtime_lock,
        trusted_asset_qualifications=source_verifier.trusted_asset_qualifications,
    )
    before = _cas_manifest(store)

    result = _evaluate(store, compile_input, verifier)

    assert result.state.status is RunStatus.BLOCKED
    assert result.state.blocker is not None
    assert result.state.blocker.code == "HARN_X2ENV_COMPILE_ASSET_SELECTION_FAILED"
    assert result.state.output is None
    assert _cas_manifest(store) == before
