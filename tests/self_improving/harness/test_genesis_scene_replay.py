"""Analytical scene traces test mathematical evidence, never execution authority."""

import copy
import json
import subprocess

import pytest
from PIL import Image

from self_improving.harness.genesis_scene_replay import evaluate_scene_replay, read_scene_replay
from self_improving.harness.schemas.genesis_scene_replay import GenesisSceneSpecV1
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401


def reference(schema=None):
    return dict(
        name="fixture.json",
        uri="artifact://sha256/" + "a" * 64,
        sha256="a" * 64,
        bytes=1,
        media_type="application/json",
        schema_version=schema,
    )


@pytest.fixture
def scene():
    return GenesisSceneSpecV1.model_validate(
        dict(
            schema_version="harness.genesis_scene_spec.v1",
            source=reference(),
            representation=reference("harness.asset_representation.v1"),
            geometry=reference(),
            object_id="object",
            position_m=[0.0, 0.0, 0.8],
            orientation_wxyz=[1.0, 0.0, 0.0, 0.0],
            local_vertices_m=[
                [x, y, z] for x in [-0.05, 0.05] for y in [-0.03, 0.03] for z in [0.0, 0.02]
            ],
            table_center_m=[0.0, 0.0, 0.75],
            table_size_m=[1.0, 1.0, 0.1],
            table_friction=0.5,
            ground_z_m=0.0,
        )
    )


def rows(scene, profile):
    dt, steps = (0.004, 1000) if profile == "baseline" else (0.002, 2000)
    states = {
        name: dict(
            position=position,
            orientation_wxyz=[1.0, 0.0, 0.0, 0.0],
            velocity=[0.0, 0.0, 0.0],
            angular_velocity=[0.0, 0.0, 0.0],
        )
        for name, position in [
            ("object", list(scene.position_m)),
            ("table", list(scene.table_center_m)),
            ("ground", [0.0, 0.0, 0.0]),
        ]
    }
    states["object"]["orientation_wxyz"] = list(scene.orientation_wxyz)
    return [
        dict(
            schema_version="harness.genesis_scene_trace_row.v1",
            step=i,
            time_s=i * dt,
            contact_phase="initial_detection" if i == 0 else "solved_step",
            objects=copy.deepcopy(states),
            contacts=[
                dict(
                    a="object",
                    b="table",
                    position=[0.0, 0.0, 0.8],
                    normal=[0.0, 0.0, 1.0],
                    penetration=0.0,
                    force_a=None if i == 0 else [0.0, 0.0, 0.6],
                    force_b=None if i == 0 else [0.0, 0.0, -0.6],
                )
            ],
        )
        for i in range(steps + 1)
    ]


def test_two_independent_profiles_require_finite_table_support(scene):
    report = evaluate_scene_replay(scene, rows(scene, "baseline"), rows(scene, "half_dt"))
    assert report.status == "passed"
    assert report.authority == "trace_consistency_only"
    assert report.numerics_tunable is False
    assert {check.category for check in report.checks} == {
        "scene_truth",
        "numerics_coupled",
        "diagnostic",
    }


@pytest.mark.parametrize(
    "change,criterion",
    [
        ("penetration", "penetration_m"),
        ("no_contact", "contact_dropout_max"),
        ("no_support", "support_fraction"),
        ("ground", "unexpected_contact"),
        ("speed", "speed_run_steps"),
        ("drift", "translation_m"),
    ],
)
def test_actual_trace_failures_are_recomputed(scene, change, criterion):
    baseline = rows(scene, "baseline")
    for row in baseline[-125:]:
        if change == "penetration":
            row["contacts"][0]["penetration"] = 0.002
        elif change == "no_contact":
            row["contacts"] = []
        elif change == "no_support":
            row["contacts"][0]["force_a"] = row["contacts"][0]["force_b"] = [0.0, 0.0, 0.0]
        elif change == "ground":
            row["contacts"][0]["b"] = "ground"
        elif change == "speed":
            row["objects"]["object"]["velocity"] = [0.1, 0.0, 0.0]
        else:
            row["objects"]["object"]["position"][0] = row["step"] * 0.0001
    report = evaluate_scene_replay(scene, baseline, rows(scene, "half_dt"))
    assert report.status == "failed"
    assert any(c.name == criterion and not c.passed for c in report.checks)
    assert report.numerics_tunable is (change == "penetration")


