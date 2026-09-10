"""Evidence closure for the controlled ``genesis.observe@1.0.0`` candidate.

This policy deliberately carries no production or simulator-execution claim.  It
only projects caller-supplied controlled captures after re-verifying their exact
replay lineage, invocation, CAS receipt, media, and freshness window.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, TypeVar

from pydantic import BaseModel

from .artifacts import LocalArtifactStore
from .golden_execution import SkillAuthorizationContext
from .schemas.base import HarnessModel
from .schemas.common import ArtifactRef, Invocation, RunState, RunStatus
from .schemas.genesis_observe import (
    GenesisObservationReceipt,
    GenesisObserveInput,
    GenesisObserveOutput,
)
from .schemas.workflow_operation import (
    ReceiptDerivedFactClaimV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from .schemas.x2env import (
    Image2EnvReplayInput,
    Image2EnvReplayOutput,
    Video2EnvReplayInput,
    Video2EnvReplayOutput,
    X2EnvEnvironmentPackage,
)
from .trusted_state import TrustedWorldState, WorldFact

_POLICY_DOMAIN = b"harness.genesis.observe.controlled.evidence-policy.v1\0"
_Model = TypeVar("_Model", bound=HarnessModel)
_IMAGE_REPLAY_SKILL = "image2env.replay@1.0.0"
_VIDEO_REPLAY_SKILL = "video2env.replay@1.0.0"


def _artifact_sort_key(ref: ArtifactRef) -> tuple[str, str, str]:
    return (ref.sha256, ref.schema_version or "", ref.media_type)


@dataclass(frozen=True)
class ControlledGenesisObserveEvidencePolicy:
    """Admit and project only receipt-bound controlled observation evidence."""

    artifact_store: LocalArtifactStore

    skill_ref: ClassVar[str] = "genesis.observe@1.0.0"
    input_model: ClassVar[type[BaseModel]] = GenesisObserveInput
    evidence_policy_id: ClassVar[str] = "genesis.observe.controlled.evidence"
    evidence_policy_version: ClassVar[str] = "1.0.0"
    simulator_executed: ClassVar[Literal[False]] = False

    def __post_init__(self) -> None:
        if type(self.artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")

    @property
    def evidence_policy_implementation_sha256(self) -> str:
        return hashlib.sha256(_POLICY_DOMAIN + Path(__file__).read_bytes()).hexdigest()

    def normalize_parameters(self, parameters: Mapping[str, object]) -> BaseModel:
        return GenesisObserveInput.model_validate(parameters)

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None:
        if type(parameters) is not GenesisObserveInput:
            raise TypeError("observe policy requires GenesisObserveInput")
        trusted_state = TrustedWorldState.model_validate(state.model_dump(mode="python"))
        workflow = _resolve_model(
            self.artifact_store,
            parameters.replay_workflow_receipt,
            WorkflowOperationReceiptV2,
        )
        if (
            workflow.status is not RunStatus.SUCCEEDED
            or workflow.resulting_state_sha256 != trusted_state.state_sha256
        ):
            raise ValueError("observe requires a successful replay workflow at the current state")
        if (
            _resolve_model(self.artifact_store, workflow.resulting_state, TrustedWorldState)
            != trusted_state
        ):
            raise ValueError("replay workflow resulting state differs from current trusted state")

        replay_receipt = _resolve_model(
            self.artifact_store,
            workflow.tool_receipt,
            TrustedToolReceiptV2,
        )
        if (
            replay_receipt.status is not RunStatus.SUCCEEDED
            or replay_receipt.workflow_run_id != workflow.workflow_run_id
            or replay_receipt.operation_id != workflow.operation_id
        ):
            raise ValueError("replay workflow and trusted tool receipt are not one success")
        skill_ref = replay_receipt.skill.skill_ref
        if skill_ref == _IMAGE_REPLAY_SKILL:
            replay_input_model = Image2EnvReplayInput
            replay_output_model = Image2EnvReplayOutput
            input_schema = "harness.image2env_replay_input.v1"
            output_schema = "harness.image2env_replay_output.v1"
        elif skill_ref == _VIDEO_REPLAY_SKILL:
            replay_input_model = Video2EnvReplayInput
            replay_output_model = Video2EnvReplayOutput
            input_schema = "harness.video2env_replay_input.v1"
            output_schema = "harness.video2env_replay_output.v1"
        else:
            raise ValueError("observe source must be an image2env or video2env replay receipt")
        if (
            replay_receipt.skill.input_schema != input_schema
            or replay_receipt.skill.output_schema != output_schema
        ):
            raise ValueError("trusted replay receipt declares the wrong exact schemas")

        assert replay_receipt.invocation is not None
        replay_invocation = _resolve_model(
            self.artifact_store,
            replay_receipt.invocation,
            Invocation,
        )
        replay_state = _resolve_model(
            self.artifact_store,
            replay_receipt.run_state,
            RunState,
        )
        if (
            replay_invocation.skill_id != skill_ref.split("@", 1)[0]
            or replay_invocation.skill_version != "1.0.0"
            or replay_invocation.run_id != replay_receipt.child_run_id
            or replay_invocation.invocation_digest != replay_receipt.invocation_digest
            or replay_state.run_id != replay_receipt.child_run_id
            or replay_state.invocation_digest != replay_receipt.invocation_digest
            or replay_state.status is not RunStatus.SUCCEEDED
            or replay_state.output != replay_receipt.typed_output
            or tuple(sorted(replay_state.artifacts, key=_artifact_sort_key))
            != replay_receipt.supporting_artifacts
        ):
            raise ValueError("trusted replay receipt does not close its invocation and child run")

        replay_input = replay_input_model.model_validate(replay_invocation.effective_parameters)
        replay_output = replay_output_model.model_validate(replay_receipt.typed_output)
        package = _resolve_model(
            self.artifact_store,
            parameters.environment_package,
            X2EnvEnvironmentPackage,
        )
        if (
            replay_input.environment_package != package
            or replay_output.environment_package != package
            or replay_output.runtime_receipt != parameters.replay_runtime_receipt
        ):
            raise ValueError("observe sources differ from the exact replay input and output")
        supporting = set(replay_receipt.supporting_artifacts)
        if replay_output.runtime_receipt not in supporting:
            raise ValueError("replay runtime receipt is absent from replay supporting evidence")
        self.artifact_store.resolve(parameters.replay_runtime_receipt)
        if parameters.checkpoint is not None:
            if parameters.checkpoint not in supporting:
                raise ValueError("observe checkpoint is absent from replay supporting evidence")
            self.artifact_store.resolve(parameters.checkpoint)

    def authorize_workflow(
        self,
        state: TrustedWorldState,
        parameters: BaseModel,
        context: SkillAuthorizationContext,
    ) -> None:
        """Bind a replay receipt to the exact visible parent workflow head."""

        if type(parameters) is not GenesisObserveInput:
            raise TypeError("observe policy requires GenesisObserveInput")
        if type(context) is not SkillAuthorizationContext:
            raise TypeError("observe policy requires exact SkillAuthorizationContext")
        workflow = _resolve_model(
            self.artifact_store,
            parameters.replay_workflow_receipt,
            WorkflowOperationReceiptV2,
        )
        if (
            context.workflow_run_id != workflow.workflow_run_id
            or context.principal_id != workflow.principal_id
            or context.receipt_head != parameters.replay_workflow_receipt
            or context.revision != workflow.committed_revision
            or context.state_ref != workflow.resulting_state
            or state.state_sha256 != workflow.resulting_state_sha256
        ):
            raise ValueError("replay receipt differs from the exact workflow authorization context")

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]:
        if state.status is not RunStatus.SUCCEEDED:
            return ()
        _, receipt = self._capture_evidence(state, invocation=None)
        return tuple(
            ReceiptDerivedFactClaimV2(key=observation.key, value=observation.value)
            for observation in receipt.observations
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
        if state.status is not RunStatus.SUCCEEDED:
            return ()
        if invocation is None:
            raise ValueError("successful observation run omitted its invocation")
        _, receipt = self._capture_evidence(state, invocation=invocation)
        return tuple(
            WorldFact(
                key=observation.key,
                value=observation.value,
                source_kind="fresh_observation",
                source_artifact=GenesisObserveOutput.model_validate(
                    state.output
                ).observation_receipt,
                observed_at=observation.captured_at,
                valid_until=observation.valid_until,
            )
            for observation in receipt.observations
        )

    def verify_result(
        self,
        *,
        state: RunState,
        claims: tuple[ReceiptDerivedFactClaimV2, ...],
        result: ToolResultV2,
        supporting: tuple[ArtifactRef, ...],
    ) -> None:
        expected_supporting = tuple(sorted(state.artifacts, key=_artifact_sort_key))
        if supporting != expected_supporting:
            raise ValueError("observe supporting evidence differs from the terminal child")
        if result.typed_output != state.output:
            raise ValueError("observe ToolResult output differs from its terminal child")
        if claims != self.claims(state):
            raise ValueError("observe claims differ from the canonical observation receipt")
        if result.diagnostics != self.diagnostics(state):
            raise ValueError("observe diagnostics differ from terminal evidence")
        if not set(result.diagnostics).issubset(supporting):
            raise ValueError("observe diagnostics are absent from supporting evidence")
        invocation = (
            None
            if result.invocation is None
            else _resolve_model(self.artifact_store, result.invocation, Invocation)
        )
        if result.fresh_observations != self.fresh_observations(state, invocation):
            raise ValueError("observe ToolResult fresh observations differ from its receipt")

    def _capture_evidence(
        self,
        state: RunState,
        *,
        invocation: Invocation | None,
    ) -> tuple[GenesisObserveOutput, GenesisObservationReceipt]:
        output = GenesisObserveOutput.model_validate(state.output)
        receipt = _resolve_model(
            self.artifact_store,
            output.observation_receipt,
            GenesisObservationReceipt,
        )
        if output.simulator_executed or receipt.simulator_executed:
            raise ValueError("controlled observe evidence cannot claim simulator execution")
        if (
            receipt.observation_child_run_id != state.run_id
            or output.images != receipt.images
            or output.video != receipt.video
            or output.media_verification != receipt.media_verification
            or output.captured_at != receipt.captured_at
            or output.valid_until != receipt.valid_until
        ):
            raise ValueError("observation output differs from its canonical receipt")
        if state.ended_at is None or not (
            state.started_at <= receipt.captured_at <= state.ended_at <= receipt.valid_until
        ):
            raise ValueError("observation capture is outside its terminal execution window")

        required = (
            output.observation_receipt,
            *output.images,
            *((output.video,) if output.video is not None else ()),
            output.media_verification,
        )
        supporting = set(state.artifacts)
        if not set(required).issubset(supporting):
            raise ValueError("observation receipt or media is absent from supporting evidence")
        for artifact in required:
            self.artifact_store.resolve(artifact)

        if invocation is not None:
            if (
                invocation.run_id != state.run_id
                or invocation.skill_id != "genesis.observe"
                or invocation.skill_version != "1.0.0"
                or invocation.invocation_digest != state.invocation_digest
            ):
                raise ValueError("observation invocation differs from its terminal child")
            request = GenesisObserveInput.model_validate(invocation.effective_parameters)
            if (
                receipt.environment_package != request.environment_package
                or receipt.replay_workflow_receipt != request.replay_workflow_receipt
                or receipt.replay_runtime_receipt != request.replay_runtime_receipt
                or receipt.checkpoint != request.checkpoint
                or receipt.capture_plan != request.capture_plan
                or receipt.requested_observation_keys != request.requested_observation_keys
                or receipt.ttl_seconds != request.ttl_seconds
            ):
                raise ValueError("observation receipt differs from the exact invocation input")
        return output, receipt


def _resolve_model(store: LocalArtifactStore, ref: ArtifactRef, model: type[_Model]) -> _Model:
    payload = store.resolve(ref).path.read_bytes()
    value = model.model_validate_json(payload)
    if payload != _canonical_bytes(value.model_dump(mode="json")):
        raise ValueError("trusted observation evidence is not canonical JSON")
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


__all__ = ["ControlledGenesisObserveEvidencePolicy"]
