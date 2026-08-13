#!/usr/bin/env python3
"""Explicit observation adapters used as versioned embodied-harness surfaces."""

from __future__ import annotations

import numpy as np


RUNTIME_COLOR_ADAPTERS = ("identity", "swap_red_blue")


def apply_runtime_color_adapter(rgb: np.ndarray, adapter: str) -> np.ndarray:
    array = np.asarray(rgb)
    if array.ndim != 3 or array.shape[-1] != 3:
        raise ValueError(f"Expected HxWx3 RGB input, got shape {array.shape}")
    if adapter == "identity":
        return array
    if adapter == "swap_red_blue":
        return array[..., [2, 1, 0]]
    raise ValueError(f"Unsupported runtime color adapter: {adapter}")


def color_adapter_reason(adapter: str) -> str:
    if adapter == "identity":
        return "Preserve RoboTwin runtime RGB because the native HDF5 converter repaired stored JPEG BGR ordering to RGB."
    if adapter == "swap_red_blue":
        return "Counterfactual harness baseline that swaps red and blue at runtime while holding policy and task inputs fixed."
    raise ValueError(f"Unsupported runtime color adapter: {adapter}")
