"""Convert one single-mesh RoboLab USD asset into RoboTwin rigid-asset files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate a textured single-mesh RoboLab USD asset to RoboTwin."
    )
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--usd", type=Path, required=True)
    parser.add_argument("--texture", type=Path, required=True)
    parser.add_argument("--mesh-prim", required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--asset-name", required=True)
    parser.add_argument("--license", type=Path, required=True)
    parser.add_argument("--license-label", required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument(
        "--source-url",
        default="https://github.com/NVLabs/RoboLab",
    )
    parser.add_argument("--mass-kg", type=float)
    parser.add_argument("--static-friction", type=float)
    parser.add_argument("--dynamic-friction", type=float)
    parser.add_argument("--restitution", type=float)
    parser.add_argument(
        "--collision-limitation",
        default="A single convex hull is used for collision approximation.",
    )
    parser.add_argument(
        "--intended-use",
        default="Academic RoboTwin evaluation.",
    )
    return parser


def source_file(source_repo: Path, relative_path: Path) -> Path:
    source_repo = source_repo.resolve()
    path = (source_repo / relative_path).resolve()
    if not path.is_relative_to(source_repo):
        raise ValueError(f"Source path escapes repository: {relative_path}")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def validate_args(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.asset_id):
        raise ValueError("asset-id may only contain letters, digits, dot, dash, underscore")

    physics = (
        args.static_friction,
        args.dynamic_friction,
        args.restitution,
    )
    if any(value is not None for value in physics) and not all(
        value is not None for value in physics
    ):
        raise ValueError(
            "static-friction, dynamic-friction, and restitution "
            "must be provided together"
        )

    if args.mass_kg is not None and args.mass_kg <= 0:
        raise ValueError("mass-kg must be greater than zero")
    if args.static_friction is not None and args.static_friction < 0:
        raise ValueError("static-friction must be non-negative")
    if args.dynamic_friction is not None and args.dynamic_friction < 0:
        raise ValueError("dynamic-friction must be non-negative")
    if args.restitution is not None and not 0 <= args.restitution <= 1:
        raise ValueError("restitution must be between zero and one")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(args: argparse.Namespace) -> Path:
    validate_args(args)

    import numpy as np
    import trimesh
    from PIL import Image
    from pxr import Usd, UsdGeom

    source_repo = args.source_repo.resolve()
    usd_path = source_file(source_repo, args.usd)
    texture_path = source_file(source_repo, args.texture)
    license_path = source_file(source_repo, args.license)

    out_dir = (args.out_root.resolve() / args.asset_id)
    visual_dir = out_dir / "visual"
    collision_dir = out_dir / "collision"
    visual_dir.mkdir(parents=True, exist_ok=True)
    collision_dir.mkdir(parents=True, exist_ok=True)

    stage = Usd.Stage.Open(str(usd_path))
    if not stage:
        raise RuntimeError(f"Cannot open USD: {usd_path}")

    mesh = UsdGeom.Mesh.Get(stage, args.mesh_prim)
    if not mesh:
        raise RuntimeError(f"USD mesh was not found: {args.mesh_prim}")

    transform = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim())
    vertices = np.asarray(
        [transform.Transform(point) for point in mesh.GetPointsAttr().Get()],
        dtype=np.float64,
    )
    face_counts = np.asarray(
        mesh.GetFaceVertexCountsAttr().Get(),
        dtype=np.int64,
    )
    if not np.all(face_counts == 3):
        raise RuntimeError("This converter currently expects triangular faces")

    faces = np.asarray(
        mesh.GetFaceVertexIndicesAttr().Get(),
        dtype=np.int64,
    ).reshape((-1, 3))

    raw_normals = mesh.GetNormalsAttr().Get()
    if raw_normals is None or len(raw_normals) != len(vertices):
        raise RuntimeError("This converter requires one normal per vertex")
    normals = np.asarray(
        [transform.TransformDir(normal) for normal in raw_normals],
        dtype=np.float64,
    )
    normal_lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.maximum(normal_lengths, 1e-12)

    uv_primvar = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st")
    uv_values = uv_primvar.ComputeFlattened()
    if uv_values is None or len(uv_values) != len(vertices):
        raise RuntimeError("This converter requires one UV coordinate per vertex")
    uv = np.asarray(uv_values, dtype=np.float64)
    uv[:, 1] = 1.0 - uv[:, 1]

    # RoboTwin origin_on_table expects the mesh bottom at z=0.
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
        "transform_matrix": np.eye(4).tolist(),
        "target_pose": [],
        "contact_points_pose": [],
    }
    if args.mass_kg is not None:
        model_data["mass_kg"] = args.mass_kg
    if args.static_friction is not None:
        model_data.update(
            {
                "static_friction": args.static_friction,
                "dynamic_friction": args.dynamic_friction,
                "restitution": args.restitution,
            }
        )

    metadata_path = out_dir / "model_data0.json"
    metadata_path.write_text(
        json.dumps(model_data, indent=2),
        encoding="utf-8",
    )
    shutil.copy2(license_path, out_dir / "LICENSE")

    commit = subprocess.check_output(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()

    physics_text = "not provided"
    if args.mass_kg is not None:
        physics_text = f"mass {args.mass_kg} kg"
    if args.static_friction is not None:
        physics_text += (
            f", static friction {args.static_friction}, "
            f"dynamic friction {args.dynamic_friction}, "
            f"restitution {args.restitution}"
        )

    source_text = f"""# Asset provenance

- Asset: {args.asset_name}
- Source repository: {args.source_url}
- Source commit: {commit}
- Source file: {args.usd.as_posix()}
- Source texture: {args.texture.as_posix()}
- License: {args.license_label}
- Source physics: {physics_text}.
- Changes: USD world-space mesh extracted, bottom aligned to z=0,
  texture embedded into GLB, and convex-hull collision geometry generated.
- Collision limitation: {args.collision_limitation}
- Intended use: {args.intended_use}
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
    print("visual/base0.glb sha256:", sha256(visual_path))
    print("collision/base0.glb sha256:", sha256(collision_path))
    print("model_data0.json sha256:", sha256(metadata_path))
    return out_dir


def main() -> int:
    args = build_parser().parse_args()
    migrate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
