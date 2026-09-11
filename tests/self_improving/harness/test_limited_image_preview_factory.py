"""Factory start/lookup do not restore unrelated runtime or qualification roots."""

import json

from self_improving.harness.limited_image_preview_factory import create_limited_image_preview_service
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
        table_center_m=[0.0, 0.0, 0.4], table_size_m=[1.0, 0.6, 0.1], table_friction=0.5,
        seed=0, revised_state_root=str(tmp_path / "revised"),
        revised_qualification=document["compile_qualification"],
        shared_state_root=str(tmp_path / "shared"), library_root=str(tmp_path / "library"),
        native_projections={}, shared_qualifications={},
    )
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(document))
    service = create_limited_image_preview_service(path)
    started = service.start(raw_image_ref=inputs.raw, user_text="mouse on table", idempotency_key="factory")
    assert service.asset_lookup(workflow_id=started["workflow_id"])["asset"]["asset_id"] == "mouse"
    reopened = create_limited_image_preview_service(path)
    assert reopened.status(workflow_id=started["workflow_id"])["snapshot"] == started["snapshot"]
    for role in ("asset.sqlite", "compile", "scene", "observation", "validation", "revised", "shared", "library"):
        assert not (tmp_path / role).exists()
