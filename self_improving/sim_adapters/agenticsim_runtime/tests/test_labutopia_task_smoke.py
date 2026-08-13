from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    module_path = ROOT / "scripts" / "smoke_labutopia_task.py"
    spec = importlib.util.spec_from_file_location("smoke_labutopia_task", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_to_rgb_uint8_converts_channel_first_float_image() -> None:
    module = load_module()
    source = np.zeros((3, 2, 4), dtype=np.float32)
    source[0, :, :] = 1.0

    image = module._to_rgb_uint8(source)

    assert image.shape == (2, 4, 3)
    assert image.dtype == np.uint8
    assert np.all(image[..., 0] == 255)
    assert np.all(image[..., 1:] == 0)


def test_to_rgb_uint8_drops_alpha_channel() -> None:
    module = load_module()
    source = np.full((2, 3, 4), 17, dtype=np.uint8)

    image = module._to_rgb_uint8(source)

    assert image.shape == (2, 3, 3)
    assert np.all(image == 17)


def test_to_rgb_uint8_rejects_non_image_array() -> None:
    module = load_module()

    with pytest.raises(ValueError, match="3D camera image"):
        module._to_rgb_uint8(np.zeros((2, 3), dtype=np.uint8))
