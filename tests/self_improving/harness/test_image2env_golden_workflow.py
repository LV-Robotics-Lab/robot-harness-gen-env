"""The assembly must recover authority from the existing production apps."""

import importlib

import pytest

from tests.self_improving.harness.test_genesis_runtime_execution import (
    execution_request,  # noqa: F401
)


def test_delivery_accepts_exact_expiration_but_rejects_one_microsecond_late():
    from datetime import datetime, timedelta, timezone

    from self_improving.harness.image2env_golden_workflow import (
        GoldenWorkflowDelivery,
        GoldenWorkflowDeliveryExpired,
    )
    from self_improving.harness.schemas.common import ArtifactRef

    ref = ArtifactRef(
        name="golden-workflow.json",
        uri="artifact://sha256/" + "a" * 64,
        sha256="a" * 64,
        bytes=2,
        media_type="application/json",
        schema_version=None,
    )
    expiry = datetime(2026, 9, 11, tzinfo=timezone.utc)
    result = GoldenWorkflowDelivery(ref, expiry, expiry)
    assert result.summary_ref == ref
    assert result.delivered_at == result.expires_at
    with pytest.raises(GoldenWorkflowDeliveryExpired) as caught:
        GoldenWorkflowDelivery(ref, expiry + timedelta(microseconds=1), expiry)
    assert caught.value.unpublished_summary_ref == ref


@pytest.mark.parametrize("offset_hours", [None, 8])
def test_delivery_rejects_ambiguous_non_utc_timestamps(offset_hours):
    from datetime import datetime, timedelta, timezone

    from self_improving.harness.image2env_golden_workflow import GoldenWorkflowDelivery

    expiry = datetime(2026, 9, 11, tzinfo=timezone.utc)
    stamp = expiry.replace(
        tzinfo=None if offset_hours is None else timezone(timedelta(hours=offset_hours))
    )
    with pytest.raises(ValueError, match="must be UTC"):
        GoldenWorkflowDelivery(None, stamp, expiry)


def test_caller_application_claims_cannot_start_a_golden_workflow(tmp_path):
    module = importlib.import_module("self_improving.harness.image2env_golden_workflow")
    before = tuple(tmp_path.iterdir())
    with pytest.raises(TypeError, match="production compile application"):
        module.Image2EnvGoldenWorkflow(
            compile_application={"status": "succeeded", "qualified": True},
            replay_application=object(),
            observation_application=object(),
            validation_application=object(),
        )
    assert tuple(tmp_path.iterdir()) == before


def test_real_compile_app_does_not_make_a_caller_replay_claim_trusted(
    execution_request,  # noqa: F811
    tmp_path,
):
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.image2env_genesis_compile_application import (
        Image2EnvGenesisCompileApplication,
    )
    from self_improving.harness.image2env_golden_workflow import Image2EnvGoldenWorkflow

    store, _, _, _, runtime, _ = execution_request
    assets = GenesisAssetQualificationApplication(
        tmp_path / "assets.sqlite", store, runtime.manifest
    )
    compile_app = Image2EnvGenesisCompileApplication(
        tmp_path / "compile", Image2EnvGenesisCompiler(store, assets)
    )
    before = set(store.root.rglob("*"))
    with pytest.raises(TypeError, match="production replay application"):
        Image2EnvGoldenWorkflow(
            compile_application=compile_app,
            replay_application={"status": "succeeded"},
            observation_application=object(),
            validation_application=object(),
        )
    assert set(store.root.rglob("*")) == before
    assert compile_app.skills == ()


