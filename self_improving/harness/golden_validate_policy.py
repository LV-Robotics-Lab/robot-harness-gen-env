"""Evidence policy for legacy replay-bound ``text2env.validate@2.0.0``."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .artifacts import LocalArtifactStore
from .handlers.text2env_validate_v2 import verify_validate_v2_lineage
from .run_receipts import LoadedPortableRunReceipt
from .schemas.base import HarnessModel
from .schemas.common import ArtifactRef, Invocation, RunState, RunStatus
from .schemas.text2env_validate_v2 import Text2EnvValidateV2Input, Text2EnvValidateV2Output
from .schemas.workflow_operation import (
    ReceiptDerivedFactClaimV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from .trusted_state import TrustedWorldState, WorldFact

_POLICY_DOMAIN = b"harness.text2env.validate.v2.evidence-policy.v1\0"
_Model = TypeVar("_Model", bound=HarnessModel)


def _artifact_sort_key(ref: ArtifactRef) -> tuple[str, str, str]:
    return (ref.sha256, ref.schema_version or "", ref.media_type)


@dataclass(frozen=True)
class Text2EnvValidateV2EvidencePolicy:
    """Authorize one closed compile/replay lineage and expose only its decision."""

    artifact_store: LocalArtifactStore | None = None
    skill_ref: str = "text2env.validate@2.0.0"
    input_model: type[BaseModel] = Text2EnvValidateV2Input
    evidence_policy_id: str = "text2env.validate.v2.evidence"
    evidence_policy_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.artifact_store is not None and type(self.artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")

    @property
    def evidence_policy_implementation_sha256(self) -> str:
        return hashlib.sha256(_POLICY_DOMAIN + Path(__file__).read_bytes()).hexdigest()

    def normalize_parameters(self, parameters: Mapping[str, object]) -> BaseModel:
        return Text2EnvValidateV2Input.model_validate(parameters)

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None:
        if type(parameters) is not Text2EnvValidateV2Input:
            raise TypeError("validate policy requires Text2EnvValidateV2Input")
        store = self.artifact_store
        if type(store) is not LocalArtifactStore:
            raise ValueError("validate policy requires its exact evidence CAS")
        package_facts = tuple(fact for fact in state.facts if fact.key == "environment.package")
        catalog_facts = tuple(fact for fact in state.facts if fact.key == "assets.catalog")
        runtime_facts = tuple(fact for fact in state.facts if fact.key == "replay.runtime_evidence")
        validation_facts = tuple(
            fact for fact in state.facts if fact.key == "environment.validation"
        )
        if (
            state.version != 3
            or len(package_facts) != 1
            or len(catalog_facts) != 1
            or len(runtime_facts) != 1
            or validation_facts
        ):
            raise ValueError("validate requires the exact revision-2 compile/replay state")
        package_fact, catalog_fact, runtime_fact = (
            package_facts[0],
            catalog_facts[0],
            runtime_facts[0],
        )
        if (
            package_fact.source_kind != "trusted_receipt"
            or catalog_fact.source_kind != "trusted_receipt"
            or runtime_fact.source_kind != "trusted_receipt"
            or package_fact.source_artifact != catalog_fact.source_artifact
        ):
            raise ValueError("validate facts must come from exact trusted receipts")

        lineage = verify_validate_v2_lineage(store, parameters)
        if package_fact.value != lineage.package.model_dump(mode="json"):
            raise ValueError("validate package differs from trusted workflow state")
        if catalog_fact.value != lineage.package.asset_catalog.model_dump(mode="json"):
            raise ValueError("validate catalog differs from trusted workflow state")
        if runtime_fact.value != parameters.runtime_evidence.model_dump(mode="json"):
            raise ValueError("validate runtime evidence differs from trusted workflow state")
        compile_receipt = _resolve_model(
            store,
            package_fact.source_artifact,
            TrustedToolReceiptV2,
        )
        replay_receipt = _resolve_model(
            store,
            runtime_fact.source_artifact,
            TrustedToolReceiptV2,
        )
        _verify_tool_receipt(
            compile_receipt,
            skill_ref="text2env.compile@1.0.0",
            child=lineage.compile,
            qualification=parameters.compile_qualification,
        )
        _verify_tool_receipt(
            replay_receipt,
            skill_ref="text2env.replay@1.0.0",
            child=lineage.replay,
            qualification=parameters.replay_qualification,
        )
        if (
            compile_receipt.workflow_run_id != replay_receipt.workflow_run_id
            or compile_receipt.base_revision != 0
            or replay_receipt.base_revision != 1
        ):
            raise ValueError("compile and replay receipts are not one ordered Golden workflow")
        for trusted, portable in (
            (compile_receipt, lineage.compile),
            (replay_receipt, lineage.replay),
        ):
            if (
                _resolve_model(store, trusted.run_state, RunState) != portable.run_state
                or trusted.invocation is None
                or _resolve_model(store, trusted.invocation, Invocation) != portable.invocation
            ):
                raise ValueError("trusted receipt child records differ from portable run")

        provenance = lineage.provenance
        if provenance.compile_trusted_receipt != package_fact.source_artifact:
            raise ValueError("request provenance names another compile trusted receipt")
        compile_history = _resolve_model(
            store,
            provenance.compile_history_authority,
            WorkflowOperationReceiptV2,
        )
        if (
            compile_history.workflow_run_id != compile_receipt.workflow_run_id
            or compile_history.tool_receipt != package_fact.source_artifact
            or compile_history.base_revision != 0
            or compile_history.committed_revision != 1
            or compile_history.resulting_state_sha256 != provenance.compile_world_state_sha256
            or replay_receipt.base_state != compile_history.resulting_state
            or replay_receipt.base_state_sha256 != compile_history.resulting_state_sha256
        ):
            raise ValueError("request provenance does not close compile into replay")

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]:
        if state.status is not RunStatus.SUCCEEDED:
            return ()
        output = Text2EnvValidateV2Output.model_validate(state.output)
        return (
            ReceiptDerivedFactClaimV2(
                key="environment.validation",
                value=output.validation_decision_receipt.model_dump(mode="json"),
            ),
        )

    def diagnostics(self, state: RunState) -> tuple[ArtifactRef, ...]:
        if state.status is RunStatus.SUCCEEDED:
            return ()
        assert state.blocker is not None
        return tuple(sorted(state.blocker.artifact_refs, key=_artifact_sort_key))

    def fresh_observations(
        self,
        state: RunState,
        invocation: Invocation | None,
    ) -> tuple[WorldFact, ...]:
        return ()

    def verify_result(
        self,
        *,
        state: RunState,
        claims: tuple[ReceiptDerivedFactClaimV2, ...],
        result: ToolResultV2,
        supporting: tuple[ArtifactRef, ...],
    ) -> None:
        if result.fresh_observations:
            raise ValueError("legacy validate cannot claim fresh observations")
        if result.typed_output != state.output:
            raise ValueError("validate ToolResult output differs from its terminal child")
        if claims != self.claims(state):
            raise ValueError("validate claims differ from its exact decision output")
        if result.diagnostics != self.diagnostics(state):
            raise ValueError("validate diagnostics differ from terminal evidence")
        supporting_ids = {_artifact_sort_key(ref) for ref in supporting}
        if not {_artifact_sort_key(ref) for ref in result.diagnostics}.issubset(supporting_ids):
            raise ValueError("validate diagnostics are absent from supporting evidence")
        if state.status is RunStatus.SUCCEEDED:
            output = Text2EnvValidateV2Output.model_validate(state.output)
            output_ids = {
                _artifact_sort_key(output.validation_report),
                _artifact_sort_key(output.validation_decision_receipt),
            }
            if not output_ids.issubset(supporting_ids):
                raise ValueError("validate output references absent supporting evidence")


def _verify_tool_receipt(
    receipt: TrustedToolReceiptV2,
    *,
    skill_ref: str,
    child: LoadedPortableRunReceipt,
    qualification: ArtifactRef,
) -> None:
    if (
        receipt.status is not RunStatus.SUCCEEDED
        or receipt.skill.skill_ref != skill_ref
        or receipt.skill.qualification_artifact != qualification
        or receipt.child_run_id != child.run_state.run_id
        or receipt.invocation_digest != child.invocation.invocation_digest
        or receipt.typed_output != child.run_state.output
        or receipt.supporting_artifacts
        != tuple(sorted(child.run_state.artifacts, key=_artifact_sort_key))
        or receipt.blocker is not None
    ):
        raise ValueError(f"trusted receipt does not bind portable {skill_ref} run")


def _resolve_model(store: LocalArtifactStore, ref: ArtifactRef, model: type[_Model]) -> _Model:
    payload = store.resolve(ref).path.read_bytes()
    value = model.model_validate_json(payload)
    if payload != _canonical_bytes(value.model_dump(mode="json")):
        raise ValueError("trusted workflow evidence is not canonical JSON")
    return value


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


__all__ = ["Text2EnvValidateV2EvidencePolicy"]