def test_two_locally_settled_profiles_can_be_inconclusive(scene):
    half = rows(scene, "half_dt")
    for row in half[-250:]:
        row["objects"]["object"]["position"][0] = 0.02
    report = evaluate_scene_replay(scene, rows(scene, "baseline"), half)
    assert report.status == "inconclusive"


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "step",
        "time",
        "fixed",
        "initial",
        "phase",
        "names",
        "force",
        "normal",
        "self_contact",
    ],
)
def test_incomplete_or_inconsistent_observations_fail_closed(scene, change):
    baseline = rows(scene, "baseline")
    if change == "missing":
        baseline.pop()
    elif change == "step":
        baseline[1]["step"] = 2
    elif change == "time":
        baseline[1]["time_s"] = 0.5
    elif change == "fixed":
        baseline[10]["objects"]["table"]["position"][0] = 0.1
    elif change == "initial":
        baseline[0]["objects"]["object"]["velocity"][0] = 0.1
    elif change == "phase":
        baseline[1]["contact_phase"] = "initial_detection"
    elif change == "names":
        baseline[1]["objects"].pop("table")
    elif change == "force":
        baseline[1]["contacts"][0]["force_b"] = [0.0, 0.0, 0.6]
    elif change == "normal":
        baseline[1]["contacts"][0]["normal"] = [0.0, 0.0, 0.0]
    else:
        baseline[1]["contacts"][0]["b"] = "object"
    with pytest.raises(ValueError):
        evaluate_scene_replay(scene, baseline, rows(scene, "half_dt"))


def test_full_footprint_cannot_be_replaced_by_center_or_ground(scene):
    value = scene.model_dump(mode="json")
    value["local_vertices_m"][0][0] = 0.49
    scene = GenesisSceneSpecV1.model_validate(value)
    report = evaluate_scene_replay(scene, rows(scene, "baseline"), rows(scene, "half_dt"))
    assert report.status == "failed"
    assert any(c.name == "support_geometry" and not c.passed for c in report.checks)


def test_float32_unit_quaternion_tolerance_does_not_invent_rotation(scene):
    baseline = rows(scene, "baseline")
    baseline[0]["objects"]["object"]["orientation_wxyz"] = [0.99999999, 0.0, 0.0, 0.0]
    assert evaluate_scene_replay(scene, baseline, rows(scene, "half_dt")).status == "passed"


