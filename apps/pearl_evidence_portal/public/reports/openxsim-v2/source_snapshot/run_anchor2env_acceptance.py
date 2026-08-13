#!/usr/bin/env python3
"""Build three image/video anchored scenes with ablation and runtime evidence."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from _bootstrap import bootstrap_repo_source

bootstrap_repo_source()

from agenticsim.openxsim.anchors import (  # noqa: E402
    AnchorExtractionError,
    ColorLayoutAnchorProvider,
    extract_anchor,
    fuse_anchor,
)
from agenticsim.openxsim.ir import EnvironmentPackage  # noqa: E402
from agenticsim.openxsim.pipeline import OpenXSimPipeline  # noqa: E402
from agenticsim.openxsim.text2env import compile_text  # noqa: E402


COLORS = {
    "red": (220, 28, 35),
    "green": (28, 165, 75),
    "blue": (30, 72, 220),
    "yellow": (232, 190, 30),
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def make_layout_image(
    path: Path,
    *,
    object_color: str,
    target_color: str,
    object_center: tuple[int, int],
    target_center: tuple[int, int],
) -> Path:
    image = Image.new("RGB", (480, 320), color=(242, 244, 246))
    draw = ImageDraw.Draw(image)
    draw.rectangle((28, 24, 452, 296), fill=(226, 229, 232), outline=(86, 94, 102), width=3)
    tx, ty = target_center
    draw.ellipse((tx - 54, ty - 42, tx + 54, ty + 42), fill=COLORS[target_color], outline=(32, 37, 42), width=4)
    ox, oy = object_center
    draw.rectangle((ox - 34, oy - 34, ox + 34, oy + 34), fill=COLORS[object_color], outline=(32, 37, 42), width=4)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def make_motion_video(path: Path, frames_dir: Path, *, frame_count: int = 48) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for video anchor acceptance")
    frames_dir.mkdir(parents=True, exist_ok=True)
    for index in range(frame_count):
        ratio = index / (frame_count - 1)
        x = round(90 + ratio * 255)
        y = round(210 - ratio * 75)
        make_layout_image(
            frames_dir / f"frame_{index:04d}.png",
            object_color="red",
            target_color="blue",
            object_center=(x, y),
            target_center=(360, 125),
        )
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-framerate",
            "12",
            "-i",
            str(frames_dir / "frame_%04d.png"),
            "-c:v",
            "mpeg4",
            "-q:v",
            "2",
            str(path),
        ],
        check=True,
    )
    return path


def position_map(package: EnvironmentPackage) -> dict[str, list[float]]:
    values = {obj.instance_id: list(obj.pose.position) for obj in package.env.objects}
    values.update(
        {
            str(region.get("id")): [float(value) for value in region.get("center", [0.0, 0.0, 0.0])]
            for region in package.env.regions
            if region.get("id")
        }
    )
    return values


def ablation(base: EnvironmentPackage, anchored: EnvironmentPackage) -> dict[str, Any]:
    text_positions = position_map(base)
    anchor_positions = position_map(anchored)
    comparisons = []
    for item_id in sorted(set(text_positions) & set(anchor_positions)):
        before = text_positions[item_id]
        after = anchor_positions[item_id]
        delta = math.dist(before, after)
        comparisons.append(
            {"id": item_id, "text_only_position_m": before, "anchor_position_m": after, "delta_m": delta}
        )
    return {
        "text_only_digest": base.digest(),
        "anchor_digest": anchored.digest(),
        "comparisons": comparisons,
        "changed_count": sum(item["delta_m"] > 1e-9 for item in comparisons),
        "max_delta_m": max((item["delta_m"] for item in comparisons), default=0.0),
    }


def mujoco_runtime_and_render(model_path: Path, preview_path: Path, *, steps: int = 20) -> dict[str, Any]:
    os.environ.setdefault("MUJOCO_GL", "cgl")
    import mujoco

    model = mujoco.MjModel.from_xml_path(str(model_path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    camera = mujoco.MjvCamera()
    mujoco.mjv_defaultCamera(camera)
    camera.lookat[:] = (0.0, -0.02, 0.74)
    camera.distance = 0.75
    camera.azimuth = 90.0
    camera.elevation = -70.0
    with mujoco.Renderer(model, height=480, width=640) as renderer:
        renderer.update_scene(data, camera=camera)
        pixels = renderer.render()
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels).save(preview_path)
    pixel_values = pixels.astype("float32")
    pixel_std = float(pixel_values.std())
    nonblack_fraction = float((pixel_values.max(axis=2) > 8.0).mean())
    initial_qpos = [float(value) for value in data.qpos]
    for _ in range(steps):
        mujoco.mj_step(model, data)
    final_qpos = [float(value) for value in data.qpos]
    finite = all(math.isfinite(value) for value in final_qpos)
    return {
        "status": "pass"
        if finite and float(data.time) > 0.0 and pixel_std > 3.0 and nonblack_fraction > 0.02
        else "fail",
        "mujoco_version": mujoco.__version__,
        "steps": steps,
        "final_time_s": float(data.time),
        "body_count": int(model.nbody),
        "geom_count": int(model.ngeom),
        "initial_qpos": initial_qpos,
        "final_qpos": final_qpos,
        "qpos_finite": finite,
        "preview_pixel_std": pixel_std,
        "preview_nonblack_fraction": nonblack_fraction,
        "preview": str(preview_path),
    }


def side_by_side(reference_path: Path, simulation_path: Path, output_path: Path) -> Path:
    with Image.open(reference_path) as reference, Image.open(simulation_path) as simulation:
        left = reference.convert("RGB")
        right = simulation.convert("RGB")
        height = 420
        left.thumbnail((600, height))
        right.thumbnail((600, height))
        canvas = Image.new("RGB", (left.width + right.width + 24, height + 44), color=(248, 249, 250))
        draw = ImageDraw.Draw(canvas)
        canvas.paste(left, (0, 44 + (height - left.height) // 2))
        canvas.paste(right, (left.width + 24, 44 + (height - right.height) // 2))
        draw.text((10, 12), "ANCHOR REFERENCE", fill=(28, 33, 38))
        draw.text((left.width + 34, 12), "MUJOCO COMPILED SCENE", fill=(28, 33, 38))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path)
    return output_path


def failure_gallery(video: Path, output: Path) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    try:
        extract_anchor(video, output / "two_sample", sample_count=2)
    except AnchorExtractionError as exc:
        failures.append({"case": "two_frame_sampling", "status": "expected_rejection", "error": str(exc)})

    corrupt = output / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    try:
        extract_anchor(corrupt, output / "corrupt_media")
    except AnchorExtractionError as exc:
        failures.append({"case": "corrupt_media", "status": "expected_rejection", "error": str(exc)})

    base = compile_text("Move the red block onto the blue zone.", repo_root=output)
    low = extract_anchor(
        output.parent / "media" / "scene_1.png",
        output / "low_confidence",
        annotations={
            "confidence": 0.2,
            "object_constraints": [
                {"instance_id": "red_block", "normalized_center": [0.1, 0.2], "confidence": 0.2}
            ],
        },
    )
    fused = fuse_anchor(base, low)
    failures.append(
        {
            "case": "low_confidence_projection",
            "status": "expected_ignore" if fused.env.objects[0].pose == base.env.objects[0].pose else "unexpected_apply",
            "threshold": 0.5,
            "confidence": 0.2,
        }
    )
    write_json(output / "failure_gallery.json", {"failures": failures})

    image = Image.new("RGB", (1080, 300), color=(244, 246, 248))
    draw = ImageDraw.Draw(image)
    for index, failure in enumerate(failures):
        x = 20 + index * 355
        draw.rectangle((x, 25, x + 330, 275), fill=(255, 255, 255), outline=(145, 44, 44), width=3)
        draw.text((x + 18, 48), str(failure["case"]), fill=(35, 39, 43))
        draw.text((x + 18, 88), str(failure["status"]), fill=(145, 44, 44))
        text = str(failure.get("error") or "constraint not applied")
        lines = [text[start : start + 42] for start in range(0, min(len(text), 168), 42)]
        for line_index, line in enumerate(lines):
            draw.text((x + 18, 130 + line_index * 24), line, fill=(70, 76, 82))
    image.save(output / "failure_gallery.png")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).expanduser().resolve()
    media_dir = output / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    scene_inputs = [
        {
            "id": "scene_1_red_blue_image",
            "instruction": "Move the red block onto the blue zone.",
            "media": make_layout_image(
                media_dir / "scene_1.png",
                object_color="red",
                target_color="blue",
                object_center=(105, 220),
                target_center=(360, 120),
            ),
        },
        {
            "id": "scene_2_green_yellow_image",
            "instruction": "Place the green block in the yellow zone.",
            "media": make_layout_image(
                media_dir / "scene_2.png",
                object_color="green",
                target_color="yellow",
                object_center=(350, 215),
                target_center=(125, 105),
            ),
        },
        {
            "id": "scene_3_red_blue_video",
            "instruction": "Move the red block to the blue zone.",
            "media": make_motion_video(
                media_dir / "scene_3.mp4", media_dir / "scene_3_frames"
            ),
        },
    ]

    scenes: list[dict[str, Any]] = []
    for item in scene_inputs:
        scene_root = output / "runs" / item["id"]
        pipeline = OpenXSimPipeline(scene_root)
        base = compile_text(
            item["instruction"],
            repo_root=Path(__file__).resolve().parents[1],
            target_backends=("mujoco", "sapien"),
        )
        package, results = pipeline.anchor2env(
            item["instruction"],
            item["media"],
            repo_root=Path(__file__).resolve().parents[1],
            backends=("mujoco", "sapien"),
            vision_provider=ColorLayoutAnchorProvider(),
            sample_count=8,
            strict=True,
        )
        run_dir = scene_root / package.package_id / "anchor2env"
        package_path = run_dir / "environment_package.json"
        replayed = EnvironmentPackage.read_json(package_path)
        second_anchor = extract_anchor(
            item["media"],
            run_dir / "anchor_evidence",
            instruction=item["instruction"],
            vision_provider=ColorLayoutAnchorProvider(),
            sample_count=8,
        )
        deterministic = fuse_anchor(base, second_anchor).digest() == package.digest()
        mujoco_result = results["mujoco"]
        runtime = mujoco_runtime_and_render(
            Path(mujoco_result.artifact_path), run_dir / "mujoco_runtime.png"
        )
        reference = Path(package.anchors[0].evidence[-1]["path"])
        comparison = side_by_side(
            reference, Path(runtime["preview"]), run_dir / "anchor_vs_mujoco.png"
        )
        ablation_report = ablation(base, package)
        write_json(run_dir / "text_vs_anchor_ablation.json", ablation_report)
        record = {
            "id": item["id"],
            "instruction": item["instruction"],
            "media_type": package.anchors[0].media_type,
            "media_path": str(item["media"]),
            "media_sha256": package.anchors[0].media_sha256,
            "sample_count": package.anchors[0].observations["sample_count"],
            "sample_sha256_unique_count": len(
                {evidence["sha256"] for evidence in package.anchors[0].evidence}
            ),
            "anchor_confidence": package.anchors[0].confidence,
            "uncertainty": list(package.anchors[0].uncertainty),
            "package_digest": package.digest(),
            "deterministic_recompile": deterministic,
            "resolved_replay_digest_match": replayed.digest() == package.digest(),
            "compile_results": {name: result.to_dict() for name, result in results.items()},
            "runtime": runtime,
            "ablation": ablation_report,
            "side_by_side": str(comparison),
        }
        record["status"] = (
            "pass"
            if deterministic
            and record["resolved_replay_digest_match"]
            and runtime["status"] == "pass"
            and ablation_report["changed_count"] >= 1
            and all(result.status == "compiled" for result in results.values())
            else "fail"
        )
        scenes.append(record)
        write_json(output / "anchor_acceptance.partial.json", {"scenes": scenes, "complete": False})
        print(f"{record['status'].upper()} {item['id']}", flush=True)

    failures = failure_gallery(scene_inputs[-1]["media"], output / "failure_gallery")
    image_count = sum(item["media_type"] == "image" for item in scenes)
    video_count = sum(item["media_type"] == "video" for item in scenes)
    pass_count = sum(item["status"] == "pass" for item in scenes)
    expected_failures = sum(item["status"].startswith("expected_") for item in failures)
    status = (
        "pass"
        if len(scenes) >= 3
        and pass_count == len(scenes)
        and image_count >= 1
        and video_count >= 1
        and expected_failures >= 3
        else "fail"
    )
    report = {
        "schema": "agenticsim.anchor2env_acceptance.v1",
        "status": status,
        "scene_count": len(scenes),
        "pass_count": pass_count,
        "image_scene_count": image_count,
        "video_scene_count": video_count,
        "expected_failure_count": expected_failures,
        "scenes": scenes,
        "failure_gallery": failures,
        "failure_gallery_image": str(output / "failure_gallery/failure_gallery.png"),
        "complete": True,
    }
    write_json(output / "anchor_acceptance.json", report)
    print(f"{status.upper()} pass={pass_count}/{len(scenes)} report={output / 'anchor_acceptance.json'}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
