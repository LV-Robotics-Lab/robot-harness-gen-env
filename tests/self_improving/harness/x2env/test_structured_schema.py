"""Regression for the real Codex invalid_json_schema rejection on media_index."""

from self_improving.harness.x2env.contracts import FieldProvenance, SceneIntentProposal
from self_improving.harness.x2env.schema_export import structured_output_schema


def test_nullable_provenance_fields_are_required_in_model_output_schema():
    schema = structured_output_schema(FieldProvenance)
    assert set(schema["required"]) == {
        "source",
        "input_sha256",
        "kind",
        "media_index",
        "frame_index",
        "note",
    }
    assert schema["additionalProperties"] is False
    assert {option.get("type") for option in schema["properties"]["media_index"]["anyOf"]} == {
        "integer",
        "null",
    }


def test_full_scene_proposal_contains_only_closed_objects_and_homogeneous_arrays():
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            if node.get("type") == "array":
                assert "items" in node
                assert "prefixItems" not in node
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(structured_output_schema(SceneIntentProposal))
