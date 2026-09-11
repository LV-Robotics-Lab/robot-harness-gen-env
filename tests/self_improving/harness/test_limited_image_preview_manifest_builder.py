from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_store import SQLiteGoldenRunStore, StoredWorkflowEntry
from self_improving.harness.limited_image_preview_manifest_builder import (
    LimitedImagePreviewManifestBuildError,
    _timings,
    build_limited_image_preview_manifest,
)


def test_manifest_builder_requires_exact_shared_authorities(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    journal = SQLiteGoldenRunStore(tmp_path / "golden.sqlite3")

    with pytest.raises(TypeError, match="exact shared Golden and CAS authorities"):
        build_limited_image_preview_manifest(
            artifact_store=object(),
            workflow_store=journal,
            snapshot=object(),
            input_record=object(),
            lookup=object(),
            project_id="preview-test",
            timing_samples=(),
        )
    with pytest.raises(TypeError, match="exact shared Golden and CAS authorities"):
        build_limited_image_preview_manifest(
            artifact_store=store,
            workflow_store=object(),
            snapshot=object(),
            input_record=object(),
            lookup=object(),
            project_id="preview-test",
            timing_samples=(),
        )


def test_manifest_timings_are_exact_nonoverlapping_controller_measurements() -> None:
    workflow_id = UUID("10000000-0000-4000-8000-000000000001")
    actions = (
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
        "genesis.observe@3.0.0",
        "image2env.validate@3.0.0",
    )
    entries = tuple(
        StoredWorkflowEntry(
            entry_id=UUID(f"20000000-0000-4000-8000-{index:012d}"),
            workflow_run_id=workflow_id,
            entry_kind="skill_operation",
            action_ref=action,
            idempotency_key=f"stage-{index}",
            request_sha256=f"{index}" * 64,
            base_revision=index - 1,
            operation=None,
            control=None,
        )
        for index, action in enumerate(actions, start=1)
    )
    calls = [
        _call("image2env_preview_status_v1", "start", 10, None),
        _call("image2env_preview_asset_lookup_v1", "asset_lookup", 20, None),
        _call("image2env_compile_v2_0_0", "compile", 100, 80),
        _call("image2env_replay_v2_0_0", "replay", 200, 180),
        _call("genesis_observe_v3_0_0", "observe", 100, 80),
        _call("image2env_validate_v3_0_0", "validate", 100, 90),
    ]
    sample = {
        "backend_elapsed_ns": 600,
        "calls": calls,
        "attribution_complete": True,
        "failure": None,
        "valid": True,
        "input_elapsed_ns": 50,
    }

    timings = _timings(entries, (sample,))

    assert {item.segment: item.duration_ns for item in timings} == {
        "input_asset": 50,
        "compile": 80,
        "runtime_integrity": 60,
        "simulation_render": 260,
        "cas_deep_read": 30,
        "codex_mcp": 70,
        "validate_publish": 100,
    }
    assert sum(item.duration_ns for item in timings) == 650

    wrong_stage = {
        **sample,
        "calls": [
            *calls[:-1],
            _call("image2env_validate_v3_0_0", "replay", 100, 90),
        ],
    }
    with pytest.raises(LimitedImagePreviewManifestBuildError) as stage_error:
        _timings(entries, (wrong_stage,))
    assert stage_error.value.reason == "timing_stage"

    overlap = {**sample, "backend_elapsed_ns": 500}
    with pytest.raises(LimitedImagePreviewManifestBuildError) as overlap_error:
        _timings(entries, (overlap,))
    assert overlap_error.value.reason == "timing_overlap"


def test_failed_attempt_wall_is_retained_before_a_complete_successful_attempt() -> None:
    workflow_id = UUID("10000000-0000-4000-8000-000000000002")
    actions = (
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
        "genesis.observe@3.0.0",
        "image2env.validate@3.0.0",
    )
    entries = tuple(
        StoredWorkflowEntry(
            entry_id=UUID(f"30000000-0000-4000-8000-{index:012d}"),
            workflow_run_id=workflow_id,
            entry_kind="skill_operation",
            action_ref=action,
            idempotency_key=f"stage-{index}",
            request_sha256=f"{index}" * 64,
            base_revision=index - 1,
            operation=None,
            control=None,
        )
        for index, action in enumerate(actions, start=1)
    )
    failed = {
        "backend_elapsed_ns": 250,
        "calls": [],
        "attribution_complete": False,
        "failure": "timeout",
        "valid": True,
        "input_elapsed_ns": 50,
        "wall_basis": "controller_started_to_observed_owned_exit",
    }
    completed = {
        "backend_elapsed_ns": 600,
        "calls": [
            _call("image2env_preview_status_v1", "start", 10, None),
            _call("image2env_preview_asset_lookup_v1", "asset_lookup", 20, None),
            _call("image2env_compile_v2_0_0", "compile", 100, 80),
            _call("image2env_replay_v2_0_0", "replay", 200, 180),
            _call("genesis_observe_v3_0_0", "observe", 100, 80),
            _call("image2env_validate_v3_0_0", "validate", 100, 90),
        ],
        "attribution_complete": True,
        "failure": None,
        "valid": True,
        "input_elapsed_ns": None,
    }

    timings = _timings(entries, (failed, completed))

    by_segment = {item.segment: item.duration_ns for item in timings}
    assert by_segment["codex_mcp"] == 320
    assert by_segment["input_asset"] == 50
    assert sum(by_segment.values()) == 900


def _call(tool: str, stage: str, transport: int, service: int | None) -> dict[str, object]:
    return {
        "tool": tool,
        "stage": stage,
        "transport_elapsed_ns": transport,
        "service_elapsed_ns": service,
    }
