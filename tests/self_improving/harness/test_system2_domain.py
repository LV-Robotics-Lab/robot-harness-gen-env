from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from self_improving.harness.schemas import ArtifactRef, Blocker, RunStatus
from self_improving.harness.system2.domain import (
    StateDelta,
    StateMutation,
    System2ToolResult,
    TrustedWorldState,
    WorldFact,
    apply_state_delta,
    build_world_state,
    fact_sha256,
)

_T0 = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


def _artifact(
    name: str,
    digest: str,
    schema_version: str,
    *,
    uri: str | None = None,
) -> ArtifactRef:
    return ArtifactRef(
        name=name,
        uri=uri or f"artifact://sha256/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=10,
        schema_version=schema_version,
    )


def _fact(
    key: str,
    value: object,
    *,
    observed_at: datetime = _T0,
    valid_until: datetime | None = None,
    digest: str = "1" * 64,
) -> WorldFact:
    return WorldFact(
        key=key,
        value=value,
        source_kind="fresh_observation",
        source_artifact=_artifact(
            "observation",
            digest,
            "harness.fresh_observation.v1",
        ),
        observed_at=observed_at,
        valid_until=valid_until,
    )


def _receipt(digest: str = "9" * 64) -> ArtifactRef:
    return _artifact("trusted_receipt", digest, "harness.trusted_tool_receipt.v1")


def _blocker() -> Blocker:
    return Blocker(
        code="HARN_DEPENDENCY_UNAVAILABLE",
        message="runtime unavailable",
        stage="preflight",
        retryable=False,
        details={},
        unknowns=(),
        artifact_refs=(),
    )


def test_build_world_state_is_canonical_and_order_independent() -> None:
    left = _fact("scene.left", {"asset": "can"}, digest="1" * 64)
    right = _fact("scene.right", {"asset": "plate"}, digest="2" * 64)

    state = build_world_state((right, left), as_of=_T0)
    repeated = build_world_state((left, right), as_of=_T0)

    assert isinstance(state, TrustedWorldState)
    assert state.version == 1
    assert state.predecessor_state_sha256 is None
    assert tuple(fact.key for fact in state.facts) == ("scene.left", "scene.right")
    assert state.state_sha256 == repeated.state_sha256


