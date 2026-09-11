"""Native image Skill evidence and exact parent-workflow authorization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .artifacts import LocalArtifactStore
from .golden_execution import SkillAuthorizationContext
from .run_store import SQLiteRunStore
from .schemas.common import Invocation, RunState, RunStatus
from .schemas.genesis_fresh_capture import GenesisFreshCaptureInputV1, GenesisFreshCaptureReceiptV1
from .schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from .schemas.genesis_scene_assessment import GenesisSceneValidateInputV1, GenesisSceneValidationV1
from .schemas.genesis_scene_execution_v2 import (
    GenesisSceneExecutionReceiptV2,
    GenesisSceneExecutionRequestV2,
)
from .schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileReceiptV1,
    Image2EnvGenesisCompileRequestV1,
)
from .schemas.workflow_operation import (
    ReceiptDerivedFactClaimV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from .schemas.x2env_compile_evidence import SceneInterpretationProposalV1
from .trusted_state import WorldFact

_TYPES = {
    "image2env.compile@2.0.0": (
        Image2EnvGenesisCompileRequestV1,
        Image2EnvGenesisCompileReceiptV1,
        "image.compile",
    ),
    "image2env.replay@2.0.0": (
        GenesisSceneReplayInputV2,
        GenesisSceneExecutionReceiptV2,
        "image.replay",
    ),
    "genesis.observe@3.0.0": (
        GenesisFreshCaptureInputV1,
        GenesisFreshCaptureReceiptV1,
        "image.observation",
    ),
    "image2env.validate@2.0.0": (
        GenesisSceneValidateInputV1,
        GenesisSceneValidationV1,
        "image.validation",
    ),
}
_TERMINAL_EVIDENCE_SCHEMAS = {
    "harness.native_read_closure.v1",
    "harness.native_terminal_admission.v1",
}


def _native_supporting(supporting):
    evidence = tuple(ref for ref in supporting if ref.schema_version in _TERMINAL_EVIDENCE_SCHEMAS)
    if evidence and (
        len(evidence) != 2 or {ref.schema_version for ref in evidence} != _TERMINAL_EVIDENCE_SCHEMAS
    ):
        raise ValueError("native predecessor has an incomplete terminal evidence pair")
    return tuple(ref for ref in supporting if ref.schema_version not in _TERMINAL_EVIDENCE_SCHEMAS)


@dataclass(frozen=True)
class NativeImageEvidencePolicy:
    """Project SQLite-owned native terminals without introducing another authority."""

    artifact_store: LocalArtifactStore
    child_authority: SQLiteRunStore
    skill_ref: str
    predecessor_authorities: dict = field(default_factory=dict)
    evidence_policy_version: str = "1.0.0"

    def __post_init__(self):
        if self.skill_ref not in _TYPES:
            raise ValueError("native policy requires one exact supported Skill")
        if (
            type(self.artifact_store) is not LocalArtifactStore
            or type(self.child_authority) is not SQLiteRunStore
        ):
            raise TypeError("native policy requires exact CAS and SQLite authorities")
        if any(
            key not in _TYPES or type(value) is not SQLiteRunStore
            for key, value in self.predecessor_authorities.items()
        ):
            raise TypeError("predecessors require exact native Skill SQLite authorities")
        object.__setattr__(
            self, "predecessor_authorities", MappingProxyType(dict(self.predecessor_authorities))
        )

    @property
    def input_model(self):
        return _TYPES[self.skill_ref][0]

    @property
    def evidence_policy_id(self):
        return _TYPES[self.skill_ref][2] + ".evidence"

    @property
    def evidence_policy_implementation_sha256(self):
        return hashlib.sha256(
            b"native.image.evidence.v1\0" + Path(__file__).read_bytes()
        ).hexdigest()

    def normalize_parameters(self, parameters):
        return self.input_model.model_validate(parameters)

    def authorize(self, state, parameters):
        if type(parameters) is not self.input_model:
            raise TypeError("native policy requires its exact input model")
        if self.skill_ref == "image2env.compile@2.0.0":
            facts = {fact.key: fact.value for fact in state.facts}
            proposal = self._read(
                parameters.compile_input.interpretation_proposal, SceneInterpretationProposalV1
            )
            prompt = self.artifact_store.resolve(proposal.effective_prompt).path.read_text()
            if (
                facts.get("request.image_source")
                != parameters.compile_input.source_media.model_dump(mode="json")
                or facts.get("request.objective") != prompt
            ):
                raise ValueError("compile media and objective must equal trusted initial facts")

    def _read(self, ref, model):
        raw = self.artifact_store.resolve(ref).path.read_bytes()
        value = model.model_validate_json(raw)
        canonical = json.dumps(
            value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if ref.schema_version != value.model_dump(mode="json").get(
            "schema_version", ref.schema_version
        ) or raw not in (canonical, canonical + b"\n"):
            raise ValueError("evidence requires exact typed canonical CAS bytes")
        return value

    def authorize_workflow(self, state, parameters, context):
        self.authorize(state, parameters)
        if type(context) is not SkillAuthorizationContext:
            raise TypeError("native policy requires exact workflow context")
        if self.skill_ref == "image2env.compile@2.0.0":
            return
        head = self._read(context.receipt_head, WorkflowOperationReceiptV2)
        if (
            head.workflow_run_id,
            head.principal_id,
            head.committed_revision,
            head.resulting_state,
            head.resulting_state_sha256,
        ) != (
            context.workflow_run_id,
            context.principal_id,
            context.revision,
            context.state_ref,
            state.state_sha256,
        ):
            raise ValueError("predecessor differs from current workflow context")
        parent = self._read(head.tool_receipt, TrustedToolReceiptV2)
        expected = {
            "image2env.replay@2.0.0": "image2env.compile@2.0.0",
            "genesis.observe@3.0.0": "image2env.replay@2.0.0",
            "image2env.validate@2.0.0": "genesis.observe@3.0.0",
        }[self.skill_ref]
        if (
            parent.workflow_run_id != context.workflow_run_id
            or parent.principal_id != context.principal_id
            or parent.operation_id != head.operation_id
            or parent.command != head.command
            or parent.base_revision != head.base_revision
            or parent.base_state_sha256 != head.base_state_sha256
            or parent.status != RunStatus.SUCCEEDED
            or parent.skill.skill_ref != expected
        ):
            raise ValueError("current workflow predecessor is not the required successful Skill")
        authority = self.predecessor_authorities.get(expected)
        if authority is None:
            raise ValueError("missing trusted predecessor SQLite authority")
        terminal = authority.read_run_state(parent.child_run_id)
        invocation = authority.read_invocation(parent.child_run_id)
        if (
            terminal is None
            or invocation is None
            or terminal != self._read(parent.run_state, type(terminal))
            or terminal.output != parent.typed_output
            or invocation.invocation_digest != parent.invocation_digest
            or invocation != self._read(parent.invocation, Invocation)
            or terminal.status != RunStatus.SUCCEEDED
            or (terminal.skill_id, terminal.skill_version) != tuple(expected.split("@"))
            or _native_supporting(parent.supporting_artifacts)
            != tuple(
                sorted(
                    terminal.artifacts,
                    key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
                )
            )
        ):
            raise ValueError("predecessor differs from its trusted child journal")
        if self.skill_ref == "image2env.replay@2.0.0":
            output = Image2EnvGenesisCompileReceiptV1.model_validate(parent.typed_output)
            request = self._read(parameters.execution_request, GenesisSceneExecutionRequestV2)
            refs = [
                ref for ref in terminal.artifacts if ref.schema_version == output.schema_version
            ]
            if (
                parameters.compile_run_id != parent.child_run_id
                or len(refs) != 1
                or request.compile_receipt != refs[0]
                or request.scene != output.scene
                or request.asset_qualification != output.qualification
            ):
                raise ValueError("replay input is not derived from current compile receipt")
        else:
            source = (
                parent.child_run_id
                if self.skill_ref == "genesis.observe@3.0.0"
                else GenesisFreshCaptureReceiptV1.model_validate(parent.typed_output).replay_run_id
            )
            if parameters.replay_run_id != source:
                raise ValueError("input replay UUID differs from current workflow predecessor")

    def _output(self, state):
        invocation = self.child_authority.read_invocation(state.run_id)
        if (
            self.child_authority.read_run_state(state.run_id) != state
            or invocation is None
            or invocation.invocation_digest != state.invocation_digest
            or f"{invocation.skill_id}@{invocation.skill_version}" != self.skill_ref
        ):
            raise ValueError("terminal differs from exact child SQLite invocation")
        output = _TYPES[self.skill_ref][1].model_validate(state.output)
        parameters = self.normalize_parameters(invocation.effective_parameters)
        if any(event.stage.startswith("qualification_candidate.") for event in state.events):
            raise ValueError("candidate terminal has no registered execution authority")
        if self.skill_ref == "image2env.compile@2.0.0":
            if self._read(output.request, self.input_model) != parameters:
                raise ValueError("compile receipt input differs from invocation")
        elif self.skill_ref == "image2env.replay@2.0.0":
            if output.request != parameters.execution_request:
                raise ValueError("replay receipt input differs from invocation")
        elif output.replay_run_id != parameters.replay_run_id:
            raise ValueError("assessment replay source differs from invocation")
        refs = [ref for ref in state.artifacts if ref.schema_version == output.schema_version]
        if len(refs) != 1 or self._read(refs[0], type(output)) != output:
            raise ValueError("terminal typed output differs from unique CAS receipt")
        if getattr(output, "candidate_only", False):
            raise ValueError("candidate output has no registered execution authority")
        return refs[0], output

    def claims(self, state):
        if state.status != RunStatus.SUCCEEDED:
            return ()
        ref, _ = self._output(state)
        claims = (
            ReceiptDerivedFactClaimV2(
                key=_TYPES[self.skill_ref][2],
                value={"run_id": str(state.run_id), "receipt": ref.model_dump(mode="json")},
            ),
        )
        if self.skill_ref == "genesis.observe@3.0.0":
            observations = self.fresh_observations(
                state, self.child_authority.read_invocation(state.run_id)
            )
            claims += tuple(
                ReceiptDerivedFactClaimV2(key=item.key, value=item.value) for item in observations
            )
        return tuple(sorted(claims, key=lambda claim: claim.key))

    def diagnostics(self, state):
        return (
            ()
            if state.blocker is None
            else tuple(
                sorted(
                    state.blocker.artifact_refs,
                    key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
                )
            )
        )

    def fresh_observations(self, state, invocation):
        if self.skill_ref != "genesis.observe@3.0.0" or state.status != RunStatus.SUCCEEDED:
            return ()
        if invocation != self.child_authority.read_invocation(state.run_id):
            raise ValueError("fresh observation invocation differs from child authority")
        ref, output = self._output(state)
        if (
            state.ended_at is None
            or not state.started_at <= output.captured_at <= state.ended_at <= output.expires_at
        ):
            raise ValueError("fresh capture lies outside terminal execution freshness window")
        self.artifact_store.resolve(output.image)
        return (
            WorldFact(
                key="image.fresh_capture",
                value={
                    "image": output.image.model_dump(mode="json"),
                    "authority": output.authority,
                    "process_continuation": False,
                },
                source_kind="fresh_observation",
                source_artifact=ref,
                observed_at=output.captured_at,
                valid_until=output.expires_at,
            ),
        )

    def verify_result(self, *, state, claims, result, supporting):
        if (
            self.child_authority.read_run_state(state.run_id) != state
            or result.child_run_id != state.run_id
            or result.skill_ref != self.skill_ref
            or result.status != state.status
            or self._read(result.run_state, RunState) != state
        ):
            raise ValueError("ToolResult child identity differs from SQLite terminal")
        actual_invocation = self.child_authority.read_invocation(state.run_id)
        supplied_invocation = (
            None if result.invocation is None else self._read(result.invocation, Invocation)
        )
        if supplied_invocation != actual_invocation:
            raise ValueError("ToolResult invocation differs from SQLite authority")
        if (
            result.typed_output != state.output
            or claims != self.claims(state)
            or result.diagnostics != self.diagnostics(state)
        ):
            raise ValueError("ToolResult differs from exact native child terminal")
        expected = tuple(
            sorted(
                state.artifacts,
                key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
            )
        )
        if supporting != expected or not set(result.diagnostics).issubset(supporting):
            raise ValueError("supporting evidence differs from native child terminal")
        for ref in supporting:
            self.artifact_store.resolve(ref)
        if result.fresh_observations != self.fresh_observations(
            state, self.child_authority.read_invocation(state.run_id)
        ):
            raise ValueError("fresh observation differs from native child receipt")
