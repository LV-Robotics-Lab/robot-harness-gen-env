"""Restricted workflow ancestry; original compile policy identity remains frozen."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .golden_execution import SkillAuthorizationContext, _create_golden_execution_binding
from .golden_image2env_policy import NativeImageEvidencePolicy
from .limited_image_preview_policy import LimitedImagePreviewPolicy, _SharedAdapter
from .schemas.common import ArtifactRef, Invocation, RunState, RunStatus
from .schemas.genesis_scene_execution_v2 import GenesisSceneExecutionReceiptV2
from .schemas.workflow_operation import TrustedToolReceiptV2, WorkflowOperationReceiptV2


class LimitedPreviewValidationPolicy(LimitedImagePreviewPolicy):
    """Permit semantic validation before fresh capture, without changing compile identity."""

    @property
    def evidence_policy_implementation_sha256(self):
        root = Path(__file__).parent
        members = tuple(
            (name, hashlib.sha256((root / name).read_bytes()).hexdigest())
            for name in (
                "limited_image_preview_policy.py",
                "limited_image_preview_workflow_policies.py",
            )
        )
        return hashlib.sha256(json.dumps(members, separators=(",", ":")).encode()).hexdigest()

    def authorize_workflow(self, state, parameters, context):
        self.authorize(state, parameters)
        if (
            self.skill_ref != "image2env.validate@3.0.0"
            or type(context) is not SkillAuthorizationContext
        ):
            raise ValueError("direct replay validation requires exact workflow context")
        head = self._read(context.receipt_head, WorkflowOperationReceiptV2)
        parent = self._read(head.tool_receipt, TrustedToolReceiptV2)
        if parent.skill.skill_ref != "image2env.replay@2.0.0":
            return super().authorize_workflow(state, parameters, context)
        if (
            head.workflow_run_id != context.workflow_run_id
            or head.principal_id != context.principal_id
            or head.committed_revision != context.revision
            or head.resulting_state != context.state_ref
            or head.resulting_state_sha256 != state.state_sha256
            or parent.workflow_run_id != context.workflow_run_id
            or parent.principal_id != context.principal_id
            or parent.operation_id != head.operation_id
            or parent.command != head.command
            or parent.status != RunStatus.SUCCEEDED
            or parent.base_revision != head.base_revision
        ):
            raise ValueError("validation source differs from this workflow's replay head")
        authority = self.predecessor_authorities.get("image2env.replay@2.0.0")
        if authority is None:
            raise ValueError("validation requires exact replay SQLite authority")
        terminal = authority.read_run_state(parent.child_run_id)
        invocation = authority.read_invocation(parent.child_run_id)
        if (
            terminal is None
            or invocation is None
            or terminal != self._read(parent.run_state, RunState)
            or invocation != self._read(parent.invocation, Invocation)
            or terminal.output != parent.typed_output
            or parameters.execution_receipt not in terminal.artifacts
            or self._read(parameters.execution_receipt, GenesisSceneExecutionReceiptV2).model_dump(
                mode="json"
            )
            != terminal.output
        ):
            raise ValueError("validation input differs from durable replay evidence")


def create_limited_validation_binding(application, *, predecessor_authorities):
    policy = LimitedPreviewValidationPolicy(
        application, "image2env.validate@3.0.0", predecessor_authorities
    )
    return _create_golden_execution_binding(
        policy=policy,
        application=_SharedAdapter(application, policy),
        child_authority=application.run_store,
    )


class LimitedPreviewObservationPolicy(NativeImageEvidencePolicy):
    """Fresh capture after semantic validation, retaining the same initial replay owner."""

    @property
    def evidence_policy_implementation_sha256(self):
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def authorize_workflow(self, state, parameters, context):
        self.authorize(state, parameters)
        if (
            self.skill_ref != "genesis.observe@3.0.0"
            or type(context) is not SkillAuthorizationContext
        ):
            raise ValueError("limited history access is exclusive to fresh observation")
        facts = [fact for fact in state.facts if fact.key == "image.replay"]
        if len(facts) != 1 or facts[0].source_kind != "trusted_receipt":
            raise ValueError("observation requires a trusted workflow replay fact")
        fact = facts[0]
        parent = self._read(fact.source_artifact, TrustedToolReceiptV2)
        if (
            parent.workflow_run_id != context.workflow_run_id
            or parent.principal_id != context.principal_id
            or parent.skill.skill_ref != "image2env.replay@2.0.0"
            or parent.status != RunStatus.SUCCEEDED
            or str(parent.child_run_id) != fact.value.get("run_id")
            or parameters.replay_run_id != parent.child_run_id
            or parent.base_revision >= context.revision
        ):
            raise ValueError("observation source is not this workflow's initial replay")
        authority = self.predecessor_authorities.get("image2env.replay@2.0.0")
        if authority is None:
            raise ValueError("observation requires its exact replay SQLite authority")
        terminal = authority.read_run_state(parent.child_run_id)
        invocation = authority.read_invocation(parent.child_run_id)
        if (
            terminal is None
            or invocation is None
            or terminal != self._read(parent.run_state, RunState)
            or invocation != self._read(parent.invocation, Invocation)
            or terminal.output != parent.typed_output
        ):
            raise ValueError("observation predecessor differs from durable child evidence")
        ref = ArtifactRef.model_validate(fact.value.get("receipt"))
        if (
            ref not in terminal.artifacts
            or self._read(ref, GenesisSceneExecutionReceiptV2).model_dump(mode="json")
            != terminal.output
        ):
            raise ValueError("observation fact does not bind its exact replay receipt")


@dataclass(frozen=True)
class _ObservationAdapter:
    application: object
    policy: LimitedPreviewObservationPolicy

    @property
    def skill_ref(self):
        return self.policy.skill_ref

    @property
    def artifact_root(self):
        return self.policy.artifact_store.root

    @property
    def authority_path(self):
        return self.policy.child_authority.path

    @property
    def skills(self):
        return tuple(
            s for s in self.application.skills if f"{s.skill_id}@{s.version}" == self.skill_ref
        )

    def invoke(self, parameters, *, run_id):
        return self.application.invoke(parameters, run_id=run_id)

    def invocation(self, run_id):
        return self.application.invocation(run_id)

    def run_state(self, run_id):
        return self.application.run_state(run_id)


def create_limited_observation_binding(application, *, replay_authority):
    """Keep the registered camera and child authority; specialize workflow ancestry only."""
    from .genesis_fresh_capture_application import GenesisFreshCaptureApplication

    if type(application) is not GenesisFreshCaptureApplication:
        raise TypeError("limited observation requires the exact registered capture application")
    policy = LimitedPreviewObservationPolicy(
        application.producer.artifact_store,
        application.run_store,
        "genesis.observe@3.0.0",
        {"image2env.replay@2.0.0": replay_authority},
    )
    return _create_golden_execution_binding(
        policy=policy,
        application=_ObservationAdapter(application, policy),
        child_authority=application.run_store,
    )
