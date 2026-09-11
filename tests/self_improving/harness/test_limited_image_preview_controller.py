"""Controller recovery exercises the real shared SQLite/CAS with external boundaries."""

from self_improving.harness.limited_image_preview_controller import LimitedImagePreviewController


def test_controller_exposes_harness_start_and_resume():
    assert callable(LimitedImagePreviewController.start)
    assert callable(LimitedImagePreviewController.resume)
