"""Frozen legacy-v1 schema seam used by ``text2env.replay@1.0.0``.

Later Text2Env or x2env schema generations belong in a separate versioned
resolver. Extending this directory would intentionally stale replay@1.
"""

from __future__ import annotations

from importlib import import_module
from types import MappingProxyType
from typing import Any

_LEGACY_V1_EXPORT_MODULES = MappingProxyType({
    "ARTIFACT_REF_SCHEMA_ID": ".common",
    "BLOCKER_SCHEMA_ID": ".common",
    "EVENT_SCHEMA_ID": ".common",
    "RUN_STATE_SCHEMA_ID": ".common",
    "SKILL_DESCRIPTOR_SCHEMA_ID": ".common",
    "SKILL_DESCRIPTOR_V2_SCHEMA_ID": ".common",
    "SKILL_INVOCATION_SCHEMA_ID": ".common",
    "SKILL_QUALIFICATION_SCHEMA_ID": ".common",
    "ArtifactRef": ".common",
    "Blocker": ".common",
    "DependencyRef": ".common",
    "Event": ".common",
    "ExecutionReproducibility": ".common",
    "Invocation": ".common",
    "RegisteredSkillDescriptor": ".common",
    "RunState": ".common",
    "RunStatus": ".common",
    "SkillDescriptor": ".common",
    "SkillDescriptorV2": ".common",
    "SkillQualification": ".common",
    "UnknownField": ".common",
    "ValidationStatus": ".common",
    "ENVIRONMENT_PACKAGE_SCHEMA_ID": ".text2env",
    "TEXT2ENV_COMPILE_INPUT_SCHEMA_ID": ".text2env",
    "TEXT2ENV_COMPILE_OUTPUT_SCHEMA_ID": ".text2env",
    "TEXT2ENV_REPLAY_INPUT_SCHEMA_ID": ".text2env",
    "TEXT2ENV_REPLAY_OUTPUT_SCHEMA_ID": ".text2env",
    "CompileConfig": ".text2env",
    "EnvironmentPackage": ".text2env",
    "RuntimeConfig": ".text2env",
    "Text2EnvCompileInput": ".text2env",
    "Text2EnvCompileOutput": ".text2env",
    "Text2EnvReplayInput": ".text2env",
    "Text2EnvReplayOutput": ".text2env",
})
_LEGACY_V1_SCHEMA_MODELS = MappingProxyType({
    "harness.artifact_ref.v1": (".common", "ArtifactRef"),
    "harness.blocker.v1": (".common", "Blocker"),
    "harness.environment_package.v1": (".text2env", "EnvironmentPackage"),
    "harness.event.v1": (".common", "Event"),
    "harness.run_state.v1": (".common", "RunState"),
    "harness.skill_descriptor.v1": (".common", "SkillDescriptor"),
    "harness.skill_descriptor.v2": (".common", "SkillDescriptorV2"),
    "harness.skill_invocation.v1": (".common", "Invocation"),
    "harness.skill_qualification.v1": (".common", "SkillQualification"),
    "harness.text2env_compile_input.v1": (".text2env", "Text2EnvCompileInput"),
    "harness.text2env_compile_output.v1": (".text2env", "Text2EnvCompileOutput"),
    "harness.text2env_replay_input.v1": (".text2env", "Text2EnvReplayInput"),
    "harness.text2env_replay_output.v1": (".text2env", "Text2EnvReplayOutput"),
})


def resolve(name: str, package: str) -> Any:
    module_name = _LEGACY_V1_EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {package!r} has no attribute {name!r}")
    return getattr(import_module(module_name, package), name)


def schema_model(schema_id: str, package: str) -> type[Any]:
    module_name, model_name = _LEGACY_V1_SCHEMA_MODELS[schema_id]
    return getattr(import_module(module_name, package), model_name)