@pytest.mark.parametrize(
    "facts,as_of,message",
    [
        ((_fact("scene.same", 1), _fact("scene.same", 2)), _T0, "unique"),
        ((_fact("scene.future", 1, observed_at=_T0 + timedelta(seconds=1)),), _T0, "future"),
        (
            (
                _fact(
                    "scene.stale",
                    1,
                    observed_at=_T0 - timedelta(seconds=2),
                    valid_until=_T0 - timedelta(seconds=1),
                ),
            ),
            _T0,
            "expired",
        ),
    ],
)
def test_build_world_state_rejects_ambiguous_or_untrusted_facts(
    facts: tuple[WorldFact, ...],
    as_of: datetime,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_world_state(facts, as_of=as_of)


def test_world_fact_requires_utc_and_content_addressed_source() -> None:
    local_time = datetime(2026, 8, 31, 16, 0, tzinfo=timezone(timedelta(hours=8)))
    with pytest.raises(ValidationError, match="UTC"):
        _fact("scene.local_time", 1, observed_at=local_time)
    with pytest.raises(ValidationError, match="content-addressed"):
        WorldFact(
            key="scene.mutable",
            value=1,
            source_kind="fresh_observation",
            source_artifact=_artifact(
                "observation",
                "1" * 64,
                "harness.fresh_observation.v1",
                uri="file:///tmp/mutable.json",
            ),
            observed_at=_T0,
            valid_until=None,
        )


def test_world_fact_rejects_an_expiry_before_observation() -> None:
    with pytest.raises(ValidationError, match="cannot precede"):
        _fact(
            "scene.expiry",
            1,
            observed_at=_T0,
            valid_until=_T0 - timedelta(microseconds=1),
        )


@pytest.mark.parametrize("attack", ["predecessor", "order", "digest", "timezone"])
def test_trusted_world_state_rejects_noncanonical_envelopes(attack: str) -> None:
    left = _fact("scene.left", 1, digest="1" * 64)
    right = _fact("scene.right", 2, digest="2" * 64)
    state = build_world_state((left, right), as_of=_T0)
    payload = state.model_dump(mode="python")
    if attack == "predecessor":
        payload["predecessor_state_sha256"] = "3" * 64
    elif attack == "order":
        payload["facts"] = (right, left)
    elif attack == "digest":
        payload["state_sha256"] = "4" * 64
    else:
        payload["as_of"] = datetime(
            2026,
            8,
            31,
            16,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        )

    with pytest.raises(ValidationError):
        TrustedWorldState.model_validate(payload)


def test_apply_state_delta_requires_compare_and_swap_and_is_deterministic() -> None:
    original = _fact("scene.pose", {"z": 0.1}, digest="1" * 64)
    state = build_world_state((original,), as_of=_T0)
    replacement = _fact(
        "scene.pose",
        {"z": 0.2},
        observed_at=_T0 + timedelta(seconds=1),
        digest="2" * 64,
    )
    added = _fact(
        "scene.contact",
        {"target": "plate"},
        observed_at=_T0 + timedelta(seconds=1),
        digest="3" * 64,
    )
    delta = StateDelta(
        base_state_sha256=state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        receipt=_receipt(),
        mutations=(
            StateMutation(
                operation="upsert",
                key="scene.contact",
                expected_fact_sha256=None,
                fact=added,
            ),
            StateMutation(
                operation="upsert",
                key="scene.pose",
                expected_fact_sha256=fact_sha256(original),
                fact=replacement,
            ),
        ),
    )

    updated = apply_state_delta(state, delta)
    repeated = apply_state_delta(state, delta)

    assert updated.version == 2
    assert updated.predecessor_state_sha256 == state.state_sha256
    assert tuple(fact.key for fact in updated.facts) == ("scene.contact", "scene.pose")
    assert updated.state_sha256 == repeated.state_sha256


@pytest.mark.parametrize("attack", ["base", "prior", "blind_insert", "stale_result"])
def test_apply_state_delta_rejects_stale_or_blind_mutations(attack: str) -> None:
    original = _fact("scene.pose", 1)
    state = build_world_state((original,), as_of=_T0)
    new = _fact(
        "scene.pose" if attack != "blind_insert" else "scene.new",
        2,
        observed_at=(_T0 if attack == "stale_result" else _T0 + timedelta(seconds=1)),
        valid_until=(_T0 + timedelta(milliseconds=500) if attack == "stale_result" else None),
    )
    expected = fact_sha256(original)
    if attack == "prior":
        expected = "8" * 64
    elif attack == "blind_insert":
        expected = "8" * 64
    delta = StateDelta(
        base_state_sha256="7" * 64 if attack == "base" else state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        receipt=_receipt(),
        mutations=(
            StateMutation(
                operation="upsert",
                key=new.key,
                expected_fact_sha256=expected,
                fact=new,
            ),
        ),
    )

    with pytest.raises(ValueError):
        apply_state_delta(state, delta)


def test_retract_requires_exact_prior_fact() -> None:
    original = _fact("scene.temporary", True)
    state = build_world_state((original,), as_of=_T0)
    delta = StateDelta(
        base_state_sha256=state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        receipt=_receipt(),
        mutations=(
            StateMutation(
                operation="retract",
                key=original.key,
                expected_fact_sha256=fact_sha256(original),
                fact=None,
            ),
        ),
    )

    updated = apply_state_delta(state, delta)

    assert updated.facts == ()


def test_state_delta_rejects_duplicate_keys_and_invalid_operation_shapes() -> None:
    fact = _fact("scene.pose", 1)
    mutation = StateMutation(
        operation="upsert",
        key=fact.key,
        expected_fact_sha256=None,
        fact=fact,
    )
    with pytest.raises(ValidationError, match="unique"):
        StateDelta(
            base_state_sha256="1" * 64,
            effective_at=_T0,
            receipt=_receipt(),
            mutations=(mutation, mutation),
        )
    with pytest.raises(ValidationError):
        StateMutation(
            operation="retract",
            key=fact.key,
            expected_fact_sha256=None,
            fact=fact,
        )
    with pytest.raises(ValidationError, match="upsert"):
        StateMutation(
            operation="upsert",
            key="scene.other",
            expected_fact_sha256=None,
            fact=fact,
        )


def test_state_delta_rejects_wrong_receipt_schema_and_noncanonical_order() -> None:
    left = _fact("scene.left", 1, digest="1" * 64)
    right = _fact("scene.right", 2, digest="2" * 64)
    left_mutation = StateMutation(
        operation="upsert",
        key=left.key,
        expected_fact_sha256=None,
        fact=left,
    )
    right_mutation = StateMutation(
        operation="upsert",
        key=right.key,
        expected_fact_sha256=None,
        fact=right,
    )
    with pytest.raises(ValidationError, match="schema_version"):
        StateDelta(
            base_state_sha256="1" * 64,
            effective_at=_T0,
            receipt=_artifact("receipt", "9" * 64, "wrong.receipt.v1"),
            mutations=(),
        )
    with pytest.raises(ValidationError, match="sorted"):
        StateDelta(
            base_state_sha256="1" * 64,
            effective_at=_T0,
            receipt=_receipt(),
            mutations=(right_mutation, left_mutation),
        )


def test_apply_state_delta_rejects_blind_overwrite() -> None:
    original = _fact("scene.pose", 1)
    state = build_world_state((original,), as_of=_T0)
    replacement = _fact("scene.pose", 2, observed_at=_T0 + timedelta(seconds=1))
    delta = StateDelta(
        base_state_sha256=state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        receipt=_receipt(),
        mutations=(
            StateMutation(
                operation="upsert",
                key=replacement.key,
                expected_fact_sha256=None,
                fact=replacement,
            ),
        ),
    )

    with pytest.raises(ValueError, match="blind overwrite"):
        apply_state_delta(state, delta)


def test_apply_state_delta_rechecks_retained_state_after_nested_mutation() -> None:
    original = _fact("scene.pose", {"z": 0.1})
    state = build_world_state((original,), as_of=_T0)
    assert isinstance(state.facts[0].value, dict)
    state.facts[0].value["z"] = 9.9
    delta = StateDelta(
        base_state_sha256=state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        receipt=_receipt(),
        mutations=(),
    )

    with pytest.raises(ValueError, match="integrity"):
        apply_state_delta(state, delta)


def test_tool_result_carries_typed_delta_receipt_diagnostics_and_fresh_observation() -> None:
    state = build_world_state((), as_of=_T0)
    observation = _fact(
        "scene.visible",
        True,
        observed_at=_T0 + timedelta(seconds=1),
    )
    receipt = _receipt()
    delta = StateDelta(
        base_state_sha256=state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        receipt=receipt,
        mutations=(
            StateMutation(
                operation="upsert",
                key=observation.key,
                expected_fact_sha256=None,
                fact=observation,
            ),
        ),
    )
    result = System2ToolResult(
        status=RunStatus.SUCCEEDED,
        started_at=_T0,
        ended_at=_T0 + timedelta(seconds=2),
        invocation=_artifact("invocation", "4" * 64, "harness.skill_invocation.v1"),
        run_state=_artifact("run_state", "5" * 64, "harness.run_state.v1"),
        typed_output={"ok": True},
        state_delta=delta,
        diagnostics=(_artifact("diagnostic", "6" * 64, "harness.runtime_diagnostic.v1"),),
        trusted_receipt=receipt,
        fresh_observations=(observation,),
        blocker=None,
    )

    assert result.state_delta.receipt == result.trusted_receipt
    assert result.fresh_observations == (observation,)


def test_failed_tool_result_is_a_valid_terminal_envelope() -> None:
    state = build_world_state((), as_of=_T0)
    receipt = _receipt()
    result = System2ToolResult(
        status=RunStatus.FAILED,
        started_at=_T0,
        ended_at=_T0 + timedelta(seconds=1),
        invocation=_artifact("invocation", "4" * 64, "harness.skill_invocation.v1"),
        run_state=_artifact("run_state", "5" * 64, "harness.run_state.v1"),
        typed_output=None,
        state_delta=StateDelta(
            base_state_sha256=state.state_sha256,
            effective_at=_T0 + timedelta(seconds=1),
            receipt=receipt,
            mutations=(),
        ),
        diagnostics=(),
        trusted_receipt=receipt,
        fresh_observations=(),
        blocker=_blocker(),
    )

    assert result.status is RunStatus.FAILED


@pytest.mark.parametrize(
    "attack",
    [
        "time_order",
        "delta_time",
        "observation_order",
        "observation_expired",
        "observation_provenance",
        "diagnostic_uri",
    ],
)
def test_tool_result_rejects_temporal_and_artifact_attacks(attack: str) -> None:
    state = build_world_state((), as_of=_T0)
    receipt = _receipt()
    first = _fact(
        "scene.first",
        True,
        observed_at=_T0 + timedelta(seconds=1),
        valid_until=(
            _T0 + timedelta(seconds=1, milliseconds=500)
            if attack == "observation_expired"
            else None
        ),
        digest="1" * 64,
    )
    second = _fact(
        "scene.second",
        True,
        observed_at=_T0 + timedelta(seconds=1),
        digest="2" * 64,
    )
    started_at = _T0
    ended_at = _T0 + timedelta(seconds=2)
    effective_at = _T0 + timedelta(seconds=1)
    observations = (first,)
    diagnostics = ()
    if attack == "time_order":
        ended_at = _T0 - timedelta(seconds=1)
    elif attack == "delta_time":
        effective_at = _T0 + timedelta(seconds=3)
    elif attack == "observation_order":
        observations = (second, first)
    elif attack == "observation_provenance":
        observations = (
            WorldFact(
                **{
                    **first.model_dump(mode="python"),
                    "source_kind": "trusted_receipt",
                }
            ),
        )
    elif attack == "diagnostic_uri":
        diagnostics = (
            _artifact(
                "diagnostic",
                "6" * 64,
                "harness.runtime_diagnostic.v1",
                uri="file:///tmp/diagnostic.json",
            ),
        )
    delta = StateDelta(
        base_state_sha256=state.state_sha256,
        effective_at=effective_at,
        receipt=receipt,
        mutations=(),
    )

    with pytest.raises(ValidationError):
        System2ToolResult(
            status=RunStatus.SUCCEEDED,
            started_at=started_at,
            ended_at=ended_at,
            invocation=_artifact("invocation", "4" * 64, "harness.skill_invocation.v1"),
            run_state=_artifact("run_state", "5" * 64, "harness.run_state.v1"),
            typed_output={"ok": True},
            state_delta=delta,
            diagnostics=diagnostics,
            trusted_receipt=receipt,
            fresh_observations=observations,
            blocker=None,
        )


@pytest.mark.parametrize(
    "attack", ["running", "success_blocker", "failed_output", "receipt", "old_observation"]
)
def test_tool_result_rejects_inconsistent_or_stale_envelopes(attack: str) -> None:
    state = build_world_state((), as_of=_T0)
    receipt = _receipt()
    observation = _fact(
        "scene.visible",
        True,
        observed_at=(
            _T0 - timedelta(seconds=1)
            if attack == "old_observation"
            else _T0 + timedelta(seconds=1)
        ),
    )
    delta = StateDelta(
        base_state_sha256=state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        receipt=_receipt("8" * 64) if attack == "receipt" else receipt,
        mutations=(),
    )
    status = RunStatus.SUCCEEDED
    output = {"ok": True}
    blocker = None
    if attack == "running":
        status = RunStatus.RUNNING
    elif attack == "success_blocker":
        blocker = _blocker()
    elif attack == "failed_output":
        status = RunStatus.FAILED
        blocker = _blocker()

    with pytest.raises(ValidationError):
        System2ToolResult(
            status=status,
            started_at=_T0,
            ended_at=_T0 + timedelta(seconds=2),
            invocation=_artifact("invocation", "4" * 64, "harness.skill_invocation.v1"),
            run_state=_artifact("run_state", "5" * 64, "harness.run_state.v1"),
            typed_output=output,
            state_delta=delta,
            diagnostics=(),
            trusted_receipt=receipt,
            fresh_observations=(observation,),
            blocker=blocker,
        )
