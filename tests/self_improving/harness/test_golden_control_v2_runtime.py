"""V2 controls cross the public durable Harness seam, with real SQLite/CAS."""

from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.schemas.golden_control_v2 import AdvisoryRecordCommandV2
from self_improving.harness.schemas.workflow import RunReadRequest
from tests.self_improving.harness import test_golden_control_submit as base


def native_case(tmp_path, capture_changes=None):
    """Controlled external camera handler; real Registry, journal, CAS and Golden kernel."""
    from datetime import timedelta
    from types import SimpleNamespace

    from self_improving.harness.qualification import QualificationCheckV1, QualificationReportV1
    from self_improving.harness.schemas import (
        QualifiedSkillSnapshot,
        SkillDescriptor,
        SkillQualification,
    )
    from self_improving.harness.schemas.genesis_fresh_capture import (
        GenesisFreshCaptureInputV1,
        GenesisFreshCaptureReceiptV1,
    )
    from self_improving.harness.schemas.golden_control_v2 import ObservationCitationV2

    store = base.LocalArtifactStore(tmp_path / "cas")

    def put(name, value, schema):
        return base._put_json(store, tmp_path, name=name, value=value, schema_version=schema)

    source = base._put_binary(
        store,
        tmp_path,
        name="prompt.txt",
        payload=b"mouse on table",
        media_type="text/plain",
        schema_version="harness.prompt_text.v1",
    )
    native = put(
        "native.json",
        {"fixture": "controlled external native environment"},
        "harness.image2env_genesis_compile_receipt.v1",
    )
    evidence = put(
        "user.json",
        base.WorldFactEvidence(
            source_kind="user_input",
            key="request.objective",
            value="mouse on table",
            observed_at=base._T0,
            valid_until=None,
        ),
        "harness.world_fact_evidence.v1",
    )
    observation = GenesisFreshCaptureReceiptV1(
        schema_version="harness.genesis_fresh_capture_receipt.v1",
        replay_run_id=base._POST_CONTROL_CHILD,
        replay_receipt=native,
        request=native,
        capture_result=native,
        image=source,
        captured_at=base._T0,
        checked_at=base._T0,
        expires_at=base._T0 + timedelta(seconds=60),
        ttl_seconds=60,
        authority="observed_restored_final_pose",
        process_continuation=False,
    )
    if capture_changes:
        observation = GenesisFreshCaptureReceiptV1.model_validate(
            {**observation.model_dump(mode="json"), **capture_changes}
        )
    observation_ref = base._put_binary(
        store,
        tmp_path,
        name="capture.json",
        payload=base.json.dumps(
            observation.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode(),
        media_type="application/json",
        schema_version=observation.schema_version,
    )
    skill = "genesis.observe@3.0.0"
    report = QualificationReportV1(
        schema_version="harness.skill_qualification_report.v1",
        skill_ref=skill,
        status="pass",
        deterministic_case_id="controlled-contract",
        regression_command="pytest controlled",
        implementation_sha256="3" * 64,
        scene_gen_tree_sha256="6" * 64,
        ledger_contract_tree_sha256="7" * 64,
        checks=(QualificationCheckV1(name="controlled", status="pass", evidence={}),),
    )
    report_ref = put("report.json", report, report.schema_version)
    q = SkillQualification(
        skill_ref=skill,
        status="pass",
        deterministic_case_id="controlled-contract",
        regression_command="pytest controlled",
        report_sha256=report_ref.sha256,
    )
    qref = put("q.json", q, "harness.skill_qualification.v1")
    descriptor = SkillDescriptor(
        skill_id="genesis.observe",
        version="3.0.0",
        mcp_tool_name="genesis_observe_v3_0_0",
        input_schema="harness.genesis_fresh_capture_input.v1",
        output_schema=observation.schema_version,
        implementation_name="tests.controlled_camera",
        implementation_version="1",
        implementation_sha256="3" * 64,
        deterministic=True,
        max_attempts=1,
        qualification_artifact=qref,
    )
    descriptor_ref = put("descriptor.json", descriptor, "harness.skill_descriptor.v1")
    registry_ref = put(
        "registry.json",
        base.RegistrySnapshot(
            schema_version="harness.registry_snapshot.v1",
            qualified_skills=(
                QualifiedSkillSnapshot(
                    skill_ref=skill,
                    mcp_tool_name=descriptor.mcp_tool_name,
                    descriptor_ref=descriptor_ref,
                    qualification_ref=qref,
                    qualification_report_ref=report_ref,
                ),
            ),
        ),
        "harness.registry_snapshot.v1",
    )
    child = base.SQLiteRunStore(tmp_path / "children.sqlite3")
    registry = base.SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=base.StaticDependencyResolver({skill: ()}),
        event_sink=base.SQLiteEventJournal(child.path),
        clock=base._Clock(),
        run_store=child,
        run_id_factory=lambda: base._OBSERVE_CHILD,
    )

    def execute(parameters, context):
        if parameters.ttl_seconds == 60:
            return base.HandlerResult(
                output=observation, artifacts=(observation_ref, source, native)
            )
        raise base.SkillBlocked(
            base.Blocker(
                code="CONTROLLED_FAILURE",
                message="controlled repair input",
                stage="diagnose",
                retryable=False,
                details={},
                unknowns=(),
                artifact_refs=(source, native),
            )
        )

    registry.register(descriptor, execute)

    class Policy:
        skill_ref = skill
        input_model = GenesisFreshCaptureInputV1
        evidence_policy_id = "tests.fresh_control"
        evidence_policy_version = "1.0.0"
        evidence_policy_implementation_sha256 = "9" * 64

        def normalize_parameters(self, p):
            return GenesisFreshCaptureInputV1.model_validate(p)

        def authorize(self, state, p):
            pass

        def claims(self, state):
            return ()

        def diagnostics(self, state):
            return ()

        def fresh_observations(self, state, invocation):
            return ()

        def verify_result(self, **kwargs):
            assert kwargs["state"].skill_id == "genesis.observe"

    policy = Policy()

    class Application:
        artifact_root = store.root
        authority_path = child.path
        skill_ref = skill
        skills = (descriptor,)

        def invoke(self, p, *, run_id):
            return registry.invoke_with_run_id(
                "genesis.observe", "3.0.0", p.model_dump(mode="json"), run_id=run_id
            )

        def invocation(self, r):
            return registry.invocation(r)

        def run_state(self, r):
            return child.read_run_state(r)

    app = Application()
    app.policy = policy
    catalog = base.create_golden_execution_catalog(
        bindings=(
            base._create_golden_execution_binding(
                policy=policy, application=app, child_authority=child
            ),
        )
    )
    clock = base._Clock()
    workflow = base.SQLiteGoldenRunStore(tmp_path / "golden.sqlite3")
    children = iter((base._OBSERVE_CHILD, base._FAILURE_CHILD))
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=workflow,
        execution_catalog=catalog,
        clock=clock,
        workflow_id_factory=lambda: base._WORKFLOW_ID,
        child_run_id_factory=lambda: next(children),
    )
    head = harness.start(
        base.RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="tests/fresh",
            idempotency_key="start",
            workspace="ephemeral",
            requested_profile="genesis.rigid_scene@1",
            initial_fact_evidence=(evidence,),
            registry_snapshot=registry_ref,
        )
    )
    read = RunReadRequest(
        schema_version="harness.run_read_request.v1",
        principal_id=head.principal_id,
        workflow_run_id=head.workflow_run_id,
    )
    for run_id, ttl in ((base._OBSERVE_CHILD, 60), (base._FAILURE_CHILD, 1)):
        harness.submit(
            base.RunCommandRequest(
                schema_version="harness.run_command_request.v1",
                command_id=run_id,
                principal_id=head.principal_id,
                workflow_run_id=head.workflow_run_id,
                idempotency_key=str(ttl),
                expected_workflow_revision=head.revision,
                expected_state_sha256=head.state_sha256,
                expected_receipt_head_sha256=head.receipt_head.sha256,
                skill_ref=skill,
                parameters=dict(
                    schema_version="harness.genesis_fresh_capture_input.v1",
                    replay_run_id=str(base._POST_CONTROL_CHILD),
                    ttl_seconds=ttl,
                ),
            )
        )
        head = harness.read(read)
    citation = ObservationCitationV2(
        schema_version="harness.observation_citation.v2",
        receipt=observation_ref,
        observation=observation,
        captured_at=observation.captured_at,
        expires_at=observation.expires_at,
        ttl_seconds=60,
        authority="observed_restored_final_pose",
        process_continuation=False,
    )
    accesses = tuple(
        sorted(
            (
                harness.read_workflow_artifact_audited(
                    read,
                    sha256=r.sha256,
                    resource_uri=f"harness://workflows/{head.workflow_run_id}/artifacts/{r.sha256}",
                ).access_receipt
                for r in (source, head.receipt_head, observation_ref)
            ),
            key=lambda r: r.sha256,
        )
    )
    command = AdvisoryRecordCommandV2(
        schema_version="harness.advisory_record_command.v2",
        command_id=base._ADVISORY_ID,
        workflow_run_id=head.workflow_run_id,
        principal_id=head.principal_id,
        idempotency_key="advisory",
        expected_workflow_revision=head.revision,
        expected_state_sha256=head.state_sha256,
        expected_receipt_head_sha256=head.receipt_head.sha256,
        advisory_kind="diagnosis",
        disposition="recommend",
        summary="revise placement",
        recommended_action="revise_prompt",
        source_prompt=source,
        source_prompt_revision=None,
        failure_receipts=(head.receipt_head,),
        observation_citations=(citation,),
        diagnostic_refs=(),
        resource_access_receipts=accesses,
        external_agent_executed=False,
    )
    return SimpleNamespace(
        harness=harness,
        store=store,
        workflow=workflow,
        clock=clock,
        child=child,
        policy=policy,
        head=head,
        read=read,
        command=command,
        native=native,
    )


