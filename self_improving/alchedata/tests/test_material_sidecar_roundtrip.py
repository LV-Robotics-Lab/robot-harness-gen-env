from __future__ import annotations

import numpy as np

from scripts.run_isaac_material_sidecar_roundtrip import (
    compare_foregrounds,
    extract_sidecar,
    rgb_to_lab,
    srgb_to_linear,
)


def test_srgb_linear_endpoints() -> None:
    np.testing.assert_allclose(srgb_to_linear(np.asarray([0.0, 1.0, 0.04045])), [0.0, 1.0, 0.0031308], atol=1e-7)


def test_extract_sidecar_and_identical_comparison() -> None:
    image = np.full((32, 32, 3), 230, dtype=np.uint8)
    image[8:24, 8:24] = [220, 45, 55]
    sidecar, crop, mask = extract_sidecar(image, (6, 6, 26, 26))
    assert sidecar["status"] == "pass_observation_material_extraction"
    assert sidecar["foreground_pixel_count"] == 256
    metrics, target_mask = compare_foregrounds(crop, mask, np.tile(crop, (8, 8, 1)))
    assert np.count_nonzero(target_mask) > 100
    assert metrics["rgb_mean_absolute_error"] == 0.0
    assert metrics["cie76_delta_e"] == 0.0


def test_lab_reference_white() -> None:
    np.testing.assert_allclose(rgb_to_lab(np.asarray([1.0, 1.0, 1.0])), [100.0, 0.0, 0.0], atol=2e-5)
