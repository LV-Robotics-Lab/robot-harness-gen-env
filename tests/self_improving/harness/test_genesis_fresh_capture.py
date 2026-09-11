"""Fresh capture is versioned separately from replay and historical observations."""

from uuid import uuid4

import pytest


def test_observe_v3_requires_exact_uuid_and_bounded_ttl():
    from self_improving.harness.schemas.genesis_fresh_capture import GenesisFreshCaptureInputV1

    payload = dict(
        schema_version="harness.genesis_fresh_capture_input.v1",
        replay_run_id=uuid4(),
        ttl_seconds=300,
    )
    assert GenesisFreshCaptureInputV1.model_validate(payload).ttl_seconds == 300
    for invalid in (True, 0, 301, 1.5):
        with pytest.raises(ValueError):
            GenesisFreshCaptureInputV1.model_validate(dict(payload, ttl_seconds=invalid))


def test_fresh_producer_requires_exact_trusted_replay_application():
    from self_improving.harness.genesis_fresh_capture import GenesisFreshCaptureProducer

    with pytest.raises(TypeError, match="trusted replay"):
        GenesisFreshCaptureProducer(None, None, None)


def test_fresh_application_rejects_arbitrary_producer_before_state(tmp_path):
    from self_improving.harness.genesis_fresh_capture_application import (
        GenesisFreshCaptureApplication,
    )

    with pytest.raises(TypeError, match="fixed fresh capture"):
        GenesisFreshCaptureApplication(tmp_path / "state", None, roots={})
    assert not (tmp_path / "state").exists()


def test_caller_cannot_construct_live_camera_authority():
    from self_improving.harness.genesis_fresh_capture import LiveGenesisFreshCapture

    with pytest.raises(TypeError, match="actual fixed capture"):
        LiveGenesisFreshCapture(None)


def build_real_capture_application(manifest_path, *, roots_enabled=True):
    import json
    from pathlib import Path

    from self_improving.harness.genesis_fresh_capture import GenesisFreshCaptureProducer
    from self_improving.harness.genesis_fresh_capture_application import (
        GenesisFreshCaptureApplication,
    )
    from self_improving.harness.schemas.common import ArtifactRef
    from tests.self_improving.harness.test_genesis_scene_application_v2 import (
        build_formal_application,
    )

    replay_root = Path("/home/jingxiang/bingsheng/golden-scene-runtime-v2-sorted-20260911.0h7liezw")
    replay, _, _ = build_formal_application(
        replay_root / "scene-manifest-summary.json",
        Path(
            "/home/jingxiang/bingsheng/golden-native-compile-app-terminal-closed-20260911/registered-handoff.json"
        ),
        roots_enabled=False,
    )
    replay.bind(
        ArtifactRef.model_validate(
            json.loads((replay_root / "replay-qualification-ref.json").read_bytes())
        )
    )
    config = json.loads(manifest_path.read_bytes())
    producer = GenesisFreshCaptureProducer(
        replay.producer.artifact_store, replay, ArtifactRef.model_validate(config["manifest_ref"])
    )
    return GenesisFreshCaptureApplication(
        manifest_path.parent / "observation-v3-application",
        producer,
        roots={key: Path(value) for key, value in config["roots"].items()} if roots_enabled else {},
    )


def test_formal_two_fresh_capture_candidates_from_registered_replay():
    import json
    import os
    import time
    from pathlib import Path
    from uuid import UUID

    from self_improving.harness.schemas.genesis_fresh_capture import GenesisFreshCaptureInputV1

    configured = os.environ.get("GENESIS_FRESH_CAPTURE_MANIFEST")
    if not configured:
        pytest.skip("requires independent fixed capture runtime and registered replay")
    path = Path(configured)
    app = build_real_capture_application(path)
    parameters = GenesisFreshCaptureInputV1(
        schema_version="harness.genesis_fresh_capture_input.v1",
        replay_run_id=UUID("00668101-6976-4d45-9c52-c35f78814e19"),
        ttl_seconds=300,
    )
    for _ in range(2):
        started = time.monotonic()
        candidate = app.evaluate(parameters)
        summary = dict(
            state=candidate.state.model_dump(mode="json"),
            invocation=candidate.invocation.model_dump(mode="json")
            if candidate.invocation
            else None,
            elapsed_seconds=time.monotonic() - started,
            implementation_sha256=app.implementation_sha256,
        )
        output = path.parent / f"fresh-candidate-{candidate.state.run_id}.json"
        output.write_text(json.dumps(summary, indent=2))
        print(output, flush=True)
        assert candidate.state.status.value == "succeeded", candidate.state.blocker
        assert app.skills == ()


def test_formal_capture_qualification_and_restart():
    import json
    import os
    from pathlib import Path
    from uuid import UUID

    configured = os.environ.get("GENESIS_FRESH_CAPTURE_QUALIFY")
    if not configured:
        pytest.skip("requires two real fresh capture candidates")
    path = Path(os.environ["GENESIS_FRESH_CAPTURE_MANIFEST"])
    run_ids = tuple(UUID(value) for value in configured.split(","))
    app = build_real_capture_application(path, roots_enabled=False)
    descriptor = app.qualify(run_ids)
    qualification = descriptor.qualification_artifact
    summary = dict(
        qualification_ref=qualification.model_dump(mode="json"),
        descriptor=descriptor.model_dump(mode="json"),
        candidate_runs=list(configured.split(",")),
        registered=True,
        restart_without_roots=False,
        state_root=str(path.parent / "observation-v3-application"),
        implementation_sha256=app.implementation_sha256,
    )
    output = path.parent / "fresh-qualification-summary.json"
    output.write_text(json.dumps(summary, indent=2))
    restarted = build_real_capture_application(path, roots_enabled=False)
    assert restarted.bind(qualification) == descriptor
    assert restarted.skills == app.skills
    for run_id in run_ids:
        assert restarted.run_state(run_id) == app.run_state(run_id)
    summary["restart_without_roots"] = True
    output.write_text(json.dumps(summary, indent=2))
