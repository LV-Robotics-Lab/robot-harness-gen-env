"""Native input providers reject configuration outside the exact public seam."""

import pytest

from tests.self_improving.harness import test_golden_control_submit as base


@pytest.fixture
def native(tmp_path):
    import json
    from types import SimpleNamespace
    from uuid import uuid4

    from self_improving.harness.genesis_runtime_manifest import capture_runtime_manifest
    from self_improving.harness.golden_native_inputs import NativeImageWorkflowInputs
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
    from self_improving.harness.schemas.genesis_runtime_manifest import RuntimeProcessIdentityV1
    from self_improving.harness.schemas.genesis_scene_application_v2 import (
        GenesisSceneReplayInputV2,
    )
    from self_improving.harness.schemas.genesis_scene_execution_v2 import (
        GenesisSceneExecutionReceiptV2,
    )
    from self_improving.harness.schemas.image2env_genesis_compile import (
        Image2EnvGenesisCompileReceiptV1,
        Image2EnvGenesisCompileRequestV1,
    )
    from tests.self_improving.harness.test_x2env_compile_closure import _case

    case = _case(tmp_path / "source")
    store = case.store

    def put(name, value, schema=None, media="application/json"):
        if hasattr(value, "model_dump"):
            schema = value.schema_version if schema is None else schema
            value = value.model_dump(mode="json")
        payload = (
            (
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            if media == "application/json"
            else value
        )
        return base._put_binary(
            store, tmp_path, name=name, payload=payload, media_type=media, schema_version=schema
        )

    roots = {}
    for role in ("backend", "genesis", "interpreter", "stdlib", "native", "distributions"):
        roots[role] = tmp_path / role
        roots[role].mkdir()
        (roots[role] / "member").write_bytes(b"declared controlled runtime")
    manifest = capture_runtime_manifest(
        store,
        roots=roots,
        process_identity=RuntimeProcessIdentityV1(
            implementation="cpython",
            version="3.12.3",
            abi="cpython-312-x86_64-linux-gnu",
            platform="linux-x86_64",
            environment=(("OMP_NUM_THREADS", "1"),),
        ),
    )
    template = Image2EnvGenesisCompileRequestV1(
        schema_version="harness.image2env_genesis_compile_request.v1",
        compile_input=case.compile_input,
        qualification_run_id=uuid4(),
        source_scene=put("source.json", {"controlled": "external source"}),
        table_center_m=(0.0, 0.0, 0.2),
        table_size_m=(1.0, 0.8, 0.4),
        table_friction=0.5,
    )
    template_ref = put("template.json", template)
    proposal = json.loads(
        store.resolve(template.compile_input.interpretation_proposal).path.read_bytes()
    )
    from self_improving.harness.schemas.common import ArtifactRef

    objective = store.resolve(
        ArtifactRef.model_validate(proposal["effective_prompt"])
    ).path.read_text()
    scene = put("scene.json", {"controlled": "native scene"}, "harness.genesis_scene_spec.v1")
    asset = put("asset.json", {"controlled": "asset"}, "harness.asset_qualification.v1")
    implementation = put("handler.py", b"controlled external handler", media="text/x-python")
    child = base.SQLiteRunStore(tmp_path / "children.sqlite")
    skills = (
        (
            "image2env.compile@2.0.0",
            Image2EnvGenesisCompileRequestV1,
            Image2EnvGenesisCompileReceiptV1,
        ),
        ("image2env.replay@2.0.0", GenesisSceneReplayInputV2, GenesisSceneExecutionReceiptV2),
        ("genesis.observe@3.0.0", GenesisFreshCaptureInputV1, GenesisFreshCaptureReceiptV1),
    )
    registry = base.SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=base.StaticDependencyResolver({s: () for s, _, _ in skills}),
        event_sink=base.SQLiteEventJournal(child.path),
        clock=base._Clock(),
        run_store=child,
        run_id_factory=uuid4,
    )
    saved = {}
    variations = {}

    def execute(p, context):
        if type(p) is Image2EnvGenesisCompileRequestV1:
            value = Image2EnvGenesisCompileReceiptV1(
                schema_version="harness.image2env_genesis_compile_receipt.v1",
                request=put("compile-request.json", p),
                scene=scene,
                qualification=asset,
                source_object_id="mouse",
                proposal_object_id="mouse",
                scene_object_id="object",
                implementation_sha256="3" * 64,
                implementation=(implementation,),
                scope="static_scene_candidate",
                simulator_executed=False,
            )
        elif type(p) is GenesisSceneReplayInputV2:
            value = GenesisSceneExecutionReceiptV2(
                schema_version="harness.genesis_scene_execution_receipt.v2",
                authority="observed_execution_only",
                request=p.execution_request,
                launch_result=put("launch.json", {}, "harness.genesis_scene_launch_result.v2"),
                replay=put("replay.json", {}, "harness.genesis_scene_replay.v1"),
                report=put("report.json", {}, "harness.genesis_scene_report.v1"),
                baseline_preview=put("base.mp4", b"external", media="video/mp4"),
                half_dt_preview=put("half.mp4", b"external", media="video/mp4"),
                retained_index=put("index.json", {}),
            )
        else:
            value = GenesisFreshCaptureReceiptV1(
                schema_version="harness.genesis_fresh_capture_receipt.v1",
                replay_run_id=p.replay_run_id,
                replay_receipt=saved["replay"],
                request=scene,
                capture_result=scene,
                image=case.compile_input.source_media.canonical_image,
                captured_at=base._T0,
                checked_at=base._T0,
                expires_at=base._T0 + base.timedelta(seconds=p.ttl_seconds),
                ttl_seconds=p.ttl_seconds,
                authority="observed_restored_final_pose",
                process_continuation=False,
            )
        if "output" in variations:
            value = type(value).model_validate(
                {**value.model_dump(mode="json"), **variations["output"]}
            )
        ref = put("output.json", value)
        if type(p) is GenesisSceneReplayInputV2:
            saved["replay"] = ref
        artifacts = (ref,)
        if variations.get("artifacts") == "missing":
            artifacts = ()
        elif variations.get("artifacts") == "different":
            other = value.model_copy(
                update={"source_object_id": "other", "proposal_object_id": "other"}
            )
            artifacts = (put("other-output.json", other),)
        return base.HandlerResult(output=value, artifacts=artifacts)

    bindings = []
    snapshots = []

    def binding(skill, model, descriptor):
        class Policy:
            skill_ref = skill
            input_model = model
            evidence_policy_id = "tests.native_inputs"
            evidence_policy_version = "1.0.0"
            evidence_policy_implementation_sha256 = "9" * 64

            def normalize_parameters(self, p):
                return model.model_validate(p)

            def authorize(self, state, p):
                pass

            def claims(self, state):
                return ()

            def diagnostics(self, state):
                return ()

            def fresh_observations(self, state, invocation):
                return ()

            def verify_result(self, **kwargs):
                pass

        policy = Policy()

        class Application:
            artifact_root = store.root
            authority_path = child.path
            skill_ref = skill
            skills = (descriptor,)

            def invoke(self, p, *, run_id):
                sid, ver = skill.split("@")
                return registry.invoke_with_run_id(
                    sid, ver, p.model_dump(mode="json"), run_id=run_id
                )

            def invocation(self, r):
                return registry.invocation(r)

            def run_state(self, r):
                return child.read_run_state(r)

        app = Application()
        app.policy = policy
        return base._create_golden_execution_binding(
            policy=policy, application=app, child_authority=child
        )

    for skill, model, output in skills:
        report = QualificationReportV1(
            schema_version="harness.skill_qualification_report.v1",
            skill_ref=skill,
            status="pass",
            deterministic_case_id="controlled",
            regression_command="pytest",
            implementation_sha256="3" * 64,
            scene_gen_tree_sha256="6" * 64,
            ledger_contract_tree_sha256="7" * 64,
            checks=(QualificationCheckV1(name="controlled", status="pass", evidence={}),),
        )
        report_ref = put(skill + "-report.json", report)
        qualification = SkillQualification(
            skill_ref=skill,
            status="pass",
            deterministic_case_id="controlled",
            regression_command="pytest",
            report_sha256=report_ref.sha256,
        )
        qref = put(skill + "-qualification.json", qualification, "harness.skill_qualification.v1")
        sid, version = skill.split("@")
        descriptor = SkillDescriptor(
            skill_id=sid,
            version=version,
            mcp_tool_name=sid.replace(".", "_") + "_v" + version.replace(".", "_"),
            input_schema=model.model_fields["schema_version"].annotation.__args__[0],
            output_schema=output.model_fields["schema_version"].annotation.__args__[0],
            implementation_name="tests.controlled_native",
            implementation_version="1",
            implementation_sha256="3" * 64,
            deterministic=True,
            max_attempts=1,
            qualification_artifact=qref,
        )
        # Schema identifiers are the exact Literal contracts, not inferred from a live handler.
        descriptor = descriptor.model_copy(
            update={
                "input_schema": model.model_fields["schema_version"].annotation.__args__[0],
                "output_schema": output.model_fields["schema_version"].annotation.__args__[0],
            }
        )
        registry.register(descriptor, execute)
        dref = put(skill + "-descriptor.json", descriptor, "harness.skill_descriptor.v1")
        snapshots.append(
            QualifiedSkillSnapshot(
                skill_ref=skill,
                mcp_tool_name=descriptor.mcp_tool_name,
                descriptor_ref=dref,
                qualification_ref=qref,
                qualification_report_ref=report_ref,
            )
        )
        bindings.append(binding(skill, model, descriptor))
    catalog = base.create_golden_execution_catalog(bindings=tuple(bindings))
    registry_ref = put(
        "registry.json",
        base.RegistrySnapshot(
            schema_version="harness.registry_snapshot.v1",
            qualified_skills=tuple(sorted(snapshots, key=lambda x: x.skill_ref)),
        ),
    )
    facts = []
    for key, value in (
        ("request.image_source", template.compile_input.source_media.model_dump(mode="json")),
        ("request.objective", objective),
    ):
        facts.append(
            put(
                key + ".json",
                base.WorldFactEvidence(
                    source_kind="user_input",
                    key=key,
                    value=value,
                    observed_at=base._T0,
                    valid_until=None,
                ),
            )
        )
    harness = base.GoldenRunHarness(
        artifact_store=store,
        workflow_store=base.SQLiteGoldenRunStore(tmp_path / "golden.sqlite"),
        execution_catalog=catalog,
        clock=base._Clock(),
    )
    start = base.RunStartRequest(
        schema_version="harness.run_start_request.v1",
        principal_id="tests/native",
        idempotency_key="start",
        workspace="ephemeral",
        requested_profile="genesis.rigid_scene@1",
        initial_fact_evidence=tuple(sorted(facts, key=lambda r: r.sha256)),
        registry_snapshot=registry_ref,
    )
    head = harness.start(start)
    provider = NativeImageWorkflowInputs(
        harness=harness,
        store=store,
        execution_catalog=catalog,
        compile_template_ref=template_ref,
        approved_runtime_manifest=manifest,
    )

    def submit(head, skill, parameters):
        harness.submit(
            base.RunCommandRequest(
                schema_version="harness.run_command_request.v1",
                command_id=uuid4(),
                principal_id=head.principal_id,
                workflow_run_id=head.workflow_run_id,
                idempotency_key=str(uuid4()),
                expected_workflow_revision=head.revision,
                expected_state_sha256=head.state_sha256,
                expected_receipt_head_sha256=head.receipt_head.sha256,
                skill_ref=skill,
                parameters=parameters.model_dump(mode="json"),
            )
        )
        return harness.read(
            base.RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=head.principal_id,
                workflow_run_id=head.workflow_run_id,
            )
        )

    return SimpleNamespace(**locals())


