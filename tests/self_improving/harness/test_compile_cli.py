from __future__ import annotations

import inspect
import re
import subprocess
import sys
import tomllib
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

import self_improving.harness.compile_cli as cli
from self_improving.harness.application import (
    CompileApplicationConfigurationError,
    CompileApplicationSettings,
    ExternalCatalogError,
)
from self_improving.harness.qualification import QualificationBundleError
from self_improving.harness.registry import (
    RegistryRegistrationError,
    RunPersistenceError,
)
from self_improving.harness.schemas import Blocker, Event, RunState, RunStatus

RUN_ID = UUID("00000000-0000-4000-8000-000000000001")
STARTED_AT = datetime(2026, 8, 31, 1, 2, 3, tzinfo=timezone.utc)
ENDED_AT = datetime(2026, 8, 31, 1, 2, 4, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[3]


def _succeeded_state() -> RunState:
    return RunState(
        run_id=RUN_ID,
        invocation_digest="a" * 64,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        status=RunStatus.SUCCEEDED,
        attempt=1,
        max_attempts=1,
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
        events=(
            Event(
                seq=1,
                timestamp=STARTED_AT,
                stage="preflight",
                attempt=1,
                from_status=None,
                to_status=RunStatus.RUNNING,
                artifact_refs=(),
            ),
            Event(
                seq=2,
                timestamp=ENDED_AT,
                stage="complete",
                attempt=1,
                from_status=RunStatus.RUNNING,
                to_status=RunStatus.SUCCEEDED,
                artifact_refs=(),
            ),
        ),
        artifacts=(),
        output={"ok": True},
        blocker=None,
    )


def _refused_state(status: RunStatus) -> RunState:
    code = "T2E_ASSET_MISSING" if status == RunStatus.BLOCKED else "HARN_INTERNAL"
    return RunState(
        run_id=RUN_ID,
        invocation_digest="b" * 64,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        status=status,
        attempt=1,
        max_attempts=1,
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
        events=(
            Event(
                seq=1,
                timestamp=STARTED_AT,
                stage="preflight",
                attempt=1,
                from_status=None,
                to_status=RunStatus.RUNNING,
                artifact_refs=(),
            ),
            Event(
                seq=2,
                timestamp=ENDED_AT,
                stage="compile",
                attempt=1,
                from_status=RunStatus.RUNNING,
                to_status=status,
                artifact_refs=(),
            ),
        ),
        artifacts=(),
        output=None,
        blocker=Blocker(
            code=code,
            message="compile did not produce a package",
            stage="compile",
            retryable=False,
            details={},
            unknowns=(),
            artifact_refs=(),
        ),
    )


def _running_state() -> RunState:
    return RunState(
        run_id=RUN_ID,
        invocation_digest="c" * 64,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        status=RunStatus.RUNNING,
        attempt=1,
        max_attempts=1,
        started_at=STARTED_AT,
        ended_at=None,
        events=(
            Event(
                seq=1,
                timestamp=STARTED_AT,
                stage="preflight",
                attempt=1,
                from_status=None,
                to_status=RunStatus.RUNNING,
                artifact_refs=(),
            ),
        ),
        artifacts=(),
        output=None,
        blocker=None,
    )


def test_success_forwards_explicit_configuration_and_writes_one_canonical_run_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    state = _succeeded_state()
    calls: dict[str, object] = {}

    class FakeApplication:
        def compile(self, **kwargs: object) -> RunState:
            calls["compile"] = kwargs
            return state

    def fake_factory(settings: CompileApplicationSettings) -> FakeApplication:
        calls["settings"] = settings
        return FakeApplication()

    monkeypatch.setattr(cli, "create_compile_application", fake_factory)
    state_root = tmp_path / "state"
    catalogs_a = tmp_path / "catalogs-a"
    catalogs_b = tmp_path / "catalogs-b"
    assets_a = tmp_path / "assets-a"
    assets_b = tmp_path / "assets-b"
    production_assets = tmp_path / "project" / "production-assets"
    catalog_path = catalogs_b / "catalog.json"

    exit_code = cli.main(
        [
            "--state-root",
            str(state_root),
            "--trusted-catalog-root",
            str(catalogs_a),
            "--trusted-catalog-root",
            str(catalogs_b),
            "--allowed-asset-root",
            str(assets_a),
            "--allowed-asset-root",
            str(assets_b),
            "--asset-library-root",
            str(production_assets),
            "--admission-date",
            "2026-08-31",
            "--catalog-path",
            str(catalog_path),
            "--request",
            "put the can on the plate",
            "--seed",
            "77",
            "--generate-missing",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls == {
        "settings": CompileApplicationSettings(
            state_root=state_root,
            external_catalog_roots=(catalogs_a, catalogs_b),
            allowed_asset_roots=(assets_a, assets_b),
            admission_date=date(2026, 8, 31),
            asset_library_root=production_assets,
        ),
        "compile": {
            "request": "put the can on the plate",
            "seed": 77,
            "asset_catalog_path": catalog_path,
            "generate_missing_assets": True,
        },
    }
    assert captured.err == ""
    assert captured.out == (
        '{"artifacts":[],"attempt":1,"blocker":null,'
        '"ended_at":"2026-08-31T01:02:04Z","events":['
        '{"artifact_refs":[],"attempt":1,"from_status":null,"seq":1,'
        '"stage":"preflight","timestamp":"2026-08-31T01:02:03Z",'
        '"to_status":"running"},'
        '{"artifact_refs":[],"attempt":1,"from_status":"running","seq":2,'
        '"stage":"complete","timestamp":"2026-08-31T01:02:04Z",'
        '"to_status":"succeeded"}],"invocation_digest":"'
        + "a"
        * 64
        + '","max_attempts":1,"output":{"ok":true},'
        '"run_id":"00000000-0000-4000-8000-000000000001",'
        '"skill_id":"text2env.compile","skill_version":"1.0.0",'
        '"started_at":"2026-08-31T01:02:03Z","status":"succeeded"}\n'
    )


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    ((RunStatus.BLOCKED, 10), (RunStatus.FAILED, 20)),
)
def test_typed_terminal_refusals_are_json_and_have_distinct_exit_codes(
    tmp_path: Path,
    monkeypatch,
    capsys,
    status: RunStatus,
    expected_exit: int,
) -> None:
    state = _refused_state(status)

    class FakeApplication:
        def compile(self, **kwargs: object) -> RunState:
            return state

    monkeypatch.setattr(cli, "create_compile_application", lambda settings: FakeApplication())

    exit_code = cli.main(
        [
            "--state-root",
            str(tmp_path / "state"),
            "--trusted-catalog-root",
            str(tmp_path / "catalogs"),
            "--allowed-asset-root",
            str(tmp_path / "assets"),
            "--admission-date",
            "2026-08-31",
            "--catalog-path",
            str(tmp_path / "catalogs/catalog.json"),
            "--request",
            "put the can on the plate",
            "--seed",
            "77",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == expected_exit
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert f'"status":"{status.value}"' in captured.out
    assert captured.out == captured.out.strip() + "\n"


def test_missing_required_input_returns_usage_exit_without_calling_application(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    def unexpected_factory(settings: CompileApplicationSettings) -> None:
        pytest.fail("invalid CLI input must not assemble the application")

    monkeypatch.setattr(cli, "create_compile_application", unexpected_factory)

    exit_code = cli.main(
        [
            "--state-root",
            str(tmp_path / "state"),
            "--trusted-catalog-root",
            str(tmp_path / "catalogs"),
            "--admission-date",
            "2026-08-31",
            "--catalog-path",
            str(tmp_path / "catalogs/catalog.json"),
            "--request",
            "put the can on the plate",
            "--seed",
            "77",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-compile: input error: the following arguments are required: "
        "--allowed-asset-root\n"
    )


@pytest.mark.parametrize(
    ("option", "value"),
    (
        ("--admission-date", "20260831"),
        ("--admission-date", "31-08-2026"),
        ("--request", "no"),
        ("--request", "x" * 2001),
        ("--seed", "-1"),
        ("--seed", "2147483648"),
        ("--seed", "seven"),
    ),
)
def test_invalid_scalar_inputs_are_configuration_errors_before_assembly(
    tmp_path: Path,
    monkeypatch,
    capsys,
    option: str,
    value: str,
) -> None:
    def unexpected_factory(settings: CompileApplicationSettings) -> None:
        pytest.fail("invalid CLI input must not assemble the application")

    monkeypatch.setattr(cli, "create_compile_application", unexpected_factory)
    values = {
        "--admission-date": "2026-08-31",
        "--request": "put the can on the plate",
        "--seed": "77",
    }
    values[option] = value

    exit_code = cli.main(
        [
            "--state-root",
            str(tmp_path / "state"),
            "--trusted-catalog-root",
            str(tmp_path / "catalogs"),
            "--allowed-asset-root",
            str(tmp_path / "assets"),
            "--admission-date",
            values["--admission-date"],
            "--catalog-path",
            str(tmp_path / "catalogs/catalog.json"),
            "--request",
            values["--request"],
            "--seed",
            values["--seed"],
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err.startswith("robot-harness-compile: input error: argument ")


@pytest.mark.parametrize(
    "error",
    (
        CompileApplicationConfigurationError("hidden=/srv/private/state"),
        ExternalCatalogError("hidden=/srv/private/catalog.json"),
        QualificationBundleError("missing_file", "hidden=/opt/package/qualification"),
        RegistryRegistrationError("hidden=/opt/package/descriptor"),
    ),
)
def test_configuration_trust_and_qualification_errors_are_sanitized_exit_78(
    tmp_path: Path,
    monkeypatch,
    capsys,
    error: Exception,
) -> None:
    def failing_factory(settings: CompileApplicationSettings) -> None:
        raise error

    monkeypatch.setattr(cli, "create_compile_application", failing_factory)

    exit_code = cli.main(
        [
            "--state-root",
            str(tmp_path / "state"),
            "--trusted-catalog-root",
            str(tmp_path / "catalogs"),
            "--allowed-asset-root",
            str(tmp_path / "assets"),
            "--admission-date",
            "2026-08-31",
            "--catalog-path",
            str(tmp_path / "catalogs/catalog.json"),
            "--request",
            "put the can on the plate",
            "--seed",
            "77",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-compile: configuration/input/trust/qualification error: "
        f"{type(error).__name__}\n"
    )
    assert "/srv/private" not in captured.err
    assert "/opt/package" not in captured.err


def test_run_persistence_error_remains_identifiable_without_leaking_its_cause(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    failure = RunPersistenceError(
        operation="terminal state",
        run_id=RUN_ID,
        state=_succeeded_state(),
        cause=OSError("token=do-not-print path=/srv/private/database"),
    )

    class FakeApplication:
        def compile(self, **kwargs: object) -> RunState:
            raise failure

    monkeypatch.setattr(cli, "create_compile_application", lambda settings: FakeApplication())

    exit_code = cli.main(
        [
            "--state-root",
            str(tmp_path / "state"),
            "--trusted-catalog-root",
            str(tmp_path / "catalogs"),
            "--allowed-asset-root",
            str(tmp_path / "assets"),
            "--admission-date",
            "2026-08-31",
            "--catalog-path",
            str(tmp_path / "catalogs/catalog.json"),
            "--request",
            "put the can on the plate",
            "--seed",
            "77",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 74
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-compile: durable persistence error: operation=terminal state "
        "run_id=00000000-0000-4000-8000-000000000001 cause=OSError\n"
    )
    assert "do-not-print" not in captured.err
    assert "/srv/private" not in captured.err


@pytest.mark.parametrize("error", (OSError("hidden=/var/db"), RuntimeError("api-key")))
def test_storage_and_internal_adapter_errors_are_sanitized_exit_74(
    tmp_path: Path,
    monkeypatch,
    capsys,
    error: Exception,
) -> None:
    def failing_factory(settings: CompileApplicationSettings) -> None:
        raise error

    monkeypatch.setattr(cli, "create_compile_application", failing_factory)

    exit_code = cli.main(
        [
            "--state-root",
            str(tmp_path / "state"),
            "--trusted-catalog-root",
            str(tmp_path / "catalogs"),
            "--allowed-asset-root",
            str(tmp_path / "assets"),
            "--admission-date",
            "2026-08-31",
            "--catalog-path",
            str(tmp_path / "catalogs/catalog.json"),
            "--request",
            "put the can on the plate",
            "--seed",
            "77",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 74
    assert captured.out == ""
    assert captured.err == (
        f"robot-harness-compile: durable/storage/internal adapter error: {type(error).__name__}\n"
    )
    assert "hidden" not in captured.err
    assert "api-key" not in captured.err


def test_help_is_returned_without_system_exit_and_exposes_only_the_fixed_surface(
    capsys,
) -> None:
    exit_code = cli.main(["--help"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert set(re.findall(r"--[a-z-]+", captured.out)) == {
        "--admission-date",
        "--allowed-asset-root",
        "--asset-library-root",
        "--catalog-path",
        "--generate-missing",
        "--help",
        "--request",
        "--seed",
        "--state-root",
        "--trusted-catalog-root",
    }
    assert list(inspect.signature(cli.main).parameters) == ["argv"]
    assert "qualification" not in captured.out
    assert "handler" not in captured.out
    assert "descriptor" not in captured.out


def test_nonterminal_application_result_is_not_published_as_a_cli_result(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class FakeApplication:
        def compile(self, **kwargs: object) -> RunState:
            return _running_state()

    monkeypatch.setattr(cli, "create_compile_application", lambda settings: FakeApplication())

    exit_code = cli.main(
        [
            "--state-root",
            str(tmp_path / "state"),
            "--trusted-catalog-root",
            str(tmp_path / "catalogs"),
            "--allowed-asset-root",
            str(tmp_path / "assets"),
            "--admission-date",
            "2026-08-31",
            "--catalog-path",
            str(tmp_path / "catalogs/catalog.json"),
            "--request",
            "put the can on the plate",
            "--seed",
            "77",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 74
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-compile: durable/storage/internal adapter error: _TerminalStateError\n"
    )


def test_module_entrypoint_preserves_exit_78_without_a_traceback() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "self_improving.harness.compile_cli"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr.startswith(
        "robot-harness-compile: input error: the following arguments are required:"
    )
    assert "Traceback" not in completed.stderr


def test_distribution_retires_legacy_compile_console_entrypoint() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "robot-harness-compile" not in configuration["project"]["scripts"]
