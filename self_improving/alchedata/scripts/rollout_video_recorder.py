#!/usr/bin/env python3
"""Continuous observer-video capture for RoboTwin rollout probes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np


class RolloutVideoRecorder:
    """Stream observer frames from RoboTwin's native save-frequency hooks."""

    def __init__(
        self,
        task: Any,
        output_path: Path,
        *,
        fps: int,
        capture_stride: int,
        max_frames: int,
        writer_factory: Callable[..., Any] | None = None,
    ) -> None:
        if writer_factory is None:
            from imageio.v2 import get_writer

            writer_factory = get_writer
        self.task = task
        self.output_path = output_path
        self.fps = fps
        self.capture_stride = capture_stride
        self.max_frames = max_frames
        self.writer_factory = writer_factory
        self.frame_count = 0
        self.capture_requests = 0
        self.capped = False
        self._writer: Any | None = None
        self._installed = False
        self._original_take_picture = task._take_picture
        self._original_save_freq = getattr(task, "save_freq", None)

    def append_frame(self, rgb: np.ndarray) -> bool:
        if self.frame_count >= self.max_frames:
            self.capped = True
            return False
        if self._writer is None:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self._writer = self.writer_factory(str(self.output_path), fps=self.fps)
        self._writer.append_data(np.asarray(rgb).astype("uint8"))
        self.frame_count += 1
        return True

    def capture(self) -> None:
        self.capture_requests += 1
        if self.frame_count >= self.max_frames:
            self.capped = True
            return
        self.task._update_render()
        self.task.cameras.update_picture()
        self.append_frame(self.task.cameras.get_observer_rgb())

    def install(self) -> None:
        if self._installed:
            return
        self.task._take_picture = self.capture
        self.task.save_freq = self.capture_stride
        self._installed = True

    def restore(self) -> None:
        if not self._installed:
            return
        self.task._take_picture = self._original_take_picture
        self.task.save_freq = self._original_save_freq
        self._installed = False

    def close(self) -> dict[str, Any]:
        self.restore()
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        return {
            "mode": "robotwin_native_save_frequency_hook",
            "endpoint_only": False,
            "frame_count": self.frame_count,
            "fps": self.fps,
            "duration_sec": round(self.frame_count / self.fps, 4),
            "capture_stride_sim_steps": self.capture_stride,
            "capture_requests": self.capture_requests,
            "max_frames": self.max_frames,
            "capped": self.capped,
        }
