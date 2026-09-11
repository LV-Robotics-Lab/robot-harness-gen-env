"""Versioned native image compilation; independent of legacy x2env package semantics."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .base import HarnessModel, Sha256, ShortString, public_schema_config
from .common import ArtifactRef
from .x2env import Image2EnvCompileV2Input

IMAGE_GENESIS_COMPILE_REQUEST_ID = "harness.image2env_genesis_compile_request.v1"
IMAGE_GENESIS_COMPILE_RECEIPT_ID = "harness.image2env_genesis_compile_receipt.v1"
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Vec3 = tuple[Finite, Finite, Finite]


def _cas(ref, schema):
    if (
        ref.uri != f"artifact://sha256/{ref.sha256}"
        or ref.media_type != "application/json"
        or ref.schema_version != schema
    ):
        raise ValueError("native compile reference requires exact canonical CAS JSON type")


class Image2EnvGenesisCompileRequestV1(HarnessModel):
    model_config = public_schema_config(IMAGE_GENESIS_COMPILE_REQUEST_ID)
    schema_version: Literal[IMAGE_GENESIS_COMPILE_REQUEST_ID]
    compile_input: Image2EnvCompileV2Input
    qualification_run_id: UUID
    source_scene: ArtifactRef
    table_center_m: Vec3
    table_size_m: Vec3
    table_friction: Finite = Field(ge=0)

    @model_validator(mode="after")
    def valid_request(self):
        _cas(self.source_scene, None)
        if min(self.table_size_m) <= 0 or self.table_center_m[2] - self.table_size_m[2] / 2 < 0:
            raise ValueError("finite table requires positive extents above ground")
        return self


class Image2EnvGenesisCompileReceiptV1(HarnessModel):
    model_config = public_schema_config(IMAGE_GENESIS_COMPILE_RECEIPT_ID)
    schema_version: Literal[IMAGE_GENESIS_COMPILE_RECEIPT_ID]
    request: ArtifactRef
    scene: ArtifactRef
    qualification: ArtifactRef
    source_object_id: ShortString
    proposal_object_id: ShortString
    scene_object_id: Literal["object"]
    implementation_sha256: Sha256
    implementation: tuple[ArtifactRef, ...] = Field(min_length=1)
    scope: Literal["static_scene_candidate"]
    simulator_executed: bool = Field(strict=True)

    @model_validator(mode="after")
    def valid_receipt(self):
        for ref, schema in (
            (self.request, IMAGE_GENESIS_COMPILE_REQUEST_ID),
            (self.scene, "harness.genesis_scene_spec.v1"),
            (self.qualification, "harness.asset_qualification.v1"),
        ):
            _cas(ref, schema)
        if self.simulator_executed or self.source_object_id != self.proposal_object_id:
            raise ValueError("compile is a static candidate with an exact source object mapping")
        names = tuple(ref.name for ref in self.implementation)
        if names != tuple(sorted(set(names))) or any(
            ref.uri != f"artifact://sha256/{ref.sha256}"
            or ref.media_type != "text/x-python"
            or ref.schema_version is not None
            for ref in self.implementation
        ):
            raise ValueError("implementation requires canonical unique Python source references")
        return self
