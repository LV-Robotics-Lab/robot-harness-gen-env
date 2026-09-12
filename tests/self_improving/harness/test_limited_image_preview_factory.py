"""Factory start/lookup do not restore unrelated runtime or qualification roots."""

import json
import shutil
from dataclasses import replace
from types import SimpleNamespace

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.development_diagnostics import install_development_diagnostics
from self_improving.harness.limited_image_preview_factory import (
    LimitedImagePreviewSettings,
    LimitedPreviewRuntime,
    create_limited_image_preview_service,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas.common import ArtifactRef
from tests.self_improving.harness.test_golden_image2env_policy import (  # noqa: F401
    capture,
    parent_capture,
)
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401
from tests.self_improving.harness.test_limited_image_preview_inputs import inputs  # noqa: F401
from tests.self_improving.harness.test_limited_image_preview_policy import policy  # noqa: F401


def _development_validation_history_case(tmp_path, shared_policy, captured_parent, monkeypatch):
    from self_improving.harness import limited_image_preview_workflow_policies as workflow_policies
    from self_improving.harness.golden_execution import _create_golden_execution_binding
    from tests.self_improving.harness.test_mcp_native_assembly import settings_document

    observed, world, _, context, head, parent, put = captured_parent
    observation_root = tmp_path / "native-observation"
    observation_root.mkdir()
    observation_path = observation_root / "runs.sqlite"
    shutil.copy2(observed.child_authority.path, observation_path)
    scene_root = tmp_path / "native-scene"
    replay_authority = SQLiteRunStore(scene_root / "runs.sqlite")
    repository = tmp_path / "repository"
    repository.mkdir()
    document = settings_document(tmp_path)
    document.pop("compile_template_ref")
    document.update(
        schema_version="harness.limited_image_preview_settings.v2",
        execution_mode="development",
        source_repository_root=str(repository),
        development_artifact_root=str(tmp_path / "development-artifacts"),
        project_id="development-history",
        asset_catalog=document["registry_snapshot"],
        source_scene=document["registry_snapshot"],
        qualification_run_id="10000000-0000-4000-8000-000000000001",
        table_center_m=[0.0, 0.0, 0.4],
        table_size_m=[1.0, 0.6, 0.1],
        table_friction=0.5,
        seed=0,
        scene_state_root=str(scene_root),
        observation_state_root=str(observation_root),
        revised_state_root=str(tmp_path / "compile"),
        revised_qualification=document["compile_qualification"],
        shared_state_root=str(tmp_path / "shared"),
        library_root=str(tmp_path / "formal-library"),
        native_projections={},
        shared_qualifications={"image2env.validate@3.0.0": document["validation_qualification"]},
    )
    settings = LimitedImagePreviewSettings.model_validate_json(json.dumps(document))
    shared = shared_policy.application
    monkeypatch.setattr(shared, "bind", lambda qualification: None)

    def validation_binding(application, *, predecessor_authorities):
        validation_policy = workflow_policies.LimitedPreviewValidationPolicy(
            application, "image2env.validate@3.0.0", predecessor_authorities
        )
        return _create_golden_execution_binding(
            policy=validation_policy, application=None, child_authority=application.run_store
        )

    monkeypatch.setattr(workflow_policies, "create_limited_validation_binding", validation_binding)
    runtime = LimitedPreviewRuntime(settings, shared.artifact_store)
    application_calls = []

    def application(key):
        application_calls.append(key)
        if key == "shared":
            return shared
        if key == "revised":
            return shared.compiler_application
        raise AssertionError(f"history restored the live {key} application")

    monkeypatch.setattr(runtime, "application", application)
    return SimpleNamespace(
        runtime=runtime,
        calls=application_calls,
        observation_path=observation_path,
        replay_authority=replay_authority,
        observed=observed,
        world=world,
        context=context,
        head=head,
        parent=parent,
        put=put,
    )


def test_development_validate_history_reads_revision_four_without_native_apps(
    tmp_path,
    policy,  # noqa: F811
    parent_capture,  # noqa: F811
    monkeypatch,
):
    from self_improving.harness.schemas.genesis_fresh_capture import (
        GenesisFreshCaptureReceiptV1,
    )

    case = _development_validation_history_case(tmp_path, policy, parent_capture, monkeypatch)
    binding = case.runtime.history_binding("image2env.validate@3.0.0")

    assert binding.application is None
    assert case.calls == ["shared", "revised"]
    predecessors = binding.policy.predecessor_authorities
    assert predecessors["image2env.replay@2.0.0"].path == case.replay_authority.path
    assert predecessors["genesis.observe@3.0.0"].path == case.observation_path

    observed_run_id = case.parent.child_run_id
    observed_terminal = case.observed.child_authority.read_run_state(observed_run_id)
    output = GenesisFreshCaptureReceiptV1.model_validate(observed_terminal.output)
    head = case.head.model_copy(update={"base_revision": 3, "committed_revision": 4})
    context = replace(
        case.context,
        revision=4,
        receipt_head=case.put(head.model_dump(mode="json"), head.schema_version),
    )
    requested = binding.policy.normalize_parameters(
        {
            "schema_version": "harness.limited_scene_validate_input.v1",
            "execution_receipt": output.replay_receipt.model_dump(mode="json"),
            "expected_layout": None,
        }
    )
    validation = replace(binding.policy, clock=lambda: output.captured_at)
    validation.authorize_workflow(case.world, requested, context)

    original = case.observation_path.with_suffix(".original")
    case.observation_path.replace(original)
    SQLiteRunStore(case.observation_path)
    with pytest.raises(ValueError, match="durable child authority"):
        validation.authorize_workflow(case.world, requested, context)
    with pytest.raises(AssertionError, match="live replay application"):
        case.runtime.ensure("image2env.validate@3.0.0")


def test_release_validate_history_still_delegates_to_live_restore(
    tmp_path,
    policy,  # noqa: F811
    parent_capture,  # noqa: F811
    monkeypatch,
):
    from self_improving.harness import limited_image_preview_factory as factory

    case = _development_validation_history_case(tmp_path, policy, parent_capture, monkeypatch)
    document = case.runtime.settings.model_dump(mode="json")
    document.update(
        schema_version="harness.limited_image_preview_settings.v1",
        execution_mode="release",
        source_repository_root=None,
        development_artifact_root=None,
    )
    runtime = LimitedPreviewRuntime(
        LimitedImagePreviewSettings.model_validate_json(json.dumps(document)), case.runtime.store
    )
    skill = "image2env.validate@3.0.0"
    live = SimpleNamespace(policy=object(), child_authority=object())
    runtime.bindings[skill] = live
    calls = []
    monkeypatch.setattr(runtime, "ensure", calls.append)
    monkeypatch.setattr(factory, "_create_golden_execution_binding", lambda **values: values)

    restored = runtime.history_binding(skill)

    assert calls == [skill]
    assert restored == {
        "policy": live.policy,
        "application": None,
        "child_authority": live.child_authority,
    }


@pytest.mark.parametrize("authority", ("replay", "observe"))
@pytest.mark.parametrize("failure", ("missing", "corrupt"))
def test_development_validate_history_fails_closed_on_invalid_native_authority(
    tmp_path,
    policy,  # noqa: F811
    parent_capture,  # noqa: F811
    monkeypatch,
    authority,
    failure,
):
    import sqlite3

    case = _development_validation_history_case(tmp_path, policy, parent_capture, monkeypatch)
    path = case.replay_authority.path if authority == "replay" else case.observation_path
    if failure == "missing":
        path.unlink()
        with pytest.raises(ValueError, match="existing child SQLite authority"):
            case.runtime.history_binding("image2env.validate@3.0.0")
        assert not path.exists()
    else:
        path.write_bytes(b"not a SQLite authority")
        with pytest.raises(sqlite3.DatabaseError):
            case.runtime.history_binding("image2env.validate@3.0.0")
        assert path.read_bytes() == b"not a SQLite authority"
    assert case.calls == []


def test_factory_raw_input_restart_does_not_create_runtime_databases(inputs, tmp_path):  # noqa: F811
    from tests.self_improving.harness.test_golden_run import _qualified_registry
    from tests.self_improving.harness.test_mcp_native_assembly import settings_document

    document = settings_document(tmp_path)
    document.pop("compile_template_ref")
    document.update(
        schema_version="harness.limited_image_preview_settings.v1",
        project_id="preview-test",
        registry_snapshot=_qualified_registry(inputs.store, tmp_path).model_dump(mode="json"),
        asset_catalog=inputs.builder.asset_catalog.model_dump(mode="json"),
        source_scene=inputs.builder.source_scene.model_dump(mode="json"),
        qualification_run_id=str(inputs.builder.qualification_run_id),
        table_center_m=[0.0, 0.0, 0.4],
        table_size_m=[1.0, 0.6, 0.1],
        table_friction=0.5,
        seed=0,
        revised_state_root=str(tmp_path / "revised"),
        revised_qualification=document["compile_qualification"],
        shared_state_root=str(tmp_path / "shared"),
        library_root=str(tmp_path / "library"),
        native_projections={},
        shared_qualifications={},
    )
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(document))
    service = create_limited_image_preview_service(path)
    started = service.start(
        raw_image_ref=inputs.raw, user_text="mouse on table", idempotency_key="factory"
    )
    assert service.asset_lookup(workflow_id=started["workflow_id"])["asset"]["asset_id"] == "mouse"
    reopened = create_limited_image_preview_service(path)
    assert reopened.status(workflow_id=started["workflow_id"])["snapshot"] == started["snapshot"]
    with pytest.raises(ValueError, match="not yet qualified"):
        reopened.observe(workflow_id=started["workflow_id"], idempotency_key="no-late-restore")
    for role in (
        "asset.sqlite",
        "compile",
        "scene",
        "observation",
        "validation",
        "revised",
        "shared",
        "library",
    ):
        assert not (tmp_path / role).exists()