def test_four_native_inputs_derive_from_real_durable_lineage(native):
    c = native
    compile_input = c.provider.build_compile(c.head)
    compiled = c.submit(c.head, "image2env.compile@2.0.0", compile_input)
    replay_input = c.provider.build_replay(compiled)
    replayed = c.submit(compiled, "image2env.replay@2.0.0", replay_input)
    observation_input = c.provider.build_observe(replayed)
    assert observation_input.ttl_seconds == 300
    observed = c.submit(replayed, "genesis.observe@3.0.0", observation_input)
    validation = c.provider.build_validate(observed)
    assert validation.replay_run_id == observation_input.replay_run_id


@pytest.mark.parametrize(
    "mode",
    [
        "wrong_type",
        "stale",
        "foreign_principal",
        "foreign_workflow",
        "wrong_head",
        "initial_only",
        "wrong_template",
        "wrong_schema",
        "noncanonical",
        "missing_nested",
        "runtime_drift",
        "wrong_cas",
        "file_locator",
    ],
)
def test_native_inputs_reject_unowned_heads_and_configuration(native, mode, tmp_path):
    c = native
    from uuid import uuid4

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.golden_native_inputs import NativeImageWorkflowInputs

    kwargs = dict(
        harness=c.harness,
        store=c.store,
        execution_catalog=c.catalog,
        compile_template_ref=c.template_ref,
        approved_runtime_manifest=c.manifest,
    )
    head = c.head
    if mode == "wrong_type":
        head = {}
    elif mode == "stale":
        c.submit(head, "image2env.compile@2.0.0", c.template)
    elif mode == "foreign_principal":
        head = head.model_copy(update={"principal_id": "foreign/user"})
    elif mode == "foreign_workflow":
        head = head.model_copy(update={"workflow_run_id": uuid4()})
    elif mode == "wrong_head":
        head = head.model_copy(update={"revision": 12})
    elif mode == "initial_only":
        head = c.submit(head, "image2env.compile@2.0.0", c.template)
    elif mode == "wrong_template":
        changed = c.template.model_copy(
            update={
                "compile_input": c.template.compile_input.model_copy(
                    update={
                        "source_media": c.template.compile_input.source_media.model_copy(
                            update={"width": 999}
                        )
                    }
                )
            }
        )
        kwargs["compile_template_ref"] = c.put("different-template.json", changed)
    elif mode == "file_locator":
        kwargs["compile_template_ref"] = c.template_ref.model_copy(
            update={"uri": c.store.resolve(c.template_ref).path.as_uri()}
        )
    elif mode == "wrong_schema":
        kwargs["compile_template_ref"] = c.template_ref.model_copy(update={"schema_version": None})
    elif mode == "noncanonical":
        payload = c.store.resolve(c.template_ref).path.read_bytes()
        kwargs["compile_template_ref"] = base._put_binary(
            c.store,
            tmp_path,
            name="noncanonical.json",
            payload=b" " + payload,
            media_type="application/json",
            schema_version=c.template_ref.schema_version,
        )
    elif mode == "missing_nested":
        c.store.resolve(c.template.compile_input.source_media.canonical_image).path.unlink()
    elif mode == "runtime_drift":
        c.store.resolve(c.manifest).path.write_bytes(b"changed")
    else:
        kwargs["store"] = LocalArtifactStore(tmp_path / "another")
    with pytest.raises((ValueError, TypeError, LookupError)):
        NativeImageWorkflowInputs(**kwargs).build_compile(head)


