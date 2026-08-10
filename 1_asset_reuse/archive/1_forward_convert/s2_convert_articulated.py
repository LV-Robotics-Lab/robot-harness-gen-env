#!/usr/bin/env python3
"""036_cabinet: mobility.urdf -> USD articulation via Isaac URDF importer.

Runs in isaac-smoke env. Post-check compares USD joint prims against the
URDF-declared movable joints recorded in the bundle. Appends an isaacsim
representation to the bundle JSON.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--bundle", required=True)
parser.add_argument("--out-dir", required=True)
args = parser.parse_args()

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})

try:
    from isaacsim.core.utils.extensions import enable_extension

    ext_used = None
    for ext in ("isaacsim.asset.importer.urdf", "omni.importer.urdf"):
        try:
            enable_extension(ext)
            ext_used = ext
            break
        except Exception:
            continue
    if ext_used is None:
        raise RuntimeError("no URDF importer extension available")
    app.update()
    print(f"urdf importer extension: {ext_used}")

    import omni.kit.commands
    import omni.usd
    from pxr import Usd, UsdPhysics

    bundle_path = Path(args.bundle)
    bundle = json.loads(bundle_path.read_text())
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    urdf_rep = bundle["representations"][0]
    urdf_src = Path(urdf_rep["uri"])
    expected_movable = int(bundle["articulation"]["joint_count_movable"])
    dest = out / "cabinet.usd"

    # PartNet-Mobility URDFs carry duplicate visual/collision names (breaks the
    # importer with "Used null prim"). Normalize into a derived copy in the test
    # folder — upstream file untouched; mesh paths rewritten to absolute.
    import xml.etree.ElementTree as ET

    urdf_path = out / "cabinet_normalized.urdf"
    tree = ET.parse(urdf_src)
    xroot = tree.getroot()
    counter = 0
    for link in xroot.findall("link"):
        for tag in ("visual", "collision"):
            for el in link.findall(tag):
                base = (el.get("name") or tag).replace("-", "_")
                el.set("name", f"{base}_u{counter}")
                counter += 1
    for mesh in xroot.iter("mesh"):
        fn = mesh.get("filename")
        if fn and not fn.startswith("/"):
            mesh.set("filename", str((urdf_src.parent / fn).resolve()))
    tree.write(urdf_path)
    print(f"normalized urdf -> {urdf_path.name} ({counter} geom names uniquified)")

    status, import_config = omni.kit.commands.execute("URDFCreateImportConfig")
    if not status:
        raise RuntimeError("URDFCreateImportConfig failed")
    import_config.merge_fixed_joints = False
    import_config.fix_base = True
    import_config.convex_decomp = True
    import_config.self_collision = False
    import_config.distance_scale = 1.0

    kwargs = dict(urdf_path=str(urdf_path), import_config=import_config)
    try:
        status, prim_path = omni.kit.commands.execute(
            "URDFParseAndImportFile", dest_path=str(dest), **kwargs
        )
        if not status:
            raise RuntimeError("URDFParseAndImportFile returned falsy status")
        print(f"imported to {prim_path}, dest={dest.name}")
    except Exception as exc:  # noqa: BLE001  (5.1 dest_path mode hits "Used null prim")
        print(f"warn: dest_path import failed ({exc}); using in-stage import + export")
        omni.usd.get_context().new_stage()
        app.update()
        status, prim_path = omni.kit.commands.execute(
            "URDFParseAndImportFile", **kwargs
        )
        if not status:
            raise RuntimeError("URDFParseAndImportFile failed in both modes")
        cur = omni.usd.get_context().get_stage()
        if prim_path:
            root_prim = cur.GetPrimAtPath(prim_path)
            if root_prim:
                cur.SetDefaultPrim(root_prim)
        cur.GetRootLayer().Export(str(dest))
        print(f"imported to current stage ({prim_path}), exported -> {dest.name}")

    if not dest.exists():
        raise RuntimeError(f"expected output {dest} missing")

    fixstage = Usd.Stage.Open(str(dest))
    if not fixstage.GetDefaultPrim():
        roots = [c for c in fixstage.GetPseudoRoot().GetChildren()]
        if roots:
            fixstage.SetDefaultPrim(roots[0])
            fixstage.GetRootLayer().Save()
            print(f"defaultPrim set -> {roots[0].GetPath()}")

    check = Usd.Stage.Open(str(dest))
    revolute = prismatic = with_limits = 0
    for prim in check.Traverse():
        if prim.IsA(UsdPhysics.RevoluteJoint):
            revolute += 1
            j = UsdPhysics.RevoluteJoint(prim)
            if (
                j.GetLowerLimitAttr().HasAuthoredValue()
                or j.GetUpperLimitAttr().HasAuthoredValue()
            ):
                with_limits += 1
        elif prim.IsA(UsdPhysics.PrismaticJoint):
            prismatic += 1
            j = UsdPhysics.PrismaticJoint(prim)
            if (
                j.GetLowerLimitAttr().HasAuthoredValue()
                or j.GetUpperLimitAttr().HasAuthoredValue()
            ):
                with_limits += 1
    movable = revolute + prismatic
    print(
        f"usd joints: revolute={revolute} prismatic={prismatic} with_limits={with_limits} "
        f"expected_movable={expected_movable}"
    )
    if movable != expected_movable:
        raise RuntimeError(
            f"joint count mismatch: usd={movable} urdf={expected_movable}"
        )

    def sha256(p):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    bundle["representations"].append(
        {
            "format": "usd",
            "uri": str(dest),
            "backend": "isaacsim",
            "role": "visual_and_collision",
            "sha256": sha256(dest),
            "size_bytes": dest.stat().st_size,
            "metadata": {
                "converter": f"urdf importer ({ext_used})",
                "fix_base": True,
                "convex_decomp": True,
                "distance_scale": 1.0,
                "joints_usd": {
                    "revolute": revolute,
                    "prismatic": prismatic,
                    "with_limits": with_limits,
                },
                "source_sha256": {
                    "urdf": urdf_rep["sha256"],
                    "normalized_urdf": sha256(urdf_path),
                },
                "normalization": "visual/collision names uniquified; mesh paths absolutized",
            },
        }
    )
    bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
    print("PASS s2 cabinet.usd")
except Exception as exc:  # noqa: BLE001
    print(f"FAIL s2: {type(exc).__name__}: {exc}", file=sys.stderr)
    app.close()
    sys.exit(1)
app.close()