def test_owned_controlled_capture_advisory_and_native_revision(tmp_path):
    from self_improving.harness.schemas.golden_control_v2 import PromptRevisionCommandV2

    case = native_case(tmp_path)
    advisory = case.harness.submit_control(case.command)
    head = case.harness.read(case.read)
    revision = PromptRevisionCommandV2(
        schema_version="harness.prompt_revision_command.v2",
        command_id=base._PROMPT_REVISION_ID,
        workflow_run_id=head.workflow_run_id,
        principal_id=head.principal_id,
        idempotency_key="revision",
        expected_workflow_revision=head.revision,
        expected_state_sha256=head.state_sha256,
        expected_receipt_head_sha256=head.receipt_head.sha256,
        source_prompt=case.command.source_prompt,
        advisory_receipt=advisory.receipt,
        failure_receipts=case.command.failure_receipts,
        predecessor_environment=case.native,
        patch=dict(
            kind="full_replace",
            base_text_sha256=case.command.source_prompt.sha256,
            replacement_text="mouse centered on table",
            reason="placement",
        ),
        fallback=dict(kind="none"),
        next_compile_skill_ref="image2env.compile@2.0.0",
        external_agent_executed=False,
    )
    result = case.harness.submit_control(revision)
    assert result.base_state == result.resulting_state
    case.clock.value += base.timedelta(days=1)
    reopened = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=case.workflow,
        execution_catalog=base.create_golden_execution_catalog(
            bindings=(
                base._create_golden_execution_binding(
                    policy=case.policy, application=None, child_authority=case.child
                ),
            )
        ),
        clock=case.clock,
    )
    assert reopened.submit_control(revision) == result
    assert reopened.read(case.read).receipt_head == result.receipt
    with base.pytest.raises(base.GoldenRunRegistryError, match="consum"):
        reopened.submit(
            base.RunCommandRequest(
                schema_version="harness.run_command_request.v1",
                command_id=base._POST_CONTROL_COMMAND,
                principal_id=result.principal_id,
                workflow_run_id=result.workflow_run_id,
                idempotency_key="cannot-ignore-revision",
                expected_workflow_revision=result.committed_revision,
                expected_state_sha256=result.resulting_state_sha256,
                expected_receipt_head_sha256=result.receipt.sha256,
                skill_ref="genesis.observe@3.0.0",
                parameters={},
            )
        )


