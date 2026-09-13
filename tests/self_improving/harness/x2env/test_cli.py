"""CLI uses the real Store/Harness; omitted model is a genuine missing dependency."""

import json
import runpy
import signal
import sys
import time

import pytest

from self_improving.harness.x2env.cli import main


@pytest.fixture
def command(tmp_path):
    config = tmp_path / "deployment.json"
    config.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    return [
        "submit",
        "--deployment",
        str(config),
        "--seed",
        "23",
        "--idempotency-key",
        "public-cli",
        "--output",
        str(tmp_path / "output"),
    ]


@pytest.mark.parametrize("args", [[], ["unknown"], ["submit", "--seed", "not-an-int"]])
def test_invalid_arguments_are_structured_without_workflow(args, capsys):
    assert main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["stage"] == "arguments"
    assert result["error_code"] == "invalid_request"
    assert result["workflow_id"] is None


def test_oversized_text_file_is_rejected_before_submission(command, tmp_path, capsys):
    text = tmp_path / "large.txt"
    text.write_bytes(b"x" * (128 * 1024 + 1))
    assert main([*command, "--text-file", str(text)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["message"] == "text input too large"
    assert result["workflow_id"] is None
    assert result["stage"] == "submit"


@pytest.mark.parametrize("flag", ["--text-file", "--image", "--video"])
def test_missing_user_media_keeps_structured_failure(command, tmp_path, capsys, flag):
    assert main([*command, flag, str(tmp_path / "missing")]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed"
    if flag == "--text-file":
        assert result["error_code"] == "blocked_external_resource"
        assert result["workflow_id"] is None
    else:
        assert result["snapshot"]["stop_reason"] == "input_unavailable"
        assert result["snapshot"]["operations"][0]["result"]["error_code"] == "input_unavailable"
        assert result["workflow_id"] is not None
        assert result["delivery"]["environment_package"] is None


def test_resume_then_new_failure_package_preserves_real_workflow(command, tmp_path, capsys):
    assert main([*command, "--text", "one table"]) == 2
    submitted = json.loads(capsys.readouterr().out)
    common = ["--deployment", command[2], "--workflow-id", submitted["workflow_id"]]
    assert main(["resume", *common]) == 2
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["status"] == "blocked"
    assert resumed["workflow_id"] == submitted["workflow_id"]
    assert main(["package", *common, "--output", str(tmp_path / "copied-failure")]) == 0
    packaged = json.loads(capsys.readouterr().out)
    assert packaged["delivery"]["environment_package"] is None
    assert packaged["workflow_id"] == submitted["workflow_id"]


def test_previous_interval_timer_is_restored(command, capsys, monkeypatch):
    calls = []
    previous_handler = signal.getsignal(signal.SIGALRM)

    def timer(which, seconds, interval=0):
        calls.append((which, seconds, interval))
        return (30.0, 2.0) if len(calls) == 1 else (0.0, 0.0)

    monkeypatch.setattr(signal, "setitimer", timer)
    assert main([*command, "--text", "one table"]) == 2
    capsys.readouterr()
    assert calls[1] == (signal.ITIMER_REAL, 0, 0)
    assert calls[2][0] == signal.ITIMER_REAL
    assert 0 < calls[2][1] <= 30.0 and calls[2][2] == 2.0
    assert signal.getsignal(signal.SIGALRM) == previous_handler


def test_delivered_alarm_is_timeout_and_restores_handler(command, capsys, monkeypatch):
    previous_handler = signal.getsignal(signal.SIGALRM)

    def timer(which, seconds, interval=0):
        if seconds:
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
        return (0.0, 0.0)

    monkeypatch.setattr(signal, "setitimer", timer)
    assert main([*command, "--text", "one table"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "command_timeout"
    assert result["workflow_id"] is None
    assert signal.getsignal(signal.SIGALRM) == previous_handler


def test_elapsed_command_budget_keeps_already_submitted_handle(command, capsys, monkeypatch):
    started = time.monotonic()
    reads = 0

    def clock():
        nonlocal reads
        reads += 1
        return started if reads == 1 else started + 2000

    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(signal, "setitimer", lambda *args: (0.0, 0.0))
    assert main([*command, "--text", "one table"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "command_timeout"
    assert result["workflow_id"] is not None
    assert result["workflow_status"] == "active"


def test_module_command_exits_with_structured_argument_error(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["x2env", "status", "--workflow-id", "missing"])
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        with pytest.raises(SystemExit) as exited:
            runpy.run_module("self_improving.harness.x2env.cli", run_name="__main__")
    assert exited.value.code == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "invalid_request"


def test_cli_accepts_deployment_after_command_and_returns_handle_on_block(tmp_path, capsys):
    config = tmp_path / "deployment.json"
    config.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    text = tmp_path / "text.txt"
    text.write_text("a table")
    code = main(
        [
            "submit",
            "--deployment",
            str(config),
            "--text-file",
            str(text),
            "--seed",
            "19",
            "--idempotency-key",
            "once",
            "--output",
            str(tmp_path / "output"),
        ]
    )
    body = json.loads(capsys.readouterr().out)
    assert code == 2 and body["status"] == "blocked" and body["workflow_id"]
    assert body["delivery"]["environment_package"] is None
    assert main(["--deployment", str(config), "status", "--workflow-id", body["workflow_id"]]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["workflow_id"] == body["workflow_id"] and status["status"] == "blocked"


def test_repeat_submit_reuses_failure_package_without_rewriting(tmp_path, capsys):
    config = tmp_path / "deployment.json"
    config.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    args = [
        "submit",
        "--deployment",
        str(config),
        "--text",
        "a table",
        "--seed",
        "19",
        "--idempotency-key",
        "repeat",
        "--output",
        str(tmp_path / "output"),
    ]
    assert main(args) == 2
    first = json.loads(capsys.readouterr().out)
    before = {str(p): p.read_bytes() for p in (tmp_path / "output").rglob("*") if p.is_file()}
    assert main(args) == 2
    second = json.loads(capsys.readouterr().out)
    assert second["workflow_id"] == first["workflow_id"]
    assert before == {
        str(p): p.read_bytes() for p in (tmp_path / "output").rglob("*") if p.is_file()
    }


def test_tampered_repeat_package_is_not_reported_as_success(tmp_path, capsys):
    config = tmp_path / "deployment.json"
    config.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    args = [
        "submit",
        "--deployment",
        str(config),
        "--text",
        "a table",
        "--seed",
        "19",
        "--idempotency-key",
        "repeat",
        "--output",
        str(tmp_path / "output"),
    ]
    assert main(args) == 2
    first = json.loads(capsys.readouterr().out)
    member = next(p for p in (tmp_path / "output").rglob("*") if p.is_file())
    member.write_bytes(b"tampered")
    assert main(args) == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["error_code"] == "package_failed"
    assert failed["workflow_id"] == first["workflow_id"]
    assert failed["workflow_status"] == "blocked"
    assert member.read_bytes() == b"tampered"


def test_missing_deployment_is_json_error(capsys):
    assert main(["status", "--workflow-id", "unknown"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "invalid_request" and result["workflow_id"] is None


def test_cli_keeps_multiple_images_and_text_without_model(tmp_path, capsys):
    from PIL import Image

    config = tmp_path / "deployment.json"
    config.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGB", (4, 4), "red").save(red)
    Image.new("RGB", (4, 4), "blue").save(blue)
    assert (
        main(
            [
                "submit",
                "--deployment",
                str(config),
                "--text",
                "two views",
                "--image",
                str(red),
                "--image",
                str(blue),
                "--seed",
                "42",
                "--source",
                "local",
                "--allow-cousin",
                "--idempotency-key",
                "media",
                "--output",
                str(tmp_path / "out"),
            ]
        )
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result["not_implemented"] == ["digital_cousin_selection"]
    from self_improving.harness.x2env.contracts import ArtifactRef, InputBundle
    from self_improving.harness.x2env.store import Store

    store = Store(tmp_path / "state")
    bundle = InputBundle.model_validate_json(
        store.read_artifact(ArtifactRef.model_validate(result["snapshot"]["input_bundle"]))
    )
    assert bundle.modality == "multimodal" and len(bundle.images) == 2 and bundle.seed == 42


def test_installed_cli_uses_canonical_entrypoint():
    import tomllib
    from pathlib import Path

    config = tomllib.loads((Path(__file__).resolve().parents[4] / "pyproject.toml").read_text())
    assert config["project"]["scripts"]["x2env"] == "self_improving.harness.x2env.cli:main"
