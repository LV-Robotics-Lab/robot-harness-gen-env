#!/usr/bin/env python3
"""Verification write-back: the pipeline obligation the v3 dialectic exposed.

Finding (2026-08-15 migration skeptic, verified): the whole library carried
ZERO backend=isaacsim verification records -- including the asset whose
Isaac E2E had demonstrably passed. Evidence lived in results/ directories;
the ledger, whose verification[] exists precisely to answer "which gates has
this model passed, on which backend", never heard about any of it. Contract
clause since v3: an Isaac run that does not write back is NOT finished.

This tool is the single write-back path: give it (asset, model, check,
verdict) facts -- typically produced by 2_sim_migration's Isaac settle
harness -- and it appends properly-anchored records (digest = the ISAACSIM
representation set, canonical timestamp, run_id idempotency) and re-validates
the ledger before writing.

Usage:
  writeback_verification.py --results <facts.json> [--library <dir>]
  facts.json: [{"asset_dir": "302_can", "model_id": 0, "check": "settle",
                "verdict": "pass", "run_id": "...", "report_path": "..."}]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

DEV = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DEV / "1_asset_reuse"))

from lib import ledger as L  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--library", default=str(DEV / "data/asset_library"))
    ap.add_argument("--upstream", default=str(DEV / "data/upstream_ledgers"))
    ap.add_argument("--backend", default="isaacsim")
    a = ap.parse_args()

    facts = json.loads(Path(a.results).read_text())
    n_ok = n_skip = 0
    for f in facts:
        lp = None
        for root in (Path(a.library), Path(a.upstream)):
            cand = root / f["asset_dir"] / "ledger.json"
            if cand.is_file():
                lp = cand
                break
        if lp is None:
            print(f"SKIP {f['asset_dir']}: no ledger")
            n_skip += 1
            continue
        led = json.loads(lp.read_text())
        model = next((m for m in led["models"] if m["model_id"] == f["model_id"]), None)
        if model is None:
            print(f"SKIP {f['asset_dir']} m{f['model_id']}: no such model")
            n_skip += 1
            continue
        run_id = f["run_id"]
        if any(
            v.get("run_id") == run_id
            and v.get("check") == f["check"]
            and v.get("backend") == a.backend
            for v in model.get("verification", [])
        ):
            print(
                f"skip {f['asset_dir']} m{f['model_id']}: run {run_id} already recorded"
            )
            n_skip += 1
            continue
        rec = {
            "backend": a.backend,
            "check": f["check"],
            "verdict": f["verdict"],
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "verified_digest": L.reps_digest(model, a.backend),
        }
        if f.get("report_path"):
            # public-repo path hygiene: never store /home/<user>; portable
            # (ACTIVE_ROOT-relative) when inside the tree, absolute otherwise
            rec["report_path"] = L.to_portable_uri(f["report_path"])
        model.setdefault("verification", []).append(rec)
        hard = [
            v
            for v in L.validate_ledger(led, check_files=False)
            if v.code != "profile_requirement_unmet"
        ]
        if hard:
            print(f"FAIL {f['asset_dir']}: invalid after append ({hard[0].code})")
            n_skip += 1
            continue
        lp.write_text(json.dumps(led, indent=2) + "\n")
        n_ok += 1
        print(
            f"ok   {f['asset_dir']} m{f['model_id']}: ({a.backend},{f['check']})={f['verdict']}"
        )
    print(f"\nwritten={n_ok} skipped={n_skip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
