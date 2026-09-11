"""Durable revised compilation uses the sealed compiler and existing run authority."""

import pytest

from self_improving.harness.revised_image_compile_application import RevisedImageCompileApplication


def test_revised_application_rejects_caller_compiler_before_creating_state(tmp_path):
    state=tmp_path/'state'
    with pytest.raises(TypeError,match='fixed revised compiler'):
        RevisedImageCompileApplication(state,object())
    assert not state.exists()
