#!/usr/bin/env python3
"""Read exact video stream evidence with ffprobe."""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any


def probe_video(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames,nb_frames,avg_frame_rate,duration,width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if len(streams) != 1:
        raise ValueError(f"Expected one video stream in {path}, found {len(streams)}")
    stream = streams[0]
    frame_count_raw = stream.get("nb_read_frames") or stream.get("nb_frames")
    if frame_count_raw in (None, "N/A"):
        raise ValueError(f"ffprobe did not return a frame count for {path}")
    fps_raw = stream.get("avg_frame_rate", "0/1")
    fps = float(Fraction(fps_raw)) if fps_raw != "0/0" else 0.0
    duration = float(stream.get("duration") or 0.0)
    return {
        "frame_count": int(frame_count_raw),
        "fps": fps,
        "duration_sec": duration,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
    }
