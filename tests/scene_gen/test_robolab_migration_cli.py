from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "script" / "migrate_robolab_asset.py"

spec = importlib.util.spec_from_file_location("migrate_robolab_asset", SCRIPT)
assert spec is not None and spec.loader is not None
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def make_args(**updates):
    values = {
        "asset_id": "905_robolab_scissors",
        "mass_kg": 0.2,
        "static_friction": 2.0,
        "dynamic_friction": 2.0,
        "restitution": 0.1,
    }
    values.update(updates)
    return argparse.Namespace(**values)


def test_validate_args_accepts_complete_physical_metadata() -> None:
    migration.validate_args(make_args())


def test_validate_args_rejects_partial_physical_material() -> None:
    with pytest.raises(ValueError, match="must be provided together"):
        migration.validate_args(
            make_args(dynamic_friction=None)
        )


def test_validate_args_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="mass-kg"):
        migration.validate_args(make_args(mass_kg=0.0))
    with pytest.raises(ValueError, match="restitution"):
        migration.validate_args(make_args(restitution=1.1))
    with pytest.raises(ValueError, match="asset-id"):
        migration.validate_args(make_args(asset_id="../escape"))


def test_source_file_rejects_repository_escape(tmp_path: Path) -> None:
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    inside = source_repo / "asset.usd"
    inside.write_bytes(b"usd")
    outside = tmp_path / "outside.usd"
    outside.write_bytes(b"outside")

    assert migration.source_file(source_repo, Path("asset.usd")) == inside.resolve()
    with pytest.raises(ValueError, match="escapes repository"):
        migration.source_file(source_repo, Path("../outside.usd"))


def test_cli_help_does_not_require_migration_dependencies() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--mesh-prim" in completed.stdout
    assert "--static-friction" in completed.stdout
