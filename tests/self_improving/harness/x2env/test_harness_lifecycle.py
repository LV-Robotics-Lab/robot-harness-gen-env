"""Single durable workflow through the user-facing Harness interface."""

import pytest

from self_improving.harness.x2env.contracts import X2EnvRequest
from self_improving.harness.x2env.harness import Harness


def test_submit_persists_one_handle_before_execution_and_survives_restart(tmp_path):
    state = tmp_path / "state"
    request = X2EnvRequest(
        text="a pink mouse on a table",
        seed=11,
        idempotency_key="one",
        output_dir=str(tmp_path / "package"),
    )
    first = Harness(state).submit(request)
    restarted = Harness(state)
    second = restarted.submit(request)
    assert second.workflow_id == first.workflow_id
    snapshot = restarted.status(first.workflow_id)
    assert snapshot.status == "active"
    assert snapshot.revision == 0
    assert snapshot.request == request
    assert snapshot.operations == ()
    with pytest.raises(ValueError, match="idempotency"):
        restarted.submit(request.model_copy(update={"text": "different request"}))
    with pytest.raises(KeyError):
        restarted.status("missing-workflow")


def test_resume_commits_real_ingest_before_reporting_missing_backend(tmp_path):
    harness = Harness(tmp_path / "state")
    request = X2EnvRequest(
        text="a pink mouse on a table",
        seed=11,
        idempotency_key="resume",
        output_dir=str(tmp_path / "package"),
    )
    handle = harness.submit(request)
    snapshot = harness.resume(handle.workflow_id)
    assert snapshot.status == "blocked"
    assert snapshot.stop_reason == "blocked_external_resource"
    assert snapshot.required_resources == ("managed_codex_backend",)
    assert [operation.capability for operation in snapshot.operations] == ["ingest"]
    assert snapshot.operations[0].status == "succeeded"
    assert snapshot.input_bundle is not None
    again = Harness(tmp_path / "state").resume(handle.workflow_id)
    assert len(again.operations) == 1
    assert again.input_bundle == snapshot.input_bundle
    with pytest.raises(ValueError, match="package"):
        harness.package(handle.workflow_id)


def test_bad_image_retains_raw_artifact_and_terminal_failure_without_retry(tmp_path):
    from self_improving.harness.x2env.contracts import InputMedia

    image = tmp_path / "bad.png"
    image.write_bytes(b"this is not an image")
    harness = Harness(tmp_path / "state")
    handle = harness.submit(
        X2EnvRequest(
            images=(InputMedia(path=str(image)),),
            seed=1,
            idempotency_key="bad",
            output_dir=str(tmp_path / "package"),
        )
    )
    snapshot = harness.resume(handle.workflow_id)
    assert snapshot.status == "failed"
    assert snapshot.stop_reason == "invalid_image"
    assert len(snapshot.operations[0].result.outputs) == 1
    assert Harness(tmp_path / "state").resume(handle.workflow_id) == snapshot


