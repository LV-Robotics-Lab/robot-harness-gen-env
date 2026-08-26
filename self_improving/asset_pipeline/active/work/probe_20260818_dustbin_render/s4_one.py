#!/usr/bin/env python3
"""probe s4 (per-asset worker): one asset in, one JSONL row out.

Runs as its own short-lived process on purpose -- doing all 66 assets in one
interpreter OOM-killed the box (exit 137) because a 553-component scan's
split() plus trimesh's caches never come back down. Cheap fix, and it also
means one bad mesh cannot take the whole census with it.
"""

import json
import pathlib
import sys

import trimesh

adir = pathlib.Path(sys.argv[1])
row = {"asset": adir.name, "provider": adir.parent.name}
try:
    col = sorted((adir / "collision").glob("base*.glb"))
    vis = sorted((adir / "visual").glob("base*.glb"))
    if not col:
        row["error"] = "no collision mesh"
    else:
        c = trimesh.load(str(col[0]), force="mesh", process=False)
        row["watertight"] = bool(c.is_watertight)
        row["faces"] = int(len(c.faces))
        row["vertices"] = int(len(c.vertices))
        row["volume_m3"] = round(float(c.volume), 8) if c.is_volume else None
        row["bbox_m"] = [round(float(x), 4) for x in c.extents]
        if vis:
            v = trimesh.load(str(vis[0]), force="mesh", process=False)
            row["same_as_visual"] = bool(
                len(c.faces) == len(v.faces) and len(c.vertices) == len(v.vertices)
            )
        else:
            row["same_as_visual"] = None
        row["collision_kind"] = (
            "visual_copy"
            if row.get("same_as_visual")
            else ("coacd" if row["watertight"] else "other")
        )
except Exception as exc:  # noqa: BLE001
    row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:80])
print(json.dumps(row, ensure_ascii=False))
