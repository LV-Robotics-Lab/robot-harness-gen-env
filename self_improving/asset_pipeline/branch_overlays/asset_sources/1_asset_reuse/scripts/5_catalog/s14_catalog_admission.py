#!/usr/bin/env python3
"""Catalog-view admission check (env-gen-yuxin env). VIEW layer, not pool layer.

For every external asset (3XX) in a built catalog view, compile one standard
prompt against a single-asset filtered catalog using the UPSTREAM compiler.
Solver acceptance decides admission INTO THIS VIEW ONLY — the asset pool and
its ledgers are never touched, and re-running s9 restores everything.

Outcomes per asset: admitted / not_admitted(reason) / skipped_vocab.
With --enforce, not_admitted external entries are filtered out of the view
catalog file (recorded in the report; reversible).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument(
    "--catalog", required=True, help="view catalog json (read, optionally rewritten)"
)
parser.add_argument(
    "--upstream", default="/home/jingxiang/yuxin/env-gen-dev/external/env-gen-github"
)
parser.add_argument("--work-dir", required=True, help="scratch dir for compile outputs")
parser.add_argument("--report", required=True)
parser.add_argument("--enforce", action="store_true")
args = parser.parse_args()

sys.path.insert(0, args.upstream)
from scene_gen.parser import OBJECT_TERMS  # noqa: E402  (upstream, read-only import)

cat_path = Path(args.catalog)
work = Path(args.work_dir)
work.mkdir(parents=True, exist_ok=True)
catalog = json.loads(cat_path.read_text())

results = []
for entry in catalog["entries"]:
    aid = entry["asset_id"]
    if not (aid[:3].isdigit() and 300 <= int(aid[:3]) < 900):
        continue
    if not any(m.get("usable") for m in entry.get("models", [])):
        continue
    category = entry["category"]
    row = {"asset_id": aid, "category": category}
    if category not in OBJECT_TERMS:
        row.update(
            status="skipped_vocab",
            note="category not in upstream parser vocabulary; kept in view",
        )
        results.append(row)
        continue
    single = dict(catalog)
    single["entries"] = [entry]
    single_path = work / f"single_{aid}.json"
    single_path.write_text(json.dumps(single))
    prompt = f"Place a {category.replace('_', ' ')} on the table."
    proc = subprocess.run(
        [
            sys.executable,
            "script/generate_scene.py",
            "--prompt",
            prompt,
            "--seed",
            "42",
            "--asset-catalog",
            str(single_path),
            "--out-root",
            str(work / "scenes"),
        ],
        capture_output=True,
        text=True,
        cwd=args.upstream,
        timeout=180,
    )
    ok = proc.returncode == 0 and "PASS" in proc.stdout
    if ok:
        row["status"] = "admitted"
    else:
        reason = "compile failed"
        for line in (work / "scenes").glob("*/failure_report.json"):
            try:
                blocker = json.loads(line.read_text()).get("blocker")
                reasons = set()
                for a in json.loads(line.read_text()).get("attempts", [])[:20]:
                    reasons.update(a.get("reasons", []))
                reason = f"{blocker}: {'; '.join(sorted(reasons))[:160]}"
            except Exception:  # noqa: BLE001
                pass
        row.update(status="not_admitted", reason=reason)
    results.append(row)
    print(
        f"{row['status'].upper()} {aid} ({category})"
        + (f" — {row.get('reason', '')}" if row.get("reason") else "")
    )

dropped = []
if args.enforce:
    bad = {r["asset_id"] for r in results if r["status"] == "not_admitted"}
    if bad:
        catalog["entries"] = [e for e in catalog["entries"] if e["asset_id"] not in bad]
        cat_path.write_text(json.dumps(catalog, indent=1))
        dropped = sorted(bad)

report = {
    "catalog": str(cat_path),
    "enforce": args.enforce,
    "dropped_from_view": dropped,
    "results": results,
}
Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False))
n_adm = sum(1 for r in results if r["status"] == "admitted")
n_bad = sum(1 for r in results if r["status"] == "not_admitted")
n_skip = sum(1 for r in results if r["status"] == "skipped_vocab")
print(
    f"ADMISSION admitted={n_adm} not_admitted={n_bad} skipped_vocab={n_skip} "
    f"dropped_from_view={len(dropped)}"
)
