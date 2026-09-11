"""Assigned backend protocol fixtures do not certify real model execution."""

import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from self_improving.harness.codex_backend import CodexAssignment, AssignedMCPInput
from tests.self_improving.harness.test_golden_control_v2_runtime import native_case


def test_assigned_relay_rejects_start_and_foreign_workflow_before_forwarding(tmp_path):
    case = native_case(tmp_path)
    assignment = CodexAssignment(snapshot=case.head, image_path=tmp_path / "image.png",
        objective="mouse on table", phase="normal", allowed_actions=("image2env_preview_status_v1",),
        call_budget=2, deadline=datetime.now(UTC)+timedelta(minutes=1))
    for name, workflow in [("image2env_preview_start_v1", str(case.head.workflow_run_id)),
                           ("image2env_preview_status_v1", "foreign")]:
        message = {"jsonrpc":"2.0", "id":1, "method":"tools/call",
                   "params":{"name":name,"arguments":{"workflow_id":workflow}}}
        stream = AssignedMCPInput(io.BytesIO(json.dumps(message).encode()+b"\n"), assignment)
        with pytest.raises(ValueError):
            stream.readline()
