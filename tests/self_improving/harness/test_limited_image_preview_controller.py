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


@pytest.mark.parametrize("capture_state", ["fresh", "expired", "missing"])
@pytest.mark.parametrize("phase", ["diagnosis", "repair"])
def test_diagnosis_uses_fresh_capture_and_its_deadline(service, tmp_path, capture_state, phase):
    from datetime import timedelta

    from self_improving.harness.schemas.genesis_fresh_capture import GenesisFreshCaptureReceiptV1

    original = service.value
    projected = original.status(workflow_id=str(service.workflow))
    projected["failure_receipts"] = [projected["snapshot"]["receipt_head"]]
    if phase == "repair":
        # The external service boundary supplies its already-committed control head.
        path = tmp_path / "committed-revision.json"
        path.write_text(
            json.dumps(
                {
                    "workflow_run_id": str(service.workflow),
                    "predecessor_receipt": projected["snapshot"]["receipt_head"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        revision = original.store.put_file(
            path,
            name=path.name,
            media_type="application/json",
            schema_version="harness.prompt_revision.v3",
        )
        projected["snapshot"]["receipt_head"] = revision.model_dump(mode="json")
    captured = datetime.now(UTC) - timedelta(seconds=90 if capture_state == "expired" else 0)
    image_path = tmp_path / "actual-observation.png"
    Image.new("RGB", (640, 480), (200, 30, 50)).save(image_path)
    image = original.store.put_file(
        image_path, name=image_path.name, media_type="image/png", schema_version=None
    )
    receipt = GenesisFreshCaptureReceiptV1(
        schema_version="harness.genesis_fresh_capture_receipt.v1",
        replay_run_id=uuid4(),
        replay_receipt=image,
        request=image,
        capture_result=image,
        image=image,
        captured_at=captured,
        checked_at=captured,
        expires_at=captured + timedelta(seconds=60),
        ttl_seconds=60,
        authority="observed_restored_final_pose",
        process_continuation=False,
    )
    path = tmp_path / "actual-capture.json"
    path.write_bytes(
        json.dumps(receipt.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    )
    ref = original.store.put_file(
        path, name=path.name, media_type="application/json", schema_version=receipt.schema_version
    )
    if capture_state != "missing":
        projected["observation"] = dict(
            receipt=ref.model_dump(mode="json"),
            image=image.model_dump(mode="json"),
            captured_at=receipt.captured_at.isoformat(),
            expires_at=receipt.expires_at.isoformat(),
            authority=receipt.authority,
        )

    class Boundary:
        store = original.store
        journal = original.journal

        def status(self, **kwargs):
            return projected

    class Backend:
        calls = []

        def run(self, assignment, *, output_root):
            self.calls.append(assignment)
            if phase == "diagnosis":
                assert assignment.image_path.read_bytes() == image_path.read_bytes()
                assert assignment.deadline <= receipt.expires_at
            else:
                assert assignment.image_path.read_bytes() == (tmp_path / "raw.png").read_bytes()
                assert assignment.deadline > receipt.expires_at
                assert assignment.deadline > datetime.now(UTC) + timedelta(seconds=290)
            assert assignment.phase == phase
            output_root.mkdir()
            return CodexBackendResult(
                "exit_failure", output_root, 0.01, 1, None, None, None, "test"
            )

    backend = Backend()
    controller = LimitedImagePreviewController(
        Boundary(), backend, evidence_root=tmp_path / "attempts", attempt_seconds=300
    )
    if capture_state == "fresh" or phase == "repair":
        assert controller.resume(service.workflow)["status"] == "exit_failure"
        assert len(backend.calls) == 1
    else:
        with pytest.raises(ValueError, match="fresh observation"):
            controller.resume(service.workflow)
        assert not backend.calls


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


def test_public_attempt_reader_does_not_create_database_and_reuses_record_checks(service, tmp_path):
    from self_improving.harness.limited_image_preview_controller import read_codex_attempts

    missing = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError, match="existing Golden database"):
        read_codex_attempts(missing, service.workflow)
    assert not missing.exists()
    original_bytes = service.value.journal.path.read_bytes()
    assert read_codex_attempts(service.value.journal.path, service.workflow) == []
    assert service.value.journal.path.read_bytes() == original_bytes
    controller = LimitedImagePreviewController(
        service.value, object(), evidence_root=tmp_path / "attempts"
    )
    assert read_codex_attempts(service.value.journal.path, service.workflow) == []
    assert controller.attempts(service.workflow) == []


def test_readonly_attempt_reader_rejects_a_self_consistent_missing_number(service, tmp_path):
    import hashlib
    import sqlite3

    from self_improving.harness.limited_image_preview_controller import read_codex_attempts

    LimitedImagePreviewController(service.value, object(), evidence_root=tmp_path / "attempts")
    payload = json.dumps(
        {"workflow_id": str(service.workflow), "number": 2}, sort_keys=True, separators=(",", ":")
    ).encode()
    with sqlite3.connect(service.value.journal.path) as connection:
        connection.execute(
            "INSERT INTO codex_preview_attempts VALUES(?,?,?,?)",
            (str(service.workflow), 2, payload, hashlib.sha256(payload).hexdigest()),
        )
    before = service.value.journal.path.read_bytes()
    with pytest.raises(ValueError, match="integrity"):
        read_codex_attempts(service.value.journal.path, service.workflow)
    assert service.value.journal.path.read_bytes() == before


def test_completed_backend_without_validated_service_head_is_not_success(service, tmp_path):
    class Backend:
        def run(self, assignment, *, output_root):
            output_root.mkdir()
            return CodexBackendResult(
                "completed",
                output_root,
                1.0,
                0,
                None,
                None,
                {
                    "calls": [],
                    "terminal_decision": {
                        "workflow_id": str(service.workflow),
                        "decision": "phase_complete",
                        "reason": "done",
                    },
                },
                None,
            )

    controller = LimitedImagePreviewController(
        service.value, Backend(), evidence_root=tmp_path / "attempts"
    )
    assert controller.resume(service.workflow)["status"] == "waiting"


def test_explicit_unsupported_is_terminal_diagnostic_without_workflow_change(service, tmp_path):
    class Backend:
        count = 0

        def run(self, assignment, *, output_root):
            self.count += 1
            output_root.mkdir()
            return CodexBackendResult(
                "completed",
                output_root,
                1.0,
                0,
                None,
                None,
                {
                    "calls": [],
                    "terminal_decision": {
                        "workflow_id": str(service.workflow),
                        "decision": "unsupported",
                        "reason": "Only one mouse on a finite table is supported.",
                    },
                },
                None,
            )

    backend = Backend()
    controller = LimitedImagePreviewController(
        service.value, backend, evidence_root=tmp_path / "attempts"
    )
    before = service.harness.read(service.read)
    assert controller.run_to_terminal(service.workflow)["status"] == "unsupported"
    assert controller.resume(service.workflow)["status"] == "unsupported"
    assert backend.count == 1
    assert (
        controller.attempts(service.workflow)[0]["terminal_decision"]["decision"] == "unsupported"
    )
    assert service.harness.read(service.read) == before


@pytest.mark.parametrize(
    "decision",
    [
        None,
        {"decision": "success"},
        {
            "workflow_id": "foreign",
            "decision": "unsupported",
            "reason": "wrong workflow",
        },
    ],
)
def test_invalid_or_missing_terminal_decision_retries_only_within_budget(
    service, tmp_path, decision
):
    class Backend:
        count = 0

        def run(self, assignment, *, output_root):
            self.count += 1
            output_root.mkdir()
            return CodexBackendResult(
                "completed",
                output_root,
                1.0,
                0,
                None,
                None,
                {"calls": [], "terminal_decision": decision},
                None,
            )

    backend = Backend()
    controller = LimitedImagePreviewController(
        service.value, backend, evidence_root=tmp_path / "attempts", max_attempts=2
    )
    before = service.harness.read(service.read)
    assert controller.run_to_terminal(service.workflow)["status"] == "attempt_limit"
    assert backend.count == 2
    assert all(
        row["backend_status"] == "invalid_transcript"
        for row in controller.attempts(service.workflow)
    )
    assert service.harness.read(service.read) == before


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
        (2.0, 1.0, 0.0, False),
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


@pytest.mark.parametrize(
    ("tool", "stage"),
    [
        ("image2env_preview_status_v1", "status"),
        ("image2env_preview_asset_lookup_v1", "asset_lookup"),
    ],
)
def test_timing_normalizes_read_only_projection_default_to_no_child_service(
    service, tmp_path, tool, stage
):
    from self_improving.harness.schemas.limited_image_preview_service import PreviewServiceResult

    projection = service.value.status(workflow_id=str(service.workflow))
    projection["stage"] = stage
    output = PreviewServiceResult.model_validate(projection).model_dump(mode="json")
    assert output["elapsed_seconds"] == 0.0
    result = CodexBackendResult(
        "completed",
        tmp_path,
        2.0,
        0,
        None,
        None,
        {
            "calls": [
                {
                    "tool": tool,
                    "elapsed_seconds": 1.0,
                    "result": {"structuredContent": output},
                }
            ]
        },
        None,
    )

    values = timing_samples(result)

    assert values["valid"] is True
    assert values["calls"][0] == {
        "tool": tool,
        "stage": stage,
        "transport_elapsed_ns": 1_000_000_000,
        "service_elapsed_ns": None,
    }


def test_failed_attempt_keeps_measured_wall_without_inventing_attribution(tmp_path):
    result = CodexBackendResult("timeout", tmp_path, 2.0, 124, None, None, None, "deadline")
    samples = timing_samples(result)
    assert samples["valid"] is True
    assert samples["attribution_complete"] is False
    assert samples["backend_elapsed_ns"] == 2_000_000_000
    assert samples["calls"] == []


def test_failed_attempt_keeps_complete_transport_call_attribution(tmp_path):
    result = CodexBackendResult(
        "exit_failure",
        tmp_path,
        2.0,
        17,
        None,
        None,
        {
            "calls": [
                {
                    "tool": "image2env_compile_v2_0_0",
                    "elapsed_seconds": 1.0,
                    "result": {"structuredContent": {"stage": "compile", "elapsed_seconds": 0.5}},
                }
            ],
            "attribution_complete": True,
        },
        "exit_failure",
    )

    samples = timing_samples(result)

    assert samples == {
        "backend_elapsed_ns": 2_000_000_000,
        "calls": [
            {
                "tool": "image2env_compile_v2_0_0",
                "stage": "compile",
                "transport_elapsed_ns": 1_000_000_000,
                "service_elapsed_ns": 500_000_000,
            }
        ],
        "attribution_complete": True,
        "failure": "exit_failure",
        "valid": True,
    }


def test_controller_exposes_harness_start_and_resume():
    assert callable(LimitedImagePreviewController.start)
    assert callable(LimitedImagePreviewController.resume)


def test_validated_development_record_is_a_stable_terminal_without_more_codex(service, tmp_path):
    projected = service.value.status(workflow_id=str(service.workflow))
    projected.update(
        status="validated_unreleased",
        execution_mode="development",
        qualification_status="development_unqualified",
        package=projected["source_prompt"],
    )

    service.value.status = lambda **kwargs: projected
    service.value.package_get = lambda **kwargs: {
        **projected,
        "stage": "package_get",
    }

    class Backend:
        def run(self, assignment, *, output_root):
            raise AssertionError("a recovered development terminal must not call Codex")

    controller = LimitedImagePreviewController(
        service.value, Backend(), evidence_root=tmp_path / "attempts"
    )
    assert controller.resume(service.workflow) == {
        "status": "validated_unreleased",
        "workflow_id": str(service.workflow),
        "package": projected["source_prompt"],
    }
