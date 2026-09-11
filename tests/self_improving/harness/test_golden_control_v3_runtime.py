"""Native layout revision crosses the same durable Golden control journal."""

import hashlib

from self_improving.harness.schemas.native_prompt_revision import PromptRevisionCommandV3
from tests.self_improving.harness.test_golden_control_v2_runtime import native_case


def test_native_layout_revision_commits_and_retries_in_existing_journal(tmp_path):
    case = native_case(tmp_path)
    advisory = case.harness.submit_control(case.command)
    head = case.harness.read(case.read)
    text = "Move the mouse to x=0.1 m, y=0.0 m, yaw=0.3 rad."
    digest = hashlib.sha256(text.encode()).hexdigest()
    from uuid import uuid4

    command = PromptRevisionCommandV3(
        schema_version="harness.prompt_revision_command.v3",
        command_id=uuid4(), workflow_run_id=head.workflow_run_id,
        principal_id=head.principal_id, idempotency_key="native-layout",
        expected_workflow_revision=head.revision, expected_state_sha256=head.state_sha256,
        expected_receipt_head_sha256=head.receipt_head.sha256,
        source_prompt=case.command.source_prompt, advisory_receipt=advisory.receipt,
        failure_receipts=case.command.failure_receipts, predecessor_environment=case.native,
        patch=dict(kind="full_replace",base_text_sha256=case.command.source_prompt.sha256,
                   replacement_text=text,reason="placement mismatch"),
        fallback=dict(kind="none"),next_compile_skill_ref="image2env.compile@3.0.0",
        external_agent_executed=False,
        layout_proposal=dict(
            schema_version="harness.tabletop_layout_revision.v1",
            workflow_run_id=head.workflow_run_id,principal_id=head.principal_id,
            base_revision=head.revision,state_sha256=head.state_sha256,
            receipt_head_sha256=head.receipt_head.sha256,
            original_source=case.native.model_copy(update={"schema_version":None}),
            source_object_id="mouse",replacement_text=text,
            effective_prompt=dict(uri="artifact://sha256/"+digest,sha256=digest,bytes=len(text),
                name="revised_prompt.txt",media_type="text/plain",schema_version="harness.prompt_text.v1"),
            position_xy_m=(0.1,0.0),yaw_rad=0.3,table_center_m=(0.0,0.0,0.7),
            table_size_m=(1.0,1.0,0.1),relation="on_table",
            physical_properties="preserve_qualified_asset",
        ),
    )
    result = case.harness.submit_control(command)
    assert result.schema_version == "harness.control_commit_snapshot.v3"
    assert case.harness.submit_control(command) == result
    assert case.harness.read(case.read).receipt_head == result.receipt
