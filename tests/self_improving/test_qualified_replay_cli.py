from __future__ import annotations

import json
import os
import re
import runpy
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from self_improving import qualified_replay_cli as cli
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import EventPage, StoredRunEvent
from self_improving.harness.events import RunEvent
from self_improving.harness.media_sandbox import SandboxError, SandboxReason
from self_improving.harness.qualification import QualificationBundleError
from self_improving.harness.registry import RunPersistenceError
from self_improving.harness.replay_application import (
    ReplayApplicationConfigurationError,
    ReplayApplicationSettings,
)
from self_improving.harness.runtime_executor import RuntimeExecutorConfigurationError
from self_improving.harness.schemas import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    Event,
    Invocation,
    RunState,
    RunStatus,
    Text2EnvReplayInput,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "script" / "run_qualified_replay.py"

RUN_ID = UUID("79000000-0000-4000-8000-000000000001")
STARTED_AT = datetime(2026, 9, 2, 1, 2, 3, tzinfo=timezone.utc)
ENDED_AT = datetime(2026, 9, 2, 1, 2, 4, tzinfo=timezone.utc)


def _ref(name: str, digest: str, schema_version: str) -> ArtifactRef:
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=17,
        schema_version=schema_version,
    )


def _fixed_input() -> Text2EnvReplayInput:
    return Text2EnvReplayInput.model_validate(
        {
            "environment_package": {
                "package_id": "b" * 64,
                "route_id": "text2env",
                "producer_skill_ref": "text2env.compile@1.0.0",
                "seed": 7,
                "scene_spec_sha256": "a" * 64,
                "resolved_scene_sha256": "b" * 64,
                "asset_catalog": _ref(
                    "asset_catalog", "c" * 64, "robotwin.asset_catalog.v1"
                ).model_dump(mode="json"),
                "package_manifest": _ref(
                    "package_manifest", "d" * 64, "robotwin.generated_scene_package.v1"
                ).model_dump(mode="json"),
            },
            "runtime_config": {
                "precheck_steps": 0,
                "settle_steps": 900,
                "contact_window_steps": 120,
                "video_frames": 120,
                "fps": 12,
            },
        }
    )


def _succeeded_state() -> RunState:
    events = (
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
    )
    runtime_evidence = _ref("runtime_evidence", "f" * 64, "robotwin.scene_runtime_evidence.v2")
    return RunState(
        run_id=RUN_ID,
        invocation_digest="e" * 64,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        status=RunStatus.SUCCEEDED,
        attempt=1,
        max_attempts=2,
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
        events=events,
        artifacts=(runtime_evidence,),
        output={
            "runtime_evidence": runtime_evidence.model_dump(mode="json"),
            "replay_artifacts": [runtime_evidence.model_dump(mode="json")],
        },
        blocker=None,
    )


def _preflight_terminal_state(status: RunStatus) -> RunState:
    return RunState(
        run_id=RUN_ID,
        invocation_digest=None,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        status=status,
        attempt=0,
        max_attempts=0,
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
        events=(
            Event(
                seq=1,
                timestamp=STARTED_AT,
                stage="preflight",
                attempt=0,
                from_status=None,
                to_status=RunStatus.RUNNING,
                artifact_refs=(),
            ),
            Event(
                seq=2,
                timestamp=ENDED_AT,
                stage="preflight",
                attempt=0,
                from_status=RunStatus.RUNNING,
                to_status=status,
                artifact_refs=(),
            ),
        ),
        artifacts=(),
        output=None,
        blocker=Blocker(
            code="PREFLIGHT_DENIED",
            message="hidden=/private/deployment/token",
            stage="preflight",
            retryable=False,
            details={"locator": "/private/deployment/token"},
            unknowns=(),
            artifact_refs=(),
        ),
    )


def _bound_terminal_state(status: RunStatus) -> RunState:
    return RunState(
        run_id=RUN_ID,
        invocation_digest="e" * 64,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        status=status,
        attempt=1,
        max_attempts=2,
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
                stage="replay",
                attempt=1,
                from_status=RunStatus.RUNNING,
                to_status=status,
                artifact_refs=(),
            ),
        ),
        artifacts=(),
        output=None,
        blocker=Blocker(
            code="RUNTIME_TERMINAL",
            message="hidden=/private/deployment/token",
            stage="replay",
            retryable=False,
            details={"locator": "/private/deployment/token"},
            unknowns=(),
            artifact_refs=(),
        ),
    )


def _persisted_events(state: RunState) -> EventPage:
    stored = tuple(
        StoredRunEvent(
            event_id=index,
            envelope=RunEvent(
                run_id=state.run_id,
                skill_id=state.skill_id,
                skill_version=state.skill_version,
                event=event,
            ),
        )
        for index, event in enumerate(state.events, start=1)
    )
    return EventPage(events=stored, last_event_id=len(stored), has_more=False)


def _settings_document() -> dict[str, object]:
    return {
        "schema_version": "harness.qualified_replay_fixed_case_launch.v1",
        "state_root": "/deployment/state",
        "evidence_artifact_root": "/deployment/cas",
        "allowed_asset_roots": ["/deployment/assets"],
        "interpreter": "/deployment/python",
        "runtime_capability_path": "/deployment/runtime-capability.json",
        "media_launcher": "/deployment/media-launcher",
        "static_ffmpeg": "/deployment/ffmpeg",
        "delegated_cgroup_root": "/sys/fs/cgroup/delegated",
        "runtime_timeout_seconds": 900.0,
        "capability_timeout_seconds": 30.0,
    }


