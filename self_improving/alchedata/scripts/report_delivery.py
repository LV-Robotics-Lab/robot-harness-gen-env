#!/usr/bin/env python3
"""Build, verify, and transactionally mirror the four TODO report bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "report_manifest.json"
BUNDLES = {
    "sceneagent": {
        "source": ROOT / "reports/sceneagent_selection2env",
        "download_name": "alchedata_sceneagent_selection2env_20260712",
        "schema_version": "alchedata.sceneagent_report_manifest.v1",
    },
    "text2env": {
        "source": ROOT / "reports/text2env_literature_review",
        "download_name": "text2env_literature_review_20260713",
        "schema_version": "alchedata.text2env_report_manifest.v1",
    },
    "openxsim": {
        "source": ROOT / "reports/openxsim_command_loop",
        "download_name": "openxsim_command_loop_20260713",
        "schema_version": "alchedata.openxsim_report_manifest.v1",
    },
    "harness": {
        "source": ROOT / "reports/embodied_harness",
        "download_name": "embodied_harness_20260713",
        "schema_version": "alchedata.embodied_harness_report_manifest.v1",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def observed_files(bundle: Path) -> dict[str, Path]:
    return {
        path.relative_to(bundle).as_posix(): path
        for path in sorted(bundle.rglob("*"))
        if path.is_file() and path.name != MANIFEST_NAME
    }


def write_manifest(bundle: Path, schema_version: str) -> dict[str, Any]:
    if not bundle.is_dir():
        raise FileNotFoundError(f"Report bundle is missing: {bundle}")
    rows = [
        {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for relative, path in observed_files(bundle).items()
    ]
    manifest = {
        "schema_version": schema_version,
        "status": "pass_report_bundle",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(rows),
        "files": rows,
    }
    (bundle / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_bundle(bundle: Path, expected_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest_path = bundle / MANIFEST_NAME
    if not manifest_path.is_file():
        raise AssertionError(f"Report manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "pass_report_bundle":
        raise AssertionError(f"Report manifest status is not pass_report_bundle: {manifest_path}")
    if expected_manifest is not None and manifest != expected_manifest:
        raise AssertionError(f"Mirrored manifest differs from source: {manifest_path}")

    rows = manifest.get("files", [])
    if manifest.get("file_count") != len(rows):
        raise AssertionError(f"Report manifest file count mismatch: {manifest_path}")
    declared = {row["path"]: row for row in rows}
    if len(declared) != len(rows):
        raise AssertionError(f"Report manifest contains duplicate paths: {manifest_path}")
    observed = observed_files(bundle)
    if set(declared) != set(observed):
        missing = sorted(set(declared) - set(observed))
        extra = sorted(set(observed) - set(declared))
        raise AssertionError(f"Report file set mismatch: missing={missing} extra={extra}")

    total_bytes = 0
    for relative, row in declared.items():
        path = observed[relative]
        size = path.stat().st_size
        if size != row["bytes"]:
            raise AssertionError(f"Report size mismatch: {path}")
        digest = sha256(path)
        if digest != row["sha256"]:
            raise AssertionError(f"Report SHA-256 mismatch: {path}")
        total_bytes += size
    return {
        "status": "pass_report_bundle_verification",
        "bundle": str(bundle),
        "file_count": len(rows),
        "total_bytes": total_bytes,
        "manifest_sha256": sha256(manifest_path),
    }


def sync_bundle(source: Path, destination: Path) -> dict[str, Any]:
    source_manifest = json.loads((source / MANIFEST_NAME).read_text(encoding="utf-8"))
    source_result = verify_bundle(source, source_manifest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{destination.name}.stage-", dir=destination.parent))
    staging = staging_root / destination.name
    backup = destination.parent / f".{destination.name}.backup-{uuid.uuid4().hex}"
    moved_old_destination = False
    try:
        shutil.copytree(source, staging)
        verify_bundle(staging, source_manifest)
        if destination.exists():
            destination.rename(backup)
            moved_old_destination = True
        staging.rename(destination)
        destination_result = verify_bundle(destination, source_manifest)
    except Exception:
        if destination.exists() and moved_old_destination:
            shutil.rmtree(destination)
        if backup.exists():
            backup.rename(destination)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
    return {
        "status": "pass_transactional_report_sync",
        "source": source_result,
        "destination": destination_result,
    }


def configured_bundle(bundle_id: str) -> dict[str, Any]:
    try:
        return BUNDLES[bundle_id]
    except KeyError as exc:
        raise ValueError(f"Unknown report bundle: {bundle_id}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-manifest", action="append", choices=sorted(BUNDLES), default=[])
    parser.add_argument("--sync-downloads", action="store_true")
    parser.add_argument("--verify-downloads", action="store_true")
    parser.add_argument("--downloads-root", type=Path, default=Path.home() / "Downloads")
    args = parser.parse_args()

    for bundle_id in args.write_manifest:
        config = configured_bundle(bundle_id)
        write_manifest(config["source"], config["schema_version"])

    results: dict[str, Any] = {}
    for bundle_id, config in BUNDLES.items():
        source = config["source"]
        source_result = verify_bundle(source)
        row: dict[str, Any] = {"source": source_result}
        destination = args.downloads_root.expanduser().resolve() / config["download_name"]
        if args.sync_downloads:
            row["sync"] = sync_bundle(source, destination)
        if args.verify_downloads:
            source_manifest = json.loads((source / MANIFEST_NAME).read_text(encoding="utf-8"))
            row["downloads"] = verify_bundle(destination, source_manifest)
        results[bundle_id] = row

    print(
        json.dumps(
            {
                "status": "pass_report_delivery",
                "downloads_root": str(args.downloads_root.expanduser().resolve()),
                "bundles": results,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
