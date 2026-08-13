from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from scripts.rollout_video_recorder import RolloutVideoRecorder


class FakeWriter:
    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []
        self.closed = False

    def append_data(self, frame: np.ndarray) -> None:
        self.frames.append(frame)

    def close(self) -> None:
        self.closed = True


class FakeCameras:
    def __init__(self) -> None:
        self.updates = 0

    def update_picture(self) -> None:
        self.updates += 1

    def get_observer_rgb(self) -> np.ndarray:
        return np.full((8, 10, 3), self.updates, dtype=np.uint8)


class FakeTask:
    def __init__(self) -> None:
        self.save_freq = None
        self.render_updates = 0
        self.cameras = FakeCameras()
        self.original_picture_calls = 0

    def _update_render(self) -> None:
        self.render_updates += 1

    def _take_picture(self) -> None:
        self.original_picture_calls += 1


class RolloutVideoRecorderTest(unittest.TestCase):
    def test_installs_native_hook_streams_frames_and_restores_task(self) -> None:
        task = FakeTask()
        writer = FakeWriter()
        original_take_picture = task._take_picture
        recorder = RolloutVideoRecorder(
            task,
            output_path=Path("/tmp/fake-rollout-video.mp4"),
            fps=12,
            capture_stride=4,
            max_frames=4,
            writer_factory=lambda *_args, **_kwargs: writer,
        )

        recorder.append_frame(np.zeros((8, 10, 3), dtype=np.uint8))
        recorder.install()
        task._take_picture()
        task._take_picture()
        metadata = recorder.close()

        self.assertEqual(len(writer.frames), 3)
        self.assertTrue(writer.closed)
        self.assertEqual(task.save_freq, None)
        self.assertEqual(task._take_picture, original_take_picture)
        self.assertEqual(task.render_updates, 2)
        self.assertEqual(task.cameras.updates, 2)
        self.assertEqual(metadata["frame_count"], 3)
        self.assertEqual(metadata["capture_requests"], 2)
        self.assertFalse(metadata["endpoint_only"])

    def test_caps_frames_without_replacing_real_frames(self) -> None:
        task = FakeTask()
        writer = FakeWriter()
        recorder = RolloutVideoRecorder(
            task,
            output_path=Path("/tmp/fake-rollout-video.mp4"),
            fps=10,
            capture_stride=1,
            max_frames=2,
            writer_factory=lambda *_args, **_kwargs: writer,
        )

        recorder.append_frame(np.zeros((8, 10, 3), dtype=np.uint8))
        recorder.install()
        task._take_picture()
        task._take_picture()
        metadata = recorder.close()

        self.assertEqual(len(writer.frames), 2)
        self.assertEqual(metadata["frame_count"], 2)
        self.assertEqual(metadata["capture_requests"], 2)
        self.assertTrue(metadata["capped"])


if __name__ == "__main__":
    unittest.main()