@pytest.mark.parametrize("method", ["build_replay", "build_observe", "build_validate"])
def test_builders_reject_wrong_predecessor(native, method):
    with pytest.raises(ValueError):
        getattr(native.provider, method)(native.head)


def test_replay_rejects_compile_input_from_another_operator_template(native):
    c = native
    foreign = c.template.model_copy(update={"table_friction": 0.8})
    head = c.submit(c.head, "image2env.compile@2.0.0", foreign)
    with pytest.raises(ValueError, match="template"):
        c.provider.build_replay(head)


def test_observe_rejects_replay_request_payload_drift(native):
    c = native
    compiled = c.submit(c.head, "image2env.compile@2.0.0", c.template)
    inputs = c.provider.build_replay(compiled)
    c.variations["output"] = {
        "request": c.template_ref.model_copy(
            update={"schema_version": "harness.genesis_scene_execution_request.v2"}
        ).model_dump(mode="json")
    }
    replayed = c.submit(compiled, "image2env.replay@2.0.0", inputs)
    with pytest.raises(ValueError, match="request"):
        c.provider.build_observe(replayed)


def test_validate_rejects_observation_replay_drift(native):
    from uuid import uuid4

    c = native
    compiled = c.submit(c.head, "image2env.compile@2.0.0", c.template)
    replayed = c.submit(compiled, "image2env.replay@2.0.0", c.provider.build_replay(compiled))
    c.variations["output"] = {"replay_run_id": str(uuid4())}
    observed = c.submit(replayed, "genesis.observe@3.0.0", c.provider.build_observe(replayed))
    with pytest.raises(ValueError, match="replay"):
        c.provider.build_validate(observed)


