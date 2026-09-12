import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from self_improving.harness.experiment_service import ExperimentService


def test_public_service_starts_text_and_preserves_idempotency_in_golden(tmp_path):
    config = tmp_path / "deployment.json"
    config.write_text(
        json.dumps({"execution_mode": "development", "allow_changed_skill_closure": True})
    )
    service = ExperimentService(config, state_root=tmp_path / "state")
    first = service.start(user_text="one red block on a table", idempotency_key="one")
    assert first["route"] == "text"
    assert service.start(user_text="one red block on a table", idempotency_key="one") == first
    restored = ExperimentService(config, state_root=tmp_path / "state")
    assert restored.status(workflow_id=first["workflow_id"])["snapshot"] == first["snapshot"]


def test_unknown_stage_is_rejected_without_advancing_workflow(tmp_path):
    config = tmp_path / "deployment.json"
    config.write_text(
        json.dumps({"execution_mode": "development", "allow_changed_skill_closure": True})
    )
    service = ExperimentService(config, state_root=tmp_path / "state")
    first = service.start(user_text="one block", idempotency_key="one")
    import pytest

    with pytest.raises(ValueError, match="stage"):
        service.execute_stage(first["workflow_id"], "publish", "never")
    assert service.status(workflow_id=first["workflow_id"])["snapshot"]["revision"] == 0


def test_missing_dependency_is_journaled_without_success_claim(tmp_path):
    config = tmp_path / "deployment.json"
    config.write_text(
        json.dumps({"execution_mode": "development", "allow_changed_skill_closure": True})
    )
    service = ExperimentService(config, state_root=tmp_path / "state")
    first = service.start(user_text="one block", idempotency_key="one")
    operation = service.execute_stage(first["workflow_id"], "compile", "compile-1")
    result = service.status(workflow_id=first["workflow_id"])
    assert result["snapshot"]["revision"] == 1
    assert result["history"][-1]["status"] == "failed"
    assert "missing stage dependency" in result["history"][-1]["body"]["reason"]
    assert "environment.scene" not in result["facts"]
    assert service.execute_stage(first["workflow_id"], "compile", "compile-1") == operation


@pytest.mark.skipif(os.environ.get("HARNESS_CODEX_REAL") != "1", reason="real model opt-in")
def test_real_text_model_asset_compile_share_one_journal(tmp_path):
    from self_improving.harness.codex_backend import CodexBackend

    config = tmp_path / "deployment.json"
    config.write_text(
        json.dumps(
            {
                "execution_mode": "development",
                "allow_changed_skill_closure": True,
                "default_source": "generated",
            }
        )
    )
    executable = Path("/home/jingxiang/.local/libexec/codex-real")
    backend = CodexBackend(
        executable=executable,
        approved_executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        server_python=Path(sys.executable),
        server_settings=config,
        server_workdir=tmp_path,
        model="gpt-6-astra",
    )
    service = ExperimentService(config, state_root=tmp_path / "state", backend=backend)
    started = service.start(
        user_text="Generate one red block on a table at x=0.1m y=-0.05m, yaw=20 degrees. "
        "Keep its generated size.",
        idempotency_key="real-text",
    )
    for stage in ("interpret", "acquire", "compile"):
        service.execute_stage(started["workflow_id"], stage, stage + "-1")
        current = service.status(workflow_id=started["workflow_id"])
        assert current["history"][-1]["status"] == "passed", current["history"][-1]
    assert current["snapshot"]["revision"] == 3
    assert current["history"][0]["body"]["model"]["external_agent_executed"] is True
