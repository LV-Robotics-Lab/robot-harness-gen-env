"""S1 development admission never confers release qualification."""

from datetime import datetime, timezone

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.development_execution import capture_development_authority
from self_improving.harness.golden_run import GoldenRunHarness, GoldenRunRegistryError
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.schemas.workflow import RunReadRequest, RunStartRequest
from tests.self_improving.harness.test_golden_run import OBJECTIVE_EVIDENCE, _put


def test_development_registry_requires_deployment_authority_and_ephemeral_workspace(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    source = tmp_path / "implementation.py"
    source.write_text("VERSION = 1\n")
    config = tmp_path / "deployment.json"
    config.write_text('{"execution_mode":"development","allow_changed_skill_closure":true}')
    authority = capture_development_authority(
        artifact_store=store,
        source_files=(source,),
        configuration=config,
    )
    registry = authority.registry(())
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    request = RunStartRequest(
        schema_version="harness.run_start_request.v1",
        principal_id="tests/dev",
        idempotency_key="dev-1",
        workspace="ephemeral",
        requested_profile="genesis.rigid_scene@1",
        initial_fact_evidence=(objective,),
        registry_snapshot=registry,
    )
    kwargs = dict(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.db"),
        clock=lambda: datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    with pytest.raises(GoldenRunRegistryError, match="development"):
        GoldenRunHarness(**kwargs).start(request)
    harness = GoldenRunHarness(**kwargs, development_authority=authority)
    snapshot = harness.start(request)
    assert snapshot.workspace == "ephemeral"
    assert (
        harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=request.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
            )
        )
        == snapshot
    )
    with pytest.raises(GoldenRunRegistryError, match="ephemeral"):
        harness.start(
            request.model_copy(update={"workspace": "production", "idempotency_key": "dev-2"})
        )
    source.write_text("VERSION = 2\n")
    with pytest.raises((GoldenRunRegistryError, ValueError), match="changed"):
        harness.start(request.model_copy(update={"idempotency_key": "dev-3"}))


def test_development_child_commits_explicit_unqualified_receipt_and_reopens(tmp_path):
    from uuid import uuid4

    from self_improving.harness.development_execution import (
        DevelopmentSkillDescriptor,
        create_development_execution_binding,
    )
    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.golden_execution import create_golden_execution_catalog
    from self_improving.harness.registry import HandlerResult, SkillRegistry
    from self_improving.harness.run_store import SQLiteRunStore
    from self_improving.harness.schemas.workflow import RunCommandRequest

    class Dependencies:
        def resolve(self, skill, parameters):
            return ()

    class Policy:
        skill_ref = "development.echo@1.0.0"
        input_model = RunReadRequest
        evidence_policy_id = "development.echo"
        evidence_policy_version = "1.0.0"
        evidence_policy_implementation_sha256 = "a" * 64

        def normalize_parameters(self, parameters):
            import json

            return self.input_model.model_validate_json(json.dumps(parameters))

        def authorize(self, state, parameters):
            pass

        def claims(self, state):
            return ()

        def diagnostics(self, state):
            return ()

        def fresh_observations(self, state, invocation=None):
            return ()

        def verify_result(self, **kwargs):
            assert kwargs["state"].output == kwargs["result"].typed_output

    store = LocalArtifactStore(tmp_path / "cas")
    source = tmp_path / "source.py"
    source.write_text("# Controlled echo tests journal, not simulation.\n")
    config = tmp_path / "config.json"
    config.write_text('{"execution_mode":"development","allow_changed_skill_closure":true}')
    authority = capture_development_authority(
        artifact_store=store, source_files=(source,), configuration=config
    )
    descriptor = DevelopmentSkillDescriptor(
        skill_id="development.echo",
        version="1.0.0",
        mcp_tool_name="development_echo_v1_0_0",
        input_schema="harness.run_read_request.v1",
        output_schema="harness.run_read_request.v1",
        implementation_sha256=authority.implementation_sha256,
    )
    child = SQLiteRunStore(tmp_path / "child.db")
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=Dependencies(),
        event_sink=SQLiteEventJournal(child.path),
        clock=lambda: datetime.now(timezone.utc),
        run_id_factory=uuid4,
        run_store=child,
    )
    binding = create_development_execution_binding(
        descriptor=descriptor,
        handler=lambda parameters, context: HandlerResult(output=parameters),
        policy=Policy(),
        registry=registry,
        run_store=child,
        authority=authority,
    )
    kwargs = dict(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.db"),
        execution_catalog=create_golden_execution_catalog(bindings=(binding,)),
        development_authority=authority,
    )
    harness = GoldenRunHarness(**kwargs)
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    started = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="tests/dev",
            idempotency_key="echo-start",
            workspace="ephemeral",
            requested_profile="genesis.rigid_scene@1",
            initial_fact_evidence=(objective,),
            registry_snapshot=authority.registry((descriptor,)),
        )
    )
    command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        command_id=uuid4(),
        principal_id="tests/dev",
        workflow_run_id=started.workflow_run_id,
        idempotency_key="echo-child",
        skill_ref=Policy.skill_ref,
        expected_workflow_revision=0,
        expected_state_sha256=started.state_sha256,
        expected_receipt_head_sha256=started.receipt_head.sha256,
        parameters=dict(
            schema_version="harness.run_read_request.v1",
            principal_id="tests/dev",
            workflow_run_id=str(started.workflow_run_id),
        ),
    )
    operation = harness.submit(command)
    assert operation.status.value == "succeeded"
    assert operation.qualified_execution.schema_version == "harness.development_skill_execution.v1"
    import json

    execution = json.loads(store.resolve(operation.qualified_execution).path.read_bytes())
    assert execution["qualification_status"] == "development_unqualified"
    assert "qualification_artifact" not in execution
    reopened = GoldenRunHarness(**kwargs)
    assert reopened.submit(command) == operation
    assert (
        reopened.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id="tests/dev",
                workflow_run_id=started.workflow_run_id,
            )
        ).revision
        == 1
    )
    ref, payload = reopened.read_workflow_artifact(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id="tests/dev",
            workflow_run_id=started.workflow_run_id,
        ),
        sha256=operation.qualified_execution.sha256,
    )
    assert json.loads(payload) == execution
    from pydantic import ValidationError

    from self_improving.harness.schemas.workflow_operation import QualifiedSkillExecutionV2

    with pytest.raises(ValidationError):
        QualifiedSkillExecutionV2.model_validate_json(payload)
    authority_free = {key: value for key, value in kwargs.items() if key != "development_authority"}
    from self_improving.harness.golden_run import GoldenRunCorruptionError

    with pytest.raises(GoldenRunCorruptionError, match="development"):
        GoldenRunHarness(**authority_free).read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id="tests/dev",
                workflow_run_id=started.workflow_run_id,
            )
        )