def _materialized_settings_document(tmp_path: Path) -> dict[str, object]:
    document = _settings_document()
    evidence = tmp_path / "evidence-cas"
    assets = tmp_path / "assets"
    cgroup = tmp_path / "delegated-cgroup"
    state_parent = tmp_path / "state-parent"
    tools = tmp_path / "operator-tools"
    for directory in (evidence, assets, cgroup, state_parent, tools):
        directory.mkdir()
    files: dict[str, Path] = {}
    for field in (
        "interpreter",
        "runtime_capability_path",
        "media_launcher",
        "static_ffmpeg",
    ):
        path = tools / field
        path.write_text(field, encoding="utf-8")
        files[field] = path
    document.update(
        {
            "state_root": str(state_parent / "fresh-state"),
            "evidence_artifact_root": str(evidence),
            "allowed_asset_roots": [str(assets)],
            "delegated_cgroup_root": str(cgroup),
            **{field: str(path) for field, path in files.items()},
        }
    )
    return document


def test_help_exposes_only_the_fixed_qualified_case_settings_seam() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert set(re.findall(r"--[a-z-]+", completed.stdout)) == {"--help", "--settings"}
    assert "fixed qualified replay case" in completed.stdout
    assert "--input" not in completed.stdout
    assert "--qualification" not in completed.stdout
    assert "Traceback" not in completed.stderr


def test_help_loads_the_current_checkout_from_an_unrelated_working_directory(
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "fixed qualified replay case" in completed.stdout
    assert completed.stderr == ""


def test_wrapper_import_is_inert_and_exposes_the_deep_main() -> None:
    namespace = runpy.run_path(str(SCRIPT), run_name="qualified_replay_wrapper")

    assert namespace["main"] is cli.main


def test_checkout_bootstrap_precedes_a_conflicting_pythonpath_package(
    tmp_path: Path,
) -> None:
    poison = tmp_path / "poison"
    package = poison / "self_improving"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "raise RuntimeError('wrong self_improving distribution')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(poison)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    source = SCRIPT.read_text(encoding="utf-8")
    assert source.index("sys.path.insert(0, str(_CHECKOUT_ROOT))") < source.index(
        "from self_improving.qualified_replay_cli"
    )
    assert completed.returncode == 0
    assert "fixed qualified replay case" in completed.stdout
    assert "wrong self_improving distribution" not in completed.stderr
    assert completed.stderr == ""


def test_invalid_command_line_never_echoes_unknown_values() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--operator-token=/private/do-not-print"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        "robot-harness-run-qualified-replay: input error: invalid command line\n"
    )
    assert "do-not-print" not in completed.stderr
    assert "Traceback" not in completed.stderr


def test_settings_option_abbreviation_is_rejected_before_qualification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings = tmp_path / "operator-secret-do-not-print.json"
    settings.write_text(json.dumps(document) + "\n", encoding="utf-8")
    qualification_calls: list[object] = []

    def qualification(*args: object, **kwargs: object) -> None:
        qualification_calls.append((args, kwargs))
        raise QualificationBundleError("must_not_run", "operator-secret-do-not-print")

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", qualification)

    exit_code = cli.main(["--sett", str(settings)])

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: input error: invalid command line\n"
    )
    assert qualification_calls == []
    assert "operator-secret-do-not-print" not in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("repeated_value", ("same", "different"))
def test_repeated_settings_option_is_rejected_before_qualification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repeated_value: str,
) -> None:
    document = _materialized_settings_document(tmp_path)
    first = tmp_path / "first-operator-secret-do-not-print.json"
    second = tmp_path / "second-operator-secret-do-not-print.json"
    first.write_text(json.dumps(document) + "\n", encoding="utf-8")
    second.write_text(json.dumps(document) + "\n", encoding="utf-8")
    repeated = first if repeated_value == "same" else second
    qualification_calls: list[object] = []

    def qualification(*args: object, **kwargs: object) -> None:
        qualification_calls.append((args, kwargs))
        raise QualificationBundleError("must_not_run", "operator-secret-do-not-print")

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", qualification)

    exit_code = cli.main(["--settings", str(first), "--settings", str(repeated)])

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: input error: invalid command line\n"
    )
    assert qualification_calls == []
    assert "operator-secret-do-not-print" not in captured.err
    assert "Traceback" not in captured.err


def test_settings_locator_must_be_a_canonical_absolute_regular_file(tmp_path: Path) -> None:
    settings = tmp_path / "launch.json"
    settings.write_text("{}\n", encoding="utf-8")
    relative = os.path.relpath(settings, ROOT)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--settings", relative],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        "robot-harness-run-qualified-replay: input error: "
        "--settings must be a canonical absolute regular file\n"
    )
    assert str(tmp_path) not in completed.stderr
    assert "Traceback" not in completed.stderr


def test_settings_locator_symlink_loop_is_a_sanitized_input_error(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.symlink_to(second.name)
    second.symlink_to(first.name)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--settings", str(first)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        "robot-harness-run-qualified-replay: input error: "
        "--settings must be a canonical absolute regular file\n"
    )
    assert str(tmp_path) not in completed.stderr
    assert "Traceback" not in completed.stderr