def test_formal_four_registered_skills_publish_one_real_workflow():
    """Real configured applications own all four executions and their evidence."""
    import json
    import os
    from pathlib import Path
    from uuid import uuid4

    configured = os.environ.get("IMAGE2ENV_GOLDEN_WORKFLOW_CONFIG")
    if not configured:
        pytest.skip("requires four qualified production applications and fixed scene deployment")
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.genesis_fresh_capture import GenesisFreshCaptureProducer
    from self_improving.harness.genesis_fresh_capture_application import (
        GenesisFreshCaptureApplication,
    )
    from self_improving.harness.genesis_scene_application_v2 import GenesisSceneApplication
    from self_improving.harness.genesis_scene_assessment import GenesisSceneAssessment
    from self_improving.harness.genesis_scene_execution_v2 import GenesisSceneExecutionProducerV2
    from self_improving.harness.genesis_scene_validation_application import (
        GenesisSceneValidationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.image2env_genesis_compile_application import (
        Image2EnvGenesisCompileApplication,
    )
    from self_improving.harness.image2env_golden_workflow import Image2EnvGoldenWorkflow
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.genesis_scene_application_v2 import (
        GenesisSceneReplayInputV2,
    )

    config = json.loads(Path(configured).read_bytes())
    ref = ArtifactRef.model_validate
    store = LocalArtifactStore(Path(config["cas_root"]))
    assets = GenesisAssetQualificationApplication(
        Path(config["asset_state_path"]), store, ref(config["asset_manifest"])
    )
    compiler = Image2EnvGenesisCompiler(store, assets)
    compile_app = Image2EnvGenesisCompileApplication(
        Path(config["compile_state_root"]),
        compiler,
        qualification=ref(config["compile_qualification"]),
    )
    producer = GenesisSceneExecutionProducerV2(store, compiler, ref(config["scene_manifest"]))
    replay_app = GenesisSceneApplication(
        Path(config["scene_state_root"]),
        producer,
        roots={role: Path(path) for role, path in config["roots"].items()},
        compile_application=compile_app,
    )
    replay_app.bind(ref(config["scene_qualification"]))
    assessment = GenesisSceneAssessment(replay_app)
    observe_app = GenesisFreshCaptureApplication(
        Path(config["observation_state_root"]),
        GenesisFreshCaptureProducer(store, replay_app, ref(config["capture_manifest"])),
        roots={role: Path(path) for role, path in config["capture_roots"].items()},
    )
    observe_app.bind(ref(config["observation_qualification"]))
    validate_app = GenesisSceneValidationApplication(
        Path(config["validation_state_root"]), assessment
    )
    validate_app.bind(ref(config["validation_qualification"]))
    workflow = Image2EnvGoldenWorkflow(
        compile_application=compile_app,
        replay_application=replay_app,
        observation_application=observe_app,
        validation_application=validate_app,
    )
    run_ids = dict(replay_run_id=uuid4(), observation_run_id=uuid4(), validation_run_id=uuid4())
    attempt_path = Path(config["attempt_path"])
    attempt_path.write_text(
        json.dumps({key: str(value) for key, value in run_ids.items()}, indent=2)
    )
    import sys

    direct_deep_reads = []
    previous_profile = sys.getprofile()

    def record_calls(frame, event, arg):
        if (
            event == "call"
            and frame.f_code.co_name in {"read", "read_replay", "read_receipt"}
            and frame.f_back is not None
            and frame.f_back.f_globals.get("__name__")
            == "self_improving.harness.image2env_golden_workflow"
        ):
            direct_deep_reads.append(frame.f_code.co_name)

    sys.setprofile(record_calls)
    try:
        delivery = workflow.run(
            GenesisSceneReplayInputV2.model_validate(config["replay_input"]),
            **run_ids,
        )
    finally:
        sys.setprofile(previous_profile)
    assert direct_deep_reads == []
    summary_ref = delivery.summary_ref
    assert delivery.delivered_at <= delivery.expires_at
    data = store.resolve(summary_ref).path.read_bytes()
    summary = json.loads(data)
    assert (
        data
        == json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )
    assert summary["authority"] == "checked_application_lineage_only"
    assert set(summary["stages"]) == {"compile", "replay", "observe", "validate"}
    assert all(stage["state"]["status"] == "succeeded" for stage in summary["stages"].values())
    observed_ref, observed = observe_app.read(run_ids["observation_run_id"])
    assert observed_ref.model_dump(mode="json") == summary["fresh_observation"]
    assert observed.authority == "observed_restored_final_pose"
    assert observed.process_continuation is False
    assert observed.captured_at <= observed.checked_at <= observed.expires_at
    assert observed.replay_receipt.model_dump(mode="json") == summary["execution_receipt"]
    Path(config["summary_ref_path"]).write_text(
        json.dumps(summary_ref.model_dump(mode="json"), indent=2)
    )
    Path(config["summary_ref_path"]).with_suffix(".delivery.json").write_text(
        json.dumps(
            {
                "summary_ref": summary_ref.model_dump(mode="json"),
                "delivered_at": delivery.delivered_at.isoformat(),
                "expires_at": delivery.expires_at.isoformat(),
                "direct_deep_reads": direct_deep_reads,
            },
            indent=2,
        )
    )