def test_lazy_projection_loaders_receive_exact_artifact_refs(tmp_path, monkeypatch):
    from self_improving.harness import limited_image_preview_factory as factory
    from self_improving.harness import limited_image_preview_policy as preview_policy
    from self_improving.harness import limited_observation_registry_projection as observation
    from self_improving.harness import native_skill_projection as native_projection
    from self_improving.harness import revised_compile_registry_projection as revised_projection
    from tests.self_improving.harness.test_mcp_native_assembly import settings_document

    document = settings_document(tmp_path)
    document.pop("compile_template_ref")
    projection = {
        name: document["compile_qualification"]
        for name in (
            "admission_ref",
            "descriptor_ref",
            "qualification_ref",
            "qualification_report_ref",
        )
    }
    document.update(
        schema_version="harness.limited_image_preview_settings.v1",
        project_id="projection-types",
        asset_catalog=document["registry_snapshot"],
        source_scene=document["registry_snapshot"],
        qualification_run_id="10000000-0000-4000-8000-000000000001",
        table_center_m=[0.0, 0.0, 0.4],
        table_size_m=[1.0, 0.6, 0.1],
        table_friction=0.5,
        seed=0,
        revised_state_root=str(tmp_path / "revised"),
        revised_qualification=document["compile_qualification"],
        shared_state_root=str(tmp_path / "shared"),
        library_root=str(tmp_path / "library"),
        native_projections={
            skill: projection
            for skill in (
                "genesis.observe@3.0.0",
                "image2env.compile@2.0.0",
                "image2env.replay@2.0.0",
            )
        },
        revised_projection=projection,
        shared_qualifications={},
    )
    settings = LimitedImagePreviewSettings.model_validate_json(json.dumps(document))
    store = LocalArtifactStore(tmp_path / "cas")
    diagnostics = install_development_diagnostics(store, runtime_manifests=())
    runtime = LimitedPreviewRuntime(settings, store)
    authorities = {
        key: SQLiteRunStore(tmp_path / f"{key}.sqlite") for key in ("compile", "replay", "observe")
    }
    applications = {
        "compile": SimpleNamespace(_run_store=authorities["compile"]),
        "replay": SimpleNamespace(run_store=authorities["replay"]),
        "observe": SimpleNamespace(run_store=authorities["observe"]),
        "revised": object(),
    }
    runtime.application = lambda key: applications[key]
    loaded = []

    def load_projection(**values):
        refs = tuple(
            values[name]
            for name in (
                "admission_ref",
                "descriptor_ref",
                "qualification_ref",
                "qualification_report_ref",
            )
        )
        assert all(type(ref) is ArtifactRef for ref in refs)
        loaded.append(refs)
        return SimpleNamespace(binding=object())

    monkeypatch.setattr(factory, "_create_golden_execution_binding", lambda **kwargs: object())
    monkeypatch.setattr(
        observation, "load_limited_observation_registry_projection", load_projection
    )
    monkeypatch.setattr(native_projection, "load_native_skill_projection", load_projection)
    monkeypatch.setattr(preview_policy, "LimitedImagePreviewPolicy", lambda *args: object())
    monkeypatch.setattr(
        revised_projection, "load_revised_compile_registry_projection", load_projection
    )

    for skill in (
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
        "genesis.observe@3.0.0",
        "image2env.compile@3.0.0",
    ):
        runtime.ensure(skill)
    assert len(loaded) == 4
    assert diagnostics.snapshot().qualified_skill_binding_count == 4
    runtime.ensure("image2env.compile@2.0.0")
    assert diagnostics.snapshot().qualified_skill_binding_count == 4


