"""Public v2 control contracts preserve exact fresh-capture evidence."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from self_improving.harness.schemas.golden_control_v2 import ObservationCitationV2


def ref(schema, label="x", media="application/json"):
    digest = hashlib.sha256(label.encode()).hexdigest()
    return dict(
        name=label,
        uri="artifact://sha256/" + digest,
        sha256=digest,
        bytes=1,
        media_type=media,
        schema_version=schema,
    )


def citation():
    observation = dict(
        schema_version="harness.genesis_fresh_capture_receipt.v1",
        replay_run_id="10000000-0000-4000-8000-000000000001",
        replay_receipt=ref("harness.genesis_scene_execution_receipt.v2"),
        request=ref("harness.genesis_fresh_capture_request.v1"),
        capture_result=ref("harness.genesis_fresh_capture_result.v1"),
        image=ref(None, media="image/png"),
        captured_at="2026-09-11T10:00:00Z",
        checked_at="2026-09-11T10:00:05Z",
        expires_at="2026-09-11T10:05:00Z",
        ttl_seconds=300,
        authority="observed_restored_final_pose",
        process_continuation=False,
    )
    raw = json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
    receipt = ref(observation["schema_version"])
    receipt.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    receipt["uri"] = "artifact://sha256/" + receipt["sha256"]
    return dict(
        schema_version="harness.observation_citation.v2",
        receipt=receipt,
        observation=observation,
        captured_at=observation["captured_at"],
        expires_at=observation["expires_at"],
        ttl_seconds=300,
        authority="observed_restored_final_pose",
        process_continuation=False,
    )


def test_citation_binds_actual_fresh_receipt_not_unchecked_time_claim():
    parsed = ObservationCitationV2.model_validate(citation())
    assert parsed.observation.replay_run_id.hex == "10000000000040008000000000000001"
    assert parsed.expires_at == parsed.observation.expires_at


def command():
    return dict(
        schema_version="harness.advisory_record_command.v2",
        command_id="20000000-0000-4000-8000-000000000002",
        workflow_run_id="10000000-0000-4000-8000-000000000001",
        principal_id="codex/test",
        idempotency_key="test",
        expected_workflow_revision=1,
        expected_state_sha256="1" * 64,
        expected_receipt_head_sha256="2" * 64,
        source_prompt=ref("harness.prompt_text.v1", media="text/plain"),
        source_prompt_revision=None,
        advisory_kind="diagnosis",
        disposition="recommend",
        summary="Mouse is visible; revise the requested placement.",
        recommended_action="revise_prompt",
        failure_receipts=[ref("harness.workflow_operation_receipt.v2")],
        observation_citations=[citation()],
        diagnostic_refs=[],
        resource_access_receipts=[],
        external_agent_executed=False,
    )


def test_diagnosis_uses_native_observation_and_revision_v2():
    from self_improving.harness.schemas.golden_control_v2 import AdvisoryRecordCommandV2

    value = AdvisoryRecordCommandV2.model_validate(command())
    assert value.observation_citations[0].authority == "observed_restored_final_pose"


def revision_command():
    base = command()
    return {
        **{
            k: base[k]
            for k in (
                "command_id",
                "workflow_run_id",
                "principal_id",
                "idempotency_key",
                "expected_workflow_revision",
                "expected_state_sha256",
                "expected_receipt_head_sha256",
                "source_prompt",
                "failure_receipts",
                "external_agent_executed",
            )
        },
        "schema_version": "harness.prompt_revision_command.v2",
        "advisory_receipt": ref("harness.external_agent_advisory_receipt.v2"),
        "predecessor_environment": ref("harness.genesis_scene_execution_receipt.v2"),
        "patch": dict(
            kind="full_replace",
            base_text_sha256=base["source_prompt"]["sha256"],
            replacement_text="Move the mouse to the table center.",
            reason="Placement revision",
        ),
        "fallback": dict(kind="none"),
        "next_compile_skill_ref": "image2env.compile@2.0.0",
    }


def test_native_environment_revision_is_explicit_not_legacy_package():
    from self_improving.harness.schemas.golden_control_v2 import PromptRevisionCommandV2

    value = PromptRevisionCommandV2.model_validate(revision_command())
    assert (
        value.predecessor_environment.schema_version == "harness.genesis_scene_execution_receipt.v2"
    )


def control_receipt():
    return dict(
        command_id=command()["command_id"],
        workflow_run_id=command()["workflow_run_id"],
        principal_id="codex/test",
        command=ref("harness.advisory_record_command.v2"),
        predecessor_receipt=ref("harness.workflow_operation_receipt.v2"),
        base_revision=1,
        committed_revision=2,
        base_turn_seq=1,
        committed_turn_seq=2,
        base_state_sha256="1" * 64,
        resulting_state_sha256="1" * 64,
        base_state=ref("harness.trusted_world_state.v1"),
        resulting_state=ref("harness.trusted_world_state.v1"),
        committed_at="2026-09-11T10:01:00Z",
        authority="advisory_only",
        grants_physical_validity=False,
        grants_publishability=False,
    )


def advisory_receipt():
    a = command()
    fields = (
        "advisory_kind",
        "disposition",
        "summary",
        "recommended_action",
        "source_prompt",
        "source_prompt_revision",
        "failure_receipts",
        "observation_citations",
        "diagnostic_refs",
        "resource_access_receipts",
        "external_agent_executed",
    )
    d = {
        **control_receipt(),
        **{k: a[k] for k in fields},
        "schema_version": "harness.external_agent_advisory_receipt.v2",
    }
    payload = {"domain": "harness.external_agent_advisory_receipt.identity.v2", "receipt": d}
    d["receipt_sha256"] = hashlib.sha256(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    return d


def test_receipt_advances_control_but_keeps_state_and_authority_unchanged():
    from self_improving.harness.schemas.golden_control_v2 import ExternalAgentAdvisoryReceiptV2

    receipt = ExternalAgentAdvisoryReceiptV2.model_validate(advisory_receipt())
    assert receipt.grants_physical_validity is False


def revision():
    command_values = revision_command()
    d = {
        **control_receipt(),
        **{
            k: v
            for k, v in command_values.items()
            if k
            not in (
                "schema_version",
                "idempotency_key",
                "expected_workflow_revision",
                "expected_state_sha256",
                "expected_receipt_head_sha256",
            )
        },
        "schema_version": "harness.prompt_revision.v2",
        "prompt_revision_number": 1,
        "predecessor_prompt_revision": None,
        "observation_citations": [citation()],
        "revised_prompt": ref("harness.prompt_text.v1", media="text/plain"),
    }
    d["command"] = ref("harness.prompt_revision_command.v2")
    d["predecessor_receipt"] = d["advisory_receipt"]
    d["revision_sha256"] = hashlib.sha256(
        (
            json.dumps(
                {"domain": "harness.prompt_revision.identity.v2", "revision": d},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
    ).hexdigest()
    return d


def test_revision_receipt_is_advisory_only_and_native():
    from self_improving.harness.schemas.golden_control_v2 import PromptRevisionV2

    value = PromptRevisionV2.model_validate(revision())
    assert value.next_compile_skill_ref == "image2env.compile@2.0.0"
    assert value.grants_publishability is False


@pytest.mark.parametrize(
    "path,value",
    [
        ("receipt.schema_version", "harness.genesis_observation_receipt.v1"),
        ("receipt.media_type", "text/plain"),
        ("receipt.uri", "file:///tmp/receipt.json"),
        ("receipt.sha256", "0" * 64),
        ("receipt.bytes", 1),
        ("captured_at", "2026-09-11T10:00:01Z"),
        ("expires_at", "2026-09-11T10:04:59Z"),
        ("ttl_seconds", 299),
        ("authority", "advisory_only"),
        ("process_continuation", 0),
        ("captured_at", "2026-09-11T11:00:00+01:00"),
        ("expires_at", "2026-09-11T11:05:00+01:00"),
    ],
)
def test_citation_rejects_wrong_bytes_time_or_authority(path, value):
    d = citation()
    target = d
    for part in path.split(".")[:-1]:
        target = target[part]
    target[path.split(".")[-1]] = value
    with pytest.raises(ValidationError):
        ObservationCitationV2.model_validate(d)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_prompt": ref("wrong", media="text/plain")},
        {"source_prompt_revision": ref("harness.prompt_revision.v1")},
        {"failure_receipts": []},
        {"observation_citations": []},
        {"advisory_kind": "visual_assessment", "observation_citations": []},
        {"observation_citations": [citation(), citation()]},
        {"disposition": "abstain"},
        {"recommended_action": "none"},
        {"failure_receipts": [ref("wrong")]},
        {"diagnostic_refs": [ref("anything", media="text/plain")]},
        {"resource_access_receipts": [ref("wrong")]},
    ],
)
def test_advisory_rejects_unbound_reasoning(changes):
    from self_improving.harness.schemas.golden_control_v2 import AdvisoryRecordCommandV2

    with pytest.raises(ValidationError):
        AdvisoryRecordCommandV2.model_validate({**command(), **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"source_prompt": ref("wrong")},
        {"predecessor_environment": ref("harness.x2env_environment_package.v1")},
        {"advisory_receipt": ref("harness.external_agent_advisory_receipt.v1")},
        {
            "patch": dict(
                kind="full_replace", base_text_sha256="0" * 64, replacement_text="x", reason="x"
            )
        },
        {"next_compile_skill_ref": "image2env.compile@1.0.0"},
        {"failure_receipts": []},
        {"external_agent_executed": True},
    ],
)
def test_revision_command_rejects_legacy_and_unbound_inputs(changes):
    from self_improving.harness.schemas.golden_control_v2 import PromptRevisionCommandV2

    with pytest.raises(ValidationError):
        PromptRevisionCommandV2.model_validate({**revision_command(), **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"command": ref("harness.advisory_record_command.v1")},
        {"predecessor_receipt": ref("harness.prompt_revision.v1")},
        {"base_state": ref("wrong")},
        {"resulting_state": ref("wrong")},
        {"committed_at": "2026-09-11T11:01:00+01:00"},
        {"committed_revision": 3},
        {"committed_turn_seq": 3},
        {"resulting_state_sha256": "0" * 64},
        {"resulting_state": ref("harness.trusted_world_state.v1", "different")},
        {"committed_at": "2026-09-11T10:05:01Z"},
        {"receipt_sha256": "0" * 64},
        {"authority": "physical_pass"},
        {"grants_physical_validity": True},
        {"grants_publishability": True},
    ],
)
def test_advisory_receipt_cannot_grant_or_rewrite_authority(changes):
    from self_improving.harness.schemas.golden_control_v2 import ExternalAgentAdvisoryReceiptV2

    with pytest.raises(ValidationError):
        ExternalAgentAdvisoryReceiptV2.model_validate({**advisory_receipt(), **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"revised_prompt": ref("wrong")},
        {"predecessor_receipt": ref("harness.workflow_operation_receipt.v2")},
        {"prompt_revision_number": 2},
        {"predecessor_prompt_revision": ref("harness.prompt_revision.v2")},
        {
            "prompt_revision_number": 2,
            "predecessor_prompt_revision": ref("harness.prompt_revision.v1"),
        },
        {"revision_sha256": "0" * 64},
    ],
)
def test_revision_receipt_rejects_invalid_history(changes):
    from self_improving.harness.schemas.golden_control_v2 import PromptRevisionV2

    with pytest.raises(ValidationError):
        PromptRevisionV2.model_validate({**revision(), **changes})


def test_optional_diagnostic_reasoning_and_later_native_revision():
    from self_improving.harness.schemas.golden_control_v2 import (
        AdvisoryRecordCommandV2,
        PromptRevisionV2,
    )

    d = command()
    d.update(
        source_prompt_revision=ref("harness.prompt_revision.v2"),
        observation_citations=[],
        diagnostic_refs=[ref("diagnostic.v1")],
        disposition="abstain",
        recommended_action="none",
    )
    assert AdvisoryRecordCommandV2.model_validate(d).disposition == "abstain"
    assert (
        AdvisoryRecordCommandV2.model_validate(
            {**command(), "advisory_kind": "visual_assessment", "failure_receipts": []}
        ).advisory_kind
        == "visual_assessment"
    )
    d = revision()
    d.pop("revision_sha256")
    d.update(
        prompt_revision_number=2,
        predecessor_prompt_revision=ref("harness.prompt_revision.v2"),
        predecessor_environment=ref("harness.image2env_genesis_compile_receipt.v1"),
    )
    d["revision_sha256"] = hashlib.sha256(
        (
            json.dumps(
                {"domain": "harness.prompt_revision.identity.v2", "revision": d},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
    ).hexdigest()
    assert PromptRevisionV2.model_validate(d).prompt_revision_number == 2


def test_snapshot_can_only_point_to_its_same_v2_receipt():
    from self_improving.harness.schemas.golden_control_v2 import ControlCommitSnapshotV2

    d = control_receipt()
    for field in ("authority", "grants_physical_validity", "grants_publishability"):
        d.pop(field)
    d.update(
        schema_version="harness.control_commit_snapshot.v2",
        control_kind="advisory",
        idempotency_key="test",
        payload=ref("harness.external_agent_advisory_receipt.v2"),
        receipt=ref("harness.external_agent_advisory_receipt.v2"),
        receipt_head=ref("harness.external_agent_advisory_receipt.v2"),
    )
    assert (
        ControlCommitSnapshotV2.model_validate(d).receipt
        == ControlCommitSnapshotV2.model_validate(d).payload
    )
    for field in ("payload", "receipt_head"):
        with pytest.raises(ValidationError):
            ControlCommitSnapshotV2.model_validate({**d, field: ref("wrong")})
    d.update(control_kind="prompt_revision", command=ref("harness.prompt_revision_command.v2"))
    for field in ("payload", "receipt", "receipt_head"):
        d[field] = ref("harness.prompt_revision.v2")
    assert ControlCommitSnapshotV2.model_validate(d).control_kind == "prompt_revision"


def test_v2_contracts_are_exported_and_do_not_extend_qualified_compile_input():
    from self_improving.harness import schemas
    from self_improving.harness.schema_catalog import schema_documents
    from self_improving.harness.schemas.x2env import Image2EnvCompileV2Input

    documents = schema_documents()
    for name in (
        "ObservationCitationV2",
        "AdvisoryRecordCommandV2",
        "ExternalAgentAdvisoryReceiptV2",
        "PromptRevisionCommandV2",
        "PromptRevisionV2",
        "ControlCommitSnapshotV2",
    ):
        model = getattr(schemas, name)
        assert model.model_json_schema()["$id"] in documents
    serialized = json.dumps(Image2EnvCompileV2Input.model_json_schema())
    assert "harness.prompt_revision.v2" not in serialized
    assert "harness.observation_citation.v2" not in serialized
