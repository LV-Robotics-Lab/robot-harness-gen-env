import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.experiment_skill_policy import ExperimentSkillEvidencePolicy
from self_improving.harness.schemas.common import Event, RunState
from self_improving.harness.schemas.experiment import ExperimentSkillOutput
from self_improving.harness.schemas.workflow_operation import ToolResultV2
from self_improving.harness.trusted_state import WorldFact, build_world_state


def test_authorization_rejects_untrusted_dependency_despite_valid_cas(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    path = tmp_path / "input"
    path.write_text("{}")
    ref = store.put_file(path, name="input", media_type="application/json", schema_version=None)
    now = datetime.now(timezone.utc)
    world = build_world_state(
        (
            WorldFact(
                key="request.experiment_input",
                value=ref.model_dump(mode="json"),
                source_kind="user_input",
                source_artifact=ref,
                observed_at=now,
                valid_until=None,
            ),
        ),
        as_of=now,
    )
    policy = ExperimentSkillEvidencePolicy(
        "experiment.interpret@1.0.0", "interpret", store, "a" * 64
    )
    parameters = {
        "schema_version": "harness.experiment_skill_input.v1",
        "input_ref": ref.model_dump(mode="json"),
        "dependencies": {},
        "proposal": {},
    }
    policy.authorize(world, policy.normalize_parameters(parameters))
    parameters["dependencies"] = {"environment.replay": ref.model_dump(mode="json")}
    with pytest.raises(ValueError, match="trusted dependency"):
        policy.authorize(world, policy.normalize_parameters(parameters))


def test_result_requires_exact_persisted_terminal_and_has_no_fresh_claim(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")

    def put(value, schema=None):
        path = tmp_path / "payload.json"
        path.write_text(json.dumps(value))
        return store.put_file(
            path, name="payload.json", media_type="application/json", schema_version=schema
        )

    ref = put({"actual": "receipt"})
    now = datetime.now(timezone.utc)
    output = ExperimentSkillOutput(stage="interpret", status="passed", receipt=ref)
    state = RunState(
        run_id=uuid4(),
        invocation_digest="b" * 64,
        skill_id="experiment.interpret",
        skill_version="1.0.0",
        status="succeeded",
        attempt=1,
        max_attempts=1,
        started_at=now,
        ended_at=now,
        events=(
            Event(
                seq=1,
                timestamp=now,
                stage="run",
                attempt=1,
                from_status=None,
                to_status="running",
                artifact_refs=(),
            ),
            Event(
                seq=2,
                timestamp=now,
                stage="run",
                attempt=1,
                from_status="running",
                to_status="succeeded",
                artifact_refs=(ref,),
            ),
        ),
        artifacts=(ref,),
        output=output.model_dump(mode="json"),
        blocker=None,
    )
    policy = ExperimentSkillEvidencePolicy(
        "experiment.interpret@1.0.0", "interpret", store, "a" * 64
    )
    claims = policy.claims(state)
    assert claims[0].key == "experiment.intent"
    assert claims[0].value == ref.model_dump(mode="json")
    assert policy.fresh_observations(state, None) == ()
    result = ToolResultV2(
        schema_version="harness.tool_result.v2",
        workflow_run_id=uuid4(),
        operation_id=uuid4(),
        skill_ref=policy.skill_ref,
        status="succeeded",
        started_at=now,
        ended_at=now,
        child_run_id=state.run_id,
        invocation=put({}, "harness.skill_invocation.v1"),
        run_state=put(state.model_dump(mode="json"), "harness.run_state.v1"),
        typed_output=state.output,
        state_delta=put({}, "harness.state_delta.v2"),
        diagnostics=(),
        trusted_receipt=put({}, "harness.trusted_tool_receipt.v2"),
        fresh_observations=(),
        blocker=None,
    )
    policy.verify_result(state=state, claims=claims, result=result, supporting=(ref,))
    bad = result.model_copy(update={"run_state": put({}, "harness.run_state.v1")})
    with pytest.raises(ValueError):
        policy.verify_result(state=state, claims=claims, result=bad, supporting=(ref,))
    failed = state.model_copy(
        update={"output": output.model_copy(update={"status": "failed"}).model_dump(mode="json")}
    )
    assert policy.claims(failed) == ()
    assert policy.diagnostics(failed) == (ref,)
