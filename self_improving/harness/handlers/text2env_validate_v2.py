"""Qualified legacy validation over one replay's complete CAS evidence closure."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import UUID4, ValidationError

from self_improving.validate_v2_snapshot import (
    SnapshotValidationError,
    SnapshotValidationRequest,
    ValidateV2SnapshotAdapter,
)

from ..artifacts import ArtifactResolutionError, LocalArtifactStore
from ..registry import HandlerResult, RunContext, SkillBlocked
from ..run_receipts import (
    LoadedPortableRunReceipt,
    PortableRunReceiptError,
    load_portable_run_receipt,
)
from ..schemas.base import HarnessModel, Sha256
from ..schemas.common import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    RunStatus,
    SkillDescriptor,
    ValidationStatus,
)
from ..schemas.text2env import (
    EnvironmentPackage,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)
from ..schemas.text2env_validate_v2 import (
    VALIDATION_DECISION_SCHEMA_VERSION,
    Text2EnvValidateV2Input,
    Text2EnvValidateV2Output,
)
from ..validate_v2 import StrictValidateV2CasReader, ValidateV2EvidenceError
from ..validate_v2_dependencies import (
    TEXT2ENV_VALIDATE_V2_SKILL_REF,
    VALIDATE_V2_POLICY_DEPENDENCY,
)

_VALIDATION_REPORT_SCHEMA = "robotwin.scene_validation.v1"
_MEDIA_VERIFICATION_SCHEMA = "harness.replay_media_verification.v1"
_REQUEST_PROVENANCE_SCHEMA = "harness.text2env_request_provenance.v1"


class _RequestProvenance(HarnessModel):
    schema_version: Literal["harness.text2env_request_provenance.v1"]
    request: str
    seed: int
    compile_skill_ref: Literal["text2env.compile@1.0.0"]
    compile_run_id: UUID4
    compile_invocation_digest: Sha256
    compile_trusted_receipt: ArtifactRef
    compile_history_authority: ArtifactRef
    compile_world_state_sha256: Sha256
    environment_package: ArtifactRef


class _MediaVerificationReceipt(HarnessModel):
    schema_version: Literal["harness.replay_media_verification.v1"]
    verification: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _VerifiedLineage:
    package: EnvironmentPackage
    compile: LoadedPortableRunReceipt
    replay: LoadedPortableRunReceipt
    replay_execution: dict[str, Any]
    provenance: _RequestProvenance


@dataclass(frozen=True)
class Text2EnvValidateV2Handler:
    """Recompute the physical report and issue one deterministic v2 decision."""

    artifact_store: LocalArtifactStore
    scratch_parent: Path
    policy_dependency: DependencyRef

    def __post_init__(self) -> None:
        if type(self.artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")
        if type(self.policy_dependency) is not DependencyRef:
            raise TypeError("policy_dependency must be an exact DependencyRef")
        if (
            self.policy_dependency.name != VALIDATE_V2_POLICY_DEPENDENCY
            or self.policy_dependency.version != "2"
        ):
            raise ValueError("policy_dependency does not identify validate v2")

    def __call__(
        self,
        value: Text2EnvValidateV2Input,
        context: RunContext,
    ) -> HandlerResult:
        if context.dependencies != (self.policy_dependency,):
            raise _evidence_blocker("validate dependency identity changed")
        try:
            lineage = verify_validate_v2_lineage(self.artifact_store, value)
            context.emit(
                "validate.lineage.verified",
                artifact_refs=tuple(_input_refs(value)),
            )
            recomputed = ValidateV2SnapshotAdapter(
                artifact_store=self.artifact_store,
                scratch_parent=self.scratch_parent,
            ).recompute(
                SnapshotValidationRequest(
                    environment_package=lineage.package,
                    runtime_evidence=value.runtime_evidence,
                    runtime_asset_snapshot_manifest=value.runtime_asset_snapshot_manifest,
                    gate_profile=value.gate_profile,
                )
            )
            _verify_recomputed_status(self.artifact_store, value, recomputed.validation_report)
        except (
            ArtifactResolutionError,
            PortableRunReceiptError,
            SnapshotValidationError,
            ValidateV2EvidenceError,
            ValidationError,
            TypeError,
            ValueError,
        ) as error:
            raise _evidence_blocker(
                "validate v2 evidence closure is invalid",
                reason=getattr(error, "reason", type(error).__name__),
            ) from error

        status = recomputed.validation_status
        blockers = (
            ()
            if status is ValidationStatus.PASS
            else (_gate_blocker(status, recomputed.validation_report),)
        )
        publishable = status is ValidationStatus.PASS
        decision = {
            "schema_version": VALIDATION_DECISION_SCHEMA_VERSION,
            "skill_ref": TEXT2ENV_VALIDATE_V2_SKILL_REF,
            "gate_profile": value.gate_profile,
            "policy_dependency": self.policy_dependency.model_dump(mode="json"),
            "input_refs": {
                name: ref.model_dump(mode="json") for name, ref in _named_input_refs(value)
            },
            "validation_report": recomputed.validation_report.model_dump(mode="json"),
            "validation_status": status.value,
            "publishable": publishable,
            "blockers": [blocker.model_dump(mode="json") for blocker in blockers],
            "genesis_profile_satisfied": False,
            "promotion_executed": False,
        }
        decision_ref = _put_json(
            self.artifact_store,
            self.scratch_parent,
            name="validation_decision_receipt",
            schema_version=VALIDATION_DECISION_SCHEMA_VERSION,
            value=decision,
        )
        output = Text2EnvValidateV2Output(
            validation_report=recomputed.validation_report,
            validation_decision_receipt=decision_ref,
            validation_status=status,
            publishable=publishable,
            blockers=blockers,
        )
        context.emit(
            "validate.decision.published",
            artifact_refs=(recomputed.validation_report, decision_ref),
        )
        return HandlerResult(
            output=output,
            artifacts=_canonical_refs(
                (*_input_refs(value), recomputed.validation_report, decision_ref)
            ),
        )


def text2env_validate_v2_descriptor(
    *,
    qualification_artifact: ArtifactRef,
    implementation_sha256: str,
) -> SkillDescriptor:
    return SkillDescriptor(
        skill_id="text2env.validate",
        version="2.0.0",
        mcp_tool_name="text2env_validate_v2_0_0",
        input_schema="harness.text2env_validate_input.v2",
        output_schema="harness.text2env_validate_output.v2",
        implementation_name="self_improving.harness.handlers.text2env_validate_v2",
        implementation_version="2",
        implementation_sha256=implementation_sha256,
        deterministic=True,
        max_attempts=1,
        qualification_artifact=qualification_artifact,
    )


def verify_validate_v2_lineage(
    store: LocalArtifactStore,
    value: Text2EnvValidateV2Input,
) -> _VerifiedLineage:
    """Deeply bind the two durable child runs to one replay evidence set."""

    raw = StrictValidateV2CasReader(store).load(value)
    package = _canonical_model(raw.artifact("environment_package").payload, EnvironmentPackage)
    compile_run = load_portable_run_receipt(store, value.compile_run_receipt)
    replay_run = load_portable_run_receipt(store, value.replay_run_receipt)
    _require_successful_run(compile_run, "text2env.compile@1.0.0")
    _require_successful_run(replay_run, "text2env.replay@1.0.0")
    if (
        compile_run.receipt.qualification != value.compile_qualification
        or replay_run.receipt.qualification != value.replay_qualification
    ):
        raise ValueError("portable run qualifications do not match validate input")

    compile_input = Text2EnvCompileInput.model_validate(
        compile_run.invocation.effective_parameters
    )
    compile_output = Text2EnvCompileOutput.model_validate(compile_run.run_state.output)
    replay_input = Text2EnvReplayInput.model_validate(replay_run.invocation.effective_parameters)
    replay_output = Text2EnvReplayOutput.model_validate(replay_run.run_state.output)
    if compile_output.environment_package != package or replay_input.environment_package != package:
        raise ValueError("compile and replay do not bind the supplied environment package")
    if replay_output.runtime_evidence != value.runtime_evidence:
        raise ValueError("replay output does not bind the supplied runtime evidence")

    replay_artifacts = {_ref_key(ref): ref for ref in replay_run.receipt.artifacts}
    for ref in (
        value.replay_execution_receipt,
        value.runtime_evidence,
        value.runtime_validation_report,
        value.runtime_asset_snapshot_manifest,
        value.runtime_event_transcript,
    ):
        if _ref_key(ref) not in replay_artifacts:
            raise ValueError("validate evidence is absent from the replay run closure")

    execution = _strict_json(raw.artifact("replay_execution_receipt").payload)
    if execution.get("schema_version") != "harness.text2env_replay_receipt.v1":
        raise ValueError("replay execution receipt schema is unsupported")
    expected = {
        "skill_ref": "text2env.replay@1.0.0",
        "environment_package_id": package.package_id,
        "package_manifest_sha256": package.package_manifest.sha256,
        "asset_catalog_sha256": package.asset_catalog.sha256,
        "resolved_scene_sha256": package.resolved_scene_sha256,
        "runtime_config": replay_input.runtime_config.model_dump(mode="json"),
    }
    if any(execution.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError("replay execution receipt does not bind package/runtime configuration")
    for key, ref in (
        ("runtime_evidence", value.runtime_evidence),
        ("runtime_validation_report", value.runtime_validation_report),
        ("transcript", value.runtime_event_transcript),
    ):
        if not _identity_matches(execution.get(key), ref):
            raise ValueError(f"replay execution receipt does not bind {key}")
    runtime_assets = execution.get("runtime_assets")
    if not isinstance(runtime_assets, dict) or not _identity_matches(
        runtime_assets.get("manifest"), value.runtime_asset_snapshot_manifest
    ):
        raise ValueError("replay execution receipt does not bind runtime assets")
    _verify_runtime_asset_members(runtime_assets, replay_artifacts)

    media = _canonical_model(
        raw.artifact("media_verification_receipt").payload,
        _MediaVerificationReceipt,
    )
    if media.verification != execution.get("media_verification"):
        raise ValueError("media verification receipt does not match replay execution")
    _verify_media_members(execution, replay_artifacts)

    provenance = _canonical_model(
        raw.artifact("request_provenance").payload,
        _RequestProvenance,
    )
    if (
        provenance.request != compile_input.request
        or provenance.seed != compile_input.seed
        or provenance.compile_run_id != compile_run.run_state.run_id
        or provenance.compile_invocation_digest != compile_run.invocation.invocation_digest
        or provenance.environment_package != value.environment_package
    ):
        raise ValueError("request provenance does not bind compile and package")
    for nested in (provenance.compile_trusted_receipt, provenance.compile_history_authority):
        store.resolve(nested)
    return _VerifiedLineage(
        package=package,
        compile=compile_run,
        replay=replay_run,
        replay_execution=execution,
        provenance=provenance,
    )


def _require_successful_run(loaded: LoadedPortableRunReceipt, skill_ref: str) -> None:
    if (
        loaded.receipt.skill_ref != skill_ref
        or loaded.receipt.status is not RunStatus.SUCCEEDED
        or loaded.run_state.status is not RunStatus.SUCCEEDED
    ):
        raise ValueError(f"portable run is not a successful {skill_ref} execution")


def _verify_media_members(
    execution: dict[str, Any],
    artifacts: dict[tuple[str, str | None, str], ArtifactRef],
) -> None:
    media = execution.get("media")
    if not isinstance(media, list) or not media:
        raise ValueError("replay execution receipt has no media closure")
    for item in media:
        if not isinstance(item, dict):
            raise ValueError("replay media identity is invalid")
        key = (item.get("sha256"), item.get("schema_version"), item.get("media_type"))
        ref = artifacts.get(key)
        if (
            ref is None
            or ref.bytes != item.get("bytes")
            or not isinstance(item.get("locator"), str)
            or "/" in item["locator"]
            or "\\" in item["locator"]
        ):
            raise ValueError("replay media is absent or inconsistently bound")


def _verify_runtime_asset_members(
    runtime_assets: dict[str, Any],
    artifacts: dict[tuple[str, str | None, str], ArtifactRef],
) -> None:
    members = runtime_assets.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("replay execution receipt has no runtime asset members")
    for item in members:
        if not isinstance(item, dict):
            raise ValueError("replay runtime asset member identity is invalid")
        key = (item.get("sha256"), item.get("schema_version"), item.get("media_type"))
        ref = artifacts.get(key)
        if ref is None or ref.bytes != item.get("bytes") or ref.name != item.get("name"):
            raise ValueError("runtime asset member is absent or inconsistently bound")


def _verify_recomputed_status(
    store: LocalArtifactStore,
    value: Text2EnvValidateV2Input,
    report_ref: ArtifactRef,
) -> None:
    original = _strict_json(store.resolve(value.runtime_validation_report).path.read_bytes())
    recomputed = _strict_json(store.resolve(report_ref).path.read_bytes())
    keys = ("scene_id", "resolved_scene_sha256", "status", "fail_count", "not_run_count")
    if any(original.get(key) != recomputed.get(key) for key in keys):
        raise ValueError("recomputed validation status differs from replay validation")


def _canonical_model(payload: bytes, model: type[HarnessModel]):
    value = model.model_validate_json(payload)
    if payload != _canonical_bytes(value.model_dump(mode="json")):
        raise ValueError("validation evidence JSON is not canonical")
    return value


def _strict_json(payload: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value

    value = json.loads(
        payload.decode("utf-8", errors="strict"),
        object_pairs_hook=unique,
        parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
    )
    if not isinstance(value, dict):
        raise ValueError("validation evidence must be a JSON object")
    return value


def _identity_matches(value: object, ref: ArtifactRef) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        value.get("sha256") == ref.sha256
        and value.get("bytes") == ref.bytes
        and value.get("media_type") == ref.media_type
        and value.get("schema_version") == ref.schema_version
    )


def _gate_blocker(status: ValidationStatus, report: ArtifactRef) -> Blocker:
    return Blocker(
        code=(
            "T2E_VALIDATION_FAILED"
            if status is ValidationStatus.FAIL
            else "T2E_VALIDATION_INCOMPLETE"
        ),
        message="one or more required legacy validation gates did not pass",
        stage="validation.gates",
        retryable=False,
        details={"validation_status": status.value},
        unknowns=(),
        artifact_refs=(report,),
    )


def _evidence_blocker(message: str, *, reason: str = "dependency_mismatch") -> SkillBlocked:
    return SkillBlocked(
        Blocker(
            code="T2E_VALIDATION_EVIDENCE_INVALID",
            message=message,
            stage="validation.evidence",
            retryable=False,
            details={"reason": str(reason)},
            unknowns=(),
            artifact_refs=(),
        )
    )


def _put_json(
    store: LocalArtifactStore,
    scratch_parent: Path,
    *,
    name: str,
    schema_version: str,
    value: object,
) -> ArtifactRef:
    payload = _canonical_bytes(value)
    path = scratch_parent / f".{name}-{hashlib.sha256(payload).hexdigest()}.json"
    try:
        path.write_bytes(payload)
        ref = store.put_file(
            path,
            name=name,
            media_type="application/json",
            schema_version=schema_version,
        )
    finally:
        path.unlink(missing_ok=True)
    return ref


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _named_input_refs(value: Text2EnvValidateV2Input) -> tuple[tuple[str, ArtifactRef], ...]:
    return tuple(
        (name, getattr(value, name))
        for name in type(value).model_fields
        if name != "gate_profile"
    )


def _input_refs(value: Text2EnvValidateV2Input) -> tuple[ArtifactRef, ...]:
    return tuple(ref for _, ref in _named_input_refs(value))


def _ref_key(ref: ArtifactRef) -> tuple[str, str | None, str]:
    return (ref.sha256, ref.schema_version, ref.media_type)


def _canonical_refs(refs: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    by_key = {_ref_key(ref): ref for ref in refs}
    return tuple(by_key[key] for key in sorted(by_key))


__all__ = [
    "Text2EnvValidateV2Handler",
    "text2env_validate_v2_descriptor",
    "verify_validate_v2_lineage",
]
