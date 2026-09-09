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
    report = tmp_path / "report.json"
    staged_probe.write_new_report(report, b'{"status":"pass"}\n')
    assert report.read_bytes() == b'{"status":"pass"}\n'
    with pytest.raises(FileExistsError):
        staged_probe.write_new_report(report, b"replacement")
    assert report.read_bytes() == b'{"status":"pass"}\n'

    partial = tmp_path / "partial.json"
    monkeypatch.setattr(staged_probe.os, "write", lambda _fd, _payload: 0)
    with pytest.raises(OSError, match="short report write"):
        staged_probe.write_new_report(partial, b"partial")
    assert not partial.exists()