@pytest.mark.parametrize(
    "mode",
    ["principal", "revision", "missing_receipt", "different_receipt", "no_child", "foreign_child"],
)
def test_native_predecessor_ownership_and_exact_receipt(native, mode, tmp_path):
    from self_improving.harness.golden_native_inputs import NativeImageWorkflowInputs

    c = native
    if mode == "missing_receipt":
        c.variations["artifacts"] = "missing"
    if mode == "different_receipt":
        c.variations["artifacts"] = "different"
    compiled = c.submit(c.head, "image2env.compile@2.0.0", c.template)
    provider = c.provider
    if mode == "principal":
        compiled = compiled.model_copy(update={"principal_id": "foreign/user"})
    elif mode == "revision":
        compiled = compiled.model_copy(update={"revision": 12})
    elif mode in ("no_child", "foreign_child"):
        authority = None if mode == "no_child" else base.SQLiteRunStore(tmp_path / "foreign.sqlite")
        catalog = base.create_golden_execution_catalog(
            bindings=tuple(
                base._create_golden_execution_binding(
                    policy=b.policy, application=None, child_authority=authority
                )
                for b in c.bindings
            )
        )
        harness = base.GoldenRunHarness(
            artifact_store=c.store,
            workflow_store=base.SQLiteGoldenRunStore(tmp_path / "golden.sqlite"),
            execution_catalog=catalog,
            clock=base._Clock(),
        )
        provider = NativeImageWorkflowInputs(
            harness=harness,
            store=c.store,
            execution_catalog=catalog,
            compile_template_ref=c.template_ref,
            approved_runtime_manifest=c.manifest,
        )
    with pytest.raises(ValueError):
        provider.build_replay(compiled)


