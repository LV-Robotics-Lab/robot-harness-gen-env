"""Publication consumes the current successful semantic-validation operation."""

import hashlib
from pathlib import Path

from .golden_execution import SkillAuthorizationContext, _create_golden_execution_binding
from .limited_image_preview_application import LimitedImagePreviewApplication
from .limited_image_preview_policy import LimitedImagePreviewPolicy, _SharedAdapter
from .schemas.common import RunState, RunStatus
from .schemas.limited_image_preview import LimitedImagePreviewManifestV1
from .schemas.workflow_operation import (
    ReceiptDerivedFactClaimV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)


class LimitedPreviewPublicationPolicy(LimitedImagePreviewPolicy):
    def __post_init__(self):
        if (
            self.skill_ref != "image2env.preview_publish@1.0.0"
            or type(self.application) is not LimitedImagePreviewApplication
        ):
            raise TypeError("publication requires its exact shared application")

    @property
    def input_model(self):
        return LimitedImagePreviewManifestV1

    @property
    def evidence_policy_implementation_sha256(self):
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def authorize_workflow(self, state, parameters, context):
        self.authorize(state, parameters)
        if type(context) is not SkillAuthorizationContext:
            raise TypeError("publication requires an exact workflow context")
        head = self._read(context.receipt_head, WorkflowOperationReceiptV2)
        final = parameters.revised_stage or parameters.initial_stage
        if (
            parameters.workflow_run_id != context.workflow_run_id
            or parameters.principal_id != context.principal_id
            or final.workflow_head != context.receipt_head
            or head.committed_revision != context.revision
            or head.resulting_state != context.state_ref
            or head.resulting_state_sha256 != state.state_sha256
            or head.status != RunStatus.SUCCEEDED
        ):
            raise ValueError("publication must consume the current final validation head")
        receipt = self._read(head.tool_receipt, TrustedToolReceiptV2)
        terminal = self.child_authority.read_run_state(receipt.child_run_id)
        if (
            receipt.skill.skill_ref != "image2env.validate@3.0.0"
            or receipt.workflow_run_id != context.workflow_run_id
            or receipt.principal_id != context.principal_id
            or terminal is None
            or terminal.status != RunStatus.SUCCEEDED
            or terminal != self._read(receipt.run_state, RunState)
            or final.validation not in terminal.artifacts
            or terminal.output != final.validation_result.model_dump(mode="json")
        ):
            raise ValueError("publication differs from its durable successful validation")

    def claims(self, state):
        self._terminal(state)
        if state.status != RunStatus.SUCCEEDED:
            return ()
        manifest = LimitedImagePreviewManifestV1.model_validate(state.output)
        refs = [ref for ref in state.artifacts if ref.schema_version == manifest.schema_version]
        if len(refs) != 1 or self._read(refs[0], type(manifest)) != manifest:
            raise ValueError("publication differs from its canonical manifest")
        return (
            ReceiptDerivedFactClaimV2(
                key="image.preview_package",
                value={"run_id": str(state.run_id), "receipt": refs[0].model_dump(mode="json")},
            ),
        )


def create_limited_publication_binding(application):
    policy = LimitedPreviewPublicationPolicy(application, "image2env.preview_publish@1.0.0", {})
    return _create_golden_execution_binding(
        policy=policy,
        application=_SharedAdapter(application, policy),
        child_authority=application.run_store,
    )
