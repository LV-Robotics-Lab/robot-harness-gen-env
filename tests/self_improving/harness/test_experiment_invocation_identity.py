"""Tiny local Git repositories exercise operator diagnostics, not runtime qualification."""

import hashlib
import json
import subprocess
import sys

from self_improving.harness.experiment_invocation_identity import capture_invocation_identity


def repository(path):
    path.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(path), *args])

    git("init", "-q")
    (path / "app.py").write_text("original\n")
    git("add", "app.py")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial")
    return path


def test_tracks_dirty_diff_and_untracked_source_without_reporting_contents(tmp_path):
    repo = repository(tmp_path / "repo")
    before = capture_invocation_identity(repo, {"runtime_roots": {"python": "/declared/python"}})
    (repo / "app.py").write_text("private changed code\n")
    (repo / "new.py").write_text("new source\n")
    (repo / "outputs").mkdir()
    (repo / "outputs" / "generated.py").write_text("excluded output")
    (repo / "secret.env").write_text("secret token")
    after = capture_invocation_identity(repo, {"runtime_roots": {"python": "/declared/python"}})
    assert before["git"]["head"] == after["git"]["head"]
    assert before["git"]["diff_sha256"] != after["git"]["diff_sha256"]
    assert after["git"]["untracked_source_sha256"] == {
        "new.py": hashlib.sha256(b"new source\n").hexdigest()
    }
    assert "private changed code" not in json.dumps(after)
    assert "secret token" not in json.dumps(after)
    assert after["python"]["executable"] == sys.executable
    assert after["runtime"]["verification_status"] == "declared_not_deep_verified"


def test_config_file_hash_is_raw_bytes_and_secret_values_are_not_returned(tmp_path):
    repo = repository(tmp_path / "repo")
    path = tmp_path / "config.json"
    body = b'{ "runtime_roots": {}, "token": "secret-value" }\n'
    path.write_bytes(body)
    result = capture_invocation_identity(repo, path)
    assert result["configuration"]["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["configuration"]["hash_basis"] == "raw_file_bytes"
    assert "secret-value" not in json.dumps(result)


def test_symlink_source_is_not_read_and_mapping_hash_is_canonical(tmp_path):
    repo = repository(tmp_path / "repo")
    secret = tmp_path / "secret"
    secret.write_text("private")
    (repo / "linked.py").symlink_to(secret)
    result = capture_invocation_identity(repo, {"runtime_roots": {}, "b": 2, "a": 1})
    assert result["git"]["untracked_source_sha256"] == {}
    assert result["configuration"]["hash_basis"] == "canonical_json"
    assert result["authority"] == "private_operator_diagnostic_only"
