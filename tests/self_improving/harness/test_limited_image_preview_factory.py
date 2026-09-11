"""Factory start/lookup do not restore unrelated runtime or qualification roots."""

import json
from types import SimpleNamespace

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.limited_image_preview_factory import (
    LimitedImagePreviewSettings,
    LimitedPreviewRuntime,
    create_limited_image_preview_service,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas.common import ArtifactRef
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401
from tests.self_improving.harness.test_limited_image_preview_inputs import inputs  # noqa: F401


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
    runtime = LimitedPreviewRuntime(settings, LocalArtifactStore(tmp_path / "cas"))
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