def test_authority_identity_properties_are_readonly(native):
    assert native.harness.artifact_root == native.store.root
    assert native.harness.execution_catalog is native.catalog
    with pytest.raises(AttributeError):
        native.harness.artifact_root = native.store.root
    with pytest.raises(AttributeError):
        native.harness.execution_catalog = native.catalog


@pytest.mark.parametrize(
    "mode", ["foreign_compile", "foreign_replay", "wrong_parent", "missing_parent_receipt"]
)
def test_native_inputs_bind_exact_adjacent_parent_outputs(native, mode):
    from uuid import uuid4

    from self_improving.harness.schemas.genesis_scene_application_v2 import (
        GenesisSceneReplayInputV2,
    )
    from self_improving.harness.schemas.genesis_scene_execution_v2 import (
        GenesisSceneExecutionRequestV2,
    )

    c = native
    if mode == "missing_parent_receipt":
        c.variations["artifacts"] = "missing"
    compiled = c.submit(c.head, "image2env.compile@2.0.0", c.template)
    if mode == "missing_parent_receipt":
        c.variations.clear()
        request = GenesisSceneExecutionRequestV2(
            schema_version="harness.genesis_scene_execution_request.v2",
            scene=c.scene,
            asset_qualification=c.asset,
            runtime_manifest=c.manifest,
            compile_receipt=c.template_ref.model_copy(
                update={"schema_version": "harness.image2env_genesis_compile_receipt.v1"}
            ),
        )
        inputs = GenesisSceneReplayInputV2(
            schema_version="harness.genesis_scene_replay_input.v2",
            compile_run_id=compiled.child_runs[-1],
            execution_request=c.put("manual-request.json", request),
        )
    else:
        inputs = c.provider.build_replay(compiled)
    if mode == "foreign_compile":
        inputs = inputs.model_copy(update={"compile_run_id": uuid4()})
    replayed = c.submit(compiled, "image2env.replay@2.0.0", inputs)
    if mode == "wrong_parent":
        replayed = c.submit(replayed, "image2env.replay@2.0.0", inputs)
    if mode == "foreign_replay":
        observe = c.provider.build_observe(replayed).model_copy(update={"replay_run_id": uuid4()})
        observed = c.submit(replayed, "genesis.observe@3.0.0", observe)
        with pytest.raises(ValueError, match="parent"):
            c.provider.build_validate(observed)
    else:
        with pytest.raises(ValueError, match="parent"):
            c.provider.build_observe(replayed)


def test_provider_requires_exact_golden_authority():
    from self_improving.harness.golden_native_inputs import NativeImageWorkflowInputs

    with pytest.raises(TypeError, match="exact"):
        NativeImageWorkflowInputs(
            harness=object(),
            store=object(),
            execution_catalog=object(),
            compile_template_ref=None,
            approved_runtime_manifest=None,
        )
