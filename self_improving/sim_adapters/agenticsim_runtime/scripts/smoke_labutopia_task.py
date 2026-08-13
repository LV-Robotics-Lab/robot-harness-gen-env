#!/usr/bin/env python3
"""Run a bounded LabUtopia task without starting its data-writer controller."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _to_rgb_uint8(image: Any):
    import numpy as np

    array = np.asarray(image)
    if array.ndim != 3:
        raise ValueError(f"Expected a 3D camera image, got shape {array.shape}")
    if array.shape[0] in (3, 4) and (
        array.shape[-1] not in (3, 4) or array.shape[1] not in (3, 4)
    ):
        array = array.transpose(1, 2, 0)
    if array.shape[-1] == 4:
        array = array[..., :3]
    if array.shape[-1] != 3:
        raise ValueError(f"Expected RGB/RGBA channels, got shape {array.shape}")
    if array.dtype.kind == "f":
        finite = array[np.isfinite(array)]
        scale = 255.0 if finite.size and float(finite.max()) <= 1.0 else 1.0
        array = array * scale
    return np.clip(array, 0, 255).astype(np.uint8)


def _save_camera_mosaic(path: Path, camera_images: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    from PIL import Image

    if not camera_images:
        raise RuntimeError("LabUtopia returned no camera images")
    converted = [(name, _to_rgb_uint8(image)) for name, image in camera_images.items()]
    target_height = min(image.shape[0] for _, image in converted)
    resized = []
    for name, image in converted:
        if image.shape[0] != target_height:
            width = max(round(image.shape[1] * target_height / image.shape[0]), 1)
            image = np.asarray(Image.fromarray(image).resize((width, target_height)))
        resized.append((name, image))
    mosaic = np.hstack([image for _, image in resized])
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mosaic).save(path)
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "width": int(mosaic.shape[1]),
        "height": int(mosaic.shape[0]),
        "camera_names": [name for name, _ in resized],
    }


def _image_evidence(path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "width": width,
        "height": height,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--config-name", default="level1_pick")
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--viewport-screenshot", type=Path)
    parser.add_argument("--camera-eye", nargs=3, type=float, default=(3.2, 3.2, 2.6))
    parser.add_argument("--camera-lookat", nargs=3, type=float, default=(0.0, 0.0, 1.0))
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    config_dir = source_root / "config"
    if not config_dir.is_dir():
        parser.error(f"LabUtopia config directory is missing: {config_dir}")
    sys.path.insert(0, str(source_root))
    os.chdir(source_root)
    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")

    report: dict[str, Any] = {
        "schema": "agenticsim.labutopia_task_smoke.v1",
        "status": "failed",
        "source_root": str(source_root),
        "config_name": args.config_name,
        "steps_requested": max(int(args.steps), 1),
        "steps_completed": 0,
        "controller_started": False,
        "started_at_epoch_s": time.time(),
    }

    from isaacsim import SimulationApp

    app = SimulationApp(
        {
            "headless": True,
            "hide_ui": True,
            "renderer": "RaytracedLighting",
            "width": 1280,
            "height": 720,
            "fast_shutdown": True,
            "active_gpu": 0,
            "physics_gpu": 0,
            "multi_gpu": False,
            "max_gpu_count": 1,
            "extra_args": ["--/rtx/raytracing/fractionalCutoutOpacity=true"],
        }
    )
    try:
        import hydra
        import numpy as np
        import omni.usd
        from isaacsim.core.api import World
        from isaacsim.core.utils import extensions
        from isaacsim.core.utils.stage import add_reference_to_stage

        extensions.enable_extension("omni.physx.bundle")
        extensions.enable_extension("omni.usdphysics.ui")

        from factories.robot_factory import create_robot
        from factories.task_factory import create_task
        from utils.object_utils import ObjectUtils

        with hydra.initialize_config_dir(config_dir=str(config_dir), version_base=None):
            cfg = hydra.compose(config_name=args.config_name)

        world = World(
            stage_units_in_meters=1.0,
            physics_prim_path="/physicsScene",
            backend="numpy",
        )
        robot = create_robot(cfg.robot.type, position=np.array(cfg.robot.position))
        stage = omni.usd.get_context().get_stage()
        scene_path = (source_root / cfg.usd_path).resolve()
        if not scene_path.is_file():
            raise FileNotFoundError(f"Scene USD is missing: {scene_path}")
        add_reference_to_stage(usd_path=str(scene_path), prim_path="/World")
        ObjectUtils.get_instance(stage)
        task = create_task(
            cfg.task_type,
            cfg=cfg,
            world=world,
            stage=stage,
            robot=robot,
        )
        task.reset()

        last_state = None
        for index in range(report["steps_requested"]):
            world.step(render=True)
            state = task.step()
            report["steps_completed"] = index + 1
            if state is not None:
                last_state = state
        if last_state is None:
            raise RuntimeError("LabUtopia task returned no state")

        report["screenshot"] = _save_camera_mosaic(
            args.screenshot.resolve(), last_state["camera_display"]
        )
        if args.viewport_screenshot:
            from _isaac_gui import capture_active_viewport

            viewport_path = args.viewport_screenshot.resolve()
            viewport_path.parent.mkdir(parents=True, exist_ok=True)
            app.run_coroutine(
                capture_active_viewport(
                    viewport_path,
                    warmup_frames=12,
                    completion_frames=8,
                    timeout_s=120,
                    prefer_new_viewport=True,
                    width=1280,
                    height=720,
                    camera_eye=tuple(args.camera_eye),
                    camera_lookat=tuple(args.camera_lookat),
                )
            )
            report["viewport_screenshot"] = _image_evidence(viewport_path)
        report["task_state"] = _jsonable(
            {
                "frame_idx": task.frame_idx,
                "object_path": last_state.get("object_path"),
                "object_position": last_state.get("object_position"),
                "gripper_position": last_state.get("gripper_position"),
                "joint_positions": last_state.get("joint_positions"),
                "camera_data_keys": sorted(last_state.get("camera_data", {})),
            }
        )
        report["scene_usd"] = {
            "path": str(scene_path),
            "size_bytes": scene_path.stat().st_size,
            "sha256": hashlib.sha256(scene_path.read_bytes()).hexdigest(),
        }
        report["stage_prim_count"] = sum(1 for _ in stage.Traverse())
        report["status"] = "passed"
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        return 1
    finally:
        report["duration_s"] = round(time.time() - report["started_at_epoch_s"], 3)
        _write_json(args.report.resolve(), report)
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
