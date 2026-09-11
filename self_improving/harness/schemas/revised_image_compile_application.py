"""Exact compile@3 qualification, scoped to durable revised static candidates."""

from typing import Literal

from pydantic import UUID4, Field, model_validator

from .base import HarnessModel, Sha256, public_schema_config
from .common import ArtifactRef

REPORT_ID = "harness.revised_compile_qualification_report.v1"
INVOCATION_ID = "harness.revised_compile_invocation.v1"


class RevisedCompileInvocationV1(HarnessModel):
    """Reference-only Registry input; full timestamped revision remains in CAS."""

    model_config = public_schema_config(INVOCATION_ID)
    schema_version: Literal[INVOCATION_ID]
    request: ArtifactRef

    @model_validator(mode="after")
    def exact_request(self):
        ref = self.request
        if (
            ref.uri != "artifact://sha256/" + ref.sha256
            or ref.media_type != "application/json"
            or ref.schema_version != "harness.image2env_genesis_compile_request.v2"
        ):
            raise ValueError("invocation requires exact revised request CAS reference")
        return self


class RevisedCompileQualificationCaseV1(HarnessModel):
    run_id: UUID4
    invocation_digest: Sha256
    compile_receipt: ArtifactRef
    prompt_revision: ArtifactRef
    predecessor_environment: ArtifactRef
    original_source: ArtifactRef
    asset_qualification: ArtifactRef
    scene: ArtifactRef

    @model_validator(mode="after")
    def exact_case(self):
        pairs = (
            (self.compile_receipt, ("harness.image2env_genesis_compile_receipt.v2",)),
            (self.prompt_revision, ("harness.prompt_revision.v3",)),
            (
                self.predecessor_environment,
                (
                    "harness.image2env_genesis_compile_receipt.v1",
                    "harness.image2env_genesis_compile_receipt.v2",
                    "harness.genesis_scene_execution_receipt.v2",
                ),
            ),
            (self.original_source, (None,)),
            (self.asset_qualification, ("harness.asset_qualification.v1",)),
            (self.scene, ("harness.genesis_scene_spec.v1",)),
        )
        if any(
            ref.uri != "artifact://sha256/" + ref.sha256
            or ref.media_type != "application/json"
            or ref.schema_version not in schemas
            for ref, schemas in pairs
        ):
            raise ValueError("revised qualification case requires exact typed CAS evidence")
        return self


class RevisedCompileQualificationReportV1(HarnessModel):
    model_config = public_schema_config(REPORT_ID)
    schema_version: Literal[REPORT_ID]
    skill_ref: Literal["image2env.compile@3.0.0"]
    status: Literal["pass"]
    scope: Literal["static_scene_candidate"]
    cases: tuple[RevisedCompileQualificationCaseV1, ...] = Field(min_length=2, max_length=64)
    implementation_sources: tuple[ArtifactRef, ...] = Field(min_length=1)
    compile_implementation_sha256: Sha256
    implementation_sha256: Sha256

    @model_validator(mode="after")
    def exact_report(self):
        ids = tuple(str(case.run_id) for case in self.cases)
        names = tuple(ref.name for ref in self.implementation_sources)
        if (
            ids != tuple(sorted(set(ids)))
            or names != tuple(sorted(set(names)))
            or any(
                ref.uri != "artifact://sha256/" + ref.sha256
                or ref.media_type != "text/x-python"
                or ref.schema_version is not None
                for ref in self.implementation_sources
            )
        ):
            raise ValueError("revised qualification requires unique cases and exact implementation")
        if (
            len({case.scene.sha256 for case in self.cases}) < 2
            or len({case.prompt_revision.sha256 for case in self.cases}) < 2
        ):
            raise ValueError("qualification requires two distinct revised scenes and revisions")
        return self
