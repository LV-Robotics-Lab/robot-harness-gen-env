"""Workflow-scoped evidence for revised compile and the shared preview runtime."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .golden_execution import SkillAuthorizationContext, _create_golden_execution_binding
from .limited_image_preview_application import LimitedImagePreviewApplication
from .revised_image_compile_application import RevisedImageCompileApplication
from .schemas.common import Invocation, RunState, RunStatus
from .schemas.genesis_fresh_capture import GenesisFreshCaptureReceiptV1
from .schemas.genesis_scene_execution_v3 import (
    GenesisSceneCompileBindingV2,
    GenesisSceneExecutionReceiptV3,
)
from .schemas.limited_image_preview_execution import (
    LimitedSceneValidateInputV1,
    LimitedSceneValidationV1,
)
from .schemas.native_prompt_revision import Image2EnvGenesisCompileReceiptV2, PromptRevisionV3
from .schemas.revised_image_compile_application import RevisedCompileInvocationV1
from .schemas.workflow_operation import (
    ReceiptDerivedFactClaimV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)

_MODELS = {
    "image2env.compile@3.0.0": (
        RevisedCompileInvocationV1,
        Image2EnvGenesisCompileReceiptV2,
        "image.compile",
    ),
    "image2env.replay@3.0.0": (
        GenesisSceneCompileBindingV2,
        GenesisSceneExecutionReceiptV3,
        "image.replay",
    ),
    "image2env.validate@3.0.0": (
        LimitedSceneValidateInputV1,
        LimitedSceneValidationV1,
        "image.validation",
    ),
}


def _ordered(refs):
    return tuple(sorted(refs, key=lambda r: (r.sha256, r.schema_version or "", r.media_type)))


@dataclass(frozen=True)
class LimitedImagePreviewPolicy:
    application: object
    skill_ref: str
    predecessor_authorities: dict
    evidence_policy_version: str = "1.0.0"

    def __post_init__(self):
        if self.skill_ref not in _MODELS:
            raise ValueError("preview policy requires an exact supported Skill")
        expected = (
            RevisedImageCompileApplication
            if self.skill_ref == "image2env.compile@3.0.0"
            else LimitedImagePreviewApplication
        )
        if type(self.application) is not expected:
            raise TypeError("preview policy requires its exact durable application")

    @property
    def artifact_store(self):
        if self.skill_ref == "image2env.compile@3.0.0":
            return self.application.compiler.artifact_store
        return self.application.artifact_store

    @property
    def child_authority(self):
        return self.application.run_store

    @property
    def input_model(self):
        return _MODELS[self.skill_ref][0]

    @property
    def evidence_policy_id(self):
        return self.skill_ref.split("@")[0] + ".limited_preview"

    @property
    def evidence_policy_implementation_sha256(self):
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def _read(self, ref, model):
        raw = self.artifact_store.resolve(ref).path.read_bytes()
        value = model.model_validate_json(raw)
        canonical = json.dumps(
            value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if raw not in (
            canonical,
            canonical + b"\n",
        ) or ref.schema_version != value.model_dump().get("schema_version", ref.schema_version):
            raise ValueError("preview evidence must be canonical typed CAS")
        return value

    def normalize_parameters(self, parameters):
        return self.input_model.model_validate(parameters)

    def authorize(self, state, parameters):
        if type(parameters) is not self.input_model:
            raise TypeError("preview requires exact typed input")

    def authorize_workflow(self, state, parameters, context):
        self.authorize(state, parameters)
        if type(context) is not SkillAuthorizationContext:
            raise TypeError("preview requires exact workflow authorization")
        if self.skill_ref == "image2env.compile@3.0.0":
            # GoldenRun independently verifies the full attempt and prompt/layout consumption.
            revision = self._read(context.receipt_head, PromptRevisionV3)
            if (
                revision.workflow_run_id != context.workflow_run_id
                or revision.principal_id != context.principal_id
                or revision.committed_revision != context.revision
                or revision.resulting_state != context.state_ref
                or revision.resulting_state_sha256 != state.state_sha256
            ):
                raise ValueError("revision differs from current workflow head")
            return
        head = self._read(context.receipt_head, WorkflowOperationReceiptV2)
        if (
            head.workflow_run_id != context.workflow_run_id
            or head.principal_id != context.principal_id
            or head.committed_revision != context.revision
            or head.resulting_state != context.state_ref
            or head.resulting_state_sha256 != state.state_sha256
        ):
            raise ValueError("predecessor differs from current workflow head")
        parent = self._read(head.tool_receipt, TrustedToolReceiptV2)
        allowed = (
            ("image2env.compile@3.0.0",)
            if self.skill_ref == "image2env.replay@3.0.0"
            else ("genesis.observe@3.0.0", "image2env.replay@3.0.0")
        )
        if (
            parent.skill.skill_ref not in allowed
            or parent.status != RunStatus.SUCCEEDED
            or parent.workflow_run_id != context.workflow_run_id
            or parent.principal_id != context.principal_id
            or parent.operation_id != head.operation_id
            or parent.command != head.command
        ):
            raise ValueError("preview input requires this workflow's successful predecessor")
        authority = self.predecessor_authorities.get(parent.skill.skill_ref)
        if authority is None:
            raise ValueError("missing trusted predecessor authority")
        terminal = authority.read_run_state(parent.child_run_id)
        invocation = authority.read_invocation(parent.child_run_id)
        if (
            terminal is None
            or invocation is None
            or terminal != self._read(parent.run_state, RunState)
            or invocation != self._read(parent.invocation, Invocation)
            or terminal.output != parent.typed_output
            or f"{terminal.skill_id}@{terminal.skill_version}" != parent.skill.skill_ref
            or terminal.status != RunStatus.SUCCEEDED
        ):
            raise ValueError("workflow predecessor differs from durable child authority")
        if self.skill_ref == "image2env.replay@3.0.0":
            output = Image2EnvGenesisCompileReceiptV2.model_validate(terminal.output)
            if (
                parameters.compile_run_id != parent.child_run_id
                or parameters.compile_request != output.request
                or self._read(parameters.compile_receipt, type(output)) != output
                or parameters.compile_receipt not in terminal.artifacts
            ):
                raise ValueError("replay must consume current registered compile")
            self.application.resolve_replay(parameters)
        elif parent.skill.skill_ref == "genesis.observe@3.0.0":
            output = GenesisFreshCaptureReceiptV1.model_validate(terminal.output)
            if parameters.execution_receipt != output.replay_receipt:
                raise ValueError("validation must consume observed initial replay")
        elif (
            parameters.execution_receipt not in terminal.artifacts
            or self._read(parameters.execution_receipt, GenesisSceneExecutionReceiptV3).model_dump(
                mode="json"
            )
            != terminal.output
        ):
            raise ValueError("validation must consume current modified replay")

    def _terminal(self, state):
        invocation = self.child_authority.read_invocation(state.run_id)
        if (
            self.child_authority.read_run_state(state.run_id) != state
            or invocation is None
            or invocation.invocation_digest != state.invocation_digest
            or f"{invocation.skill_id}@{invocation.skill_version}" != self.skill_ref
            or any(e.stage.startswith("qualification_candidate.") for e in state.events)
        ):
            raise ValueError("preview terminal differs from registered SQLite authority")
        return invocation

    def claims(self, state):
        invocation = self._terminal(state)
        if state.status != RunStatus.SUCCEEDED:
            return ()
        model = _MODELS[self.skill_ref][1]
        output = model.model_validate(state.output)
        refs = [r for r in state.artifacts if r.schema_version == output.schema_version]
        if len(refs) != 1 or self._read(refs[0], model) != output:
            raise ValueError("preview output differs from its exact CAS receipt")
        claims = (
            ReceiptDerivedFactClaimV2(
                key=_MODELS[self.skill_ref][2],
                value={"run_id": str(state.run_id), "receipt": refs[0].model_dump(mode="json")},
            ),
        )
        if self.skill_ref == "image2env.compile@3.0.0":
            claims += (
                ReceiptDerivedFactClaimV2(
                    operation="retract", key="image.fresh_capture", value=None
                ),
            )
        elif self.skill_ref == "image2env.validate@3.0.0":
            parameters = LimitedSceneValidateInputV1.model_validate(invocation.effective_parameters)
            if (
                parameters.execution_receipt.schema_version
                == "harness.genesis_scene_execution_receipt.v2"
                and parameters.expected_layout is None
            ):
                claims += (
                    ReceiptDerivedFactClaimV2(
                        operation="retract", key="image.fresh_capture", value=None
                    ),
                )
        return tuple(sorted(claims, key=lambda c: c.key))

    def diagnostics(self, state):
        self._terminal(state)
        if state.status == RunStatus.SUCCEEDED:
            return ()
        refs = {r for e in state.events for r in e.artifact_refs}
        if state.blocker is not None:
            refs.update(state.blocker.artifact_refs)
        for ref in refs:
            self.artifact_store.resolve(ref)
        return _ordered(refs)

    def fresh_observations(self, state, invocation):
        return ()

    def verify_result(self, *, state, claims, result, supporting):
        invocation = self._terminal(state)
        if (
            result.child_run_id != state.run_id
            or result.skill_ref != self.skill_ref
            or result.status != state.status
            or result.typed_output != state.output
            or self._read(result.run_state, RunState) != state
            or self._read(result.invocation, Invocation) != invocation
            or claims != self.claims(state)
            or result.diagnostics != self.diagnostics(state)
            or result.fresh_observations != ()
            or supporting != _ordered(state.artifacts)
        ):
            raise ValueError("preview result differs from durable child evidence")
        for ref in supporting:
            self.artifact_store.resolve(ref)


@dataclass(frozen=True)
class _SharedAdapter:
    application: LimitedImagePreviewApplication
    policy: LimitedImagePreviewPolicy

    @property
    def skill_ref(self):
        return self.policy.skill_ref

    @property
    def artifact_root(self):
        return self.application.artifact_store.root

    @property
    def authority_path(self):
        return self.application.run_store.path

    @property
    def skills(self):
        return tuple(
            s for s in self.application.skills if f"{s.skill_id}@{s.version}" == self.skill_ref
        )

    def invoke(self, parameters, *, run_id):
        return self.application.invoke(self.skill_ref, parameters, run_id=run_id)

    def invocation(self, run_id):
        return self.application.invocation(run_id)

    def run_state(self, run_id):
        return self.application.run_state(run_id)


def create_shared_preview_bindings(application, *, predecessor_authorities):
    bindings = []
    for skill in ("image2env.replay@3.0.0", "image2env.validate@3.0.0"):
        policy = LimitedImagePreviewPolicy(application, skill, predecessor_authorities)
        bindings.append(
            _create_golden_execution_binding(
                policy=policy,
                application=_SharedAdapter(application, policy),
                child_authority=application.run_store,
            )
        )
    return tuple(bindings)
