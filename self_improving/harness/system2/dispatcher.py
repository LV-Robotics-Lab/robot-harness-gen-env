"""Receipt-bound dispatch from a validated System 2 proposal to one Skill.

The language model never receives a callable.  This module replays the planner
evidence closure, matches the selected version to one application-owned Skill,
executes the typed input once, and turns the Registry result into a trusted
``System2ToolResult``.  Compile-derived facts describe only package/catalog
identity; they deliberately do not claim physical validation.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, JsonValue, model_validator

from scene_gen import ResolvedSceneSpec, SceneSpec
from scene_gen.builder import generated_module_source
from scene_gen.catalog import AssetCatalog
from scene_gen.solver import solve_scene
from scene_gen.validator import validate_resolved_scene

from ..application import CompileApplication
from ..artifacts import ArtifactResolutionError, LocalArtifactStore
from ..qualification import QualificationReportV1
from ..registry import _invocation_digest
from ..schema_catalog import schema_model
from ..schemas import (
    ArtifactRef,
    Blocker,
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillDescriptorV2,
    SkillQualification,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
)
from ..schemas.base import CanonicalSkillRef, HarnessModel, JsonObject, SchemaId, Sha256
from .context import (
    PLANNER_CONTEXT_SCHEMA,
    PlannerContext,
    PlannerDecision,
    PlannerHistoryEntry,
    PlannerSkillCard,
    build_planner_prompt,
    compile_planner_context,
    decision_sha256,
    parse_planner_decision,
    verify_planner_context,
)
from .domain import (
    INVOCATION_SCHEMA,
    RUN_STATE_SCHEMA,
    TRUSTED_RECEIPT_SCHEMA,
    WORLD_STATE_SCHEMA,
    StateMutation,
    System2ToolResult,
    TrustedWorldState,
    WorldFact,
    WorldFactEvidence,
    _parse_canonical_model,
    build_state_delta,
    fact_sha256,
)
from .history import (
    PLANNER_HISTORY_AUTHORITY_SCHEMA,
    verify_planner_history_authority,
)
from .planner import (
    EXECUTION_RECEIPT_SCHEMA,
    PlannerExecution,
    PlannerExecutionReceipt,
    PlannerProgressEvent,
)

DERIVED_FACT_CLAIM_SCHEMA = "harness.receipt_derived_fact_claim.v1"
SKILL_EXECUTION_IDENTITY_SCHEMA = "harness.skill_execution_identity.v1"

_CAS_PREFIX = "artifact://sha256/"
_COMPILE_PURPOSE = "Compile an environment package without claiming physical success."
_COMPILE_SKILL_REF = "text2env.compile@1.0.0"
_COMPILE_INPUT_SCHEMA = "harness.text2env_compile_input.v1"
_COMPILE_OUTPUT_SCHEMA = "harness.text2env_compile_output.v1"
_PACKAGE_MEMBER_PATHS = (
    "generated_scene.py",
    "request.txt",
    "resolved_scene.json",
    "scene_spec.json",
)


def _canonical_json_bytes(value: object) -> bytes:
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


def _strict_json_object(payload: bytes, *, label: str) -> dict[str, JsonValue]:
    def reject_duplicate(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains non-finite JSON constant: {value}")

    try:
        value = json.loads(
            payload,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _json_equal(left: object, right: object) -> bool:
    return _canonical_json_bytes(left) == _canonical_json_bytes(right)


def _read_verified_ref(store: LocalArtifactStore, ref: ArtifactRef) -> bytes:
    _require_cas(ref, label=ref.name)
    resolved = store.resolve(ref)
    payload = resolved.path.read_bytes()
    if len(payload) != ref.bytes or hashlib.sha256(payload).hexdigest() != ref.sha256:
        raise ArtifactResolutionError(
            "content_drift",
            "CAS object changed while trusted evidence was read",
        )
    return payload


def _read_verified_digest(store: LocalArtifactStore, digest: str) -> bytes:
    path = store.resolve_digest(digest)
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ArtifactResolutionError(
            "content_drift",
            "CAS digest object changed while trusted evidence was read",
        )
    return payload


def _require_utc(value: datetime, *, label: str) -> None:
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{label} must use UTC")


def _require_cas(ref: ArtifactRef, *, label: str, schema: str | None = None) -> None:
    if ref.uri != f"{_CAS_PREFIX}{ref.sha256}":
        raise ValueError(f"{label} must use its content-addressed artifact URI")
    if schema is not None and ref.schema_version != schema:
        raise ValueError(f"{label} must have schema_version={schema}")


def _artifact_sort_key(ref: ArtifactRef) -> tuple[str, str, str, str]:
    return (ref.sha256, ref.schema_version or "", ref.media_type, ref.name)


def _artifact_identity(ref: ArtifactRef) -> tuple[str, str | None, str]:
    return (ref.media_type, ref.schema_version, ref.sha256)


def _artifact_refs(value: object) -> tuple[ArtifactRef, ...]:
    refs: list[ArtifactRef] = []

    def visit(item: object) -> None:
        if isinstance(item, ArtifactRef):
            refs.append(item)
        elif isinstance(item, BaseModel):
            for name in item.__class__.model_fields:
                visit(getattr(item, name))
        elif isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return tuple(refs)


def _unique_artifacts(refs: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    by_identity: dict[tuple[str, str | None, str], ArtifactRef] = {}
    for ref in refs:
        identity = _artifact_identity(ref)
        prior = by_identity.get(identity)
        if prior is None or _artifact_sort_key(ref) < _artifact_sort_key(prior):
            by_identity[identity] = ref
    return tuple(sorted(by_identity.values(), key=_artifact_sort_key))


def _require_json_ref(ref: ArtifactRef, *, schema: str, label: str) -> None:
    _require_cas(ref, label=label, schema=schema)
    if ref.media_type != "application/json":
        raise ValueError(f"{label} must have media_type=application/json")


def _supporting_member(
    supporting: tuple[ArtifactRef, ...],
    *,
    payload: bytes,
    media_type: str,
    schema_version: str | None,
    label: str,
) -> ArtifactRef:
    digest = hashlib.sha256(payload).hexdigest()
    matches = tuple(
        ref
        for ref in supporting
        if (
            ref.sha256 == digest
            and ref.bytes == len(payload)
            and ref.media_type == media_type
            and ref.schema_version == schema_version
        )
    )
    identities = {_artifact_identity(ref) for ref in matches}
    if len(identities) != 1:
        raise ValueError(f"compile package member is absent or ambiguous: {label}")
    return min(matches, key=_artifact_sort_key)


def _verify_static_validation(
    validation: dict[str, JsonValue],
    *,
    scene_id: str,
    resolved_scene_sha256: str,
) -> None:
    expected_keys = {
        "schema_version",
        "scene_id",
        "resolved_scene_sha256",
        "status",
        "fail_count",
        "not_run_count",
        "checks",
    }
    checks = validation.get("checks")
    if (
        set(validation) != expected_keys
        or validation.get("schema_version") != "robotwin.scene_validation.v1"
        or validation.get("scene_id") != scene_id
        or validation.get("resolved_scene_sha256") != resolved_scene_sha256
        or type(validation.get("fail_count")) is not int
        or type(validation.get("not_run_count")) is not int
        or not isinstance(checks, list)
        or not checks
    ):
        raise ValueError("compile static validation is not bound or has an invalid shape")
    names: list[str] = []
    statuses: list[str] = []
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"name", "status", "evidence"}:
            raise ValueError("compile static validation check has an invalid shape")
        name = check.get("name")
        status = check.get("status")
        if (
            not isinstance(name, str)
            or not name
            or status
            not in {
                "pass",
                "fail",
                "not_run",
                "not_applicable",
            }
        ):
            raise ValueError("compile static validation check has an invalid identity or status")
        names.append(name)
        assert isinstance(status, str)
        statuses.append(status)
    if len(names) != len(set(names)):
        raise ValueError("compile static validation check names must be unique")
    fail_count = statuses.count("fail")
    not_run_count = statuses.count("not_run")
    derived_status = "fail" if fail_count else "incomplete" if not_run_count else "pass"
    if (
        validation["fail_count"] != fail_count
        or validation["not_run_count"] != not_run_count
        or validation.get("status") != derived_status
        or derived_status == "fail"
    ):
        raise ValueError("compile static validation summary contradicts its checks")


def _normalized_static_report(report: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(report)
    raw_checks = report["checks"]
    assert isinstance(raw_checks, list)
    normalized_checks: list[dict[str, object]] = []
    for raw_check in raw_checks:
        assert isinstance(raw_check, dict)
        check = dict(raw_check)
        if str(check.get("name", "")).startswith("real_asset_files:"):
            check["status"] = "not_applicable"
        normalized_checks.append(check)
    normalized["checks"] = normalized_checks
    return normalized


def _compile_derived_fact_claims(
    *,
    store: LocalArtifactStore,
    skill: "SkillExecutionIdentity",
    typed_input: Text2EnvCompileInput,
    output: Text2EnvCompileOutput,
    supporting: tuple[ArtifactRef, ...],
) -> tuple["ReceiptDerivedFactClaim", ...]:
    if (
        skill.skill_ref != _COMPILE_SKILL_REF
        or skill.input_schema != _COMPILE_INPUT_SCHEMA
        or skill.output_schema != _COMPILE_OUTPUT_SCHEMA
    ):
        raise ValueError("compile receipt Skill identity is not the supported contract")
    package = output.environment_package
    if package.producer_skill_ref != skill.skill_ref or package.seed != typed_input.seed:
        raise ValueError("compile package is not bound to the selected Skill input")

    _require_json_ref(output.scene_spec, schema="robotwin.scene_spec.v1", label="scene_spec")
    _require_json_ref(
        output.resolved_scene,
        schema="robotwin.resolved_scene.v1",
        label="resolved_scene",
    )
    _require_json_ref(
        package.asset_catalog,
        schema="robotwin.asset_catalog.v1",
        label="effective asset catalog",
    )
    _require_json_ref(
        package.package_manifest,
        schema="robotwin.generated_scene_package.v1",
        label="package manifest",
    )
    _require_json_ref(
        output.static_validation,
        schema="robotwin.scene_validation.v1",
        label="static validation",
    )
    scene_bytes = _read_verified_ref(store, output.scene_spec)
    resolved_bytes = _read_verified_ref(store, output.resolved_scene)
    catalog_bytes = _read_verified_ref(store, package.asset_catalog)
    manifest_bytes = _read_verified_ref(store, package.package_manifest)
    validation_bytes = _read_verified_ref(store, output.static_validation)
    try:
        scene = SceneSpec.model_validate_json(scene_bytes)
        resolved = ResolvedSceneSpec.model_validate_json(resolved_bytes)
        catalog = AssetCatalog.model_validate_json(catalog_bytes)
    except ValueError as error:
        raise ValueError("compile output contains an invalid typed domain document") from error
    scene_digest = scene.digest()
    resolved_digest = resolved.digest()
    catalog_digest = catalog.digest()
    if (
        scene.request != typed_input.request
        or scene.seed != typed_input.seed
        or resolved.request != scene.request
        or resolved.scene_id != scene.scene_id
        or resolved.seed != scene.seed
        or resolved.frame != scene.frame
        or resolved.workspace != scene.workspace
        or resolved.relations != scene.relations
        or resolved.source_scene_spec_sha256 != scene_digest
        or resolved.asset_catalog_sha256 != catalog_digest
    ):
        raise ValueError("compile SceneSpec, ResolvedSceneSpec, input, and catalog are not bound")
    if resolved != solve_scene(scene, catalog):
        raise ValueError("compile ResolvedSceneSpec differs from the deterministic solver output")
    if (
        package.package_id != resolved_digest
        or package.scene_spec_sha256 != scene_digest
        or package.resolved_scene_sha256 != resolved_digest
        or package.asset_catalog.sha256 != catalog_digest
    ):
        raise ValueError("compile EnvironmentPackage semantic identities are inconsistent")

    manifest = _strict_json_object(manifest_bytes, label="compile package manifest")
    expected_manifest_keys = {
        "schema_version",
        "scene_id",
        "seed",
        "source_scene_spec_sha256",
        "resolved_scene_sha256",
        "asset_catalog_sha256",
        "compiler_version",
        "entrypoint",
        "resolved_only_entrypoint",
        "files",
    }
    if set(manifest) != expected_manifest_keys:
        raise ValueError("compile package manifest has an unexpected top-level shape")
    if (
        manifest["schema_version"] != "robotwin.generated_scene_package.v1"
        or manifest["scene_id"] != scene.scene_id
        or manifest["seed"] != scene.seed
        or manifest["source_scene_spec_sha256"] != scene_digest
        or manifest["resolved_scene_sha256"] != resolved_digest
        or manifest["asset_catalog_sha256"] != catalog_digest
        or manifest["compiler_version"] != resolved.compiler_version
        or manifest["entrypoint"] != "generated_scene.py:load_scene"
        or manifest["resolved_only_entrypoint"]
        != "scene_gen.envs.generated_scene:load_resolved_scene"
    ):
        raise ValueError("compile package manifest semantic identities are not bound")
    files = manifest["files"]
    if not isinstance(files, list):
        raise ValueError("compile package manifest files must be a list")
    records: dict[str, dict[str, JsonValue]] = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}:
            raise ValueError("compile package manifest file record is invalid")
        path = item.get("path")
        if not isinstance(path, str) or path in records:
            raise ValueError("compile package manifest path is invalid or duplicated")
        records[path] = item
    if tuple(sorted(records)) != _PACKAGE_MEMBER_PATHS:
        raise ValueError("compile package manifest does not declare the exact package members")
    expected_members = {
        "request.txt": (scene.request + "\n").encode("utf-8"),
        "scene_spec.json": scene_bytes,
        "resolved_scene.json": resolved_bytes,
        "generated_scene.py": generated_module_source(resolved).encode("utf-8"),
    }
    member_types = {
        "request.txt": ("text/plain", None),
        "scene_spec.json": ("application/json", "robotwin.scene_spec.v1"),
        "resolved_scene.json": ("application/json", "robotwin.resolved_scene.v1"),
        "generated_scene.py": ("text/x-python", None),
    }
    for path, payload in expected_members.items():
        record = records[path]
        digest = hashlib.sha256(payload).hexdigest()
        if record["sha256"] != digest or record["bytes"] != len(payload):
            raise ValueError(f"compile package member record changed identity: {path}")
        media_type, schema_version = member_types[path]
        ref = _supporting_member(
            supporting,
            payload=payload,
            media_type=media_type,
            schema_version=schema_version,
            label=path,
        )
        _read_verified_ref(store, ref)

    validation = _strict_json_object(validation_bytes, label="compile static validation")
    _verify_static_validation(
        validation,
        scene_id=scene.scene_id,
        resolved_scene_sha256=resolved_digest,
    )
    with tempfile.TemporaryDirectory(prefix="system2-static-validation-") as temporary:
        package_root = Path(temporary)
        for path, payload in expected_members.items():
            (package_root / path).write_bytes(payload)
        (package_root / "package_manifest.json").write_bytes(manifest_bytes)
        expected_validation = validate_resolved_scene(
            resolved,
            catalog=None,
            package_root=package_root,
            require_runtime=False,
        )

    if not _json_equal(
        _normalized_static_report(validation),
        _normalized_static_report(expected_validation),
    ):
        raise ValueError("compile report differs from the official static validator")
    return (
        ReceiptDerivedFactClaim(
            operation="upsert",
            key="assets.catalog",
            value=package.asset_catalog.model_dump(mode="json"),
        ),
        ReceiptDerivedFactClaim(
            operation="upsert",
            key="environment.package",
            value=package.model_dump(mode="json"),
        ),
    )


class ReceiptDerivedFactClaim(HarnessModel):
    """One non-physical world-state fact justified by the Skill output."""

    schema_version: Literal["harness.receipt_derived_fact_claim.v1"] = DERIVED_FACT_CLAIM_SCHEMA
    operation: Literal["upsert", "retract"] = "upsert"
    key: Annotated[
        str,
        Field(
            strict=True,
            pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
            max_length=255,
        ),
    ]
    value: JsonValue

    @model_validator(mode="after")
    def operation_has_one_unambiguous_shape(self) -> "ReceiptDerivedFactClaim":
        if self.operation == "retract" and self.value is not None:
            raise ValueError("retract claim value must be null")
        return self


class SkillExecutionIdentity(HarnessModel):
    """Exact qualified implementation selected by the dispatcher."""

    schema_version: Literal["harness.skill_execution_identity.v1"] = SKILL_EXECUTION_IDENTITY_SCHEMA
    skill_ref: CanonicalSkillRef
    input_schema: SchemaId
    output_schema: SchemaId
    implementation_sha256: Sha256
    qualification_artifact: ArtifactRef
    max_attempts: Annotated[int, Field(strict=True, ge=1)]

    @model_validator(mode="after")
    def qualification_is_cas_bound(self) -> "SkillExecutionIdentity":
        _require_cas(
            self.qualification_artifact,
            label="qualification artifact",
            schema="harness.skill_qualification.v1",
        )
        return self


class TrustedToolReceipt(HarnessModel):
    """Terminal evidence joining planning intent to one Registry outcome."""

    schema_version: Literal["harness.trusted_tool_receipt.v1"] = TRUSTED_RECEIPT_SCHEMA
    planner_call_id: UUID
    planner_context_sha256: Sha256
    planner_receipt: ArtifactRef
    planner_decision: ArtifactRef
    planner_decision_sha256: Sha256
    base_state_sha256: Sha256
    base_state: ArtifactRef
    planner_context: ArtifactRef
    history_authority: ArtifactRef | None = None
    skill: SkillExecutionIdentity
    status: RunStatus
    started_at: AwareDatetime
    ended_at: AwareDatetime
    invocation: ArtifactRef | None
    run_state: ArtifactRef
    invocation_digest: Sha256 | None
    typed_output: JsonObject | None
    blocker: Blocker | None
    supporting_artifacts: tuple[ArtifactRef, ...]
    derived_fact_claims: tuple[ReceiptDerivedFactClaim, ...]

    @model_validator(mode="after")
    def receipt_is_terminal_and_canonical(self) -> "TrustedToolReceipt":
        _require_utc(self.started_at, label="started_at")
        _require_utc(self.ended_at, label="ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        _require_cas(
            self.planner_receipt,
            label="planner_receipt",
            schema=EXECUTION_RECEIPT_SCHEMA,
        )
        _require_cas(
            self.planner_decision,
            label="planner_decision",
            schema="harness.planner_decision.v1",
        )
        _require_json_ref(
            self.base_state,
            label="base_state",
            schema=WORLD_STATE_SCHEMA,
        )
        _require_json_ref(
            self.planner_context,
            label="planner_context",
            schema=PLANNER_CONTEXT_SCHEMA,
        )
        if self.history_authority is not None:
            _require_cas(
                self.history_authority,
                label="history_authority",
                schema=PLANNER_HISTORY_AUTHORITY_SCHEMA,
            )
            if self.history_authority.media_type != "application/json":
                raise ValueError("history_authority must have media_type=application/json")
        if self.invocation is not None:
            _require_cas(self.invocation, label="invocation", schema=INVOCATION_SCHEMA)
        _require_cas(self.run_state, label="run_state", schema=RUN_STATE_SCHEMA)
        for artifact in self.supporting_artifacts:
            _require_cas(artifact, label="supporting artifact")
        if list(self.supporting_artifacts) != sorted(
            self.supporting_artifacts,
            key=_artifact_sort_key,
        ):
            raise ValueError("supporting artifacts must be canonically sorted")
        identities = [_artifact_identity(ref) for ref in self.supporting_artifacts]
        if len(identities) != len(set(identities)):
            raise ValueError("supporting artifacts must be unique by content identity")
        keys = [claim.key for claim in self.derived_fact_claims]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("derived fact claims must be sorted and unique")
        if self.status is RunStatus.RUNNING:
            raise ValueError("trusted receipt must be terminal")
        if self.status is RunStatus.SUCCEEDED:
            if (
                self.invocation is None
                or self.invocation_digest is None
                or self.typed_output is None
                or self.blocker is not None
            ):
                raise ValueError("successful trusted receipt is incomplete")
        elif self.typed_output is not None or self.blocker is None:
            raise ValueError("blocked/failed trusted receipt is contradictory")
        if (self.invocation is None) != (self.invocation_digest is None):
            raise ValueError("invocation artifact and digest must be present together")
        if self.status is not RunStatus.SUCCEEDED and self.derived_fact_claims:
            raise ValueError("failed execution cannot publish derived facts")
        return self


@dataclass(frozen=True, slots=True)
class _VerifiedTrustedToolReceipt:
    receipt: TrustedToolReceipt
    run_state: RunState
    invocation: Invocation | None
    typed_input: HarnessModel | None
    typed_output: HarnessModel | None


def verify_trusted_tool_receipt(
    artifact_store: LocalArtifactStore,
    receipt_ref: ArtifactRef,
) -> _VerifiedTrustedToolReceipt:
    """Rebuild and cross-check one complete receipt closure from CAS bytes."""

    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be an exact LocalArtifactStore")
    _require_json_ref(
        receipt_ref,
        schema=TRUSTED_RECEIPT_SCHEMA,
        label="trusted tool receipt",
    )
    receipt_payload = _read_verified_ref(artifact_store, receipt_ref)
    receipt = _parse_canonical_model(
        TrustedToolReceipt,
        receipt_payload,
        label="trusted tool receipt",
    )
    base_state = _parse_canonical_model(
        TrustedWorldState,
        _read_verified_ref(artifact_store, receipt.base_state),
        label="trusted receipt base state",
    )
    planner_context = verify_planner_context(
        _parse_canonical_model(
            PlannerContext,
            _read_verified_ref(artifact_store, receipt.planner_context),
            label="trusted receipt planner context",
        )
    )
    if (
        base_state.state_sha256 != receipt.base_state_sha256
        or planner_context.context_sha256 != receipt.planner_context_sha256
        or planner_context.world_state_sha256 != base_state.state_sha256
        or planner_context.world_state_version != base_state.version
        or planner_context.as_of != base_state.as_of
    ):
        raise ValueError("trusted receipt base state and planner context are not cross-bound")
    if receipt.skill.skill_ref != _COMPILE_SKILL_REF:
        raise ValueError("trusted receipt Skill is not supported by this System 2 verifier")
    requires_history_authority = (
        base_state.version > 1
        or any(fact.source_kind == "trusted_receipt" for fact in base_state.facts)
        or bool(planner_context.history)
        or planner_context.omitted_history_count > 0
    )
    if receipt.history_authority is None:
        if requires_history_authority:
            raise ValueError("trusted receipt requires its complete history authority")
    else:
        if not requires_history_authority:
            raise ValueError("initial trusted receipt must not carry a history authority")
        verified_history = verify_planner_history_authority(
            artifact_store=artifact_store,
            authority_ref=receipt.history_authority,
            current_state=base_state,
        )
        assert verified_history.lineage[-1] == base_state
    qualification = _parse_canonical_model(
        SkillQualification,
        _read_verified_ref(artifact_store, receipt.skill.qualification_artifact),
        label="Skill qualification",
    )
    report = _parse_canonical_model(
        QualificationReportV1,
        _read_verified_digest(artifact_store, qualification.report_sha256),
        label="Skill qualification report",
    )
    if (
        qualification.skill_ref != receipt.skill.skill_ref
        or report.skill_ref != qualification.skill_ref
        or report.deterministic_case_id != qualification.deterministic_case_id
        or report.regression_command != qualification.regression_command
        or report.implementation_sha256 != receipt.skill.implementation_sha256
    ):
        raise ValueError("trusted receipt qualification does not bind the Skill implementation")

    planner_receipt = _parse_canonical_model(
        PlannerExecutionReceipt,
        _read_verified_ref(artifact_store, receipt.planner_receipt),
        label="planner execution receipt",
    )
    decision_payload = _read_verified_ref(artifact_store, receipt.planner_decision)
    decision = _parse_canonical_model(
        PlannerDecision,
        decision_payload,
        label="planner decision",
    )
    if (
        planner_receipt.status != "succeeded"
        or planner_receipt.call_id != receipt.planner_call_id
        or planner_receipt.context_sha256 != receipt.planner_context_sha256
        or planner_receipt.world_state_sha256 != receipt.base_state_sha256
        or planner_receipt.decision != receipt.planner_decision
        or planner_receipt.decision_sha256 != receipt.planner_decision_sha256
        or decision_sha256(decision) != receipt.planner_decision_sha256
        or decision.base_state_sha256 != receipt.base_state_sha256
        or decision.context_sha256 != receipt.planner_context_sha256
        or decision.action != "invoke_skill"
        or decision.skill_ref != receipt.skill.skill_ref
        or planner_receipt.ended_at > receipt.started_at
    ):
        raise ValueError("trusted receipt planner evidence is not cross-bound")
    assert planner_receipt.raw_response is not None
    raw_decision = _parse_canonical_model(
        PlannerDecision,
        _read_verified_ref(artifact_store, planner_receipt.raw_response),
        label="planner raw response",
    )
    if raw_decision != decision:
        raise ValueError("trusted receipt raw planner response differs from its decision")
    if _read_verified_ref(artifact_store, planner_receipt.prompt) != build_planner_prompt(
        planner_context
    ):
        raise ValueError("trusted receipt planner prompt differs from its planner context")

    run_state = _parse_canonical_model(
        RunState,
        _read_verified_ref(artifact_store, receipt.run_state),
        label="Skill RunState",
    )
    if (
        f"{run_state.skill_id}@{run_state.skill_version}" != receipt.skill.skill_ref
        or run_state.status is not receipt.status
        or run_state.started_at != receipt.started_at
        or run_state.ended_at != receipt.ended_at
        or run_state.invocation_digest != receipt.invocation_digest
        or not _json_equal(run_state.output, receipt.typed_output)
        or not _json_equal(
            run_state.blocker.model_dump(mode="json") if run_state.blocker is not None else None,
            receipt.blocker.model_dump(mode="json") if receipt.blocker is not None else None,
        )
        or receipt.supporting_artifacts != _unique_artifacts(run_state.artifacts)
    ):
        raise ValueError("trusted receipt RunState is not cross-bound")
    if run_state.attempt == 0:
        if run_state.max_attempts != 0 or receipt.invocation is not None:
            raise ValueError("preflight receipt has an invalid attempt or Invocation")
    elif run_state.max_attempts != receipt.skill.max_attempts or receipt.invocation is None:
        raise ValueError("execution receipt has an invalid attempt or Invocation")
    declared = {_artifact_identity(ref) for ref in run_state.artifacts}
    required = {
        _artifact_identity(ref)
        for ref in (
            *_artifact_refs(run_state.events),
            *_artifact_refs(run_state.output),
            *_artifact_refs(run_state.blocker),
        )
    }
    if not required.issubset(declared):
        raise ValueError("trusted receipt RunState artifact closure is incomplete")
    for ref in run_state.artifacts:
        _read_verified_ref(artifact_store, ref)

    invocation: Invocation | None = None
    typed_input: HarnessModel | None = None
    if receipt.invocation is not None:
        invocation = _parse_canonical_model(
            Invocation,
            _read_verified_ref(artifact_store, receipt.invocation),
            label="Skill Invocation",
        )
        typed_input = schema_model(receipt.skill.input_schema).model_validate(
            invocation.effective_parameters
        )
        if (
            invocation.run_id != run_state.run_id
            or f"{invocation.skill_id}@{invocation.skill_version}" != receipt.skill.skill_ref
            or invocation.max_attempts != receipt.skill.max_attempts
            or invocation.invocation_digest != receipt.invocation_digest
            or not _json_equal(decision.parameters, invocation.effective_parameters)
            or _invocation_digest(
                skill_id=invocation.skill_id,
                skill_version=invocation.skill_version,
                effective_parameters=typed_input,
                dependencies=invocation.dependencies,
                max_attempts=invocation.max_attempts,
            )
            != invocation.invocation_digest
        ):
            raise ValueError("trusted receipt Invocation is not cross-bound")
        for ref in _artifact_refs(typed_input):
            _read_verified_ref(artifact_store, ref)

    typed_output: HarnessModel | None = None
    expected_claims: tuple[ReceiptDerivedFactClaim, ...] = ()
    if receipt.status is RunStatus.SUCCEEDED:
        assert typed_input is not None and receipt.typed_output is not None
        typed_output = schema_model(receipt.skill.output_schema).model_validate(
            receipt.typed_output
        )
        for ref in _artifact_refs(typed_output):
            _read_verified_ref(artifact_store, ref)
        assert type(typed_input) is Text2EnvCompileInput
        assert type(typed_output) is Text2EnvCompileOutput
        expected_claims = _compile_derived_fact_claims(
            store=artifact_store,
            skill=receipt.skill,
            typed_input=typed_input,
            output=typed_output,
            supporting=receipt.supporting_artifacts,
        )
    if not _json_equal(
        tuple(claim.model_dump(mode="json") for claim in receipt.derived_fact_claims),
        tuple(claim.model_dump(mode="json") for claim in expected_claims),
    ):
        raise ValueError("trusted receipt claims are not re-derived from typed output")
    return _VerifiedTrustedToolReceipt(
        receipt=receipt,
        run_state=run_state,
        invocation=invocation,
        typed_input=typed_input,
        typed_output=typed_output,
    )


class System2DispatchError(RuntimeError):
    """Dispatch stopped at a typed trust boundary before returning a ToolResult."""

    def __init__(self, *, reason: str, cause: Exception) -> None:
        self.reason = reason
        self.cause_type = type(cause).__name__
        super().__init__(f"System 2 dispatch failed: {reason}")


class System2SkillApplication(Protocol):
    """Application-owned executable Skill surface consumed by the dispatcher."""

    @property
    def artifact_root(self) -> Path: ...

    @property
    def descriptor(self) -> RegisteredSkillDescriptor: ...

    @property
    def planner_skill_card(self) -> PlannerSkillCard: ...

    def invoke(self, parameters: HarnessModel) -> RunState: ...

    def invocation(self, run_id: UUID) -> Invocation | None: ...

    def derived_fact_claims(
        self,
        typed_input: HarnessModel,
        output: HarnessModel,
        supporting: tuple[ArtifactRef, ...],
    ) -> tuple[ReceiptDerivedFactClaim, ...]: ...


class CompileSystem2Application:
    """Narrow adapter exposing the fixed compile application to System 2."""

    __slots__ = ("_application", "_test_backend", "_test_store")

    def __init__(self, application: CompileApplication) -> None:
        if type(application) is not CompileApplication:
            raise TypeError("compile adapter requires an exact CompileApplication")
        application._assert_production_assembly()
        self._application: CompileApplication | None = application
        self._test_backend: object | None = None
        self._test_store: LocalArtifactStore | None = None

    @classmethod
    def _for_testing(
        cls,
        *,
        backend: object,
        artifact_store: LocalArtifactStore,
    ) -> "CompileSystem2Application":
        """Construct the internal fake adapter used only by dispatcher contract tests."""

        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("test adapter requires an exact LocalArtifactStore")
        instance = object.__new__(cls)
        instance._application = None
        instance._test_backend = backend
        instance._test_store = artifact_store
        return instance

    def _backend(self) -> object:
        if self._application is not None:
            self._application._assert_production_assembly()
            return self._application
        assert self._test_backend is not None
        return self._test_backend

    def _store(self) -> LocalArtifactStore:
        if self._application is not None:
            self._application._assert_production_assembly()
            return self._application._artifact_store
        assert self._test_store is not None
        return self._test_store

    @property
    def artifact_root(self) -> Path:
        if self._application is None and self._test_store is not None:
            return self._test_store.root
        return Path(getattr(self._backend(), "artifact_root"))

    @property
    def descriptor(self) -> RegisteredSkillDescriptor:
        skills = getattr(self._backend(), "skills")
        if len(skills) != 1:
            raise ValueError("compile application must expose only text2env.compile")
        descriptor = skills[0]
        if type(descriptor) in {SkillDescriptor, SkillDescriptorV2}:
            if descriptor.skill_id != "text2env.compile":
                raise ValueError("compile application must expose only text2env.compile")
        return descriptor

    def invoke(self, parameters: HarnessModel) -> RunState:
        if type(parameters) is not Text2EnvCompileInput:
            raise TypeError("compile application requires Text2EnvCompileInput")
        result = getattr(self._backend(), "invoke_typed")(parameters)
        if type(result) is not RunState:
            raise TypeError("compile application returned a non-RunState result")
        return result

    def invocation(self, run_id: UUID) -> Invocation | None:
        result = getattr(self._backend(), "invocation")(run_id)
        if result is not None and type(result) is not Invocation:
            raise TypeError("compile application returned a non-Invocation result")
        return result

    @property
    def planner_skill_card(self) -> PlannerSkillCard:
        descriptor = self.descriptor
        return PlannerSkillCard(
            skill_ref=f"{descriptor.skill_id}@{descriptor.version}",
            purpose=_COMPILE_PURPOSE,
            input_schema=descriptor.input_schema,
            output_schema=descriptor.output_schema,
            qualification_sha256=descriptor.qualification_artifact.sha256,
            max_attempts=descriptor.max_attempts,
        )

    def derived_fact_claims(
        self,
        typed_input: HarnessModel,
        output: HarnessModel,
        supporting: tuple[ArtifactRef, ...],
    ) -> tuple[ReceiptDerivedFactClaim, ...]:
        if type(typed_input) is not Text2EnvCompileInput:
            raise TypeError("compile facts require Text2EnvCompileInput")
        if type(output) is not Text2EnvCompileOutput:
            raise TypeError("compile facts require Text2EnvCompileOutput")
        descriptor = self.descriptor
        return _compile_derived_fact_claims(
            store=self._store(),
            skill=SkillExecutionIdentity(
                skill_ref=f"{descriptor.skill_id}@{descriptor.version}",
                input_schema=descriptor.input_schema,
                output_schema=descriptor.output_schema,
                implementation_sha256=descriptor.implementation_sha256,
                qualification_artifact=descriptor.qualification_artifact,
                max_attempts=descriptor.max_attempts,
            ),
            typed_input=typed_input,
            output=output,
            supporting=supporting,
        )


class System2Dispatcher:
    """Execute one validated ``invoke_skill`` decision and publish its closure."""

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        scratch_root: Path,
        applications: tuple[System2SkillApplication, ...],
    ) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be LocalArtifactStore")
        if not applications:
            raise ValueError("at least one System 2 Skill application is required")
        self._store = artifact_store
        self._scratch_root = scratch_root.expanduser().resolve()
        self._applications: dict[str, System2SkillApplication] = {}
        self._descriptors: dict[str, RegisteredSkillDescriptor] = {}
        for application in applications:
            if type(application) is not CompileSystem2Application:
                raise TypeError(
                    "System 2 currently accepts only an exact CompileSystem2Application"
                )
            if application.artifact_root.expanduser().resolve() != artifact_store.root:
                raise ValueError("every Skill application must use the dispatcher CAS")
            descriptor = self._validate_descriptor(application.descriptor)
            skill_ref = f"{descriptor.skill_id}@{descriptor.version}"
            if skill_ref in self._applications:
                raise ValueError(f"duplicate System 2 Skill application: {skill_ref}")
            self._verify_qualification(descriptor)
            self._applications[skill_ref] = application
            self._descriptors[skill_ref] = descriptor

    def dispatch(
        self,
        *,
        planning: PlannerExecution,
        context: PlannerContext,
        state: TrustedWorldState,
        history_authority: ArtifactRef | None = None,
    ) -> System2ToolResult:
        try:
            trusted_state = TrustedWorldState.model_validate(state.model_dump(mode="python"))
            trusted_context = verify_planner_context(context)
            self._verify_context_state(
                trusted_context,
                trusted_state,
                history_authority=history_authority,
            )
            decision = self._verify_planning(planning, trusted_context, trusted_state)
        except Exception as error:
            raise System2DispatchError(
                reason="planner_evidence_invalid",
                cause=error,
            ) from error
        assert decision.skill_ref is not None and decision.parameters is not None
        application = self._applications.get(decision.skill_ref)
        descriptor = self._descriptors.get(decision.skill_ref)
        if application is None or descriptor is None:
            raise System2DispatchError(
                reason="skill_not_available",
                cause=KeyError(decision.skill_ref),
            )
        try:
            self._verify_context_applications(trusted_context)
            current_descriptor = self._validate_descriptor(application.descriptor)
            if current_descriptor != descriptor:
                raise ValueError("application descriptor changed after assembly")
            self._verify_qualification(descriptor)
            typed_input = schema_model(descriptor.input_schema).model_validate(decision.parameters)
            for ref in _artifact_refs(typed_input):
                self._resolve_ref(ref)
        except Exception as error:
            raise System2DispatchError(
                reason="skill_identity_mismatch",
                cause=error,
            ) from error
        try:
            run_state = application.invoke(typed_input)
        except Exception as error:
            raise System2DispatchError(
                reason="skill_execution_failed",
                cause=error,
            ) from error
        try:
            return self._package_execution(
                planning=planning,
                context=trusted_context,
                state=trusted_state,
                application=application,
                descriptor=descriptor,
                typed_input=typed_input,
                run_state=run_state,
                history_authority=history_authority,
            )
        except Exception as error:
            raise System2DispatchError(
                reason="skill_execution_invalid",
                cause=error,
            ) from error

    def _verify_planning(
        self,
        planning: PlannerExecution,
        context: PlannerContext,
        state: TrustedWorldState,
    ) -> PlannerDecision:
        if type(planning) is not PlannerExecution:
            raise TypeError("planning must be PlannerExecution")
        _require_cas(
            planning.receipt_ref,
            label="planner receipt",
            schema=EXECUTION_RECEIPT_SCHEMA,
        )
        if build_planner_prompt(context) != self._read_ref(planning.prompt_ref):
            raise ValueError("planner prompt artifact does not match the trusted context")
        raw_response = self._read_ref(planning.raw_response_ref)
        decision = PlannerDecision.model_validate(planning.decision.model_dump(mode="python"))
        if parse_planner_decision(raw_response, context=context) != decision:
            raise ValueError("planner raw response does not match the retained decision")
        decision_bytes = _canonical_json_bytes(decision.model_dump(mode="json"))
        if decision_bytes != self._read_ref(planning.decision_ref):
            raise ValueError("planner decision artifact does not match the retained decision")
        digest = decision_sha256(decision)
        if digest != planning.decision_sha256:
            raise ValueError("planner decision digest mismatch")
        receipt = PlannerExecutionReceipt.model_validate(planning.receipt.model_dump(mode="python"))
        receipt_bytes = _canonical_json_bytes(receipt.model_dump(mode="json"))
        if receipt_bytes != self._read_ref(planning.receipt_ref):
            raise ValueError("planner receipt artifact does not match the retained receipt")
        if (
            receipt.status != "succeeded"
            or receipt.call_id != planning.call_id
            or receipt.world_state_sha256 != state.state_sha256
            or receipt.context_sha256 != context.context_sha256
            or receipt.prompt != planning.prompt_ref
            or receipt.raw_response != planning.raw_response_ref
            or receipt.decision != planning.decision_ref
            or receipt.decision_sha256 != digest
        ):
            raise ValueError("planner execution receipt is not cross-bound")
        events = tuple(
            PlannerProgressEvent.model_validate(event.model_dump(mode="python"))
            for event in planning.events
        )
        if [event.seq for event in events] != list(range(1, len(events) + 1)):
            raise ValueError("planner progress sequence is not canonical")
        if any(event.call_id != planning.call_id for event in events):
            raise ValueError("planner progress event belongs to another call")
        if any(right.timestamp < left.timestamp for left, right in zip(events, events[1:])):
            raise ValueError("planner progress timestamps are not monotonic")
        if not events or events[-1].stage != "receipt.published":
            raise ValueError("planner progress has no terminal receipt publication")
        if events[-1].artifact_refs != (planning.receipt_ref,):
            raise ValueError("terminal planner event does not publish the receipt")
        if tuple(event.stage for event in events[:-1]) != receipt.event_stages:
            raise ValueError("planner receipt does not bind its prior progress stages")
        if planning.observer_failure_stages != receipt.observer_failure_stages:
            raise ValueError("planner observer failure record drifted")
        for event in events:
            for ref in event.artifact_refs:
                self._resolve_ref(ref)
        if decision.action != "invoke_skill":
            raise ValueError("dispatcher only accepts invoke_skill decisions")
        return decision

    def _verify_context_state(
        self,
        context: PlannerContext,
        state: TrustedWorldState,
        *,
        history_authority: ArtifactRef | None,
    ) -> None:
        for fact in state.facts:
            if fact.source_kind == "trusted_receipt":
                receipt = verify_trusted_tool_receipt(
                    self._store,
                    fact.source_artifact,
                ).receipt
                if receipt.status is not RunStatus.SUCCEEDED or not any(
                    claim.operation == "upsert"
                    and claim.key == fact.key
                    and _json_equal(claim.value, fact.value)
                    for claim in receipt.derived_fact_claims
                ):
                    raise ValueError(f"world fact is not justified by its receipt: {fact.key}")
            else:
                if fact.source_kind == "derived":
                    raise ValueError("derived facts require a re-computable derivation receipt")
                _require_json_ref(
                    fact.source_artifact,
                    schema="harness.world_fact_evidence.v1",
                    label=f"world fact evidence {fact.key}",
                )
                evidence = _parse_canonical_model(
                    WorldFactEvidence,
                    self._read_ref(fact.source_artifact),
                    label=f"world fact evidence {fact.key}",
                )
                if (
                    evidence.source_kind != fact.source_kind
                    or evidence.key != fact.key
                    or not _json_equal(evidence.value, fact.value)
                    or evidence.observed_at != fact.observed_at
                    or evidence.valid_until != fact.valid_until
                ):
                    raise ValueError(f"world fact source envelope drifted: {fact.key}")
        requires_history_authority = (
            state.version > 1
            or any(fact.source_kind == "trusted_receipt" for fact in state.facts)
            or bool(context.history)
            or context.omitted_history_count > 0
        )
        if history_authority is None:
            if requires_history_authority:
                raise ValueError("planner context requires its complete history authority")
            complete_history: tuple[PlannerHistoryEntry, ...] = ()
        else:
            if not requires_history_authority:
                raise ValueError(
                    "initial planner state must not carry a redundant history authority"
                )
            verified_history = verify_planner_history_authority(
                artifact_store=self._store,
                authority_ref=history_authority,
                current_state=state,
            )
            complete_history = verified_history.entries
        projected = compile_planner_context(
            state=state,
            skills=context.skills,
            history=complete_history,
            required_fact_keys=context.required_fact_keys,
            budget=context.budget,
        )
        if projected != context:
            raise ValueError("planner context cannot be reproduced from current world state")

    def _verify_context_applications(self, context: PlannerContext) -> None:
        expected_cards = tuple(
            sorted(
                (application.planner_skill_card for application in self._applications.values()),
                key=lambda card: card.skill_ref,
            )
        )
        if context.skills != expected_cards:
            raise ValueError("planner context Skill cards do not match available applications")

    @staticmethod
    def _validate_descriptor(
        descriptor: RegisteredSkillDescriptor,
    ) -> RegisteredSkillDescriptor:
        if type(descriptor) is SkillDescriptor:
            return SkillDescriptor.model_validate(descriptor.model_dump(mode="python"))
        if type(descriptor) is SkillDescriptorV2:
            return SkillDescriptorV2.model_validate(descriptor.model_dump(mode="python"))
        raise TypeError("application descriptor must be SkillDescriptor v1 or v2")

    def _verify_qualification(self, descriptor: RegisteredSkillDescriptor) -> None:
        payload = self._read_ref(descriptor.qualification_artifact)
        qualification = _parse_canonical_model(
            SkillQualification,
            payload,
            label="Skill qualification",
        )
        if qualification.skill_ref != f"{descriptor.skill_id}@{descriptor.version}":
            raise ValueError("qualification does not match the executable Skill")
        report = _parse_canonical_model(
            QualificationReportV1,
            _read_verified_digest(self._store, qualification.report_sha256),
            label="Skill qualification report",
        )
        if (
            report.skill_ref != qualification.skill_ref
            or report.deterministic_case_id != qualification.deterministic_case_id
            or report.regression_command != qualification.regression_command
            or report.implementation_sha256 != descriptor.implementation_sha256
        ):
            raise ValueError("qualification report does not bind the executable implementation")

    def _package_execution(
        self,
        *,
        planning: PlannerExecution,
        context: PlannerContext,
        state: TrustedWorldState,
        application: System2SkillApplication,
        descriptor: RegisteredSkillDescriptor,
        typed_input: HarnessModel,
        run_state: RunState,
        history_authority: ArtifactRef | None,
    ) -> System2ToolResult:
        trusted_run = RunState.model_validate(run_state.model_dump(mode="python"))
        if trusted_run.status is RunStatus.RUNNING or trusted_run.ended_at is None:
            raise ValueError("Skill returned a non-terminal RunState")
        if (
            trusted_run.skill_id != descriptor.skill_id
            or trusted_run.skill_version != descriptor.version
            or trusted_run.started_at < state.as_of
            or trusted_run.started_at < planning.receipt.ended_at
        ):
            raise ValueError("RunState identity or time window is invalid")
        if trusted_run.attempt == 0:
            if trusted_run.max_attempts != 0:
                raise ValueError("preflight RunState max_attempts must be zero")
        elif trusted_run.max_attempts != descriptor.max_attempts:
            raise ValueError("RunState max_attempts does not match the descriptor")
        closure_refs = (
            *trusted_run.artifacts,
            *_artifact_refs(trusted_run.events),
            *_artifact_refs(trusted_run.blocker),
        )
        for ref in closure_refs:
            self._resolve_ref(ref)
        invocation = application.invocation(trusted_run.run_id)
        trusted_invocation: Invocation | None = None
        if invocation is not None:
            trusted_invocation = Invocation.model_validate(invocation.model_dump(mode="python"))
        if trusted_run.attempt == 0:
            if trusted_invocation is not None:
                raise ValueError("preflight RunState cannot have an Invocation")
        elif trusted_invocation is None:
            raise ValueError("execution RunState has no Invocation")
        typed_output: HarnessModel | None = None
        if trusted_run.status is RunStatus.SUCCEEDED:
            typed_output = schema_model(descriptor.output_schema).model_validate(trusted_run.output)
            for ref in _artifact_refs(typed_output):
                self._resolve_ref(ref)
        declared_identities = {_artifact_identity(ref) for ref in trusted_run.artifacts}
        required_identities = {
            _artifact_identity(ref)
            for ref in (
                *_artifact_refs(trusted_run.events),
                *_artifact_refs(typed_output),
                *_artifact_refs(trusted_run.blocker),
            )
        }
        if not required_identities.issubset(declared_identities):
            raise ValueError("RunState artifact closure is incomplete")
        if trusted_invocation is not None:
            self._verify_invocation(
                trusted_invocation,
                trusted_run,
                descriptor,
                typed_input,
            )
        invocation_ref = (
            self._publish_model(
                trusted_invocation,
                name="skill_invocation",
                schema_version=INVOCATION_SCHEMA,
            )
            if trusted_invocation is not None
            else None
        )
        run_state_ref = self._publish_model(
            trusted_run,
            name="run_state",
            schema_version=RUN_STATE_SCHEMA,
        )
        base_state_ref = self._publish_model(
            state,
            name="base_world_state",
            schema_version=WORLD_STATE_SCHEMA,
        )
        planner_context_ref = self._publish_model(
            context,
            name="planner_context",
            schema_version=PLANNER_CONTEXT_SCHEMA,
        )
        supporting = _unique_artifacts(trusted_run.artifacts)
        claims = (
            application.derived_fact_claims(typed_input, typed_output, supporting)
            if typed_output is not None
            else ()
        )
        claims = tuple(
            ReceiptDerivedFactClaim.model_validate(claim.model_dump(mode="python"))
            for claim in claims
        )
        if list(claims) != sorted(claims, key=lambda claim: claim.key):
            raise ValueError("application derived facts are not sorted")
        receipt = TrustedToolReceipt(
            planner_call_id=planning.call_id,
            planner_context_sha256=planning.receipt.context_sha256,
            planner_receipt=planning.receipt_ref,
            planner_decision=planning.decision_ref,
            planner_decision_sha256=planning.decision_sha256,
            base_state_sha256=state.state_sha256,
            base_state=base_state_ref,
            planner_context=planner_context_ref,
            history_authority=history_authority,
            skill=SkillExecutionIdentity(
                skill_ref=f"{descriptor.skill_id}@{descriptor.version}",
                input_schema=descriptor.input_schema,
                output_schema=descriptor.output_schema,
                implementation_sha256=descriptor.implementation_sha256,
                qualification_artifact=descriptor.qualification_artifact,
                max_attempts=descriptor.max_attempts,
            ),
            status=trusted_run.status,
            started_at=trusted_run.started_at,
            ended_at=trusted_run.ended_at,
            invocation=invocation_ref,
            run_state=run_state_ref,
            invocation_digest=trusted_run.invocation_digest,
            typed_output=(
                typed_output.model_dump(mode="json") if typed_output is not None else None
            ),
            blocker=trusted_run.blocker,
            supporting_artifacts=supporting,
            derived_fact_claims=claims,
        )
        receipt_ref = self._publish_model(
            receipt,
            name="trusted_tool_receipt",
            schema_version=TRUSTED_RECEIPT_SCHEMA,
        )
        verify_trusted_tool_receipt(self._store, receipt_ref)
        facts = tuple(
            WorldFact(
                key=claim.key,
                value=claim.value,
                source_kind="trusted_receipt",
                source_artifact=receipt_ref,
                observed_at=trusted_run.ended_at,
                valid_until=None,
            )
            for claim in claims
        )
        current = {fact.key: fact for fact in state.facts}
        mutations = tuple(
            StateMutation(
                operation="upsert",
                key=fact.key,
                expected_fact_sha256=(
                    fact_sha256(current[fact.key]) if fact.key in current else None
                ),
                fact=fact,
            )
            for fact in facts
        )
        delta = (
            build_state_delta(
                base_state_sha256=state.state_sha256,
                effective_at=trusted_run.ended_at,
                receipt=receipt_ref,
                mutations=mutations,
            )
            if trusted_run.status is RunStatus.SUCCEEDED
            else None
        )
        output_identities = {_artifact_identity(ref) for ref in _artifact_refs(typed_output)}
        diagnostics = tuple(
            ref for ref in supporting if _artifact_identity(ref) not in output_identities
        )
        return System2ToolResult(
            status=trusted_run.status,
            started_at=trusted_run.started_at,
            ended_at=trusted_run.ended_at,
            invocation=invocation_ref,
            run_state=run_state_ref,
            typed_output=(
                typed_output.model_dump(mode="json") if typed_output is not None else None
            ),
            state_delta=delta,
            diagnostics=diagnostics,
            trusted_receipt=receipt_ref,
            fresh_observations=(),
            blocker=trusted_run.blocker,
        )

    @staticmethod
    def _verify_invocation(
        invocation: Invocation,
        run_state: RunState,
        descriptor: RegisteredSkillDescriptor,
        typed_input: HarnessModel,
    ) -> None:
        if (
            invocation.run_id != run_state.run_id
            or invocation.skill_id != descriptor.skill_id
            or invocation.skill_version != descriptor.version
            or invocation.max_attempts != descriptor.max_attempts
            or invocation.effective_parameters != typed_input.model_dump(mode="json")
            or invocation.invocation_digest != run_state.invocation_digest
        ):
            raise ValueError("Invocation does not match the selected Skill or RunState")
        wanted = _invocation_digest(
            skill_id=invocation.skill_id,
            skill_version=invocation.skill_version,
            effective_parameters=typed_input,
            dependencies=invocation.dependencies,
            max_attempts=invocation.max_attempts,
        )
        if wanted != invocation.invocation_digest:
            raise ValueError("Invocation digest does not match its content identity")

    def _publish_model(
        self,
        model: HarnessModel,
        *,
        name: str,
        schema_version: str,
    ) -> ArtifactRef:
        return self._publish_bytes(
            _canonical_json_bytes(model.model_dump(mode="json")),
            name=name,
            schema_version=schema_version,
        )

    def _publish_bytes(
        self,
        payload: bytes,
        *,
        name: str,
        schema_version: str,
    ) -> ArtifactRef:
        self._scratch_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._scratch_root,
            prefix=".tool-result.",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            ref = self._store.put_file(
                temporary,
                name=name,
                media_type="application/json",
                schema_version=schema_version,
            )
        finally:
            temporary.unlink(missing_ok=True)
        if self._read_ref(ref) != payload:
            raise RuntimeError("published ToolResult artifact changed bytes")
        return ref

    def _resolve_ref(self, ref: ArtifactRef) -> Path:
        _require_cas(ref, label=ref.name)
        return self._store.resolve(ref).path

    def _read_ref(self, ref: ArtifactRef) -> bytes:
        return _read_verified_ref(self._store, ref)
