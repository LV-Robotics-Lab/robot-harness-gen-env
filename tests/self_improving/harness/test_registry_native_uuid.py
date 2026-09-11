"""UUID-bearing committed schemas retain canonical identity in the public kernel."""

from datetime import datetime, timezone
from uuid import uuid4

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.registry import (
    HandlerResult,
    QualificationCandidate,
    SkillRegistry,
    StaticDependencyResolver,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas.common import Invocation


def test_committed_uuid_model_roundtrips_through_real_registry(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    registry = SkillRegistry(
        artifact_resolver=LocalArtifactStore(tmp_path / "cas"),
        dependency_resolver=StaticDependencyResolver({"test.echo@1.0.0": ()}),
        event_sink=SQLiteEventJournal(tmp_path / "runs.sqlite"),
        clock=lambda: datetime.now(timezone.utc),
        run_id_factory=uuid4,
        run_store=store,
    )
    candidate = QualificationCandidate(
        skill_id="test.echo",
        version="1.0.0",
        input_schema="harness.skill_invocation.v1",
        output_schema="harness.skill_invocation.v1",
        max_attempts=1,
    )
    value = Invocation(
        run_id=uuid4(),
        skill_id="test.source",
        skill_version="1.0.0",
        effective_parameters={},
        dependencies=(),
        max_attempts=1,
        invocation_digest="a" * 64,
    )

    def echo(parameters, context):
        return HandlerResult(parameters)

    first = registry.evaluate_candidate(candidate, echo, value.model_dump(mode="json"))
    second = registry.evaluate_candidate(
        candidate,
        echo,
        Invocation.model_validate_json(value.model_dump_json()).model_dump(mode="json"),
    )
    assert first.state.status.value == "succeeded"
    assert first.invocation.effective_parameters["run_id"] == str(value.run_id)
    assert first.invocation.invocation_digest == second.invocation.invocation_digest
    assert first.state.run_id != second.state.run_id
    assert store.read_run_state(first.state.run_id) == first.state
