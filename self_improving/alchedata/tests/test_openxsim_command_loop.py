from scripts.openxsim_command_loop import (
    ADAPTER_MATRIX,
    COMMAND_REGISTRY,
    REQUIRED_ADAPTER_COLUMNS,
    REQUIRED_COMMANDS,
    read_json,
    validate_openxsim_package,
)


def test_openxsim_command_loop_package_passes() -> None:
    report = validate_openxsim_package()

    assert report["status"] == "pass_openxsim_command_loop_package"
    assert report["acceptance_items"] == 8
    assert report["commands"] == 6
    assert report["adapters"] == 10
    assert report["benchmarks"] == 3
    assert report["dense_categories"] == 6
    assert report["fallback_gate_rows"] == 14
    assert report["isaac_runtime_passes"] == 11
    assert report["isaac_runtime_blocked"] == 1
    assert report["isaac_academic_use_accepted"] == 11
    assert report["isaac_license_advisories"] == 5
    assert report["video_unique_decoded_frames"] == 1730
    assert report["isaac_command_bundle"]["status"] == "pass_isaac_full_command_bundle"
    assert report["isaac_command_bundle"]["command_count"] == 5
    assert report["isaac_command_bundle"]["task_success"] is True
    assert report["isaac_command_bundle"]["unique_video_frames"] == 24


def test_every_command_has_failures_and_current_evidence() -> None:
    registry = read_json(COMMAND_REGISTRY)

    assert {row["command"] for row in registry["commands"]} == REQUIRED_COMMANDS
    assert all(row["failure_codes"] for row in registry["commands"])
    assert all(row["current_evidence"] for row in registry["commands"])


def test_adapter_matrix_has_all_required_columns() -> None:
    matrix = read_json(ADAPTER_MATRIX)

    assert matrix["adapter_count"] == 10
    assert all(REQUIRED_ADAPTER_COLUMNS.issubset(row) for row in matrix["adapters"])
