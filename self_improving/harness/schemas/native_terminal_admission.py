"""Portable evidence that an exact native production terminal was deeply reread."""

import json
from typing import Literal

from pydantic import UUID4, Field, model_validator

from .base import HarnessModel, Sha256, public_schema_config
from .common import ArtifactRef, Invocation, RunState

NATIVE_READ_CLOSURE_ID = "harness.native_read_closure.v1"
NATIVE_TERMINAL_ADMISSION_ID = "harness.native_terminal_admission.v1"
NativeSkill = Literal[
    "image2env.compile@2.0.0",
    "image2env.replay@2.0.0",
    "genesis.observe@3.0.0",
    "image2env.validate@2.0.0",
]


class NativeDigestMemberV1(HarnessModel):
    sha256: Sha256
    bytes: int = Field(strict=True, ge=0)


class NativeReadClosureV1(HarnessModel):
    model_config = public_schema_config(NATIVE_READ_CLOSURE_ID)
    schema_version: Literal[NATIVE_READ_CLOSURE_ID]
    artifacts: tuple[ArtifactRef, ...] = Field(min_length=1)
    digest_members: tuple[NativeDigestMemberV1, ...]
    scope: Literal["actual_public_deep_read_accesses"]

    @model_validator(mode="after")
    def canonical_members(self):
        keys = [
            json.dumps(ref.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            for ref in self.artifacts
        ]
        if keys != sorted(set(keys)) or any(
            ref.uri != f"artifact://sha256/{ref.sha256}" for ref in self.artifacts
        ):
            raise ValueError("read artifacts require sorted unique exact CAS references")
        digests = [member.sha256 for member in self.digest_members]
        if digests != sorted(set(digests)):
            raise ValueError("digest members require sorted unique identities")
        return self


class NativeTerminalAdmissionV1(HarnessModel):
    model_config = public_schema_config(NATIVE_TERMINAL_ADMISSION_ID)
    schema_version: Literal[NATIVE_TERMINAL_ADMISSION_ID]
    skill_ref: NativeSkill
    run_id: UUID4
    invocation: Invocation
    run_state: RunState
    controlled_intent_sha256: Sha256
    native_receipt: ArtifactRef
    qualification_admission: ArtifactRef
    application_implementation_sha256: Sha256
    admission_implementation: ArtifactRef
    read_closure: ArtifactRef
    predecessor_terminal_admission: ArtifactRef | None
    authority: Literal["observed_successful_public_deep_read"]
    simulator_rerun: Literal[False]

    @model_validator(mode="after")
    def exact_subject(self):
        if (self.skill_ref == "genesis.observe@3.0.0") != (
            self.predecessor_terminal_admission is not None
        ):
            raise ValueError("fresh observation admission requires its verified replay predecessor")
        if (
            self.run_id != self.run_state.run_id
            or self.run_id != self.invocation.run_id
            or self.run_state.invocation_digest != self.invocation.invocation_digest
            or self.skill_ref != f"{self.run_state.skill_id}@{self.run_state.skill_version}"
            or self.run_state.status.value != "succeeded"
        ):
            raise ValueError("terminal admission requires one exact successful subject")
        native_schema = {
            "image2env.compile@2.0.0": "harness.image2env_genesis_compile_receipt.v1",
            "image2env.replay@2.0.0": "harness.genesis_scene_execution_receipt.v2",
            "genesis.observe@3.0.0": "harness.genesis_fresh_capture_receipt.v1",
            "image2env.validate@2.0.0": "harness.genesis_scene_validation.v1",
        }[self.skill_ref]
        bindings = [
            (self.qualification_admission, "harness.typed_qualification_admission.v1"),
            (self.read_closure, NATIVE_READ_CLOSURE_ID),
            (self.native_receipt, native_schema),
        ]
        if self.predecessor_terminal_admission is not None:
            bindings.append((self.predecessor_terminal_admission, NATIVE_TERMINAL_ADMISSION_ID))
        for ref, schema in bindings:
            if (
                ref.uri != f"artifact://sha256/{ref.sha256}"
                or ref.media_type != "application/json"
                or ref.schema_version != schema
            ):
                raise ValueError("terminal admission requires typed canonical CAS bindings")
        implementation = self.admission_implementation
        if (
            implementation.uri != f"artifact://sha256/{implementation.sha256}"
            or implementation.media_type != "text/x-python"
            or implementation.schema_version is not None
        ):
            raise ValueError("admission implementation requires exact Python source bytes")
        return self
