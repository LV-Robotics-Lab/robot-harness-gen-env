"""Stable package seam for public Harness schema exports."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name != "__all__":
        from ._qualified_exports import resolve as resolve_qualified

        try:
            value = resolve_qualified(name, __name__)
        except AttributeError:
            pass
        else:
            globals()[name] = value
            return value
    from ._public_exports import public_names, resolve

    value = list(public_names()) if name == "__all__" else resolve(name, __name__)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    from ._public_exports import public_names

    return sorted(set(globals()) | set(public_names()) | {"__all__"})
