"""Validate a fresh capture against the owned workflow and durable child authority."""

from .schemas.common import Invocation, RunState, RunStatus
from .schemas.genesis_fresh_capture import GenesisFreshCaptureReceiptV1
from .schemas.golden_control_v2 import ObservationCitationV2
from .schemas.workflow_operation import (
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)


def verify_fresh_citation(*, citation, current, at, reachable, resolve, store, children):
    """No arbitrary registered receipt may stand in for this workflow's observation."""
    payload = store.resolve(citation.receipt).path.read_bytes()
    observation = GenesisFreshCaptureReceiptV1.model_validate_json(payload)
    # The public citation model rechecks canonical bytes/SHA/size and exact lifecycle.
    # CAS resolution already checked the payload against that same content identity.
    ObservationCitationV2.model_validate(
        {**citation.model_dump(mode="python"), "observation": observation}
    )
    if not citation.captured_at <= at <= citation.expires_at:
        raise ValueError("fresh capture citation expired at control commit")
    matches = []
    for ref in reachable:
        if ref.schema_version != "harness.workflow_operation_receipt.v2":
            continue
        operation = resolve(ref, WorkflowOperationReceiptV2)
        tool = resolve(operation.tool_receipt, TrustedToolReceiptV2)
        if (
            tool.skill.skill_ref != "genesis.observe@3.0.0"
            or tool.status is not RunStatus.SUCCEEDED
        ):
            continue
        if citation.receipt not in tool.supporting_artifacts:
            continue
        result = resolve(operation.tool_result, ToolResultV2)
        state = resolve(tool.run_state, RunState)
        # TrustedToolReceiptV2 requires a typed invocation for succeeded results.
        invocation = resolve(tool.invocation, Invocation)
        if (
            operation.workflow_run_id != current.workflow_run_id
            or operation.principal_id != current.principal_id
            or tool.workflow_run_id != current.workflow_run_id
            or tool.principal_id != current.principal_id
            or operation.status is not RunStatus.SUCCEEDED
            or state.status is not RunStatus.SUCCEEDED
            or state.skill_id != "genesis.observe"
            or state.skill_version != "3.0.0"
            or not (operation.operation_id == tool.operation_id == result.operation_id)
            or not (tool.child_run_id == result.child_run_id == state.run_id == invocation.run_id)
            or tool.run_state != result.run_state
            or tool.invocation != result.invocation
            or state.invocation_digest != invocation.invocation_digest
            or state.output != observation.model_dump(mode="json")
            or tool.typed_output != state.output
            or result.typed_output != state.output
            or invocation.effective_parameters.get("replay_run_id")
            != str(observation.replay_run_id)
            or invocation.effective_parameters.get("ttl_seconds") != observation.ttl_seconds
            or tuple(r for r in state.artifacts if r.schema_version == observation.schema_version)
            != (citation.receipt,)
        ):
            raise ValueError("fresh capture workflow, child or replay lineage differs")
        authority = children("genesis.observe@3.0.0")
        if authority is None:
            raise ValueError("fresh capture has no trusted child authority")
        lifecycle = authority.read_controlled_lifecycle(state.run_id)
        if (
            lifecycle.terminal != state
            or lifecycle.invocation != invocation
            or lifecycle.intent is None
        ):
            raise ValueError("fresh capture differs from durable child lifecycle")
        matches.append(operation)
    if len(matches) != 1:
        raise ValueError("fresh capture must belong to one reachable observation operation")
