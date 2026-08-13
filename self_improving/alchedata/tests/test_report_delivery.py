import json
from pathlib import Path

import pytest

from scripts.report_delivery import sync_bundle, verify_bundle, write_manifest


def make_bundle(root: Path) -> Path:
    bundle = root / "source"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "index.html").write_text("<h1>report</h1>\n", encoding="utf-8")
    (bundle / "assets" / "evidence.json").write_text('{"status":"pass"}\n', encoding="utf-8")
    write_manifest(bundle, "test.report_manifest.v1")
    return bundle


def test_manifest_verifies_exact_file_set_and_hashes(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)

    result = verify_bundle(bundle)

    assert result["status"] == "pass_report_bundle_verification"
    assert result["file_count"] == 2
    manifest = json.loads((bundle / "report_manifest.json").read_text(encoding="utf-8"))
    assert {row["path"] for row in manifest["files"]} == {"index.html", "assets/evidence.json"}


def test_manifest_rejects_content_changes_and_extra_files(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "index.html").write_text("changed\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="size mismatch|SHA-256 mismatch"):
        verify_bundle(bundle)

    bundle = make_bundle(tmp_path / "second")
    (bundle / "stale.txt").write_text("stale\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="file set mismatch"):
        verify_bundle(bundle)


def test_sync_replaces_stale_destination_and_matches_source(tmp_path: Path) -> None:
    source = make_bundle(tmp_path)
    destination = tmp_path / "downloads" / "report"
    destination.mkdir(parents=True)
    (destination / "stale.txt").write_text("stale\n", encoding="utf-8")

    result = sync_bundle(source, destination)

    assert result["status"] == "pass_transactional_report_sync"
    assert not (destination / "stale.txt").exists()
    assert verify_bundle(destination)["manifest_sha256"] == verify_bundle(source)["manifest_sha256"]
