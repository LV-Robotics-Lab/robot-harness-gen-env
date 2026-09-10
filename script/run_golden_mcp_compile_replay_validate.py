#!/usr/bin/env python3
"""Run a real qualified text2env Golden workflow through MCP stdio."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import date
from importlib.metadata import version
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from self_improving.harness.mcp_assembly import GoldenMCPServerSettings
from self_improving.harness.schemas.common import ArtifactRef, RunState
from self_improving.harness.schemas.text2env import (
    CompileConfig,
    RuntimeConfig,
    Text2EnvCompileInput,
    Text2EnvReplayInput,
)
from self_improving.harness.schemas.text2env_validate_v2 import (
    Text2EnvValidateV2Input,
    Text2EnvValidateV2Output,
)
from self_improving.harness.schemas.workflow_operation import (
    ToolResultV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.trusted_state import TrustedWorldState

_REQUEST = "Place a can on top of a plate."
_TOOLS = (
    "harness_workflow_create_v1",
    "text2env_compile_v1_0_0",
    "text2env_replay_v1_0_0",
    "text2env_validate_v2_0_0",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--robotwin-root", type=Path, required=True)
    parser.add_argument("--asset-catalog", type=Path, required=True)
    parser.add_argument("--registry-document", type=Path, required=True)
    parser.add_argument("--qualification-cas", type=Path, required=True)
    parser.add_argument("--interpreter", type=Path, required=True)
    parser.add_argument("--runtime-capability", type=Path, required=True)
    parser.add_argument("--media-launcher", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--delegated-cgroup-root", type=Path, required=True)
    return parser.parse_args()


def _canonical_bytes(value: object) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _artifact(path: Path, *, name: str, schema_version: str) -> ArtifactRef:
    payload = path.resolve(strict=True).read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        sha256=digest,
        bytes=len(payload),
        media_type="application/json",
        schema_version=schema_version,
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


def _database_manifest(output: Path) -> dict[str, list[list[object]]]:
    paths = {
        "golden": output / "golden.sqlite3",
        "compile": output / "compile-state/harness.sqlite3",
        "replay": output / "replay-state/harness.sqlite3",
        "validate": output / "validate-state/harness.sqlite3",
    }
    return {name: [list(row) for row in _database_rows(path)] for name, path in paths.items()}


def _cas_manifest(root: Path) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.glob("sha256/*/*"))
        if path.is_file()
    )


def _server_parameters(repository: Path, settings_path: Path) -> StdioServerParameters:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/home/jingxiang"),
        "LANG": os.environ.get("LANG", "C"),
        "LC_ALL": os.environ.get("LC_ALL", "C"),
        "PYTHONPATH": str(repository),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return StdioServerParameters(
        command=sys.executable,
        args=(
            "-m",
            "self_improving.harness.mcp_stdio",
            "--settings",
            str(settings_path),
        ),
        cwd=str(repository),
        env=environment,
    )


async def _read_json(client: Client, workflow_id: str, ref: ArtifactRef, model):
    resource = await client.read_resource(
        f"harness://workflows/{workflow_id}/artifacts/{ref.sha256}"
    )
    if len(resource.contents) != 1 or not isinstance(
        resource.contents[0], types.TextResourceContents
    ):
        raise RuntimeError(f"workflow artifact is not JSON text: {ref.sha256}")
    payload = resource.contents[0].text.encode()
    if len(payload) != ref.bytes or hashlib.sha256(payload).hexdigest() != ref.sha256:
        raise RuntimeError(f"workflow artifact content identity failed: {ref.sha256}")
    return model.model_validate_json(payload)


def _command(snapshot: dict[str, object], *, index: int, parameters: dict) -> dict:
    return {
        "schema_version": "harness.run_command_request.v1",
        "command_id": f"44000000-0000-4000-8000-{index:012d}",
        "workflow_run_id": snapshot["workflow_run_id"],
        "idempotency_key": f"golden-mcp-real-step-{index}",
        "expected_workflow_revision": snapshot["revision"],
        "expected_state_sha256": snapshot["state_sha256"],
        "expected_receipt_head_sha256": snapshot["receipt_head"]["sha256"],
        "parameters": parameters,
    }


async def _first_process(
    server: StdioServerParameters,
    *,
    catalog: ArtifactRef,
    output: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        try:
            async with Client(
                stdio_client(server, errlog=errlog),
                raise_exceptions=True,
                cache=None,
            ) as client:
                if client.protocol_version != "2026-07-28":
                    raise RuntimeError(f"unexpected MCP protocol: {client.protocol_version}")
                listed = await client.list_tools()
                names = tuple(tool.name for tool in listed.tools)
                if names != _TOOLS:
                    raise RuntimeError(f"unexpected MCP tool catalog: {names}")
                start_arguments = {
                    "schema_version": "harness.mcp_workflow_create_request.v1",
                    "idempotency_key": "golden-mcp-real-start-001",
                    "workspace": "ephemeral",
                    "requested_profile": "legacy.robotwin_validation@2",
                    "initial_user_facts": [
                        {
                            "key": "request.objective",
                            "value": _REQUEST,
                            "observed_at": "2026-09-10T00:00:00Z",
                            "valid_until": None,
                        }
                    ],
                }
                created = await client.call_tool(_TOOLS[0], start_arguments)
                if created.is_error or created.structured_content["revision"] != 0:
                    raise RuntimeError(f"workflow create failed: {created}")
                workflow_id = str(created.structured_content["workflow_run_id"])
                compile_input = Text2EnvCompileInput(
                    request=_REQUEST,
                    seed=7,
                    asset_catalog=catalog,
                    config=CompileConfig(generate_missing_assets=False),
                )
                compiled = await client.call_tool(
                    _TOOLS[1],
                    _command(
                        created.structured_content,
                        index=1,
                        parameters=compile_input.model_dump(mode="json"),
                    ),
                )
                if compiled.is_error or compiled.structured_content["committed_revision"] != 1:
                    raise RuntimeError(f"compile failed: {compiled}")
                revision_one_resource = await client.read_resource(
                    f"harness://workflows/{workflow_id}"
                )
                revision_one = json.loads(revision_one_resource.contents[0].text)
                state_one = await _read_json(
                    client,
                    workflow_id,
                    ArtifactRef.model_validate(revision_one["state_ref"]),
                    TrustedWorldState,
                )
                package = next(
                    fact.value for fact in state_one.facts if fact.key == "environment.package"
                )
                replay_input = Text2EnvReplayInput(
                    environment_package=package,
                    runtime_config=RuntimeConfig(
                        precheck_steps=0,
                        settle_steps=900,
                        contact_window_steps=120,
                        video_frames=120,
                        fps=12,
                    ),
                )
                replayed = await client.call_tool(
                    _TOOLS[2],
                    _command(
                        revision_one,
                        index=2,
                        parameters=replay_input.model_dump(mode="json"),
                    ),
                )
                if replayed.is_error or replayed.structured_content["committed_revision"] != 2:
                    raise RuntimeError(f"replay failed: {replayed}")
                revision_two_resource = await client.read_resource(
                    f"harness://workflows/{workflow_id}"
                )
                revision_two = json.loads(revision_two_resource.contents[0].text)
                prepared = await client.read_resource(
                    f"harness://workflows/{workflow_id}/skill-inputs/text2env.validate/2.0.0"
                )
                validate_input = Text2EnvValidateV2Input.model_validate_json(
                    prepared.contents[0].text
                )
                validate_arguments = _command(
                    revision_two,
                    index=3,
                    parameters=validate_input.model_dump(mode="json"),
                )
                validated = await client.call_tool(_TOOLS[3], validate_arguments)
                if validated.is_error or validated.structured_content["committed_revision"] != 3:
                    raise RuntimeError(f"validate failed: {validated}")
                revision_three_resource = await client.read_resource(
                    f"harness://workflows/{workflow_id}"
                )
                revision_three = json.loads(revision_three_resource.contents[0].text)
                receipt = await _read_json(
                    client,
                    workflow_id,
                    ArtifactRef.model_validate(revision_three["receipt_head"]),
                    WorkflowOperationReceiptV2,
                )
                result = await _read_json(
                    client,
                    workflow_id,
                    receipt.tool_result,
                    ToolResultV2,
                )
                validate_output = Text2EnvValidateV2Output.model_validate(result.typed_output)
                if (
                    not validate_output.publishable
                    or validate_output.validation_status.value != "pass"
                ):
                    raise RuntimeError(f"validate did not publish a pass: {validate_output}")
                replay_receipt = await _read_json(
                    client,
                    workflow_id,
                    receipt.predecessor_receipt,
                    WorkflowOperationReceiptV2,
                )
                replay_result = await _read_json(
                    client,
                    workflow_id,
                    replay_receipt.tool_result,
                    ToolResultV2,
                )
                replay_run = await _read_json(
                    client,
                    workflow_id,
                    replay_result.run_state,
                    RunState,
                )
                videos = tuple(
                    artifact
                    for artifact in replay_run.artifacts
                    if artifact.media_type == "video/mp4"
                )
                if len(videos) != 1:
                    raise RuntimeError(f"expected exactly one replay video, got {len(videos)}")
                video = videos[0]
                media = await client.read_resource(
                    f"harness://workflows/{workflow_id}/artifacts/{video.sha256}"
                )
                if len(media.contents) != 1 or not isinstance(
                    media.contents[0], types.BlobResourceContents
                ):
                    raise RuntimeError("replay video was not returned as an MCP blob")
                video_bytes = base64.b64decode(media.contents[0].blob, validate=True)
                if (
                    len(video_bytes) != video.bytes
                    or hashlib.sha256(video_bytes).hexdigest() != video.sha256
                ):
                    raise RuntimeError("MCP replay video content identity failed")
                view = output / "view"
                view.mkdir()
                video_path = view / f"{video.name}-{video.sha256[:12]}.mp4"
                video_path.write_bytes(video_bytes)
        except BaseException as error:
            errlog.flush()
            errlog.seek(0)
            raise RuntimeError(f"first MCP stdio process failed:\n{errlog.read()}") from error
        errlog.seek(0)
        (output / "mcp-a.stderr.log").write_text(errlog.read(), encoding="utf-8")
    return (
        start_arguments,
        validate_arguments,
        {
            "protocol_version": client.protocol_version,
            "tools": list(_TOOLS),
            "workflow": revision_three,
            "operations": [
                compiled.structured_content,
                replayed.structured_content,
                validated.structured_content,
            ],
            "validate_output": validate_output.model_dump(mode="json"),
            "video": {**video.model_dump(mode="json"), "path": str(video_path)},
        },
    )


async def _restart_process(
    server: StdioServerParameters,
    *,
    start_arguments: dict[str, object],
    validate_arguments: dict[str, object],
) -> dict[str, object]:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        try:
            async with Client(
                stdio_client(server, errlog=errlog),
                raise_exceptions=True,
                cache=None,
            ) as client:
                listed = await client.list_tools()
                if tuple(tool.name for tool in listed.tools) != _TOOLS:
                    raise RuntimeError("MCP restart tool catalog changed")
                created = await client.call_tool(_TOOLS[0], start_arguments)
                if created.is_error or created.structured_content["revision"] != 3:
                    raise RuntimeError(f"workflow restart recovery failed: {created}")
                repeated = await client.call_tool(_TOOLS[3], validate_arguments)
                if repeated.is_error or repeated.structured_content["committed_revision"] != 3:
                    raise RuntimeError(f"validate restart recovery failed: {repeated}")
        except BaseException as error:
            errlog.flush()
            errlog.seek(0)
            raise RuntimeError(f"second MCP stdio process failed:\n{errlog.read()}") from error
        errlog.seek(0)
        stderr = errlog.read()
    return {
        "workflow": created.structured_content,
        "validate_operation": repeated.structured_content,
        "stderr": stderr,
    }


async def _run(
    server: StdioServerParameters,
    *,
    catalog: ArtifactRef,
    output: Path,
) -> dict[str, object]:
    with anyio.fail_after(1900):
        start_arguments, validate_arguments, first = await _first_process(
            server,
            catalog=catalog,
            output=output,
        )
    databases_before = _database_manifest(output)
    cas_before = _cas_manifest(output / "compile-state/cas")
    with anyio.fail_after(120):
        restarted = await _restart_process(
            server,
            start_arguments=start_arguments,
            validate_arguments=validate_arguments,
        )
    databases_after = _database_manifest(output)
    cas_after = _cas_manifest(output / "compile-state/cas")
    if restarted["validate_operation"] != first["operations"][2]:
        raise RuntimeError("restart returned a different validate operation")
    if databases_before != databases_after:
        raise RuntimeError("restart changed durable database row counts")
    if cas_before != cas_after:
        raise RuntimeError("restart changed CAS contents")
    restarted["stderr_sha256"] = hashlib.sha256(restarted.pop("stderr").encode()).hexdigest()
    return {
        **first,
        "restart": restarted,
        "database_rows": databases_after,
        "cas": {
            "objects": len(cas_after),
            "bytes": sum(item[1] for item in cas_after),
            "manifest_sha256": hashlib.sha256(_canonical_bytes(cas_after)).hexdigest(),
        },
    }


def main() -> None:
    if version("mcp") != "2.2.0":
        raise RuntimeError(f"expected mcp==2.2.0, found {version('mcp')}")
    args = _arguments()
    output = args.output_root.expanduser().absolute()
    if output.exists():
        raise RuntimeError("output-root must be new")
    output.mkdir(parents=True)
    repository = args.repository.resolve(strict=True)
    robotwin = args.robotwin_root.resolve(strict=True)
    catalog_path = args.asset_catalog.resolve(strict=True)
    registry_path = args.registry_document.resolve(strict=True)
    qualification_cas = args.qualification_cas.resolve(strict=True)
    settings = GoldenMCPServerSettings(
        schema_version="harness.golden_mcp_server_settings.v1",
        principal_id="evidence/golden-mcp-real",
        registry_snapshot=_artifact(
            registry_path,
            name="compile-replay-validate-registry",
            schema_version="harness.registry_snapshot.v1",
        ),
        compile_state_root=output / "compile-state",
        external_catalog_roots=(catalog_path.parent,),
        allowed_asset_roots=(robotwin,),
        admission_date=date(2026, 9, 10),
        asset_library_root=output / "asset-library",
        golden_database_path=output / "golden.sqlite3",
        qualification_cas_roots=(qualification_cas,),
        replay_state_root=output / "replay-state",
        replay_qualification_bundle_root=(
            repository / "self_improving/harness/qualified_skills/text2env.replay/1.0.0"
        ),
        validate_state_root=output / "validate-state",
        validate_qualification_bundle_root=(
            repository / "self_improving/harness/qualified_skills/text2env.validate/2.0.0"
        ),
        implementation_root=repository,
        scene_gen_root=repository / "scene_gen",
        ledger_contract_root=(
            repository / "self_improving/asset_pipeline/active/1_asset_reuse/lib"
        ),
        interpreter=args.interpreter.resolve(strict=True),
        runtime_runner=repository / "script/run_scene_runtime.py",
        runtime_module_root=repository,
        runtime_capability_path=args.runtime_capability.resolve(strict=True),
        media_launcher=args.media_launcher.resolve(strict=True),
        static_ffmpeg=args.ffmpeg.resolve(strict=True),
        delegated_cgroup_root=args.delegated_cgroup_root.resolve(strict=True),
        runtime_timeout_seconds=1800,
        capability_timeout_seconds=30,
    )
    settings_path = output / "mcp-settings.json"
    settings_path.write_bytes(_canonical_bytes(settings))
    catalog = _artifact(
        catalog_path,
        name="input_asset_catalog",
        schema_version="robotwin.asset_catalog.v1",
    )

    async def execute() -> dict[str, object]:
        return await _run(
            _server_parameters(repository, settings_path),
            catalog=catalog,
            output=output,
        )

    result = anyio.run(execute)
    summary = {
        "schema_version": "golden.mcp_compile_replay_validate_evidence.v1",
        "implementation_commit": _git_head(repository),
        "request": _REQUEST,
        **result,
        "limits": {
            "genesis_native": False,
            "promotion_executed": False,
            "robot_policy_ready": False,
            "external_codex_planner_executed": False,
        },
    }
    (output / "summary.json").write_bytes(_canonical_bytes(summary))


def _git_head(repository: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    main()
