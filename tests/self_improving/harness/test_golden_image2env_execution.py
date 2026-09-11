"""Native bindings use exact production applications and existing journals."""

import pytest

from self_improving.harness.golden_image2env_execution import create_native_image_execution_bindings


def test_arbitrary_application_claims_cannot_create_a_sealed_binding():
    with pytest.raises(TypeError, match="exact"):
        create_native_image_execution_bindings(
            compile_application=object(),
            replay_application=object(),
            observation_application=object(),
            validation_application=object(),
        )


@pytest.fixture(scope="module")
def restored_apps(tmp_path_factory):
    """One session restore; copied SQLite authority, shared immutable input CAS."""
    import json
    import os
    import shutil
    import sqlite3
    from contextlib import closing
    from pathlib import Path

    tmp_path = tmp_path_factory.mktemp("native-bindings")
    configured = os.environ.get("NATIVE_IMAGE_BINDINGS_CONFIG")
    if not configured:
        pytest.skip("requires the fixed M1 qualified journals and CAS")
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.genesis_fresh_capture import GenesisFreshCaptureProducer
    from self_improving.harness.genesis_fresh_capture_application import (
        GenesisFreshCaptureApplication,
    )
    from self_improving.harness.genesis_scene_application_v2 import GenesisSceneApplication
    from self_improving.harness.genesis_scene_assessment import GenesisSceneAssessment
    from self_improving.harness.genesis_scene_execution_v2 import GenesisSceneExecutionProducerV2
    from self_improving.harness.genesis_scene_validation_application import (
        GenesisSceneValidationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.image2env_genesis_compile_application import (
        Image2EnvGenesisCompileApplication,
    )
    from self_improving.harness.schemas.common import ArtifactRef

    config = json.loads(Path(configured).read_bytes())
    source = Path(config["cas_root"])
    before = {
        str(path.relative_to(source)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in source.rglob("*")
        if path.is_file()
    }
    # Separate namespace: append-only CAS writes cannot enter the original object directory.
    shutil.copytree(source, tmp_path / "cas", copy_function=os.link)
    store = LocalArtifactStore(tmp_path / "cas")

    def database(source_path, target_path):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            closing(sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(target_path)) as dst,
        ):
            src.backup(dst)

    database(config["asset_state_path"], tmp_path / "assets.sqlite")
    for role, name in (
        ("compile", "harness.sqlite3"),
        ("scene", "runs.sqlite"),
        ("observation", "runs.sqlite"),
        ("validation", "runs.sqlite"),
    ):
        database(Path(config[f"{role}_state_root"]) / name, tmp_path / role / name)
    ref = ArtifactRef.model_validate
    compiler = Image2EnvGenesisCompiler(
        store,
        GenesisAssetQualificationApplication(
            tmp_path / "assets.sqlite", store, ref(config["asset_manifest"])
        ),
    )
    compile_app = Image2EnvGenesisCompileApplication(
        tmp_path / "compile", compiler, qualification=ref(config["compile_qualification"])
    )
    replay = GenesisSceneApplication(
        tmp_path / "scene",
        GenesisSceneExecutionProducerV2(store, compiler, ref(config["scene_manifest"])),
        roots={},
        compile_application=compile_app,
    )
    replay.bind(ref(config["scene_qualification"]))
    observe = GenesisFreshCaptureApplication(
        tmp_path / "observation",
        GenesisFreshCaptureProducer(store, replay, ref(config["capture_manifest"])),
        roots={},
    )
    observe.bind(ref(config["observation_qualification"]))
    validate = GenesisSceneValidationApplication(
        tmp_path / "validation", GenesisSceneAssessment(replay)
    )
    validate.bind(ref(config["validation_qualification"]))
    bindings = create_native_image_execution_bindings(
        compile_application=compile_app,
        replay_application=replay,
        observation_application=observe,
        validation_application=validate,
    )
    assert tuple(binding.skill_ref for binding in bindings) == (
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
        "genesis.observe@3.0.0",
        "image2env.validate@2.0.0",
    )
    assert all(binding.application.artifact_root == store.root for binding in bindings)
    assert all(
        binding.application.authority_path == binding.child_authority.path for binding in bindings
    )
    after = {
        str(path.relative_to(source)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in source.rglob("*")
        if path.is_file()
    }
    assert before == after
    return bindings, store, tmp_path, config


def test_formal_restore_four_existing_registered_apps_without_execution(restored_apps):
    bindings, _, _, _ = restored_apps
    assert len(bindings) == 4


@pytest.mark.parametrize("index", range(4))
def test_formal_native_policy_reconstructs_the_actual_registered_terminal(restored_apps, index):
    from uuid import UUID

    bindings, _, _, _ = restored_apps
    run = UUID(
        (
            "a42a3a83-98ea-4c01-a35f-3003a9109c49",
            "bfc30903-0a69-478b-94ed-3ad2c9bbea03",
            "90af3aa7-6d77-4c5e-87d8-185b3bea36ea",
            "510c3c7d-c020-4623-b2bc-67e0dd045212",
        )[index]
    )
    binding = bindings[index]
    invocation = binding.application.invocation(run)
    state = binding.application.run_state(run)
    assert binding.policy.claims(state)
    observations = binding.policy.fresh_observations(state, invocation)
    assert bool(observations) == (index == 2)
    with pytest.raises(TypeError, match="exact native input"):
        binding.application.invoke({}, run_id=run)
    assert (
        binding.application.invoke(
            binding.policy.normalize_parameters(invocation.effective_parameters), run_id=run
        )
        == state
    )


@pytest.mark.skip(reason="M2 native typed qualification Registry bridge is a separate pending seam")
def test_formal_four_native_skills_resume_existing_children_into_one_workflow(restored_apps):
    import json
    from datetime import timedelta
    from uuid import UUID, uuid4

    from self_improving.harness.golden_execution import create_golden_execution_catalog
    from self_improving.harness.golden_run import GoldenRunHarness
    from self_improving.harness.golden_store import SQLiteGoldenRunStore
    from self_improving.harness.schemas.common import SkillQualification
    from self_improving.harness.schemas.registry_snapshot import (
        QualifiedSkillSnapshot,
        RegistrySnapshot,
    )
    from self_improving.harness.schemas.workflow import (
        RunCommandRequest,
        RunReadRequest,
        RunStartRequest,
    )
    from self_improving.harness.trusted_state import WorldFactEvidence

    bindings, store, root, _ = restored_apps
    runs = tuple(
        map(
            UUID,
            (
                "a42a3a83-98ea-4c01-a35f-3003a9109c49",
                "bfc30903-0a69-478b-94ed-3ad2c9bbea03",
                "90af3aa7-6d77-4c5e-87d8-185b3bea36ea",
                "510c3c7d-c020-4623-b2bc-67e0dd045212",
            ),
        )
    )

    def put(value, schema):
        path = root / str(uuid4())
        path.write_bytes(
            (
                json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            ).encode()
        )
        return store.put_file(
            path, name=path.name, media_type="application/json", schema_version=schema
        )

    entries = []
    for binding in bindings:
        descriptor = binding.application.skills[0]
        qualification = SkillQualification.model_validate_json(
            store.resolve(descriptor.qualification_artifact).path.read_bytes()
        )
        report_path = store.resolve_digest(qualification.report_sha256)
        report_schema = json.loads(report_path.read_bytes())["schema_version"]
        report = store.put_file(
            report_path,
            name="qualification-report.json",
            media_type="application/json",
            schema_version=report_schema,
        )
        entries.append(
            QualifiedSkillSnapshot(
                skill_ref=binding.skill_ref,
                mcp_tool_name=descriptor.mcp_tool_name,
                descriptor_ref=put(
                    descriptor.model_dump(mode="json"), "harness.skill_descriptor.v2"
                ),
                qualification_ref=descriptor.qualification_artifact,
                qualification_report_ref=report,
            )
        )
    registry = RegistrySnapshot(
        schema_version="harness.registry_snapshot.v1",
        qualified_skills=tuple(sorted(entries, key=lambda item: item.skill_ref)),
    )
    states = tuple(
        binding.child_authority.read_run_state(run_id)
        for binding, run_id in zip(bindings, runs, strict=True)
    )
    invocations = tuple(
        binding.child_authority.read_invocation(run_id)
        for binding, run_id in zip(bindings, runs, strict=True)
    )
    instant = [states[0].started_at - timedelta(seconds=1)]
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(root / "workflow.sqlite"),
        execution_catalog=create_golden_execution_catalog(bindings=bindings),
        clock=lambda: instant[0],
        child_run_id_factory=iter(runs).__next__,
    )
    compile_input = invocations[0].effective_parameters["compile_input"]
    from self_improving.harness.schemas.common import ArtifactRef

    proposal = json.loads(
        store.resolve(
            ArtifactRef.model_validate(compile_input["interpretation_proposal"])
        ).path.read_bytes()
    )
    prompt = store.resolve(
        ArtifactRef.model_validate(proposal["effective_prompt"])
    ).path.read_text()
    evidence = tuple(
        put(
            WorldFactEvidence(
                source_kind="user_input",
                key=key,
                value=value,
                observed_at=instant[0],
                valid_until=None,
            ).model_dump(mode="json"),
            "harness.world_fact_evidence.v1",
        )
        for key, value in (
            ("request.objective", prompt),
            ("request.image_source", compile_input["source_media"]),
        )
    )
    snapshot = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="tests/native-resume",
            idempotency_key="native-start",
            workspace="ephemeral",
            requested_profile="genesis.rigid_scene@1",
            initial_fact_evidence=evidence,
            registry_snapshot=put(registry.model_dump(mode="json"), registry.schema_version),
        )
    )
    for index, (binding, invocation, state) in enumerate(
        zip(bindings, invocations, states, strict=True)
    ):
        instant[0] = state.started_at
        operation = harness.submit(
            RunCommandRequest(
                schema_version="harness.run_command_request.v1",
                command_id=uuid4(),
                principal_id=snapshot.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
                idempotency_key=f"native-{index}",
                expected_workflow_revision=snapshot.revision,
                expected_state_sha256=snapshot.state_sha256,
                expected_receipt_head_sha256=snapshot.receipt_head.sha256,
                skill_ref=binding.skill_ref,
                parameters=invocation.effective_parameters,
            )
        )
        assert operation.status.value == "succeeded"
        snapshot = harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=snapshot.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
            )
        )
        assert snapshot.revision == index + 1