@base.pytest.mark.parametrize(
    "mode",
    [
        "unread",
        "foreign_principal",
        "foreign_workflow",
        "stale",
        "payload_drift",
        "external_receipt",
        "replay_uuid",
        "no_child_authority",
        "foreign_child_authority",
    ],
)
def test_v2_capture_controls_reject_unowned_or_unfresh_sources(tmp_path, mode):
    from datetime import timedelta

    changes = {"replay_run_id": str(base._FAILURE_CHILD)} if mode == "replay_uuid" else None
    case = native_case(tmp_path, capture_changes=changes)
    values = case.command.model_dump(mode="json")
    if mode == "unread":
        values["resource_access_receipts"] = []
    if mode == "foreign_principal":
        values["principal_id"] = "someone/else"
    if mode == "foreign_workflow":
        values["workflow_run_id"] = str(base._POST_CONTROL_CHILD)
    if mode == "stale":
        case.clock.value += timedelta(seconds=61)
    if mode in ("no_child_authority", "foreign_child_authority"):
        authority = (
            None
            if mode == "no_child_authority"
            else base.SQLiteRunStore(tmp_path / "foreign.sqlite3")
        )
        case.harness = GoldenRunHarness(
            artifact_store=case.store,
            workflow_store=case.workflow,
            execution_catalog=base.create_golden_execution_catalog(
                bindings=(
                    base._create_golden_execution_binding(
                        policy=case.policy, application=None, child_authority=authority
                    ),
                )
            ),
            clock=case.clock,
        )
    if mode in ("payload_drift", "external_receipt"):
        citation = values["observation_citations"][0]
        if mode == "payload_drift":
            case.store.resolve(case.command.observation_citations[0].receipt).path.write_bytes(
                b"changed"
            )
        else:
            citation["observation"]["checked_at"] = "2026-09-11T02:00:01Z"
            payload = base.json.dumps(
                citation["observation"], sort_keys=True, separators=(",", ":")
            ).encode()
            external = base._put_binary(
                case.store,
                tmp_path,
                name="external.json",
                payload=payload,
                media_type="application/json",
                schema_version="harness.genesis_fresh_capture_receipt.v1",
            )
            citation["receipt"] = external.model_dump(mode="json")
    command = AdvisoryRecordCommandV2.model_validate(values)
    with base.pytest.raises(
        (
            base.GoldenRunNotFoundError,
            base.GoldenRunRegistryError,
            base.GoldenRunCorruptionError,
            ValueError,
        )
    ):
        case.harness.submit_control(command)