def test_operator_development_settings_isolate_the_formal_library(tmp_path):
    from tests.self_improving.harness.test_mcp_native_assembly import settings_document

    repository = tmp_path / "repository"
    repository.mkdir()
    document = settings_document(tmp_path)
    document.pop("compile_template_ref")
    document.update(
        schema_version="harness.limited_image_preview_settings.v2",
        execution_mode="development",
        source_repository_root=str(repository),
        development_artifact_root=str(tmp_path / "development-artifacts"),
        project_id="development-preview",
        asset_catalog=document["registry_snapshot"],
        source_scene=document["registry_snapshot"],
        qualification_run_id="10000000-0000-4000-8000-000000000001",
        table_center_m=[0.0, 0.0, 0.4],
        table_size_m=[1.0, 0.6, 0.1],
        table_friction=0.5,
        seed=0,
        revised_state_root=str(tmp_path / "revised"),
        revised_qualification=document["compile_qualification"],
        shared_state_root=str(tmp_path / "shared"),
        library_root=str(tmp_path / "formal-library"),
        native_projections={},
        shared_qualifications={},
    )
    settings = LimitedImagePreviewSettings.model_validate_json(json.dumps(document))
    runtime = LimitedPreviewRuntime(settings, LocalArtifactStore(tmp_path / "cas"))

    with pytest.raises(ValueError, match="development mode cannot open the formal preview library"):
        runtime.application("library")
    with pytest.raises(ValueError, match="development mode cannot bind formal publication"):
        runtime.ensure("image2env.preview_publish@1.0.0")
    assert not (tmp_path / "formal-library").exists()

    with pytest.raises(ValueError, match="settings v2"):
        LimitedImagePreviewSettings.model_validate_json(
            json.dumps({**document, "schema_version": "harness.limited_image_preview_settings.v1"})
        )
    with pytest.raises(ValueError, match="release settings cannot configure development"):
        LimitedImagePreviewSettings.model_validate_json(
            json.dumps({**document, "execution_mode": "release"})
        )
