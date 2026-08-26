#!/usr/bin/env python3
"""probe s1: harvest every runtime_evidence.json on disk and measure what the
`still_moving` gate actually does.

Gate under test (script/run_scene_runtime.py:687):
    still_moving = late_translation > 0.001 or late_rotation > 0.5

Question: how many objects does it call "still moving" while they are in fact
converging to rest? Convergence signal available without re-running anything:
    ratio = late_window_rotation_deg / rotation_drift_deg
A body still tumbling keeps rotating, so its late window holds a large share of
the total; a body that rocked and stopped has a small share.

Output: out/settle_rows.jsonl (one row per object, re-checkable by hand) plus a
distribution readout. NOT a verdict -- the verdict needs the longer-settle
rerun in s2.
"""

import json
import pathlib
import sys

ROOTS = [pathlib.Path(p) for p in sys.argv[1:-1]]
OUT = pathlib.Path(sys.argv[-1])
OUT.parent.mkdir(parents=True, exist_ok=True)

T_TRANS, T_ROT = 0.001, 0.5

rows = []
for root in ROOTS:
    if not root.exists():
        continue
    for f in root.rglob("runtime_evidence.json"):
        try:
            ev = json.loads(f.read_text())
        except Exception:
            continue
        for obj_id, o in (ev.get("objects") or {}).items():
            lt = o.get("late_window_translation_m")
            lr = o.get("late_window_rotation_deg")
            if lt is None or lr is None:
                continue
            rows.append(
                {
                    "file": str(f),
                    "scene": ev.get("scene_id"),
                    "object": obj_id,
                    "asset_id": o.get("asset_id"),
                    "late_trans": lt,
                    "late_rot": lr,
                    "total_trans": o.get("translation_drift_m"),
                    "total_rot": o.get("rotation_drift_deg"),
                    "still_moving": o.get("still_moving"),
                    "penetration": o.get("penetration_count"),
                    "support_contact": o.get("support_contact"),
                }
            )

with OUT.open("w") as fh:
    for r in rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")

n = len(rows)
flagged = [r for r in rows if r["late_trans"] > T_TRANS or r["late_rot"] > T_ROT]
rot_only = [r for r in flagged if r["late_trans"] <= T_TRANS]
trans_only = [r for r in flagged if r["late_rot"] <= T_ROT]
both = [r for r in flagged if r["late_trans"] > T_TRANS and r["late_rot"] > T_ROT]

print("样本: %d 个物体实例, 来自 %d 份 evidence" % (n, len({r["file"] for r in rows})))
print(
    "被 still_moving 判定为「仍在动」: %d (%.1f%%)"
    % (len(flagged), 100 * len(flagged) / max(n, 1))
)
print("  仅因旋转超阈值: %d" % len(rot_only))
print("  仅因位移超阈值: %d" % len(trans_only))
print("  两者都超:       %d" % len(both))


def pct(vals, q):
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


rots = [r["late_rot"] for r in rows]
print("\nlate_window_rotation_deg 分布 (全体 n=%d):" % n)
for q in (0.5, 0.75, 0.9, 0.95, 0.99):
    print("  p%-4s %.4f" % (int(q * 100), pct(rots, q)))
print("  max   %.4f" % max(rots or [0]))

print("\n仅因旋转被判定的 %d 例 —— 收敛比 late_rot/total_rot:" % len(rot_only))
conv = []
for r in rot_only:
    tot = r["total_rot"] or 0
    ratio = (r["late_rot"] / tot) if tot > 1e-9 else float("inf")
    conv.append((ratio, r))
conv.sort(key=lambda x: x[0])
for ratio, r in conv[:12]:
    print(
        "  %-16s late_rot=%.3f total_rot=%.3f  比=%.1f%%  late_trans=%.5f"
        % (r["asset_id"], r["late_rot"], r["total_rot"], 100 * ratio, r["late_trans"])
    )
print("\n明细: %s" % OUT)
