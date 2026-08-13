#!/usr/bin/env python3
"""Measure每个可用模型的外观属性（颜色为主），产出 asset_attributes.json。

Why: 上游 grounding 早就支持按颜色/材质检索（parser 中英抽取 -> entry.colors
/entry.materials 匹配 -> 运行时按色着色），但我们从没给资产填过 colors，于是
"红色的杯子" 走到 "color metadata unknown" 分支，任何颜色的杯子都被放行——
识别不出、也复用不准。

判据（上游语义决定了必须保守）：entry.colors 一旦非空，颜色不匹配的资产会被
grounding 直接拒收。错标一个颜色 = 永久检索不到该资产。所以这里只发布"看得
明白"的颜色：占比不足门槛、或整体偏灰无彩，一律留空（= unknown = 放行），
宁可少标不可错标。

方法：每个可用刚体模型在中性全向光下从 4 个方位离屏渲染，用 Position 通道的
深度掩膜取出物体像素，转 HSV 逐像素归类到上游 10 个规范色（含棕色=暗橙/暗红、
无彩按明度归 black/white），跨视角汇总占比。
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np

DEV = Path("/home/jingxiang/yuxin/env-gen-dev")

# 上游 scene_gen/colors.py 的规范色（只能产出这 10 个名字）
CANON = (
    "black",
    "blue",
    "brown",
    "green",
    "orange",
    "pink",
    "purple",
    "red",
    "white",
    "yellow",
)
DOMINANT_MIN = 0.30  # 低于此占比不发布（宁可 unknown）
SECOND_MIN = 0.25  # 第二颜色的门槛
GRAY_MAX = 0.55  # 无彩像素占比超过它则认为"整体偏灰"，不发布彩色


def classify(h, s, v):
    """单像素 -> 规范色名 或 None（灰/不确定）。h in [0,360)."""
    if v < 0.12:
        return "black"
    if s < 0.18:
        if v > 0.72:
            return "white"
        if v < 0.28:
            return "black"
        return None  # 中灰：上游没有 gray，留空好过错标
    # 有彩：棕色是"暗的橙/红"，必须在色相判断之前拦下
    if (h < 45 or h >= 330) and v < 0.45:
        return "brown"
    if h < 15 or h >= 345:
        return "red"
    if h < 45:
        return "orange"
    if h < 70:
        return "yellow"
    if h < 165:
        return "green"
    if h < 255:
        return "blue"
    if h < 290:
        return "purple"
    if h < 345:
        return "pink"
    return "red"


def look_at(eye, target, up=(0.0, 0.0, 1.0)):
    """SAPIEN 相机的本地前向是 +X、左为 +Y、上为 +Z。"""
    import sapien

    fwd = np.asarray(target, float) - np.asarray(eye, float)
    fwd = fwd / max(np.linalg.norm(fwd), 1e-9)
    up = np.asarray(up, float)
    left = np.cross(up, fwd)
    n = np.linalg.norm(left)
    if n < 1e-6:  # 正上/正下俯视时换一个参考轴
        up = np.array([0.0, 1.0, 0.0])
        left = np.cross(up, fwd)
        n = np.linalg.norm(left)
    left = left / n
    up2 = np.cross(fwd, left)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = fwd, left, up2
    m[:3, 3] = eye
    return sapien.Pose(m)


def rgb_to_hsv(arr):
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = np.max(arr[..., :3], axis=-1)
    mn = np.min(arr[..., :3], axis=-1)
    d = mx - mn
    h = np.zeros_like(mx)
    mask = d > 1e-6
    idx = mask & (mx == r)
    h[idx] = (60 * ((g[idx] - b[idx]) / d[idx])) % 360
    idx = mask & (mx == g)
    h[idx] = 60 * ((b[idx] - r[idx]) / d[idx]) + 120
    idx = mask & (mx == b)
    h[idx] = 60 * ((r[idx] - g[idx]) / d[idx]) + 240
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)
    return h, s, mx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--catalog", default=str(DEV / "data/scene_gen_ext/asset_catalog.json")
    )
    ap.add_argument("--shadow", default=str(DEV / "data/robotwin_shadow"))
    ap.add_argument(
        "--out", default=str(DEV / "data/scene_gen_ext/asset_attributes.json")
    )
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dump-dir", help="也把渲染图存下来，便于亲眼复核")
    a = ap.parse_args()

    os.chdir(a.shadow)
    sys.path.insert(0, a.shadow)
    import sapien
    import yaml
    from envs.utils import create_actor

    _raw = yaml.safe_load(
        (DEV / "data/scene_gen_ext/asset_overrides_ext.yml").read_text()
    )
    overrides = _raw.get("assets") or {}

    cat = json.load(open(a.catalog))
    entries = cat["entries"] if isinstance(cat, dict) else cat
    todo = []
    for e in entries:
        aid = e["asset_id"]
        if a.only and aid not in a.only:
            continue
        for m in e.get("models", []):
            if (
                not m.get("usable")
                or m.get("load_type") == "urdf"
                or m.get("urdf_path")
            ):
                continue
            todo.append((aid, m["model_id"], tuple(m.get("dimensions_m") or ())))
    todo = sorted(set(todo))
    # 续测：GPU 被他人训练占用时渲染缓存会耗尽，外层脚本分批重启本进程，
    # 已测出的行必须原样保留（2026-08-14）
    done = {}
    outp = Path(a.out)
    if outp.exists() and not a.only:
        try:
            done = json.loads(outp.read_text()).get("models", {})
        except Exception:  # noqa: BLE001
            done = {}
    todo = [t for t in todo if str(t[1]) not in done.get(t[0], {})]
    print(f"待测: {len(todo)} 个可用模型（已完成 {sum(len(v) for v in done.values())}）", flush=True)

    dump = Path(a.dump_dir) if a.dump_dir else None
    if dump:
        dump.mkdir(parents=True, exist_ok=True)

    # ONE scene + ONE camera for the whole run: a fresh Scene per model leaks
    # renderer buffers and the run dies with "cannot create buffer" a few
    # hundred models in (measured 2026-08-14).
    scene = sapien.Scene()
    scene.set_ambient_light([0.85, 0.85, 0.85])
    scene.add_directional_light([0, 0.5, -1], [0.35, 0.35, 0.35])
    scene.add_directional_light([0, -0.5, -1], [0.35, 0.35, 0.35])
    cam = scene.add_camera(name="c", width=192, height=192, fovy=0.9, near=0.02, far=20)

    result = {k: dict(v) for k, v in done.items()}
    for aid, mid, dims in todo:
        decl = (overrides.get(aid) or {}).get("models", {}).get(str(mid), {})
        q = decl.get("stable_orientation_wxyz", [1, 0, 0, 0])
        row = {}
        actor = None
        try:
            span = max(dims) if dims else 0.3
            actor = create_actor(
                scene,
                pose=sapien.Pose([0, 0, 0], q),
                modelname=aid,
                model_id=mid,
                convex=True,
                is_static=True,
            )
            if actor is None:
                raise RuntimeError("create_actor returned None")
            d = max(0.22, span * 1.8)
            counts = {}
            total = 0
            for ang in (0.0, np.pi / 2, np.pi, 3 * np.pi / 2):
                eye = np.array([d * np.cos(ang), d * np.sin(ang), d * 0.55])
                cam.set_local_pose(
                    look_at(eye, np.array([0.0, 0.0, dims[2] / 2 if dims else 0.0]))
                )
                scene.step()
                scene.update_render()
                cam.take_picture()
                rgb = np.asarray(cam.get_picture("Color"))[..., :3]
                seg = np.asarray(cam.get_picture("Segmentation"))
                mask = seg[..., 1] > 0  # 该像素属于某个 actor，而非背景
                if dump and ang == 0.0:
                    from PIL import Image

                    Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).save(
                        dump / f"{aid}_m{mid}.png"
                    )
                if mask.sum() < 50:
                    continue
                px = rgb[mask]
                h, s, v = rgb_to_hsv(px)
                for i in range(px.shape[0]):
                    name = classify(float(h[i]), float(s[i]), float(v[i]))
                    total += 1
                    if name:
                        counts[name] = counts.get(name, 0) + 1
            if not total:
                raise RuntimeError("no object pixels rendered")
            frac = {k: v / total for k, v in counts.items()}
            gray_frac = 1.0 - sum(frac.values())
            ranked = sorted(frac.items(), key=lambda kv: -kv[1])
            colors = []
            if ranked and ranked[0][1] >= DOMINANT_MIN and gray_frac <= GRAY_MAX:
                colors.append(ranked[0][0])
                if len(ranked) > 1 and ranked[1][1] >= SECOND_MIN:
                    colors.append(ranked[1][0])
            row = {
                "colors": colors,
                "fractions": {k: round(v, 3) for k, v in ranked[:4]},
                "unlabeled_gray_fraction": round(gray_frac, 3),
                "pixels": int(total),
            }
        except Exception as ex:  # noqa: BLE001
            row = {"error": str(ex)[:120]}
        finally:
            if actor is not None:
                ent = getattr(actor, "actor", actor)
                for meth in ("remove_from_scene", "remove"):
                    fn = getattr(ent, meth, None)
                    if callable(fn):
                        try:
                            fn()
                            break
                        except Exception:  # noqa: BLE001
                            pass
                else:
                    rm = getattr(scene, "remove_actor", None)
                    if callable(rm):
                        rm(ent)
        result.setdefault(aid, {})[str(mid)] = row
        print(f"{aid}/m{mid}: {row.get('colors', row.get('error'))}", flush=True)
        try:
            import sapien.render as _sr

            _sr.clear_cache()
        except Exception:  # noqa: BLE001
            pass
        if "buffer" in str(row.get("error", "")):
            print("渲染缓存耗尽，保存进度并退出（外层会重启续测）", flush=True)
            result[aid].pop(str(mid), None)
            break

    Path(a.out).write_text(
        json.dumps(
            {
                "schema": "envgen.asset_attributes.v1",
                "method": "4-view offscreen render, HSV per-pixel vote, conservative publish",
                "thresholds": {
                    "dominant_min": DOMINANT_MIN,
                    "second_min": SECOND_MIN,
                    "gray_max": GRAY_MAX,
                },
                "measured_at": date.today().isoformat(),
                "models": result,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    n_col = sum(1 for ms in result.values() for r in ms.values() if r.get("colors"))
    print(f"\n完成：{n_col}/{len(todo)} 个模型给出了可发布颜色 -> {a.out}")


if __name__ == "__main__":
    main()
