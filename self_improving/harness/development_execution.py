"""Deployment-owned development admission; no qualification is manufactured."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from .schemas.base import (
    CanonicalSkillRef,
    HarnessModel,
    McpToolName,
    PositiveInt,
    SchemaId,
    Sha256,
    SkillId,
    StableSemVer,
    public_schema_config,
)
from .schemas.common import ArtifactRef


class DevelopmentSkillDescriptor(HarnessModel):
    model_config = public_schema_config("harness.development_skill_descriptor.v1")
    schema_version: Literal["harness.development_skill_descriptor.v1"] = (
        "harness.development_skill_descriptor.v1"
    )
    skill_id: SkillId
    version: StableSemVer
    mcp_tool_name: McpToolName
    input_schema: SchemaId
    output_schema: SchemaId
    implementation_sha256: Sha256
    max_attempts: PositiveInt = 1
    qualification_status: Literal["development_unqualified"] = "development_unqualified"


class DevelopmentSkillExecution(HarnessModel):
    model_config = public_schema_config("harness.development_skill_execution.v1")
    schema_version: Literal["harness.development_skill_execution.v1"] = (
        "harness.development_skill_execution.v1"
    )
    skill_ref: CanonicalSkillRef
    descriptor: ArtifactRef
    input_schema: SchemaId
    output_schema: SchemaId
    implementation_sha256: Sha256
    max_attempts: PositiveInt
    evidence_policy_id: str
    evidence_policy_version: str
    evidence_policy_implementation_sha256: Sha256
    development_context: ArtifactRef
    qualification_status: Literal["development_unqualified"] = "development_unqualified"

    @model_validator(mode="after")
    def references_are_development_only(self):
        _require_ref(self.descriptor, "harness.development_skill_descriptor.v1")
        _require_ref(self.development_context, "harness.development_authority.v1")
        return self


class DevelopmentSkillSnapshot(HarnessModel):
    skill_ref: CanonicalSkillRef
    mcp_tool_name: str
    descriptor_ref: ArtifactRef

    @model_validator(mode="after")
    def descriptor_is_development_only(self):
        _require_ref(self.descriptor_ref, "harness.development_skill_descriptor.v1")
        return self


class DevelopmentRegistrySnapshot(HarnessModel):
    model_config = public_schema_config("harness.development_registry_snapshot.v1")
    schema_version: Literal["harness.development_registry_snapshot.v1"] = (
        "harness.development_registry_snapshot.v1"
    )
    development_context: ArtifactRef
    development_skills: tuple[DevelopmentSkillSnapshot, ...]
    qualified_skills: tuple[()] = ()

    @model_validator(mode="after")
    def context_is_development_only(self):
        _require_ref(self.development_context, "harness.development_authority.v1")
        return self


def _require_ref(ref, schema):
    if (
        ref.schema_version != schema
        or ref.media_type != "application/json"
        or ref.uri != f"artifact://sha256/{ref.sha256}"
    ):
        raise ValueError("development reference must identify exact canonical CAS schema")


def _bytes(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def _put(store, value, schema, name):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / name
        path.write_bytes(_bytes(value))
        return store.put_file(path, name=name, media_type="application/json", schema_version=schema)


_TOKEN = object()


class DevelopmentExecutionAuthority:
    """Source/config binding supplied by the operator, never a request or model."""

    def __init__(self, *, token, store, files, context):
        if token is not _TOKEN:
            raise TypeError("capture development authority through its deployment factory")
        object.__setattr__(self, "store", store)
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "context", context)

    def __setattr__(self, name, value):
        raise AttributeError("development authority is immutable")

    @property
    def implementation_sha256(self):
        return hashlib.sha256(
            _bytes([dict(path=str(p), sha256=d) for p, d in self.files[:-1]])
        ).hexdigest()

    def verify(self):
        self.store.resolve(self.context)
        for path, digest in self.files:
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("development source or configuration changed")

    def registry(self, descriptors):
        self.verify()
        entries = []
        for descriptor in descriptors:
            if type(descriptor) is not DevelopmentSkillDescriptor:
                raise TypeError("development registry requires development descriptors")
            if descriptor.implementation_sha256 != self.implementation_sha256:
                raise ValueError("development descriptor must bind the declared source closure")
            ref = _put(
                self.store, descriptor, descriptor.schema_version, "development-descriptor.json"
            )
            entries.append(
                DevelopmentSkillSnapshot(
                    skill_ref=f"{descriptor.skill_id}@{descriptor.version}",
                    mcp_tool_name=descriptor.mcp_tool_name,
                    descriptor_ref=ref,
                )
            )
        value = DevelopmentRegistrySnapshot(
            development_context=self.context,
            development_skills=tuple(sorted(entries, key=lambda entry: entry.mcp_tool_name)),
        )
        return _put(self.store, value, value.schema_version, "development-registry.json")


def capture_development_authority(*, artifact_store, source_files, configuration):
    """Capture the explicitly declared implementation files and operator JSON config."""
    files = []
    for path in (*source_files, configuration):
        path = Path(path)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError("development source/config must be absolute regular files")
        files.append((path, hashlib.sha256(path.read_bytes()).hexdigest()))
    if not source_files:
        raise ValueError("development requires declared implementation files")
    config = json.loads(Path(configuration).read_bytes())
    if (
        config.get("execution_mode") != "development"
        or config.get("allow_changed_skill_closure") is not True
    ):
        raise ValueError(
            "deployment must explicitly enable development and allow_changed_skill_closure"
        )
    document = dict(
        schema_version="harness.development_authority.v1",
        qualification_status="development_unqualified",
        source_scope="declared_files",
        files=[dict(path=str(path), sha256=digest) for path, digest in files],
    )
    context = _put(
        artifact_store, document, document["schema_version"], "development-authority.json"
    )
    return DevelopmentExecutionAuthority(
        token=_TOKEN, store=artifact_store, files=tuple(files), context=context
    )


def create_development_execution_binding(
    *, descriptor, handler, policy, registry, run_store, authority
):
    """Adapt the existing Registry execution kernel to the existing Golden catalog."""
    from .golden_execution import _create_golden_execution_binding

    authority.verify()
    if descriptor.implementation_sha256 != authority.implementation_sha256:
        raise ValueError("development descriptor must bind the declared source closure")

    class Application:
        skill_ref = f"{descriptor.skill_id}@{descriptor.version}"
        artifact_root = authority.store.root
        authority_path = run_store.path
        skills = (descriptor,)

        def invoke(self, parameters, *, run_id):
            return registry.invoke_development_with_run_id(
                descriptor,
                handler,
                parameters.model_dump(mode="json"),
                run_id=run_id,
                development_authority=authority,
            )

        def invocation(self, run_id):
            return registry.invocation(run_id)

        def run_state(self, run_id):
            return run_store.read_run_state(run_id)

    application = Application()
    application.policy = policy
    return _create_golden_execution_binding(
        policy=policy, application=application, child_authority=run_store
    )
