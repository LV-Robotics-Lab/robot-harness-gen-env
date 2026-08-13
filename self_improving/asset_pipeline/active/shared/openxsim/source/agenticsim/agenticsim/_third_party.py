"""Helpers for loading vendored third-party dependencies."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def bootstrap_vendored_isaaclab() -> list[str]:
    """Prepend vendored Isaac Lab package roots to ``sys.path`` when available."""

    if os.environ.get("AGENTICSIM_SKIP_VENDORED_ISAACLAB") == "1":
        return []

    repo_root = Path(__file__).resolve().parents[3]
    isaaclab_root = repo_root / "third_party" / "IsaacLab"
    source_root = isaaclab_root / "source"
    if not source_root.is_dir():
        return []

    added: list[str] = []
    package_roots: list[Path] = []
    for package_root in sorted(source_root.iterdir()):
        if not package_root.is_dir():
            continue
        if (package_root / package_root.name).is_dir():
            package_roots.append(package_root)

    for package_root in reversed(package_roots):
        package_root_str = str(package_root)
        if package_root_str not in sys.path:
            sys.path.insert(0, package_root_str)
            added.append(package_root_str)

    os.environ.setdefault("ISAACLAB_PATH", str(isaaclab_root))
    return added
