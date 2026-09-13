"""Exact-version typed bindings for canonical Skills and internal tools."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from .contracts import Model

PUBLIC_SKILLS = ("x2env.compile", "x2env.replay", "x2env.validate")
INTERNAL_TOOLS = ("asset.resolve", "observe", "revise")


@dataclass(frozen=True)
class CapabilityDescriptor:
    name: str
    version: str
    schema_sha256: str


@dataclass(frozen=True)
class Binding:
    descriptor: CapabilityDescriptor
    input_type: type[Model]
    output_type: type[Model]
    handler: Callable[[Model], Model]


class CapabilityRegistry:
    """Bind once and invoke by exact version; no legacy namespace translation."""

    def __init__(self) -> None:
        self._bindings: dict[str, Binding] = {}

    def register(self, name, version, input_type, output_type, handler) -> None:
        if name not in PUBLIC_SKILLS + INTERNAL_TOOLS:
            raise ValueError("not a canonical capability")
        if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
            raise ValueError("capability requires exact release version")
        if name in self._bindings:
            raise ValueError("capability already bound")
        schemas = {
            "input": input_type.model_json_schema(),
            "output": output_type.model_json_schema(),
        }
        digest = hashlib.sha256(
            json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._bindings[name] = Binding(
            CapabilityDescriptor(name, version, digest), input_type, output_type, handler
        )

    def describe(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(binding.descriptor for binding in self._bindings.values())

    def invoke(self, name: str, version: str, value: Model) -> Model:
        binding = self._bindings.get(name)
        if binding is None:
            raise ValueError("unbound capability")
        if version != binding.descriptor.version:
            raise ValueError("capability version mismatch")
        if type(value) is not binding.input_type:
            raise TypeError("capability input type mismatch")
        result = binding.handler(value)
        if type(result) is not binding.output_type:
            raise TypeError("capability output type mismatch")
        return result
