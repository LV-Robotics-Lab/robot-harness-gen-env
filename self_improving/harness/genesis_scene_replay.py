"""Gujie-derived finite-table dual replay mathematics and CAS-only evidence reading.

Criterion semantics derive from Gujie eb0b710 physics_criteria, validate_single_asset,
validate_imported_scene and scene_physics_workflow. Runtime authority is separate.
"""

import hashlib
import json
import math
import subprocess

from PIL import Image

from .schemas.genesis_scene_replay import (
    SCENE_REPLAY_ID,
    SCENE_REPORT_ID,
    SCENE_ROW_ID,
    GenesisSceneCheckV1,
    GenesisSceneMediaSummaryV1,
    GenesisSceneReplayV1,
    GenesisSceneReportV1,
    GenesisSceneSpecV1,
    GenesisSceneTraceRowV1,
)


def norm(values):
    return math.sqrt(sum(value * value for value in values))


def distance(left, right):
    return norm(a - b for a, b in zip(left, right))


def angle(left, right):
    cosine = abs(sum(a * b for a, b in zip(left, right))) / (norm(left) * norm(right))
    return math.degrees(2 * math.acos(min(1.0, cosine)))


def world_vertices(scene, state):
    w, x, y, z = state.orientation_wxyz
    matrix = (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )
    return tuple(
        tuple(
            sum(a * b for a, b in zip(row, point)) + offset
            for row, offset in zip(matrix, state.position)
        )
        for point in scene.local_vertices_m
    )


def profile_settings(profile):
    if profile not in ("baseline", "half_dt"):
        raise ValueError("unknown scene replay profile")
    return (0.004, 1000, 25) if profile == "baseline" else (0.002, 2000, 50)


def _profile(scene, raw_rows, profile):
    dt, steps, _ = profile_settings(profile)
    rows = tuple(GenesisSceneTraceRowV1.model_validate(row) for row in raw_rows)
    if len(rows) != steps + 1:
        raise ValueError("incomplete scene trajectory")
    for i, row in enumerate(rows):
        if row.step != i or abs(row.time_s - i * dt) > 1e-9:
            raise ValueError("nonsequential scene trajectory")
        for name, position in (("table", scene.table_center_m), ("ground", (0.0, 0.0, 0.0))):
            state = row.objects[name]
            if (
                distance(state.position, position) > 1e-7
                or angle(state.orientation_wxyz, (1.0, 0.0, 0.0, 0.0)) > 1e-5
                or norm(state.velocity) > 1e-7
                or norm(state.angular_velocity) > 1e-7
            ):
                raise ValueError("fixed scene root moved")
    initial = rows[0].objects["object"]
    if (
        distance(initial.position, scene.position_m) > 1e-6
        or angle(initial.orientation_wxyz, scene.orientation_wxyz) > 1e-4
        or norm(initial.velocity) > 1e-6
        or norm(initial.angular_velocity) > 1e-6
    ):
        raise ValueError("initial state differs from authored scene")
    window = rows[-round(0.5 / dt) :]
    states = [row.objects["object"] for row in window]
    positions = [state.position for state in states]
    center = tuple(sum(p[i] for p in positions) / len(positions) for i in range(3))
    radius = max(norm(vertex) for vertex in scene.local_vertices_m)
    effective = [
        max(norm(state.velocity), radius * norm(state.angular_velocity)) for state in states
    ]
    speed_limit = round(1.5 * 9.81 * dt, 6)
    longest = current = 0
    for value in effective:
        current = current + 1 if value >= speed_limit else 0
        longest = max(longest, current)
    touching = supported = 0
    for row in window:
        contacts = [c for c in row.contacts if "object" in (c.a, c.b)]
        touching += bool(contacts)
        upward = sum(
            (c.force_a if c.a == "object" else c.force_b)[2]
            for c in contacts
            if "table" in (c.a, c.b)
        )
        supported += upward > 1e-6
    penetration = max((c.penetration for row in rows for c in row.contacts), default=0.0)
    footprint_margin = math.inf
    minimum_z = math.inf
    unexpected_contacts = 0
    for row in rows:
        for point in world_vertices(scene, row.objects["object"]):
            footprint_margin = min(
                footprint_margin,
                *(
                    scene.table_size_m[i] / 2 - abs(point[i] - scene.table_center_m[i])
                    for i in (0, 1)
                ),
            )
            minimum_z = min(minimum_z, point[2])
        unexpected_contacts += sum({c.a, c.b} == {"object", "ground"} for c in row.contacts)
    measures = {
        "translation_m": (max(distance(p, positions[0]) for p in positions), 0.001),
        "rotation_deg": (
            max(angle(s.orientation_wxyz, states[0].orientation_wxyz) for s in states),
            0.5,
        ),
        "drift_rate_mps": (distance(positions[-1], positions[0]) / 0.5, 0.002),
        "excursion_m": (max(distance(p, center) for p in positions), 0.001),
        "rotation_rate_dps": (
            angle(states[-1].orientation_wxyz, states[0].orientation_wxyz) / 0.5,
            1.0,
        ),
        "penetration_m": (penetration, 0.001),
        "contact_dropout_max": (1 - touching / len(window), 0.05),
        "speed_run_steps": (longest, 5.0),
        "support_fraction": (supported / touching if touching else 0.0, 0.8),
        "support_geometry": (footprint_margin, 0.02),
        "below_ground": (minimum_z, -0.001),
        "unexpected_contact": (unexpected_contacts, 0.0),
        "effective_speed_max_mps": (max(effective), speed_limit),
        "penetration_budget_used": (
            max((c.penetration for row in window for c in row.contacts), default=0.0) / 0.001,
            0.5,
        ),
        "final_linear_speed_mps": (norm(states[-1].velocity), speed_limit),
        "final_angular_speed_radps": (
            norm(states[-1].angular_velocity),
            speed_limit / max(radius, 1e-12),
        ),
    }
    checks = []
    diagnostics = {
        "effective_speed_max_mps",
        "final_linear_speed_mps",
        "final_angular_speed_radps",
        "penetration_budget_used",
    }
    for name, (value, limit) in measures.items():
        category = (
            "diagnostic"
            if name in diagnostics
            else "numerics_coupled"
            if name in {"penetration_m", "contact_dropout_max"}
            else "scene_truth"
        )
        passed = (
            True
            if category == "diagnostic"
            else value + 1e-8 >= limit
            if name == "support_geometry"
            else value >= limit
            if name in {"support_fraction", "below_ground"}
            else value < limit
            if name == "speed_run_steps"
            else value <= limit
        )
        checks.append(
            GenesisSceneCheckV1(
                profile=profile,
                name=name,
                category=category,
                observed=float(value),
                limit=limit,
                passed=passed,
            )
        )
    return checks, states[-1]


