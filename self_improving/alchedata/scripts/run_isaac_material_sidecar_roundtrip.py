#!/usr/bin/env python3
"""Extract a material sidecar from a RoboTwin image and render it in Isaac Sim."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_crop(value: str) -> tuple[int, int, int, int]:
    coordinates = tuple(int(part) for part in value.split(","))
    if len(coordinates) != 4:
        raise ValueError("Crop must have four comma-separated integers: x0,y0,x1,y1")
    x0, y0, x1, y1 = coordinates
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise ValueError(f"Invalid crop coordinates: {coordinates}")
    return coordinates


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=np.float64)
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    linear = srgb_to_linear(np.asarray(rgb, dtype=np.float64))
    xyz = np.asarray(
        [
            0.4124564 * linear[0] + 0.3575761 * linear[1] + 0.1804375 * linear[2],
            0.2126729 * linear[0] + 0.7151522 * linear[1] + 0.0721750 * linear[2],
            0.0193339 * linear[0] + 0.1191920 * linear[1] + 0.9503041 * linear[2],
        ]
    )
    normalized = xyz / np.asarray([0.95047, 1.0, 1.08883])
    delta = 6.0 / 29.0
    transformed = np.where(
        normalized > delta**3,
        np.cbrt(normalized),
        normalized / (3.0 * delta**2) + 4.0 / 29.0,
    )
    return np.asarray(
        [
            116.0 * transformed[1] - 16.0,
            500.0 * (transformed[0] - transformed[1]),
            200.0 * (transformed[1] - transformed[2]),
        ]
    )


def red_foreground_mask(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=np.float64)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (red > 110.0) & (red > 1.35 * green) & (red > 1.35 * blue)


def extract_sidecar(image: np.ndarray, crop: tuple[int, int, int, int]) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    x0, y0, x1, y1 = crop
    if x1 > image.shape[1] or y1 > image.shape[0]:
        raise ValueError(f"Crop {crop} exceeds image shape {image.shape}")
    crop_rgb = np.asarray(image[y0:y1, x0:x1, :3], dtype=np.uint8)
    mask = red_foreground_mask(crop_rgb)
    if int(np.count_nonzero(mask)) < 24:
        raise ValueError("Fewer than 24 red foreground pixels were extracted")
    pixels = crop_rgb[mask].astype(np.float64) / 255.0
    median_srgb = np.median(pixels, axis=0)
    luminance = pixels @ np.asarray([0.2126, 0.7152, 0.0722])
    highlight_ratio = float(np.percentile(luminance, 95) / max(np.median(luminance), 1e-6))
    roughness = float(np.clip(1.15 - 0.42 * highlight_ratio, 0.18, 0.9))
    sidecar = {
        "schema_version": "alchedata.material_sidecar.v0",
        "status": "pass_observation_material_extraction",
        "method": "deterministic_red_foreground_observation_heuristic",
        "source_crop_xyxy": list(crop),
        "foreground_pixel_count": int(np.count_nonzero(mask)),
        "foreground_fraction": float(np.mean(mask)),
        "base_color_srgb": median_srgb.tolist(),
        "base_color_linear": srgb_to_linear(median_srgb).tolist(),
        "roughness": roughness,
        "metallic": 0.0,
        "opacity": 1.0,
        "roughness_heuristic": {
            "highlight_ratio_p95_over_median_luminance": highlight_ratio,
            "formula": "clip(1.15 - 0.42 * highlight_ratio, 0.18, 0.90)",
        },
        "claim_boundary": (
            "This is a deterministic observation heuristic for a bounded roundtrip, not intrinsic decomposition, "
            "BRDF identification, relighting invariance, or a NeuMaTeX reproduction."
        ),
    }
    return sidecar, crop_rgb, mask


def save_masked(path: Path, rgb: np.ndarray, mask: np.ndarray) -> None:
    output = np.zeros_like(rgb)
    output[mask] = rgb[mask]
    Image.fromarray(output).save(path)


def compare_foregrounds(source_rgb: np.ndarray, source_mask: np.ndarray, rendered_rgb: np.ndarray) -> tuple[dict[str, Any], np.ndarray]:
    target_mask = red_foreground_mask(rendered_rgb)
    if int(np.count_nonzero(target_mask)) < 100:
        raise ValueError("Rendered foreground segmentation contains fewer than 100 pixels")
    source_median = np.median(source_rgb[source_mask].astype(np.float64) / 255.0, axis=0)
    target_median = np.median(rendered_rgb[target_mask].astype(np.float64) / 255.0, axis=0)
    source_lab = rgb_to_lab(source_median)
    target_lab = rgb_to_lab(target_median)
    metrics = {
        "source_foreground_pixel_count": int(np.count_nonzero(source_mask)),
        "rendered_foreground_pixel_count": int(np.count_nonzero(target_mask)),
        "source_median_srgb": source_median.tolist(),
        "rendered_median_srgb": target_median.tolist(),
        "rgb_mean_absolute_error": float(np.mean(np.abs(source_median - target_median))),
        "rgb_root_mean_squared_error": float(np.sqrt(np.mean((source_median - target_median) ** 2))),
        "source_cie_lab": source_lab.tolist(),
        "rendered_cie_lab": target_lab.tolist(),
        "cie76_delta_e": float(np.linalg.norm(source_lab - target_lab)),
        "comparison_scope": "robust median foreground color; geometry, framing, and lighting are not pixel-aligned",
    }
    return metrics, target_mask


def file_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(root.resolve())),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def write_manifest(out_dir: Path) -> dict[str, Any]:
    rows = [
        file_record(path, out_dir)
        for path in sorted(out_dir.rglob("*"))
        if path.is_file() and path.name != "bundle_manifest.json"
    ]
    manifest = {
        "schema_version": "alchedata.material_roundtrip_manifest.v0",
        "status": "pass_material_roundtrip_bundle",
        "file_count": len(rows),
        "files": rows,
    }
    write_json(out_dir / "bundle_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-image", type=Path, required=True)
    parser.add_argument("--crop", default="190,129,212,151")
    parser.add_argument("--agenticsim-runtime", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=640)
    args = parser.parse_args()
    source_path = args.source_image.expanduser().resolve()
    runtime = args.agenticsim_runtime.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    crop = parse_crop(args.crop)
    source_image = np.asarray(Image.open(source_path).convert("RGB"), dtype=np.uint8)
    sidecar, source_crop, source_mask = extract_sidecar(source_image, crop)
    sidecar.update(
        {
            "generated_at": utc_now(),
            "source_image": str(source_path),
            "source_image_sha256": sha256_file(source_path),
        }
    )
    Image.fromarray(source_crop).resize((352, 352), Image.Resampling.NEAREST).save(out_dir / "source_crop.png")
    save_masked(out_dir / "source_foreground.png", source_crop, source_mask)
    sidecar_path = out_dir / "material_sidecar.json"
    write_json(sidecar_path, sidecar)
    run_state = {
        "schema_version": "alchedata.material_roundtrip_run_state.v0",
        "state": "started",
        "started_at": utc_now(),
        "source_image_sha256": sidecar["source_image_sha256"],
    }
    write_json(out_dir / "run_state.json", run_state)

    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
    nvidia_icd = Path("/usr/share/vulkan/icd.d/nvidia_icd.json")
    if nvidia_icd.is_file():
        os.environ.setdefault("VK_ICD_FILENAMES", str(nvidia_icd))
    sys.path.insert(0, str(runtime / "scripts"))
    from _isaac_gui import capture_active_viewport
    from isaacsim import SimulationApp

    app = SimulationApp(
        {
            "headless": True,
            "hide_ui": True,
            "renderer": "RaytracedLighting",
            "width": args.width,
            "height": args.height,
            "fast_shutdown": True,
            "active_gpu": 0,
            "physics_gpu": 0,
            "multi_gpu": False,
            "max_gpu_count": 1,
        }
    )
    summary: dict[str, Any]
    try:
        import omni.timeline
        from isaacsim.core.api import World
        from isaacsim.core.utils.viewports import set_camera_view
        from pxr import Gf, Sdf, UsdGeom, UsdLux, UsdShade

        world = World(stage_units_in_meters=1.0)
        world.scene.add_default_ground_plane()
        sphere = UsdGeom.Sphere.Define(world.stage, "/World/ExtractedMaterialSphere")
        sphere.CreateRadiusAttr(0.25)
        UsdGeom.Xformable(sphere).AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.25))
        material = UsdShade.Material.Define(world.stage, "/World/Looks/ExtractedObservationMaterial")
        shader = UsdShade.Shader.Define(world.stage, "/World/Looks/ExtractedObservationMaterial/PreviewSurface")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*[float(value) for value in sidecar["base_color_linear"]])
        )
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(sidecar["roughness"]))
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(sidecar["metallic"]))
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(sidecar["opacity"]))
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)

        dome = UsdLux.DomeLight.Define(world.stage, "/World/MaterialDome")
        dome.CreateIntensityAttr(450.0)
        dome.CreateColorAttr(Gf.Vec3f(1.0, 1.0, 1.0))
        key = UsdLux.DistantLight.Define(world.stage, "/World/MaterialKey")
        key.CreateIntensityAttr(1800.0)
        key.CreateAngleAttr(1.0)
        key.CreateColorAttr(Gf.Vec3f(1.0, 0.98, 0.96))
        set_camera_view([1.25, 1.25, 0.78], [0.0, 0.0, 0.25], camera_prim_path="/OmniverseKit_Persp")
        world.reset()
        for _ in range(12):
            world.step(render=True)

        stage_path = out_dir / "material_scene.usda"
        world.stage.GetRootLayer().Export(str(stage_path))
        timeline = omni.timeline.get_timeline_interface()
        was_playing = timeline.is_playing()
        timeline.pause()
        render_path = out_dir / "isaac_material_render.png"
        try:
            app.run_coroutine(
                capture_active_viewport(
                    render_path,
                    warmup_frames=12,
                    completion_frames=6,
                    timeout_s=120,
                    prefer_new_viewport=True,
                    width=args.width,
                    height=args.height,
                    camera_eye=(1.25, 1.25, 0.78),
                    camera_lookat=(0.0, 0.0, 0.25),
                )
            )
        finally:
            if was_playing:
                timeline.play()

        bound_material, _ = UsdShade.MaterialBindingAPI(sphere.GetPrim()).ComputeBoundMaterial()
        imported = {
            "schema_version": "alchedata.isaac_material_import.v0",
            "status": "pass_usd_preview_surface_binding",
            "simulator": "Isaac Sim 5.1",
            "sidecar": file_record(sidecar_path, out_dir),
            "scene_stage": file_record(stage_path, out_dir),
            "material_prim": str(material.GetPrim().GetPath()),
            "shader_id": str(shader.GetIdAttr().Get()),
            "bound_material_prim": str(bound_material.GetPrim().GetPath()),
            "bound": bool(bound_material and bound_material.GetPrim().IsValid()),
            "imported_inputs": {
                "diffuseColor_linear": list(shader.GetInput("diffuseColor").Get()),
                "roughness": float(shader.GetInput("roughness").Get()),
                "metallic": float(shader.GetInput("metallic").Get()),
                "opacity": float(shader.GetInput("opacity").Get()),
            },
            "renderer": "RaytracedLighting",
        }
        if not imported["bound"] or imported["shader_id"] != "UsdPreviewSurface":
            raise RuntimeError("Isaac material binding verification failed")
        write_json(out_dir / "isaac_material_import.json", imported)

        rendered = np.asarray(Image.open(render_path).convert("RGB"), dtype=np.uint8)
        metrics, target_mask = compare_foregrounds(source_crop, source_mask, rendered)
        save_masked(out_dir / "isaac_render_foreground.png", rendered, target_mask)
        comparison = {
            "schema_version": "alchedata.material_roundtrip_comparison.v0",
            "status": "pass_material_roundtrip_comparison",
            "source_image": file_record(source_path, source_path.parent),
            "rendered_image": file_record(render_path, out_dir),
            "metrics": metrics,
            "acceptance": {
                "roundtrip_completed": True,
                "finite_metrics": all(
                    np.isfinite(value)
                    for value in (
                        metrics["rgb_mean_absolute_error"],
                        metrics["rgb_root_mean_squared_error"],
                        metrics["cie76_delta_e"],
                    )
                ),
                "visual_parity_threshold_predeclared": False,
            },
        }
        write_json(out_dir / "comparison.json", comparison)
        run_state.update(
            {
                "state": "completed",
                "finished_at": utc_now(),
                "runtime": {
                    "hostname": socket.gethostname(),
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "simulator": "Isaac Sim 5.1",
                },
                "gates": {
                    "observation_extraction": sidecar["status"],
                    "native_material_import": imported["status"],
                    "render": render_path.is_file(),
                    "comparison": comparison["status"],
                },
            }
        )
        write_json(out_dir / "run_state.json", run_state)
        manifest = write_manifest(out_dir)
        report = {
            "schema_version": "alchedata.material_sidecar_roundtrip.v0",
            "status": "pass_material_sidecar_roundtrip",
            "source_observation": file_record(source_path, source_path.parent),
            "material_sidecar": file_record(sidecar_path, out_dir),
            "isaac_import": file_record(out_dir / "isaac_material_import.json", out_dir),
            "isaac_render": file_record(render_path, out_dir),
            "comparison": file_record(out_dir / "comparison.json", out_dir),
            "metrics": metrics,
            "bundle_file_count_before_report": manifest["file_count"],
            "claim_boundary": sidecar["claim_boundary"],
        }
        write_json(out_dir / "roundtrip_report.json", report)
        manifest = write_manifest(out_dir)
        summary = {
            "status": report["status"],
            "rgb_mae": metrics["rgb_mean_absolute_error"],
            "cie76_delta_e": metrics["cie76_delta_e"],
            "file_count": manifest["file_count"],
            "out_dir": str(out_dir),
        }
    except Exception as exc:  # noqa: BLE001
        run_state.update(
            {
                "state": "failed",
                "finished_at": utc_now(),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        write_json(out_dir / "run_state.json", run_state)
        summary = {"status": "fail_material_sidecar_roundtrip", "error": run_state["error"], "out_dir": str(out_dir)}
    finally:
        app.close()
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "pass_material_sidecar_roundtrip" else 1


if __name__ == "__main__":
    raise SystemExit(main())
