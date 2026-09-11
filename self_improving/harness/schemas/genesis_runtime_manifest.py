"""Content identity for declared runtime roots, without qualification authority."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from .base import HarnessModel, ShortString, public_schema_config
from .common import ArtifactRef

GENESIS_RUNTIME_MANIFEST_SCHEMA_ID = "harness.genesis_runtime_manifest.v1"
RUNTIME_ROOT_ROLES = ("backend", "distributions", "genesis", "interpreter", "native", "stdlib")
RuntimeRootRole = Literal["backend", "distributions", "genesis", "interpreter", "native", "stdlib"]


class RuntimeProcessIdentityV1(HarnessModel):
    implementation: ShortString
    version: ShortString
    abi: ShortString
    platform: ShortString
    environment: tuple[tuple[ShortString, str], ...]

    @model_validator(mode="after")
    def canonical_environment(self):
        names = tuple(name for name, _ in self.environment)
        if names != tuple(sorted(set(names))):
            raise ValueError("process environment must be sorted and unique")
        if any("=" in name or "\x00" in name + value for name, value in self.environment):
            raise ValueError("invalid process environment")
        return self


class RuntimeManifestMemberV1(HarnessModel):
    root: RuntimeRootRole
    path: ShortString
    artifact: ArtifactRef

    @model_validator(mode="after")
    def canonical_member(self):
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or self.path != path.as_posix()
            or self.path == "."
            or "\\" in self.path
            or "\x00" in self.path
        ):
            raise ValueError("member path must be canonical and relative")
        ref = self.artifact
        if (
            ref.uri != f"artifact://sha256/{ref.sha256}"
            or ref.name != f"{self.root}/{self.path}"
            or ref.media_type != "application/octet-stream"
            or ref.schema_version is not None
        ):
            raise ValueError("member must have its exact canonical CAS reference")
        return self


class GenesisRuntimeManifestV1(HarnessModel):
    model_config = public_schema_config(GENESIS_RUNTIME_MANIFEST_SCHEMA_ID)
    schema_version: Literal[GENESIS_RUNTIME_MANIFEST_SCHEMA_ID]
    authority: Literal["declared_byte_closure_only"]
    process_identity: RuntimeProcessIdentityV1
    members: tuple[RuntimeManifestMemberV1, ...] = Field(min_length=6)

    @model_validator(mode="after")
    def canonical_closure(self):
        keys = tuple((member.root, member.path) for member in self.members)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("members must be sorted and unique")
        if tuple(sorted({member.root for member in self.members})) != RUNTIME_ROOT_ROLES:
            raise ValueError("manifest must contain the exact six nonempty root roles")
        return self
