"""Qualification of a native compile implementation by durable candidate runs."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .base import HarnessModel, Sha256, public_schema_config
from .common import ArtifactRef

NATIVE_COMPILE_QUALIFICATION_REPORT_ID = "harness.image2env_genesis_skill_qualification_report.v1"


class NativeCompileQualificationCaseV1(HarnessModel):
    run_id: UUID
    invocation_digest: Sha256
    compile_receipt: ArtifactRef

    @model_validator(mode="after")
    def exact_case(self):
        ref = self.compile_receipt
        if (
            self.run_id.version != 4
            or ref.uri != f"artifact://sha256/{ref.sha256}"
            or ref.media_type != "application/json"
            or ref.schema_version != "harness.image2env_genesis_compile_receipt.v1"
        ):
            raise ValueError("qualification case requires UUID4 and exact native compile receipt")
        return self


class Image2EnvGenesisSkillQualificationReportV1(HarnessModel):
    model_config = public_schema_config(NATIVE_COMPILE_QUALIFICATION_REPORT_ID)
    schema_version: Literal[NATIVE_COMPILE_QUALIFICATION_REPORT_ID]
    skill_ref: Literal["image2env.compile@2.0.0"]
    status: Literal["pass"]
    scope: Literal["static_scene_candidate"]
    cases: tuple[NativeCompileQualificationCaseV1, ...] = Field(min_length=3, max_length=64)
    application_source: ArtifactRef
    implementation_sources: tuple[ArtifactRef, ...] = Field(min_length=1)
    compile_implementation_sha256: Sha256
    implementation_sha256: Sha256

    @model_validator(mode="after")
    def exact_report(self):
        ids = tuple(str(case.run_id) for case in self.cases)
        ref = self.application_source
        if (
            ids != tuple(sorted(set(ids)))
            or ref.uri != f"artifact://sha256/{ref.sha256}"
            or ref.media_type != "text/x-python"
            or ref.schema_version is not None
        ):
            raise ValueError(
                "native qualification requires sorted unique cases and exact application source"
            )
        names = tuple(item.name for item in self.implementation_sources)
        if (
            names != tuple(sorted(set(names)))
            or ref not in self.implementation_sources
            or any(
                item.uri != f"artifact://sha256/{item.sha256}"
                or item.media_type != "text/x-python"
                or item.schema_version is not None
                for item in self.implementation_sources
            )
        ):
            raise ValueError("implementation sources must be exact sorted unique CAS Python files")
        return self
