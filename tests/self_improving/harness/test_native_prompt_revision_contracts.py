"""New native revision contracts preserve old versions and bind explicit inputs."""

import hashlib
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from self_improving.harness.schemas.native_prompt_revision import TabletopLayoutRevisionV1


def typed_draft(model, data):
    """Validate nested fields before filling the public model's self-hash."""
    return model.model_construct(
        **{
            name: TypeAdapter(model.model_fields[name].rebuild_annotation()).validate_python(value)
            for name, value in data.items()
        }
    )


def ref(schema, text="evidence", media="application/json"):
    payload = text.encode()
    digest = hashlib.sha256(payload).hexdigest()
    return dict(
        name="evidence",
        uri="artifact://sha256/" + digest,
        sha256=digest,
        bytes=len(payload),
        media_type=media,
        schema_version=schema,
    )


def layout():
    text = "Place the mouse at x=0.10 m, y=0.00 m, yaw=0.00 rad on the table."
    return dict(
        schema_version="harness.tabletop_layout_revision.v1",
        workflow_run_id=uuid4(),
        principal_id="operator",
        base_revision=5,
        state_sha256="a" * 64,
        receipt_head_sha256="b" * 64,
        original_source=ref(None),
        source_object_id="mouse",
        replacement_text=text,
        effective_prompt=ref("harness.prompt_text.v1", text, "text/plain"),
        position_xy_m=(0.1, 0.0),
        yaw_rad=0.0,
        table_center_m=(0.0, 0.0, 0.37),
        table_size_m=(1.0, 1.0, 0.74),
        relation="on_table",
        physical_properties="preserve_qualified_asset",
    )


def test_layout_binds_text_and_explicit_bounded_scene_edit():
    value = TabletopLayoutRevisionV1.model_validate(layout())
    assert value.position_xy_m == (0.1, 0.0)
    assert value.physical_properties == "preserve_qualified_asset"


@pytest.mark.parametrize(
    "field,value",
    [
        ("replacement_text", "different prompt"),
        ("position_xy_m", (2.0, 0.0)),
        ("yaw_rad", float("nan")),
        ("relation", "inside"),
        ("mass", 2.0),
    ],
)
def test_layout_rejects_unconsumed_prompt_and_unsupported_edits(field, value):
    document = layout()
    document[field] = value
    with pytest.raises(ValidationError):
        TabletopLayoutRevisionV1.model_validate(document)


def command():
    change = layout()
    advisory = ref("harness.external_agent_advisory_receipt.v2")
    change["receipt_head_sha256"] = advisory["sha256"]
    prompt = ref("harness.prompt_text.v1", "original", "text/plain")
    return dict(
        schema_version="harness.prompt_revision_command.v3",
        command_id=uuid4(),
        principal_id=change["principal_id"],
        workflow_run_id=change["workflow_run_id"],
        idempotency_key="revision-three",
        expected_workflow_revision=change["base_revision"],
        expected_state_sha256=change["state_sha256"],
        expected_receipt_head_sha256=advisory["sha256"],
        source_prompt=prompt,
        advisory_receipt=advisory,
        failure_receipts=(ref("harness.workflow_operation_receipt.v2"),),
        predecessor_environment=ref("harness.image2env_genesis_compile_receipt.v1"),
        patch=dict(
            kind="full_replace",
            base_text_sha256=prompt["sha256"],
            replacement_text=change["replacement_text"],
            reason="Move within table",
        ),
        fallback=dict(kind="none"),
        next_compile_skill_ref="image2env.compile@3.0.0",
        external_agent_executed=False,
        layout_proposal=change,
    )


def test_control_v3_names_new_compile_without_reinterpreting_old_control():
    from self_improving.harness.schemas.native_prompt_revision import PromptRevisionCommandV3

    value = PromptRevisionCommandV3.model_validate(command())
    assert value.next_compile_skill_ref == "image2env.compile@3.0.0"


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_run_id", uuid4()),
        ("expected_workflow_revision", 6),
        ("expected_receipt_head_sha256", "c" * 64),
        ("next_compile_skill_ref", "image2env.compile@2.0.0"),
        ("fallback", dict(kind="video_frame_to_image")),
    ],
)
def test_control_v3_rejects_foreign_declared_context_and_old_consumer(field, value):
    from self_improving.harness.schemas.native_prompt_revision import PromptRevisionCommandV3

    document = command()
    document[field] = value
    with pytest.raises(ValidationError):
        PromptRevisionCommandV3.model_validate(document)


