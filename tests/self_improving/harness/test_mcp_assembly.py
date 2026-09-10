"""Production assembly tests for the restartable compile MCP server."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from self_improving.harness.application import create_compile_application
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.mcp_adapter import MCPToolCall
from self_improving.harness.mcp_assembly import (
    CompileMCPServerSettings,
    GoldenMCPServerSettings,
    create_compile_mcp_adapter,
    create_golden_mcp_adapter,
)
from self_improving.harness.mcp_stdio import (
    load_compile_mcp_settings,
    load_mcp_settings,
)
from self_improving.harness.replay_application import create_replay_application
from self_improving.harness.schemas.text2env import CompileConfig, Text2EnvCompileInput
from self_improving.harness.validate_application import (
    ValidateApplicationSettings,
    create_validate_application,
)
from tests.self_improving.harness import test_golden_run as base
from tests.self_improving.harness import test_golden_validate_submit as validate_base
from tests.self_improving.harness import test_replay_application as replay_application_base
from tests.self_improving.harness import test_text2env_replay_handler as replay_fixture

ROOT = Path(__file__).resolve().parents[3]


def test_compile_mcp_assembly_reopens_the_same_durable_authorities(tmp_path: Path) -> None:
    application_settings = base._compile_settings(tmp_path)
    application = create_compile_application(application_settings)
    store = LocalArtifactStore(application.artifact_root)
    registry = base._qualified_registry_for_application(store, tmp_path, application)
    golden_database = tmp_path / "golden.sqlite3"
    catalog_path = application_settings.external_catalog_roots[0] / "empty.json"
    base._empty_catalog(
        catalog_path,
        objects_root=application_settings.allowed_asset_roots[0] / "objects",
    )
    settings = CompileMCPServerSettings(
        schema_version="harness.compile_mcp_server_settings.v1",
        principal_id="operator/system2",
        registry_snapshot=registry,
        compile_state_root=application_settings.state_root,
        external_catalog_roots=application_settings.external_catalog_roots,
        allowed_asset_roots=application_settings.allowed_asset_roots,
        admission_date=application_settings.admission_date,
        asset_library_root=application_settings.asset_library_root,
        golden_database_path=golden_database,
    )
    adapter = create_compile_mcp_adapter(settings)
    tools = {tool.name: tool for tool in adapter.list_tools(settings.registry_snapshot)}
    start_tool = tools["harness_workflow_create_v1"]
    tool = tools["text2env_compile_v1_0_0"]
    start_arguments = {
        "schema_version": "harness.mcp_workflow_create_request.v1",
        "idempotency_key": "start-through-reopened-mcp",
        "workspace": "ephemeral",
        "requested_profile": "legacy.robotwin_validation@2",
        "initial_user_facts": [
            {
                "key": "request.objective",
                "value": base.GENERATED_OBJECTIVE,
                "observed_at": "2026-09-09T00:00:00Z",
                "valid_until": None,
            }
        ],
    }
    started = adapter.call_tool(MCPToolCall(name=start_tool.name, arguments=start_arguments))
    assert started.is_error is False
    arguments = {
        "schema_version": "harness.run_command_request.v1",
        "command_id": "20000000-0000-4000-8000-000000000001",
        "workflow_run_id": started.structured_content["workflow_run_id"],
        "idempotency_key": "compile-through-reopened-mcp",
        "expected_workflow_revision": 0,
        "expected_state_sha256": started.structured_content["state_sha256"],
        "expected_receipt_head_sha256": started.structured_content["receipt_head"]["sha256"],
        "parameters": Text2EnvCompileInput(
            request=base.GENERATED_OBJECTIVE,
            seed=77,
            asset_catalog=application.snapshot_asset_catalog(catalog_path),
            config=CompileConfig(generate_missing_assets=True),
        ).model_dump(mode="json"),
    }

    first = adapter.call_tool(MCPToolCall(name=tool.name, arguments=arguments))
    reopened = create_compile_mcp_adapter(settings)
    recovered_start = reopened.call_tool(
        MCPToolCall(name=start_tool.name, arguments=start_arguments)
    )
    second = reopened.call_tool(MCPToolCall(name=tool.name, arguments=arguments))

    assert first == second
    assert recovered_start.structured_content["revision"] == 1
    assert first.structured_content["status"] == "succeeded"
    assert first.structured_content["committed_revision"] == 1

    with pytest.raises(TypeError, match="exact CompileMCPServerSettings"):
        create_compile_mcp_adapter(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty exact string"):
        create_compile_mcp_adapter(settings.model_copy(update={"principal_id": ""}))


def test_golden_mcp_assembly_reopens_three_exact_live_applications(tmp_path: Path) -> None:
    source = replay_fixture._fixture(tmp_path / "source")
    compile_settings = base._compile_settings(tmp_path)
    compile_settings = compile_settings.__class__(
        state_root=compile_settings.state_root,
        external_catalog_roots=(tmp_path / "source",),
        allowed_asset_roots=(source.robotwin_root,),
        admission_date=compile_settings.admission_date,
        asset_library_root=compile_settings.asset_library_root,
    )
    compile_application = create_compile_application(compile_settings)
    store = LocalArtifactStore(compile_application.artifact_root)
    (tmp_path / "replay-factory").mkdir()
    replay_settings = replay_application_base._factory_settings(
        tmp_path / "replay-factory",
        evidence_root=store.root,
        allowed_asset_root=source.robotwin_root,
    )
    patches, _executors = replay_application_base._factory_boundary_patches(
        store=store,
        robotwin_root=source.robotwin_root,
        runtime_root=tmp_path / "runtime-test-adapter",
    )
    validate_settings = ValidateApplicationSettings(
        state_root=tmp_path / "validate-state",
        evidence_artifact_root=store.root,
    )
    with ExitStack() as stack:
        stack.enter_context(patches)
        replay_application = create_replay_application(replay_settings)
        validate_application = create_validate_application(validate_settings)
        registry = validate_base._registry(
            store,
            tmp_path,
            (compile_application, replay_application, validate_application),
        )
        settings = GoldenMCPServerSettings(
            schema_version="harness.golden_mcp_server_settings.v1",
            principal_id="operator/system2",
            registry_snapshot=registry,
            compile_state_root=compile_settings.state_root,
            external_catalog_roots=compile_settings.external_catalog_roots,
            allowed_asset_roots=compile_settings.allowed_asset_roots,
            admission_date=compile_settings.admission_date,
            asset_library_root=compile_settings.asset_library_root,
            golden_database_path=tmp_path / "golden.sqlite3",
            qualification_cas_roots=(store.root,),
            replay_state_root=replay_settings.state_root,
            replay_qualification_bundle_root=replay_settings.qualification_bundle_root,
            validate_state_root=validate_settings.state_root,
            validate_qualification_bundle_root=(
                ROOT / "self_improving/harness/qualified_skills/text2env.validate/2.0.0"
            ),
            implementation_root=replay_settings.implementation_root,
            scene_gen_root=replay_settings.scene_gen_root,
            ledger_contract_root=replay_settings.ledger_contract_root,
            interpreter=replay_settings.interpreter,
            runtime_runner=replay_settings.runtime_runner,
            runtime_module_root=replay_settings.runtime_module_root,
            runtime_capability_path=replay_settings.runtime_capability_path,
            media_launcher=replay_settings.media_launcher,
            static_ffmpeg=replay_settings.static_ffmpeg,
            delegated_cgroup_root=replay_settings.delegated_cgroup_root,
            runtime_timeout_seconds=replay_settings.runtime_timeout_seconds,
            capability_timeout_seconds=replay_settings.capability_timeout_seconds,
        )

        first = create_golden_mcp_adapter(settings)
        reopened = create_golden_mcp_adapter(settings)

    expected = (
        "harness_workflow_create_v1",
        "harness_advisory_record_v1",
        "harness_prompt_revision_record_v1",
        "text2env_compile_v1_0_0",
        "text2env_replay_v1_0_0",
        "text2env_validate_v2_0_0",
    )
    assert tuple(tool.name for tool in first.list_tools(registry)) == expected
    assert tuple(tool.name for tool in reopened.list_tools(registry)) == expected
    settings_path = tmp_path / "golden-mcp-settings.json"
    settings_path.write_text(settings.model_dump_json(), encoding="utf-8")
    assert load_mcp_settings(settings_path) == settings
    with pytest.raises(ValueError, match="not compile-only"):
        load_compile_mcp_settings(settings_path)
    with pytest.raises(TypeError, match="exact Path"):
        load_mcp_settings("not-a-path")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact GoldenMCPServerSettings"):
        create_golden_mcp_adapter(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty exact string"):
        create_golden_mcp_adapter(settings.model_copy(update={"principal_id": ""}))

    with pytest.raises(ValueError, match="must not be empty"):
        create_golden_mcp_adapter(settings.model_copy(update={"qualification_cas_roots": ()}))
    missing_digest_root = tmp_path / "qualification-without-digest-root"
    missing_digest_root.mkdir()
    with pytest.raises(ValueError, match="has no sha256 directory"):
        create_golden_mcp_adapter(
            settings.model_copy(update={"qualification_cas_roots": (missing_digest_root,)})
        )
    empty_cas = tmp_path / "empty-qualification-cas"
    (empty_cas / "sha256").mkdir(parents=True)
    with pytest.raises(ValueError, match="qualification CAS is empty"):
        create_golden_mcp_adapter(
            settings.model_copy(update={"qualification_cas_roots": (empty_cas,)})
        )

    invalid_members: list[Path] = []
    symlink_cas = tmp_path / "symlink-qualification-cas"
    (symlink_cas / "sha256" / "aa").mkdir(parents=True)
    symlink_member = symlink_cas / "sha256" / "aa" / ("a" * 64)
    symlink_member.symlink_to(compile_settings.external_catalog_roots[0] / "catalog.json")
    invalid_members.append(symlink_cas)
    directory_cas = tmp_path / "directory-qualification-cas"
    (directory_cas / "sha256" / "aa" / ("a" * 64)).mkdir(parents=True)
    invalid_members.append(directory_cas)
    short_name_cas = tmp_path / "short-name-qualification-cas"
    short_member = short_name_cas / "sha256" / "aa" / "short"
    short_member.parent.mkdir(parents=True)
    short_member.write_bytes(b"short")
    invalid_members.append(short_name_cas)
    wrong_prefix_cas = tmp_path / "wrong-prefix-qualification-cas"
    wrong_prefix_member = wrong_prefix_cas / "sha256" / "bb" / ("a" * 64)
    wrong_prefix_member.parent.mkdir(parents=True)
    wrong_prefix_member.write_bytes(b"wrong-prefix")
    invalid_members.append(wrong_prefix_cas)
    for invalid_cas in invalid_members:
        with pytest.raises(ValueError, match="has an invalid member"):
            create_golden_mcp_adapter(
                settings.model_copy(update={"qualification_cas_roots": (invalid_cas,)})
            )

    mismatched_cas = tmp_path / "mismatched-qualification-cas"
    mismatched_member = mismatched_cas / "sha256" / "aa" / ("a" * 64)
    mismatched_member.parent.mkdir(parents=True)
    mismatched_member.write_bytes(b"digest mismatch")
    with pytest.raises(ValueError, match="member digest mismatch"):
        create_golden_mcp_adapter(
            settings.model_copy(update={"qualification_cas_roots": (mismatched_cas,)})
        )
