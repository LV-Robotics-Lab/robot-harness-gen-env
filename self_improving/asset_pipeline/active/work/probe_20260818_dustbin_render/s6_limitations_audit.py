#!/usr/bin/env python3
"""probe s6: measure the current truth behind each of README's six "known
limitations", so each one can be closed, rewritten with real numbers, or kept
as a genuine open item -- rather than judged from memory.

A limitation that is merely a stale sentence is worse than no sentence: it
sends a reader looking for a problem that was fixed months ago.
"""

import json
import pathlib
import collections
import sys

A = pathlib.Path(sys.argv[1])
LIB = A / "data/asset_library"
UP = A / "data/upstream_ledgers"


def ledgers(root):
    for f in sorted(root.glob("*/*/ledger.json")):
        yield f
    for f in sorted(root.glob("*/ledger.json")):
        yield f


print("=== 限制 5：外部资产许可证标 unknown ===")
lic = collections.Counter()
spdx = collections.Counter()
for f in ledgers(LIB):
    for m in json.loads(f.read_text()).get("models", []):
        l = (m.get("source") or {}).get("license") or {}
        lic[l.get("status", "(缺字段)")] += 1
        spdx[l.get("spdx") or "(null)"] += 1
print("  我方资产 license.status:", dict(lic))
print("  spdx:", dict(spdx.most_common(6)))

print()
print("=== 限制 6：质量默认 0.1kg ===")
mass = collections.Counter()
vals = collections.Counter()
for f in ledgers(LIB):
    for m in json.loads(f.read_text()).get("models", []):
        ph = m.get("physical") or {}
        mm = ph.get("mass") or {}
        if isinstance(mm, dict):
            mass[mm.get("status", "(缺字段)")] += 1
            vals[mm.get("value_kg")] += 1
        else:
            mass["(非 dict)"] += 1
            vals[mm] += 1
print("  mass.status:", dict(mass))
print("  取值分布(前6):", dict(vals.most_common(6)))

print()
print("=== 限制 1：碰撞体默认视觉副本 ===")
census = A / "work/probe_20260818_dustbin_render/out/collision_census.jsonl"
if census.exists():
    rows = [json.loads(l) for l in census.read_text().splitlines() if l.strip()]
    kinds = collections.Counter(
        r.get("collision_kind") for r in rows if "error" not in r
    )
    print("  实测:", dict(kinds))
else:
    print("  (需要先跑 s4 普查)")

print()
print("=== 限制 2/3：滚动件定姿 & 穿模判据 ===")
rev = A / "data/scene_gen_ext/runtime_revocations.json"
if rev.exists():
    d = json.loads(rev.read_text())
    body = d.get("models", d)
    print("  runtime_revocations 条目:", len(body))
    reasons = collections.Counter()
    for k, v in body.items() if isinstance(body, dict) else []:
        r = v.get("reason") if isinstance(v, dict) else str(v)
        reasons[str(r)[:60]] += 1
    for r, n in reasons.most_common(8):
        print("    %-62s %d" % (r, n))
else:
    print("  无 runtime_revocations.json")

ov = A / "data/scene_gen_ext/asset_overrides_ext.yml"
if ov.exists():
    import yaml

    data = yaml.safe_load(ov.read_text()) or {}
    assets = data.get("assets", {})
    revoked = infeas = 0
    for aid, e in assets.items():
        for mid, m in (e.get("models") or {}).items():
            if "placement_revoked" in m:
                revoked += 1
            if "placement_infeasible" in m:
                infeas += 1
    print(
        "  扩展 overrides: placement_revoked=%d  placement_infeasible=%d (共 %d 资产)"
        % (revoked, infeas, len(assets))
    )

print()
print("=== 限制 4：A 线脚本 s1/s2 ===")
for p in ("1_asset_reuse/scripts", "1_asset_reuse/archive"):
    hits = sorted((A / p).rglob("s[12]_*.py"))
    print("  %-26s %s" % (p, [h.relative_to(A).as_posix() for h in hits] or "无"))
