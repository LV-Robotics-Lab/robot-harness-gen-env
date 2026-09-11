"""Exact native control projection uses real Harness authority and official MCP."""

from self_improving.harness.mcp_control_v2 import create_native_harness_mcp_adapter
from tests.self_improving.harness.test_golden_control_v2_runtime import native_case


def test_native_adapter_projects_v2_controls_and_never_v1(tmp_path):
    case = native_case(tmp_path)
    adapter = create_native_harness_mcp_adapter(harness=case.harness,principal_id=case.head.principal_id,registry_snapshot=case.head.registry_snapshot)
    names = {tool.name for tool in adapter.list_tools(case.head.registry_snapshot)}
    assert {"harness_advisory_record_v2", "harness_prompt_revision_record_v2"} <= names
    assert not {"harness_advisory_record_v1", "harness_prompt_revision_record_v1", "skill.invoke"} & names
