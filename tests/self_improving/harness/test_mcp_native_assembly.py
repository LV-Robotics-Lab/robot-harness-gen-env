"""Public strict settings for the production native image MCP assembly."""

import json

import pytest
from pydantic import ValidationError

from self_improving.harness.mcp_native_assembly import NativeImageMCPServerSettings
from self_improving.harness.mcp_stdio import load_mcp_settings


def settings_document(tmp_path):
    def ref(schema):
        return {
            "name": "configured.json",
            "uri": "artifact://sha256/" + "a" * 64,
            "sha256": "a" * 64,
            "bytes": 1,
            "media_type": "application/json",
            "schema_version": schema,
        }

    return {
        "schema_version": "harness.native_image_mcp_server_settings.v1",
        "principal_id": "native-operator",
        "cas_root": str(tmp_path / "cas"),
        "asset_state_path": str(tmp_path / "asset.sqlite"),
        "golden_database_path": str(tmp_path / "golden.sqlite"),
        **{
            f"{role}_state_root": str(tmp_path / role)
            for role in ("compile", "scene", "observation", "validation")
        },
        **{
            f"{role}_qualification": ref("harness.skill_qualification.v1")
            for role in ("compile", "scene", "observation", "validation")
        },
        **{
            f"{role}_manifest": ref("harness.genesis_runtime_manifest.v1")
            for role in ("asset", "scene", "capture")
        },
        "scene_roots": {},
        "capture_roots": {},
        "compile_template_ref": ref("harness.image2env_genesis_compile_request.v1"),
        "registry_snapshot": ref("harness.registry_snapshot.v1"),
    }


def test_native_settings_load_through_public_stdio_discriminator(tmp_path):
    document = settings_document(tmp_path)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(document))
    loaded = load_mcp_settings(path)
    assert type(loaded) is NativeImageMCPServerSettings
    assert loaded.cas_root == tmp_path / "cas"
    assert loaded.model_dump(mode="json") == document


@pytest.mark.parametrize("field", ("clock", "child_run_id", "generic_invoke", "historical_time"))
def test_production_settings_reject_caller_execution_or_time_overrides(tmp_path, field):
    document = settings_document(tmp_path)
    document[field] = "caller-controlled"
    with pytest.raises(ValidationError):
        NativeImageMCPServerSettings.model_validate_json(json.dumps(document))
