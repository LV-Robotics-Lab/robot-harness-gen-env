"""Extensible handler export catalog outside Skill implementation closures."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "EligibilityVerifier": ".text2env_validate",
    "RequirePromotionEvidence": ".text2env_validate",
    "Text2EnvCompileDependencyResolver": ".text2env_compile_dependencies",
    "Text2EnvCompileHandler": ".text2env_compile",
    "Text2EnvValidateHandler": ".text2env_validate",
    "text2env_compile_descriptor": ".text2env_compile",
    "text2env_validate_descriptor": ".text2env_validate",
}
_PUBLIC_NAMES = (
    "Text2EnvCompileDependencyResolver",
    "Text2EnvCompileHandler",
    "EligibilityVerifier",
    "RequirePromotionEvidence",
    "Text2EnvValidateHandler",
    "text2env_compile_descriptor",
    "text2env_validate_descriptor",
)


def public_names() -> tuple[str, ...]:
    return _PUBLIC_NAMES


def resolve(name: str, package: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {package!r} has no attribute {name!r}")
    return getattr(import_module(module_name, package), name)
