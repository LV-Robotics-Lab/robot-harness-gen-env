"""Input construction uses real CAS/image/geometry; no simulator or model calls."""

from self_improving.harness.limited_image_preview_inputs import LimitedImagePreviewInputs


def test_public_input_builder_has_explicit_stages():
    assert all(callable(getattr(LimitedImagePreviewInputs, name)) for name in (
        "start", "lookup", "build_initial",
    ))
