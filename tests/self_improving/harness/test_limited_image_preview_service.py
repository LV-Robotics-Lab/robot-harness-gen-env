"""Preview start and resources are one existing Golden workflow, not a second journal."""

import json

from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.limited_image_preview_service import LimitedImagePreviewService
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401
from tests.self_improving.harness.test_limited_image_preview_inputs import inputs  # noqa: F401


def test_start_retry_lookup_and_audited_resources_share_one_workflow(inputs, tmp_path):  # noqa: F811
    from tests.self_improving.harness.test_golden_run import _qualified_registry

    registry = _qualified_registry(inputs.store, tmp_path)
    journal = SQLiteGoldenRunStore(tmp_path / "golden.sqlite")
    service = LimitedImagePreviewService(
        artifact_store=inputs.store, workflow_store=journal, input_builder=inputs.builder,
        registry_snapshot=registry, principal_id="test/preview",
    )
    started = service.start(raw_image_ref=inputs.raw, user_text="mouse on table", idempotency_key="one")
    assert started["snapshot"]["workflow_run_id"] == started["workflow_id"]
    assert service.start(raw_image_ref=inputs.raw, user_text="mouse on table", idempotency_key="one") == started
    looked = service.asset_lookup(workflow_id=started["workflow_id"], query="mouse")
    assert looked["asset"]["asset_id"] == "mouse"
    assert looked["resource_links"]
    resource = service.read_resource(started["workflow_id"], looked["resource_links"][0]["uri"])
    assert resource["contents"]
    status = service.status(workflow_id=started["workflow_id"])
    assert status["snapshot"] == started["snapshot"]
    snapshot = journal.read_snapshot(principal_id="test/preview", workflow_run_id=inputs.workflow)
    assert snapshot is None  # The fixture's unrelated UUID cannot be selected by the caller.
    state_ref = started["snapshot"]["state_ref"]
    from self_improving.harness.schemas.common import ArtifactRef
    state = json.loads(inputs.store.resolve(ArtifactRef.model_validate(state_ref)).path.read_bytes())
    fact = next(f for f in state["facts"] if f["key"] == "request.preview_input")
    record = json.loads(inputs.store.resolve(ArtifactRef.model_validate(fact["value"])).path.read_bytes())
    assert record["workflow_id"] == started["workflow_id"]
