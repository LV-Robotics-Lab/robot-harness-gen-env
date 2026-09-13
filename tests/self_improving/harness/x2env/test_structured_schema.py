"""Regression for the real Codex invalid_json_schema rejection on media_index."""

from self_improving.harness.x2env.contracts import FieldProvenance, SceneIntentProposal
from self_improving.harness.x2env.schema_export import structured_output_schema


def test_request_bound_schema_rejects_transcribed_hash_without_changing_static_schema(tmp_path):
    import copy

    import jsonschema
    import pytest

    from self_improving.harness.x2env.contracts import X2EnvRequest
    from self_improving.harness.x2env.input import ingest
    from self_improving.harness.x2env.store import Store

    bundle = ingest(
        X2EnvRequest(text="a table", seed=11, idempotency_key="bound", output_dir=str(tmp_path)),
        Store(tmp_path / "state"),
    )
    static = structured_output_schema(SceneIntentProposal)
    schema = structured_output_schema(SceneIntentProposal, bundle=bundle)
    assert (
        schema["$defs"]["SceneIR"]["properties"]["input_sha256"]["const"] == bundle.request_sha256
    )
    assert schema["$defs"]["SceneIR"]["properties"]["revision"]["const"] == 0
    proposal = {
        "schema_version": "x2env.scene_intent_proposal.v2",
        "scene": None,
        "unknowns": [
            {
                "field": "dimensions",
                "reason": "unspecified",
                "critical": True,
                "reason_kind": "scale_unobservable",
                "provenance": [
                    {
                        "source": "text",
                        "input_sha256": bundle.text.sha256,
                        "kind": "inferred",
                        "media_index": None,
                        "frame_index": None,
                        "note": "No dimensions supplied",
                    }
                ],
            }
        ],
    }
    jsonschema.validate(proposal, schema)
    wrong = copy.deepcopy(proposal)
    # Actual table transport probe transposed these SHA substrings; keep the regression.
    wrong["unknowns"][0]["provenance"][0]["input_sha256"] = (
        "ad875d4bcb7fa0fef5d27aadd709b8ca253b2a10d15e8283bf26918e8283d4dc"
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(wrong, schema)
    assert structured_output_schema(SceneIntentProposal) == static


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


def test_bound_schema_collects_original_sources_not_decoder_or_frame_hashes():
    from self_improving.harness.x2env.contracts import (
        ArtifactRef,
        ImageInputEvidence,
        InputBundle,
        VideoInputEvidence,
    )

    def ref(char, media):
        return ArtifactRef(sha256=char * 64, size_bytes=10, media_type=media)

    image = ImageInputEvidence(
        source=ref("b", "image/jpeg"),
        canonical=ref("c", "image/png"),
        width=2,
        height=2,
        mode="RGB",
        decoder_version="test",
    )
    video = VideoInputEvidence(
        source=ref("d", "video/mp4"),
        probe=ref("e", "application/json"),
        sequence=ref("f", "application/json"),
        width=2,
        height=2,
        codec="test",
        fps_num=1,
        fps_den=1,
        time_base_num=1,
        time_base_den=1,
        frame_count=1,
        unique_frame_count=1,
        decoder_version="test",
    )
    bundle = InputBundle(
        request_sha256="0" * 64,
        text=ref("a", "text/plain"),
        images=(image, image),
        video=video,
        modality="multimodal",
        seed=1,
        resource_limits={},
    )
    schema = structured_output_schema(SceneIntentProposal, bundle=bundle)
    assert schema["$defs"]["FieldProvenance"]["properties"]["input_sha256"]["enum"] == [
        "a" * 64,
        "b" * 64,
        "d" * 64,
    ]


def test_binding_context_rejects_wrong_model_empty_sources_and_invalid_digest():
    import pytest

    from self_improving.harness.x2env.contracts import ArtifactRef, InputBundle

    bundle = InputBundle(
        request_sha256="0" * 64, text=None, modality="text", seed=1, resource_limits={}
    )
    with pytest.raises(ValueError, match="empty_schema_binding_sources"):
        structured_output_schema(SceneIntentProposal, bundle=bundle)
    with pytest.raises(ValueError, match="invalid_schema_binding_context"):
        structured_output_schema(FieldProvenance, bundle=bundle)
    with pytest.raises(ValueError, match="invalid_schema_binding_context"):
        structured_output_schema(SceneIntentProposal, bundle={})
    invalid = bundle.model_copy(
        update={
            "text": ArtifactRef.model_construct(
                sha256="not-a-digest", size_bytes=10, media_type="text/plain"
            )
        }
    )
    with pytest.raises(ValueError):
        structured_output_schema(SceneIntentProposal, bundle=invalid)


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
