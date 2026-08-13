from scripts.sync_agenticsim_isaac_evidence import DEFAULT_OUTPUT, build_snapshot


def test_agenticsim_isaac_snapshot_matches_source() -> None:
    checked = build_snapshot()
    persisted = __import__("json").loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))

    assert checked == persisted
    assert checked["catalog_summary"]["repository_count"] == 745
    assert checked["catalog_summary"]["verified_open_source_count"] == 308
    assert checked["runtime_summary"]["repository_probe_count"] == 12
    assert checked["runtime_summary"]["runtime_pass_count"] == 11
    assert checked["runtime_summary"]["runtime_blocked_count"] == 1
    assert checked["runtime_summary"]["strict_open_source_runtime_pass_count"] == 6
    assert (
        checked["runtime_summary"]["runtime_pass_without_open_source_closure_count"]
        == 5
    )
    assert checked["runtime_summary"]["academic_use_runtime_accepted_count"] == 11
    assert checked["runtime_summary"]["academic_use_runtime_blocked_count"] == 1
    assert checked["runtime_summary"]["academic_use_license_advisory_count"] == 5
    assert checked["usage_policy"]["policy_id"] == ("noncommercial_academic_local_use")
    current = checked["current_runtime_rows"]
    assert sum(row["academic_use_accepted"] for row in current) == 11
    assert (
        sum(
            row["academic_use_license_advisory"] != "none"
            for row in current
            if row["academic_use_accepted"]
        )
        == 5
    )
    assert checked["runtime_baseline"]["video_evidence"]["frame_count"] == 32
