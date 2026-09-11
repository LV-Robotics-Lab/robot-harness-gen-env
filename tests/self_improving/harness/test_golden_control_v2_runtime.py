"""V2 controls cross the public durable Harness seam, with real SQLite/CAS."""

from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.schemas.golden_control_v2 import AdvisoryRecordCommandV2
from self_improving.harness.schemas.workflow import RunReadRequest
from tests.self_improving.harness import test_golden_control_submit as base


def test_v2_diagnostic_advisory_commits_and_reopens_without_live_application(tmp_path):
    case = base._case(tmp_path)
    values = base._advisory_command(case, audit_accesses=False).model_dump(mode="json")
    values.update(schema_version="harness.advisory_record_command.v2", observation_citations=[])
    read = RunReadRequest(schema_version="harness.run_read_request.v1",
        principal_id=case.head.principal_id,workflow_run_id=case.head.workflow_run_id)
    sources=(case.source_prompt,case.failure_receipt,case.diagnostic)
    values['resource_access_receipts']=sorted([
        case.harness.read_workflow_artifact_audited(read,sha256=r.sha256,
            resource_uri=f"harness://workflows/{case.head.workflow_run_id}/artifacts/{r.sha256}")
        .access_receipt.model_dump(mode="json") for r in sources],key=lambda r:r['sha256'])
    command=AdvisoryRecordCommandV2.model_validate(values)
    result=case.harness.submit_control(command)
    assert result.schema_version=='harness.control_commit_snapshot.v2'
    assert result.base_state==result.resulting_state==case.head.state_ref
    reopened=GoldenRunHarness(artifact_store=case.store,workflow_store=case.workflow_store,
                             clock=case.clock)
    assert reopened.read(read).receipt_head==result.receipt
    assert reopened.submit_control(command)==result
