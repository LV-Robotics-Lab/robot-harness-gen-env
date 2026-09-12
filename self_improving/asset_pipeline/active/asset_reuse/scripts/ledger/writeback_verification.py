#!/usr/bin/env python3
"""Write already-published qualified verification evidence into one ledger.

The backend is explicit and limited to the sealed issuer registry.  At the
current tranche that registry qualifies SAPIEN producers only; an Isaac result
must remain untrusted until an Isaac-specific producer and evaluator are
implemented and replayed.

This tool is the single write-back path.  It consumes only an already-published
immutable evidence artifact; command-line JSON fields cannot manufacture a
physical verdict or representation binding.

Usage:
  writeback_verification.py --backend sapien --results <facts.json> [--library <dir>]
  facts.json: [{"asset_dir": "302_can", "model_id": 0,
                "evidence": {<asset_verification_evidence.v1 record>}}]
"""

import argparse
import json
import sys
from pathlib import Path

DEV = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DEV / "asset_reuse"))

from lib import ledger as L  # noqa: E402
from lib import ledger_writes  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--library", default=str(DEV / "data/asset_library"))
    ap.add_argument("--upstream", default=str(DEV / "data/upstream_ledgers"))
    ap.add_argument(
        "--backend",
        required=True,
        choices=L.qualified_verification_backends(),
    )
    a = ap.parse_args()

    facts = json.loads(Path(a.results).read_text())
    n_ok = n_skip = n_fail = 0
    seen = set()
    prepared_facts = []
    try:
        if not isinstance(facts, list):
            raise ValueError("results must be a list")
        for f in facts:
            if not isinstance(f, dict) or set(f) != {"asset_dir", "model_id", "evidence"}:
                raise ValueError("each fact must contain only asset_dir, model_id, and evidence")
            asset, model_id = L.claim_asset_model(seen, f["asset_dir"], f["model_id"])
            prepared_facts.append((f, asset, model_id))
    except (
        ValueError,
        L.UnsafeAssetKeyError,
        L.InvalidModelIdError,
        L.DuplicateAssetModelError,
    ) as exc:
        print(f"FAIL input: {exc}")
        return 1

    for f, asset, model_id in prepared_facts:
        loaded = None
        for root in (Path(a.library), Path(a.upstream)):
            try:
                loaded = L.load_asset_ledger(root, asset)
                break
            except FileNotFoundError:
                continue
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"FAIL {asset}: unsafe or invalid ledger ({exc})")
                n_fail += 1
                loaded = False
                break
        if loaded is False:
            continue
        if loaded is None:
            print(f"SKIP {asset}: no ledger")
            n_skip += 1
            continue
        model = next((m for m in loaded.document["models"] if m["model_id"] == model_id), None)
        if model is None:
            print(f"SKIP {asset} m{model_id}: no such model")
            n_skip += 1
            continue
        rec = L.verification_from_trusted_evidence(model, f["evidence"], asset)
        if rec is None or rec["backend"] != a.backend:
            print(
                f"FAIL {asset} m{model_id}: evidence is missing, invalid, stale, "
                f"or not for backend {a.backend!r}"
            )
            n_fail += 1
            continue
        try:
            ledger_writes.append_validated_verification(
                loaded.ledger_path, model_id, rec, asset_key=asset
            )
        except (
            ledger_writes.LedgerWriteError,
            ledger_writes.EvidenceDigestError,
            L.VerificationConflictError,
            L.LedgerIdentityError,
        ) as exc:
            code = (
                exc.violations[0].code
                if isinstance(exc, ledger_writes.LedgerWriteError)
                else "conflict"
                if isinstance(exc, L.VerificationConflictError)
                else "verified_digest"
            )
            print(f"FAIL {asset}: invalid after append ({code})")
            n_fail += 1
            continue
        n_ok += 1
        print(f"ok   {asset} m{model_id}: ({a.backend},{rec['check']})={rec['verdict']}")
    print(f"\nwritten={n_ok} skipped={n_skip} failed={n_fail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
