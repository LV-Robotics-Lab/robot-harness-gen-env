"""Contract tests for the opt-in staged-only RoboTwin/SAPIEN probe CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import script.probe_staged_robotwin_assets as staged_probe

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "script" / "probe_staged_robotwin_assets.py"


def test_probe_cli_exposes_every_explicit_authority_input() -> None:
    completed = subprocess.run(
        (sys.executable, SCRIPT, "--help"),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert "--expected-stage-result-sha256" in completed.stdout
    assert "--expected-stage-binding-sha256" in completed.stdout
    assert "--expected-inventory-sha256" in completed.stdout
    assert "--expected-repair-plan-sha256" in completed.stdout
    assert "--expected-source-manifest-sha256" in completed.stdout


def test_probe_report_path_resolves_outside_the_feature_checkout(tmp_path: Path) -> None:
    feature_root = tmp_path / "feature"
    external_root = tmp_path / "evidence"
    feature_root.mkdir()
    external_root.mkdir()
    accepted = external_root / "report.json"

    assert staged_probe.require_external_report_path(
        feature_root=feature_root,
        requested_path=accepted,
    ) == accepted
    with pytest.raises(ValueError, match="outside"):
        staged_probe.require_external_report_path(
            feature_root=feature_root,
            requested_path=feature_root / "report.json",
        )
    outside_symlink = tmp_path / "outside-symlink"
    outside_symlink.symlink_to(feature_root, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        staged_probe.require_external_report_path(
            feature_root=feature_root,
            requested_path=outside_symlink / "report.json",
        )
    dangling_report_symlink = external_root / "dangling-report.json"
    dangling_report_symlink.symlink_to(external_root / "missing-target.json")
    with pytest.raises(ValueError, match="new"):
        staged_probe.require_external_report_path(
            feature_root=feature_root,
            requested_path=dangling_report_symlink,
        )
    accepted.write_bytes(b"existing")
    with pytest.raises(ValueError, match="new"):
        staged_probe.require_external_report_path(
            feature_root=feature_root,
            requested_path=accepted,
        )


def test_probe_report_is_exclusively_published_without_partial_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_root = tmp_path / "feature"
    feature_root.mkdir()
    report = tmp_path / "report.json"
    staged_probe.write_new_report(
        feature_root=feature_root,
        path=report,
        payload=b'{"status":"pass"}\n',
    )
    assert report.read_bytes() == b'{"status":"pass"}\n'
    with pytest.raises(FileExistsError):
        staged_probe.write_new_report(
            feature_root=feature_root,
            path=report,
            payload=b"replacement",
        )
    assert report.read_bytes() == b'{"status":"pass"}\n'

    partial = tmp_path / "partial.json"
    monkeypatch.setattr(staged_probe.os, "write", lambda _fd, _payload: 0)
    with pytest.raises(OSError, match="short report write"):
        staged_probe.write_new_report(
            feature_root=feature_root,
            path=partial,
            payload=b"partial",
        )
    assert not partial.exists()


def test_probe_report_rejects_a_parent_replaced_after_path_validation(
    tmp_path: Path,
) -> None:
    feature_root = tmp_path / "feature"
    external_root = tmp_path / "evidence"
    moved_external_root = tmp_path / "moved-evidence"
    feature_root.mkdir()
    external_root.mkdir()
    accepted = staged_probe.require_external_report_path(
        feature_root=feature_root,
        requested_path=external_root / "report.json",
    )

    external_root.rename(moved_external_root)
    external_root.symlink_to(feature_root, target_is_directory=True)

    with pytest.raises((OSError, ValueError)):
        staged_probe.write_new_report(
            feature_root=feature_root,
            path=accepted,
            payload=b'{"status":"pass"}\n',
        )
    assert not (feature_root / "report.json").exists()
    assert not (moved_external_root / "report.json").exists()


def test_probe_report_short_write_cleanup_uses_the_opened_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_root = tmp_path / "feature"
    external_root = tmp_path / "evidence"
    moved_external_root = tmp_path / "moved-evidence"
    feature_root.mkdir()
    external_root.mkdir()
    accepted = staged_probe.require_external_report_path(
        feature_root=feature_root,
        requested_path=external_root / "report.json",
    )

    def rename_parent_then_short_write(_fd: int, _payload: object) -> int:
        external_root.rename(moved_external_root)
        external_root.symlink_to(feature_root, target_is_directory=True)
        return 0

    monkeypatch.setattr(staged_probe.os, "write", rename_parent_then_short_write)

    with pytest.raises(OSError, match="short report write"):
        staged_probe.write_new_report(
            feature_root=feature_root,
            path=accepted,
            payload=b"partial",
        )
    assert not (feature_root / "report.json").exists()
    assert not (moved_external_root / "report.json").exists()


def test_probe_report_fails_if_the_opened_parent_moves_into_the_feature_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_root = tmp_path / "feature"
    external_root = tmp_path / "evidence"
    moved_external_root = feature_root / "moved-evidence"
    feature_root.mkdir()
    external_root.mkdir()
    accepted = staged_probe.require_external_report_path(
        feature_root=feature_root,
        requested_path=external_root / "report.json",
    )
    real_write = staged_probe.os.write
    parent_moved = False

    def move_parent_then_write(file_descriptor: int, payload: object) -> int:
        nonlocal parent_moved
        if not parent_moved:
            external_root.rename(moved_external_root)
            parent_moved = True
        return real_write(file_descriptor, payload)

    monkeypatch.setattr(staged_probe.os, "write", move_parent_then_write)

    with pytest.raises(OSError):
        staged_probe.write_new_report(
            feature_root=feature_root,
            path=accepted,
            payload=b'{"status":"pass"}\n',
        )
    assert parent_moved is True
    assert not (moved_external_root / "report.json").exists()


def test_probe_report_fails_if_the_opened_parent_is_renamed_and_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_root = tmp_path / "feature"
    external_root = tmp_path / "evidence"
    moved_external_root = tmp_path / "moved-evidence"
    feature_root.mkdir()
    external_root.mkdir()
    accepted = staged_probe.require_external_report_path(
        feature_root=feature_root,
        requested_path=external_root / "report.json",
    )
    real_write = staged_probe.os.write
    parent_replaced = False

    def replace_parent_then_write(file_descriptor: int, payload: object) -> int:
        nonlocal parent_replaced
        if not parent_replaced:
            external_root.rename(moved_external_root)
            external_root.mkdir()
            parent_replaced = True
        return real_write(file_descriptor, payload)

    monkeypatch.setattr(staged_probe.os, "write", replace_parent_then_write)

    with pytest.raises(OSError):
        staged_probe.write_new_report(
            feature_root=feature_root,
            path=accepted,
            payload=b'{"status":"pass"}\n',
        )
    assert parent_replaced is True
    assert not (external_root / "report.json").exists()
    assert not (moved_external_root / "report.json").exists()


@pytest.mark.parametrize("path_kind", ["relative", "dotdot", "root"])
def test_probe_report_publication_requires_an_absolute_normalized_file_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_kind: str,
) -> None:
    feature_root = tmp_path / "feature"
    external_root = tmp_path / "evidence"
    feature_root.mkdir()
    (external_root / "nested").mkdir(parents=True)
    if path_kind == "relative":
        monkeypatch.chdir(tmp_path)
        report_path = Path("evidence/report.json")
    elif path_kind == "dotdot":
        report_path = external_root / "nested" / ".." / "report.json"
    else:
        report_path = Path("/")

    with pytest.raises(ValueError):
        staged_probe.write_new_report(
            feature_root=feature_root,
            path=report_path,
            payload=b'{"status":"pass"}\n',
        )
    assert not (external_root / "report.json").exists()
