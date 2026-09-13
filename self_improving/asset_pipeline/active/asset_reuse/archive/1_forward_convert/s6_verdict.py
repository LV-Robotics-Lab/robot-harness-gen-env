#!/usr/bin/env python3
"""Final smoke verdict from artifact CONTENT (Kit swallows exit codes, so
isaac-side steps cannot be judged by return status)."""

import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
ok = True


def check(label, cond, detail=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'} {label}{' ' + detail if detail else ''}")
    ok = ok and bool(cond)


for name in ("sapien_validation.json", "isaac_validation.json"):
    p = out / name
    if not p.exists():
        check(name, False, "(missing)")
        continue
    rep = json.loads(p.read_text())
    for asset in ("bottle", "cabinet"):
        st = rep.get(asset, {}).get("status")
        check(f"{name}:{asset}", st == "pass", f"status={st}")

for name in ("bottle_bundle.json", "cabinet_bundle.json"):
    b = json.loads((out / name).read_text())
    reps = [r for r in b["representations"] if r["backend"] == "isaacsim"]
    check(f"{name}:isaacsim-rep", bool(reps) and Path(reps[0]["uri"]).is_file())

print("VERDICT PASS" if ok else "VERDICT FAIL")
sys.exit(0 if ok else 1)