def evaluate_scene_replay(scene, baseline_rows, half_dt_rows):
    """Recompute both profiles and agreement; analytical traces confer no execution authority."""
    scene = GenesisSceneSpecV1.model_validate(scene)
    baseline, a = _profile(scene, baseline_rows, "baseline")
    half_dt, b = _profile(scene, half_dt_rows, "half_dt")
    agreement = [
        GenesisSceneCheckV1(
            profile="agreement",
            name=name,
            category="scene_truth",
            observed=value,
            limit=limit,
            passed=value <= limit,
        )
        for name, value, limit in (
            ("position_delta_m", distance(a.position, b.position), 0.01),
            ("rotation_delta_deg", angle(a.orientation_wxyz, b.orientation_wxyz), 5.0),
        )
    ]
    status = (
        "failed"
        if not all(c.passed for c in baseline + half_dt)
        else "inconclusive"
        if not all(c.passed for c in agreement)
        else "passed"
    )
    failed = [check for check in baseline + half_dt + agreement if not check.passed]
    return GenesisSceneReportV1(
        schema_version=SCENE_REPORT_ID,
        authority="trace_consistency_only",
        status=status,
        numerics_tunable=bool(failed) and all(c.category == "numerics_coupled" for c in failed),
        checks=tuple(baseline + half_dt + agreement),
    )


def _resolve(store, reference):
    if reference.uri != f"artifact://sha256/{reference.sha256}":
        raise ValueError("scene evidence must use CAS references")
    return store.resolve(reference).path


def _read_json(store, reference):
    if reference.media_type != "application/json":
        raise ValueError("scene JSON media type differs")
    payload = _resolve(store, reference).read_bytes()
    value = json.loads(payload)
    if (
        payload
        != json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ):
        raise ValueError("scene evidence JSON must be canonical")
    return value


