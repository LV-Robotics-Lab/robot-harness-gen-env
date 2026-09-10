"""Public-seam RED for exact PromptRevision consumption by image2env compile."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import Client
from pydantic import BaseModel

from self_improving.harness import schemas as public_schemas
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.golden_execution import (
    QualifiedApplicationRunPendingError,
    _create_golden_execution_binding,
    create_golden_execution_catalog,
)
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.mcp_adapter import create_harness_mcp_adapter
from self_improving.harness.mcp_server import create_harness_mcp_server
from self_improving.harness.registry import (
    ControlledRunPendingError,
    HandlerResult,
    SkillBlocked,
    SkillRegistry,
    StaticDependencyResolver,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schema_catalog import schema_model
from self_improving.harness.schemas.common import (
    ArtifactRef,
    Blocker,
    Invocation,
    RunState,
    SkillDescriptor,
)
from self_improving.harness.schemas.golden_control import (
    PromptRevision,
    prompt_revision_sha256,
)
from self_improving.harness.schemas.registry_snapshot import RegistrySnapshot
from self_improving.harness.schemas.workflow_operation import WorkflowOperationReceiptV2
from tests.self_improving.harness import test_golden_control_submit as control_base
from tests.self_improving.harness import test_golden_observation_submit as observe_base
from tests.self_improving.harness import test_mcp_control_tools as mcp_base
from tests.self_improving.harness import test_x2env_contracts as x2env_base

_COMPILE_SKILL = "image2env.compile@1.0.0"
_COMPILE_TOOL = "image2env_compile_v1_0_0"
_V2_SCHEMA = "harness.image2env_compile_input.v2"
_V2_SYMBOLS = (
    "InitialCompileAttemptV1",
    "PromptRevisionCompileAttemptV1",
    "Image2EnvCompileV2Input",
)
_V2_READY = all(hasattr(public_schemas, name) for name in _V2_SYMBOLS)
_requires_v2 = pytest.mark.skipif(
    not _V2_READY,
    reason="waiting for the public image2env compile-v2 attempt contract",
)

_COMPILE_COMMAND = UUID("73000000-0000-4000-8000-000000000020")
_COMPILE_CHILD = UUID("72000000-0000-4000-8000-000000000020")
_ALTERNATE_PROMPT_COMMAND = UUID("73000000-0000-4000-8000-000000000021")


@dataclass(frozen=True)
class _TrackingCompileApplication:
    artifact_root: Path
    authority_path: Path
    registry: SkillRegistry
    descriptor: SkillDescriptor
    policy: observe_base._FixturePolicy
    calls: list[tuple[UUID, dict[str, object]]]
    skill_ref: str = _COMPILE_SKILL

    @property
    def skills(self) -> tuple[SkillDescriptor, ...]:
        return (self.descriptor,)

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        self.calls.append((run_id, parameters.model_dump(mode="json")))
        try:
            return self.registry.invoke_with_run_id(
                "image2env.compile",
                "1.0.0",
                parameters.model_dump(mode="json"),
                run_id=run_id,
            )
        except ControlledRunPendingError as error:
            raise QualifiedApplicationRunPendingError(str(error)) from error

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.registry.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return SQLiteRunStore(self.authority_path).read_run_state(run_id)


@dataclass(frozen=True)
class _LiveCase:
    case: control_base._Case
    application: _TrackingCompileApplication
    child_allocations: list[UUID]
    server: object


def _case_with_v2_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> control_base._Case:
    original = observe_base._qualified_fixture

    def qualified_fixture(*args: object, **kwargs: object):
        if kwargs.get("skill_id") == "image2env.compile":
            kwargs["input_schema"] = _V2_SCHEMA
        return original(*args, **kwargs)

    monkeypatch.setattr(observe_base, "_qualified_fixture", qualified_fixture)
    return control_base._case(tmp_path)


def _live_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _LiveCase:
    case = _case_with_v2_descriptor(tmp_path, monkeypatch)
    registry_snapshot = mcp_base._read_model(
        case.store,
        case.head.registry_snapshot,
        RegistrySnapshot,
    )
    assert type(registry_snapshot) is RegistrySnapshot
    entry = next(
        item for item in registry_snapshot.qualified_skills if item.skill_ref == _COMPILE_SKILL
    )
    descriptor = mcp_base._read_model(case.store, entry.descriptor_ref, SkillDescriptor)
    assert type(descriptor) is SkillDescriptor
    assert descriptor.input_schema == _V2_SCHEMA
    policy = observe_base._FixturePolicy(
        skill_ref=_COMPILE_SKILL,
        input_model=schema_model(_V2_SCHEMA),
    )
    registry = SkillRegistry(
        artifact_resolver=case.store,
        dependency_resolver=StaticDependencyResolver({_COMPILE_SKILL: ()}),
        event_sink=SQLiteEventJournal(case.child_store.path),
        run_store=case.child_store,
        run_id_factory=lambda: pytest.fail("Golden must supply the compile child run id"),
        clock=case.clock,
    )

    def stop_after_preflight(_value: BaseModel, _context: object) -> HandlerResult:
        raise SkillBlocked(
            Blocker(
                code="HARN_TEST_CONTROLLED_STOP",
                message="the public-seam tracer stops after the compile application is invoked",
                stage="compile",
                retryable=False,
                details={},
                unknowns=(),
                artifact_refs=(case.diagnostic,),
            )
        )

    registry.register(descriptor, stop_after_preflight)
    calls: list[tuple[UUID, dict[str, object]]] = []
    application = _TrackingCompileApplication(
        artifact_root=case.store.root,
        authority_path=case.child_store.path,
        registry=registry,
        descriptor=descriptor,
        policy=policy,
        calls=calls,
    )
    allocations: list[UUID] = []

    def allocate_child() -> UUID:
        allocations.append(_COMPILE_CHILD)
        return _COMPILE_CHILD

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
    return _LiveCase(
        case=case,
        application=application,
        child_allocations=allocations,
        server=create_harness_mcp_server(adapter),
    )


async def _submit_prompt_revision(client: Client, case: control_base._Case) -> dict[str, object]:
    deliveries = tuple(
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
    access_receipts = tuple(sorted(str(delivery.contents[1].uri) for delivery in deliveries))
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
    return dict(prompt.structured_content)


def _put_json_ref(
    case: control_base._Case,
    tmp_path: Path,
    *,
    name: str,
    value: object,
    schema_version: str,
) -> ArtifactRef:
    return control_base._put_json(
        case.store,
        tmp_path,
        name=name,
        value=value,
        schema_version=schema_version,
    )


def _put_text_ref(
    case: control_base._Case,
    tmp_path: Path,
    *,
    name: str,
    text: str,
) -> ArtifactRef:
    return control_base._put_binary(
        case.store,
        tmp_path,
        name=name,
        payload=text.encode("utf-8"),
        media_type="text/plain",
        schema_version="harness.prompt_text.v1",
    )


def _put_png_ref(
    case: control_base._Case,
    tmp_path: Path,
    *,
    name: str,
    payload: bytes,
) -> ArtifactRef:
    return control_base._put_binary(
        case.store,
        tmp_path,
        name=name,
        payload=payload,
        media_type="image/png",
        schema_version=None,
    )


def _compile_parameters(
    case: control_base._Case,
    tmp_path: Path,
    prompt_snapshot: dict[str, object],
) -> tuple[dict[str, object], PromptRevision]:
    prompt_ref = ArtifactRef.model_validate(prompt_snapshot["receipt"])
    prompt = mcp_base._read_model(case.store, prompt_ref, PromptRevision)
    assert type(prompt) is PromptRevision
    assert prompt.fallback.kind == "video_frame_to_image"
    probe = _put_json_ref(
        case,
        tmp_path,
        name="retry-image-probe.json",
        value={"width": 1, "height": 1, "source": case.frame.model_dump(mode="json")},
        schema_version="harness.image_media_probe.v1",
    )
    proposal = _put_json_ref(
        case,
        tmp_path,
        name="retry-interpretation.json",
        value={"route": "image2env", "prompt": prompt.revised_prompt.model_dump(mode="json")},
        schema_version="harness.scene_interpretation_proposal.v1",
    )
    catalog = _put_json_ref(
        case,
        tmp_path,
        name="retry-asset-catalog.json",
        value={"assets": []},
        schema_version="harness.portable_asset_catalog.v1",
    )
    parameters = x2env_base.compile_input("image2env")
    parameters.update(
        {
            "schema_version": _V2_SCHEMA,
            "source_media": {
                "schema_version": "harness.image_source_media.v1",
                "source_media_sha256": case.frame.sha256,
                "source": case.frame.model_dump(mode="json"),
                "canonical_image": case.frame.model_dump(mode="json"),
                "probe": probe.model_dump(mode="json"),
                "width": 1,
                "height": 1,
                "color_mode": "RGBA",
                "decoded_frame_count": 1,
                "unique_frame_count": 1,
            },
            "interpretation_proposal": proposal.model_dump(mode="json"),
            "asset_catalog": catalog.model_dump(mode="json"),
            "predecessor_package": case.predecessor_package.model_dump(mode="json"),
            "repair_evidence_refs": [],
            "attempt": {
                "schema_version": "harness.prompt_revision_compile_attempt.v1",
                "kind": "prompt_revision",
                "prompt_revision": prompt_ref.model_dump(mode="json"),
                "effective_prompt": prompt.revised_prompt.model_dump(mode="json"),
                "failure_receipts": [case.failure_receipt.model_dump(mode="json")],
                "predecessor_package": case.predecessor_package.model_dump(mode="json"),
            },
        }
    )
    model = getattr(public_schemas, "Image2EnvCompileV2Input").model_validate(parameters)
    return model.model_dump(mode="json"), prompt


def _command_arguments(
    case: control_base._Case,
    before: dict[str, object],
    parameters: dict[str, object],
    *,
    suffix: str,
) -> dict[str, object]:
    return {
        "schema_version": "harness.run_command_request.v1",
        "command_id": str(_COMPILE_COMMAND),
        "workflow_run_id": str(case.head.workflow_run_id),
        "idempotency_key": f"consume-prompt-revision-{suffix}",
        "expected_workflow_revision": before["revision"],
        "expected_state_sha256": before["state_sha256"],
        "expected_receipt_head_sha256": before["receipt_head"]["sha256"],
        "parameters": parameters,
    }


def _alternate_prompt_revision(
    case: control_base._Case,
    tmp_path: Path,
    prompt: PromptRevision,
) -> ArtifactRef:
    unhashed = prompt.model_copy(
        update={
            "command_id": _ALTERNATE_PROMPT_COMMAND,
            "revision_sha256": "0" * 64,
        }
    )
    alternate = PromptRevision(
        **{
            name: getattr(unhashed, name)
            for name in PromptRevision.model_fields
            if name != "revision_sha256"
        },
        revision_sha256=prompt_revision_sha256(unhashed),
    )
    return _put_json_ref(
        case,
        tmp_path,
        name="alternate-prompt-revision.json",
        value=alternate,
        schema_version="harness.prompt_revision.v1",
    )


def _apply_drift(
    name: str,
    parameters: dict[str, object],
    *,
    case: control_base._Case,
    tmp_path: Path,
    prompt: PromptRevision,
) -> tuple[str, dict[str, object]]:
    drifted = deepcopy(parameters)
    attempt = drifted["attempt"]
    assert isinstance(attempt, dict)
    if name == "target":
        return "tests_evidence_v1_0_0", case.observation.model_dump(mode="json")
    if name == "prompt_revision":
        attempt["prompt_revision"] = _alternate_prompt_revision(
            case,
            tmp_path,
            prompt,
        ).model_dump(mode="json")
    elif name == "effective_prompt":
        attempt["effective_prompt"] = _put_text_ref(
            case,
            tmp_path,
            name="alternate-effective-prompt.txt",
            text="Build a different scene without the cited fallback lineage.",
        ).model_dump(mode="json")
    elif name == "failure_receipts":
        failure = mcp_base._read_model(case.store, case.failure_receipt, WorkflowOperationReceiptV2)
        assert type(failure) is WorkflowOperationReceiptV2
        attempt["failure_receipts"] = [failure.predecessor_receipt.model_dump(mode="json")]
    elif name == "predecessor_package":
        package = _put_json_ref(
            case,
            tmp_path,
            name="alternate-predecessor-package.json",
            value={"package_id": "2" * 64, "route_id": "video2env"},
            schema_version="harness.x2env_environment_package.v1",
        )
        attempt["predecessor_package"] = package.model_dump(mode="json")
        drifted["predecessor_package"] = package.model_dump(mode="json")
    elif name == "fallback_frame":
        alternate_frame = _put_png_ref(
            case,
            tmp_path,
            name="alternate-frame.png",
            payload=control_base._PNG + b"alternate",
        )
        source_media = drifted["source_media"]
        assert isinstance(source_media, dict)
        source_media["source_media_sha256"] = alternate_frame.sha256
        source_media["source"] = alternate_frame.model_dump(mode="json")
        source_media["canonical_image"] = alternate_frame.model_dump(mode="json")
    else:  # pragma: no cover - the parametrization is closed below
        raise AssertionError(name)
    model = getattr(public_schemas, "Image2EnvCompileV2Input").model_validate(drifted)
    return _COMPILE_TOOL, model.model_dump(mode="json")


async def _workflow_json(client: Client, case: control_base._Case) -> dict[str, object]:
    resource = await client.read_resource(f"harness://workflows/{case.head.workflow_run_id}")
    return json.loads(resource.contents[0].text)


@_requires_v2
def test_exact_prompt_revision_attempt_invokes_compile_once_and_advances_revision_five(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_case(tmp_path, monkeypatch)

    async def exercise() -> None:
        async with Client(live.server, raise_exceptions=True, cache=None) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools[_COMPILE_TOOL].input_schema["properties"]["parameters"]["$ref"].endswith(
                "Image2EnvCompileV2Input"
            )
            prompt_snapshot = await _submit_prompt_revision(client, live.case)
            parameters, _ = _compile_parameters(live.case, tmp_path, prompt_snapshot)
            before = await _workflow_json(client, live.case)
            arguments = _command_arguments(live.case, before, parameters, suffix="exact")
            Draft202012Validator(tools[_COMPILE_TOOL].input_schema).validate(arguments)

            result = await client.call_tool(_COMPILE_TOOL, arguments)

            assert result.structured_content is not None
            assert result.structured_content["status"] == "blocked"
            assert live.child_allocations == [_COMPILE_CHILD]
            assert live.application.calls == [(_COMPILE_CHILD, parameters)]
            after = await _workflow_json(client, live.case)
            assert before["revision"] == before["turn_seq"] == 4
            assert after["revision"] == after["turn_seq"] == 5
            assert after["state_ref"] == before["state_ref"]
            assert after["state_sha256"] == before["state_sha256"]
            assert after["child_runs"] == [*before["child_runs"], str(_COMPILE_CHILD)]
            workflow_receipt = mcp_base._read_model(
                live.case.store,
                ArtifactRef.model_validate(after["receipt_head"]),
                WorkflowOperationReceiptV2,
            )
            assert type(workflow_receipt) is WorkflowOperationReceiptV2
            assert workflow_receipt.predecessor_receipt == ArtifactRef.model_validate(
                prompt_snapshot["receipt"]
            )

    anyio.run(exercise)


@pytest.mark.parametrize(
    "drift",
    (
        "target",
        "prompt_revision",
        "effective_prompt",
        "failure_receipts",
        "predecessor_package",
        "fallback_frame",
    ),
)
@_requires_v2
def test_prompt_revision_attempt_drift_fails_before_child_cas_or_database_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    live = _live_case(tmp_path, monkeypatch)

    async def exercise() -> None:
        async with Client(live.server, raise_exceptions=True, cache=None) as client:
            prompt_snapshot = await _submit_prompt_revision(client, live.case)
            parameters, prompt = _compile_parameters(live.case, tmp_path, prompt_snapshot)
            tool_name, drifted = _apply_drift(
                drift,
                parameters,
                case=live.case,
                tmp_path=tmp_path,
                prompt=prompt,
            )
            before = await _workflow_json(client, live.case)
            database_before = mcp_base._database_rows(live.case.workflow_store.path)
            cas_before = mcp_base._cas_manifest(live.case.store.root)
            arguments = _command_arguments(live.case, before, drifted, suffix=drift)

            rejected = await client.call_tool(tool_name, arguments)

            assert rejected.is_error is True
            assert rejected.structured_content is not None
            assert rejected.structured_content["code"] == "HARN_HISTORY_MISMATCH"
            assert await _workflow_json(client, live.case) == before
            assert mcp_base._database_rows(live.case.workflow_store.path) == database_before
            assert mcp_base._cas_manifest(live.case.store.root) == cas_before
            assert live.application.calls == []
            assert live.child_allocations == []

    anyio.run(exercise)


@_requires_v2
def test_old_image_compile_v1_input_remains_rejected_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_case(tmp_path, monkeypatch)

    async def exercise() -> None:
        async with Client(live.server, raise_exceptions=True, cache=None) as client:
            await _submit_prompt_revision(client, live.case)
            before = await _workflow_json(client, live.case)
            database_before = mcp_base._database_rows(live.case.workflow_store.path)
            cas_before = mcp_base._cas_manifest(live.case.store.root)
            old_parameters = x2env_base.compile_input("image2env")
            old_parameters["repair_evidence_refs"] = []
            arguments = _command_arguments(
                live.case,
                before,
                old_parameters,
                suffix="old-v1",
            )

            rejected = await client.call_tool(_COMPILE_TOOL, arguments)

            assert rejected.is_error is True
            assert rejected.structured_content is not None
            assert rejected.structured_content["code"] == "HARN_INPUT_SCHEMA_INVALID"
            assert await _workflow_json(client, live.case) == before
            assert mcp_base._database_rows(live.case.workflow_store.path) == database_before
            assert mcp_base._cas_manifest(live.case.store.root) == cas_before
            assert live.application.calls == []
            assert live.child_allocations == []

    anyio.run(exercise)
