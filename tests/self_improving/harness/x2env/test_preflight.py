"""Read-only deployment check: reports configuration state, never loads models or Genesis."""

import hashlib
import json

from self_improving.harness.x2env.cli import main
from self_improving.harness.x2env.preflight import check_deployment


def _base(tmp_path):
    return {"state_dir": str(tmp_path / "state")}


def test_minimal_deployment_reports_unconfigured_sources(tmp_path):
    report = check_deployment(_base(tmp_path))
    assert report["schema_version"] == "x2env.deployment_check.v1"
    assert report["components"]["local"]["status"] == "configured"
    for name in ("codex", "genesis", "web", "reconstruction"):
        assert report["components"][name]["status"] == "not_configured"
    assert report["ok"] is True
    assert "api_key" not in json.dumps(report)


def test_pinned_web_config_is_hash_checked_without_reading_secrets(tmp_path):
    providers = tmp_path / "providers.json"
    providers.write_text(json.dumps({"providers": {"github_tree": {"enabled": True}}}))
    digest = hashlib.sha256(providers.read_bytes()).hexdigest()
    config = {
        **_base(tmp_path),
        "web": {"provider_config": {"path": str(providers), "sha256": digest}},
    }
    report = check_deployment(config)
    web = report["components"]["web"]
    assert web["status"] == "configured"
    assert web["checks"]["provider_config"] == "passed"
    assert web["providers"] == ["github_tree"]

    providers.write_text("{}")
    report = check_deployment(config)
    assert report["components"]["web"]["checks"]["provider_config"] == "failed"
    assert report["components"]["web"]["status"] == "misconfigured"
    assert report["ok"] is False


def test_codex_and_genesis_report_missing_paths_as_fields(tmp_path):
    key = tmp_path / "router.key"
    key.write_text("secret-value")
    key.chmod(0o600)
    roots = {
        name: str(tmp_path / name)
        for name in ("interpreter", "stdlib", "distributions", "native", "genesis")
    }
    (tmp_path / "interpreter").mkdir()
    config = {
        **_base(tmp_path),
        "codex": {
            "executable": str(tmp_path / "codex"),
            "sha256": "a" * 64,
            "model": "openai/gpt-5.6-terra",
            "router": {"api_key_file": str(key)},
        },
        "genesis": {"runtime_roots": roots},
    }
    report = check_deployment(config)
    codex = report["components"]["codex"]
    assert codex["checks"]["executable"] == "missing"
    assert codex["checks"]["router_credential"] == "present"
    assert "secret-value" not in json.dumps(report)
    genesis = report["components"]["genesis"]
    assert genesis["checks"]["runtime_roots.interpreter"] == "present"
    assert genesis["checks"]["runtime_roots.stdlib"] == "missing"
    assert report["ok"] is False
    assert report["missing"] == [
        "codex.executable",
        "genesis.runtime_roots.distributions",
        "genesis.runtime_roots.genesis",
        "genesis.runtime_roots.native",
        "genesis.runtime_roots.stdlib",
    ]


def test_reconstruction_reports_identity_fields_without_running_backend(tmp_path):
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"python": str(tmp_path / "python")}))
    digest = hashlib.sha256(runtime.read_bytes()).hexdigest()
    config = {
        **_base(tmp_path),
        "reconstruction": {
            "source_root": str(tmp_path / "source"),
            "source_commit": "b" * 40,
            "python": str(tmp_path / "python"),
            "python_sha256": "c" * 64,
            "segmentation_runtime": {"path": str(runtime), "sha256": digest},
            "reconstruction_runtime": {"path": str(runtime), "sha256": digest},
            "model_refs": {"reconstruction.model": "trellis"},
        },
    }
    report = check_deployment(config)
    rec = report["components"]["reconstruction"]
    assert rec["checks"]["source_root"] == "missing"
    assert rec["checks"]["python"] == "missing"
    assert rec["checks"]["segmentation_runtime"] == "passed"
    assert rec["checks"]["derivation_authorization"] == "missing"
    assert rec["status"] == "misconfigured"


def test_cli_check_prints_report_and_exit_code(tmp_path, capsys):
    config = tmp_path / "deployment.json"
    config.write_text(json.dumps(_base(tmp_path)))
    assert main(["check", "--deployment", str(config)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == "x2env.deployment_check.v1"
    assert report["components"]["web"]["status"] == "not_configured"
    assert not (tmp_path / "state").exists()


def test_check_rejects_symbolic_pinned_config_like_execution(tmp_path):
    original = tmp_path / "original.json"
    original.write_text('{"providers": {}}')
    alias = tmp_path / "alias.json"
    alias.symlink_to(original)
    report = check_deployment(
        {
            **_base(tmp_path),
            "web": {
                "provider_config": {
                    "path": str(alias),
                    "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                }
            },
        }
    )
    assert report["ok"] is False
    assert report["components"]["web"]["checks"]["provider_config"] == "failed"
