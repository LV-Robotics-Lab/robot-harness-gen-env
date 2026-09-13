from __future__ import annotations

import pytest

from self_improving.compile_acceptance import (
    CampaignAcceptanceError,
    build_acceptance_plan,
    validate_campaign_summary,
)


def test_acceptance_plan_is_reproducible_and_covers_requested_groups() -> None:
    first = build_acceptance_plan(seed=20260906)
    second = build_acceptance_plan(seed=20260906)

    assert first == second
    assert len(first) == 20
    assert [case.group for case in first].count("fresh") == 10
    assert [case.group for case in first].count("existing_reuse") == 5
    assert [case.group for case in first].count("digital_cousins") == 5
    assert len({case.case_id for case in first}) == 20
    assert len({case.request for case in first[:10]}) == 10
    assert all(case.source_case_id is None for case in first[:10])
    assert [case.source_case_id for case in first[10:15]] == [
        "fresh-01",
        "fresh-02",
        "fresh-03",
        "fresh-04",
        "fresh-05",
    ]
    assert all(
        case.expected_origin == "accepted_library_task_affordance_cousin"
        for case in first[15:]
    )
    assert [case.source_case_id for case in first[15:]] == [
        "fresh-06",
        "fresh-07",
        "fresh-08",
        "fresh-09",
        "fresh-10",
    ]
    assert all(
        case.request != first[index + 5].request
        for index, case in enumerate(first[15:])
    )


def test_acceptance_plan_changes_with_campaign_seed() -> None:
    first = build_acceptance_plan(seed=20260906)
    second = build_acceptance_plan(seed=20260907)

    assert [case.seed for case in first] != [case.seed for case in second]
    assert [case.request for case in first[:10]] != [case.request for case in second[:10]]


def test_digital_cousin_requests_reuse_shape_without_claiming_wrong_appearance() -> None:
    plan = build_acceptance_plan(seed=20260906)
    by_id = {case.case_id: case for case in plan}

    for case in plan[15:]:
        source = by_id[case.source_case_id]
        source_words = (
            source.request.removeprefix("Place a ").removesuffix(" on the table.").split()
        )
        shape = " ".join(source_words[2:])

        assert case.request == f"Place a {shape} on the table."
        assert source_words[0] not in case.request
        assert source_words[1] not in case.request


def _complete_record(case_id: str, group: str) -> dict[str, object]:
    record: dict[str, object] = {
        "case": {"case_id": case_id, "group": group},
        "status": "succeeded",
        "selected_asset_ids": [f"asset-{case_id}"],
        "environment": {
            "package_id": f"package-{case_id}",
            "resolved_scene_sha256": "a" * 64,
            "materialized_root": f"/evidence/{case_id}/environment",
            "load_smoke": {
                "status": "loaded",
                "resolved_scene_sha256": "a" * 64,
            },
        },
        "media": {
            "kind": "compiled_environment_preview",
            "resolved_scene_sha256": "a" * 64,
            "preview": f"/evidence/{case_id}/preview.png",
            "video": f"/evidence/{case_id}/preview.mp4",
        },
        "portable_receipt_verified_from_destination_cas_only": True,
    }
    if group == "existing_reuse":
        record["reuse_verification"] = {"same_asset_id": True}
    if group == "digital_cousins":
        record["digital_cousin_selection"] = {
            "status": "selected",
            "source_case_id": "fresh-06",
            "selected_asset_id": f"asset-{case_id}",
            "exact_request_match": False,
        }
    return record


def _complete_summary() -> dict[str, object]:
    records = [
        *[_complete_record(f"fresh-{index:02d}", "fresh") for index in range(1, 11)],
        *[_complete_record(f"reuse-{index:02d}", "existing_reuse") for index in range(1, 6)],
        *[
            _complete_record(f"digital-cousins-{index:02d}", "digital_cousins")
            for index in range(1, 6)
        ],
    ]
    return {"cases": records}


def test_campaign_acceptance_requires_environment_media_not_asset_turntables() -> None:
    summary = _complete_summary()
    summary["cases"][0]["media"]["kind"] = "asset_turntable_preview_not_physical_replay"

    with pytest.raises(CampaignAcceptanceError, match="compiled environment preview"):
        validate_campaign_summary(summary)


def test_campaign_acceptance_requires_five_real_digital_cousin_selections() -> None:
    summary = _complete_summary()
    summary["cases"][-1]["digital_cousin_selection"] = None

    with pytest.raises(CampaignAcceptanceError, match="Digital Cousin"):
        validate_campaign_summary(summary)


def test_complete_environment_campaign_is_accepted() -> None:
    assert validate_campaign_summary(_complete_summary()) is None
