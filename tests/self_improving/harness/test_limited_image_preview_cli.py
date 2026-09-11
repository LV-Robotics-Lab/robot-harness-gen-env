"""One Harness command owns assignment and bounded phases, not the user."""

import json
from pathlib import Path

from self_improving.harness.codex_backend import CodexBackendResult
from self_improving.harness.limited_image_preview_cli import run_cli
from tests.self_improving.harness.test_limited_image_preview_controller import service  # noqa: F401


def test_submit_runs_owned_backend_and_reports_unsupported_without_manual_resume(service, tmp_path, capsys):
    settings = tmp_path / "service.json"
    settings.write_text("{}")
    deployment = tmp_path / "deployment.json"
    deployment.write_text(json.dumps(dict(
        service_settings=str(settings), codex_executable="/operator/codex",
        approved_executable_sha256="a" * 64, server_python="/operator/python",
        server_workdir=str(tmp_path), model="operator-model", evidence_root=str(tmp_path / "evidence"),
        max_attempts=2, call_budget=15, attempt_seconds=90,
    )))
    image = tmp_path / "raw.png"
    image.write_bytes(b"service fixture owns image validation")
    called = []

    def service_factory(path):
        assert path == settings
        return service.value

    class Backend:
        def run(self, assignment, *, output_root):
            called.append(assignment)
            output_root.mkdir()
            return CodexBackendResult(
                "completed", output_root, 1.0, 0, None, None,
                {"calls": [], "terminal_decision": {
                    "workflow_id": str(service.workflow), "decision": "unsupported",
                    "reason": "Only one mouse is supported.",
                }}, None,
            )

    def backend_factory(**kwargs):
        assert kwargs["executable"] == Path("/operator/codex")
        assert kwargs["server_settings"] == settings
        return Backend()

    code = run_cli([
        "--deployment", str(deployment), "submit", "--image", str(image),
        "--text", "Use an unsupported object", "--idempotency-key", "first",
    ], service_factory=service_factory, backend_factory=backend_factory)
    assert code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "unsupported"
    assert len(called) == 1