@pytest.fixture
def replay(candidate, tmp_path):  # noqa: F811
    from self_improving.harness.genesis_runtime_manifest import capture_runtime_manifest
    from self_improving.harness.gujie_scene_adapter import (
        adapt_gujie_tabletop_scene,
        read_gujie_tabletop_scene,
    )
    from self_improving.harness.schemas.genesis_runtime_manifest import RuntimeProcessIdentityV1

    store, representation, source, _, publish = candidate
    scene_ref = adapt_gujie_tabletop_scene(
        store,
        source_ref=source,
        representation_ref=representation,
        table_center_m=(0.0, 0.0, 0.4),
        table_size_m=(1.0, 0.6, 0.1),
        table_friction=0.5,
    )
    scene = read_gujie_tabletop_scene(store, scene_ref)
    roots = {}
    for role in ("backend", "interpreter", "stdlib", "distributions", "genesis", "native"):
        root = tmp_path / role
        root.mkdir()
        (root / "member").write_bytes(b"declared fixture, no execution")
        roots[role] = root
    manifest = capture_runtime_manifest(
        store,
        roots=roots,
        process_identity=RuntimeProcessIdentityV1(
            implementation="cpython",
            version="fixture",
            abi="fixture",
            platform="fixture",
            environment=(),
        ),
    )
    frames = []
    pixels = []
    for i in range(41):
        path = tmp_path / f"frame-{i}.png"
        image = Image.new("RGB", (8, 6), (i, 2 * i, 3 * i))
        image.save(path)
        pixels.append(image.tobytes())
        frames.append(
            store.put_file(path, name=path.name, media_type="image/png", schema_version=None)
        )
    video = tmp_path / "frames.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            "8x6",
            "-r",
            "10",
            "-i",
            "-",
            "-c:v",
            "ffv1",
            str(video),
        ],
        input=b"".join(pixels),
        check=True,
    )
    video_ref = store.put_file(
        video, name=video.name, media_type="video/x-matroska", schema_version=None
    )
    result = dict(
        schema_version="harness.genesis_scene_replay.v1",
        scene=scene_ref.model_dump(mode="json"),
        runtime_manifest=manifest.model_dump(mode="json"),
    )
    for profile, stride in (("baseline", 25), ("half_dt", 50)):
        trace = tmp_path / f"{profile}.jsonl"
        trace.write_bytes(
            b"".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                for row in rows(scene, profile)
            )
        )
        trace_ref = store.put_file(
            trace,
            name=trace.name,
            media_type="application/x-ndjson",
            schema_version="harness.genesis_scene_trace_row.v1",
        )
        loaded = dict(
            profile=profile,
            representation=representation.model_dump(mode="json"),
            table_center_m=list(scene.table_center_m),
            table_size_m=list(scene.table_size_m),
            object_dofs=6,
            table_fixed=True,
            ground_fixed=True,
            object_collision_geoms=1,
            table_collision_geoms=1,
            ground_collision_geoms=1,
        )
        loaded_ref = publish(loaded, f"loaded-{profile}.json")
        result[profile] = dict(
            profile=profile,
            rows=trace_ref.model_dump(mode="json"),
            loaded_scene=loaded_ref.model_dump(mode="json"),
            video=video_ref.model_dump(mode="json"),
            frames=[
                dict(step=i * stride, image=ref.model_dump(mode="json"))
                for i, ref in enumerate(frames)
            ],
        )
    return store, publish(result, "replay.json", result["schema_version"]), result, publish


def test_cas_reader_decodes_all_media_and_recomputes_both_profiles(replay):
    store, reference, _, _ = replay
    report = read_scene_replay(store, reference)
    assert report.status == "passed"
    assert all(media.total_frames == media.unique_frames == 41 for media in report.media)


