"""Dependency-free helpers for runtime evidence sampling."""

from __future__ import annotations


def video_sample_steps(total_steps: int, requested_frames: int) -> tuple[int, ...]:
    if total_steps <= 0 or requested_frames <= 0:
        return ()
    frame_count = min(total_steps, requested_frames)
    if frame_count == 1:
        return (total_steps - 1,)
    if frame_count == total_steps:
        return tuple(range(total_steps))
    return (*range(frame_count - 1), total_steps - 1)
