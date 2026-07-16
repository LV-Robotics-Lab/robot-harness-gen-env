from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from agenticsim.openxsim.anchors import (
    AnchorExtractionError,
    ColorLayoutAnchorProvider,
    extract_anchor,
    fuse_anchor,
)
from agenticsim.openxsim.pipeline import OpenXSimPipeline
from agenticsim.openxsim.text2env import compile_text


def make_image(path: Path) -> Path:
    image = Image.new("RGB", (96, 64), color=(30, 120, 210))
    image.save(path)
    return path


def make_video(path: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=96x64:rate=12",
            "-c:v",
            "mpeg4",
            "-q:v",
            "3",
            str(path),
        ],
        check=True,
    )
    return path


def test_image_anchor_decodes_media_and_fuses_explicit_position(tmp_path: Path) -> None:
    image = make_image(tmp_path / "anchor.png")
    anchor = extract_anchor(
        image,
        tmp_path / "evidence",
        annotations={
            "confidence": 0.82,
            "object_constraints": [
                {"instance_id": "red_block", "position_m": [0.1, -0.05, 0.8], "confidence": 0.75}
            ],
            "camera_constraints": [{"sensor_id": "reference_camera", "fov_deg": 60}],
        },
    )
    package = compile_text("Move the red block onto the blue zone.", repo_root=tmp_path)
    fused = fuse_anchor(package, anchor)

    assert anchor.media_type == "image"
    assert anchor.observations["width"] == 96
    assert anchor.observations["height"] == 64
    assert len(anchor.evidence) == 1
    assert len(anchor.media_sha256) == 64
    assert "absolute_scale_unknown" in anchor.uncertainty
    assert fused.anchors == (anchor,)
    assert fused.env.objects[0].pose.position == (0.1, -0.05, 0.8)
    assert any(sensor["id"] == "reference_camera" for sensor in fused.env.sensors)


def test_video_anchor_samples_full_timeline_not_only_endpoints(tmp_path: Path) -> None:
    video = make_video(tmp_path / "anchor.mp4")
    anchor = extract_anchor(video, tmp_path / "evidence", sample_count=8)

    assert anchor.media_type == "video"
    assert anchor.observations["sample_count"] == 8
    assert len(anchor.evidence) == 8
    assert anchor.observations["sample_times_s"][0] == 0.0
    assert anchor.observations["sample_times_s"][-1] > 1.5
    assert len(set(item["sha256"] for item in anchor.evidence)) >= 6
    assert all(Path(item["path"]).is_file() for item in anchor.evidence)


def test_video_anchor_rejects_two_frame_sampling(tmp_path: Path) -> None:
    video = make_video(tmp_path / "anchor.mp4")

    with pytest.raises(AnchorExtractionError, match="at least 3"):
        extract_anchor(video, tmp_path / "evidence", sample_count=2)


def test_pluggable_vision_provider_contributes_semantics(tmp_path: Path) -> None:
    image = make_image(tmp_path / "anchor.png")

    class Provider:
        name = "test_vision_provider"

        def __call__(self, sample_paths, instruction):
            assert len(sample_paths) == 1
            assert "red block" in instruction
            return {
                "confidence": 0.91,
                "object_constraints": [{"instance_id": "red_block", "label": "block"}],
                "spatial_constraints": [{"type": "left_of", "subject": "red_block", "object": "blue_zone"}],
            }

    anchor = extract_anchor(
        image,
        tmp_path / "evidence",
        instruction="Move the red block onto the blue zone.",
        vision_provider=Provider(),
    )

    assert anchor.confidence == 0.91
    assert anchor.observations["semantic_provider"] == "test_vision_provider"
    assert anchor.spatial_constraints[0]["type"] == "left_of"


def test_offline_color_layout_provider_extracts_objects_and_relation(tmp_path: Path) -> None:
    path = tmp_path / "layout.png"
    image = Image.new("RGB", (120, 80), color=(245, 245, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 20, 35, 48), fill=(230, 20, 20))
    draw.rectangle((78, 15, 112, 55), fill=(20, 55, 230))
    image.save(path)

    anchor = extract_anchor(
        path,
        tmp_path / "evidence",
        instruction="Move the red block onto the blue zone.",
        vision_provider=ColorLayoutAnchorProvider(),
    )

    constraints = {item["instance_id"]: item for item in anchor.object_constraints}
    assert set(constraints) == {"red_block", "blue_zone"}
    assert constraints["red_block"]["normalized_center"][0] < constraints["blue_zone"]["normalized_center"][0]
    assert anchor.spatial_constraints == (
        {"type": "left_of", "subject": "red_block", "object": "blue_zone", "confidence": 0.7},
    )
    assert anchor.observations["semantic_provider"] == "color_layout_v1"


def test_normalized_anchor_positions_project_into_objects_and_regions(tmp_path: Path) -> None:
    path = tmp_path / "layout.png"
    image = Image.new("RGB", (120, 80), color=(245, 245, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 20, 35, 48), fill=(230, 20, 20))
    draw.rectangle((78, 15, 112, 55), fill=(20, 55, 230))
    image.save(path)
    base = compile_text("Move the red block onto the blue zone.", repo_root=tmp_path)
    anchor = extract_anchor(
        path,
        tmp_path / "evidence",
        instruction=base.task.instruction,
        vision_provider=ColorLayoutAnchorProvider(),
    )
    fused = fuse_anchor(base, anchor)

    assert fused.env.objects[0].pose.position != base.env.objects[0].pose.position
    assert fused.env.regions[0]["center"] != base.env.regions[0]["center"]
    fusion = fused.env.metadata["anchor_fusion"]
    assert fusion["applied_object_positions"] == ["red_block"]
    assert fusion["applied_region_positions"] == ["blue_zone"]
    assert fusion["projection"]["metric_depth"] == "preserve_text_compiler_z"


def test_low_confidence_normalized_projection_is_not_applied(tmp_path: Path) -> None:
    image = make_image(tmp_path / "anchor.png")
    base = compile_text("Move the red block onto the blue zone.", repo_root=tmp_path)
    anchor = extract_anchor(
        image,
        tmp_path / "evidence",
        annotations={
            "confidence": 0.2,
            "object_constraints": [
                {"instance_id": "red_block", "normalized_center": [0.1, 0.2], "confidence": 0.2}
            ],
        },
    )
    fused = fuse_anchor(base, anchor)
    assert fused.env.objects[0].pose == base.env.objects[0].pose
    ignored = fused.env.metadata["anchor_fusion"]["ignored_constraints"]
    assert ignored[0]["reason"] == "below_projection_confidence"


def test_anchor2env_pipeline_writes_package_evidence_and_backend(tmp_path: Path) -> None:
    image = make_image(tmp_path / "anchor.png")
    pipeline = OpenXSimPipeline(tmp_path / "runs")
    package, results = pipeline.anchor2env(
        "Move the red block onto the blue zone.",
        image,
        repo_root=tmp_path,
        backends=("mujoco",),
        annotations={"confidence": 0.7},
        strict=True,
    )

    run_dir = tmp_path / "runs" / package.package_id / "anchor2env"
    manifest = json.loads((run_dir / "workflow_manifest.json").read_text())
    assert len(package.anchors) == 1
    assert results["mujoco"].status == "compiled"
    assert manifest["workflow"] == "anchor2env"
    assert manifest["anchors"] == [package.anchors[0].anchor_id]
