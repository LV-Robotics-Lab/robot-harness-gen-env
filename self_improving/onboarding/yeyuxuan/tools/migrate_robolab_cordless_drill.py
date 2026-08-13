from pathlib import Path
import hashlib
import json
import shutil
import subprocess

import numpy as np
import trimesh
from PIL import Image
from pxr import Usd, UsdGeom

source_repo = Path("/home/jingxiang/yeyuxuan/robolab-source")
project_root = Path("/home/jingxiang/yeyuxuan/robot-harness-gen-env")

usd_path = source_repo / "assets/objects/ycb/cordless_drill.usd"
texture_path = source_repo / "assets/objects/ycb/textures/obj_000015.png"
license_path = source_repo / "assets/objects/ycb/LICENSE"

out_dir = (
    project_root
    / "onboarding-report/artifacts/robolab_migration"
    / "904_robolab_cordless_drill"
)
visual_dir = out_dir / "visual"
collision_dir = out_dir / "collision"
visual_dir.mkdir(parents=True, exist_ok=True)
collision_dir.mkdir(parents=True, exist_ok=True)

stage = Usd.Stage.Open(str(usd_path))
if not stage:
    raise RuntimeError(f"Cannot open USD: {usd_path}")

mesh = UsdGeom.Mesh.Get(stage, "/cordless_drill/obj_000015_Mesh")
if not mesh:
    raise RuntimeError("Cordless-drill mesh was not found")

transform = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim())
vertices = np.asarray(
    [transform.Transform(point) for point in mesh.GetPointsAttr().Get()],
    dtype=np.float64,
)
face_counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), dtype=np.int64)

if not np.all(face_counts == 3):
    raise RuntimeError("This converter currently expects triangular faces")

faces = np.asarray(
    mesh.GetFaceVertexIndicesAttr().Get(),
    dtype=np.int64,
).reshape((-1, 3))

normals = np.asarray(
    [transform.TransformDir(normal) for normal in mesh.GetNormalsAttr().Get()],
    dtype=np.float64,
)
normal_lengths = np.linalg.norm(normals, axis=1, keepdims=True)
normals = normals / np.maximum(normal_lengths, 1e-12)
uv_primvar = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st")
uv = np.asarray(uv_primvar.ComputeFlattened(), dtype=np.float64)

# USD texture coordinates use the opposite V direction from the GLB output.
uv[:, 1] = 1.0 - uv[:, 1]

# Move the bottom of the object to z=0 for RoboTwin origin_on_table.
vertices[:, 2] -= vertices[:, 2].min()

image = Image.open(texture_path).convert("RGB")
material = trimesh.visual.material.PBRMaterial(
    baseColorTexture=image,
    metallicFactor=0.05,
    roughnessFactor=0.55,
)
visual = trimesh.visual.texture.TextureVisuals(
    uv=uv,
    material=material,
)

visual_mesh = trimesh.Trimesh(
    vertices=vertices,
    faces=faces,
    vertex_normals=normals,
    visual=visual,
    process=False,
    maintain_order=True,
)
visual_path = visual_dir / "base0.glb"
visual_mesh.export(visual_path)

# A convex hull is sufficient and more stable for a rigid can collision model.
collision_source = trimesh.Trimesh(
    vertices=vertices,
    faces=faces,
    process=True,
)
collision_mesh = collision_source.convex_hull
collision_path = collision_dir / "base0.glb"
collision_mesh.export(collision_path)

bounds = visual_mesh.bounds
center = bounds.mean(axis=0)
extents = bounds[1] - bounds[0]

model_data = {
    "center": center.tolist(),
    "extents": extents.tolist(),
    "scale": [1.0, 1.0, 1.0],
    "mass_kg": 1.5,
    "dynamic_friction": 2.0,
    "static_friction": 2.0,
    "restitution": 0.1,
    "transform_matrix": np.eye(4).tolist(),
    "target_pose": [],
    "contact_points_pose": [],
}
(out_dir / "model_data0.json").write_text(
    json.dumps(model_data, indent=2),
    encoding="utf-8",
)

shutil.copy2(license_path, out_dir / "LICENSE")

commit = subprocess.check_output(
    ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
    text=True,
).strip()

source_text = f"""# Asset provenance

- Asset: YCB cordless drill
- Source repository: https://github.com/NVLabs/RoboLab
- Source commit: {commit}
- Source file: assets/objects/ycb/cordless_drill.usd
- Source texture: assets/objects/ycb/textures/obj_000015.png
- License: MIT
- Source physics: mass 1.5 kg, dynamic friction 2.0,
  static friction 2.0, restitution 0.1.
- Changes: USD mesh extracted, bottom aligned to z=0, texture embedded
  into GLB, and convex-hull collision geometry generated.
- Intended use: non-commercial academic RoboTwin evaluation.
"""
(out_dir / "SOURCE.md").write_text(source_text, encoding="utf-8")

print("PASS migration")
print("output:", out_dir)
print("visual vertices:", len(visual_mesh.vertices))
print("visual faces:", len(visual_mesh.faces))
print("collision vertices:", len(collision_mesh.vertices))
print("collision faces:", len(collision_mesh.faces))
print("center:", center.tolist())
print("extents:", extents.tolist())

for path in [visual_path, collision_path, out_dir / "model_data0.json"]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(path.name, "sha256:", digest)
