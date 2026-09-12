"""CLI uses the real Store/Harness; omitted model is a genuine missing dependency."""

import json

from self_improving.harness.x2env.cli import main


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
