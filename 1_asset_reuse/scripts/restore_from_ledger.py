#!/usr/bin/env python3
"""Restore-from-ledger: verify per-asset ledger representation files against
their recorded sha256 digests, and (--restore) re-fetch MISSING ones from
recorded provenance.

Reads every data/asset_library/*/ledger.json (schema: lib/ledger.py). Never
writes ledger.json itself -- read-only regarding ledger content.

Recoverability, by design: a representation's `uri` is only re-fetchable
when its local filename matches an entry in the SOURCE_MANIFEST.json named
by `source.source_manifest_path` (written today only for nvidia_server-tier
imports, at `lib/_source/<group>/SOURCE_MANIFEST.json`, prefix = the S3 key
prefix under BUCKET below). That's true for the single "raw source copy"
representation (backend=isaacsim, uri under `_source/<group>/`) but NOT for
the sapien visual/collision glb representations -- those are locally-derived
conversion products (offline mesh conversion / coacd decomposition) with no
independent URL of their own; re-deriving them needs the conversion
pipeline, not a plain HTTP fetch, so they are correctly reported
UNRECOVERABLE rather than silently skipped or wrongly "restored" from
mismatched content.

`_resolve_download_url` also accepts an absolute-URL prefix (http/https),
not just an S3-relative one -- this generalizes the same recorded-provenance
mechanism to any source that fetch_asset-style tooling pre-populates a
SOURCE_MANIFEST.json sidecar for (e.g. lib/a6_embodiedgen_source.py), not
just NVIDIA's bucket.
"""

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

# Mirrors lib.a1_providers.BUCKET (not imported, to keep this tool
# standalone/decoupled from the tier-registry provider module).
BUCKET = "https://omniverse-content-production.s3-us-west-2.amazonaws.com"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_fetch(url, timeout_s=60):
    with urllib.request.urlopen(url, timeout=timeout_s) as r:
        return r.read()


def iter_ledgers(library_dir, asset_filter=None):
    lib = Path(library_dir)
    if not lib.is_dir():
        return
    for d in sorted(lib.iterdir()):
        if not d.is_dir():
            continue
        if asset_filter and d.name != asset_filter:
            continue
        lp = d / "ledger.json"
        if not lp.is_file():
            continue
        try:
            ledger = json.loads(lp.read_text())
        except json.JSONDecodeError:
            continue
        yield d.name, ledger


def verify_library(library_dir, asset_filter=None):
    """Per-asset OK/MISSING/HASH_MISMATCH report. Read-only."""
    results = []
    for asset, ledger in iter_ledgers(library_dir, asset_filter):
        problems = []
        for model in ledger.get("models", []):
            for i, rep in enumerate(model.get("representations", [])):
                uri = rep.get("uri")
                if not uri:
                    continue
                p = Path(uri)
                if not p.is_file():
                    problems.append(
                        {
                            "model": model.get("model_id"),
                            "rep_index": i,
                            "uri": uri,
                            "status": "MISSING",
                        }
                    )
                    continue
                expected = rep.get("sha256")
                if expected:
                    actual = sha256_file(p)
                    if actual != expected:
                        problems.append(
                            {
                                "model": model.get("model_id"),
                                "rep_index": i,
                                "uri": uri,
                                "status": "HASH_MISMATCH",
                                "expected_sha256": expected,
                                "actual_sha256": actual,
                            }
                        )
        if not problems:
            status = "OK"
        elif any(pr["status"] == "MISSING" for pr in problems):
            status = "MISSING"
        else:
            status = "HASH_MISMATCH"
        results.append({"asset": asset, "status": status, "problems": problems})
    return results


def _load_source_manifest(path):
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_download_url(prefix, filename):
    if prefix.startswith("http://") or prefix.startswith("https://"):
        return prefix.rstrip("/") + "/" + filename
    return BUCKET + "/" + urllib.parse.quote(prefix.rstrip("/") + "/" + filename)


