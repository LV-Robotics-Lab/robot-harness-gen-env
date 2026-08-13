#!/usr/bin/env python3
"""Verify the official RoboTwin background archive and its operational subset."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from pose_conditioned_trajectory_policy import sha256_file, write_json


EXPECTED_ARCHIVE_SHA256 = "54ede0fb5b783e0faa2bc98720d3affd6ca3bb9280b225b48c1aafaf31473070"
EXPECTED_ARCHIVE_SIZE = 10_970_687_027


def build_receipt(archive: Path, seen_dir: Path, out_path: Path, subset_count: int) -> dict:
    archive = archive.expanduser().resolve()
    seen_dir = seen_dir.expanduser().resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"RoboTwin background archive is missing: {archive}")
    archive_size = archive.stat().st_size
    archive_sha256 = sha256_file(archive)
    if archive_size != EXPECTED_ARCHIVE_SIZE:
        raise ValueError(f"Archive size mismatch: expected {EXPECTED_ARCHIVE_SIZE}, found {archive_size}")
    if archive_sha256 != EXPECTED_ARCHIVE_SHA256:
        raise ValueError(f"Archive SHA-256 mismatch: {archive_sha256}")

    expected_files = [seen_dir / f"{index}.png" for index in range(subset_count)]
    missing = [str(path) for path in expected_files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Operational background subset is incomplete: {missing[:5]}")
    extracted_size = sum(path.stat().st_size for path in expected_files)
    subset_digest_payload = "".join(
        f"{path.name}\t{path.stat().st_size}\t{sha256_file(path)}\n"
        for path in expected_files
    ).encode("utf-8")
    import hashlib

    receipt = {
        "schema_version": "alchedata.robotwin_background_asset_receipt.v0",
        "status": "pass_official_robotwin_background_asset",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "dataset": "TianxingChen/RoboTwin2.0",
            "file": "background_texture.zip",
            "url": "https://huggingface.co/datasets/TianxingChen/RoboTwin2.0",
            "declared_by": "external/RoboTwin/assets/_download.py",
        },
        "archive": {
            "path": str(archive),
            "size_bytes": archive_size,
            "sha256": archive_sha256,
        },
        "operational_subset": {
            "directory": str(seen_dir),
            "selection": f"official contiguous seen texture ids 0..{subset_count - 1}",
            "file_count": subset_count,
            "size_bytes": extracted_size,
            "manifest_sha256": hashlib.sha256(subset_digest_payload).hexdigest(),
            "runtime_contract": (
                "RoboTwin counts files in assets/background_texture/seen and samples integer ids in [0, count). "
                "A contiguous 0-based subset therefore exercises the unmodified random_background loader."
            ),
        },
        "claim_boundary": (
            "The full official archive is downloaded and hash-verified. Exactly the declared contiguous official "
            "seen subset is extracted for this bounded evaluation; the result is not a claim that every archive "
            "texture was exercised."
        ),
    }
    write_json(out_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--seen-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--subset-count", type=int, default=256)
    args = parser.parse_args()
    receipt = build_receipt(Path(args.archive), Path(args.seen_dir), Path(args.out), args.subset_count)
    print(json.dumps({"status": receipt["status"], **receipt["archive"], **receipt["operational_subset"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
