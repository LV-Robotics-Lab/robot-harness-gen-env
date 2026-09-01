import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ledger" / "rescale_backfill.py"


@pytest.fixture
def rescale_module():
    spec = importlib.util.spec_from_file_location("rescale_backfill_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_file_transaction_rolls_back_every_published_mesh_after_exception(tmp_path, rescale_module):
    first = tmp_path / "visual.glb"
    second = tmp_path / "collision.glb"
    first.write_bytes(b"old-visual")
    second.write_bytes(b"old-collision")
    transaction = rescale_module._FileTransaction()
    first_stage = transaction.stage(first)
    second_stage = transaction.stage(second)
    first_stage.write_bytes(b"new-visual")
    second_stage.write_bytes(b"new-collision")
    transaction.publish(first)
    transaction.publish(second)

    transaction.rollback()

    assert first.read_bytes() == b"old-visual"
    assert second.read_bytes() == b"old-collision"
    assert not first_stage.exists()
    assert not second_stage.exists()


def test_file_transaction_discards_unpublished_stages(tmp_path, rescale_module):
    target = tmp_path / "mesh.glb"
    target.write_bytes(b"old")
    transaction = rescale_module._FileTransaction()
    staged = transaction.stage(target)
    staged.write_bytes(b"new")

    transaction.rollback()

    assert target.read_bytes() == b"old"
    assert not staged.exists()


def test_apply_fails_closed_before_any_multi_file_mutation(
    tmp_path, monkeypatch, capsys, rescale_module
):
    missing_config = tmp_path / "must-not-be-read.yml"
    monkeypatch.setattr(
        rescale_module.sys,
        "argv",
        ["rescale_backfill.py", "--apply", "--sizes", str(missing_config)],
    )

    assert rescale_module.main() == 2
    assert "BLOCKED" in capsys.readouterr().err
    assert not missing_config.exists()
