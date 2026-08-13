"""Image/video evidence extraction and AnchorSpec fusion."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .ir import AnchorSpec, EnvironmentPackage, EnvSpec, Pose, SceneObject


class AnchorExtractionError(RuntimeError):
    """Raised when media cannot be decoded into stable anchor evidence."""


class VisionAnchorProvider(Protocol):
    """Pluggable semantic extractor for sampled image evidence."""

    def __call__(self, sample_paths: tuple[Path, ...], instruction: str) -> Mapping[str, Any]: ...


class ColorLayoutAnchorProvider:
    """Offline extractor for strongly coloured tabletop markers and objects.

    This is a deterministic fallback, not a general object detector. It is
    useful for task fixtures, coloured blocks/zones, and validating the full
    image/video anchor path without an external VLM service.
    """

    name = "color_layout_v1"
    _COLORS = ("red", "green", "blue", "yellow")

    def __init__(self, *, minimum_fraction: float = 0.002):
        if not 0.0 < minimum_fraction < 1.0:
            raise ValueError("minimum_fraction must be between zero and one")
        self.minimum_fraction = minimum_fraction

    @staticmethod
    def _matches(color: str, red: int, green: int, blue: int) -> bool:
        if color == "red":
            return red >= 120 and red >= green * 1.35 and red >= blue * 1.35
        if color == "green":
            return green >= 100 and green >= red * 1.25 and green >= blue * 1.25
        if color == "blue":
            return blue >= 110 and blue >= red * 1.3 and blue >= green * 1.3
        return red >= 140 and green >= 110 and blue <= min(red, green) * 0.7

    @staticmethod
    def _instance_id(color: str, instruction: str) -> str:
        text = instruction.lower()
        for noun in ("block", "zone", "bowl", "cup", "can", "bottle"):
            if f"{color} {noun}" in text:
                return f"{color}_{noun}"
        return f"{color}_object"

    def __call__(self, sample_paths: tuple[Path, ...], instruction: str) -> Mapping[str, Any]:
        try:
            from PIL import Image
        except ImportError as exc:
            raise AnchorExtractionError("ColorLayoutAnchorProvider requires Pillow") from exc

        tracks: dict[str, list[dict[str, Any]]] = {color: [] for color in self._COLORS}
        for frame_index, path in enumerate(sample_paths):
            with Image.open(path) as image:
                rgb = image.convert("RGB")
                width, height = rgb.size
                pixels = rgb.load()
                total = width * height
                for color in self._COLORS:
                    xs: list[int] = []
                    ys: list[int] = []
                    for y in range(height):
                        for x in range(width):
                            if self._matches(color, *pixels[x, y]):
                                xs.append(x)
                                ys.append(y)
                    fraction = len(xs) / total
                    if fraction < self.minimum_fraction:
                        continue
                    tracks[color].append(
                        {
                            "frame_index": frame_index,
                            "center": [sum(xs) / len(xs) / width, sum(ys) / len(ys) / height],
                            "bbox": [min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height],
                            "pixel_fraction": fraction,
                        }
                    )

        object_constraints: list[dict[str, Any]] = []
        appearance_constraints: list[dict[str, Any]] = []
        motion_constraints: list[dict[str, Any]] = []
        first_centres: list[tuple[str, list[float]]] = []
        for color, observations in tracks.items():
            if not observations:
                continue
            instance_id = self._instance_id(color, instruction)
            latest = observations[-1]
            confidence = min(0.95, 0.55 + 0.4 * len(observations) / max(len(sample_paths), 1))
            object_constraints.append(
                {
                    "instance_id": instance_id,
                    "label": f"{color} region or object",
                    "normalized_center": latest["center"],
                    "normalized_bbox": latest["bbox"],
                    "frames_observed": len(observations),
                    "confidence": confidence,
                    "metric_depth_status": "unknown",
                }
            )
            appearance_constraints.append(
                {"instance_id": instance_id, "dominant_color": color, "confidence": confidence}
            )
            first_centres.append((instance_id, observations[0]["center"]))
            if len(observations) >= 2:
                start = observations[0]["center"]
                end = observations[-1]["center"]
                displacement = [end[0] - start[0], end[1] - start[1]]
                if abs(displacement[0]) + abs(displacement[1]) >= 0.02:
                    motion_constraints.append(
                        {
                            "instance_id": instance_id,
                            "normalized_displacement": displacement,
                            "start_frame": observations[0]["frame_index"],
                            "end_frame": observations[-1]["frame_index"],
                            "confidence": confidence,
                        }
                    )

        spatial_constraints: list[dict[str, Any]] = []
        for index, (left_id, left_center) in enumerate(first_centres):
            for right_id, right_center in first_centres[index + 1 :]:
                if abs(left_center[0] - right_center[0]) >= 0.05:
                    subject, target = (left_id, right_id) if left_center[0] < right_center[0] else (right_id, left_id)
                    spatial_constraints.append(
                        {"type": "left_of", "subject": subject, "object": target, "confidence": 0.7}
                    )

        observed_frames = sum(len(value) for value in tracks.values())
        confidence = min(0.9, 0.45 + 0.45 * observed_frames / max(len(sample_paths) * 2, 1))
        return {
            "confidence": confidence,
            "object_constraints": object_constraints,
            "spatial_constraints": spatial_constraints,
            "appearance_constraints": appearance_constraints,
            "motion_constraints": motion_constraints,
            "uncertainty": [
                "color_segmentation_is_not_category_recognition",
                "metric_depth_unknown",
                "occlusion_not_resolved",
            ],
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_json(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise AnchorExtractionError(f"media probe failed: {exc}") from exc


def _parse_rate(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    numerator, _, denominator = value.partition("/")
    try:
        return float(numerator) / float(denominator or 1.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _image_evidence(path: Path, evidence_dir: Path) -> tuple[dict[str, Any], tuple[Path, ...]]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            image_format = str(image.format or path.suffix.lstrip(".")).lower()
    except (ImportError, OSError) as exc:
        raise AnchorExtractionError(f"image decode failed for {path}: {exc}") from exc

    copied = evidence_dir / f"source{path.suffix.lower() or '.img'}"
    shutil.copy2(path, copied)
    return (
        {
            "width": int(width),
            "height": int(height),
            "format": image_format,
            "sample_count": 1,
        },
        (copied,),
    )


def _video_evidence(
    path: Path,
    evidence_dir: Path,
    *,
    sample_count: int,
    ffprobe: str,
    ffmpeg: str,
) -> tuple[dict[str, Any], tuple[Path, ...]]:
    if sample_count < 3:
        raise AnchorExtractionError("video sample_count must be at least 3")
    probe = _run_json(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,nb_frames,duration:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    streams = probe.get("streams") or []
    if not streams:
        raise AnchorExtractionError(f"video has no decodable video stream: {path}")
    stream = streams[0]
    duration = float(stream.get("duration") or (probe.get("format") or {}).get("duration") or 0.0)
    fps = _parse_rate(stream.get("avg_frame_rate"))
    frame_count = int(stream.get("nb_frames") or round(duration * fps) or 0)
    if duration <= 0.0:
        raise AnchorExtractionError(f"video duration is unavailable: {path}")

    frame_interval = 1.0 / fps if fps > 0.0 else 0.04
    last_decodable_time = max(duration - frame_interval, 0.0)
    sample_times = [last_decodable_time * index / (sample_count - 1) for index in range(sample_count)]
    sample_paths: list[Path] = []
    for index, seconds in enumerate(sample_times):
        output = evidence_dir / f"frame_{index:03d}.png"
        command = [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-ss",
            f"{seconds:.6f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            str(output),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise AnchorExtractionError(f"video frame extraction failed at {seconds:.3f}s: {exc}") from exc
        if not output.is_file() or output.stat().st_size == 0:
            raise AnchorExtractionError(f"video frame extraction produced no file at {seconds:.3f}s")
        sample_paths.append(output)

    return (
        {
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
            "duration_s": duration,
            "fps": fps,
            "frame_count": frame_count,
            "sample_count": len(sample_paths),
            "sample_times_s": sample_times,
        },
        tuple(sample_paths),
    )


def extract_anchor(
    media_path: str | Path,
    output_dir: str | Path,
    *,
    instruction: str = "",
    annotations: Mapping[str, Any] | None = None,
    vision_provider: VisionAnchorProvider | None = None,
    sample_count: int = 8,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
) -> AnchorSpec:
    """Decode media, sample evidence, and optionally obtain semantic constraints."""

    path = Path(media_path).expanduser().resolve()
    if not path.is_file():
        raise AnchorExtractionError(f"media file does not exist: {path}")
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    media_sha = _sha256(path)
    evidence_dir = output / f"anchor_{media_sha[:12]}"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    mime = mimetypes.guess_type(path.name)[0] or ""
    media_type = "video" if mime.startswith("video/") else "image" if mime.startswith("image/") else ""
    if not media_type:
        if path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
            media_type = "video"
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
            media_type = "image"
    if media_type == "image":
        observations, samples = _image_evidence(path, evidence_dir)
    elif media_type == "video":
        observations, samples = _video_evidence(
            path,
            evidence_dir,
            sample_count=sample_count,
            ffprobe=ffprobe,
            ffmpeg=ffmpeg,
        )
    else:
        raise AnchorExtractionError(f"unsupported media type: {path.suffix or path.name}")

    semantic: dict[str, Any] = dict(annotations or {})
    provider_name = "user_annotations" if annotations else "media_only"
    if vision_provider is not None:
        provider_output = dict(vision_provider(samples, instruction))
        semantic.update(provider_output)
        provider_name = getattr(vision_provider, "name", vision_provider.__class__.__name__)

    evidence = tuple(
        {
            "path": str(sample),
            "sha256": _sha256(sample),
            "size_bytes": sample.stat().st_size,
        }
        for sample in samples
    )
    confidence = float(semantic.get("confidence", 0.35 if semantic else 0.15))
    uncertainty = list(semantic.get("uncertainty") or [])
    if media_type == "image":
        uncertainty.extend(["occluded_geometry_unknown", "absolute_scale_unknown", "depth_ambiguous"])
    else:
        uncertainty.extend(["absolute_scale_unknown", "unobserved_geometry_unknown"])
    if not semantic.get("object_constraints"):
        uncertainty.append("semantic_objects_not_extracted")

    anchor = AnchorSpec(
        anchor_id=f"anchor_{media_sha[:12]}",
        media_type=media_type,
        media_uri=str(path),
        media_sha256=media_sha,
        confidence=max(0.0, min(1.0, confidence)),
        observations={**observations, "semantic_provider": provider_name},
        object_constraints=tuple(dict(item) for item in semantic.get("object_constraints", [])),
        spatial_constraints=tuple(dict(item) for item in semantic.get("spatial_constraints", [])),
        camera_constraints=tuple(dict(item) for item in semantic.get("camera_constraints", [])),
        appearance_constraints=tuple(dict(item) for item in semantic.get("appearance_constraints", [])),
        motion_constraints=tuple(dict(item) for item in semantic.get("motion_constraints", [])),
        evidence=evidence,
        uncertainty=tuple(dict.fromkeys(str(value) for value in uncertainty)),
    )
    anchor.validate()
    (evidence_dir / "anchor.json").write_text(
        json.dumps(anchor.__dict__, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return anchor


def _project_normalized_center(
    value: Any,
    *,
    workspace_bounds_m: tuple[float, float, float, float, float, float],
    z_m: float,
) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        normalized_x, normalized_y = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= normalized_x <= 1.0 or not 0.0 <= normalized_y <= 1.0:
        return None
    x_min, y_min, _, x_max, y_max, _ = workspace_bounds_m
    return (
        x_min + normalized_x * (x_max - x_min),
        y_max - normalized_y * (y_max - y_min),
        z_m,
    )


def fuse_anchor(
    package: EnvironmentPackage,
    anchor: AnchorSpec,
    *,
    minimum_projection_confidence: float = 0.5,
) -> EnvironmentPackage:
    """Fuse explicit anchor positions/cameras while preserving uncertainty metadata."""

    anchor.validate()
    if not 0.0 <= minimum_projection_confidence <= 1.0:
        raise ValueError("minimum_projection_confidence must be in [0, 1]")
    object_updates: dict[str, dict[str, Any]] = {}
    for constraint in anchor.object_constraints:
        instance_id = str(constraint.get("instance_id") or "")
        if instance_id:
            object_updates[instance_id] = constraint

    objects: list[SceneObject] = []
    applied_objects: list[str] = []
    ignored_constraints: list[dict[str, Any]] = []
    for obj in package.env.objects:
        constraint = object_updates.get(obj.instance_id)
        position: tuple[float, float, float] | None = None
        source = ""
        confidence = float((constraint or {}).get("confidence", anchor.confidence))
        if constraint and "position_m" in constraint:
            raw_position = constraint["position_m"]
            if isinstance(raw_position, (list, tuple)) and len(raw_position) == 3:
                position = tuple(float(value) for value in raw_position)
                source = "explicit_position_m"
        elif constraint and confidence >= minimum_projection_confidence:
            position = _project_normalized_center(
                constraint.get("normalized_center"),
                workspace_bounds_m=package.env.workspace_bounds_m,
                z_m=obj.pose.position[2],
            )
            source = "normalized_center_workspace_projection" if position else ""
        elif constraint:
            ignored_constraints.append(
                {
                    "instance_id": obj.instance_id,
                    "reason": "below_projection_confidence",
                    "confidence": confidence,
                    "threshold": minimum_projection_confidence,
                }
            )
        if position is not None:
            pose = Pose(position=position, orientation_wxyz=obj.pose.orientation_wxyz)
            metadata = {
                **obj.metadata,
                "anchor_override": {
                    "anchor_id": anchor.anchor_id,
                    "confidence": confidence,
                    "source": source,
                    "projection_assumption": "top_down_linear_workspace_mapping" if source.startswith("normalized") else None,
                },
            }
            objects.append(replace(obj, pose=pose, metadata=metadata))
            applied_objects.append(obj.instance_id)
        else:
            objects.append(obj)

    regions: list[dict[str, Any]] = []
    applied_regions: list[str] = []
    for raw_region in package.env.regions:
        region = dict(raw_region)
        region_id = str(region.get("id") or "")
        constraint = object_updates.get(region_id)
        confidence = float((constraint or {}).get("confidence", anchor.confidence))
        center = list(region.get("center") or [0.0, 0.0, package.env.workspace_bounds_m[2]])
        projected = None
        source = ""
        if constraint and "position_m" in constraint:
            raw_position = constraint["position_m"]
            if isinstance(raw_position, (list, tuple)) and len(raw_position) == 3:
                projected = tuple(float(value) for value in raw_position)
                source = "explicit_position_m"
        elif constraint and confidence >= minimum_projection_confidence:
            projected = _project_normalized_center(
                constraint.get("normalized_center"),
                workspace_bounds_m=package.env.workspace_bounds_m,
                z_m=float(center[2]),
            )
            source = "normalized_center_workspace_projection" if projected else ""
        elif constraint:
            ignored_constraints.append(
                {
                    "instance_id": region_id,
                    "reason": "below_projection_confidence",
                    "confidence": confidence,
                    "threshold": minimum_projection_confidence,
                }
            )
        if projected is not None:
            region["center"] = list(projected)
            region["anchor_override"] = {
                "anchor_id": anchor.anchor_id,
                "confidence": confidence,
                "source": source,
                "projection_assumption": "top_down_linear_workspace_mapping" if source.startswith("normalized") else None,
            }
            applied_regions.append(region_id)
        regions.append(region)

    sensors = list(package.env.sensors)
    for constraint in anchor.camera_constraints:
        sensor_id = str(constraint.get("sensor_id") or "anchor_camera")
        sensors.append({"id": sensor_id, "type": "anchor_camera", **constraint})
    env = replace(
        package.env,
        objects=tuple(objects),
        sensors=tuple(sensors),
        regions=tuple(regions),
        metadata={
            **package.env.metadata,
            "anchor_fusion": {
                "anchor_id": anchor.anchor_id,
                "applied_object_positions": sorted(applied_objects),
                "applied_region_positions": sorted(applied_regions),
                "ignored_constraints": ignored_constraints,
                "projection": {
                    "type": "top_down_linear_workspace_mapping",
                    "minimum_confidence": minimum_projection_confidence,
                    "metric_depth": "preserve_text_compiler_z",
                },
                "uncertainty": list(anchor.uncertainty),
            },
        },
    )
    fused = replace(
        package,
        env=env,
        anchors=package.anchors + (anchor,),
        source={**package.source, "mode": "anchor_to_env", "anchor_count": len(package.anchors) + 1},
    )
    fused.validate()
    return fused
