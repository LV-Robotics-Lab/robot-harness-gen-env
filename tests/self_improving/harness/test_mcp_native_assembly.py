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


@pytest.mark.parametrize(
    "field,value",
    (
        ("cas_root", "relative/cas"),
        ("golden_database_path", "relative/golden.sqlite"),
        ("principal_id", ""),
        ("scene_roots", {"arbitrary": "/native"}),
        (
            "capture_roots",
            {
                role: "relative"
                for role in (
                    "backend",
                    "distributions",
                    "genesis",
                    "interpreter",
                    "native",
                    "stdlib",
                )
            },
        ),
        ("scene_manifest", "wrong_schema"),
        ("compile_qualification", "wrong_schema"),
    ),
)
def test_settings_reject_ambiguous_deployment_and_authority_types(tmp_path, field, value):
    document = settings_document(tmp_path)
    if value == "wrong_schema":
        document[field]["schema_version"] = "harness.run_state.v1"
    else:
        document[field] = value
    with pytest.raises(ValidationError):
        NativeImageMCPServerSettings.model_validate_json(json.dumps(document))


def test_restore_requires_existing_native_authority_before_any_creation(tmp_path):
    from self_improving.harness.mcp_native_assembly import restore_native_image_applications

    with pytest.raises(TypeError, match="exact"):
        restore_native_image_applications(object())
    settings = NativeImageMCPServerSettings.model_validate_json(
        json.dumps(settings_document(tmp_path))
    )
    with pytest.raises(ValueError, match="existing"):
        restore_native_image_applications(settings)
    assert list(tmp_path.iterdir()) == []
    (tmp_path / "cas" / "sha256").mkdir(parents=True)
    with pytest.raises(ValueError, match="journals"):
        restore_native_image_applications(settings)


def test_complete_absolute_deployment_mapping_is_preserved(tmp_path):
    document = settings_document(tmp_path)
    document["scene_roots"] = {
        role: str(tmp_path / role)
        for role in ("backend", "distributions", "genesis", "interpreter", "native", "stdlib")
    }
    settings = NativeImageMCPServerSettings.model_validate_json(json.dumps(document))
    assert settings.model_dump(mode="json") == document


@pytest.mark.parametrize(
    "mode",
    (
        "uri",
        "media_type",
        "database",
        "parent",
        "snapshot",
        "template",
    ),
)
def test_settings_authority_configuration_is_exact_without_projection_requirements(tmp_path, mode):
    document = settings_document(tmp_path)
    if mode == "uri":
        document["scene_manifest"]["uri"] = "file:///outside/authority.json"
    elif mode == "media_type":
        document["compile_qualification"]["media_type"] = "text/plain"
    elif mode == "database":
        document["golden_database_path"] = document["asset_state_path"]
    elif mode == "parent":
        document["cas_root"] = str(tmp_path) + "/child/../cas"
    elif mode == "snapshot":
        document["registry_snapshot"]["schema_version"] = "harness.skill_qualification.v1"
    else:
        document["compile_template_ref"]["schema_version"] = (
            "harness.image2env_genesis_compile_request.v2"
        )
    with pytest.raises(ValidationError):
        NativeImageMCPServerSettings.model_validate_json(json.dumps(document))