def test_settings_manifest_requires_the_exact_fixed_launch_fields(tmp_path: Path) -> None:
    settings = tmp_path / "launch.json"
    settings.write_text("{}\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--settings", str(settings)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        "robot-harness-run-qualified-replay: input error: "
        "settings must be strict JSON with the exact fixed launch fields\n"
    )
    assert str(tmp_path) not in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("strict_json_attack", ("duplicate-key", "nan"))
def test_settings_manifest_rejects_non_strict_json(
    tmp_path: Path,
    strict_json_attack: str,
) -> None:
    document = _settings_document()
    if strict_json_attack == "nan":
        document["runtime_timeout_seconds"] = float("nan")
        payload = json.dumps(document)
    else:
        payload = json.dumps(document).replace(
            '"schema_version":',
            '"schema_version":"duplicate","schema_version":',
            1,
        )
    settings = tmp_path / "launch.json"
    settings.write_text(payload + "\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--settings", str(settings)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        "robot-harness-run-qualified-replay: input error: "
        "settings must be strict JSON with the exact fixed launch fields\n"
    )
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (
            b" " * 65_537,
            "settings file must be one stable regular file no larger than 65536 bytes",
        ),
        (
            ("[" * 10_000 + "0" + "]" * 10_000).encode("ascii"),
            "settings must be strict JSON with the exact fixed launch fields",
        ),
    ),
)
def test_settings_reader_is_bounded_and_maps_deep_json_to_input_error(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    settings = tmp_path / "launch.json"
    settings.write_bytes(payload)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--settings", str(settings)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == f"robot-harness-run-qualified-replay: input error: {message}\n"
    assert "Traceback" not in completed.stderr


def test_settings_reader_rejects_a_path_swap_during_the_bounded_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = tmp_path / "launch.json"
    settings.write_text(json.dumps(_settings_document()) + "\n", encoding="utf-8")
    replacement = tmp_path / "replacement.json"
    replacement.write_text("{}\n", encoding="utf-8")
    read = cli.os.read
    swapped = False

    def read_then_swap(descriptor: int, size: int) -> bytes:
        nonlocal swapped
        chunk = read(descriptor, size)
        if chunk and not swapped:
            swapped = True
            replacement.replace(settings)
        return chunk

    monkeypatch.setattr(cli.os, "read", read_then_swap)

    exit_code = cli.main(["--settings", str(settings)])

    captured = capsys.readouterr()
    assert swapped
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: input error: "
        "settings file must be one stable regular file no larger than 65536 bytes\n"
    )
    assert str(tmp_path) not in captured.err
    assert "Traceback" not in captured.err


def test_settings_reader_rejects_growth_past_the_bound_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = tmp_path / "launch.json"
    settings.write_text(json.dumps(_settings_document()) + "\n", encoding="utf-8")
    read = cli.os.read
    grown = False

    def read_then_grow(descriptor: int, size: int) -> bytes:
        nonlocal grown
        chunk = read(descriptor, size)
        if chunk and not grown:
            grown = True
            with settings.open("ab") as stream:
                stream.write(b" " * 65_537)
        return chunk

    monkeypatch.setattr(cli.os, "read", read_then_grow)

    exit_code = cli.main(["--settings", str(settings)])

    captured = capsys.readouterr()
    assert grown
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: input error: "
        "settings file must be one stable regular file no larger than 65536 bytes\n"
    )
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("allowed_asset_roots", []),
        ("allowed_asset_roots", [""]),
        ("allowed_asset_roots", "/deployment/assets"),
        ("interpreter", 7),
        ("runtime_timeout_seconds", True),
        ("capability_timeout_seconds", 0),
    ),
)
def test_settings_values_use_strict_path_root_and_timeout_types(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    document = _settings_document()
    document[field] = replacement
    settings = tmp_path / "launch.json"
    settings.write_text(json.dumps(document) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--settings", str(settings)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        "robot-harness-run-qualified-replay: input error: "
        "settings values must use strict path, root, and timeout types\n"
    )


def test_unrepresentably_large_timeout_is_a_sanitized_input_error(tmp_path: Path) -> None:
    document = _settings_document()
    document["runtime_timeout_seconds"] = 10**3999
    settings = tmp_path / "launch.json"
    settings.write_text(json.dumps(document) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--settings", str(settings)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        "robot-harness-run-qualified-replay: input error: "
        "settings values must use strict path, root, and timeout types\n"
    )
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("invalid_path", ("existing-state", "tool-symlink", "evidence-file"))
def test_launch_path_type_and_freshness_attacks_fail_closed(
    tmp_path: Path,
    invalid_path: str,
) -> None:
    document = _materialized_settings_document(tmp_path)
    state_root = Path(str(document["state_root"]))
    if invalid_path == "existing-state":
        state_root.mkdir()
    elif invalid_path == "tool-symlink":
        interpreter = Path(str(document["interpreter"]))
        symlink = interpreter.parent / "python-link"
        symlink.symlink_to(interpreter.name)
        document["interpreter"] = str(symlink)
    else:
        evidence_file = tmp_path / "evidence-file"
        evidence_file.write_text("not a directory", encoding="utf-8")
        document["evidence_artifact_root"] = str(evidence_file)
    settings = tmp_path / "launch.json"
    settings.write_text(json.dumps(document) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--settings", str(settings)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        "robot-harness-run-qualified-replay: input error: settings paths must be canonical, "
        "non-overlapping, and state_root must be fresh\n"
    )
    assert str(tmp_path) not in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("overlap", ("source", "evidence", "asset", "cgroup"))
def test_fresh_state_root_cannot_overlap_source_or_operator_trees(
    tmp_path: Path,
    overlap: str,
) -> None:
    document = _materialized_settings_document(tmp_path)
    overlap_roots = {
        "source": ROOT,
        "evidence": Path(str(document["evidence_artifact_root"])),
        "asset": Path(str(document["allowed_asset_roots"][0])),
        "cgroup": Path(str(document["delegated_cgroup_root"])),
    }
    state_root = overlap_roots[overlap] / ".qualified-replay-state-must-never-be-created"
    assert not state_root.exists()
    document["state_root"] = str(state_root)
    settings = tmp_path / "launch.json"
    settings.write_text(json.dumps(document) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--settings", str(settings)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 78
    assert completed.stdout == ""
    assert completed.stderr == (
        "robot-harness-run-qualified-replay: input error: settings paths must be canonical, "
        "non-overlapping, and state_root must be fresh\n"
    )
    assert not state_root.exists()
    assert str(state_root) not in completed.stderr


def test_state_claim_race_fails_closed_without_removing_the_other_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    state_root = Path(str(document["state_root"]))
    validate = cli._validate_launch_paths

    def validate_then_lose_race(settings: object) -> None:
        validate(settings)
        state_root.mkdir(mode=0o700)

    monkeypatch.setattr(cli, "_validate_launch_paths", validate_then_lose_race)
    monkeypatch.setattr(
        cli,
        "load_replay_qualification_bundle",
        lambda *args, **kwargs: pytest.fail("qualification must not load after a lost claim race"),
    )

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: input error: "
        "state_root could not be claimed as fresh\n"
    )
    assert state_root.is_dir()
    assert list(state_root.iterdir()) == []
    assert str(state_root) not in captured.err
    assert "Traceback" not in captured.err


def test_invalid_state_leaf_after_validation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = _materialized_settings_document(tmp_path)
    document["state_root"] = "/"
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_validate_launch_paths", lambda settings: None)

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: input error: "
        "state_root could not be claimed as fresh\n"
    )
    assert "Traceback" not in captured.err


