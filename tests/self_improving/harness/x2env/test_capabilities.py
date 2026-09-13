"""Exact-version typed invocation through the CapabilityRegistry public seam."""

import pytest

from self_improving.harness.x2env.capabilities import CapabilityRegistry
from self_improving.harness.x2env.contracts import Model


class Input(Model):
    value: int


class Output(Model):
    value: int


def test_registry_invokes_bound_handler_and_preserves_typed_handoff():
    registry = CapabilityRegistry()
    registry.register("x2env.compile", "1.0.0", Input, Output, lambda x: Output(value=x.value + 1))
    result = registry.invoke("x2env.compile", "1.0.0", Input(value=7))
    assert result.value == 8
    (descriptor,) = registry.describe()
    assert descriptor.name == "x2env.compile"
    assert descriptor.version == "1.0.0"
    assert len(descriptor.schema_sha256) == 64


@pytest.mark.parametrize(
    "name",
    ["experiment.compile", "image2env.compile", "mcp.invoke", "x2env.observe", "x2env.publish"],
)
def test_registry_rejects_noncanonical_public_capability(name):
    with pytest.raises(ValueError, match="canonical"):
        CapabilityRegistry().register(name, "1.0.0", Input, Output, lambda x: Output(value=x.value))


def test_registry_rejects_version_mismatch_duplicate_and_wrong_types():
    registry = CapabilityRegistry()
    registry.register("x2env.compile", "1.0.0", Input, Output, lambda x: Output(value=x.value))
    with pytest.raises(ValueError, match="already"):
        registry.register("x2env.compile", "1.0.0", Input, Output, lambda x: Output(value=x.value))
    with pytest.raises(ValueError, match="version"):
        registry.invoke("x2env.compile", "2.0.0", Input(value=1))
    with pytest.raises(TypeError, match="input"):
        registry.invoke("x2env.compile", "1.0.0", Output(value=1))
    with pytest.raises(ValueError, match="unbound"):
        registry.invoke("x2env.replay", "1.0.0", Input(value=1))


@pytest.mark.parametrize("version", ["latest", "1", "1.0", "01.0.0", "1.0.-1", ""])
def test_registry_requires_exact_semantic_version(version):
    with pytest.raises(ValueError, match="version"):
        CapabilityRegistry().register("x2env.compile", version, Input, Output, Output)


def test_registry_rejects_handler_output_that_does_not_match_contract():
    registry = CapabilityRegistry()
    registry.register("observe", "1.0.0", Input, Output, lambda value: value)
    with pytest.raises(TypeError, match="output"):
        registry.invoke("observe", "1.0.0", Input(value=3))