def test_missing_decoder_is_blocked_with_raw_input_evidence(tmp_path, monkeypatch):
    from self_improving.harness.x2env.contracts import InputMedia

    video = tmp_path / "input.mp4"
    video.write_bytes(b"source bytes survive missing decoder")
    harness = Harness(tmp_path / "state")
    handle = harness.submit(
        X2EnvRequest(
            video=InputMedia(path=str(video)),
            seed=1,
            idempotency_key="decoder",
            output_dir=str(tmp_path / "package"),
        )
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no-decoder"))
    snapshot = harness.resume(handle.workflow_id)
    assert snapshot.status == "blocked"
    assert snapshot.stop_reason == "blocked_external_resource"
    assert snapshot.operations[0].status == "blocked"
    assert snapshot.operations[0].result.outputs


def test_managed_interpretation_is_journaled_but_critical_unknown_blocks(tmp_path):
    from self_improving.harness.x2env.contracts import (
        BackendProposal,
        FieldProvenance,
        SceneIntentProposal,
        UnknownField,
    )

    class UncertainModel:
        def interpret(self, bundle, *, output_root, timeout):
            assert bundle.text is not None
            return BackendProposal(
                status="completed",
                proposal=SceneIntentProposal(
                    scene=None,
                    unknowns=(
                        UnknownField(
                            field="cabinet.articulation_state",
                            reason="request conflicts with image",
                            critical=True,
                            provenance=(
                                FieldProvenance(
                                    source="text", input_sha256=bundle.text.sha256, kind="explicit"
                                ),
                            ),
                        ),
                    ),
                ),
                evidence=(),
                error_code=None,
                elapsed_seconds=0.0,
            )

    harness = Harness(tmp_path / "state", backend_factory=lambda store: UncertainModel())
    handle = harness.submit(
        X2EnvRequest(
            text="open cabinet",
            seed=1,
            idempotency_key="unknown",
            output_dir=str(tmp_path / "package"),
        )
    )
    snapshot = harness.resume(handle.workflow_id)
    assert snapshot.status == "blocked"
    assert snapshot.stop_reason == "clarification_required"
    assert [op.capability for op in snapshot.operations] == ["ingest", "codex.interpret"]
    assert snapshot.proposal is not None
    assert snapshot.scene_ir is None
    assert harness.resume(handle.workflow_id) == snapshot


def test_concurrent_submissions_share_one_persisted_workflow(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    harness = Harness(tmp_path / "state")
    request = X2EnvRequest(
        text="one mouse", seed=1, idempotency_key="concurrent", output_dir=str(tmp_path / "package")
    )
    with ThreadPoolExecutor(max_workers=4) as workers:
        handles = list(workers.map(lambda _: harness.submit(request), range(8)))
    assert len({handle.workflow_id for handle in handles}) == 1


def test_live_owner_is_rejected_and_dead_owner_is_recovered_via_resume(tmp_path):
    import os
    import select
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    # A paused external decoder allows an actual abrupt worker death at the public seam.
    decoder_dir = tmp_path / "decoder"
    decoder_dir.mkdir()
    decoder = decoder_dir / "ffprobe"
    decoder.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    decoder.chmod(0o700)
    video = tmp_path / "input.mp4"
    video.write_bytes(b"invalid media; decoder intentionally paused for owner-death test")
    state = tmp_path / "state"
    root = Path(__file__).resolve().parents[4]
    worker_code = """
import sys
from pathlib import Path
from self_improving.harness.x2env.harness import Harness
from self_improving.harness.x2env.contracts import X2EnvRequest, InputMedia
h = Harness(Path(sys.argv[1]))
r = X2EnvRequest(video=InputMedia(path=sys.argv[2]), seed=1,
                idempotency_key="owner", output_dir=sys.argv[3])
handle = h.submit(r)
print(handle.workflow_id, flush=True)
h.resume(handle.workflow_id)
"""
    environment = {
        **os.environ,
        "PYTHONPATH": str(root),
        "PATH": str(decoder_dir) + os.pathsep + os.environ["PATH"],
    }
    process = subprocess.Popen(
        [sys.executable, "-c", worker_code, str(state), str(video), str(tmp_path / "package")],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert select.select([process.stdout], [], [], 10)[0]
        workflow_id = process.stdout.readline().strip()
        assert workflow_id
        harness = Harness(state)
        deadline = time.monotonic() + 10
        while not harness.status(workflow_id).owner and time.monotonic() < deadline:
            time.sleep(0.01)
        assert harness.status(workflow_id).owner is not None
        with pytest.raises(ValueError, match="owner"):
            harness.resume(workflow_id)
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=5)
    snapshot = harness.resume(workflow_id)
    assert snapshot.status == "failed"
    assert len(snapshot.operations) == 2
    assert snapshot.operations[0].result.error_code == "recoverable_dead_owner"
    assert snapshot.operations[1].result.error_code == "invalid_video"
