"""Stable evidence-policy closure for the exact text2env replay Skill."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from .schemas.common import ArtifactRef, RunState, RunStatus
from .schemas.text2env import Text2EnvReplayInput, Text2EnvReplayOutput
from .schemas.workflow_operation import ReceiptDerivedFactClaimV2, ToolResultV2
from .trusted_state import TrustedWorldState

_POLICY_DIGEST_DOMAIN = b"harness.text2env.replay.evidence-policy.v1\0"
_TRUSTED_RECEIPT_SCHEMA = "harness.trusted_tool_receipt.v2"


def _artifact_sort_key(ref: ArtifactRef) -> tuple[str, str, str]:
    return (ref.sha256, ref.schema_version or "", ref.media_type)


@dataclass(frozen=True)
class Text2EnvReplayEvidencePolicy:
    """Authorize compile-bound replay and expose only its runtime evidence fact."""

    skill_ref: str = "text2env.replay@1.0.0"
    input_model: type[BaseModel] = Text2EnvReplayInput
    evidence_policy_id: str = "text2env.replay.evidence"
    evidence_policy_version: str = "1.0.0"

    @property
    def evidence_policy_implementation_sha256(self) -> str:
        return hashlib.sha256(_POLICY_DIGEST_DOMAIN + Path(__file__).read_bytes()).hexdigest()

    def normalize_parameters(self, parameters: Mapping[str, object]) -> BaseModel:
        return Text2EnvReplayInput.model_validate(parameters)

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None:
        if type(parameters) is not Text2EnvReplayInput:
            raise TypeError("replay policy requires Text2EnvReplayInput")
        package_facts = tuple(fact for fact in state.facts if fact.key == "environment.package")
        catalog_facts = tuple(fact for fact in state.facts if fact.key == "assets.catalog")
        if len(package_facts) != 1 or len(catalog_facts) != 1:
            raise ValueError("replay requires one trusted package and asset catalog fact")
        package_fact = package_facts[0]
        catalog_fact = catalog_facts[0]
        if (
            package_fact.source_kind != "trusted_receipt"
            or catalog_fact.source_kind != "trusted_receipt"
            or package_fact.source_artifact != catalog_fact.source_artifact
            or package_fact.source_artifact.schema_version != _TRUSTED_RECEIPT_SCHEMA
        ):
            raise ValueError("replay package and catalog must share one trusted compile receipt")
        package = parameters.environment_package.model_dump(mode="json")
        catalog = parameters.environment_package.asset_catalog.model_dump(mode="json")
        if package_fact.value != package or catalog_fact.value != catalog:
            raise ValueError("replay input must equal the trusted compile package and catalog")

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]:
        if state.status is not RunStatus.SUCCEEDED:
            return ()
        output = Text2EnvReplayOutput.model_validate(state.output)
        return (
            ReceiptDerivedFactClaimV2(
                key="replay.runtime_evidence",
                value=output.runtime_evidence.model_dump(mode="json"),
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
            raise ValueError("replay output does not contain a fresh observation envelope")
        if result.typed_output != state.output:
            raise ValueError("replay ToolResult output does not equal its terminal child")
        if claims != self.claims(state):
            raise ValueError("replay claims do not equal the exact typed output")
        if result.diagnostics != self.diagnostics(state):
            raise ValueError("replay diagnostics do not equal terminal diagnostic evidence")
        if not set(result.diagnostics).issubset(supporting):
            raise ValueError("replay diagnostics are absent from supporting evidence")
        if state.status is RunStatus.SUCCEEDED:
            output = Text2EnvReplayOutput.model_validate(state.output)
            output_identities = {
                _artifact_sort_key(ref)
                for ref in (output.runtime_evidence, *output.replay_artifacts)
            }
            supporting_identities = {_artifact_sort_key(ref) for ref in supporting}
            if not output_identities.issubset(supporting_identities):
                raise ValueError("replay typed output references absent supporting evidence")


__all__ = ["Text2EnvReplayEvidencePolicy"]
