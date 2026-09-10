"""RED contracts for advisory and prompt-revision workflow controls."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from self_improving.harness.schema_catalog import schema_documents
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.golden_control import (
    ADVISORY_RECORD_COMMAND_SCHEMA_ID,
    CONTROL_COMMIT_SNAPSHOT_SCHEMA_ID,
    EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
    PROMPT_REVISION_COMMAND_SCHEMA_ID,
    PROMPT_REVISION_SCHEMA_ID,
    AdvisoryRecordCommand,
    ControlCommitSnapshot,
    ExternalAgentAdvisoryReceipt,
    NoPromptFallback,
    ObservationCitation,
    PromptRevision,
    PromptRevisionCommand,
    ReplacePromptPatch,
    VideoFrameToImageFallback,
    external_agent_advisory_receipt_sha256,
    prompt_revision_sha256,
)

NOW = datetime(2026, 9, 11, 3, 0, tzinfo=timezone.utc)
WORKFLOW_ID = UUID("10000000-0000-4000-8000-000000000001")
CONTROL_ID = UUID("20000000-0000-4000-8000-000000000002")
STATE_SHA = "1" * 64
HEAD_SHA = "2" * 64


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _ref(
    label: str,
    schema_version: str | None,
    *,
    media_type: str = "application/json",
) -> ArtifactRef:
    digest = _sha(label)
    return ArtifactRef(
        name=f"{label}.json",
        uri=f"artifact://sha256/{digest}",
        media_type=media_type,
        sha256=digest,
        bytes=100 + len(label),
        schema_version=schema_version,
    )


def _text_ref(label: str, value: str) -> ArtifactRef:
    encoded = value.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return ArtifactRef(
        name=f"{label}.txt",
        uri=f"artifact://sha256/{digest}",
        media_type="text/plain",
        sha256=digest,
        bytes=len(encoded),
        schema_version="harness.prompt_text.v1",
    )


def _ordered(*refs: ArtifactRef) -> tuple[ArtifactRef, ...]:
    return tuple(
        sorted(refs, key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type))
    )


def _observation(label: str = "fresh-observation") -> dict[str, Any]:
    return {
        "receipt": _ref(label, "harness.genesis_observation_receipt.v1"),
        "captured_at": NOW - timedelta(seconds=30),
        "valid_until": NOW,
        "ttl_seconds": 30,
    }


def _ordered_observations(*labels: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        sorted(
            (_observation(label) for label in labels),
            key=lambda item: item["receipt"].sha256,
        )
    )


def _command_authority(schema_version: str) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "command_id": CONTROL_ID,
        "principal_id": "tests/golden-control",
        "workflow_run_id": WORKFLOW_ID,
        "idempotency_key": "control-command-001",
        "expected_workflow_revision": 7,
        "expected_state_sha256": STATE_SHA,
        "expected_receipt_head_sha256": HEAD_SHA,
    }


def _advisory_command_values(kind: str = "diagnosis") -> dict[str, Any]:
    failure = _ref("failed-replay", "harness.workflow_operation_receipt.v2")
    diagnostic = _ref("penetration-diagnostic", "robotwin.scene_validation.v1")
    values = {
        **_command_authority("harness.advisory_record_command.v1"),
        "advisory_kind": kind,
        "disposition": "recommend",
        "summary": "The replay failed because the can penetrated the plate.",
        "recommended_action": "revise_prompt",
        "source_prompt": _text_ref(
            "source-prompt",
            "Use the source video to build a stable robot-policy scene.",
        ),
        "source_prompt_revision": None,
        "failure_receipts": (failure,),
        "observation_citations": (_observation(),),
        "diagnostic_refs": (diagnostic,),
        "external_agent_executed": False,
    }
    if kind == "visual_assessment":
        values["failure_receipts"] = ()
        values["diagnostic_refs"] = ()
        values["recommended_action"] = "retry"
        values["summary"] = "The can is visible above the plate in the fresh observation."
    return values


def _control_authority(*, command_schema: str) -> dict[str, Any]:
    state_ref = _ref("state", "harness.trusted_world_state.v1")
    return {
        "command_id": CONTROL_ID,
        "workflow_run_id": WORKFLOW_ID,
        "principal_id": "tests/golden-control",
        "command": _ref("control-command", command_schema),
        "predecessor_receipt": _ref(
            "predecessor-receipt",
            "harness.workflow_operation_receipt.v2",
        ),
        "base_revision": 7,
        "committed_revision": 8,
        "base_turn_seq": 4,
        "committed_turn_seq": 5,
        "base_state_sha256": STATE_SHA,
        "base_state": state_ref,
        "resulting_state_sha256": STATE_SHA,
        "resulting_state": state_ref,
        "committed_at": NOW,
    }


def _canonical_identity(domain: str, key: str, model: Any, hash_field: str) -> str:
    payload = {
        "domain": domain,
        key: model.model_dump(mode="json", exclude={hash_field}),
    }
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _advisory_receipt_values(kind: str = "diagnosis") -> dict[str, Any]:
    command = _advisory_command_values(kind)
    values = {
        "schema_version": "harness.external_agent_advisory_receipt.v1",
        **_control_authority(command_schema="harness.advisory_record_command.v1"),
        "advisory_kind": command["advisory_kind"],
        "disposition": command["disposition"],
        "summary": command["summary"],
        "recommended_action": command["recommended_action"],
        "source_prompt": command["source_prompt"],
        "source_prompt_revision": command["source_prompt_revision"],
        "failure_receipts": command["failure_receipts"],
        "observation_citations": command["observation_citations"],
        "diagnostic_refs": command["diagnostic_refs"],
        "authority": "advisory_only",
        "external_agent_executed": False,
        "grants_physical_validity": False,
        "grants_publishability": False,
        "receipt_sha256": "0" * 64,
    }
    unhashed = ExternalAgentAdvisoryReceipt.model_construct(
        **{
            **values,
            "observation_citations": tuple(
                ObservationCitation.model_validate(item) for item in values["observation_citations"]
            ),
        }
    )
    values["receipt_sha256"] = _canonical_identity(
        "harness.external_agent_advisory_receipt.identity.v1",
        "receipt",
        unhashed,
        "receipt_sha256",
    )
    return values


def _prompt_command_values(*, fallback: str = "none") -> dict[str, Any]:
    source_text = "Use the source video to build a stable robot-policy scene."
    replacement_text = "Place the can upright at the center of the plate."
    source_prompt = _text_ref("source-prompt", source_text)
    fallback_value: dict[str, Any] = {"kind": "none"}
    if fallback == "video_frame_to_image":
        fallback_value = {
            "kind": "video_frame_to_image",
            "source_video": _ref(
                "source-video",
                None,
                media_type="video/mp4",
            ),
            "selected_frame": _ref(
                "selected-video-frame",
                None,
                media_type="image/png",
            ),
            "frame_index": 7,
            "frame_sequence_manifest": _ref(
                "frame-sequence-manifest",
                "harness.video_frame_sequence.v1",
            ),
        }
    return {
        **_command_authority("harness.prompt_revision_command.v1"),
        "source_prompt": source_prompt,
        "advisory_receipt": _ref(
            "advisory-receipt",
            "harness.external_agent_advisory_receipt.v1",
        ),
        "failure_receipts": (_ref("failed-replay", "harness.workflow_operation_receipt.v2"),),
        "predecessor_package": _ref(
            "failed-package",
            "harness.x2env_environment_package.v1",
        ),
        "patch": {
            "kind": "full_replace",
            "base_text_sha256": source_prompt.sha256,
            "replacement_text": replacement_text,
            "reason": "The failed video route requires an explicit frame-derived retry.",
        },
        "fallback": fallback_value,
        "next_compile_skill_ref": (
            "image2env.compile@1.0.0"
            if fallback == "video_frame_to_image"
            else "video2env.compile@1.0.0"
        ),
        "external_agent_executed": False,
    }


def _prompt_revision_values(*, fallback: str = "none") -> dict[str, Any]:
    command = _prompt_command_values(fallback=fallback)
    advisory = command["advisory_receipt"]
    authority = _control_authority(command_schema="harness.prompt_revision_command.v1")
    authority["predecessor_receipt"] = advisory
    values = {
        "schema_version": "harness.prompt_revision.v1",
        **authority,
        "prompt_revision_number": 2,
        "source_prompt": command["source_prompt"],
        "predecessor_prompt_revision": _ref(
            "prompt-revision-1",
            "harness.prompt_revision.v1",
        ),
        "advisory_receipt": advisory,
        "failure_receipts": command["failure_receipts"],
        "observation_citations": (_observation(),),
        "predecessor_package": command["predecessor_package"],
        "patch": command["patch"],
        "revised_prompt": _text_ref(
            "revised-prompt",
            command["patch"]["replacement_text"],
        ),
        "fallback": command["fallback"],
        "next_compile_skill_ref": command["next_compile_skill_ref"],
        "external_agent_executed": False,
        "revision_sha256": "0" * 64,
    }
    fallback_model = (
        NoPromptFallback if values["fallback"]["kind"] == "none" else VideoFrameToImageFallback
    )
    unhashed = PromptRevision.model_construct(
        **{
            **values,
            "observation_citations": tuple(
                ObservationCitation.model_validate(item) for item in values["observation_citations"]
            ),
            "patch": ReplacePromptPatch.model_validate(values["patch"]),
            "fallback": fallback_model.model_validate(values["fallback"]),
        }
    )
    values["revision_sha256"] = _canonical_identity(
        "harness.prompt_revision.identity.v1",
        "revision",
        unhashed,
        "revision_sha256",
    )
    return values


def test_control_models_publish_exact_strict_schema_ids() -> None:
    assert ADVISORY_RECORD_COMMAND_SCHEMA_ID == "harness.advisory_record_command.v1"
    assert EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID == (
        "harness.external_agent_advisory_receipt.v1"
    )
    assert PROMPT_REVISION_COMMAND_SCHEMA_ID == "harness.prompt_revision_command.v1"
    assert PROMPT_REVISION_SCHEMA_ID == "harness.prompt_revision.v1"
    assert CONTROL_COMMIT_SNAPSHOT_SCHEMA_ID == "harness.control_commit_snapshot.v1"

    command = AdvisoryRecordCommand.model_validate(_advisory_command_values())
    assert command.model_json_schema()["$id"] == ADVISORY_RECORD_COMMAND_SCHEMA_ID
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AdvisoryRecordCommand.model_validate(
            {**_advisory_command_values(), "untrusted_authority": True}
        )
    with pytest.raises(ValidationError, match="valid integer"):
        AdvisoryRecordCommand.model_validate(
            {**_advisory_command_values(), "expected_workflow_revision": True}
        )
    for invalid_flag in (True, 0, "false"):
        with pytest.raises(ValidationError):
            AdvisoryRecordCommand.model_validate(
                {
                    **_advisory_command_values(),
                    "external_agent_executed": invalid_flag,
                }
            )
    with pytest.raises(ValidationError, match="frozen"):
        command.summary = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kind", "action"),
    [
        ("visual_assessment", "retry"),
        ("diagnosis", "revise_prompt"),
        ("repair_proposal", "repair_assets"),
    ],
)
def test_advisory_command_accepts_exact_three_kinds(kind: str, action: str) -> None:
    values = _advisory_command_values(kind)
    values["recommended_action"] = action
    command = AdvisoryRecordCommand.model_validate(values)
    assert command.advisory_kind == kind

    with pytest.raises(ValidationError):
        AdvisoryRecordCommand.model_validate({**values, "advisory_kind": "physical_validation"})


def test_advisory_command_binds_optional_prompt_revision() -> None:
    values = _advisory_command_values()
    values["source_prompt_revision"] = _ref(
        "prior-prompt-revision",
        PROMPT_REVISION_SCHEMA_ID,
    )
    AdvisoryRecordCommand.model_validate(values)

    with pytest.raises(ValidationError, match="source_prompt_revision"):
        AdvisoryRecordCommand.model_validate(
            {
                **values,
                "source_prompt_revision": _ref(
                    "prior-prompt-revision",
                    "harness.run_state.v1",
                ),
            }
        )


@pytest.mark.parametrize(
    ("kind", "missing_field"),
    [
        ("visual_assessment", "observation_citations"),
        ("diagnosis", "failure_receipts"),
        ("repair_proposal", "failure_receipts"),
    ],
)
def test_advisory_kind_requires_its_minimum_evidence(kind: str, missing_field: str) -> None:
    values = _advisory_command_values(kind)
    values[missing_field] = ()
    with pytest.raises(ValidationError, match=missing_field):
        AdvisoryRecordCommand.model_validate(values)

    if kind != "visual_assessment":
        values = _advisory_command_values(kind)
        values["observation_citations"] = ()
        values["diagnostic_refs"] = ()
        with pytest.raises(ValidationError, match="observation_citations.*diagnostic_refs"):
            AdvisoryRecordCommand.model_validate(values)


@pytest.mark.parametrize("field", ["failure_receipts", "diagnostic_refs"])
def test_advisory_command_references_are_typed_sorted_and_unique(field: str) -> None:
    values = _advisory_command_values()
    schema = (
        "harness.workflow_operation_receipt.v2"
        if field == "failure_receipts"
        else "robotwin.scene_validation.v1"
    )
    refs = _ordered(_ref(f"{field}-a", schema), _ref(f"{field}-b", schema))
    values[field] = refs
    AdvisoryRecordCommand.model_validate(values)

    for changed in (tuple(reversed(refs)), (refs[0], refs[0])):
        with pytest.raises(ValidationError, match=f"{field}.*sorted.*unique"):
            AdvisoryRecordCommand.model_validate({**values, field: changed})

    if field == "failure_receipts":
        wrong = _ref("failed-replay", "harness.run_state.v1")
        with pytest.raises(ValidationError, match="failure_receipts.*schema_version"):
            AdvisoryRecordCommand.model_validate({**values, "failure_receipts": (wrong,)})


def test_observation_citations_are_typed_sorted_unique_and_ttl_bound() -> None:
    values = _advisory_command_values()
    citations = _ordered_observations("fresh-observation-a", "fresh-observation-b")
    values["observation_citations"] = citations
    AdvisoryRecordCommand.model_validate(values)

    for changed in (tuple(reversed(citations)), (citations[0], citations[0])):
        with pytest.raises(ValidationError, match="observation_citations.*sorted.*unique"):
            AdvisoryRecordCommand.model_validate({**values, "observation_citations": changed})

    for changes, message in (
        ({"ttl_seconds": 0}, "greater than or equal to 1"),
        ({"ttl_seconds": 301}, "less than or equal to 300"),
        ({"valid_until": NOW - timedelta(seconds=1)}, "captured_at.*ttl_seconds"),
        (
            {"receipt": _ref("fresh-observation-a", "harness.fresh_observation.v1")},
            "observation_citations.*receipt.*schema_version",
        ),
    ):
        changed = {**citations[0], **changes}
        with pytest.raises(ValidationError, match=message):
            AdvisoryRecordCommand.model_validate({**values, "observation_citations": (changed,)})

    offset = timezone(timedelta(hours=1))
    non_utc = {
        **citations[0],
        "captured_at": (NOW - timedelta(seconds=30)).astimezone(offset),
        "valid_until": NOW.astimezone(offset),
    }
    with pytest.raises(ValidationError, match="captured_at.*UTC"):
        AdvisoryRecordCommand.model_validate({**values, "observation_citations": (non_utc,)})


def test_advisory_disposition_cannot_claim_execution_authority() -> None:
    values = _advisory_command_values()
    with pytest.raises(ValidationError, match="abstain.*none"):
        AdvisoryRecordCommand.model_validate({**values, "disposition": "abstain"})
    with pytest.raises(ValidationError, match="recommend.*non-none"):
        AdvisoryRecordCommand.model_validate({**values, "recommended_action": "none"})


def test_advisory_receipt_is_self_hashed_state_unchanged_and_fresh_at_closed_boundary() -> None:
    values = _advisory_receipt_values()
    receipt = ExternalAgentAdvisoryReceipt.model_validate(values)
    assert receipt.authority == "advisory_only"
    assert receipt.external_agent_executed is False
    assert receipt.grants_physical_validity is False
    assert receipt.grants_publishability is False
    assert receipt.base_state_sha256 == receipt.resulting_state_sha256
    assert receipt.base_state == receipt.resulting_state
    assert receipt.committed_at == receipt.observation_citations[0].valid_until
    assert external_agent_advisory_receipt_sha256(receipt) == values["receipt_sha256"]

    for changes, message in (
        ({"receipt_sha256": "f" * 64}, "receipt_sha256"),
        ({"resulting_state_sha256": "e" * 64}, "state.*unchanged"),
        (
            {"resulting_state": _ref("other-state", "harness.trusted_world_state.v1")},
            "state.*unchanged",
        ),
        ({"committed_revision": 9}, "advance exactly one"),
        ({"committed_turn_seq": 6}, "turn.*advance exactly one"),
        ({"external_agent_executed": True}, "False"),
        ({"grants_physical_validity": True}, "False"),
        ({"grants_publishability": True}, "False"),
    ):
        with pytest.raises(ValidationError, match=message):
            ExternalAgentAdvisoryReceipt.model_validate({**values, **changes})

    stale = _observation()
    stale["valid_until"] = NOW - timedelta(microseconds=1)
    with pytest.raises(ValidationError, match="observation.*expired"):
        ExternalAgentAdvisoryReceipt.model_validate({**values, "observation_citations": (stale,)})


def test_advisory_receipt_authority_refs_are_exact_and_cas_bound() -> None:
    values = _advisory_receipt_values()
    for field, replacement in (
        (
            "command",
            _ref("control-command", "harness.prompt_revision_command.v1"),
        ),
        (
            "base_state",
            _ref("state", "harness.run_state.v1"),
        ),
        (
            "predecessor_receipt",
            _ref("predecessor-receipt", "harness.run_state.v1"),
        ),
    ):
        with pytest.raises(ValidationError, match=field):
            ExternalAgentAdvisoryReceipt.model_validate({**values, field: replacement})


@pytest.mark.parametrize("fallback", ["none", "video_frame_to_image"])
def test_prompt_revision_command_has_discriminated_fallback(fallback: str) -> None:
    values = _prompt_command_values(fallback=fallback)
    command = PromptRevisionCommand.model_validate(values)
    assert command.fallback.kind == fallback
    assert command.model_json_schema()["$id"] == PROMPT_REVISION_COMMAND_SCHEMA_ID

    invalid = {**values, "fallback": {"kind": "none", "source_video": _ref("video", None)}}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PromptRevisionCommand.model_validate(invalid)
    with pytest.raises(ValidationError, match="False"):
        PromptRevisionCommand.model_validate({**values, "external_agent_executed": True})


def test_video_frame_fallback_routes_only_to_image_compile() -> None:
    values = _prompt_command_values(fallback="video_frame_to_image")
    with pytest.raises(ValidationError, match="image2env.compile@1.0.0"):
        PromptRevisionCommand.model_validate(
            {**values, "next_compile_skill_ref": "video2env.compile@1.0.0"}
        )

    for missing in (
        "source_video",
        "selected_frame",
        "frame_index",
        "frame_sequence_manifest",
    ):
        fallback = dict(values["fallback"])
        del fallback[missing]
        with pytest.raises(ValidationError, match=missing):
            PromptRevisionCommand.model_validate({**values, "fallback": fallback})

    attacks = (
        (
            "source_video",
            _ref("source-video", None, media_type="image/png"),
            "source_video.*video",
        ),
        (
            "selected_frame",
            _ref("selected-video-frame", None, media_type="video/mp4"),
            "selected_frame.*image",
        ),
        ("frame_index", -1, "frame_index"),
        (
            "frame_sequence_manifest",
            _ref("frame-sequence-manifest", "harness.run_state.v1"),
            "frame_sequence_manifest",
        ),
    )
    for field, replacement, message in attacks:
        fallback = {**values["fallback"], field: replacement}
        with pytest.raises(ValidationError, match=message):
            PromptRevisionCommand.model_validate({**values, "fallback": fallback})


def test_prompt_revision_command_authority_refs_are_exact() -> None:
    values = _prompt_command_values()
    for field, replacement in (
        (
            "advisory_receipt",
            _ref("advisory-receipt", "harness.workflow_operation_receipt.v2"),
        ),
        (
            "predecessor_package",
            _ref("failed-package", "harness.environment_package.v1"),
        ),
    ):
        with pytest.raises(ValidationError, match=field):
            PromptRevisionCommand.model_validate({**values, field: replacement})

    with pytest.raises(ValidationError, match="failure_receipts"):
        PromptRevisionCommand.model_validate({**values, "failure_receipts": ()})
    with pytest.raises(ValidationError, match="exact compile Skill"):
        PromptRevisionCommand.model_validate(
            {**values, "next_compile_skill_ref": "text2env.replay@1.0.0"}
        )


def test_prompt_patch_is_a_hash_bound_full_replacement() -> None:
    values = _prompt_command_values()
    patch = values["patch"]
    assert patch == {
        "kind": "full_replace",
        "base_text_sha256": values["source_prompt"].sha256,
        "replacement_text": "Place the can upright at the center of the plate.",
        "reason": "The failed video route requires an explicit frame-derived retry.",
    }
    PromptRevisionCommand.model_validate(values)

    for changes, message in (
        ({"kind": "replace"}, "full_replace"),
        ({"base_text_sha256": "e" * 64}, "base_text_sha256.*source_prompt"),
        ({"replacement_text": ""}, "replacement_text"),
        ({"reason": ""}, "reason"),
    ):
        with pytest.raises(ValidationError, match=message):
            PromptRevisionCommand.model_validate({**values, "patch": {**patch, **changes}})


def test_prompt_revision_is_self_hashed_state_unchanged_and_lineage_bound() -> None:
    values = _prompt_revision_values()
    revision = PromptRevision.model_validate(values)
    assert revision.predecessor_receipt == revision.advisory_receipt
    assert revision.base_state_sha256 == revision.resulting_state_sha256
    assert revision.base_state == revision.resulting_state
    assert revision.external_agent_executed is False
    assert prompt_revision_sha256(revision) == values["revision_sha256"]
    assert revision.model_json_schema()["$id"] == PROMPT_REVISION_SCHEMA_ID

    for changes, message in (
        ({"revision_sha256": "f" * 64}, "revision_sha256"),
        ({"resulting_state_sha256": "e" * 64}, "state.*unchanged"),
        (
            {
                "predecessor_receipt": _ref(
                    "other-advisory",
                    EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
                )
            },
            "predecessor_receipt.*advisory_receipt",
        ),
        ({"committed_revision": 9}, "advance exactly one"),
        ({"external_agent_executed": True}, "False"),
    ):
        with pytest.raises(ValidationError, match=message):
            PromptRevision.model_validate({**values, **changes})

    with pytest.raises(ValidationError, match="base_text_sha256.*source_prompt"):
        PromptRevision.model_validate(
            {
                **values,
                "patch": {**values["patch"], "base_text_sha256": "e" * 64},
            }
        )
    with pytest.raises(ValidationError, match="exact compile Skill"):
        PromptRevision.model_validate({**values, "next_compile_skill_ref": "text2env.replay@1.0.0"})

    video_values = _prompt_revision_values(fallback="video_frame_to_image")
    with pytest.raises(ValidationError, match="image2env.compile@1.0.0"):
        PromptRevision.model_validate(
            {**video_values, "next_compile_skill_ref": "video2env.compile@1.0.0"}
        )


def test_prompt_revision_number_and_observation_ttl_are_closed() -> None:
    values = _prompt_revision_values()
    first = {
        **values,
        "prompt_revision_number": 1,
        "predecessor_prompt_revision": None,
    }
    unhashed = PromptRevision.model_construct(
        **{
            **first,
            "observation_citations": tuple(
                ObservationCitation.model_validate(item) for item in first["observation_citations"]
            ),
            "patch": ReplacePromptPatch.model_validate(first["patch"]),
            "fallback": NoPromptFallback.model_validate(first["fallback"]),
        }
    )
    first["revision_sha256"] = _canonical_identity(
        "harness.prompt_revision.identity.v1",
        "revision",
        unhashed,
        "revision_sha256",
    )
    PromptRevision.model_validate(first)

    with pytest.raises(ValidationError, match="predecessor_prompt_revision"):
        PromptRevision.model_validate({**values, "prompt_revision_number": 1})
    with pytest.raises(ValidationError, match="predecessor_prompt_revision"):
        PromptRevision.model_validate(
            {**values, "prompt_revision_number": 2, "predecessor_prompt_revision": None}
        )
    with pytest.raises(ValidationError, match="failure_receipts"):
        PromptRevision.model_validate({**values, "failure_receipts": ()})
    stale = _observation()
    stale["captured_at"] -= timedelta(microseconds=1)
    stale["valid_until"] -= timedelta(microseconds=1)
    with pytest.raises(ValidationError, match="observation.*expired"):
        PromptRevision.model_validate({**values, "observation_citations": (stale,)})


def test_control_documents_are_standalone_draft_2020_12_and_accept_examples() -> None:
    documents = schema_documents()
    examples = {
        ADVISORY_RECORD_COMMAND_SCHEMA_ID: AdvisoryRecordCommand.model_validate(
            _advisory_command_values()
        ),
        EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID: ExternalAgentAdvisoryReceipt.model_validate(
            _advisory_receipt_values()
        ),
        PROMPT_REVISION_COMMAND_SCHEMA_ID: PromptRevisionCommand.model_validate(
            _prompt_command_values(fallback="video_frame_to_image")
        ),
        PROMPT_REVISION_SCHEMA_ID: PromptRevision.model_validate(_prompt_revision_values()),
        CONTROL_COMMIT_SNAPSHOT_SCHEMA_ID: ControlCommitSnapshot.model_validate(
            {
                "schema_version": CONTROL_COMMIT_SNAPSHOT_SCHEMA_ID,
                "control_kind": "advisory",
                "idempotency_key": "control-command-001",
                **_control_authority(command_schema=ADVISORY_RECORD_COMMAND_SCHEMA_ID),
                "payload": _ref(
                    "control-receipt",
                    EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
                ),
                "receipt": _ref(
                    "control-receipt",
                    EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
                ),
                "receipt_head": _ref(
                    "control-receipt",
                    EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
                ),
            }
        ),
    }
    for schema_id, example in examples.items():
        document = documents[schema_id]
        Draft202012Validator.check_schema(document)
        Draft202012Validator(document).validate(example.model_dump(mode="json"))


def test_prompt_revision_evidence_refs_are_sorted_unique() -> None:
    values = _prompt_revision_values()
    failures = _ordered(
        _ref("failed-compile", "harness.workflow_operation_receipt.v2"),
        _ref("failed-replay", "harness.workflow_operation_receipt.v2"),
    )
    values["failure_receipts"] = failures
    unhashed = PromptRevision.model_construct(
        **{
            **values,
            "observation_citations": tuple(
                ObservationCitation.model_validate(item) for item in values["observation_citations"]
            ),
            "patch": ReplacePromptPatch.model_validate(values["patch"]),
            "fallback": NoPromptFallback.model_validate(values["fallback"]),
        }
    )
    values["revision_sha256"] = _canonical_identity(
        "harness.prompt_revision.identity.v1",
        "revision",
        unhashed,
        "revision_sha256",
    )
    PromptRevision.model_validate(values)

    for changed in (tuple(reversed(failures)), (failures[0], failures[0])):
        with pytest.raises(ValidationError, match="failure_receipts.*sorted.*unique"):
            PromptRevision.model_validate({**values, "failure_receipts": changed})


@pytest.mark.parametrize(
    ("control_kind", "command_schema", "receipt_schema"),
    [
        (
            "advisory",
            "harness.advisory_record_command.v1",
            "harness.external_agent_advisory_receipt.v1",
        ),
        (
            "prompt_revision",
            "harness.prompt_revision_command.v1",
            "harness.prompt_revision.v1",
        ),
    ],
)
def test_control_commit_snapshot_advances_history_but_not_state(
    control_kind: str,
    command_schema: str,
    receipt_schema: str,
) -> None:
    authority = _control_authority(command_schema=command_schema)
    receipt = _ref("control-receipt", receipt_schema)
    values = {
        "schema_version": "harness.control_commit_snapshot.v1",
        "control_kind": control_kind,
        "idempotency_key": "control-command-001",
        **authority,
        "payload": receipt,
        "receipt": receipt,
        "receipt_head": receipt,
    }
    snapshot = ControlCommitSnapshot.model_validate(values)
    assert snapshot.committed_revision == snapshot.base_revision + 1
    assert snapshot.committed_turn_seq == snapshot.base_turn_seq + 1
    assert snapshot.resulting_state_sha256 == snapshot.base_state_sha256
    assert snapshot.resulting_state == snapshot.base_state
    assert snapshot.payload == snapshot.receipt
    assert snapshot.receipt_head == snapshot.receipt
    assert snapshot.model_json_schema()["$id"] == CONTROL_COMMIT_SNAPSHOT_SCHEMA_ID

    for changes, message in (
        ({"resulting_state_sha256": "e" * 64}, "state.*unchanged"),
        ({"committed_revision": 9}, "advance exactly one"),
        ({"committed_turn_seq": 6}, "turn.*advance exactly one"),
        (
            {"payload": _ref("other-payload", receipt_schema)},
            "payload.*receipt",
        ),
        (
            {"receipt_head": _ref("other-head", receipt_schema)},
            "receipt_head.*receipt",
        ),
        ({"receipt": _ref("wrong-receipt", "harness.run_state.v1")}, "receipt"),
    ):
        with pytest.raises(ValidationError, match=message):
            ControlCommitSnapshot.model_validate({**values, **changes})
