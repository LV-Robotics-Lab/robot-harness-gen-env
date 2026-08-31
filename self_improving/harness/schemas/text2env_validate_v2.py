"""CAS-only input and decision records for ``text2env.validate@2.0.0``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import GetJsonSchemaHandler, StrictBool, model_validator

from .base import HarnessModel, public_schema_config
from .common import ArtifactRef, Blocker, ValidationStatus

TEXT2ENV_VALIDATE_V2_INPUT_SCHEMA_ID = "harness.text2env_validate_input.v2"
TEXT2ENV_VALIDATE_V2_OUTPUT_SCHEMA_ID = "harness.text2env_validate_output.v2"
VALIDATION_DECISION_SCHEMA_VERSION = "harness.text2env_validation_decision.v2"
VALIDATE_V2_GATE_PROFILE = "robotwin.scene_validation.v1"
_CAS_URI_PATTERN = r"^artifact://sha256/[0-9a-f]{64}$"


@dataclass(frozen=True, slots=True)
class _ArtifactContract:
    schema_version: str
    media_type: str = "application/json"


_INPUT_ARTIFACT_CONTRACTS = {
    "environment_package": _ArtifactContract("harness.environment_package.v1"),
    "compile_run_receipt": _ArtifactContract("harness.portable_run_receipt.v1"),
    "compile_qualification": _ArtifactContract("harness.skill_qualification.v1"),
    "replay_run_receipt": _ArtifactContract("harness.portable_run_receipt.v1"),
    "replay_qualification": _ArtifactContract("harness.skill_qualification.v1"),
    "replay_execution_receipt": _ArtifactContract("harness.text2env_replay_receipt.v1"),
    "runtime_evidence": _ArtifactContract("robotwin.scene_runtime_evidence.v2"),
    "runtime_validation_report": _ArtifactContract("robotwin.scene_validation.v1"),
    "runtime_asset_snapshot_manifest": _ArtifactContract("harness.runtime_asset_snapshot.v1"),
    "media_verification_receipt": _ArtifactContract("harness.replay_media_verification.v1"),
    "request_provenance": _ArtifactContract("harness.text2env_request_provenance.v1"),
    "runtime_event_transcript": _ArtifactContract(
        "harness.runtime_event_transcript.v1",
        "application/x-ndjson",
    ),
}
_OUTPUT_ARTIFACT_CONTRACTS = {
    "validation_report": _ArtifactContract("robotwin.scene_validation.v1"),
    "validation_decision_receipt": _ArtifactContract(VALIDATION_DECISION_SCHEMA_VERSION),
}
_CAS_URI_LENGTH = len("artifact://sha256/") + 64


def _cas_uri_json_schema() -> dict[str, int | str]:
    return {
        "maxLength": _CAS_URI_LENGTH,
        "minLength": _CAS_URI_LENGTH,
        "pattern": _CAS_URI_PATTERN,
    }


def _require_canonical_artifact_ref(
    field_name: str,
    ref: ArtifactRef,
    contract: _ArtifactContract,
) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError(f"{field_name}.uri must be the canonical CAS locator")
    if ref.schema_version != contract.schema_version:
        raise ValueError(f"{field_name}.schema_version must be {contract.schema_version!r}")
    if ref.media_type != contract.media_type:
        raise ValueError(f"{field_name}.media_type must be {contract.media_type!r}")


def _require_canonical_cas_locator(field_name: str, ref: ArtifactRef) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError(f"{field_name}.uri must be the canonical CAS locator")


def _express_artifact_contracts(
    schema: dict[str, Any],
    contracts: dict[str, _ArtifactContract],
) -> dict[str, Any]:
    properties = schema["properties"]
    for field_name, contract in contracts.items():
        field_schema = properties[field_name]
        artifact_ref = field_schema.pop("$ref")
        field_schema["allOf"] = [
            {"$ref": artifact_ref},
            {
                "properties": {
                    "uri": _cas_uri_json_schema(),
                    "media_type": {"const": contract.media_type},
                    "schema_version": {"const": contract.schema_version},
                },
                "type": "object",
            },
        ]
    return schema


def _express_blocker_cas_contract(schema: dict[str, Any]) -> dict[str, Any]:
    blocker_items = schema["properties"]["blockers"]["items"]
    blocker_ref = blocker_items.pop("$ref")
    blocker_items["allOf"] = [
        {"$ref": blocker_ref},
        {
            "properties": {
                "artifact_refs": {
                    "items": {
                        "properties": {"uri": _cas_uri_json_schema()},
                        "type": "object",
                    },
                    "type": "array",
                }
            },
            "type": "object",
        },
    ]
    return schema


class Text2EnvValidateV2Input(HarnessModel):
    """The complete named evidence set required before validation can begin."""

    model_config = public_schema_config(TEXT2ENV_VALIDATE_V2_INPUT_SCHEMA_ID)

    environment_package: ArtifactRef
    compile_run_receipt: ArtifactRef
    compile_qualification: ArtifactRef
    replay_run_receipt: ArtifactRef
    replay_qualification: ArtifactRef
    replay_execution_receipt: ArtifactRef
    runtime_evidence: ArtifactRef
    runtime_validation_report: ArtifactRef
    runtime_asset_snapshot_manifest: ArtifactRef
    media_verification_receipt: ArtifactRef
    request_provenance: ArtifactRef
    runtime_event_transcript: ArtifactRef
    gate_profile: Literal["robotwin.scene_validation.v1"] = VALIDATE_V2_GATE_PROFILE

    @model_validator(mode="after")
    def evidence_refs_are_typed_cas_objects(self) -> "Text2EnvValidateV2Input":
        for field_name, contract in _INPUT_ARTIFACT_CONTRACTS.items():
            _require_canonical_artifact_ref(field_name, getattr(self, field_name), contract)
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        return _express_artifact_contracts(handler(core_schema), _INPUT_ARTIFACT_CONTRACTS)


class Text2EnvValidateV2Output(HarnessModel):
    """A physical decision plus the receipt that makes the decision auditable."""

    model_config = public_schema_config(TEXT2ENV_VALIDATE_V2_OUTPUT_SCHEMA_ID)

    validation_report: ArtifactRef
    validation_decision_receipt: ArtifactRef
    validation_status: ValidationStatus
    publishable: StrictBool
    blockers: tuple[Blocker, ...]

    @model_validator(mode="after")
    def decision_is_typed_and_consistent(self) -> "Text2EnvValidateV2Output":
        for field_name, contract in _OUTPUT_ARTIFACT_CONTRACTS.items():
            _require_canonical_artifact_ref(field_name, getattr(self, field_name), contract)
        for blocker_index, blocker in enumerate(self.blockers):
            for ref_index, ref in enumerate(blocker.artifact_refs):
                _require_canonical_cas_locator(
                    f"blockers[{blocker_index}].artifact_refs[{ref_index}]",
                    ref,
                )
        if self.publishable:
            if self.validation_status != ValidationStatus.PASS or self.blockers:
                raise ValueError(
                    "publishable output requires validation_status=pass and no blockers"
                )
        elif not self.blockers:
            raise ValueError("non-publishable output requires at least one blocker")
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        schema = _express_artifact_contracts(handler(core_schema), _OUTPUT_ARTIFACT_CONTRACTS)
        return _express_blocker_cas_contract(schema)


__all__ = [
    "TEXT2ENV_VALIDATE_V2_INPUT_SCHEMA_ID",
    "TEXT2ENV_VALIDATE_V2_OUTPUT_SCHEMA_ID",
    "VALIDATION_DECISION_SCHEMA_VERSION",
    "VALIDATE_V2_GATE_PROFILE",
    "Text2EnvValidateV2Input",
    "Text2EnvValidateV2Output",
]
