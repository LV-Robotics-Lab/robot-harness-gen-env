"""Protocol fixtures test verification only; they never certify a real Codex run."""

import json
from pathlib import Path

import pytest

from self_improving.harness.codex_mcp_acceptance import (
    CodexMCPInvocation,
    prepare_codex_mcp_invocation,
)


def test_invocation_is_ephemeral_exact_mcp_and_does_not_edit_user_config(tmp_path):
    for name in ("codex", "python", "settings.json"):
        (tmp_path / name).write_text("fixture")
    invocation = prepare_codex_mcp_invocation(
        codex_executable=tmp_path / "codex",
        server_python=tmp_path / "python",
        server_settings=tmp_path / "settings.json",
        server_workdir=tmp_path,
        model="test-model",
        case="normal",
        objective="mouse on table",
    )
    assert type(invocation) is CodexMCPInvocation
    assert invocation.argv[:5] == (
        str(tmp_path / "codex"), "exec", "--json", "--ephemeral", "--ignore-user-config"
    )
    assert "--sandbox" in invocation.argv
    assert "read-only" in invocation.argv
    overrides = [invocation.argv[i + 1] for i, value in enumerate(invocation.argv) if value == "-c"]
    assert 'features.shell_tool=false' in overrides
    config = next(value for value in overrides if value.startswith("mcp_servers="))
    assert str(tmp_path / "python") in config
    assert str(tmp_path / "settings.json") in config
    assert "skill.invoke" in invocation.prompt
    assert not (tmp_path / ".codex").exists()


def test_relative_server_settings_are_rejected_before_launch(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        prepare_codex_mcp_invocation(
            codex_executable=Path("codex"), server_python=tmp_path / "python",
            server_settings=tmp_path / "settings.json", server_workdir=tmp_path,
            model="test", case="normal", objective="mouse",
        )