def test_state_parent_replacement_after_validation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    state_root = Path(str(document["state_root"]))
    foreign_parent = Path(str(document["evidence_artifact_root"]))
    stat_path = cli.os.stat

    def stat_with_replaced_parent(
        path: object,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        if Path(path) == state_root.parent and dir_fd is None:
            return stat_path(foreign_parent, follow_symlinks=False)
        return stat_path(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(cli, "_validate_launch_paths", lambda settings: None)
    monkeypatch.setattr(cli.os, "stat", stat_with_replaced_parent)

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: input error: "
        "state_root could not be claimed as fresh\n"
    )
    assert not state_root.exists()
    assert "Traceback" not in captured.err


def test_descriptor_close_failure_does_not_mask_a_lost_state_claim_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    state_root = Path(str(document["state_root"]))
    validate = cli._validate_launch_paths
    close_descriptor = cli.os.close

    def validate_then_lose_race(settings: object) -> None:
        validate(settings)
        state_root.mkdir(mode=0o700)
        monkeypatch.setattr(cli.os, "close", close_then_report_failure)

    def close_then_report_failure(descriptor: int) -> None:
        close_descriptor(descriptor)
        raise OSError("simulated close failure")

    monkeypatch.setattr(cli, "_validate_launch_paths", validate_then_lose_race)

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: input error: "
        "state_root could not be claimed as fresh\n"
    )
    assert state_root.is_dir()
    assert list(state_root.iterdir()) == []
    assert "simulated close failure" not in captured.err
    assert "Traceback" not in captured.err


