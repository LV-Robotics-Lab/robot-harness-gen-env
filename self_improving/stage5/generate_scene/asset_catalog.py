#!/usr/bin/env python3
"""Asset catalog loading helpers."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from generate_scene.schemas import read_json


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_asset_catalog(path: Path) -> dict[str, Any]:
    """Load either a full catalog or a prompt-case catalog.

    Prompt-case catalogs contain `base_catalog`, `selected_asset_ids`, and
    optional `entry_overrides`. This expands them into the legacy `entries`
    shape consumed by the placement harness.
    """

    path = path.expanduser()
    catalog = read_json(path)
    base_catalog = catalog.get("base_catalog")
    if not base_catalog:
        return catalog

    base_path = (path.parent / str(base_catalog)).resolve()
    base = load_asset_catalog(base_path)
    base_entries = {entry["asset_id"]: entry for entry in base.get("entries", [])}
    selected_ids = catalog.get("selected_asset_ids") or list(base_entries)
    overrides = catalog.get("entry_overrides", {})

    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for asset_id in selected_ids:
        if asset_id not in base_entries:
            missing.append(asset_id)
            continue
        entry = base_entries[asset_id]
        if asset_id in overrides:
            entry = _deep_merge(entry, overrides[asset_id])
        entries.append(entry)

    if missing:
        raise KeyError(f"Unknown selected_asset_ids in {path}: {missing}")

    resolved = copy.deepcopy(catalog)
    resolved["catalog_version"] = base.get("catalog_version", "robotwin.tabletop_asset_catalog.v0")
    resolved["base_catalog_path"] = str(base_path)
    resolved["source"] = catalog.get("source", base.get("source", {}))
    resolved["entries"] = entries
    resolved["resolved_from_case"] = str(path)
    return resolved
