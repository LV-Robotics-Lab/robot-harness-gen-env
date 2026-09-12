"""CAS-owned experimental inputs; interpretation is advisory, never physical evidence."""

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .artifacts import LocalArtifactStore
from .image_ingest import ImageMediaIngestor
from .schemas.common import ArtifactRef
from .schemas.video_ingest import VideoSourceMediaV2
from .schemas.x2env import ImageSourceMedia
from .video_ingest import DecodedVideoFrame, DecodedVideoStream, VideoMediaIngestor


class FFmpegExperimentDecoder:
    """Bounded full H.264/CFR decode; development tool identity, no qualification claim."""

    def decode(self, source, payload):
        with TemporaryDirectory(prefix="experiment-video-") as temporary:
            path = Path(temporary) / "input.mp4"
            path.write_bytes(payload)
            probe = json.loads(
                subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-count_frames",
                        "-show_streams",
                        "-show_format",
                        "-of",
                        "json",
                        str(path),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                ).stdout
            )
            stream = probe["streams"][0]
            count = int(stream["nb_read_frames"])
            if count > 600 or int(stream["width"]) * int(stream["height"]) > 4_000_000:
                raise ValueError("video exceeds experimental decode limits")
            if stream["avg_frame_rate"] != stream["r_frame_rate"]:
                raise ValueError("variable frame rate is unsupported")
            decoded = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-fps_mode",
                    "passthrough",
                    "-f",
                    "framemd5",
                    "-hash",
                    "sha256",
                    "-",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            ).stdout.decode()
        frames = []
        timebase = None
        for line in decoded.splitlines():
            if line.startswith("#tb 0:"):
                timebase = Fraction(line.split(":", 1)[1].strip())
            elif line and not line.startswith("#"):
                values = [part.strip() for part in line.split(",")]
                frames.append(
                    DecodedVideoFrame(
                        index=len(frames),
                        dts=int(values[1]),
                        pts=int(values[2]),
                        duration=int(values[3]),
                        bytes=int(values[4]),
                        sha256=values[5],
                    )
                )
        if timebase is None:
            raise ValueError("decoder omitted stream timebase")
        fps = Fraction(stream["avg_frame_rate"])
        version = (
            subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True, timeout=10)
            .stdout.decode()
            .splitlines()[0]
        )
        return DecodedVideoStream(
            detected_format="MP4"
            if "mp4" in probe["format"]["format_name"].split(",")
            else "unknown",
            codec_name=stream["codec_name"],
            pixel_format=stream["pix_fmt"],
            width=int(stream["width"]),
            height=int(stream["height"]),
            duration_ms=(count * timebase.numerator * 1000 + timebase.denominator - 1)
            // timebase.denominator,
            fps_numerator=fps.numerator,
            fps_denominator=fps.denominator,
            time_base_numerator=timebase.numerator,
            time_base_denominator=timebase.denominator,
            probed_frame_count=count,
            frames=tuple(frames),
            decoder_name="ffmpeg",
            decoder_version=version,
            full_decode=True,
        )


class ExperimentInputBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["harness.experiment_input.v1"] = "harness.experiment_input.v1"
    route: Literal["text", "image", "video", "multimodal"]
    text: ArtifactRef | None = None
    images: tuple[ImageSourceMedia, ...] = ()
    video: VideoSourceMediaV2 | None = None


class SceneIntentV1(BaseModel):
    """Model proposal restricted to the implemented one-rigid-object experiment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    category: str = Field(min_length=1, max_length=80, pattern=r"^[\w -]+$")
    color: str | None
    source: Literal["local", "web", "generated"]
    selection: Literal["exact", "cousin"]
    dimensions_m: tuple[Annotated[float, Field(gt=0, le=0.4, allow_inf_nan=False)], ...] = Field(
        min_length=3, max_length=3
    )
    xy_m: tuple[Annotated[float, Field(ge=-0.4, le=0.4, allow_inf_nan=False)], ...] = Field(
        min_length=2, max_length=2
    )
    yaw_deg: float = Field(ge=-180, le=180, allow_inf_nan=False)
    support: Literal["table"]
    object_count: Literal[1]
    unknowns: tuple[str, ...] = Field(max_length=0)


class ExperimentInputs:
    def __init__(self, store: LocalArtifactStore):
        self.store = store

    def publish(self, value, *, name, schema=None, media="application/json"):
        raw = (
            value
            if isinstance(value, bytes)
            else json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
        )
        with TemporaryDirectory(prefix="experiment-input-") as temporary:
            path = Path(temporary) / "payload"
            path.write_bytes(raw)
            return self.store.put_file(path, name=name, media_type=media, schema_version=schema)

    def save(self, bundle: ExperimentInputBundle) -> ArtifactRef:
        return self.publish(
            bundle.model_dump(mode="json"),
            name="experiment-input.json",
            schema=bundle.schema_version,
        )

    def accept_intent(self, input_ref: ArtifactRef, proposal: dict) -> SceneIntentV1:
        if input_ref.schema_version != "harness.experiment_input.v1":
            raise ValueError("intent requires typed original input")
        ExperimentInputBundle.model_validate_json(self.store.resolve(input_ref).path.read_bytes())
        intent = SceneIntentV1.model_validate_json(json.dumps(proposal, allow_nan=False))
        if intent.input_sha256 != input_ref.sha256:
            raise ValueError("intent input identity differs")
        return intent

    def ingest(self, *, text=None, images=(), video=None):
        if text is not None and (not isinstance(text, str) or not text.strip()):
            raise ValueError("text must be nonempty when supplied")
        if not text and not images and video is None:
            raise ValueError("input must be nonempty")
        if len(images) > 8:
            raise ValueError("at most eight input images")
        prompt = (
            None
            if text is None
            else self.publish(text.encode(), name="user-prompt.txt", media="text/plain")
        )
        normalized = []
        for path in images:
            path = Path(path)
            if path.is_symlink() or not path.is_file():
                raise ValueError("media must be a regular file")
            with Image.open(path) as image:
                mime = Image.MIME.get(image.format)
            ref = self.store.put_file(path, name=path.name, media_type=mime, schema_version=None)
            normalized.append(ImageMediaIngestor(self.store, 20_000_000, 20_000_000).ingest(ref))
        video_media = None
        if video is not None:
            path = Path(video)
            if path.is_symlink() or not path.is_file():
                raise ValueError("media must be a regular file")
            if path.stat().st_size > 50_000_000:
                raise ValueError("video exceeds source byte limit")
            ref = self.store.put_file(
                path, name=path.name, media_type="video/mp4", schema_version=None
            )
            video_media = VideoMediaIngestor(
                self.store, FFmpegExperimentDecoder(), 50_000_000, 4_000_000, 600
            ).ingest(ref)
        count = bool(text) + bool(images) + (video is not None)
        route = (
            "multimodal"
            if count > 1
            else "video"
            if video is not None
            else "image"
            if images
            else "text"
        )
        return ExperimentInputBundle(
            route=route, text=prompt, images=tuple(normalized), video=video_media
        )