def revision():
    from datetime import datetime, timezone

    from self_improving.harness.schemas.native_prompt_revision import (
        PromptRevisionV3,
        prompt_revision_v3_sha256,
    )

    data = command()
    for key in (
        "idempotency_key",
        "expected_workflow_revision",
        "expected_state_sha256",
        "expected_receipt_head_sha256",
    ):
        data.pop(key)
    state = ref("harness.trusted_world_state.v1")
    data.update(
        schema_version="harness.prompt_revision.v3",
        command=ref("harness.prompt_revision_command.v3"),
        predecessor_receipt=data["advisory_receipt"],
        base_revision=5,
        committed_revision=6,
        base_turn_seq=5,
        committed_turn_seq=6,
        base_state_sha256="a" * 64,
        resulting_state_sha256="a" * 64,
        base_state=state,
        resulting_state=state,
        committed_at=datetime(2026, 9, 11, tzinfo=timezone.utc),
        prompt_revision_number=1,
        predecessor_prompt_revision=None,
        observation_citations=(),
        revised_prompt=data["layout_proposal"]["effective_prompt"],
        authority="advisory_only",
        grants_physical_validity=False,
        grants_publishability=False,
        revision_sha256="0" * 64,
    )
    draft = typed_draft(PromptRevisionV3, data)
    data["revision_sha256"] = prompt_revision_v3_sha256(draft)
    return data


def test_revision_v3_receipt_has_new_identity_and_preserves_state():
    from self_improving.harness.schemas.native_prompt_revision import PromptRevisionV3

    value = PromptRevisionV3.model_validate(revision())
    assert value.base_state == value.resulting_state
    assert value.authority == "advisory_only"


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision_sha256", "f" * 64),
        ("revised_prompt", ref("harness.prompt_text.v1", "ignored", "text/plain")),
        ("predecessor_receipt", ref("harness.workflow_operation_receipt.v2", "other")),
        ("prompt_revision_number", 2),
    ],
)
def test_revision_v3_rejects_drift(field, value):
    from self_improving.harness.schemas.native_prompt_revision import PromptRevisionV3

    data = revision()
    data[field] = value
    with pytest.raises(ValidationError):
        PromptRevisionV3.model_validate(data)


def model_ref(model, newline=False):
    import json

    text = json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return ref(model.schema_version, text + ("\n" if newline else ""))


def compile_request():
    from self_improving.harness.schemas.native_prompt_revision import PromptRevisionV3
    from self_improving.harness.schemas.x2env_compile_evidence import (
        SceneInterpretationProposalV1,
        canonical_evidence_sha256,
    )

    revised = PromptRevisionV3.model_validate(revision())
    image = ref(None, "image", "image/png")
    proposal = dict(
        schema_version="harness.scene_interpretation_proposal.v1",
        proposal_sha256="0" * 64,
        route_id="image2env",
        source_media_sha256=image["sha256"],
        effective_prompt=revised.revised_prompt,
        objects=(
            dict(object_id="mouse", category="mouse", role="dynamic", dimensions_m=(0.1, 0.1, 0.1)),
        ),
        relations=(dict(relation="on_table", source="mouse", target="table"),),
        unknowns=(),
    )
    proposal["proposal_sha256"] = canonical_evidence_sha256(
        typed_draft(SceneInterpretationProposalV1, proposal), "proposal_sha256"
    )
    proposal = SceneInterpretationProposalV1.model_validate(proposal)
    attempt = dict(
        schema_version="harness.prompt_revision_compile_attempt.v2",
        kind="prompt_revision",
        prompt_revision=model_ref(revised, True),
        revision=revised,
        effective_prompt=revised.revised_prompt,
        failure_receipts=revised.failure_receipts,
        predecessor_environment=revised.predecessor_environment,
    )
    return dict(
        schema_version="harness.image2env_genesis_compile_request.v2",
        workflow_run_id=revised.workflow_run_id,
        principal_id=revised.principal_id,
        expected_workflow_revision=revised.committed_revision,
        expected_state_sha256=revised.resulting_state_sha256,
        expected_receipt_head_sha256=attempt["prompt_revision"]["sha256"],
        qualification_run_id=uuid4(),
        compile_input=dict(
            schema_version="harness.image2env_compile_input.v3",
            attempt=attempt,
            source_media=dict(
                schema_version="harness.image_source_media.v1",
                source_media_sha256=image["sha256"],
                source=image,
                canonical_image=image,
                probe=ref("harness.image_media_probe.v1"),
                width=640,
                height=480,
                color_mode="RGB",
                decoded_frame_count=1,
                unique_frame_count=1,
            ),
            interpretation_proposal=model_ref(proposal),
            proposal=proposal,
            asset_catalog=ref("harness.portable_asset_catalog.v1"),
            seed=17,
            requested_profile="genesis.rigid_scene@1",
            acquisition_policy="exact_only",
            model_execution_allowed=False,
        ),
    )


def test_native_compile_v2_request_consumes_revision_and_proposal():
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
    )

    value = Image2EnvGenesisCompileRequestV2.model_validate(compile_request())
    assert value.compile_input.attempt.revision.layout_proposal.source_object_id == "mouse"


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_run_id", uuid4()),
        ("expected_workflow_revision", 99),
        ("expected_receipt_head_sha256", "f" * 64),
    ],
)
def test_recompile_contract_rejects_another_declared_workflow_head(field, value):
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
    )

    data = compile_request()
    data[field] = value
    with pytest.raises(ValidationError):
        Image2EnvGenesisCompileRequestV2.model_validate(data)


