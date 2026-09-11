"""Restricted preview evidence uses the existing durable child journal."""

import pytest

from self_improving.harness.limited_image_preview_policy import LimitedImagePreviewPolicy


def test_policy_rejects_callers_without_exact_shared_application():
    with pytest.raises(TypeError, match="exact"):
        LimitedImagePreviewPolicy(object(), "image2env.validate@3.0.0", {})


def test_policy_rejects_unknown_skill_before_creating_any_authority():
    with pytest.raises(ValueError, match="exact"):
        LimitedImagePreviewPolicy(object(), "skill.invoke@1.0.0", {})
