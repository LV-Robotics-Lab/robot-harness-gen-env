"""Official MCP 2.2 RED for exact Golden Workflow control tools."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import UUID

import anyio
import pytest
from jsonschema import Draft202012Validator, ValidationError

pytest.importorskip("mcp")
assert version("mcp") == "2.2.0"

from mcp import Client, MCPError, StdioServerParameters, types  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from self_improving.harness.artifacts import LocalArtifactStore  # noqa: E402
from self_improving.harness.event_journal import SQLiteEventJournal  # noqa: E402
from self_improving.harness.golden_execution import (  # noqa: E402
    _create_golden_execution_binding,
    create_golden_execution_catalog,
)
from self_improving.harness.golden_run import GoldenRunHarness  # noqa: E402
from self_improving.harness.golden_store import SQLiteGoldenRunStore  # noqa: E402
from self_improving.harness.mcp_adapter import create_harness_mcp_adapter  # noqa: E402
from self_improving.harness.mcp_server import create_harness_mcp_server  # noqa: E402
from self_improving.harness.mcp_stdio import _serve_stdio  # noqa: E402
from self_improving.harness.registry import (  # noqa: E402
    HandlerResult,
    SkillRegistry,
    StaticDependencyResolver,
)
from self_improving.harness.run_store import SQLiteRunStore  # noqa: E402
from self_improving.harness.schema_catalog import schema_model  # noqa: E402
from self_improving.harness.schemas.common import (  # noqa: E402
    ArtifactRef,
    Invocation,
    RunState,
    SkillDescriptor,
)
from self_improving.harness.schemas.registry_snapshot import RegistrySnapshot  # noqa: E402
from tests.self_improving.harness import test_golden_control_submit as control_base  # noqa: E402
from tests.self_improving.harness import (  # noqa: E402
    test_golden_observation_submit as observe_base,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ADVISORY_TOOL = "harness_advisory_record_v1"
PROMPT_TOOL = "harness_prompt_revision_record_v1"
CONTROL_TOOLS = (ADVISORY_TOOL, PROMPT_TOOL)

_COMMON_INPUT_FIELDS = {
    "schema_version",
    "command_id",
    "workflow_run_id",
    "idempotency_key",
    "expected_workflow_revision",
    "expected_state_sha256",
    "expected_receipt_head_sha256",
}
_ADVISORY_INPUT_FIELDS = _COMMON_INPUT_FIELDS | {
    "advisory_kind",
    "disposition",
    "summary",
    "recommended_action",
    "source_prompt_uri",
    "source_prompt_revision_uri",
    "failure_receipt_uris",
    "observation_citations",
    "diagnostic_uris",
    "resource_access_receipt_uris",
}
_PROMPT_INPUT_FIELDS = _COMMON_INPUT_FIELDS | {
    "source_prompt_uri",
    "advisory_receipt_uri",
    "failure_receipt_uris",
    "predecessor_package_uri",
    "patch",
    "fallback",
    "next_compile_skill_ref",
}


@dataclass(frozen=True)
class _CatalogOnlyApplication:
    """Live descriptor carrier for the qualified compile route cited by the control."""

    artifact_root: Path
    authority_path: Path
    descriptor: SkillDescriptor
    policy: observe_base._FixturePolicy
    skill_ref: str

    @property
    def skills(self) -> tuple[SkillDescriptor, ...]:
        return (self.descriptor,)

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        raise AssertionError("MCP control tests must not invoke the cited compile Skill")

    def invocation(self, run_id: UUID) -> Invocation | None:
        raise AssertionError("MCP control tests must not inspect an uninvoked compile Skill")

    def run_state(self, run_id: UUID) -> RunState | None:
        raise AssertionError("MCP control tests must not inspect an uninvoked compile Skill")


def _read_model(store: LocalArtifactStore, ref: ArtifactRef, model: type[BaseModel]) -> BaseModel:
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _adapter(
    root: Path,
    *,
    registry_ref: ArtifactRef,
    observation_ref: ArtifactRef,
    diagnostic_ref: ArtifactRef,
):
    store = LocalArtifactStore(root / "cas")
    child_store = SQLiteRunStore(root / "children.sqlite3")
    snapshot = _read_model(store, registry_ref, RegistrySnapshot)
    assert type(snapshot) is RegistrySnapshot
    entries = {entry.skill_ref: entry for entry in snapshot.qualified_skills}
    evidence_entry = entries[control_base._SKILL_REF]
    evidence_descriptor = _read_model(store, evidence_entry.descriptor_ref, SkillDescriptor)
    assert type(evidence_descriptor) is SkillDescriptor
    evidence_policy = control_base._EvidencePolicy(
        store=store,
        observation=observation_ref,
        diagnostic=diagnostic_ref,
    )
    evidence_registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=StaticDependencyResolver({control_base._SKILL_REF: ()}),
        event_sink=SQLiteEventJournal(child_store.path),
        run_store=child_store,
        run_id_factory=lambda: pytest.fail("control tool must not reserve a Skill child"),
        clock=control_base._Clock(),
    )

    def never_execute(_value: ArtifactRef, _context: object) -> HandlerResult:
        raise AssertionError("control tool must not invoke a qualified Skill")

    evidence_registry.register(evidence_descriptor, never_execute)
    evidence_application = control_base._EvidenceApplication(
        artifact_root=store.root,
        authority_path=child_store.path,
        registry=evidence_registry,
        descriptor=evidence_descriptor,
        policy=evidence_policy,
    )

    compile_ref = "image2env.compile@1.0.0"
    compile_entry = entries[compile_ref]
    compile_descriptor = _read_model(store, compile_entry.descriptor_ref, SkillDescriptor)
    assert type(compile_descriptor) is SkillDescriptor
    compile_policy = observe_base._FixturePolicy(
        skill_ref=compile_ref,
        input_model=schema_model(compile_descriptor.input_schema),
    )
    compile_application = _CatalogOnlyApplication(
        artifact_root=store.root,
        authority_path=child_store.path,
        descriptor=compile_descriptor,
        policy=compile_policy,
        skill_ref=compile_ref,
    )
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(root / "golden.sqlite3"),
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=evidence_policy,
                    application=evidence_application,
                    child_authority=child_store,
                ),
                _create_golden_execution_binding(
                    policy=compile_policy,
                    application=compile_application,
                    child_authority=child_store,
                ),
            )
        ),
        clock=control_base._Clock(),
        child_run_id_factory=lambda: pytest.fail("control tool must not reserve a Skill child"),
    )
    return create_harness_mcp_adapter(
        harness=harness,
        principal_id="tests/golden-control",
        registry_snapshot=registry_ref,
    )


def _artifact_uri(case: control_base._Case, ref: ArtifactRef) -> str:
    return f"harness://workflows/{case.head.workflow_run_id}/artifacts/{ref.sha256}"


def _advisory_arguments(
    case: control_base._Case,
    *,
    resource_access_receipt_uri: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "harness.mcp_advisory_record_request.v1",
        "command_id": str(control_base._ADVISORY_ID),
        "workflow_run_id": str(case.head.workflow_run_id),
        "idempotency_key": "diagnosis-001",
        "expected_workflow_revision": 2,
        "expected_state_sha256": case.head.state_sha256,
        "expected_receipt_head_sha256": case.head.receipt_head.sha256,
        "advisory_kind": "diagnosis",
        "disposition": "recommend",
        "summary": "The video route failed; reuse the cited frame through image2env.",
        "recommended_action": "fallback_image",
        "source_prompt_uri": _artifact_uri(case, case.source_prompt),
        "source_prompt_revision_uri": None,
        "failure_receipt_uris": [_artifact_uri(case, case.failure_receipt)],
        "observation_citations": [
            {
                "receipt_uri": _artifact_uri(case, case.observation),
                "captured_at": "2026-09-11T02:00:00Z",
                "valid_until": "2026-09-11T02:01:00Z",
                "ttl_seconds": 60,
            }
        ],
        "diagnostic_uris": [_artifact_uri(case, case.diagnostic)],
        "resource_access_receipt_uris": (
            [] if resource_access_receipt_uri is None else [resource_access_receipt_uri]
        ),
    }


def _prompt_arguments(
    case: control_base._Case,
    advisory: dict[str, Any],
) -> dict[str, object]:
    receipt = ArtifactRef.model_validate(advisory["receipt"])
    revised = "Build the scene from cited frame 7 and preserve the failed package lineage."
    return {
        "schema_version": "harness.mcp_prompt_revision_record_request.v1",
        "command_id": str(control_base._PROMPT_REVISION_ID),
        "workflow_run_id": str(case.head.workflow_run_id),
        "idempotency_key": "prompt-revision-001",
        "expected_workflow_revision": 3,
        "expected_state_sha256": case.head.state_sha256,
        "expected_receipt_head_sha256": receipt.sha256,
        "source_prompt_uri": _artifact_uri(case, case.source_prompt),
        "advisory_receipt_uri": _artifact_uri(case, receipt),
        "failure_receipt_uris": [_artifact_uri(case, case.failure_receipt)],
        "predecessor_package_uri": _artifact_uri(case, case.predecessor_package),
        "patch": {
            "kind": "full_replace",
            "base_text_sha256": case.source_prompt.sha256,
            "replacement_text": revised,
            "reason": "The failed route requires an explicit frame-derived retry.",
        },
        "fallback": {
            "kind": "video_frame_to_image",
            "source_video_uri": _artifact_uri(case, case.video),
            "selected_frame_uri": _artifact_uri(case, case.frame),
            "frame_index": 7,
            "frame_sequence_manifest_uri": _artifact_uri(case, case.frame_manifest),
        },
        "next_compile_skill_ref": "image2env.compile@1.0.0",
    }


def _assert_no_artifact_ref_shape(value: object) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            assert not {"uri", "sha256", "bytes", "media_type"}.issubset(properties)
        for child in value.values():
            _assert_no_artifact_ref_shape(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_artifact_ref_shape(child)


def _assert_error(result: types.CallToolResult, code: str) -> None:
    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["schema_version"] == "harness.mcp_error.v1"
    assert result.structured_content["code"] == code
    assert isinstance(result.structured_content["message"], str)
    assert result.structured_content["message"]


def test_official_client_exposes_exact_control_tools_and_commits_only_through_harness(
    tmp_path: Path,
) -> None:
    case = control_base._case(tmp_path)
    adapter = _adapter(
        tmp_path,
        registry_ref=case.head.registry_snapshot,
        observation_ref=case.observation,
        diagnostic_ref=case.diagnostic,
    )
    server = create_harness_mcp_server(adapter)

    async def exercise() -> None:
        async with Client(server, raise_exceptions=True, cache=None) as client:
            assert client.protocol_version == "2026-07-28"
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert set(CONTROL_TOOLS) <= set(tools)
            assert "skill.invoke" not in tools
            assert len(tools) == len(listed.tools)

            advisory_tool = tools[ADVISORY_TOOL]
            prompt_tool = tools[PROMPT_TOOL]
            assert set(advisory_tool.input_schema["properties"]) == _ADVISORY_INPUT_FIELDS
            assert set(prompt_tool.input_schema["properties"]) == _PROMPT_INPUT_FIELDS
            assert advisory_tool.input_schema["additionalProperties"] is False
            assert prompt_tool.input_schema["additionalProperties"] is False
            for tool in (advisory_tool, prompt_tool):
                Draft202012Validator.check_schema(tool.input_schema)
                Draft202012Validator.check_schema(tool.output_schema)
                _assert_no_artifact_ref_shape(tool.input_schema)
                assert "principal_id" not in tool.input_schema["properties"]
                assert "registry_snapshot" not in tool.input_schema["properties"]
                assert "external_agent_executed" not in tool.input_schema["properties"]

            source_delivery = await client.read_resource(_artifact_uri(case, case.source_prompt))
            assert len(source_delivery.contents) == 2
            resource_access_receipt_uri = str(source_delivery.contents[1].uri)
            advisory_arguments = _advisory_arguments(
                case,
                resource_access_receipt_uri=resource_access_receipt_uri,
            )
            Draft202012Validator(advisory_tool.input_schema).validate(advisory_arguments)
            with pytest.raises(ValidationError):
                Draft202012Validator(advisory_tool.input_schema).validate(
                    {**advisory_arguments, "principal_id": "caller/forged"}
                )
            with pytest.raises(ValidationError):
                Draft202012Validator(advisory_tool.input_schema).validate(
                    {
                        **advisory_arguments,
                        "source_prompt_uri": case.source_prompt.model_dump(mode="json"),
                    }
                )

            advisory = await client.call_tool(ADVISORY_TOOL, advisory_arguments)
            advisory_retry = await client.call_tool(ADVISORY_TOOL, advisory_arguments)
            assert advisory_retry == advisory
            assert advisory.is_error is False
            assert advisory.structured_content is not None
            assert advisory.structured_content["schema_version"] == (
                "harness.control_commit_snapshot.v1"
            )
            assert advisory.structured_content["control_kind"] == "advisory"
            assert advisory.structured_content["committed_revision"] == 3
            assert [content.type for content in advisory.content] == ["text", "resource_link"]
            assert isinstance(advisory.content[1], types.ResourceLink)
            workflow_uri = f"harness://workflows/{case.head.workflow_run_id}"
            assert str(advisory.content[1].uri) == workflow_uri
            advisory_resource = await client.read_resource(workflow_uri)
            assert json.loads(advisory_resource.contents[0].text)["revision"] == 3
            Draft202012Validator(advisory_tool.output_schema).validate(advisory.structured_content)

            for forged in (
                {**advisory_arguments, "principal_id": "caller/forged"},
                {
                    **advisory_arguments,
                    "registry_snapshot": case.head.registry_snapshot.model_dump(mode="json"),
                },
                {
                    **advisory_arguments,
                    "source_prompt_uri": case.source_prompt.model_dump(mode="json"),
                },
            ):
                _assert_error(
                    await client.call_tool(ADVISORY_TOOL, forged),
                    "HARN_INPUT_SCHEMA_INVALID",
                )

            changed = {**advisory_arguments, "summary": "Caller changed an immutable advisory."}
            _assert_error(
                await client.call_tool(ADVISORY_TOOL, changed),
                "HARN_IDEMPOTENCY_CONFLICT",
            )
            foreign_uri = (
                "harness://workflows/"
                "71000000-0000-4000-8000-000000000099/artifacts/"
                f"{case.source_prompt.sha256}"
            )
            _assert_error(
                await client.call_tool(
                    ADVISORY_TOOL,
                    {
                        **advisory_arguments,
                        "command_id": "73000000-0000-4000-8000-000000000004",
                        "idempotency_key": "diagnosis-foreign-uri-004",
                        "source_prompt_uri": foreign_uri,
                    },
                ),
                "HARN_INPUT_SCHEMA_INVALID",
            )
            _assert_error(
                await client.call_tool(
                    ADVISORY_TOOL,
                    {
                        **advisory_arguments,
                        "command_id": "73000000-0000-4000-8000-000000000005",
                        "idempotency_key": "diagnosis-unbound-uri-005",
                        "source_prompt_uri": (
                            f"harness://workflows/{case.head.workflow_run_id}/artifacts/{'0' * 64}"
                        ),
                    },
                ),
                "HARN_WORKFLOW_NOT_FOUND",
            )

            prompt_arguments = _prompt_arguments(case, advisory.structured_content)
            Draft202012Validator(prompt_tool.input_schema).validate(prompt_arguments)
            _assert_error(
                await client.call_tool(
                    PROMPT_TOOL,
                    {**prompt_arguments, "principal_id": "caller/forged"},
                ),
                "HARN_INPUT_SCHEMA_INVALID",
            )
            _assert_error(
                await client.call_tool(
                    PROMPT_TOOL,
                    {
                        **prompt_arguments,
                        "command_id": "73000000-0000-4000-8000-000000000006",
                        "idempotency_key": "prompt-revision-no-fallback-006",
                        "fallback": {"kind": "none"},
                    },
                ),
                "HARN_HISTORY_MISMATCH",
            )
            prompt = await client.call_tool(PROMPT_TOOL, prompt_arguments)
            prompt_retry = await client.call_tool(PROMPT_TOOL, prompt_arguments)
            assert prompt_retry == prompt
            assert prompt.is_error is False
            assert prompt.structured_content is not None
            assert prompt.structured_content["schema_version"] == (
                "harness.control_commit_snapshot.v1"
            )
            assert prompt.structured_content["control_kind"] == "prompt_revision"
            assert prompt.structured_content["committed_revision"] == 4
            assert [content.type for content in prompt.content] == ["text", "resource_link"]
            assert isinstance(prompt.content[1], types.ResourceLink)
            assert str(prompt.content[1].uri) == workflow_uri
            prompt_resource = await client.read_resource(workflow_uri)
            snapshot = json.loads(prompt_resource.contents[0].text)
            assert snapshot["revision"] == 4
            assert snapshot["state_sha256"] == case.head.state_sha256
            Draft202012Validator(prompt_tool.output_schema).validate(prompt.structured_content)

            stale = {
                **advisory_arguments,
                "command_id": "73000000-0000-4000-8000-000000000003",
                "idempotency_key": "diagnosis-stale-002",
            }
            _assert_error(
                await client.call_tool(ADVISORY_TOOL, stale),
                "HARN_WORKFLOW_HEAD_CONFLICT",
            )
            with pytest.raises(MCPError, match="unknown exact MCP tool"):
                await client.call_tool("skill.invoke", {})

    anyio.run(exercise)


def _server_parameters(config_path: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "tests.self_improving.harness.test_mcp_control_tools",
            "--serve",
            str(config_path),
        ],
        cwd=str(PROJECT_ROOT),
        env={
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
    )


async def _stdio_roundtrip(
    server: StdioServerParameters,
    *,
    case: control_base._Case,
    resource_access_receipt_uri: str | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], str, str]:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        try:
            async with Client(
                stdio_client(server, errlog=errlog),
                raise_exceptions=True,
                cache=None,
            ) as client:
                assert client.protocol_version == "2026-07-28"
                listed = await client.list_tools()
                names = tuple(tool.name for tool in listed.tools)
                assert set(CONTROL_TOOLS) <= set(names)
                assert "skill.invoke" not in names
                if resource_access_receipt_uri is None:
                    delivery = await client.read_resource(_artifact_uri(case, case.source_prompt))
                    assert len(delivery.contents) == 2
                    resource_access_receipt_uri = str(delivery.contents[1].uri)
                else:
                    receipt = await client.read_resource(resource_access_receipt_uri)
                    assert len(receipt.contents) == 1
                advisory = await client.call_tool(
                    ADVISORY_TOOL,
                    _advisory_arguments(
                        case,
                        resource_access_receipt_uri=resource_access_receipt_uri,
                    ),
                )
                assert advisory.is_error is False and advisory.structured_content is not None
                prompt = await client.call_tool(
                    PROMPT_TOOL,
                    _prompt_arguments(case, advisory.structured_content),
                )
                assert prompt.is_error is False and prompt.structured_content is not None
                workflow_uri = f"harness://workflows/{case.head.workflow_run_id}"
                resource = await client.read_resource(workflow_uri)
                assert json.loads(resource.contents[0].text)["revision"] == 4
        except BaseException as error:
            errlog.flush()
            errlog.seek(0)
            raise AssertionError(f"stdio control server failed:\n{errlog.read()}") from error
        errlog.seek(0)
        stderr = errlog.read()
    return (
        advisory.model_dump(mode="json", by_alias=True, exclude_none=True),
        prompt.model_dump(mode="json", by_alias=True, exclude_none=True),
        resource.model_dump(mode="json", by_alias=True, exclude_none=True),
        stderr,
        resource_access_receipt_uri,
    )


def _database_rows(path: Path) -> tuple[tuple[str, int], ...]:
    with closing(sqlite3.connect(path)) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        return tuple(
            (table, connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        )


def _cas_manifest(root: Path) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (str(path.relative_to(root)), path.stat().st_size, sha256(path.read_bytes()).hexdigest())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def test_official_stdio_restart_reuses_both_control_commits(tmp_path: Path) -> None:
    case_root = tmp_path / "case"
    case = control_base._case(case_root)
    config_path = tmp_path / "control-stdio.json"
    config_path.write_text(
        json.dumps(
            {
                "root": str(case_root),
                "registry_snapshot": case.head.registry_snapshot.model_dump(mode="json"),
                "observation": case.observation.model_dump(mode="json"),
                "diagnostic": case.diagnostic.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    server = _server_parameters(config_path)
    database_before = _database_rows(case.workflow_store.path)
    cas_before = _cas_manifest(case.store.root)

    async def exercise():
        with anyio.fail_after(30):
            first = await _stdio_roundtrip(server, case=case)
        database_after_first = _database_rows(case.workflow_store.path)
        cas_after_first = _cas_manifest(case.store.root)
        with anyio.fail_after(30):
            second = await _stdio_roundtrip(
                server,
                case=case,
                resource_access_receipt_uri=first[4],
            )
        return (
            first,
            second,
            database_after_first,
            _database_rows(case.workflow_store.path),
            cas_after_first,
            _cas_manifest(case.store.root),
        )

    results = anyio.run(exercise)
    (
        first,
        second,
        database_after_first,
        database_after_second,
        cas_after_first,
        cas_after_second,
    ) = results
    assert first[:3] == second[:3]
    assert database_before != database_after_first
    assert database_after_first == database_after_second
    assert cas_before != cas_after_first
    assert cas_after_first == cas_after_second
    counts = dict(database_after_second)
    assert counts["golden_workflow_operations"] == 2
    assert counts["golden_workflow_control_records"] == 2
    assert counts["golden_workflow_turns"] == 4
    assert not first[3]
    assert not second[3]
    assert first[4] == second[4]


def _load_stdio_adapter(config_path: Path):
    value = json.loads(config_path.read_text(encoding="utf-8"))
    return _adapter(
        Path(value["root"]),
        registry_ref=ArtifactRef.model_validate(value["registry_snapshot"]),
        observation_ref=ArtifactRef.model_validate(value["observation"]),
        diagnostic_ref=ArtifactRef.model_validate(value["diagnostic"]),
    )


def _main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "--serve":
        raise SystemExit("expected --serve SETTINGS.json")
    anyio.run(_serve_stdio, lambda: _load_stdio_adapter(Path(sys.argv[2])))


if __name__ == "__main__":
    _main()
