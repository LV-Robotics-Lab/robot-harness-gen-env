import json

import pytest

from self_improving.harness.experiment_cli import ExperimentDeployment, parse_request, run_cli


def test_parser_accepts_combined_inputs_without_model_or_service():
    request = parse_request(
        [
            "--deployment",
            "/tmp/deploy.json",
            "submit",
            "--text",
            "block",
            "--image",
            "/tmp/a.png",
            "--image",
            "/tmp/b.png",
            "--video",
            "/tmp/c.mp4",
            "--idempotency-key",
            "mixed-1",
        ]
    )
    assert len(request.image) == 2
    assert str(request.video) == "/tmp/c.mp4"
    assert request.text == "block"


def test_invalid_cli_returns_json_without_creating_workflow(capsys):
    assert run_cli(["--deployment", "/tmp/absent.json", "submit", "--idempotency-key", "x"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_request"


def test_release_deployment_is_rejected_before_loading_service(tmp_path, capsys):
    config = tmp_path / "deployment.json"
    config.write_text('{"execution_mode":"release"}')
    assert (
        run_cli(
            [
                "--deployment",
                str(config),
                "status",
                "--workflow-id",
                "00000000-0000-4000-8000-000000000000",
            ]
        )
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "invalid_deployment"


def test_development_opt_in_requires_boolean_not_integer():
    config = {
        "execution_mode": "development",
        "allow_changed_skill_closure": 1,
        "state_root": "/tmp/state",
        "runtime_roots": {},
        "codex_executable": "/tmp/codex",
        "approved_executable_sha256": "a" * 64,
        "server_python": "/tmp/python",
        "server_workdir": "/tmp/source",
        "model": "configured-model",
        "evidence_root": "/tmp/evidence",
    }
    with pytest.raises(ValueError):
        ExperimentDeployment.model_validate_json(json.dumps(config))
