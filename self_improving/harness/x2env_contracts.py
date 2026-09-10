"""Exact image2env/video2env namespace contracts without runtime registration."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .schemas.common import ArtifactRef, ExecutionReproducibility, SkillDescriptorV2
from .schemas.x2env import (
    IMAGE2ENV_COMPILE_OUTPUT_SCHEMA_ID,
    IMAGE2ENV_COMPILE_V2_INPUT_SCHEMA_ID,
    IMAGE2ENV_REPLAY_INPUT_SCHEMA_ID,
    IMAGE2ENV_REPLAY_OUTPUT_SCHEMA_ID,
    IMAGE2ENV_VALIDATE_INPUT_SCHEMA_ID,
    IMAGE2ENV_VALIDATE_OUTPUT_SCHEMA_ID,
    VIDEO2ENV_COMPILE_OUTPUT_SCHEMA_ID,
    VIDEO2ENV_COMPILE_V2_INPUT_SCHEMA_ID,
    VIDEO2ENV_REPLAY_INPUT_SCHEMA_ID,
    VIDEO2ENV_REPLAY_OUTPUT_SCHEMA_ID,
    VIDEO2ENV_VALIDATE_INPUT_SCHEMA_ID,
    VIDEO2ENV_VALIDATE_OUTPUT_SCHEMA_ID,
)


@dataclass(frozen=True)
class X2EnvSkillContract:
    skill_ref: str
    mcp_tool_name: str
    input_schema: str
    output_schema: str
    reproducibility: ExecutionReproducibility
    max_attempts: int
    handler_registered: bool = False
    qualification_available: bool = False


def _contract(
    skill_ref: str,
    input_schema: str,
    output_schema: str,
    *,
    reproducibility: ExecutionReproducibility,
    max_attempts: int,
) -> X2EnvSkillContract:
    skill_id, version = skill_ref.split("@", 1)
    return X2EnvSkillContract(
        skill_ref=skill_ref,
        mcp_tool_name=f"{skill_id.replace('.', '_')}_v{version.replace('.', '_')}",
        input_schema=input_schema,
        output_schema=output_schema,
        reproducibility=reproducibility,
        max_attempts=max_attempts,
    )


_CONTRACTS = (
    _contract(
        "image2env.compile@1.0.0",
        IMAGE2ENV_COMPILE_V2_INPUT_SCHEMA_ID,
        IMAGE2ENV_COMPILE_OUTPUT_SCHEMA_ID,
        reproducibility=ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE,
        max_attempts=2,
    ),
    _contract(
        "image2env.replay@1.0.0",
        IMAGE2ENV_REPLAY_INPUT_SCHEMA_ID,
        IMAGE2ENV_REPLAY_OUTPUT_SCHEMA_ID,
        reproducibility=ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE,
        max_attempts=2,
    ),
    _contract(
        "image2env.validate@1.0.0",
        IMAGE2ENV_VALIDATE_INPUT_SCHEMA_ID,
        IMAGE2ENV_VALIDATE_OUTPUT_SCHEMA_ID,
        reproducibility=ExecutionReproducibility.CONTENT_BITWISE_DETERMINISTIC,
        max_attempts=1,
    ),
    _contract(
        "video2env.compile@1.0.0",
        VIDEO2ENV_COMPILE_V2_INPUT_SCHEMA_ID,
        VIDEO2ENV_COMPILE_OUTPUT_SCHEMA_ID,
        reproducibility=ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE,
        max_attempts=2,
    ),
    _contract(
        "video2env.replay@1.0.0",
        VIDEO2ENV_REPLAY_INPUT_SCHEMA_ID,
        VIDEO2ENV_REPLAY_OUTPUT_SCHEMA_ID,
        reproducibility=ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE,
        max_attempts=2,
    ),
    _contract(
        "video2env.validate@1.0.0",
        VIDEO2ENV_VALIDATE_INPUT_SCHEMA_ID,
        VIDEO2ENV_VALIDATE_OUTPUT_SCHEMA_ID,
        reproducibility=ExecutionReproducibility.CONTENT_BITWISE_DETERMINISTIC,
        max_attempts=1,
    ),
)

X2ENV_SKILL_CONTRACTS: Mapping[str, X2EnvSkillContract] = MappingProxyType(
    {contract.skill_ref: contract for contract in _CONTRACTS}
)


def x2env_skill_contract(skill_ref: str) -> X2EnvSkillContract:
    """Return one exact namespace contract; aliases and version fallback are forbidden."""

    return X2ENV_SKILL_CONTRACTS[skill_ref]


def build_x2env_skill_descriptor(
    skill_ref: str,
    *,
    qualification_artifact: ArtifactRef | dict[str, object],
    implementation_name: str,
    implementation_sha256: str,
    qualification_verified: bool,
) -> SkillDescriptorV2:
    """Build a Registry descriptor only after a caller has verified real qualification.

    Contract publication alone deliberately cannot produce a registrable descriptor.
    """

    if qualification_verified is not True:
        raise RuntimeError("x2env descriptor requires verified qualification")
    contract = x2env_skill_contract(skill_ref)
    skill_id, version = contract.skill_ref.split("@", 1)
    return SkillDescriptorV2(
        skill_id=skill_id,
        version=version,
        mcp_tool_name=contract.mcp_tool_name,
        input_schema=contract.input_schema,
        output_schema=contract.output_schema,
        implementation_name=implementation_name,
        implementation_version="1",
        implementation_sha256=implementation_sha256,
        reproducibility=contract.reproducibility,
        max_attempts=contract.max_attempts,
        qualification_artifact=ArtifactRef.model_validate(qualification_artifact),
    )
