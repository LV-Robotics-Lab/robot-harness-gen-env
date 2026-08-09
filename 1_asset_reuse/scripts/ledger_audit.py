#!/usr/bin/env python3
"""Whole-library ledger audit (H2 hardening 1): validate every asset's
ledger.json under --library-dir with validate_ledger(check_files=True), in
one sweep.

This is the "bridge/orphan file integrity moved to audit layer" piece
carried over from import_materialize's per-model admission gate (T5 review:
that gate only ever checks the model being admitted THIS run --
check_files=True there is scoped to a single-model ledger by design, so a
sibling model's files going stale/missing on disk between runs, or one of
the 6 assets never promoted to v1, has no automated sweep watching it. This
tool is that sweep. It doesn't re-implement any check -- validate_ledger
already reports file_missing / sha256_mismatch / everything else; this tool
just runs it over every asset dir and files the results into one report.

Assets with no ledger.json at all (pre-v1, or one of the excluded assets
noted in the delivery record) are NOT a violation -- they're recorded under
`no_ledger` as an informational item only when the asset dir actually looks
materialized (has a model_data*.json marker, flat or articulated layout);
an asset dir with neither a ledger nor any model markers is silently
skipped (not an asset this tool has an opinion about).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger


def _has_model_markers(asset_dir):
    """True iff asset_dir looks materialized: a flat model_data<N>.json
    (rigid layout) or <N>/model_data<N>.json (articulated layout, s13b's own
    --instance-dir convention)."""
    if any(asset_dir.glob("model_data*.json")):
        return True
    for sub in asset_dir.iterdir():
        if sub.is_dir() and sub.name.isdigit():
            if (sub / f"model_data{sub.name}.json").exists():
                return True
    return False


def audit(library_dir):
    """Sweep every asset dir under library_dir. Returns
    {"audited": n, "clean": [...], "violations": {...}, "no_ledger": [...]}.
    `audited` counts ledger.json files actually run through validate_ledger
    (== len(clean) + len(violations)); no_ledger assets were never validated
    (there's nothing to validate) and aren't counted in it."""
    lib = Path(library_dir)
    report = {"audited": 0, "clean": [], "violations": {}, "no_ledger": []}
    for asset_dir in sorted(
        p for p in lib.iterdir() if p.is_dir() and not p.name.startswith("_")
    ):
        asset = asset_dir.name
        lp = asset_dir / "ledger.json"
        if not lp.exists():
            if _has_model_markers(asset_dir):
                report["no_ledger"].append(asset)
            continue

        report["audited"] += 1
        try:
            led = json.loads(lp.read_text())
        except json.JSONDecodeError as exc:
            # A corrupt ledger.json is itself the worst violation this tool
            # can report -- surface it instead of crashing the whole sweep
            # over one bad asset.
            report["violations"][asset] = [
                {"path": "", "code": "invalid_json", "message": str(exc)}
            ]
            continue

        violations = ledger.validate_ledger(led, check_files=True)
        if violations:
            report["violations"][asset] = [
                {"path": v.path, "code": v.code, "message": v.message}
                for v in violations
            ]
        else:
            report["clean"].append(asset)

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", required=True)
    parser.add_argument(
        "--out", help="write the full report as JSON here; default: stdout summary"
    )
    args = parser.parse_args()

    report = audit(args.library_dir)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n")
    else:
        print(f"audited: {report['audited']}")
        print(f"clean: {len(report['clean'])}")
        print(f"violations: {len(report['violations'])} asset(s)")
        for asset in sorted(report["violations"]):
            print(f"  {asset}: {len(report['violations'][asset])} violation(s)")
        print(f"no_ledger: {len(report['no_ledger'])} asset(s)")
        for asset in sorted(report["no_ledger"]):
            print(f"  {asset}")

    sys.exit(1 if report["violations"] else 0)


if __name__ == "__main__":
    main()