def restore_library(library_dir, asset_filter=None, fetch_fn=None):
    """Re-fetch representations whose file is MISSING (present-but-corrupt
    files are left alone -- verify-detected HASH_MISMATCH on an existing
    file is not auto-repaired here). Does not touch ledger.json or rebuild
    the catalog; the caller (main/CLI) prints the catalog-rebuild hint."""
    fetch_fn = fetch_fn or _default_fetch
    restored, mismatched, unrecoverable = [], [], []
    for asset, ledger in iter_ledgers(library_dir, asset_filter):
        for model in ledger.get("models", []):
            source = model.get("source", {})
            manifest = _load_source_manifest(source.get("source_manifest_path"))
            for rep in model.get("representations", []):
                uri = rep.get("uri")
                if not uri:
                    continue
                p = Path(uri)
                if p.is_file():
                    continue
                base = {"asset": asset, "model": model.get("model_id"), "uri": uri}
                if manifest is None or "prefix" not in manifest:
                    unrecoverable.append(
                        {
                            **base,
                            "reason": "no source_manifest_path recorded "
                            "(local-origin or unknown provenance)",
                        }
                    )
                    continue
                filename = p.name
                if filename not in manifest.get("files", {}):
                    unrecoverable.append(
                        {
                            **base,
                            "reason": f"{filename!r} not present in recorded "
                            "SOURCE_MANIFEST (locally-derived representation, "
                            "no independent source)",
                        }
                    )
                    continue
                url = _resolve_download_url(manifest["prefix"], filename)
                try:
                    data = fetch_fn(url)
                except Exception as exc:  # noqa: BLE001
                    unrecoverable.append(
                        {
                            **base,
                            "url": url,
                            "reason": f"fetch failed: {type(exc).__name__}: {exc}",
                        }
                    )
                    continue
                expected_sha = rep.get("sha256")
                actual_sha = hashlib.sha256(data).hexdigest()
                p.parent.mkdir(parents=True, exist_ok=True)
                if expected_sha and actual_sha != expected_sha:
                    mismatch_path = p.with_name(p.name + ".mismatch")
                    mismatch_path.write_bytes(data)
                    mismatched.append(
                        {
                            **base,
                            "url": url,
                            "expected_sha256": expected_sha,
                            "actual_sha256": actual_sha,
                            "quarantine_path": str(mismatch_path),
                        }
                    )
                    continue
                p.write_bytes(data)
                restored.append({**base, "url": url})
    return {
        "restored": restored,
        "mismatched": mismatched,
        "unrecoverable": unrecoverable,
    }


def resolve_library_dir(dev_root=None, library_dir=None):
    if library_dir:
        return Path(library_dir)
    if dev_root:
        return Path(dev_root) / "data" / "asset_library"
    raise SystemExit("either --library-dir or --dev-root is required")


def main(argv=None, fetch_fn=None):
    ap = argparse.ArgumentParser(
        description="Verify (default) or restore data/asset_library/*/ledger.json "
        "representation files against their recorded sha256 digests."
    )
    ap.add_argument("--dev-root", default=None)
    ap.add_argument(
        "--library-dir", default=None, help="default: <dev-root>/data/asset_library"
    )
    ap.add_argument("--asset", default=None, help="scope to a single asset id")
    ap.add_argument(
        "--verify", action="store_true", help="verify only, no writes (default)"
    )
    ap.add_argument(
        "--restore",
        action="store_true",
        help="re-fetch MISSING representation files from recorded provenance",
    )
    args = ap.parse_args(argv)
    library_dir = resolve_library_dir(args.dev_root, args.library_dir)

    results = verify_library(library_dir, asset_filter=args.asset)
    problems_total = 0
    for r in results:
        print(f"{r['status']} {r['asset']}")
        for pr in r["problems"]:
            print(
                f"  {pr['status']} model={pr['model']} rep{pr['rep_index']} {pr['uri']}"
            )
            problems_total += 1
    print(
        f"SUMMARY {'OK' if problems_total == 0 else 'PROBLEMS'} assets={len(results)} problems={problems_total}"
    )

    if not args.restore:
        return 0 if problems_total == 0 else 1

    restore_result = restore_library(
        library_dir, asset_filter=args.asset, fetch_fn=fetch_fn
    )
    for r in restore_result["restored"]:
        print(f"RESTORED {r['asset']} {r['uri']}")
    for r in restore_result["mismatched"]:
        print(
            f"MISMATCH {r['asset']} {r['uri']} -> quarantined at {r['quarantine_path']}"
        )
    for r in restore_result["unrecoverable"]:
        print(f"UNRECOVERABLE {r['asset']} {r['uri']}: {r['reason']}")
    print(
        "Catalog was NOT rebuilt automatically -- restored files won't show up until "
        "you run (production catalog-rebuild flow, not this tool):\n"
        "  python scripts/5_catalog/s9_build_shadow_root.py --library-dir <library> "
        "--shadow <shadow> --ext-dir <ext>"
    )

    post = verify_library(library_dir, asset_filter=args.asset)
    remaining = sum(len(r["problems"]) for r in post)
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
