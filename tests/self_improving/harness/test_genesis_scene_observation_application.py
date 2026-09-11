"""Public observation boundaries; real candidates are supplied by the formal scene run."""

from uuid import uuid4

import pytest

from self_improving.harness.genesis_scene_observation_application import (
    GenesisSceneObservationApplication,
)
from self_improving.harness.schemas.genesis_scene_assessment import GenesisSceneObserveInputV1


def test_observation_application_cannot_use_arbitrary_terminal_provider(tmp_path):
    with pytest.raises(TypeError, match="fixed scene assessment"):
        GenesisSceneObservationApplication(tmp_path, object())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("ttl", [True, 0, 301, 1.5, "300"])
def test_observation_input_refuses_implicit_or_extended_ttl(ttl):
    with pytest.raises(ValueError):
        GenesisSceneObserveInputV1(
            schema_version="harness.genesis_scene_observe_input.v1",
            replay_run_id=uuid4(),
            ttl_seconds=ttl,
        )


def evaluate_real_assessment_candidates(replay_application, replay_run_id, output_root):
    """Immediately consume a real replay candidate through both durable candidate kernels."""
    import json

    from self_improving.harness.genesis_scene_assessment import GenesisSceneAssessment
    from self_improving.harness.genesis_scene_validation_application import (
        GenesisSceneValidationApplication,
    )
    from self_improving.harness.schemas.genesis_scene_assessment import GenesisSceneValidateInputV1

    assessment = GenesisSceneAssessment(replay_application)
    observe = GenesisSceneObservationApplication(
        output_root / "observation-application", assessment
    )
    observed = observe.evaluate(
        GenesisSceneObserveInputV1(
            schema_version="harness.genesis_scene_observe_input.v1",
            replay_run_id=replay_run_id,
            ttl_seconds=300,
        )
    )
    observation_path = output_root / f"observation-candidate-{observed.state.run_id}.json"
    observation_path.write_text(
        json.dumps(
            {
                "state": observed.state.model_dump(mode="json"),
                "invocation": observed.invocation.model_dump(mode="json"),
                "candidate_only": True,
            },
            indent=2,
        )
    )
    with pytest.raises(ValueError, match="distinct"):
        observe.qualify((observed.state.run_id, observed.state.run_id))
    with pytest.raises(ValueError, match="qualified replay"):
        assessment.observe(replay_run_id, ttl_seconds=300)
    assert observe.skills == ()
    validate = GenesisSceneValidationApplication(output_root / "validation-application", assessment)
    validated = validate.evaluate(
        GenesisSceneValidateInputV1(
            schema_version="harness.genesis_scene_validate_input.v1",
            replay_run_id=replay_run_id,
        )
    )
    validation_path = output_root / f"validation-candidate-{validated.state.run_id}.json"
    validation_path.write_text(
        json.dumps(
            {
                "state": validated.state.model_dump(mode="json"),
                "invocation": validated.invocation.model_dump(mode="json"),
                "candidate_only": True,
            },
            indent=2,
        )
    )
    with pytest.raises(ValueError, match="distinct"):
        validate.qualify((validated.state.run_id, validated.state.run_id))
    assert validate.skills == ()
    return {"observation": str(observation_path), "validation": str(validation_path)}


def test_formal_qualifies_two_observations_and_two_validations():
    import json
    import os
    from pathlib import Path
    from uuid import UUID

    configured = os.environ.get("GENESIS_SCENE_ASSESSMENT_QUALIFY_CONFIG")
    if not configured:
        pytest.skip("requires two actual successful replay/assessment candidates")
    from self_improving.harness.genesis_scene_assessment import GenesisSceneAssessment
    from self_improving.harness.genesis_scene_validation_application import (
        GenesisSceneValidationApplication,
    )
    from tests.self_improving.harness.test_genesis_scene_application_v2 import (
        build_formal_application,
    )

    config = json.loads(Path(configured).read_bytes())
    manifest = Path(config["manifest_path"])
    replay, _, _ = build_formal_application(manifest, Path(config["handoff_path"]))
    assessment = GenesisSceneAssessment(replay)
    summary = {"simulator_rerun": False, "candidate_only_sources": True}
    for kind, app_type, skill in (
        ("observation", GenesisSceneObservationApplication, "genesis.observe"),
        ("validation", GenesisSceneValidationApplication, "image2env.validate"),
    ):
        app = app_type(manifest.parent / (kind + "-application"), assessment)
        run_ids = tuple(UUID(value) for value in config[kind + "_runs"])
        descriptor = app.qualify(run_ids)
        assert len(app.skills) == 1
        assert app.skills[0] == descriptor
        assert descriptor.skill_id == skill and descriptor.version == "2.0.0"
        summary[kind] = {
            "qualification_ref": descriptor.qualification_artifact.model_dump(mode="json"),
            "implementation_sha256": app.implementation_sha256,
            "candidate_runs": config[kind + "_runs"],
            "registered": True,
        }
        Path(config["summary_path"]).write_text(json.dumps(summary, indent=2))
