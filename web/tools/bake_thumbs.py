#!/usr/bin/env python3
"""为 asset_catalog 中可加载的资产离屏烘焙缩略图（web 层工具）。

- 只读 catalog 与资产 mesh；只写 results/web_thumbs/<asset_id>.png（幂等，已存在跳过）。
- rigid 且 model0 为 trimesh 可读格式（glb/gltf/obj/stl/ply）才烘；urdf/unsupported 跳过。
- 单个资产失败不中断，末尾汇总 baked/skipped/failed。

用法：
  /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python web/tools/bake_thumbs.py \
      [--catalog data/scene_gen_ext/asset_catalog.json] [--out results/web_thumbs] \
      [--size 384] [--force]
"""

import argparse
import json
from pathlib import Path

DEV = Path("/home/jingxiang/yuxin/env-gen-dev")
LOADABLE = {".glb", ".gltf", ".obj", ".stl", ".ply"}


class Baker:
    """单 Scene 复用（每资产新建 Scene 会累积 vulkan 资源导致段错误）。"""

    def __init__(self, size_px):
        import numpy as np
        import sapien

        self.np = np
        self.sapien = sapien
        self.size_px = size_px
        self.scene = sapien.Scene()
        self.scene.set_ambient_light([0.45, 0.45, 0.45])
        self.scene.add_directional_light([0.4, -0.6, -1], [2.2, 2.2, 2.2], shadow=False)
        self.scene.add_directional_light(
            [-0.6, 0.4, -0.4], [0.8, 0.8, 0.8], shadow=False
        )
        self.cam = self.scene.add_camera(
            "c", size_px, size_px, float(np.deg2rad(42)), 0.005, 100.0
        )

    def bake(self, glb, out_path):
        import trimesh
        from PIL import Image
        from scipy.spatial.transform import Rotation

        np = self.np
        tm = trimesh.load(str(glb), force="scene")
        lo, hi = tm.bounds
        center = (lo + hi) / 2
        dim = float(max(hi - lo))
        if not (dim > 0):
            raise ValueError("degenerate bounds")

        b = self.scene.create_actor_builder()
        b.add_visual_from_file(str(glb))
        entity = b.build_static()
        try:
            eye = center + np.array([1.0, 0.85, 0.65]) * dim * 1.15
            fwd = center - eye
            fwd /= np.linalg.norm(fwd)
            up0 = np.array([0.0, 0.0, 1.0])
            left = np.cross(up0, fwd)
            left /= np.linalg.norm(left)
            up = np.cross(fwd, left)
            q = Rotation.from_matrix(np.column_stack([fwd, left, up])).as_quat()
            self.cam.set_entity_pose(
                self.sapien.Pose(eye.tolist(), [q[3], q[0], q[1], q[2]])
            )
            self.scene.update_render()
            self.cam.take_picture()
            rgba = (self.cam.get_picture("Color") * 255).clip(0, 255).astype("uint8")
            Image.fromarray(rgba).save(out_path)
        finally:
            entity.remove_from_scene()


def pick_mesh(entry):
    models = entry.get("models") or []
    if not models:
        return None
    vp = models[0].get("visual_path") or ""
    p = Path(vp)
    if p.suffix.lower() in LOADABLE and p.is_file():
        return p
    # visual_path 指向目录时找其中的 glb
    if p.is_dir():
        for cand in sorted(p.glob("visual/*.glb")) + sorted(p.glob("*.glb")):
            return cand
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--catalog", default=str(DEV / "data/scene_gen_ext/asset_catalog.json")
    )
    ap.add_argument("--out", default=str(DEV / "results/web_thumbs"))
    ap.add_argument("--size", type=int, default=384)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    entries = json.load(open(a.catalog))["entries"]
    baker = None
    baked = skipped = failed = 0
    for e in entries:
        aid = e.get("asset_id")
        dst = out / f"{aid}.png"
        if dst.exists() and not a.force:
            skipped += 1
            continue
        mesh = pick_mesh(e)
        if e.get("load_type") != "rigid" or mesh is None:
            skipped += 1
            continue
        if baker is None:
            baker = Baker(a.size)
        try:
            baker.bake(mesh, dst)
            baked += 1
            print(f"baked {aid}", flush=True)
        except Exception as exc:
            failed += 1
            dst.unlink(missing_ok=True)
            print(f"FAILED {aid}: {type(exc).__name__}: {exc}", flush=True)
    print(f"\ndone: baked={baked} skipped={skipped} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
