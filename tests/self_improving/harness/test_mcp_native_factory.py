"""Production native MCP assembly requires sealed terminal admission authority."""

import json

import pytest

from self_improving.harness.mcp_native_assembly import (
    NativeImageMCPServerSettings,
    create_native_image_mcp_adapter,
)
from tests.self_improving.harness.test_mcp_native_assembly import settings_document


def test_factory_rejects_arbitrary_settings_and_missing_authority_without_creation(tmp_path):
    with pytest.raises(TypeError, match="exact"):
        create_native_image_mcp_adapter(object())
    settings = NativeImageMCPServerSettings.model_validate_json(
        json.dumps(settings_document(tmp_path))
    )
    with pytest.raises(ValueError, match="existing"):
        create_native_image_mcp_adapter(settings)
    assert list(tmp_path.iterdir()) == []


def test_production_factory_has_no_callable_resolver_or_clock_override(tmp_path):
    with pytest.raises(TypeError):
        create_native_image_mcp_adapter(object(), clock=lambda: None)
    with pytest.raises(TypeError):
        create_native_image_mcp_adapter(object(), terminal_resolver=lambda: None)