def receipt():
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
    )

    request = Image2EnvGenesisCompileRequestV2.model_validate(compile_request())
    return dict(
        schema_version="harness.image2env_genesis_compile_receipt.v2",
        request=model_ref(request),
        scene=ref("harness.genesis_scene_spec.v1"),
        qualification=ref("harness.asset_qualification.v1"),
        layout_proposal=model_ref(request.compile_input.attempt.revision.layout_proposal),
        original_source=request.compile_input.attempt.revision.layout_proposal.original_source,
        derived_source_scene=ref(None, "derived source"),
        source_object_id="mouse",
        proposal_object_id="mouse",
        scene_object_id="object",
        implementation_sha256="a" * 64,
        implementation=(ref(None, "compiler bytes", "text/x-python"),),
        scope="static_scene_candidate",
        simulator_executed=False,
    )


def test_native_receipt_v2_retains_source_derivation_and_static_scope():
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileReceiptV2,
    )

    value = Image2EnvGenesisCompileReceiptV2.model_validate(receipt())
    assert value.original_source != value.derived_source_scene
    assert value.scope == "static_scene_candidate"


@pytest.mark.parametrize(
    "field,value",
    [
        ("proposal_object_id", "other"),
        ("simulator_executed", True),
        ("implementation", (ref(None, "a", "application/json"),)),
    ],
)
def test_native_receipt_cannot_claim_execution_or_different_object(field, value):
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileReceiptV2,
    )

    data = receipt()
    data[field] = value
    with pytest.raises(ValidationError):
        Image2EnvGenesisCompileReceiptV2.model_validate(data)


def test_table_bottom_and_native_predecessor_are_bounded():
    from self_improving.harness.schemas.native_prompt_revision import PromptRevisionCommandV3

    data = layout()
    data["table_center_m"] = (0.0, 0.0, 0.0)
    with pytest.raises(ValidationError, match="bottom"):
        TabletopLayoutRevisionV1.model_validate(data)
    data = command()
    data["predecessor_environment"] = ref("harness.x2env_environment_package.v1")
    with pytest.raises(ValidationError, match="native environment"):
        PromptRevisionCommandV3.model_validate(data)
    data = command()
    data["patch"]["replacement_text"] = "not consumed"
    with pytest.raises(ValidationError, match="consume"):
        PromptRevisionCommandV3.model_validate(data)
    data = command()
    data["expected_receipt_head_sha256"] = data["layout_proposal"]["receipt_head_sha256"] = "c" * 64
    with pytest.raises(ValidationError, match="immediately"):
        PromptRevisionCommandV3.model_validate(data)


def test_revision_sequence_and_advisory_identity():
    from self_improving.harness.schemas.native_prompt_revision import (
        PromptRevisionV3,
        prompt_revision_v3_sha256,
    )

    data = revision()
    data["prompt_revision_number"] = 2
    data["predecessor_prompt_revision"] = ref("harness.prompt_revision.v3")
    data["revision_sha256"] = prompt_revision_v3_sha256(typed_draft(PromptRevisionV3, data))
    assert PromptRevisionV3.model_validate(data).prompt_revision_number == 2
    data = revision()
    other = ref("harness.external_agent_advisory_receipt.v2", "other")
    data["predecessor_receipt"] = other
    data["layout_proposal"]["receipt_head_sha256"] = other["sha256"]
    with pytest.raises(ValidationError, match="immediately"):
        PromptRevisionV3.model_validate(data)


@pytest.mark.parametrize("change", ("payload", "attempt_prompt", "source_media"))
def test_compile_input_requires_exact_consumption(change):
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileRequestV2,
    )

    data = compile_request()
    inputs = data["compile_input"]
    if change == "payload":
        inputs["attempt"]["prompt_revision"] = ref("harness.prompt_revision.v3", "not the revision")
    elif change == "attempt_prompt":
        inputs["attempt"]["effective_prompt"] = ref(
            "harness.prompt_text.v1", "unconsumed", "text/plain"
        )
    else:
        image = ref(None, "another image", "image/png")
        inputs["source_media"]["source"] = image
        inputs["source_media"]["source_media_sha256"] = image["sha256"]
    with pytest.raises(ValidationError):
        Image2EnvGenesisCompileRequestV2.model_validate(data)


def test_receipt_implementation_members_are_unique():
    from self_improving.harness.schemas.native_prompt_revision import (
        Image2EnvGenesisCompileReceiptV2,
    )

    data = receipt()
    data["implementation"] *= 2
    with pytest.raises(ValidationError, match="unique"):
        Image2EnvGenesisCompileReceiptV2.model_validate(data)
