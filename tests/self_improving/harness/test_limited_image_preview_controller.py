"""Controller recovery exercises the real shared SQLite/CAS with external boundaries."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from self_improving.harness.codex_backend import CodexBackendResult
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.limited_image_preview_controller import (
    LimitedImagePreviewController,
    timing_samples,
)
from self_improving.harness.schemas.workflow import RunReadRequest, RunStartRequest
from self_improving.harness.trusted_state import WorldFactEvidence
from tests.self_improving.harness.test_golden_control_v2_runtime import native_case


@pytest.fixture
def service(tmp_path):
    base = native_case(tmp_path / "seed")
    workflow = uuid4()
    journal = SQLiteGoldenRunStore(tmp_path / "shared.sqlite")
    harness = GoldenRunHarness(
        artifact_store=base.store, workflow_store=journal, workflow_id_factory=lambda: workflow
    )

    def put(value, name, schema=None, media="application/json"):
        path = tmp_path / name
        path.write_bytes(
            value
            if isinstance(value, bytes)
            else (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        return base.store.put_file(path, name=name, media_type=media, schema_version=schema)

    image = tmp_path / "raw.png"
    Image.new("RGB", (2, 2)).save(image)
    raw = base.store.put_file(image, name="raw.png", media_type="image/png", schema_version=None)
    prompt = put(
        b"Original objective with unparsed target",
        "prompt.txt",
        "harness.prompt_text.v1",
        "text/plain",
    )
    source = put(
        {
            "workflow_id": str(workflow),
            "source_media": {"source": raw.model_dump(mode="json")},
            "effective_prompt": prompt.model_dump(mode="json"),
        },
        "input.json",
    )
    fact = WorldFactEvidence(
        source_kind="user_input",
        key="request.preview_input",
        value=source.model_dump(mode="json"),
        observed_at=datetime.now(UTC),
        valid_until=None,
    )
    evidence = put(fact.model_dump(mode="json"), "fact.json", "harness.world_fact_evidence.v1")
    head = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="test/controller",
            idempotency_key="start",
            workspace="ephemeral",
            requested_profile="genesis.rigid_scene@1",
            initial_fact_evidence=(evidence,),
            registry_snapshot=base.head.registry_snapshot,
        )
    )
    read = RunReadRequest(
        schema_version="harness.run_read_request.v1",
        principal_id=head.principal_id,
        workflow_run_id=workflow,
    )

    class Service:
        store = base.store

        def __init__(self):
            self.journal = journal

        def status(self, *, workflow_id):
            assert workflow_id == str(workflow)
            snapshot = harness.read(read)
            return {
                "workflow_id": str(workflow),
                "revision": snapshot.revision,
                "stage": "status",
                "status": "active",
                "snapshot": snapshot.model_dump(mode="json"),
                "source_prompt": prompt.model_dump(mode="json"),
            }

        def start(self, **kwargs):
            return {**self.status(workflow_id=str(workflow)), "stage": "start"}

        def publish(self, **kwargs):
            pytest.fail("unvalidated backend result cannot publish")

    return SimpleNamespace(value=Service(), workflow=workflow, harness=harness, read=read)


@pytest.mark.parametrize("failure", ["timeout", "exit_failure", "invalid_transcript"])
def test_attempt_failure_stays_in_shared_database_and_resume_reads_original_input(
    service, tmp_path, failure
):
    class Backend:
        calls = []

        def run(self, assignment, *, output_root):
            self.calls.append(assignment)
            assert assignment.objective == "Original objective with unparsed target"
            assert not any(
                "publish" in action or "package" in action for action in assignment.allowed_actions
            )
            output_root.mkdir()
            (output_root / "stderr").write_bytes(b"external boundary fixture")
            return CodexBackendResult(failure, output_root, 1.0, 124, None, None, None, failure)

    backend = Backend()
    controller = LimitedImagePreviewController(
        service.value, backend, evidence_root=tmp_path / "attempts", max_attempts=2
    )
    before = service.harness.read(service.read)
    assert (
        controller.start(
            raw_image_path="ignored-external-service-fixture",
            user_text="ignored",
            idempotency_key="start",
        )["status"]
        == failure
    )
    reopened = LimitedImagePreviewController(
        service.value, backend, evidence_root=tmp_path / "attempts", max_attempts=2
    )
    assert reopened.resume(service.workflow)["status"] == failure
    assert reopened.resume(service.workflow)["status"] == "attempt_limit"
    rows = reopened.attempts(service.workflow)
    assert [row["number"] for row in rows] == [1, 2]
    assert all(row["status"] == "terminal" and row["evidence"] for row in rows)
    assert service.harness.read(service.read) == before


def test_completed_backend_without_validated_service_head_is_not_success(service, tmp_path):
    class Backend:
        def run(self, assignment, *, output_root):
            output_root.mkdir()
            return CodexBackendResult(
                "completed", output_root, 1.0, 0, None, None, {"calls": []}, None
            )

    controller = LimitedImagePreviewController(
        service.value, Backend(), evidence_root=tmp_path / "attempts"
    )
    assert controller.resume(service.workflow)["status"] == "waiting"


def test_interrupted_attempt_waits_for_exact_live_process_then_resumes(service, tmp_path):
    import os
    import signal
    import subprocess
    import sys

    from self_improving.harness.codex_backend import process_start_ticks

    class Backend:
        executable_sha = "e" * 64
        count = 0
        child = None

        def run(self, assignment, *, output_root):
            self.count += 1
            output_root.mkdir()
            if self.count == 1:
                self.child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    cwd=output_root,
                    start_new_session=True,
                )
                (output_root / "process.json").write_text(
                    json.dumps(
                        {
                            "pid": self.child.pid,
                            "start_ticks": process_start_ticks(self.child.pid),
                            "deadline": assignment.deadline.isoformat(),
                            "executable_sha256": self.executable_sha,
                            "output_root": str(output_root),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                raise KeyboardInterrupt("external controller interruption fixture")
            return CodexBackendResult(
                "exit_failure", output_root, 0.1, 17, None, None, None, "fixture"
            )

    backend = Backend()
    controller = LimitedImagePreviewController(
        service.value, backend, evidence_root=tmp_path / "attempts"
    )
    try:
        with pytest.raises(KeyboardInterrupt):
            controller.resume(service.workflow)
        assert controller.resume(service.workflow)["status"] == "running"
        assert backend.count == 1
        os.killpg(backend.child.pid, signal.SIGTERM)
        backend.child.wait(timeout=5)
        assert controller.resume(service.workflow)["status"] == "exit_failure"
        assert controller.attempts(service.workflow)[0]["backend_status"] == "interrupted"
        assert backend.count == 2
    finally:
        if backend.child is not None and backend.child.poll() is None:
            os.killpg(backend.child.pid, signal.SIGKILL)
            backend.child.wait(timeout=5)


@pytest.mark.parametrize(
    "backend,transport,child,valid",
    [
        (2.0, 1.0, 0.5, True),
        (1.0, 2.0, 0.5, False),
        (2.0, 1.0, 2.0, False),
        (2.0, 1.0, None, False),
    ],
)
def test_timing_retains_samples_and_rejects_missing_or_overlapping(
    backend, transport, child, valid, tmp_path
):
    output = {"stage": "replay"}
    if child is not None:
        output["elapsed_seconds"] = child
    result = CodexBackendResult(
        "completed",
        tmp_path,
        backend,
        0,
        None,
        None,
        {
            "calls": [
                {
                    "tool": "image2env_replay_v2_0_0",
                    "elapsed_seconds": transport,
                    "result": {"structuredContent": output},
                }
            ]
        },
        None,
    )
    values = timing_samples(result)
    assert values["valid"] is valid
    assert values["calls"][0]["service_elapsed_ns"] == (
        None if child is None else round(child * 1e9)
    )


def test_controller_exposes_harness_start_and_resume():
    assert callable(LimitedImagePreviewController.start)
    assert callable(LimitedImagePreviewController.resume)