def test_v2_capture_expiring_during_verification_cannot_commit(tmp_path):
    from datetime import timedelta

    case = native_case(tmp_path)
    times = iter((base._T0, base._T0 + timedelta(seconds=61)))
    harness = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=case.workflow,
        execution_catalog=base.create_golden_execution_catalog(
            bindings=(
                base._create_golden_execution_binding(
                    policy=case.policy, application=None, child_authority=case.child
                ),
            )
        ),
        clock=lambda: next(times),
    )
    with base.pytest.raises(base.GoldenRunRegistryError, match="expired"):
        harness.submit_control(case.command)
    assert case.harness.read(case.read) == case.head


def test_v2_diagnostic_advisory_commits_and_reopens_without_live_application(tmp_path):
    case = base._case(tmp_path)
    values = base._advisory_command(case, audit_accesses=False).model_dump(mode="json")
    values.update(schema_version="harness.advisory_record_command.v2", observation_citations=[])
    read = RunReadRequest(
        schema_version="harness.run_read_request.v1",
        principal_id=case.head.principal_id,
        workflow_run_id=case.head.workflow_run_id,
    )
    sources = (case.source_prompt, case.failure_receipt, case.diagnostic)
    values["resource_access_receipts"] = sorted(
        [
            case.harness.read_workflow_artifact_audited(
                read,
                sha256=r.sha256,
                resource_uri=f"harness://workflows/{case.head.workflow_run_id}/artifacts/{r.sha256}",
            ).access_receipt.model_dump(mode="json")
            for r in sources
        ],
        key=lambda r: r["sha256"],
    )
    command = AdvisoryRecordCommandV2.model_validate(values)
    result = case.harness.submit_control(command)
    assert result.schema_version == "harness.control_commit_snapshot.v2"
    assert result.base_state == result.resulting_state == case.head.state_ref
    reopened = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=case.workflow_store,
        execution_catalog=base.create_golden_execution_catalog(
            bindings=(
                base._create_golden_execution_binding(
                    policy=case.policy, application=None, child_authority=case.child_store
                ),
            )
        ),
        clock=case.clock,
    )
    assert reopened.read(read).receipt_head == result.receipt
    assert reopened.submit_control(command) == result
