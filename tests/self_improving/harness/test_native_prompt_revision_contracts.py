"""New native revision contracts preserve old versions and bind explicit inputs."""

import hashlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from self_improving.harness.schemas.native_prompt_revision import TabletopLayoutRevisionV1


def ref(schema, text="evidence", media="application/json"):
    payload = text.encode()
    digest = hashlib.sha256(payload).hexdigest()
    return dict(name="evidence", uri="artifact://sha256/" + digest, sha256=digest,
                bytes=len(payload), media_type=media, schema_version=schema)


def layout():
    text = "Place the mouse at x=0.10 m, y=0.00 m, yaw=0.00 rad on the table."
    return dict(
        schema_version="harness.tabletop_layout_revision.v1", workflow_run_id=uuid4(),
        principal_id="operator", base_revision=5, state_sha256="a"*64, receipt_head_sha256="b"*64,
        original_source=ref(None), source_object_id="mouse", replacement_text=text,
        effective_prompt=ref("harness.prompt_text.v1", text, "text/plain"),
        position_xy_m=(0.1, 0.0), yaw_rad=0.0,
        table_center_m=(0.0, 0.0, 0.37), table_size_m=(1.0, 1.0, 0.74),
        relation="on_table", physical_properties="preserve_qualified_asset",
    )


def test_layout_binds_text_and_explicit_bounded_scene_edit():
    value = TabletopLayoutRevisionV1.model_validate(layout())
    assert value.position_xy_m == (0.1, 0.0)
    assert value.physical_properties == "preserve_qualified_asset"


@pytest.mark.parametrize("field,value", [
    ("replacement_text", "different prompt"), ("position_xy_m", (2.0, 0.0)),
    ("yaw_rad", float("nan")), ("relation", "inside"), ("mass", 2.0),
])
def test_layout_rejects_unconsumed_prompt_and_unsupported_edits(field, value):
    document = layout()
    document[field] = value
    with pytest.raises(ValidationError):
        TabletopLayoutRevisionV1.model_validate(document)
