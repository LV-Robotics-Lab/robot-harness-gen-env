"""Official MCP tracer for chaining all three live text2env Skills."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, timezone
from importlib.metadata import version
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
assert version("mcp") == "2.2.0"

from mcp import Client, MCPError, types  # noqa: E402

from scene_gen.schema import ResolvedSceneSpec  # noqa: E402
from self_improving.harness.application import (  # noqa: E402
    CompileApplicationSettings,
    create_compile_application,
)
from self_improving.harness.artifacts import LocalArtifactStore  # noqa: E402
from self_improving.harness.golden_compile_execution import (  # noqa: E402
    create_text2env_compile_execution_binding,
)
from self_improving.harness.golden_execution import (  # noqa: E402
    create_golden_execution_catalog,
)
from self_improving.harness.golden_replay_execution import (  # noqa: E402
    create_text2env_replay_execution_binding,
)
from self_improving.harness.golden_run import GoldenRunHarness  # noqa: E402
from self_improving.harness.golden_store import SQLiteGoldenRunStore  # noqa: E402
from self_improving.harness.golden_validate_execution import (  # noqa: E402
    create_text2env_validate_execution_binding,
)
from self_improving.harness.mcp_adapter import (  # noqa: E402
    MCPWorkflowSkillInputProvider,
    create_harness_mcp_adapter,
)
from self_improving.harness.mcp_server import create_harness_mcp_server  # noqa: E402
from self_improving.harness.schemas.common import ArtifactRef, RunState  # noqa: E402
from self_improving.harness.schemas.text2env import (  # noqa: E402
    CompileConfig,
    RuntimeConfig,
    Text2EnvCompileInput,
    Text2EnvReplayInput,
)
from self_improving.harness.schemas.text2env_validate_v2 import (  # noqa: E402
    Text2EnvValidateV2Input,
    Text2EnvValidateV2Output,
)
from self_improving.harness.schemas.workflow_operation import (  # noqa: E402
    StateDeltaV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.trusted_state import TrustedWorldState  # noqa: E402
from self_improving.harness.validate_application import (  # noqa: E402
    ValidateApplicationSettings,
    create_validate_application,
)
from self_improving.harness.validate_input_builder import (  # noqa: E402
    Text2EnvValidateV2InputBuilder,
)
from tests.self_improving import test_validate_v2_snapshot as snapshot_fixture  # noqa: E402
from tests.self_improving.harness import test_golden_replay_submit as replay_base  # noqa: E402
from tests.self_improving.harness import test_golden_run as base  # noqa: E402
from tests.self_improving.harness import test_golden_validate_submit as validate_base  # noqa: E402
from tests.self_improving.harness import (  # noqa: E402
    test_text2env_replay_handler as replay_fixture,
)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def test_official_client_prepares_validate_input_after_exact_compile_and_replay(
    tmp_path: Path,
) -> None:
    source = replay_fixture._fixture(tmp_path / "source")
    compile_application = create_compile_application(
        CompileApplicationSettings(
            state_root=tmp_path / "compile-state",
            external_catalog_roots=(tmp_path / "source",),
            allowed_asset_roots=(source.robotwin_root,),
            admission_date=date(2026, 9, 10),
            asset_library_root=tmp_path / "asset-library",
        )
    )
    store = LocalArtifactStore(compile_application.artifact_root)
    replay_application, executor = replay_base._replay_application(
        tmp_path,
        store=store,
        source=source,
    )
    validate_application = create_validate_application(
        ValidateApplicationSettings(
            state_root=tmp_path / "validate-state",
            evidence_artifact_root=store.root,
        )
    )
    registry = validate_base._registry(
        store,
        tmp_path,
        (compile_application, replay_application, validate_application),
    )
    children = iter(validate_base.CHILD_IDS)
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                create_text2env_compile_execution_binding(application=compile_application),
                create_text2env_replay_execution_binding(application=replay_application),
                create_text2env_validate_execution_binding(application=validate_application),
            )
        ),
        clock=lambda: datetime.now(timezone.utc),
        workflow_id_factory=lambda: base.WORKFLOW_ID,
        child_run_id_factory=lambda: next(children),
    )
    validate_input_builder = Text2EnvValidateV2InputBuilder(
        artifact_store=store,
        compile_application=compile_application,
        replay_application=replay_application,
    )
    adapter = create_harness_mcp_adapter(
        harness=harness,
        principal_id="tests/golden-e2e",
        registry_snapshot=registry,
        skill_input_providers=(
            MCPWorkflowSkillInputProvider(
                skill_ref="text2env.validate@2.0.0",
                build=validate_input_builder.build,
            ),
        ),
    )
    server = create_harness_mcp_server(adapter)
    foreign_server = create_harness_mcp_server(
        create_harness_mcp_adapter(
            harness=harness,
            principal_id="tests/foreign-principal",
            registry_snapshot=registry,
        )
    )
    projected_tools = adapter.list_tools(registry)
    snapshot_path = (
        Path(__file__).resolve().parents[3]
        / "self_improving/harness/mcp_tool_snapshots/text2env_golden.v1.json"
    )
    snapshot = json.loads(snapshot_path.read_bytes())
    assert snapshot == {
        "schema_version": "harness.mcp_tool_catalog_snapshot.v1",
        "tools": [
            {
                "name": tool.name,
                "skill_ref": tool.skill_ref,
                "input_schema_sha256": hashlib.sha256(
                    _canonical_json_bytes(tool.input_schema)
                ).hexdigest(),
                "output_schema_sha256": hashlib.sha256(
                    _canonical_json_bytes(tool.output_schema)
                ).hexdigest(),
            }
            for tool in projected_tools
        ],
    }
    compile_parameters = Text2EnvCompileInput(
        request=validate_base.REQUEST,
        seed=7,
        asset_catalog=compile_application.snapshot_asset_catalog(tmp_path / "source/catalog.json"),
        config=CompileConfig(generate_missing_assets=False),
    ).model_dump(mode="json")

    async def exercise() -> None:
        async with Client(server, raise_exceptions=True, cache=None) as client:
            listed = await client.list_tools()
            assert tuple(tool.name for tool in listed.tools) == (
                "harness_workflow_create_v1",
                "text2env_compile_v1_0_0",
                "text2env_replay_v1_0_0",
                "text2env_validate_v2_0_0",
            )
            created = await client.call_tool(
                listed.tools[0].name,
                {
                    "schema_version": "harness.mcp_workflow_create_request.v1",
                    "idempotency_key": "mcp-three-skill-start-001",
                    "workspace": "ephemeral",
                    "requested_profile": "legacy.robotwin_validation@2",
                    "initial_user_facts": [
                        {
                            "key": "request.objective",
                            "value": validate_base.REQUEST,
                            "observed_at": "2026-09-10T00:00:00Z",
                            "valid_until": None,
                        }
                    ],
                },
            )
            assert created.is_error is False
            assert created.structured_content["revision"] == 0
            compiled = await client.call_tool(
                listed.tools[1].name,
                {
                    "schema_version": "harness.run_command_request.v1",
                    "command_id": "42000000-0000-4000-8000-000000000001",
                    "workflow_run_id": created.structured_content["workflow_run_id"],
                    "idempotency_key": "mcp-three-skill-compile-001",
                    "expected_workflow_revision": 0,
                    "expected_state_sha256": created.structured_content["state_sha256"],
                    "expected_receipt_head_sha256": created.structured_content["receipt_head"][
                        "sha256"
                    ],
                    "parameters": compile_parameters,
                },
            )
            assert compiled.is_error is False
            assert compiled.structured_content["status"] == "succeeded"
            assert compiled.structured_content["committed_revision"] == 1
            workflow = await client.read_resource(
                f"harness://workflows/{created.structured_content['workflow_run_id']}"
            )
            revision_one = json.loads(workflow.contents[0].text)
            assert revision_one["revision"] == 1

            state_resource = await client.read_resource(
                "harness://workflows/"
                f"{created.structured_content['workflow_run_id']}/artifacts/"
                f"{revision_one['state_ref']['sha256']}"
            )
            assert len(state_resource.contents) == 1
            assert isinstance(state_resource.contents[0], types.TextResourceContents)
            state = TrustedWorldState.model_validate_json(state_resource.contents[0].text)
            assert [fact.key for fact in state.facts] == [
                "assets.catalog",
                "environment.package",
                "request.objective",
            ]
            package = next(fact.value for fact in state.facts if fact.key == "environment.package")
            replay_parameters = Text2EnvReplayInput(
                environment_package=package,
                runtime_config=RuntimeConfig(
                    precheck_steps=0,
                    settle_steps=900,
                    contact_window_steps=120,
                    video_frames=120,
                    fps=12,
                ),
            )
            manifest = json.loads(
                store.resolve(
                    replay_parameters.environment_package.package_manifest
                ).path.read_bytes()
            )
            resolved_record = next(
                item for item in manifest["files"] if item["path"] == "resolved_scene.json"
            )
            resolved_path = (
                store.root / "sha256" / resolved_record["sha256"][:2] / resolved_record["sha256"]
            )
            resolved = ResolvedSceneSpec.model_validate_json(resolved_path.read_bytes())

            def complete_evidence(evidence: dict[str, object]) -> None:
                complete = snapshot_fixture._passing_runtime_evidence(
                    resolved,
                    snapshot_sha256=str(evidence["runtime_asset_snapshot_sha256"]),
                )
                complete.update(
                    {
                        "precheck_steps": 0,
                        "contact_window_steps": 120,
                        "base_simulation_step_count": 900,
                        "simulation_step_count": 900,
                        "total_physics_step_count": 900,
                        "settle_converge_max": 0,
                        "video_frame_count": 120,
                        "unique_video_frame_count": 120,
                        "video_sample_step_indices": [index * 899 // 119 for index in range(120)],
                        "fps": 12,
                        "images": evidence["images"],
                        "video": evidence["video"],
                    }
                )
                evidence.clear()
                evidence.update(complete)

            executor.evidence_mutation = complete_evidence
            executor.validation_mutation = lambda report: report.update(
                {
                    "status": "pass",
                    "fail_count": 0,
                    "not_run_count": 0,
                    "checks": [{"name": "physical_contact", "status": "pass", "evidence": {}}],
                }
            )
            replayed = await client.call_tool(
                listed.tools[2].name,
                {
                    "schema_version": "harness.run_command_request.v1",
                    "command_id": "42000000-0000-4000-8000-000000000002",
                    "workflow_run_id": revision_one["workflow_run_id"],
                    "idempotency_key": "mcp-three-skill-replay-001",
                    "expected_workflow_revision": 1,
                    "expected_state_sha256": revision_one["state_sha256"],
                    "expected_receipt_head_sha256": revision_one["receipt_head"]["sha256"],
                    "parameters": replay_parameters.model_dump(mode="json"),
                },
            )
            assert replayed.is_error is False
            assert replayed.structured_content["status"] == "succeeded"
            assert replayed.structured_content["committed_revision"] == 2
            workflow = await client.read_resource(
                f"harness://workflows/{revision_one['workflow_run_id']}"
            )
            revision_two = json.loads(workflow.contents[0].text)
            assert revision_two["revision"] == 2
            state_resource = await client.read_resource(
                "harness://workflows/"
                f"{revision_two['workflow_run_id']}/artifacts/"
                f"{revision_two['state_ref']['sha256']}"
            )
            state = TrustedWorldState.model_validate_json(state_resource.contents[0].text)
            assert [fact.key for fact in state.facts] == [
                "assets.catalog",
                "environment.package",
                "replay.runtime_evidence",
                "request.objective",
            ]
            with pytest.raises(MCPError, match="unknown Harness resource"):
                await client.read_resource(
                    "harness://workflows/"
                    "10000000-0000-4000-8000-000000000099/artifacts/"
                    f"{revision_one['state_ref']['sha256']}"
                )
            with pytest.raises(MCPError, match="unknown Harness resource"):
                await client.read_resource(
                    "harness://workflows/"
                    f"{created.structured_content['workflow_run_id']}/artifacts/"
                    f"{'0' * 64}"
                )

            prepared = await client.read_resource(
                "harness://workflows/"
                f"{revision_two['workflow_run_id']}/skill-inputs/"
                "text2env.validate/2.0.0"
            )
            validate_input = Text2EnvValidateV2Input.model_validate_json(prepared.contents[0].text)
            assert validate_input.gate_profile == "robotwin.scene_validation.v1"
            validate_arguments = {
                "schema_version": "harness.run_command_request.v1",
                "command_id": "42000000-0000-4000-8000-000000000003",
                "workflow_run_id": revision_two["workflow_run_id"],
                "idempotency_key": "mcp-three-skill-validate-001",
                "expected_workflow_revision": 2,
                "expected_state_sha256": revision_two["state_sha256"],
                "expected_receipt_head_sha256": revision_two["receipt_head"]["sha256"],
                "parameters": validate_input.model_dump(mode="json"),
            }
            validated = await client.call_tool(listed.tools[3].name, validate_arguments)
            assert validated.is_error is False
            assert validated.structured_content["status"] == "succeeded"
            assert validated.structured_content["committed_revision"] == 3
            cas_before_retry = tuple(
                sorted(
                    (path.relative_to(store.root).as_posix(), path.stat().st_size)
                    for path in store.root.glob("sha256/*/*")
                )
            )
            repeated = await client.call_tool(listed.tools[3].name, validate_arguments)
            assert repeated.structured_content == validated.structured_content
            assert (
                tuple(
                    sorted(
                        (path.relative_to(store.root).as_posix(), path.stat().st_size)
                        for path in store.root.glob("sha256/*/*")
                    )
                )
                == cas_before_retry
            )
            workflow = await client.read_resource(
                f"harness://workflows/{revision_two['workflow_run_id']}"
            )
            revision_three = json.loads(workflow.contents[0].text)
            assert revision_three["revision"] == 3
            assert revision_three["child_runs"] == [str(item) for item in validate_base.CHILD_IDS]
            state_resource = await client.read_resource(
                "harness://workflows/"
                f"{revision_three['workflow_run_id']}/artifacts/"
                f"{revision_three['state_ref']['sha256']}"
            )
            state_payload = state_resource.contents[0].text.encode()
            assert (
                hashlib.sha256(state_payload).hexdigest() == revision_three["state_ref"]["sha256"]
            )
            state = TrustedWorldState.model_validate_json(state_payload)
            assert [fact.key for fact in state.facts] == [
                "assets.catalog",
                "environment.package",
                "environment.validation",
                "replay.runtime_evidence",
                "request.objective",
            ]

            receipt_resource = await client.read_resource(
                "harness://workflows/"
                f"{revision_three['workflow_run_id']}/artifacts/"
                f"{revision_three['receipt_head']['sha256']}"
            )
            receipt_payload = receipt_resource.contents[0].text.encode()
            assert (
                hashlib.sha256(receipt_payload).hexdigest()
                == revision_three["receipt_head"]["sha256"]
            )
            receipt = WorkflowOperationReceiptV2.model_validate_json(receipt_payload)
            assert receipt.predecessor_receipt == ArtifactRef.model_validate(
                revision_two["receipt_head"]
            )
            assert receipt.tool_result == ArtifactRef.model_validate(
                validated.structured_content["tool_result"]
            )

            async def read_artifact(ref: ArtifactRef, model):
                resource = await client.read_resource(
                    "harness://workflows/"
                    f"{revision_three['workflow_run_id']}/artifacts/{ref.sha256}"
                )
                assert isinstance(resource.contents[0], types.TextResourceContents)
                payload = resource.contents[0].text.encode()
                assert hashlib.sha256(payload).hexdigest() == ref.sha256
                return model.model_validate_json(payload)

            result = await read_artifact(receipt.tool_result, ToolResultV2)
            output = Text2EnvValidateV2Output.model_validate(result.typed_output)
            assert output.publishable is True
            assert output.validation_status.value == "pass"
            assert result.fresh_observations == ()
            assert result.state_delta is not None
            delta = await read_artifact(result.state_delta, StateDeltaV2)
            assert [mutation.key for mutation in delta.mutations] == ["environment.validation"]
            trusted = await read_artifact(result.trusted_receipt, TrustedToolReceiptV2)
            assert trusted.base_revision == 2
            assert trusted.base_state == ArtifactRef.model_validate(revision_two["state_ref"])

            replay_receipt = await read_artifact(
                receipt.predecessor_receipt,
                WorkflowOperationReceiptV2,
            )
            replay_result = await read_artifact(replay_receipt.tool_result, ToolResultV2)
            replay_run = await read_artifact(replay_result.run_state, RunState)
            videos = tuple(
                artifact for artifact in replay_run.artifacts if artifact.media_type == "video/mp4"
            )
            assert len(videos) == 1
            video = videos[0]
            video_resource = await client.read_resource(
                f"harness://workflows/{revision_three['workflow_run_id']}/artifacts/{video.sha256}"
            )
            assert len(video_resource.contents) == 1
            assert isinstance(video_resource.contents[0], types.BlobResourceContents)
            video_bytes = base64.b64decode(video_resource.contents[0].blob, validate=True)
            assert len(video_bytes) == video.bytes
            assert hashlib.sha256(video_bytes).hexdigest() == video.sha256
            assert video_bytes == store.resolve(video).path.read_bytes()

        async with Client(foreign_server, raise_exceptions=True, cache=None) as foreign:
            with pytest.raises(MCPError, match="unknown Harness resource"):
                await foreign.read_resource(
                    "harness://workflows/"
                    f"{created.structured_content['workflow_run_id']}/artifacts/"
                    f"{revision_one['state_ref']['sha256']}"
                )

    anyio.run(exercise)
