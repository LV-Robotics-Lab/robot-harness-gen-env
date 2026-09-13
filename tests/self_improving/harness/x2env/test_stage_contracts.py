"""Hash-bound stage handoffs cannot grant success with missing or stale evidence."""

import pytest
from pydantic import ValidationError

from self_improving.harness.x2env.contracts import ArtifactRef, ToolResult


def test_artifact_ref_is_content_identity_not_workspace_location():
    ref = ArtifactRef(sha256="a" * 64, size_bytes=42, media_type="application/json")
    assert ref.sha256 == "a" * 64
    with pytest.raises(ValidationError):
        ArtifactRef(
            sha256="a" * 64,
            size_bytes=42,
            media_type="application/json",
            path="/original/workspace/scene.json",
        )


def test_failed_tool_preserves_partial_artifacts_and_structured_reason():
    result = ToolResult(
        operation_id="op1",
        status="failed",
        outputs=(ArtifactRef(sha256="b" * 64, size_bytes=13, media_type="text/plain"),),
        error_code="missing_physical_metadata",
        message="collision metadata absent",
    )
    assert len(result.outputs) == 1
    assert result.error_code == "missing_physical_metadata"
    with pytest.raises(ValidationError):
        ToolResult(operation_id="op2", status="succeeded", error_code="provider_failure")
    with pytest.raises(ValidationError):
        ToolResult(operation_id="op3", status="failed")
