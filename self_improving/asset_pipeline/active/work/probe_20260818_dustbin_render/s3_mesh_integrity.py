#!/usr/bin/env python3
"""probe s3: is the "incomplete render" a missing-geometry problem or a
viewing/lighting one?

Decides it from the mesh itself rather than from a 320x240 top-down frame:
per-asset vertex/face counts, connected-component split, watertightness and
bbox for visual vs collision, plus the source mirror the asset was built from.
A visual mesh that lost components during materialization shows up as fewer
components / smaller bbox than its source; one that matches its source is
complete and the problem is elsewhere.
"""

import json
import pathlib
import sys

import trimesh

LIB = pathlib.Path(sys.argv[1])
ASSETS = sys.argv[2:]


def describe(path):
    try:
        loaded = trimesh.load(str(path), force="scene")
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    geoms = list(loaded.geometry.values()) if hasattr(loaded, "geometry") else [loaded]
    merged = trimesh.util.concatenate(geoms) if geoms else None
    if merged is None:
        return {"error": "empty"}
    try:
        bodies = merged.split(only_watertight=False)
    except Exception:
        bodies = [merged]
    return {
        "submeshes": len(geoms),
        "vertices": int(len(merged.vertices)),
        "faces": int(len(merged.faces)),
        "components": len(bodies),
        "watertight": bool(merged.is_watertight),
        "bbox_m": [round(float(x), 4) for x in merged.extents],
        "volume_m3": round(float(merged.volume), 6) if merged.is_volume else None,
        "component_face_share": [
            round(len(b.faces) / max(len(merged.faces), 1), 3) for b in bodies[:6]
        ],
    }


for asset in ASSETS:
    hit = next(iter(LIB.glob("*/" + asset)), None)
    if hit is None:
        print("!! %s 未找到" % asset)
        continue
    print("=== %s  (%s)" % (asset, hit.parent.name))
    for role in ("visual", "collision"):
        for f in sorted((hit / role).glob("base*.glb")):
            print(
                "  %-9s %-12s %s"
                % (role, f.name, json.dumps(describe(f), ensure_ascii=False))
            )
    led = hit / "ledger.json"
    if led.is_file():
        d = json.loads(led.read_text())
        m = d["models"][0]
        src = m.get("source", {})
        print(
            "  source     %s / %s / %s"
            % (src.get("library"), src.get("group"), src.get("file"))
        )
        for r in m.get("representations", []):
            if r.get("role") == "visual_and_collision":
                p = pathlib.Path(r["uri"])
                full = p if p.is_absolute() else LIB.parents[1] / p
                print("  原始源件   %s" % full)
                if full.exists():
                    print(
                        "  %-9s %-12s %s"
                        % (
                            "SOURCE",
                            full.name,
                            json.dumps(describe(full), ensure_ascii=False),
                        )
                    )
                else:
                    print("             (不存在)")
    print()