def _read_profile(store, evidence, profile, scene):
    _, steps, stride = profile_settings(profile)
    if evidence.profile != profile or tuple(frame.step for frame in evidence.frames) != tuple(
        range(0, steps + 1, stride)
    ):
        raise ValueError("media and physics profile steps differ")
    loaded = _read_json(store, evidence.loaded_scene)
    expected = {
        "profile": profile,
        "representation": scene.representation.model_dump(mode="json"),
        "table_center_m": list(scene.table_center_m),
        "table_size_m": list(scene.table_size_m),
    }
    if any(loaded.get(key) != value for key, value in expected.items()):
        raise ValueError("loaded scene differs from requested finite table and asset")
    if (
        type(loaded.get("object_dofs")) is not int
        or loaded.get("object_dofs") != 6
        or loaded.get("table_fixed") is not True
        or loaded.get("ground_fixed") is not True
        or any(
            type(loaded.get(name)) is not int or loaded[name] < 1
            for name in (
                "object_collision_geoms",
                "table_collision_geoms",
                "ground_collision_geoms",
            )
        )
    ):
        raise ValueError("loaded scene lacks dynamic object and collidable fixed roots")
    if (
        evidence.rows.schema_version != SCENE_ROW_ID
        or evidence.rows.media_type != "application/x-ndjson"
    ):
        raise ValueError("scene rows require their exact typed NDJSON reference")
    payload = _resolve(store, evidence.rows).read_bytes()
    rows = [json.loads(line) for line in payload.splitlines()]
    if payload != b"".join(
        json.dumps(
            row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
        + b"\n"
        for row in rows
    ):
        raise ValueError("trace rows must be canonical NDJSON")
    frame_hashes = []
    size = None
    for frame in evidence.frames:
        if frame.image.media_type != "image/png":
            raise ValueError("scene frame media type differs")
        with Image.open(_resolve(store, frame.image)) as im:
            im.load()
            if im.format != "PNG" or im.mode != "RGB" or (size is not None and im.size != size):
                raise ValueError("scene frames must be same-size RGB PNG")
            size = im.size
            frame_hashes.append(hashlib.sha256(im.tobytes()).hexdigest())
    if evidence.video.media_type not in ("video/x-matroska", "video/mp4"):
        raise ValueError("scene video media type differs")
    video_path = _resolve(store, evidence.video)
    probed = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "frame=best_effort_timestamp_time,width,height",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            check=True,
        ).stdout
    )["frames"]
    if len(probed) != 41 or any(
        abs(float(frame["best_effort_timestamp_time"]) - i * 0.1) > 1e-6
        or (frame["width"], frame["height"]) != size
        for i, frame in enumerate(probed)
    ):
        raise ValueError("video frames must follow the complete 0..4s physics sampling cadence")
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    frame_bytes = size[0] * size[1] * 3
    if (
        len(decoded) != 41 * frame_bytes
        or [
            hashlib.sha256(decoded[i : i + frame_bytes]).hexdigest()
            for i in range(0, len(decoded), frame_bytes)
        ]
        != frame_hashes
    ):
        raise ValueError("video is not the exact complete ordered PNG frame sequence")
    return rows, GenesisSceneMediaSummaryV1(
        profile=profile,
        total_frames=41,
        unique_frames=len(set(frame_hashes)),
        width=size[0],
        height=size[1],
        fps=10,
        last_frame_time_s=4.0,
    )


def read_scene_replay(store, reference):
    """Pure read of adapter scene, all declared runtime bytes, traces and actual decoded media."""
    from .genesis_runtime_manifest import read_runtime_manifest
    from .gujie_scene_adapter import read_gujie_tabletop_scene

    if reference.schema_version != SCENE_REPLAY_ID:
        raise ValueError("scene replay reference schema differs")
    replay = GenesisSceneReplayV1.model_validate(_read_json(store, reference))
    scene = read_gujie_tabletop_scene(store, replay.scene)
    read_runtime_manifest(store, replay.runtime_manifest)
    baseline, baseline_media = _read_profile(store, replay.baseline, "baseline", scene)
    half_dt, half_media = _read_profile(store, replay.half_dt, "half_dt", scene)
    report = evaluate_scene_replay(scene, baseline, half_dt)
    return report.model_copy(update={"media": (baseline_media, half_media)})
