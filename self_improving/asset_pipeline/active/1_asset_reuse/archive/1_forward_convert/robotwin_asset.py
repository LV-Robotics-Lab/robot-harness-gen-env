#!/usr/bin/env python3
"""Read RoboTwin private asset layout into AssetBundle-shaped JSON (sapien-side reps only).

Smoke scope: 001_bottle (rigid, model 0) + 036_cabinet/46653 (articulated).
Isaac-side representations are appended later by s1/s2 converters.
"""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

RT = Path("/home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin")
RT_COMMIT = "c3ddfa8b97d5519efa828b075999bd0006778e5e"

UNKNOWN_MASS = {
    "value": None,
    "status": "unknown",
    "runtime_default_kg": 0.1,
    "note": "RoboTwin model_data.json carries no mass/inertia; default documented here",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rep(
    fmt: str, uri: Path, backend: str, role: str, metadata: dict | None = None
) -> dict:
    return {
        "format": fmt,
        "uri": str(uri),
        "backend": backend,
        "role": role,
        "sha256": sha256(uri),
        "size_bytes": uri.stat().st_size,
        "metadata": metadata or {},
    }


def glb_bbox(path: Path) -> list[float]:
    """Raw mesh bbox via trimesh — the authoritative geometry size (matches
    what SAPIEN loads; model_data 'extents' is a separate annotation that
    does NOT always equal the mesh bbox, e.g. 001_bottle)."""
    import trimesh

    scene = trimesh.load(str(path))
    lo, hi = scene.bounds
    return [float(b - a) for a, b in zip(lo, hi)]


def bottle_bundle() -> dict:
    d = RT / "assets/objects/001_bottle"
    md = json.load(open(d / "model_data0.json"))
    bbox_raw = glb_bbox(d / "visual/base0.glb")
    return {
        "asset_id": "robotwin_001_bottle_m0",
        "category": "bottle",
        "representations": [
            rep("glb", d / "visual/base0.glb", "sapien", "visual"),
            rep("glb", d / "collision/base0.glb", "sapien", "collision"),
        ],
        "source": {
            "library": "RoboTwin",
            "commit": RT_COMMIT,
            "path": "assets/objects/001_bottle",
            "model_id": 0,
            "license": "unknown (RoboTwin repo license applies; per-object origin unpublished)",
        },
        "physical": {
            "mass_kg": UNKNOWN_MASS,
            "scale": md["scale"],
            "extents_raw": md["extents"],
            "extents_m": [e * s for e, s in zip(md["extents"], md["scale"])],
            "mesh_bbox_raw": bbox_raw,
            "mesh_bbox_m": [b * s for b, s in zip(bbox_raw, md["scale"])],
            "mesh_up_axis": "Y",
            "note": "mesh_bbox_m (glb bbox x scale) is authoritative for size; "
            "model_data extents disagrees with mesh bbox on some assets",
        },
        "articulation": {},
        "tags": ["rigid", "smoke"],
    }


def cabinet_bundle() -> dict:
    d = RT / "assets/objects/036_cabinet/46653"
    urdf = d / "mobility.urdf"
    root = ET.parse(urdf).getroot()
    joints = [
        {"name": j.get("name"), "type": j.get("type")} for j in root.findall("joint")
    ]
    movable = [j for j in joints if j["type"] != "fixed"]
    limits = {}
    for j in root.findall("joint"):
        lim = j.find("limit")
        if lim is not None:
            limits[j.get("name")] = {
                "lower": lim.get("lower"),
                "upper": lim.get("upper"),
            }
    n_meshes = len(list(d.rglob("*.obj")))
    return {
        "asset_id": "robotwin_036_cabinet_46653",
        "category": "cabinet",
        "representations": [
            rep(
                "urdf",
                urdf,
                "sapien",
                "visual_and_collision",
                {
                    "note": f"urdf references {n_meshes} local .obj meshes (hash covers urdf only)"
                },
            ),
        ],
        "source": {
            "library": "RoboTwin",
            "commit": RT_COMMIT,
            "path": "assets/objects/036_cabinet/46653",
            "upstream_dataset": "PartNet-Mobility id 46653",
            "license": "unknown (PartNet-Mobility academic terms apply)",
        },
        "physical": {
            "mass_kg": UNKNOWN_MASS,
            "scale_assumed": [1.0, 1.0, 1.0],
            "note": "PartNet-Mobility units are normalized; metric sizing policy TBD (structured unknown)",
        },
        "articulation": {
            "joint_count_total": len(joints),
            "joint_count_movable": len(movable),
            "joints": joints,
            "limits": limits,
        },
        "tags": ["articulated", "smoke"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, bundle in (
        ("bottle_bundle.json", bottle_bundle()),
        ("cabinet_bundle.json", cabinet_bundle()),
    ):
        path = out / name
        path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
        print(
            f"PASS {name} asset_id={bundle['asset_id']} reps={len(bundle['representations'])}"
        )


if __name__ == "__main__":
    main()
