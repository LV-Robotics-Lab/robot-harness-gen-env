"""Shared runtime skills must not grow separate authorities or accept caller runners."""

import pytest

from self_improving.harness.limited_image_preview_application import LimitedImagePreviewApplication


def test_shared_application_rejects_unqualified_compiler_before_state_creation(tmp_path):
    root = tmp_path / "shared"
    with pytest.raises(TypeError, match="exact"):
        LimitedImagePreviewApplication(root, object(), object(), roots={})
    assert not root.exists()
