#!/usr/bin/env python3
"""Bounded Isaac Lab Gym task smoke runner with screenshot evidence."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from _isaac_gui import apply_camera_view, capture_active_viewport, parse_vector3, run_coroutine_with_timeout


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_ASSET_ROOT = ROOT / "assets" / "vendor" / "isaacsim_5_1_minimal"


def _jsonify(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "detach") and hasattr(value, "cpu"):
        return value.detach().cpu().tolist()
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(_jsonify(payload), indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    tmp_path.replace(path)


def _add_pythonpaths(paths: list[Path]) -> None:
    for path in reversed(paths):
        if not path:
            continue
        resolved = path if path.is_absolute() else ROOT / path
        value = str(resolved)
        if resolved.exists() and value not in sys.path:
            sys.path.insert(0, value)


def _parse_env_cfg_assignment(assignment: str) -> tuple[str, Any]:
    path, separator, raw_value = assignment.partition("=")
    path = path.strip()
    if not separator or not path:
        raise ValueError(f"Expected dotted.path=value, got: {assignment!r}")
    parts = path.split(".")
    if any(not part.isidentifier() or part.startswith("__") for part in parts):
        raise ValueError(f"Invalid environment config path: {path!r}")
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value
    return path, value


def _apply_env_cfg_assignments(env_cfg: Any, assignments: list[str]) -> list[dict[str, Any]]:
    applied = []
    for assignment in assignments:
        path, value = _parse_env_cfg_assignment(assignment)
        parts = path.split(".")
        target = env_cfg
        for part in parts[:-1]:
            if isinstance(target, dict):
                if part not in target:
                    raise AttributeError(f"Environment config path does not exist: {path!r}")
                target = target[part]
            else:
                if not hasattr(target, part):
                    raise AttributeError(f"Environment config path does not exist: {path!r}")
                target = getattr(target, part)
        leaf = parts[-1]
        if isinstance(target, dict):
            if leaf not in target:
                raise AttributeError(f"Environment config path does not exist: {path!r}")
            previous = target[leaf]
            target[leaf] = value
        else:
            if not hasattr(target, leaf):
                raise AttributeError(f"Environment config path does not exist: {path!r}")
            previous = getattr(target, leaf)
            setattr(target, leaf, value)
        applied.append({"path": path, "previous": previous, "value": value})
    return applied


def _describe_stage(env: Any, search_terms: list[str]) -> dict[str, Any]:
    stage = getattr(getattr(getattr(env, "unwrapped", env), "scene", None), "stage", None)
    if stage is None:
        return {"ready": False, "reason": "stage_unavailable"}
    matches: list[dict[str, str]] = []
    terms = [term.lower() for term in search_terms if term]
    for prim in stage.Traverse():
        path = prim.GetPath().pathString
        if not terms or any(term in path.lower() for term in terms):
            matches.append({"path": path, "type": prim.GetTypeName()})
            if len(matches) >= 80:
                break
    return {
        "ready": True,
        "root_layer": str(stage.GetRootLayer().identifier),
        "matches": matches,
    }


def _camera_from_bounds(
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
    padding: float = 2.2,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    center = tuple((low + high) / 2.0 for low, high in zip(minimum, maximum, strict=True))
    extent = max(high - low for low, high in zip(minimum, maximum, strict=True))
    radius = max(extent, 0.5) * float(padding)
    eye = (
        center[0] + radius,
        center[1] + radius,
        center[2] + radius * 0.55,
    )
    return eye, center


def _translate_bounds(
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
    authored_root: tuple[float, float, float],
    runtime_root: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    offset = tuple(
        runtime - authored
        for authored, runtime in zip(authored_root, runtime_root, strict=True)
    )
    return (
        tuple(value + delta for value, delta in zip(minimum, offset, strict=True)),
        tuple(value + delta for value, delta in zip(maximum, offset, strict=True)),
    )


def _describe_prim_geometry(env: Any, prim_path: str) -> dict[str, Any]:
    stage = getattr(getattr(getattr(env, "unwrapped", env), "scene", None), "stage", None)
    if stage is None:
        return {"ready": False, "path": prim_path, "reason": "stage_unavailable"}
    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        return {"ready": False, "path": prim_path, "reason": "prim_missing"}

    from pxr import Usd, UsdGeom

    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=True,
    )
    aligned = cache.ComputeWorldBound(prim).ComputeAlignedBox()
    minimum = tuple(float(value) for value in aligned.GetMin())
    maximum = tuple(float(value) for value in aligned.GetMax())
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    root_translation = tuple(float(value) for value in matrix.ExtractTranslation())
    if not all(math.isfinite(value) for value in (*minimum, *maximum)):
        return {"ready": False, "path": prim_path, "reason": "non_finite_bounds"}
    if any(high < low for low, high in zip(minimum, maximum, strict=True)):
        return {"ready": False, "path": prim_path, "reason": "invalid_bounds"}
    return {
        "ready": True,
        "path": prim_path,
        "type": prim.GetTypeName(),
        "minimum": minimum,
        "maximum": maximum,
        "root_translation": root_translation,
        "center": tuple((low + high) / 2.0 for low, high in zip(minimum, maximum, strict=True)),
        "extent": tuple(high - low for low, high in zip(minimum, maximum, strict=True)),
    }


def _configure_asset_root(asset_root: Path | None, report: dict[str, Any]) -> None:
    if asset_root is None:
        return
    resolved = asset_root if asset_root.is_absolute() else ROOT / asset_root
    if not resolved.is_dir():
        report["asset_root"] = {"path": str(resolved), "ready": False}
        return
    value = str(resolved.resolve())
    import carb

    carb.settings.get_settings().set("/persistent/isaac/asset_root/cloud", value)
    report["asset_root"] = {"path": value, "ready": True}
    assets_module = sys.modules.get("isaaclab.utils.assets")
    if assets_module is not None:
        assets_module.NUCLEUS_ASSET_ROOT_DIR = value
        assets_module.NVIDIA_NUCLEUS_DIR = f"{value}/NVIDIA"
        assets_module.ISAAC_NUCLEUS_DIR = f"{value}/Isaac"
        assets_module.ISAACLAB_NUCLEUS_DIR = f"{value}/Isaac/IsaacLab"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--register-module", action="append", default=[])
    parser.add_argument("--pythonpath", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--skip-reset", action="store_true")
    parser.add_argument("--skip-screenshot", action="store_true")
    parser.add_argument("--num-envs", type=int, default=1)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--disable-fabric", action="store_true")
    parser.add_argument("--camera-eye", default="")
    parser.add_argument("--camera-lookat", default="")
    parser.add_argument(
        "--frame-prim",
        default="",
        help="Frame this exact stage prim from its world-space render bounds.",
    )
    parser.add_argument(
        "--frame-asset",
        default="",
        help="Use this Isaac Lab scene asset's runtime root position when framing a prim.",
    )
    parser.add_argument("--frame-padding", type=float, default=2.2)
    parser.add_argument("--search-term", action="append", default=[])
    parser.add_argument(
        "--env-cfg-set",
        action="append",
        default=[],
        metavar="DOTTED.PATH=VALUE",
        help="Override an existing environment config field after task parsing; VALUE accepts JSON or a raw string.",
    )
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--capture-timeout-s", type=int, default=180)
    parser.add_argument(
        "--asset-root",
        type=Path,
        default=DEFAULT_LOCAL_ASSET_ROOT if DEFAULT_LOCAL_ASSET_ROOT.is_dir() else None,
        help="Local IsaacSim asset root mirroring /Assets/Isaac/5.1, used before IsaacLab tasks import assets.",
    )
    args = parser.parse_args()

    if not args.output.is_absolute():
        args.output = ROOT / args.output
    if not args.screenshot.is_absolute():
        args.screenshot = ROOT / args.screenshot

    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
    os.environ.setdefault("ACCEPT_EULA", "Y")
    os.environ.setdefault("NVIDIA_ACCEPT_EULA", "YES")
    os.environ.setdefault("ENABLE_CAMERAS", "1")
    _add_pythonpaths(args.pythonpath)

    started = time.time()
    report: dict[str, Any] = {
        "schema": "agenticsim.isaaclab_gym_task_smoke.v0",
        "status": "started",
        "task": args.task,
        "register_modules": args.register_module,
        "pythonpath": [str(path) for path in args.pythonpath],
        "steps_requested": int(args.steps),
        "num_envs": int(args.num_envs),
            "device": args.device,
            "screenshot": str(args.screenshot),
            "asset_root": str(args.asset_root) if args.asset_root else "",
            "env_cfg_overrides_requested": list(args.env_cfg_set),
        }

    simulation_app = None
    env = None
    def checkpoint(status: str, **values: Any) -> None:
        report.update({"status": status, "elapsed_s": round(time.time() - started, 3), **values})
        _write_json(args.output, report)

    try:
        from isaaclab.app import AppLauncher

        checkpoint("launching_app")
        launcher_args = argparse.Namespace(
            headless=bool(args.headless),
            enable_cameras=True,
            device=args.device,
            livestream=0,
            xr=False,
            offscreen_render=True,
            kit_args="",
            experience="",
            width=int(args.width),
            height=int(args.height),
            renderer="RaytracedLighting",
            display_options=3094,
            active_gpu=0,
            physics_gpu=0,
            portable=True,
        )
        app_launcher = AppLauncher(launcher_args)
        simulation_app = app_launcher.app
        _configure_asset_root(args.asset_root, report)
        checkpoint("app_launched")

        import gymnasium as gym
        import torch
        from isaaclab_tasks.utils import parse_env_cfg

        imported_modules = []
        for module_name in args.register_module:
            module = importlib.import_module(module_name)
            imported_modules.append({"module": module_name, "file": getattr(module, "__file__", "")})
        checkpoint("modules_imported", imported_modules=imported_modules)

        env_cfg = parse_env_cfg(
            args.task,
            device=args.device,
            num_envs=int(args.num_envs),
            use_fabric=not bool(args.disable_fabric),
        )
        env_cfg_overrides = _apply_env_cfg_assignments(env_cfg, args.env_cfg_set)
        checkpoint("env_cfg_parsed", env_cfg_overrides=env_cfg_overrides)
        env = gym.make(args.task, cfg=env_cfg)
        checkpoint(
            "env_created",
            observation_space=str(env.observation_space),
            action_space=str(env.action_space),
            stage=_describe_stage(env, args.search_term),
        )
        obs = None
        info = {}
        if not args.skip_reset:
            obs, info = env.reset()
            checkpoint("env_reset", reset_info=_jsonify(info))
        camera_eye = parse_vector3(args.camera_eye)
        camera_lookat = parse_vector3(args.camera_lookat)
        framed_prim = None
        framed_asset = None
        if args.frame_prim:
            framed_prim = _describe_prim_geometry(env, args.frame_prim)
            if not framed_prim.get("ready"):
                raise RuntimeError(f"Cannot frame prim {args.frame_prim!r}: {framed_prim.get('reason')}")
            minimum = tuple(framed_prim["minimum"])
            maximum = tuple(framed_prim["maximum"])
            if args.frame_asset:
                scene = getattr(env.unwrapped, "scene", None)
                asset = scene[args.frame_asset]
                root_position = tuple(
                    float(value)
                    for value in asset.data.root_pos_w[0].detach().cpu().tolist()
                )
                framed_asset = {"name": args.frame_asset, "root_position": root_position}
                minimum, maximum = _translate_bounds(
                    minimum,
                    maximum,
                    tuple(framed_prim["root_translation"]),
                    root_position,
                )
            camera_eye, camera_lookat = _camera_from_bounds(
                minimum,
                maximum,
                padding=float(args.frame_padding),
            )
        elif camera_eye is None or camera_lookat is None:
            viewer = getattr(getattr(env.unwrapped, "cfg", None), "viewer", None)
            camera_eye = tuple(float(v) for v in getattr(viewer, "eye", (2.5, 2.5, 1.5)))
            camera_lookat = tuple(float(v) for v in getattr(viewer, "lookat", (0.0, 0.0, 0.0)))
        apply_camera_view(env, camera_eye, camera_lookat)
        checkpoint(
            "camera_applied",
            camera_pose=[camera_eye, camera_lookat],
            framed_prim=framed_prim,
            framed_asset=framed_asset,
        )

        step_rows = []
        for step_idx in range(max(int(args.steps), 0)):
            with torch.inference_mode():
                actions = torch.zeros(env.action_space.shape, device=env.unwrapped.device)
                obs, reward, terminated, truncated, info = env.step(actions)
            if step_idx in {0, max(int(args.steps) - 1, 0)}:
                step_rows.append(
                    {
                        "step": step_idx,
                        "reward": _jsonify(reward),
                        "terminated": _jsonify(terminated),
                        "truncated": _jsonify(truncated),
                    }
                )
            simulation_app.update()
            if step_idx == 0 or step_idx == max(int(args.steps) - 1, 0):
                checkpoint("stepping", steps_completed=step_idx + 1, step_samples=step_rows)

        screenshot_ready = False
        screenshot_size = 0
        if not args.skip_screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            checkpoint(
                "capturing_screenshot",
                capture_timeout_s=int(args.capture_timeout_s),
                screenshot_ready=False,
                screenshot_size_bytes=0,
            )
            run_coroutine_with_timeout(
                simulation_app,
                capture_active_viewport(
                    args.screenshot.resolve(),
                    warmup_frames=8,
                    timeout_s=float(args.capture_timeout_s),
                    prefer_new_viewport=True,
                    width=int(args.width),
                    height=int(args.height),
                    camera_eye=camera_eye,
                    camera_lookat=camera_lookat,
                ),
                timeout_s=float(args.capture_timeout_s),
            )
            screenshot_ready = args.screenshot.is_file()
            screenshot_size = args.screenshot.stat().st_size if args.screenshot.is_file() else 0
        report.update(
            {
                "status": "captured",
                "elapsed_s": round(time.time() - started, 3),
                "imported_modules": imported_modules,
                "observation_space": str(env.observation_space),
                "action_space": str(env.action_space),
                "steps_completed": max(int(args.steps), 0),
                "step_samples": step_rows,
                "stage": _describe_stage(env, args.search_term),
                "camera_pose": [camera_eye, camera_lookat],
                "framed_prim": framed_prim,
                "framed_asset": framed_asset,
                "screenshot_ready": screenshot_ready,
                "screenshot_size_bytes": screenshot_size,
            }
        )
        _write_json(args.output, report)
        return 0
    except BaseException as exc:
        report.update(
            {
                "status": "failed",
                "elapsed_s": round(time.time() - started, 3),
                "error": repr(exc),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "screenshot_ready": bool(args.screenshot.is_file()),
                "screenshot_size_bytes": args.screenshot.stat().st_size if args.screenshot.is_file() else 0,
            }
        )
        _write_json(args.output, report)
        return 1
    finally:
        if env is not None:
            try:
                env.close()
            except Exception:
                pass
        if simulation_app is not None:
            simulation_app.close()
        _write_json(args.output, report)


if __name__ == "__main__":
    raise SystemExit(main())
