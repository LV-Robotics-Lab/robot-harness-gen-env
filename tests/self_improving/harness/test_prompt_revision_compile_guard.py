"""Public-seam RED for consuming prompt-revision lineage before compile."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import anyio
from jsonschema import Draft202012Validator
from mcp import Client
from pydantic import BaseModel

from self_improving.harness.golden_execution import (
    _create_golden_execution_binding,
    create_golden_execution_catalog,
)
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.mcp_adapter import create_harness_mcp_adapter
from self_improving.harness.mcp_server import create_harness_mcp_server
from self_improving.harness.schema_catalog import schema_model
from self_improving.harness.schemas.common import Invocation, RunState, SkillDescriptor
from self_improving.harness.schemas.registry_snapshot import RegistrySnapshot
from tests.self_improving.harness import test_golden_control_submit as control_base
from tests.self_improving.harness import test_golden_observation_submit as observe_base
from tests.self_improving.harness import test_mcp_control_tools as mcp_base
from tests.self_improving.harness import test_x2env_contracts as x2env_base

_COMPILE_SKILL = "image2env.compile@1.0.0"
_COMPILE_TOOL = "image2env_compile_v1_0_0"
_POST_REVISION_COMMAND = UUID("73000000-0000-4000-8000-000000000010")
_POST_REVISION_CHILD = UUID("72000000-0000-4000-8000-000000000010")


@dataclass(frozen=True)
class _TrackingCompileApplication:
    artifact_root: Path
    authority_path: Path
    descriptor: SkillDescriptor
    policy: observe_base._FixturePolicy
    calls: list[UUID]
    skill_ref: str = _COMPILE_SKILL

    @property
    def skills(self) -> tuple[SkillDescriptor, ...]:
        return (self.descriptor,)

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        self.calls.append(run_id)
        raise AssertionError("an unconsumed prompt revision reached the compile application")

    def invocation(self, run_id: UUID) -> Invocation | None:
        raise AssertionError("a rejected compile must not inspect a child invocation")

    def run_state(self, run_id: UUID) -> RunState | None:
        raise AssertionError("a rejected compile must not inspect a child run state")


def test_prompt_revision_head_rejects_old_compile_input_without_side_effects(
    tmp_path: Path,
) -> None:
    case = control_base._case(tmp_path)
    registry = mcp_base._read_model(
        case.store,
        case.head.registry_snapshot,
        RegistrySnapshot,
    )
    assert type(registry) is RegistrySnapshot
    entry = next(item for item in registry.qualified_skills if item.skill_ref == _COMPILE_SKILL)
    descriptor = mcp_base._read_model(case.store, entry.descriptor_ref, SkillDescriptor)
    assert type(descriptor) is SkillDescriptor
    policy = observe_base._FixturePolicy(
        skill_ref=_COMPILE_SKILL,
        input_model=schema_model(descriptor.input_schema),
    )
    application_calls: list[UUID] = []
    application = _TrackingCompileApplication(
        artifact_root=case.store.root,
        authority_path=case.child_store.path,
        descriptor=descriptor,
        policy=policy,
        calls=application_calls,
    )
    child_allocations: list[UUID] = []

    def allocate_child() -> UUID:
        child_allocations.append(_POST_REVISION_CHILD)
        return _POST_REVISION_CHILD

    harness = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=case.workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=case.policy,
                    application=case.application,
                    child_authority=case.child_store,
                ),
                _create_golden_execution_binding(
                    policy=policy,
                    application=application,
                    child_authority=case.child_store,
                ),
            )
        ),
        clock=case.clock,
        child_run_id_factory=allocate_child,
    )
    adapter = create_harness_mcp_adapter(
        harness=harness,
        principal_id=case.head.principal_id,
        registry_snapshot=case.head.registry_snapshot,
    )
    server = create_harness_mcp_server(adapter)

    async def exercise() -> None:
        async with Client(server, raise_exceptions=True, cache=None) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            source_deliveries = tuple(
                [
                    await client.read_resource(mcp_base._artifact_uri(case, artifact))
                    for artifact in (
                        case.source_prompt,
                        case.failure_receipt,
                        case.observation,
                        case.diagnostic,
                    )
                ]
            )
            access_receipts = tuple(
                sorted(str(delivery.contents[1].uri) for delivery in source_deliveries)
            )
            advisory = await client.call_tool(
                mcp_base.ADVISORY_TOOL,
                mcp_base._advisory_arguments(
                    case,
                    resource_access_receipt_uris=access_receipts,
                ),
            )
            assert advisory.is_error is False
            assert advisory.structured_content is not None
            prompt = await client.call_tool(
                mcp_base.PROMPT_TOOL,
                mcp_base._prompt_arguments(case, advisory.structured_content),
            )
            assert prompt.is_error is False
            assert prompt.structured_content is not None
            workflow_uri = f"harness://workflows/{case.head.workflow_run_id}"
            before_resource = await client.read_resource(workflow_uri)
            before = json.loads(before_resource.contents[0].text)
            database_before = mcp_base._database_rows(case.workflow_store.path)
            cas_before = mcp_base._cas_manifest(case.store.root)
            old_parameters = x2env_base.compile_input("image2env")
            old_parameters["repair_evidence_refs"] = []
            arguments = {
                "schema_version": "harness.run_command_request.v1",
                "command_id": str(_POST_REVISION_COMMAND),
                "workflow_run_id": str(case.head.workflow_run_id),
                "idempotency_key": "old-input-after-prompt-revision",
                "expected_workflow_revision": before["revision"],
                "expected_state_sha256": before["state_sha256"],
                "expected_receipt_head_sha256": before["receipt_head"]["sha256"],
                "parameters": old_parameters,
            }
            Draft202012Validator(tools[_COMPILE_TOOL].input_schema).validate(arguments)

            rejected = await client.call_tool(_COMPILE_TOOL, arguments)

            assert rejected.is_error is True
            assert rejected.structured_content is not None
            assert rejected.structured_content["schema_version"] == "harness.mcp_error.v1"
            assert rejected.structured_content["code"] == "HARN_HISTORY_MISMATCH"
            after_resource = await client.read_resource(workflow_uri)
            after = json.loads(after_resource.contents[0].text)
            assert before["revision"] == before["turn_seq"] == 4
            assert after == before
            assert after["receipt_head"] == prompt.structured_content["receipt"]
            assert after["state_ref"] == before["state_ref"]
            assert after["state_sha256"] == before["state_sha256"]
            assert after["child_runs"] == before["child_runs"]
            assert mcp_base._database_rows(case.workflow_store.path) == database_before
            assert mcp_base._cas_manifest(case.store.root) == cas_before
            assert application_calls == []
            assert child_allocations == []

    anyio.run(exercise)
