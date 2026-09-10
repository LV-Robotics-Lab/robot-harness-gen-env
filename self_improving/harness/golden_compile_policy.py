"""Stable evidence-policy closure for the exact text2env compile Skill."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from .schemas.common import ArtifactRef, RunState, RunStatus
from .schemas.text2env import Text2EnvCompileInput, Text2EnvCompileOutput
from .schemas.workflow_operation import ReceiptDerivedFactClaimV2, ToolResultV2
from .trusted_state import TrustedWorldState

_POLICY_DIGEST_DOMAIN = b"harness.text2env.compile.evidence-policy.v1\0"


def _artifact_sort_key(ref: ArtifactRef) -> tuple[str, str, str]:
    return (ref.sha256, ref.schema_version or "", ref.media_type)


@dataclass(frozen=True)
class Text2EnvCompileEvidencePolicy:
    """Interpret compile input and output without claiming simulator observations."""

    skill_ref: str = "text2env.compile@1.0.0"
    input_model: type[BaseModel] = Text2EnvCompileInput
    evidence_policy_id: str = "text2env.compile.evidence"
    evidence_policy_version: str = "1.0.0"

    @property
    def evidence_policy_implementation_sha256(self) -> str:
        return hashlib.sha256(_POLICY_DIGEST_DOMAIN + Path(__file__).read_bytes()).hexdigest()

    def normalize_parameters(self, parameters: Mapping[str, object]) -> BaseModel:
        return Text2EnvCompileInput.model_validate(parameters)

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None:
        if type(parameters) is not Text2EnvCompileInput:
            raise TypeError("compile policy requires Text2EnvCompileInput")
        objectives = tuple(fact for fact in state.facts if fact.key == "request.objective")
        if len(objectives) != 1 or type(objectives[0].value) is not str:
            raise ValueError("compile requires one trusted request.objective fact")
        if objectives[0].value != parameters.request:
            raise ValueError("compile request must equal the trusted request.objective fact")

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]:
        if state.status is not RunStatus.SUCCEEDED:
            return ()
        output = Text2EnvCompileOutput.model_validate(state.output)
        return (
            ReceiptDerivedFactClaimV2(
                key="assets.catalog",
                value=output.environment_package.asset_catalog.model_dump(mode="json"),
            ),
            ReceiptDerivedFactClaimV2(
                key="environment.package",
                value=output.environment_package.model_dump(mode="json"),
            ),
        )

    def diagnostics(self, state: RunState) -> tuple[ArtifactRef, ...]:
        if state.status is RunStatus.SUCCEEDED:
            return ()
        assert state.blocker is not None
        return tuple(sorted(state.blocker.artifact_refs, key=_artifact_sort_key))

    def verify_result(
        self,
        *,
        state: RunState,
        claims: tuple[ReceiptDerivedFactClaimV2, ...],
        result: ToolResultV2,
        supporting: tuple[ArtifactRef, ...],
    ) -> None:
        if result.fresh_observations:
            raise ValueError("compile cannot claim fresh observations")
        if result.typed_output != state.output:
            raise ValueError("compile ToolResult output does not equal its terminal child")
        if claims != self.claims(state):
            raise ValueError("compile claims do not equal the exact typed output")
        if result.diagnostics != self.diagnostics(state):
            raise ValueError("compile diagnostics do not equal terminal diagnostic evidence")
        if not set(result.diagnostics).issubset(supporting):
            raise ValueError("compile diagnostics are absent from supporting evidence")


__all__ = ["Text2EnvCompileEvidencePolicy"]
