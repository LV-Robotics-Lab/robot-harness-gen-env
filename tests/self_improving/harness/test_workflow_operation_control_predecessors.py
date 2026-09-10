"""Public-model contract for Skill operations after workflow controls."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

import self_improving.harness as public_harness
from self_improving.harness import schemas as public_schemas
from self_improving.harness.schemas.common import ArtifactRef

NOW = datetime(2026, 9, 11, 8, tzinfo=timezone.utc)
WORKFLOW_ID = UUID("10000000-0000-4000-8000-000000000001")
OPERATION_ID = UUID("20000000-0000-4000-8000-000000000002")
STATE_SHA256 = "1" * 64


def _ref(
    label: str,
    schema_version: str | None,
    *,
    media_type: str = "application/json",
    corrupt_uri: bool = False,
) -> ArtifactRef:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    uri_digest = "f" * 64 if corrupt_uri else digest
    return ArtifactRef(
        name=f"{label}.json",
        uri=f"artifact://sha256/{uri_digest}",
        media_type=media_type,
        sha256=digest,
        bytes=len(label),
        schema_version=schema_version,
    )


def _receipt_values(predecessor: ArtifactRef) -> dict[str, Any]:
    return {
        "schema_version": "harness.workflow_operation_receipt.v2",
        "workflow_run_id": WORKFLOW_ID,
        "operation_id": OPERATION_ID,
        "principal_id": "tests/control-predecessor",
        "command": _ref("run-command", "harness.run_command_request.v1"),
        "predecessor_receipt": predecessor,
        "tool_receipt": _ref("tool-receipt", "harness.trusted_tool_receipt.v2"),
        "tool_result": _ref("tool-result", "harness.tool_result.v2"),
        "state_delta": None,
        "base_revision": 4,
        "committed_revision": 5,
        "base_state_sha256": STATE_SHA256,
        "resulting_state_sha256": STATE_SHA256,
        "resulting_state": _ref("trusted-state", "harness.trusted_world_state.v1"),
        "status": "blocked",
        "committed_at": NOW,
    }


@pytest.mark.parametrize(
    "schema_version",
    [
        "harness.external_agent_advisory_receipt.v1",
        "harness.prompt_revision.v1",
    ],
)
def test_public_workflow_operation_accepts_exact_control_receipt_predecessors(
    schema_version: str,
) -> None:
    assert public_harness.WorkflowOperationReceiptV2 is (
        public_schemas.WorkflowOperationReceiptV2
    )
    predecessor = _ref("control-receipt", schema_version)

    receipt = public_harness.WorkflowOperationReceiptV2.model_validate(
        _receipt_values(predecessor)
    )

    assert receipt.predecessor_receipt == predecessor
    assert receipt.model_json_schema()["$id"] == "harness.workflow_operation_receipt.v2"


@pytest.mark.parametrize(
    ("predecessor", "message"),
    [
        (
            _ref("control-snapshot", "harness.control_commit_snapshot.v1"),
            "unsupported schema_version",
        ),
        (
            _ref("advisory-command", "harness.advisory_record_command.v1"),
            "unsupported schema_version",
        ),
        (
            _ref("untyped-receipt", None),
            "unsupported schema_version",
        ),
        (
            _ref(
                "advisory-receipt",
                "harness.external_agent_advisory_receipt.v1",
                media_type="text/plain",
            ),
            "media_type=application/json",
        ),
        (
            _ref(
                "prompt-revision",
                "harness.prompt_revision.v1",
                corrupt_uri=True,
            ),
            "content-addressed artifact URI",
        ),
    ],
)
def test_public_workflow_operation_rejects_non_receipt_or_unbound_predecessors(
    predecessor: ArtifactRef,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        public_harness.WorkflowOperationReceiptV2.model_validate(
            _receipt_values(predecessor)
        )
