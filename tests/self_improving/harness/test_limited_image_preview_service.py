"""Preview start and resources are one existing Golden workflow, not a second journal."""

import json

import pytest

from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.limited_image_preview_service import LimitedImagePreviewService
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401
from tests.self_improving.harness.test_limited_image_preview_inputs import inputs  # noqa: F401


def test_start_retry_lookup_and_audited_resources_share_one_workflow(inputs, tmp_path):  # noqa: F811
    from tests.self_improving.harness.test_golden_run import _qualified_registry

    registry = _qualified_registry(inputs.store, tmp_path)
    journal = SQLiteGoldenRunStore(tmp_path / "golden.sqlite")
    service = LimitedImagePreviewService(
        artifact_store=inputs.store,
        workflow_store=journal,
        input_builder=inputs.builder,
        registry_snapshot=registry,
        principal_id="test/preview",
    )
    started = service.start(
        raw_image_ref=inputs.raw, user_text="mouse on table", idempotency_key="one"
    )
    with pytest.raises(ValueError, match="audited lookup"):
        service.compile(
            workflow_id=started["workflow_id"], asset_id="mouse", idempotency_key="compile",
            expected_skill_ref="image2env.compile@2.0.0",
        )
    assert started["snapshot"]["workflow_run_id"] == started["workflow_id"]
    assert (
        service.start(raw_image_ref=inputs.raw, user_text="mouse on table", idempotency_key="one")
        == started
    )
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

    state = json.loads(
        inputs.store.resolve(ArtifactRef.model_validate(state_ref)).path.read_bytes()
    )
    fact = next(f for f in state["facts"] if f["key"] == "request.preview_input")
    record = json.loads(
        inputs.store.resolve(ArtifactRef.model_validate(fact["value"])).path.read_bytes()
    )
    assert record["workflow_id"] == started["workflow_id"]
    assert service.read_resource(started["workflow_id"], resource["contents"][1]["uri"])["contents"]
    image_uri = f"harness://workflows/{started['workflow_id']}/artifacts/{inputs.raw.sha256}"
    assert "blob" in service.read_resource(started["workflow_id"], image_uri)["contents"][0]
    with pytest.raises(ValueError, match="different input"):
        service.start(
            raw_image_ref=inputs.raw, user_text="different mouse request", idempotency_key="one"
        )
    with pytest.raises(ValueError, match="exact workflow"):
        service.read_resource(inputs.workflow, image_uri)
    with pytest.raises(ValueError, match="exactly one"):
        service.start(user_text="mouse", idempotency_key="missing")


@pytest.mark.parametrize("change", ["workflow", "extra", "prompt", "catalog", "missing"])
def test_preview_input_resource_reader_rejects_foreign_or_incomplete_roots(inputs, change):  # noqa: F811
    from uuid import uuid4

    from self_improving.harness.limited_image_preview_input_evidence import (
        read_limited_preview_input,
    )

    ref = inputs.builder.start(
        workflow_id=inputs.workflow, raw_image_ref=inputs.raw, user_text="mouse"
    )
    payload = json.loads(inputs.store.resolve(ref).path.read_bytes())
    if change == "workflow":
        payload["workflow_id"] = str(uuid4())
    elif change == "extra":
        payload["arbitrary"] = inputs.raw.model_dump(mode="json")
    elif change == "prompt":
        payload["effective_prompt"]["schema_version"] = None
    elif change == "catalog":
        payload["catalog_ref"]["schema_version"] = None
    else:
        inputs.store.resolve(inputs.builder.asset_catalog).path.unlink()
    invalid = inputs.publish(payload, "invalid-preview-input.json", None)
    with pytest.raises(ValueError):
        read_limited_preview_input(inputs.store, invalid, inputs.workflow)
