from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_store import SQLiteGoldenRunStore, StoredWorkflowEntry
from self_improving.harness.limited_image_preview_manifest_builder import (
    LimitedImagePreviewManifestBuildError,
    _repair_actions,
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

    persisted_read_only_zero = {
        **sample,
        "calls": [
            _call("image2env_preview_status_v1", "status", 10, 0),
            _call("image2env_preview_asset_lookup_v1", "asset_lookup", 20, 0),
            *calls[2:],
        ],
    }
    assert _timings(entries, (persisted_read_only_zero,)) == timings

    read_only_positive = {
        **sample,
        "calls": [
            _call("image2env_preview_status_v1", "status", 10, 1),
            *calls[1:],
        ],
    }
    with pytest.raises(LimitedImagePreviewManifestBuildError) as read_only_error:
        _timings(entries, (read_only_positive,))
    assert read_only_error.value.reason == "timing_stage"

    business_zero = {
        **sample,
        "calls": [*calls[:2], _call("image2env_compile_v2_0_0", "compile", 100, 0), *calls[3:]],
    }
    with pytest.raises(LimitedImagePreviewManifestBuildError) as business_error:
        _timings(entries, (business_zero,))
    assert business_error.value.reason in {"timing_missing", "timing_value"}

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

    invalid = {**sample, "valid": False}
    with pytest.raises(LimitedImagePreviewManifestBuildError) as invalid_error:
        _timings(entries, (invalid,))
    assert invalid_error.value.reason == "timing_incomplete"

    negative = {**sample, "backend_elapsed_ns": -1}
    with pytest.raises(LimitedImagePreviewManifestBuildError) as negative_error:
        _timings(entries, (negative,))
    assert negative_error.value.reason == "timing_value"


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


def test_complete_failed_attempt_calls_combine_with_retry_without_losing_lineage() -> None:
    workflow_id = UUID("10000000-0000-4000-8000-000000000004")
    actions = (
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
        "genesis.observe@3.0.0",
        "image2env.validate@3.0.0",
    )
    entries = tuple(
        StoredWorkflowEntry(
            entry_id=UUID(f"50000000-0000-4000-8000-{index:012d}"),
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
    failed_after_compile = {
        "backend_elapsed_ns": 200,
        "calls": [
            _call("image2env_preview_status_v1", "start", 10, None),
            _call("image2env_preview_asset_lookup_v1", "asset_lookup", 10, None),
            _call("image2env_compile_v2_0_0", "compile", 100, 80),
        ],
        "attribution_complete": True,
        "failure": "invalid_transcript",
        "valid": True,
        "input_elapsed_ns": 50,
    }
    completed_retry = {
        "backend_elapsed_ns": 500,
        "calls": [
            _call("image2env_preview_status_v1", "compile", 10, None),
            _call("image2env_replay_v2_0_0", "replay", 200, 180),
            _call("genesis_observe_v3_0_0", "observe", 100, 80),
            _call("image2env_validate_v3_0_0", "validate", 100, 90),
        ],
        "attribution_complete": True,
        "failure": None,
        "valid": True,
        "input_elapsed_ns": None,
    }

    timings = _timings(entries, (failed_after_compile, completed_retry))

    by_segment = {item.segment: item.duration_ns for item in timings}
    assert by_segment == {
        "input_asset": 50,
        "compile": 80,
        "runtime_integrity": 60,
        "simulation_render": 260,
        "cas_deep_read": 30,
        "codex_mcp": 170,
        "validate_publish": 100,
    }
    assert sum(by_segment.values()) == 700 + 50

    incomplete_after_compile = {
        **failed_after_compile,
        "calls": [],
        "attribution_complete": False,
    }
    with pytest.raises(LimitedImagePreviewManifestBuildError) as error:
        _timings(entries, (incomplete_after_compile, completed_retry))
    assert error.value.reason == "timing_lineage"


def test_repair_timing_follows_mismatch_then_diagnostic_observation() -> None:
    workflow_id = UUID("10000000-0000-4000-8000-000000000003")
    actions = (
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
        "image2env.validate@3.0.0",
        "genesis.observe@3.0.0",
        "advisory",
        "prompt_revision",
        "image2env.compile@3.0.0",
        "image2env.replay@3.0.0",
        "image2env.validate@3.0.0",
    )
    entries = tuple(
        StoredWorkflowEntry(
            entry_id=UUID(f"40000000-0000-4000-8000-{index:012d}"),
            workflow_run_id=workflow_id,
            entry_kind="control_record"
            if action in {"advisory", "prompt_revision"}
            else "skill_operation",
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
        _call("image2env_preview_asset_lookup_v1", "asset_lookup", 10, None),
        _call("image2env_compile_v2_0_0", "compile", 20, 15),
        _call("image2env_replay_v2_0_0", "replay", 30, 25),
        _call("image2env_validate_v3_0_0", "validate", 20, 15),
        _call("genesis_observe_v3_0_0", "observe", 30, 25),
        _call("harness_advisory_record_v2", None, 10, None),
        _call("harness_prompt_revision_record_v3", None, 10, None),
        _call("image2env_compile_v3_0_0", "compile", 20, 15),
        _call("image2env_replay_v3_0_0", "replay", 30, 25),
        _call("image2env_validate_v3_0_0", "validate", 20, 15),
    ]

    _repair_actions(actions)
    timings = _timings(
        entries,
        (
            {
                "backend_elapsed_ns": 310,
                "calls": calls,
                "attribution_complete": True,
                "failure": None,
                "valid": True,
                "input_elapsed_ns": 10,
            },
        ),
    )

    assert sum(item.duration_ns for item in timings) == 320
    _repair_actions((*actions[:4], "genesis.observe@3.0.0", *actions[4:]))


def _call(tool: str, stage: str | None, transport: int, service: int | None) -> dict[str, object]:
    return {
        "tool": tool,
        "stage": stage,
        "transport_elapsed_ns": transport,
        "service_elapsed_ns": service,
    }