def test_state_claim_replacement_race_preserves_the_foreign_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    state_root = Path(str(document["state_root"]))
    stat_path = cli.os.stat
    replaced = False

    def stat_after_replacement(
        path: object,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replaced
        candidate = Path(path)
        try:
            metadata = cli.os.lstat(state_root)
        except OSError:
            metadata = None
        observes_leaf = candidate == state_root or (
            dir_fd is not None and candidate == Path(state_root.name)
        )
        if observes_leaf and metadata is not None and stat.S_ISDIR(metadata.st_mode):
            state_root.rmdir()
            state_root.write_text("foreign state must survive", encoding="utf-8")
            replaced = True
        return stat_path(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(cli.os, "stat", stat_after_replacement)

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert replaced
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: input error: "
        "state_root could not be claimed as fresh\n"
    )
    assert state_root.read_text(encoding="utf-8") == "foreign state must survive"
    assert str(state_root) not in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("failure", "expected_exit", "category"),
    (
        (
            ReplayApplicationConfigurationError("operator-secret-do-not-print"),
            78,
            "configuration/trust/qualification error",
        ),
        (
            RuntimeError("operator-secret-do-not-print"),
            74,
            "durable/internal error",
        ),
    ),
    ids=("configuration", "internal"),
)
def test_failure_never_removes_an_empty_foreign_directory_seen_as_the_initial_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    expected_exit: int,
    category: str,
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    state_root = Path(str(document["state_root"]))
    foreign_root = state_root.parent / "foreign-empty-state"
    foreign_root.mkdir(mode=0o700)
    foreign_inode = foreign_root.stat().st_ino
    open_path = cli.os.open
    stat_path = cli.os.stat
    replaced = False

    def replace_claim_leaf() -> None:
        nonlocal replaced
        state_root.rmdir()
        foreign_root.rename(state_root)
        replaced = True

    def replace_before_claim_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == state_root.name and dir_fd is not None and not replaced:
            replace_claim_leaf()
        return open_path(path, flags, mode, dir_fd=dir_fd)

    def replace_before_first_claim_observation(
        path: object,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replaced
        candidate = Path(path) if not isinstance(path, int) else None
        try:
            metadata = cli.os.lstat(candidate) if candidate is not None else None
        except OSError:
            metadata = None
        if (
            candidate == state_root
            and not replaced
            and metadata is not None
            and stat.S_ISDIR(metadata.st_mode)
        ):
            replace_claim_leaf()
        return stat_path(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def fail_after_claim(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(cli.os, "open", replace_before_claim_open)
    monkeypatch.setattr(cli.os, "stat", replace_before_first_claim_observation)
    monkeypatch.setattr(cli, "load_replay_qualification_bundle", fail_after_claim)

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert replaced
    assert exit_code == expected_exit
    assert captured.out == ""
    assert captured.err == (
        f"robot-harness-run-qualified-replay: {category}: {type(failure).__name__}\n"
    )
    assert state_root.is_dir()
    assert cli.os.lstat(state_root).st_ino == foreign_inode
    assert list(state_root.iterdir()) == []
    assert "operator-secret-do-not-print" not in captured.err
    assert "Traceback" not in captured.err


def test_post_claim_path_replacement_fails_before_replay_and_preserves_foreign_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    state_root = Path(str(document["state_root"]))
    foreign_root = state_root.parent / "foreign-empty-state"
    foreign_root.mkdir(mode=0o700)
    foreign_inode = foreign_root.stat().st_ino
    fixed_input = _fixed_input()
    descriptor = object()
    loaded = SimpleNamespace(
        descriptor=descriptor,
        kernel_evaluation=SimpleNamespace(
            invocation=SimpleNamespace(
                effective_parameters=fixed_input.model_dump(mode="json"),
                invocation_digest="e" * 64,
            )
        ),
    )
    replay_calls: list[Text2EnvReplayInput] = []

    def replace_after_claim(*args: object, **kwargs: object) -> object:
        claimed_inode = state_root.stat().st_ino
        state_root.rmdir()
        foreign_root.rename(state_root)
        assert state_root.stat().st_ino == foreign_inode
        assert foreign_inode != claimed_inode
        return loaded

    class Application:
        skills = (descriptor,)

        def replay(self, value: Text2EnvReplayInput) -> None:
            replay_calls.append(value)
            raise RuntimeError("operator-secret-do-not-print")

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", replace_after_claim)
    monkeypatch.setattr(cli, "create_replay_application", lambda settings: Application())

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == 74
    assert captured.out == ""
    assert captured.err.startswith("robot-harness-run-qualified-replay: durable/internal error: ")
    assert replay_calls == []
    assert state_root.is_dir()
    assert state_root.stat().st_ino == foreign_inode
    assert list(state_root.iterdir()) == []
    assert "operator-secret-do-not-print" not in captured.err
    assert "Traceback" not in captured.err


def test_state_root_may_be_a_sibling_of_operator_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = _materialized_settings_document(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    document["state_root"] = str(deployment / "state")
    for field in (
        "interpreter",
        "runtime_capability_path",
        "media_launcher",
        "static_ffmpeg",
    ):
        tool = deployment / field
        tool.write_text(field, encoding="utf-8")
        document[field] = str(tool)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    state_root = Path(str(document["state_root"]))
    loaded = SimpleNamespace(descriptor=object())

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", lambda *args, **kwargs: loaded)

    def stop_after_path_validation(settings: ReplayApplicationSettings) -> None:
        assert settings.state_root == state_root
        assert state_root.is_dir()
        raise ReplayApplicationConfigurationError("expected test stop")

    monkeypatch.setattr(cli, "create_replay_application", stop_after_path_validation)

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err.endswith("ReplayApplicationConfigurationError\n")
    assert state_root.is_dir()
    assert list(state_root.iterdir()) == []


def test_success_runs_only_the_verified_fixed_case_and_emits_a_sanitized_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    fixed_input = _fixed_input()
    returned = _succeeded_state()
    invocation = Invocation(
        run_id=RUN_ID,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        effective_parameters=fixed_input.model_dump(mode="json"),
        dependencies=(),
        max_attempts=2,
        invocation_digest="e" * 64,
    )
    descriptor = object()
    loaded = SimpleNamespace(
        descriptor=descriptor,
        kernel_evaluation=SimpleNamespace(
            invocation=SimpleNamespace(
                effective_parameters=fixed_input.model_dump(mode="json"),
                invocation_digest="e" * 64,
            )
        ),
        exact_dependencies=SimpleNamespace(dependencies=()),
        case_binding=SimpleNamespace(case_id="can-on-plate-seed-7-900-120-120-12"),
        generic=SimpleNamespace(
            implementation_sha256="1" * 64,
            qualification=SimpleNamespace(report_sha256="2" * 64),
        ),
    )
    calls: dict[str, object] = {}

    class Application:
        skills = (descriptor,)

        def replay(self, value: Text2EnvReplayInput) -> RunState:
            calls["replay_input"] = value
            return returned

        def run_state(self, run_id: UUID) -> RunState:
            calls.setdefault("run_state_ids", []).append(run_id)
            return returned

        def invocation(self, run_id: UUID) -> Invocation:
            calls.setdefault("invocation_ids", []).append(run_id)
            return invocation

        def events(self, **kwargs: object) -> EventPage:
            calls["events"] = kwargs
            return _persisted_events(returned)

    def load(bundle_root: Path, **kwargs: object) -> object:
        calls["load"] = (bundle_root, kwargs)
        return loaded

    def create(settings: ReplayApplicationSettings) -> Application:
        assert settings.state_root.is_dir()
        calls["application_settings"] = settings
        return Application()

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", load)
    monkeypatch.setattr(cli, "create_replay_application", create)

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    state_root = Path(str(document["state_root"]))
    evidence_root = Path(str(document["evidence_artifact_root"]))
    load_root, load_kwargs = calls["load"]
    assert exit_code == 0
    assert load_root == (ROOT / "self_improving/harness/qualified_skills/text2env.replay/1.0.0")
    assert isinstance(load_kwargs, dict)
    assert isinstance(load_kwargs["artifact_store"], LocalArtifactStore)
    assert load_kwargs["artifact_store"].root == evidence_root
    assert load_kwargs["implementation_root"] == ROOT
    assert load_kwargs["scene_gen_root"] == ROOT / "scene_gen"
    assert load_kwargs["ledger_contract_root"] == (
        ROOT / "self_improving/asset_pipeline/active/1_asset_reuse/lib"
    )
    assert calls["application_settings"] == ReplayApplicationSettings(
        state_root=state_root,
        qualification_bundle_root=load_root,
        evidence_artifact_root=evidence_root,
        implementation_root=ROOT,
        scene_gen_root=ROOT / "scene_gen",
        ledger_contract_root=(ROOT / "self_improving/asset_pipeline/active/1_asset_reuse/lib"),
        allowed_asset_roots=(Path(str(document["allowed_asset_roots"][0])),),
        interpreter=Path(str(document["interpreter"])),
        runtime_runner=ROOT / "script/run_scene_runtime.py",
        runtime_module_root=ROOT,
        runtime_capability_path=Path(str(document["runtime_capability_path"])),
        media_launcher=Path(str(document["media_launcher"])),
        static_ffmpeg=Path(str(document["static_ffmpeg"])),
        delegated_cgroup_root=Path(str(document["delegated_cgroup_root"])),
        runtime_timeout_seconds=900.0,
        capability_timeout_seconds=30.0,
    )
    assert calls["replay_input"] == fixed_input
    assert calls["run_state_ids"] == [RUN_ID, RUN_ID]
    assert calls["invocation_ids"] == [RUN_ID, RUN_ID]
    assert calls["events"] == {"after_event_id": 0, "run_id": RUN_ID, "limit": 200}
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "artifact_count": 1,
        "attempt": 1,
        "blocker_code": None,
        "case_id": "can-on-plate-seed-7-900-120-120-12",
        "environment_package_id": "b" * 64,
        "event_count": 2,
        "implementation_sha256": "1" * 64,
        "invocation_digest": "e" * 64,
        "qualification_report_sha256": "2" * 64,
        "run_id": str(RUN_ID),
        "runtime_evidence_sha256": "f" * 64,
        "schema_version": "harness.qualified_replay_fixed_case_cli_summary.v1",
        "skill_ref": "text2env.replay@1.0.0",
        "status": "succeeded",
    }
    assert captured.out == captured.out.strip() + "\n"
    assert str(tmp_path) not in captured.out
    assert not {"validated", "publishable", "cas_reread_verified"}.intersection(
        json.loads(captured.out)
    )


@pytest.mark.parametrize(
    ("status", "max_attempts", "expected_exit"),
    (
        (RunStatus.BLOCKED, 0, 10),
        (RunStatus.FAILED, 0, 20),
        (RunStatus.BLOCKED, 1, 74),
    ),
)
def test_preflight_terminal_result_keeps_its_typed_exit_and_redacts_blocker_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: RunStatus,
    max_attempts: int,
    expected_exit: int,
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    fixed_input = _fixed_input()
    returned = _preflight_terminal_state(status)
    if max_attempts:
        returned = returned.model_copy(update={"max_attempts": max_attempts})
    descriptor = object()
    loaded = SimpleNamespace(
        descriptor=descriptor,
        kernel_evaluation=SimpleNamespace(
            invocation=SimpleNamespace(
                effective_parameters=fixed_input.model_dump(mode="json"),
                invocation_digest="e" * 64,
            )
        ),
        exact_dependencies=SimpleNamespace(dependencies=()),
        case_binding=SimpleNamespace(case_id="can-on-plate-seed-7-900-120-120-12"),
        generic=SimpleNamespace(
            implementation_sha256="1" * 64,
            qualification=SimpleNamespace(report_sha256="2" * 64),
        ),
    )

    class Application:
        skills = (descriptor,)

        def replay(self, value: Text2EnvReplayInput) -> RunState:
            assert value == fixed_input
            return returned

        def run_state(self, run_id: UUID) -> RunState:
            assert run_id == RUN_ID
            return returned

        def invocation(self, run_id: UUID) -> None:
            assert run_id == RUN_ID
            return None

        def events(self, **kwargs: object) -> EventPage:
            return _persisted_events(returned)

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", lambda *args, **kwargs: loaded)
    monkeypatch.setattr(cli, "create_replay_application", lambda settings: Application())

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == expected_exit
    if expected_exit == 74:
        assert captured.out == ""
        assert captured.err == (
            "robot-harness-run-qualified-replay: durable/internal error: _DurableResultError\n"
        )
        state_root = Path(str(document["state_root"]))
        assert state_root.is_dir()
        assert list(state_root.iterdir()) == []
        return
    assert captured.err == ""
    summary = json.loads(captured.out)
    assert summary["status"] == status.value
    assert summary["attempt"] == 0
    assert summary["invocation_digest"] is None
    assert summary["blocker_code"] == "PREFLIGHT_DENIED"
    assert summary["runtime_evidence_sha256"] is None
    assert "/private/deployment/token" not in captured.out
    assert "hidden=" not in captured.out


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    (
        (RunStatus.BLOCKED, 10),
        (RunStatus.FAILED, 20),
    ),
)
def test_bound_terminal_result_keeps_its_typed_exit_and_invocation_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: RunStatus,
    expected_exit: int,
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    fixed_input = _fixed_input()
    returned = _bound_terminal_state(status)
    invocation = Invocation(
        run_id=RUN_ID,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        effective_parameters=fixed_input.model_dump(mode="json"),
        dependencies=(),
        max_attempts=2,
        invocation_digest="e" * 64,
    )
    descriptor = object()
    loaded = SimpleNamespace(
        descriptor=descriptor,
        kernel_evaluation=SimpleNamespace(
            invocation=SimpleNamespace(
                effective_parameters=fixed_input.model_dump(mode="json"),
                invocation_digest="e" * 64,
            )
        ),
        exact_dependencies=SimpleNamespace(dependencies=()),
        case_binding=SimpleNamespace(case_id="can-on-plate-seed-7-900-120-120-12"),
        generic=SimpleNamespace(
            implementation_sha256="1" * 64,
            qualification=SimpleNamespace(report_sha256="2" * 64),
        ),
    )

    class Application:
        skills = (descriptor,)

        def replay(self, value: Text2EnvReplayInput) -> RunState:
            assert value == fixed_input
            return returned

        def run_state(self, run_id: UUID) -> RunState:
            assert run_id == RUN_ID
            return returned

        def invocation(self, run_id: UUID) -> Invocation:
            assert run_id == RUN_ID
            return invocation

        def events(self, **kwargs: object) -> EventPage:
            return _persisted_events(returned)

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", lambda *args, **kwargs: loaded)
    monkeypatch.setattr(cli, "create_replay_application", lambda settings: Application())

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == expected_exit
    assert captured.err == ""
    summary = json.loads(captured.out)
    assert summary["status"] == status.value
    assert summary["attempt"] == 1
    assert summary["invocation_digest"] == "e" * 64
    assert summary["blocker_code"] == "RUNTIME_TERMINAL"
    assert summary["runtime_evidence_sha256"] is None
    assert "/private/deployment/token" not in captured.out
    assert "hidden=" not in captured.out


@pytest.mark.parametrize(
    "drift",
    (
        "skill_version",
        "max_attempts",
        "dependencies",
        "descriptor",
        "qualified_digest",
        "post_event_state",
        "post_event_invocation",
        "event_page",
        "event_envelope",
        "succeeded_output",
    ),
)
def test_durable_or_qualified_drift_fails_closed_without_a_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    drift: str,
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    fixed_input = _fixed_input()
    returned = _succeeded_state()
    invocation = Invocation(
        run_id=RUN_ID,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        effective_parameters=fixed_input.model_dump(mode="json"),
        dependencies=(),
        max_attempts=2,
        invocation_digest="e" * 64,
    )
    second_state = returned
    second_invocation = invocation
    qualified_digest = "e" * 64
    if drift == "skill_version":
        returned = returned.model_copy(update={"skill_version": "9.0.0"})
        invocation = invocation.model_copy(update={"skill_version": "9.0.0"})
        second_state = returned
        second_invocation = invocation
    elif drift == "max_attempts":
        returned = returned.model_copy(update={"max_attempts": 3})
        invocation = invocation.model_copy(update={"max_attempts": 3})
        second_state = returned
        second_invocation = invocation
    elif drift == "dependencies":
        invocation = invocation.model_copy(
            update={
                "dependencies": (DependencyRef(name="unexpected", version="1", sha256="a" * 64),)
            }
        )
        second_invocation = invocation
    elif drift == "qualified_digest":
        qualified_digest = "d" * 64
    elif drift == "post_event_state":
        second_state = returned.model_copy(update={"artifacts": ()})
    elif drift == "post_event_invocation":
        second_invocation = invocation.model_copy(update={"invocation_digest": "d" * 64})
    elif drift == "succeeded_output":
        returned = returned.model_copy(update={"output": {"unexpected": True}})
        second_state = returned

    descriptor = object()
    loaded = SimpleNamespace(
        descriptor=descriptor,
        kernel_evaluation=SimpleNamespace(
            invocation=SimpleNamespace(
                effective_parameters=fixed_input.model_dump(mode="json"),
                invocation_digest=qualified_digest,
            )
        ),
        exact_dependencies=SimpleNamespace(dependencies=()),
        case_binding=SimpleNamespace(case_id="can-on-plate-seed-7-900-120-120-12"),
        generic=SimpleNamespace(
            implementation_sha256="1" * 64,
            qualification=SimpleNamespace(report_sha256="2" * 64),
        ),
    )
    states = iter((returned, second_state))
    invocations = iter((invocation, second_invocation))
    installed_descriptor = object() if drift == "descriptor" else descriptor

    class Application:
        skills = (installed_descriptor,)

        def replay(self, value: Text2EnvReplayInput) -> RunState:
            assert value == fixed_input
            return returned

        def run_state(self, run_id: UUID) -> RunState:
            assert run_id == RUN_ID
            return next(states)

        def invocation(self, run_id: UUID) -> Invocation:
            assert run_id == RUN_ID
            return next(invocations)

        def events(self, **kwargs: object) -> EventPage:
            page = _persisted_events(returned)
            if drift == "event_page":
                return EventPage(
                    events=page.events,
                    last_event_id=page.last_event_id,
                    has_more=True,
                )
            if drift == "event_envelope":
                return EventPage(
                    events=(
                        StoredRunEvent(
                            event_id=page.events[1].event_id,
                            envelope=page.events[0].envelope,
                        ),
                        page.events[1],
                    ),
                    last_event_id=page.last_event_id,
                    has_more=False,
                )
            return page

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", lambda *args, **kwargs: loaded)
    monkeypatch.setattr(cli, "create_replay_application", lambda settings: Application())

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == 74
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: durable/internal error: _DurableResultError\n"
    )
    assert "Traceback" not in captured.err
    state_root = Path(str(document["state_root"]))
    assert state_root.is_dir()
    assert list(state_root.iterdir()) == []


@pytest.mark.parametrize(
    "configuration_error",
    (
        ReplayApplicationConfigurationError(
            "hidden=/deployment/private/operator token=do-not-print"
        ),
        RuntimeExecutorConfigurationError("hidden=/deployment/private/operator token=do-not-print"),
        SandboxError(SandboxReason.CGROUP_UNAVAILABLE),
    ),
    ids=("application", "runtime-executor", "sandbox"),
)
def test_application_configuration_failure_is_sanitized_and_preserves_one_shot_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    configuration_error: Exception,
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    fixed_input = _fixed_input()
    loaded = SimpleNamespace(
        descriptor=object(),
        kernel_evaluation=SimpleNamespace(
            invocation=SimpleNamespace(effective_parameters=fixed_input.model_dump(mode="json"))
        ),
    )
    state_root = Path(str(document["state_root"]))

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", lambda *args, **kwargs: loaded)

    def fail(settings: ReplayApplicationSettings) -> None:
        assert settings.state_root == state_root
        assert state_root.is_dir()
        raise configuration_error

    monkeypatch.setattr(cli, "create_replay_application", fail)

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err == (
        "robot-harness-run-qualified-replay: configuration/trust/qualification error: "
        f"{type(configuration_error).__name__}\n"
    )
    assert state_root.is_dir()
    assert list(state_root.iterdir()) == []
    assert "do-not-print" not in captured.err
    assert "/deployment/private" not in captured.err


@pytest.mark.parametrize(
    ("error", "expected_exit", "category"),
    (
        (
            QualificationBundleError("invalid_bundle", "secret=/private/bundle"),
            78,
            "configuration/trust/qualification error",
        ),
        (
            RunPersistenceError(
                operation="write_private_database",
                run_id=RUN_ID,
                state=None,
                cause=RuntimeError("secret=/private/database"),
            ),
            74,
            "persistence error",
        ),
        (
            RuntimeError("secret=/private/internal"),
            74,
            "durable/internal error",
        ),
    ),
    ids=("qualification", "persistence", "internal"),
)
def test_terminal_error_taxonomy_is_sanitized_and_preserves_one_shot_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected_exit: int,
    category: str,
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    state_root = Path(str(document["state_root"]))

    def fail_load(*args: object, **kwargs: object) -> None:
        assert state_root.is_dir()
        raise error

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", fail_load)

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == expected_exit
    assert captured.out == ""
    assert captured.err == (
        f"robot-harness-run-qualified-replay: {category}: {type(error).__name__}\n"
    )
    assert "secret=" not in captured.err
    assert "/private/" not in captured.err
    assert "Traceback" not in captured.err
    assert state_root.is_dir()
    assert list(state_root.iterdir()) == []


@pytest.mark.parametrize("foreign_state", ("nonempty", "replacement"))
def test_operator_cli_never_removes_foreign_state_during_failure_handling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    foreign_state: str,
) -> None:
    document = _materialized_settings_document(tmp_path)
    settings_path = tmp_path / "launch.json"
    settings_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    state_root = Path(str(document["state_root"]))
    replacement_root = state_root.parent / "foreign-replacement"
    if foreign_state == "replacement":
        replacement_root.mkdir(mode=0o700)
    loaded = SimpleNamespace(descriptor=object())

    monkeypatch.setattr(cli, "load_replay_qualification_bundle", lambda *args, **kwargs: loaded)

    def fail_after_foreign_state_arrives(settings: ReplayApplicationSettings) -> None:
        assert settings.state_root == state_root
        if foreign_state == "nonempty":
            (state_root / "foreign.db").write_text("must survive", encoding="utf-8")
        else:
            claimed_inode = state_root.stat().st_ino
            state_root.rmdir()
            replacement_root.rename(state_root)
            assert state_root.stat().st_ino != claimed_inode
        raise ReplayApplicationConfigurationError("private failure detail")

    monkeypatch.setattr(cli, "create_replay_application", fail_after_foreign_state_arrives)

    exit_code = cli.main(["--settings", str(settings_path)])

    captured = capsys.readouterr()
    assert exit_code == 78
    assert captured.out == ""
    assert captured.err.endswith("ReplayApplicationConfigurationError\n")
    assert state_root.is_dir()
    if foreign_state == "nonempty":
        assert (state_root / "foreign.db").read_text(encoding="utf-8") == "must survive"
