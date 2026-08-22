#!/usr/bin/env python3
"""probe s4: library-wide census of collision-mesh quality.

Two kinds of collision geometry exist in the pool:
  * CoACD decomposition  -- many watertight convex parts, has a volume
  * visual copy (default) -- whatever the source mesh was; for photogrammetry
    scans that is a non-watertight soup of hundreds of disconnected components

The second kind is what README's first known limitation describes. This census
says how many assets carry it and pairs that with each asset's measured
late-window rotation, so the link between "collision is a raw scan" and "never
quite settles" is a number rather than a hunch.
"""

import json
import pathlib
import sys

import trimesh

LIB = pathlib.Path(sys.argv[1])
ROWS = pathlib.Path(sys.argv[2])
SETTLE = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else None

late_by_asset = {}
if SETTLE and SETTLE.exists():
    for line in SETTLE.read_text().splitlines():
        r = json.loads(line)
        a = r.get("asset_id")
        if a:
            late_by_asset.setdefault(a, []).append(r["late_rot"])

out = []
for ledger_file in sorted(LIB.glob("*/*/ledger.json")):
    adir = ledger_file.parent
    col = sorted((adir / "collision").glob("base*.glb"))
    vis = sorted((adir / "visual").glob("base*.glb"))
    if not col:
        continue
    try:
        c = trimesh.load(str(col[0]), force="mesh")
        v = trimesh.load(str(vis[0]), force="mesh") if vis else None
    except Exception as exc:  # noqa: BLE001
        out.append({"asset": adir.name, "error": str(exc)[:80]})
        continue
    comps = len(c.split(only_watertight=False))
    same_as_visual = bool(
        v is not None
        and len(c.faces) == len(v.faces)
        and len(c.vertices) == len(v.vertices)
    )
    lates = late_by_asset.get(adir.name, [])
    out.append(
        {
            "asset": adir.name,
            "provider": adir.parent.name,
            "collision_kind": "coacd"
            if (c.is_watertight and not same_as_visual)
            else ("visual_copy" if same_as_visual else "other"),
            "watertight": bool(c.is_watertight),
            "components": comps,
            "faces": int(len(c.faces)),
            "late_rot_max": round(max(lates), 4) if lates else None,
            "runs": len(lates),
        }
    )

ROWS.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n")

ok = [r for r in out if "error" not in r]
by_kind = {}
for r in ok:
    by_kind.setdefault(r["collision_kind"], []).append(r)
print("资产数: %d" % len(ok))
for k, rs in sorted(by_kind.items()):
    wt = sum(1 for r in rs if r["watertight"])
    print("  %-12s %3d 个 (水密 %d)" % (k, len(rs), wt))

print("\n非水密 collision 且有实测数据的资产（按末段旋转降序）:")
cand = [r for r in ok if not r["watertight"] and r["late_rot_max"] is not None]
cand.sort(key=lambda r: -r["late_rot_max"])
for r in cand[:15]:
    print(
        "  %-16s %-11s comps=%-4d faces=%-6d late_rot_max=%.4f (%d 次跑)"
        % (
            r["asset"],
            r["collision_kind"],
            r["components"],
            r["faces"],
            r["late_rot_max"],
            r["runs"],
        )
    )

print("\n水密 collision 且有实测数据的对照组:")
ctrl = [r for r in ok if r["watertight"] and r["late_rot_max"] is not None]
ctrl.sort(key=lambda r: -r["late_rot_max"])
for r in ctrl[:8]:
    print(
        "  %-16s %-11s comps=%-4d late_rot_max=%.4f (%d 次跑)"
        % (
            r["asset"],
            r["collision_kind"],
            r["components"],
            r["late_rot_max"],
            r["runs"],
        )
    )
print("\n明细: %s" % ROWS)
