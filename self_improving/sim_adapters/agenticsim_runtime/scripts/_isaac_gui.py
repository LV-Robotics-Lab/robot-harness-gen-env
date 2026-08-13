"""Shared Isaac Sim GUI helpers for AgenticSim scripts."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

import numpy as np


def unwrap_env(env):
    return getattr(env, "unwrapped", env)


def build_launch_config(args: Any) -> dict[str, object]:
    """Use a conservative GUI config that is less crash-prone on desktop RTX setups."""
    return {
        "headless": bool(getattr(args, "headless", False)),
        "hide_ui": bool(getattr(args, "hide_ui", False)),
        "active_gpu": args.active_gpu,
        "physics_gpu": args.physics_gpu,
        "multi_gpu": False,
        "max_gpu_count": 1,
        "sync_loads": True,
        "width": args.width,
        "height": args.height,
        "window_width": args.window_width,
        "window_height": args.window_height,
        "renderer": args.renderer,
        "anti_aliasing": args.anti_aliasing,
        "samples_per_pixel_per_frame": args.samples_per_pixel_per_frame,
        "denoiser": args.denoiser,
        "fast_shutdown": True,
        "limit_cpu_threads": args.limit_cpu_threads,
        "extra_args": [
            "--/renderer/multiGpu/autoEnable=0",
            "--/renderer/multiGpu/enabled=0",
        ],
    }


def configure_isaaclab_runtime_settings(*, headless: bool, offscreen: bool | None = None) -> dict[str, object]:
    """Set IsaacLab app flags across older local and upstream-main IsaacLab APIs."""
    values = {
        "/isaaclab/cameras_enabled": True,
        "/isaaclab/has_gui": not bool(headless),
        "/isaaclab/render/offscreen": bool(headless) if offscreen is None else bool(offscreen),
        "/isaaclab/render/active_viewport": True,
        "/isaaclab/render/rtx_sensors": True,
    }
    try:
        from isaaclab.app.settings_manager import get_settings_manager, initialize_carb_settings

        initialize_carb_settings()
        settings = get_settings_manager()
        source = "isaaclab.app.settings_manager"
    except ModuleNotFoundError:
        import carb

        settings = carb.settings.get_settings()
        source = "carb.settings"

    for key, value in values.items():
        settings.set_bool(key, value)
    return {"source": source, "values": values}


def make_zero_action(env, obs: object, fallback: int) -> np.ndarray:
    action_space = getattr(env, "action_space", None)
    shape = getattr(action_space, "shape", None)
    if shape and len(shape) >= 1 and all(dim for dim in shape):
        return np.zeros(shape, dtype=np.float32)

    if isinstance(obs, tuple) and len(obs) == 2:
        obs = obs[0]
    if isinstance(obs, dict):
        joint_pos = obs.get("joint_pos")
        if hasattr(joint_pos, "shape") and joint_pos.shape:
            num_envs = int(getattr(getattr(env, "unwrapped", env), "num_envs", 1))
            return np.zeros((num_envs, int(joint_pos.shape[-1])), dtype=np.float32)

    num_envs = int(getattr(getattr(env, "unwrapped", env), "num_envs", 1))
    return np.zeros((num_envs, fallback), dtype=np.float32)


def parse_vector3(text: str) -> tuple[float, float, float] | None:
    value = (text or "").strip()
    if not value:
        return None
    parts = [part.strip() for part in value.replace(";", ",").split(",")]
    if len(parts) != 3 or any(not part for part in parts):
        raise ValueError(f"Expected x,y,z triple, got: {text!r}")
    return float(parts[0]), float(parts[1]), float(parts[2])


def resolve_viewer_camera(
    env,
    *,
    eye_override: tuple[float, float, float] | None = None,
    lookat_override: tuple[float, float, float] | None = None,
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    if eye_override is not None and lookat_override is not None:
        return eye_override, lookat_override

    viewer = getattr(getattr(unwrap_env(env), "cfg", None), "viewer", None)
    eye = eye_override or getattr(viewer, "eye", None)
    lookat = lookat_override or getattr(viewer, "lookat", None)
    if eye is None or lookat is None:
        return None
    return tuple(float(v) for v in eye), tuple(float(v) for v in lookat)


def apply_camera_view(env, eye: tuple[float, float, float], lookat: tuple[float, float, float]) -> None:
    unwrapped_env = unwrap_env(env)
    sim = getattr(unwrapped_env, "sim", None)
    if sim is not None and hasattr(sim, "set_camera_view"):
        sim.set_camera_view(eye=eye, target=lookat)

    from isaacsim.core.utils.viewports import set_camera_view

    viewer = getattr(getattr(unwrapped_env, "cfg", None), "viewer", None)
    camera_prim_path = getattr(viewer, "cam_prim_path", "/OmniverseKit_Persp")
    set_camera_view(list(eye), list(lookat), camera_prim_path=camera_prim_path)


def describe_active_viewport() -> dict[str, object]:
    from omni.kit.viewport.utility import get_active_viewport, get_active_viewport_camera_string

    viewport = get_active_viewport()
    if viewport is None:
        return {"ready": False}

    return {
        "ready": True,
        "camera": get_active_viewport_camera_string(),
        "resolution": tuple(int(v) for v in viewport.resolution),
        "render_product_path": str(getattr(viewport, "render_product_path", "")),
    }


def describe_camera_prim(camera_prim_path: str) -> dict[str, object]:
    from omni.usd import get_context
    from pxr import Gf, UsdGeom

    stage = get_context().get_stage()
    if stage is None:
        return {"ready": False, "camera_prim_path": camera_prim_path, "reason": "stage_unavailable"}

    prim = stage.GetPrimAtPath(camera_prim_path)
    if not prim.IsValid():
        return {"ready": False, "camera_prim_path": camera_prim_path, "reason": "camera_prim_missing"}

    xformable = UsdGeom.Xformable(prim)
    matrix = xformable.ComputeLocalToWorldTransform(0.0)
    translation = matrix.ExtractTranslation()
    forward = matrix.TransformDir(Gf.Vec3d(0.0, 0.0, -1.0))
    return {
        "ready": True,
        "camera_prim_path": camera_prim_path,
        "translation": tuple(float(v) for v in translation),
        "forward": tuple(float(v) for v in forward),
        "prim_type": prim.GetTypeName(),
    }


def describe_stage_matches(
    env,
    *,
    expected_paths: list[str] | tuple[str, ...] = (),
    search_terms: list[str] | tuple[str, ...] = (),
    limit: int = 32,
    prefer_current_stage: bool = False,
) -> dict[str, object]:
    from omni.usd import get_context

    stage = None if prefer_current_stage else getattr(getattr(unwrap_env(env), "scene", None), "stage", None)
    if stage is None:
        stage = get_context().get_stage()
    if stage is None:
        return {"ready": False, "reason": "stage_unavailable"}

    existing_paths = []
    for prim_path in expected_paths:
        prim = stage.GetPrimAtPath(prim_path)
        existing_paths.append(
            {
                "path": prim_path,
                "exists": bool(prim.IsValid()),
                "type": prim.GetTypeName() if prim.IsValid() else "",
            }
        )

    normalized_terms = [term.lower() for term in search_terms if term]
    matches: list[dict[str, str]] = []
    if normalized_terms:
        for prim in stage.Traverse():
            path = prim.GetPath().pathString
            lower_path = path.lower()
            if any(term in lower_path for term in normalized_terms):
                matches.append({"path": path, "type": prim.GetTypeName()})
                if len(matches) >= max(int(limit), 1):
                    break

    return {
        "ready": True,
        "stage_root": str(stage.GetRootLayer().identifier),
        "existing_paths": existing_paths,
        "matches": matches,
    }


def describe_stage_sources(env, *, top_level_limit: int = 16) -> dict[str, object]:
    from omni.usd import get_context

    env_stage = getattr(getattr(unwrap_env(env), "scene", None), "stage", None)
    current_stage = get_context().get_stage()

    def _stage_info(stage) -> dict[str, object]:
        if stage is None:
            return {"ready": False}

        pseudo_root = stage.GetPseudoRoot()
        children = []
        for prim in pseudo_root.GetChildren():
            children.append(prim.GetPath().pathString)
            if len(children) >= max(int(top_level_limit), 1):
                break

        return {
            "ready": True,
            "root_layer": str(stage.GetRootLayer().identifier),
            "session_layer": str(stage.GetSessionLayer().identifier),
            "top_level_prims": children,
        }

    return {
        "env_stage": _stage_info(env_stage),
        "current_stage": _stage_info(current_stage),
        "same_object": bool(env_stage is not None and current_stage is not None and env_stage == current_stage),
    }


def describe_light_prim(light_prim_path: str = "/World/Light") -> dict[str, object]:
    from omni.usd import get_context

    stage = get_context().get_stage()
    if stage is None:
        return {"ready": False, "light_prim_path": light_prim_path, "reason": "stage_unavailable"}

    prim = stage.GetPrimAtPath(light_prim_path)
    if not prim.IsValid():
        return {"ready": False, "light_prim_path": light_prim_path, "reason": "light_prim_missing"}

    intensity_attr = prim.GetAttribute("inputs:intensity")
    color_attr = prim.GetAttribute("inputs:color")
    return {
        "ready": True,
        "light_prim_path": light_prim_path,
        "prim_type": prim.GetTypeName(),
        "intensity": intensity_attr.Get() if intensity_attr.IsValid() else None,
        "color": color_attr.Get() if color_attr.IsValid() else None,
    }


def describe_light_collection(light_root_path: str = "/Environment/light", *, limit: int = 32) -> dict[str, object]:
    from omni.usd import get_context

    stage = get_context().get_stage()
    if stage is None:
        return {"ready": False, "light_root_path": light_root_path, "reason": "stage_unavailable"}

    root_prim = stage.GetPrimAtPath(light_root_path)
    if not root_prim.IsValid():
        return {"ready": False, "light_root_path": light_root_path, "reason": "light_root_missing"}

    lights: list[dict[str, object]] = []
    for prim in stage.Traverse():
        path = prim.GetPath().pathString
        if not path.startswith(f"{light_root_path}/"):
            continue
        prim_type = prim.GetTypeName()
        if not prim_type.endswith("Light"):
            continue
        intensity_attr = prim.GetAttribute("inputs:intensity")
        color_attr = prim.GetAttribute("inputs:color")
        lights.append(
            {
                "path": path,
                "type": prim_type,
                "intensity": intensity_attr.Get() if intensity_attr.IsValid() else None,
                "color": color_attr.Get() if color_attr.IsValid() else None,
            }
        )
        if len(lights) >= max(int(limit), 1):
            break

    return {
        "ready": True,
        "light_root_path": light_root_path,
        "light_count": len(lights),
        "lights": lights,
    }


def describe_bound_material(prim_path: str) -> dict[str, object]:
    from omni.usd import get_context
    from pxr import Sdf, Usd, UsdShade

    stage = get_context().get_stage()
    if stage is None:
        return {"ready": False, "prim_path": prim_path, "reason": "stage_unavailable"}

    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        return {"ready": False, "prim_path": prim_path, "reason": "prim_missing"}

    target_prim = prim
    if prim.GetTypeName() != "Mesh":
        for descendant in Usd.PrimRange(prim):
            if descendant.GetTypeName() == "Mesh":
                target_prim = descendant
                break

    material, _ = UsdShade.MaterialBindingAPI(target_prim).ComputeBoundMaterial()
    if not material:
        return {
            "ready": False,
            "prim_path": prim_path,
            "target_prim_path": target_prim.GetPath().pathString,
            "reason": "material_unbound",
        }

    texture_sources: list[dict[str, object]] = []
    for shader_prim in Usd.PrimRange(material.GetPrim()):
        if shader_prim.GetTypeName() != "Shader":
            continue
        shader = UsdShade.Shader(shader_prim)
        asset_inputs: dict[str, str] = {}
        for shader_input in shader.GetInputs():
            value = shader_input.Get()
            if isinstance(value, Sdf.AssetPath):
                asset_inputs[shader_input.GetBaseName()] = value.path
        if asset_inputs:
            texture_sources.append(
                {
                    "shader_path": shader_prim.GetPath().pathString,
                    "info_id": shader.GetIdAttr().Get(),
                    "asset_inputs": asset_inputs,
                }
            )

    return {
        "ready": True,
        "prim_path": prim_path,
        "target_prim_path": target_prim.GetPath().pathString,
        "material_path": material.GetPath().pathString,
        "texture_sources": texture_sources,
    }


def override_light_intensity(light_prim_path: str, intensity: float) -> None:
    from omni.usd import get_context

    stage = get_context().get_stage()
    if stage is None:
        raise RuntimeError("Cannot override light intensity because the stage is unavailable.")

    prim = stage.GetPrimAtPath(light_prim_path)
    if not prim.IsValid():
        raise RuntimeError(f"Light prim not found at {light_prim_path}")

    intensity_attr = prim.GetAttribute("inputs:intensity")
    if not intensity_attr.IsValid():
        raise RuntimeError(f"Light prim {light_prim_path} does not expose inputs:intensity")
    intensity_attr.Set(float(intensity))


def run_coroutine_with_timeout(app: Any, coroutine: Any, *, timeout_s: float | None = None) -> Any:
    """Run a Kit coroutine while enforcing a wall-clock timeout in Python."""
    import time

    if not timeout_s or timeout_s <= 0:
        return app.run_coroutine(coroutine)

    task = app.run_coroutine(coroutine, run_until_complete=False)
    deadline = time.monotonic() + float(timeout_s)
    while not task.done():
        if time.monotonic() >= deadline:
            task.cancel()
            raise TimeoutError(f"Kit coroutine timed out after {timeout_s}s")
        app.update()
    return task.result()


async def capture_active_viewport(
    output_path: Path,
    *,
    warmup_frames: int = 8,
    completion_frames: int = 30,
    timeout_s: float | None = None,
    prefer_new_viewport: bool = False,
    width: int = 960,
    height: int = 540,
    camera_eye: tuple[float, float, float] | None = None,
    camera_lookat: tuple[float, float, float] | None = None,
) -> Path:
    import asyncio
    import time

    import omni.kit.renderer_capture
    from omni.kit.viewport.utility import (
        capture_viewport_to_file,
        create_viewport_window,
        get_active_viewport,
        next_viewport_frame_async,
    )

    capture_window = None
    viewport = None
    if prefer_new_viewport:
        capture_window = create_viewport_window(
            name="AgenticSimCaptureViewport",
            width=max(int(width), 64),
            height=max(int(height), 64),
        )
        viewport = getattr(capture_window, "viewport_api", None)
    if viewport is None:
        viewport = get_active_viewport()
    if viewport is None:
        raise RuntimeError("Active viewport is not ready for capture.")
    if camera_eye is not None and camera_lookat is not None:
        from isaacsim.core.utils.viewports import set_camera_view

        camera_path = getattr(viewport, "camera_path", None)
        camera_prim_path = getattr(camera_path, "pathString", None) or str(camera_path or "/OmniverseKit_Persp")
        set_camera_view(list(camera_eye), list(camera_lookat), camera_prim_path=camera_prim_path, viewport_api=viewport)

    deadline = time.monotonic() + float(timeout_s) if timeout_s and timeout_s > 0 else None

    async def _with_deadline(awaitable, phase: str):
        if deadline is None:
            return await awaitable
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Viewport capture timed out before {phase}")
        return await asyncio.wait_for(awaitable, timeout=remaining)

    try:
        await _with_deadline(next_viewport_frame_async(viewport, max(int(warmup_frames), 0)), "warmup")
        capture = capture_viewport_to_file(viewport, file_path=str(output_path), is_hdr=False)
        success = await _with_deadline(
            capture.wait_for_result(completion_frames=max(int(completion_frames), 1)),
            "capture",
        )
        omni.kit.renderer_capture.acquire_renderer_capture_interface().wait_async_capture()
        if not success or not output_path.exists():
            raise RuntimeError(f"Viewport capture failed for {output_path}")
        return output_path
    finally:
        if capture_window is not None:
            destroy = getattr(capture_window, "destroy", None)
            if callable(destroy):
                destroy()


def encode_png_sequence_to_mp4(frame_dir: Path, output_path: Path, *, fps: float = 8.0) -> dict[str, object]:
    """Encode frame_00000.png ... into an MP4 using the system ffmpeg."""
    frame_dir = frame_dir.resolve()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames = sorted(frame_dir.glob("frame_*.png"))
    if not frames:
        return {"ready": False, "reason": "no_frames", "frame_dir": str(frame_dir), "output_path": str(output_path)}

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        str(float(fps)),
        "-i",
        str(frame_dir / "frame_%05d.png"),
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    ready = result.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0
    return {
        "ready": ready,
        "frame_count": len(frames),
        "fps": float(fps),
        "frame_dir": str(frame_dir),
        "output_path": str(output_path),
        "size_bytes": output_path.stat().st_size if output_path.is_file() else 0,
        "returncode": result.returncode,
        "stderr": result.stderr[-2000:],
    }
