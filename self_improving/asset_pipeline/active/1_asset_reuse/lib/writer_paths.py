"""Fail-closed locator checks for active filesystem writers."""

import os
from pathlib import Path

from . import ledger


class UnsafeWriterPathError(ValueError):
    """An untrusted identifier or derived writer path is unsafe."""


def portable_segment(value, *, field):
    """Return one portable path segment or raise a typed error."""

    try:
        return ledger.canonical_asset_key(value)
    except ledger.UnsafeAssetKeyError as exc:
        raise UnsafeWriterPathError(
            f"unsafe {field}: expected one portable path segment, got {value!r}"
        ) from exc


def _reject_symlink_components(path):
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise UnsafeWriterPathError(f"writer path traverses symlink component: {current}")
        if not current.exists():
            break


def contained_path(root, candidate):
    """Return an absolute lexical child after strict no-symlink containment."""

    root = Path(os.path.abspath(root))
    candidate = Path(os.path.abspath(candidate))
    if not candidate.is_relative_to(root):
        raise UnsafeWriterPathError(f"writer path escapes library root: {candidate}")
    _reject_symlink_components(root)
    _reject_symlink_components(candidate)
    return candidate
