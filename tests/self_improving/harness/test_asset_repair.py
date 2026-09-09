"""Public S5 tracer tests for read-only asset debt planning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from self_improving.harness import AssetRepairApplication as PublicAssetRepairApplication
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.asset_repair import AssetRepairApplication, AssetRepairError
from self_improving.harness.schemas.asset_repair import (
    AssetRepairPlan,
    AssetRepairPlanRequest,
)

FIXTURE = Path(__file__).parents[2] / "fixtures" / "asset_repair_tracer_inventory.json"


def _put_inventory(store: LocalArtifactStore, tmp_path: Path, payload: dict):
    source = tmp_path / "inventory.json"
    source.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return store.put_file(
        source,
        name="inventory.json",
        media_type="application/json",
        schema_version="harness.asset_debt_inventory.v1",
    )


def _application(store: LocalArtifactStore, *trusted_inventory_refs):
    return AssetRepairApplication(
        artifact_store=store,
        trusted_inventory_refs=trusted_inventory_refs,
    )


def _single_plate_inventory() -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["entries"] = payload["entries"][:1]
    return payload


def test_asset_repair_application_is_exposed_by_the_public_facade() -> None:
    assert PublicAssetRepairApplication is AssetRepairApplication


def test_plan_classifies_real_plate_and_can_debt_without_writing(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    inventory_ref = store.put_file(
        FIXTURE,
        name="asset_repair_tracer_inventory.json",
        media_type="application/json",
        schema_version="harness.asset_debt_inventory.v1",
    )
    assert inventory_ref.sha256 == (
        "8e54c0d52d16ed5d9de19c55aa37e81012cb28ca6b25443a4285ce1b0daf94e2"
    )
    assert inventory_ref.bytes == 5736
    before = tuple(path.relative_to(store.root) for path in sorted(store.root.rglob("*")))
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate", "robotwin_071_can"),
    )

    plan = _application(store, inventory_ref).plan(request)

    assert plan.schema_version == "harness.asset_repair_plan.v1"
    assert plan.inventory_ref == inventory_ref
    assert plan.inventory_scope == "structural_tracer"
    assert plan.selected_asset_ids == ("robotwin_003_plate", "robotwin_071_can")
    assert plan.planned_ledger_count == 2
    assert plan.planned_violation_count == 58
    assert plan.planned_representation_count == 14
    assert plan.disposition_counts.model_dump() == {
        "observed_primary_digest_match": 14,
        "observed_primary_digest_match_remeasure_size": 0,
        "hash_mismatch_new_identity_or_retire": 0,
        "source_missing_new_identity_or_retire": 0,
    }
    assert [(item.asset_id, item.violation_count, item.next_step) for item in plan.items] == [
        ("robotwin_003_plate", 9, "stage_and_rehash_observed_primary"),
        ("robotwin_071_can", 49, "stage_and_rehash_observed_primary"),
    ]
    assert all(
        representation.disposition == "observed_primary_digest_match"
        for item in plan.items
        for representation in item.representations
    )
    assert all(
        item.required_followup
        == (
            "stage_and_rehash_bytes",
            "enumerate_loader_closure",
            "qualify_collision_provenance",
            "qualify_genesis_runtime",
            "qualify_settle",
        )
        for item in plan.items
    )
    assert plan.full_baseline_evaluated is False
    assert plan.probe_bytes_in_cas is False
    assert plan.runtime_qualification_executed is False
    assert plan.writes_performed is False
    after = tuple(path.relative_to(store.root) for path in sorted(store.root.rglob("*")))
    assert after == before


def test_plan_marks_exact_hash_with_stale_size_for_remeasurement(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = _single_plate_inventory()
    payload["entries"][0]["recovery_probes"][0]["observed_bytes"] += 1
    inventory_ref = _put_inventory(store, tmp_path, payload)
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )

    plan = _application(store, inventory_ref).plan(request)

    assert [item.disposition for item in plan.items[0].representations] == [
        "observed_primary_digest_match_remeasure_size",
        "observed_primary_digest_match",
    ]
    assert plan.items[0].next_step == "stage_and_rehash_observed_primary_remeasure_size"


def test_plan_requires_new_identity_when_recovered_hash_differs(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = _single_plate_inventory()
    payload["entries"][0]["recovery_probes"][0]["observed_sha256"] = "f" * 64
    inventory_ref = _put_inventory(store, tmp_path, payload)
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )

    plan = _application(store, inventory_ref).plan(request)

    assert (
        plan.items[0].representations[0].disposition
        == "hash_mismatch_new_identity_or_retire"
    )
    assert plan.items[0].next_step == "build_new_identity_or_retire_asset"


def test_plan_requires_new_identity_or_retirement_when_bytes_are_missing(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = _single_plate_inventory()
    probe = payload["entries"][0]["recovery_probes"][0]
    probe.update(availability="missing", observed_sha256=None, observed_bytes=None)
    inventory_ref = _put_inventory(store, tmp_path, payload)
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )

    plan = _application(store, inventory_ref).plan(request)

    missing = plan.items[0].representations[0]
    assert missing.disposition == "source_missing_new_identity_or_retire"
    assert missing.observed_sha256 is None
    assert missing.observed_bytes is None
    assert plan.items[0].next_step == "build_new_identity_or_retire_asset"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"uri": "file:///tmp/inventory.json"}, "content-addressed artifact URI"),
        (
            {"uri": "artifact://sha256/" + "f" * 64},
            "content-addressed artifact URI",
        ),
        ({"media_type": "text/plain"}, "media_type='application/json'"),
        ({"schema_version": "harness.other.v1"}, "harness.asset_debt_inventory.v1"),
    ],
)
def test_plan_request_rejects_unbound_inventory_refs(
    tmp_path: Path,
    changes: dict[str, str],
    message: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    inventory_ref = _put_inventory(store, tmp_path, _single_plate_inventory())

    with pytest.raises(ValidationError, match=message):
        AssetRepairPlanRequest(
            schema_version="harness.asset_repair_plan_request.v1",
            inventory_ref=inventory_ref.model_copy(update=changes),
            selected_asset_ids=("robotwin_003_plate",),
        )


@pytest.mark.parametrize(
    "asset_ids",
    [
        ("robotwin_071_can", "robotwin_003_plate"),
        ("robotwin_003_plate", "robotwin_003_plate"),
    ],
)
def test_plan_request_rejects_ambiguous_asset_selection(
    tmp_path: Path,
    asset_ids: tuple[str, str],
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    inventory_ref = _put_inventory(store, tmp_path, _single_plate_inventory())

    with pytest.raises(ValidationError, match="sorted and unique"):
        AssetRepairPlanRequest(
            schema_version="harness.asset_repair_plan_request.v1",
            inventory_ref=inventory_ref,
            selected_asset_ids=asset_ids,
        )


def test_plan_rejects_assets_missing_from_the_bound_inventory(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    inventory_ref = _put_inventory(store, tmp_path, _single_plate_inventory())
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_999_missing",),
    )

    with pytest.raises(ValueError, match="not present in inventory: robotwin_999_missing"):
        _application(store, inventory_ref).plan(request)


@pytest.mark.parametrize("attack", ["duplicate", "unsorted"])
def test_plan_rejects_ambiguous_inventory_entries(tmp_path: Path, attack: str) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if attack == "duplicate":
        payload["entries"][1] = payload["entries"][0]
    else:
        payload["entries"].reverse()
    inventory_ref = _put_inventory(store, tmp_path, payload)
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate", "robotwin_071_can"),
    )

    with pytest.raises(AssetRepairError, match="entries must be sorted and unique"):
        _application(store, inventory_ref).plan(request)


@pytest.mark.parametrize(
    ("attack", "message"),
    [
        ("duplicate_violation", "violation_counts must be sorted and unique"),
        ("unsorted_violation", "violation_counts must be sorted and unique"),
        ("duplicate_probe", "recovery_probes must be sorted and unique"),
        ("unsorted_probe", "recovery_probes must be sorted and unique"),
    ],
)
def test_plan_rejects_ambiguous_debt_entry_details(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = _single_plate_inventory()
    entry = payload["entries"][0]
    if attack == "duplicate_violation":
        entry["violation_counts"][1] = entry["violation_counts"][0]
    elif attack == "unsorted_violation":
        entry["violation_counts"].reverse()
    elif attack == "duplicate_probe":
        entry["recovery_probes"][1] = entry["recovery_probes"][0]
    else:
        entry["recovery_probes"].reverse()
    inventory_ref = _put_inventory(store, tmp_path, payload)
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )

    with pytest.raises(AssetRepairError, match=message):
        _application(store, inventory_ref).plan(request)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"availability": "missing"}, "missing probes must omit observed identity"),
        ({"observed_sha256": None}, "available probes require observed identity"),
        ({"observed_bytes": None}, "available probes require observed identity"),
    ],
)
def test_plan_rejects_internally_conflicting_recovery_probes(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = _single_plate_inventory()
    payload["entries"][0]["recovery_probes"][0].update(changes)
    inventory_ref = _put_inventory(store, tmp_path, payload)
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )

    with pytest.raises(AssetRepairError, match=message):
        _application(store, inventory_ref).plan(request)


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("ledger", "/tmp/ledger.json"),
        ("ledger", "self_improving/../ledger.json"),
        ("probe", "/tmp/base0.glb"),
        ("probe", "objects/003_plate/../../base0.glb"),
        ("probe", r"C:\\assets\\base0.glb"),
        ("probe", "C:/assets/base0.glb"),
        ("probe", "C:base0.glb"),
        ("probe", "."),
    ],
)
def test_plan_rejects_nonportable_inventory_paths(
    tmp_path: Path,
    target: str,
    value: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = _single_plate_inventory()
    entry = payload["entries"][0]
    if target == "ledger":
        entry["ledger_path"] = value
    else:
        entry["recovery_probes"][0]["logical_path"] = value
    inventory_ref = _put_inventory(store, tmp_path, payload)
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )

    with pytest.raises(AssetRepairError, match="portable POSIX-relative path"):
        _application(store, inventory_ref).plan(request)


def test_plan_can_represent_a_trusted_full_baseline_without_runtime_claims(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["scope"] = "full_baseline"
    inventory_ref = _put_inventory(store, tmp_path, payload)
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )

    plan = _application(store, inventory_ref).plan(request)

    assert plan.inventory_scope == "full_baseline"
    assert plan.full_baseline_evaluated is True
    assert plan.runtime_qualification_executed is False
    assert plan.writes_performed is False


def test_plan_rejects_untrusted_inventory_even_when_it_is_valid_cas_json(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    inventory_ref = _put_inventory(store, tmp_path, _single_plate_inventory())
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )

    with pytest.raises(AssetRepairError, match="not in the trusted inventory set") as caught:
        _application(store).plan(request)
    assert caught.value.code == "HARN_UNTRUSTED_ASSET_DEBT_INVENTORY"


def test_plan_rejects_a_trusted_ref_when_its_cas_object_is_unavailable(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    inventory_ref = _put_inventory(store, tmp_path, _single_plate_inventory())
    store.resolve(inventory_ref).path.unlink()
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )

    with pytest.raises(AssetRepairError, match="inventory is unavailable") as caught:
        _application(store, inventory_ref).plan(request)
    assert caught.value.code == "HARN_ARTIFACT_UNAVAILABLE"


@pytest.mark.parametrize("attack", ["pretty", "duplicate_key"])
def test_plan_requires_strict_canonical_inventory_bytes(tmp_path: Path, attack: str) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = _single_plate_inventory()
    source = tmp_path / "inventory.json"
    if attack == "pretty":
        encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    else:
        canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        encoded = canonical[:-1] + ',"scope":"structural_tracer"}\n'
    source.write_text(encoded, encoding="utf-8")
    inventory_ref = store.put_file(
        source,
        name="inventory.json",
        media_type="application/json",
        schema_version="harness.asset_debt_inventory.v1",
    )
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )

    with pytest.raises(AssetRepairError, match="strict canonical JSON") as caught:
        _application(store, inventory_ref).plan(request)
    assert caught.value.code == "HARN_INPUT_SCHEMA_INVALID"


def test_application_rejects_artifact_store_lookalikes() -> None:
    class StoreLookalike:
        pass

    with pytest.raises(TypeError, match="exact LocalArtifactStore"):
        AssetRepairApplication(  # type: ignore[arg-type]
            artifact_store=StoreLookalike(),
            trusted_inventory_refs=(),
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("planned_ledger_count", 2),
        ("planned_violation_count", 10),
        ("planned_representation_count", 3),
        ("selected_asset_ids", ("robotwin_999_missing",)),
        ("full_baseline_evaluated", True),
        (
            "disposition_counts",
            {
                "observed_primary_digest_match": 0,
                "observed_primary_digest_match_remeasure_size": 0,
                "hash_mismatch_new_identity_or_retire": 0,
                "source_missing_new_identity_or_retire": 0,
            },
        ),
    ],
)
def test_public_plan_rejects_forged_aggregate_claims(
    tmp_path: Path,
    path: str,
    value: object,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    inventory_ref = _put_inventory(store, tmp_path, _single_plate_inventory())
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )
    valid = _application(store, inventory_ref).plan(request).model_dump(mode="json")
    valid[path] = value

    with pytest.raises(
        ValidationError,
        match="plan .* binding|full_baseline_evaluated",
    ):
        AssetRepairPlan.model_validate(valid)


@pytest.mark.parametrize(
    "attack",
    ["disposition", "partial_observation", "duplicate_representation", "next_step", "followup"],
)
def test_public_plan_rejects_forged_representation_and_followup_claims(
    tmp_path: Path,
    attack: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    inventory_ref = _put_inventory(store, tmp_path, _single_plate_inventory())
    request = AssetRepairPlanRequest(
        schema_version="harness.asset_repair_plan_request.v1",
        inventory_ref=inventory_ref,
        selected_asset_ids=("robotwin_003_plate",),
    )
    valid = _application(store, inventory_ref).plan(request).model_dump(mode="json")
    if attack == "disposition":
        valid["items"][0]["representations"][0]["observed_sha256"] = "f" * 64
    elif attack == "partial_observation":
        valid["items"][0]["representations"][0]["observed_sha256"] = None
    elif attack == "duplicate_representation":
        valid["items"][0]["representations"].append(
            valid["items"][0]["representations"][0]
        )
    elif attack == "next_step":
        valid["items"][0]["next_step"] = "build_new_identity_or_retire_asset"
    else:
        valid["items"][0]["required_followup"] = ["qualify_settle"]

    with pytest.raises(
        ValidationError,
        match="observed identity|disposition|representations|next_step|required_followup",
    ):
        AssetRepairPlan.model_validate(valid)
