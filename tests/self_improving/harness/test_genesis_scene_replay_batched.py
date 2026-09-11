"""All-vertex batched arithmetic is checked against the frozen scalar public seam."""

import copy

import pytest

from self_improving.harness.genesis_scene_replay import evaluate_scene_replay
from self_improving.harness.genesis_scene_replay_batched import evaluate_scene_replay_batched
from tests.self_improving.harness.test_genesis_scene_replay import rows, scene  # noqa: F401


@pytest.mark.parametrize(
    "variation", ["ordinary", "rare_row", "last_vertex", "penetration", "dropout"]
)
def test_every_named_check_matches_frozen_scalar_reader(scene, variation):  # noqa: F811
    if variation == "last_vertex":
        scene = scene.model_copy(
            update={"local_vertices_m": (*scene.local_vertices_m, (0.6, 0.0, 0.0))}
        )
    baseline, half = rows(scene, "baseline"), rows(scene, "half_dt")
    if variation == "rare_row":
        baseline[17]["objects"]["object"]["position"][0] = 0.6
    elif variation == "penetration":
        half[1937]["contacts"][0]["penetration"] = 0.00101
    elif variation == "dropout":
        for row in baseline[-20:]:
            row["contacts"] = []
    original = evaluate_scene_replay(scene, copy.deepcopy(baseline), copy.deepcopy(half))
    batched = evaluate_scene_replay_batched(scene, baseline, half)
    assert batched.status == original.status
    assert batched.numerics_tunable == original.numerics_tunable
    for left, right in zip(original.checks, batched.checks, strict=True):
        assert left.model_dump(exclude={"observed"}) == right.model_dump(exclude={"observed"})
        assert left.observed == pytest.approx(right.observed, abs=1e-12, rel=1e-12)


def test_formal_batched_reader_matches_actual_scalar_report_and_media():
    import json
    import os
    import time
    from pathlib import Path

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_replay_batched import read_scene_replay_batched
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.genesis_scene_replay import GenesisSceneReportV1

    configured = os.environ.get("GENESIS_SCENE_EXECUTION_CONFIG")
    if not configured:
        pytest.skip("requires the preserved real scalar scene evidence")
    config_path = Path(configured)
    config = json.loads(config_path.read_bytes())
    store = LocalArtifactStore(Path(config["cas_root"]))
    summary = json.loads(
        (
            config_path.parent / "scene-candidate-f0698892-d75c-4409-b4ed-6b8d211a82a0.json"
        ).read_bytes()
    )
    refs = {
        ref["schema_version"]: ArtifactRef.model_validate(ref)
        for ref in summary["state"]["artifacts"]
    }
    expected = GenesisSceneReportV1.model_validate_json(
        store.resolve(refs["harness.genesis_scene_report.v1"]).path.read_bytes()
    )
    started = time.monotonic()
    actual = read_scene_replay_batched(store, refs["harness.genesis_scene_replay.v1"])
    elapsed = time.monotonic() - started
    assert actual.model_dump(exclude={"checks"}) == expected.model_dump(exclude={"checks"})
    differences = []
    for scalar, batched in zip(expected.checks, actual.checks, strict=True):
        assert scalar.model_dump(exclude={"observed"}) == batched.model_dump(exclude={"observed"})
        assert scalar.observed == pytest.approx(batched.observed, abs=1e-12, rel=1e-12)
        differences.append(abs(scalar.observed - batched.observed))
    result = dict(
        elapsed_seconds=elapsed,
        checks=len(actual.checks),
        vertices=24573,
        rows=3002,
        maximum_observed_difference=max(differences),
        status=actual.status,
        media=[m.model_dump(mode="json") for m in actual.media],
    )
    (config_path.parent / "batched-equivalence-summary.json").write_text(
        json.dumps(result, indent=2)
    )
    print(json.dumps(result), flush=True)
