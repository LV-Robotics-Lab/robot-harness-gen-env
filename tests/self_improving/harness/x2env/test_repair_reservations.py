"""Public SQLite journal reservation gates, without DB edits or model/runtime doubles."""

import pytest

from self_improving.harness.x2env.contracts import ToolResult, X2EnvRequest
from self_improving.harness.x2env.store import Store


def active(tmp_path):
    store = Store(tmp_path / "state")
    snapshot = store.submit(
        X2EnvRequest(
            text="repair", seed=0, idempotency_key="repair", output_dir=str(tmp_path / "output")
        )
    )
    snapshot = store.claim(snapshot.workflow_id)
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(operation_id=snapshot.operations[-1].operation_id, status="succeeded"),
        None,
        status="active",
    )
    return store, snapshot


def reserve(store, snapshot, fingerprint, *, cost=1, capability="revise", **updates):
    from self_improving.harness.x2env.contracts import RepairReservation

    approval = store.write_artifact(b"explicit controller approval fixture", "text/plain")
    value = RepairReservation(
        kind="scene" if capability == "revise" else "asset",
        cost=cost,
        failure_fingerprint=fingerprint * 64,
        approval=approval,
        base_revision=snapshot.revision,
    )
    return store.begin_operation(
        snapshot, capability, repair_reservation=value.model_copy(update=updates)
    )


def finish(store, snapshot, status):
    return store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status=status,
            error_code=None if status == "succeeded" else "fixture_failure",
        ),
        None,
        status="active",
    )


def test_failed_and_cancelled_repairs_keep_shared_budget_after_store_reopen(tmp_path):
    store, snapshot = active(tmp_path)
    snapshot = reserve(store, snapshot, "a")
    snapshot = finish(store, snapshot, "failed")
    snapshot = reserve(store, snapshot, "b", capability="asset.revise")
    snapshot = finish(store, snapshot, "cancelled")
    reopened = Store(tmp_path / "state")
    assert reopened.status(snapshot.workflow_id) == snapshot
    with pytest.raises(ValueError, match="repair_budget_exhausted"):
        reserve(reopened, snapshot, "c")
    assert reopened.status(snapshot.workflow_id) == snapshot
    assert (
        sum(op.repair_reservation.cost for op in snapshot.operations if op.repair_reservation) == 2
    )


@pytest.mark.parametrize(
    "fault,error",
    [
        ("base", "repair_reservation_stale_base"),
        ("capability", "repair_reservation_capability_mismatch"),
        ("approval", None),
        ("invalid_cost", None),
    ],
)
def test_rejected_reservation_does_not_write_an_operation(tmp_path, fault, error):
    from self_improving.harness.x2env.contracts import ArtifactRef

    store, snapshot = active(tmp_path)
    updates = {"base_revision": snapshot.revision - 1} if fault == "base" else {}
    if fault == "approval":
        updates["approval"] = ArtifactRef(sha256="f" * 64, size_bytes=1, media_type="text/plain")
    if fault == "invalid_cost":
        updates["cost"] = True
    with pytest.raises((ValueError, FileNotFoundError), match=error):
        reserve(
            store,
            snapshot,
            "a",
            capability="observe" if fault == "capability" else "revise",
            **updates,
        )
    assert store.status(snapshot.workflow_id) == snapshot


def test_repeated_failure_does_not_spend_second_reservation(tmp_path):
    store, snapshot = active(tmp_path)
    snapshot = finish(store, reserve(store, snapshot, "a"), "failed")
    with pytest.raises(ValueError, match="repair_repeated_failure"):
        reserve(store, snapshot, "a", capability="asset.revise")
    assert store.status(snapshot.workflow_id) == snapshot
    next_snapshot = reserve(store, snapshot, "b", capability="asset.revise")
    assert next_snapshot.operations[-1].repair_reservation.cost == 1


def test_concurrent_reservations_have_one_atomic_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, snapshot = active(tmp_path)
    barrier = Barrier(2)

    def attempt(fingerprint):
        worker = Store(tmp_path / "state")
        barrier.wait(timeout=5)
        try:
            return reserve(worker, snapshot, fingerprint)
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(attempt, ["a", "b"]))
    assert sum(not isinstance(value, ValueError) for value in values) == 1
    current = store.status(snapshot.workflow_id)
    assert len(current.operations) == len(snapshot.operations) + 1
    assert current.operations[-1].status == "running"
    assert current.operations[-1].repair_reservation.cost == 1


def test_cost_two_uses_whole_budget_even_after_success(tmp_path):
    store, snapshot = active(tmp_path)
    snapshot = finish(store, reserve(store, snapshot, "a", cost=2, kind="scene_asset"), "succeeded")
    with pytest.raises(ValueError, match="repair_budget_exhausted"):
        reserve(store, snapshot, "b")