@pytest.mark.parametrize(
    "change",
    [
        "profile",
        "cadence",
        "order",
        "load",
        "load_binding",
        "load_boolean",
        "rows_type",
        "png_type",
        "png_size",
        "video_type",
        "video_timing",
        "row_canonical",
        "json_canonical",
        "reference_schema",
        "reference_locator",
        "json_type",
    ],
)
def test_reader_rejects_mismatched_closure_or_media(replay, tmp_path, change):
    store, reference, value, publish = replay
    if change == "profile":
        value["baseline"]["profile"] = "half_dt"
    elif change == "cadence":
        value["baseline"]["frames"][1]["step"] = 1
    elif change == "order":
        a, b = value["baseline"]["frames"][:2]
        a["image"], b["image"] = b["image"], a["image"]
    elif change in ("load", "load_binding", "load_boolean"):
        from self_improving.harness.schemas.common import ArtifactRef

        ref = ArtifactRef.model_validate(value["baseline"]["loaded_scene"])
        loaded = json.loads(store.resolve(ref).path.read_bytes())
        if change == "load_boolean":
            loaded["object_collision_geoms"] = True
        else:
            loaded["object_dofs" if change == "load" else "profile"] = 0
        value["baseline"]["loaded_scene"] = publish(loaded, "bad-loaded.json").model_dump(
            mode="json"
        )
    elif change == "rows_type":
        value["baseline"]["rows"]["schema_version"] = "wrong"
    elif change == "png_type":
        value["baseline"]["frames"][0]["image"]["media_type"] = "image/jpeg"
    elif change == "png_size":
        path = tmp_path / "small.png"
        Image.new("L", (2, 2)).save(path)
        value["baseline"]["frames"][0]["image"] = store.put_file(
            path, name=path.name, media_type="image/png", schema_version=None
        ).model_dump(mode="json")
    elif change == "video_type":
        value["baseline"]["video"]["media_type"] = "application/octet-stream"
    elif change == "video_timing":
        from self_improving.harness.schemas.common import ArtifactRef

        original = store.resolve(ArtifactRef.model_validate(value["baseline"]["video"])).path
        path = tmp_path / "fast.mkv"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-itsscale",
                "0.5",
                "-i",
                str(original),
                "-c",
                "copy",
                str(path),
            ],
            check=True,
        )
        value["baseline"]["video"] = store.put_file(
            path, name=path.name, media_type="video/x-matroska", schema_version=None
        ).model_dump(mode="json")
    elif change == "row_canonical":
        path = tmp_path / "bad-rows.jsonl"
        path.write_text(" {}\n")
        value["baseline"]["rows"] = store.put_file(
            path,
            name=path.name,
            media_type="application/x-ndjson",
            schema_version="harness.genesis_scene_trace_row.v1",
        ).model_dump(mode="json")
    elif change == "json_canonical":
        path = tmp_path / "noncanonical.json"
        path.write_text(json.dumps(value, indent=2))
        reference = store.put_file(
            path,
            name=path.name,
            media_type="application/json",
            schema_version=value["schema_version"],
        )
    elif change == "reference_schema":
        reference = reference.model_copy(update={"schema_version": "wrong"})
    elif change == "json_type":
        reference = reference.model_copy(update={"media_type": "text/plain"})
    else:
        reference = reference.model_copy(update={"uri": "file:///unbound.json"})
    if change not in ("json_canonical", "reference_schema", "reference_locator", "json_type"):
        reference = publish(value, "changed-replay.json", value["schema_version"])
    with pytest.raises(ValueError):
        read_scene_replay(store, reference)


@pytest.mark.parametrize(
    "change",
    [
        "quaternion",
        "extents",
        "ground",
        "locator",
        "representation",
        "boolean_position",
        "boolean_ground",
    ],
)
def test_scene_schema_rejects_invalid_authored_domain(scene, change):
    value = scene.model_dump(mode="json")
    if change == "quaternion":
        value["orientation_wxyz"] = [0.0, 0.0, 0.0, 0.0]
    elif change == "extents":
        value["table_size_m"][0] = 0.0
    elif change == "ground":
        value["table_center_m"][2] = 0.0
    elif change == "locator":
        value["source"]["uri"] = "file:///unbound.json"
    elif change == "boolean_position":
        value["position_m"][0] = False
    elif change == "boolean_ground":
        value["ground_z_m"] = False
    else:
        value["representation"]["schema_version"] = "wrong"
    with pytest.raises(ValueError):
        GenesisSceneSpecV1.model_validate(value)


def test_initial_and_missing_solved_forces_are_unavailable_and_invalid_respectively(scene):
    baseline = rows(scene, "baseline")
    baseline[0]["contacts"][0]["force_a"] = [0.0, 0.0, 0.6]
    with pytest.raises(ValueError, match="initial contact force"):
        evaluate_scene_replay(scene, baseline, rows(scene, "half_dt"))
    baseline[0]["contacts"][0]["force_a"] = None
    baseline[1]["contacts"][0]["force_a"] = None
    with pytest.raises(ValueError, match="solved contact forces"):
        evaluate_scene_replay(scene, baseline, rows(scene, "half_dt"))


def test_runner_profile_interface_does_not_accept_custom_numerics():
    from self_improving.harness.genesis_scene_replay import profile_settings

    assert profile_settings("baseline") == (0.004, 1000, 25)
    assert profile_settings("half_dt") == (0.002, 2000, 50)
    with pytest.raises(ValueError, match="unknown scene replay profile"):
        profile_settings("custom")
