import hashlib
import os
from pathlib import Path

import pytest

from self_improving.harness.codex_backend import CodexBackend
from self_improving.harness.experiment_inputs import SceneIntentV1


@pytest.mark.skipif(os.environ.get("HARNESS_CODEX_REAL") != "1", reason="real Codex opt-in")
def test_managed_backend_returns_actual_text_interpretation(tmp_path):
    executable = Path("/home/jingxiang/.local/libexec/codex-real")
    backend = CodexBackend(
        executable=executable,
        approved_executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        server_python=Path(__import__("sys").executable),
        server_settings=tmp_path / "unused.json",
        server_workdir=tmp_path,
        model="gpt-6-astra",
    )
    value = backend.propose(
        prompt="Interpret: generate one red cube of 0.08 m on table at XY [-0.15,0], yaw 0. "
        "input_sha256 is " + "a" * 64 + ". Use source generated, selection exact, no unknowns.",
        output_model=SceneIntentV1,
        images=(),
        output_root=tmp_path / "attempt",
        timeout=120,
    )
    assert value["status"] == "completed", value
    assert value["proposal"]["category"] in {"cube", "box"}
    assert value["proposal"]["source"] == "generated"
    assert value["external_agent_executed"] is True
