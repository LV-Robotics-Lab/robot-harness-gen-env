"""Shared support-footprint geometry for solving and validation."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal

FootprintShape = Literal["box", "circle"]


@dataclass(frozen=True)
class Footprint2D:
    shape: FootprintShape
    half_x: float
    half_y: float
    radius: float


def footprint_2d(
    dimensions_m: tuple[float, float, float] | tuple[float, float],
    yaw: float,
    shape: FootprintShape = "box",
) -> Footprint2D:
    width, depth = dimensions_m[:2]
    if shape == "circle":
        radius = min(width, depth) / 2.0
        return Footprint2D(shape=shape, half_x=radius, half_y=radius, radius=radius)
    cosine = abs(math.cos(yaw))
    sine = abs(math.sin(yaw))
    half_x = 0.5 * (cosine * width + sine * depth)
    half_y = 0.5 * (sine * width + cosine * depth)
    return Footprint2D(
        shape=shape,
        half_x=half_x,
        half_y=half_y,
        radius=math.hypot(width / 2.0, depth / 2.0),
    )


def support_surface_dimensions(
    object_dimensions_m: tuple[float, float, float],
    explicit_dimensions_m: tuple[float, float] | None,
) -> tuple[float, float]:
    return explicit_dimensions_m or object_dimensions_m[:2]


def support_surface_shape(
    object_shape: FootprintShape,
    explicit_shape: FootprintShape | None,
) -> FootprintShape:
    return explicit_shape or object_shape


def support_surface_z(
    *,
    bottom_z: float,
    top_z: float,
    explicit_offset_m: float | None,
) -> float:
    return top_z if explicit_offset_m is None else bottom_z + explicit_offset_m


def _local_offset(dx: float, dy: float, target_yaw: float) -> tuple[float, float]:
    cosine = math.cos(target_yaw)
    sine = math.sin(target_yaw)
    return cosine * dx + sine * dy, -sine * dx + cosine * dy


def support_footprint_margin(
    *,
    source_dimensions_m: tuple[float, float, float],
    source_yaw: float,
    source_shape: FootprintShape,
    target_surface_dimensions_m: tuple[float, float],
    target_yaw: float,
    target_surface_shape: FootprintShape,
    dx: float,
    dy: float,
) -> float:
    """Return the minimum planar support margin; negative means overhang."""

    if target_surface_shape == "circle":
        target_radius = min(target_surface_dimensions_m) / 2.0
        source = footprint_2d(source_dimensions_m, source_yaw, source_shape)
        return target_radius - math.hypot(dx, dy) - source.radius

    local_x, local_y = _local_offset(dx, dy, target_yaw)
    source = footprint_2d(source_dimensions_m, source_yaw - target_yaw, source_shape)
    target_half_x = target_surface_dimensions_m[0] / 2.0
    target_half_y = target_surface_dimensions_m[1] / 2.0
    return min(
        target_half_x - abs(local_x) - source.half_x,
        target_half_y - abs(local_y) - source.half_y,
    )


def sample_supported_offset(
    *,
    rng: random.Random,
    source_dimensions_m: tuple[float, float, float],
    source_yaw: float,
    source_shape: FootprintShape,
    target_surface_dimensions_m: tuple[float, float],
    target_yaw: float,
    target_surface_shape: FootprintShape,
    required_margin_m: float,
) -> tuple[float, float] | None:
    """Sample an offset whose complete source footprint keeps the requested margin."""

    if target_surface_shape == "circle":
        target_radius = min(target_surface_dimensions_m) / 2.0
        source = footprint_2d(source_dimensions_m, source_yaw, source_shape)
        clearance = target_radius - source.radius - required_margin_m
        if clearance < 0.0:
            return None
        radius = math.sqrt(rng.random()) * clearance
        angle = rng.uniform(-math.pi, math.pi)
        return radius * math.cos(angle), radius * math.sin(angle)

    source = footprint_2d(source_dimensions_m, source_yaw - target_yaw, source_shape)
    x_clearance = target_surface_dimensions_m[0] / 2.0 - source.half_x - required_margin_m
    y_clearance = target_surface_dimensions_m[1] / 2.0 - source.half_y - required_margin_m
    if x_clearance < 0.0 or y_clearance < 0.0:
        return None
    local_x = rng.uniform(-x_clearance, x_clearance) if x_clearance else 0.0
    local_y = rng.uniform(-y_clearance, y_clearance) if y_clearance else 0.0
    cosine = math.cos(target_yaw)
    sine = math.sin(target_yaw)
    return cosine * local_x - sine * local_y, sine * local_x + cosine * local_y
