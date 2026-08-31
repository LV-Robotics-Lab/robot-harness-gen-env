from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas import ArtifactRef, Blocker, RunStatus
from self_improving.harness.system2.dispatcher import (
    ReceiptDerivedFactClaim,
    SkillExecutionIdentity,
    TrustedToolReceipt,
)
from self_improving.harness.system2.domain import (
    StateMutation,
    System2ToolResult,
    TrustedWorldState,
    WorldFact,
    WorldFactEvidence,
    apply_state_delta,
    build_state_delta,
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


def _put_json(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    name: str,
    schema_version: str,
    value: object,
) -> ArtifactRef:
    path = tmp_path / f"{name}-{len(tuple(tmp_path.glob('*.json')))}.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _publish_state_receipt(
    tmp_path: Path,
    *,
    base_state_sha256: str,
    effective_at: datetime,
    claims: tuple[tuple[str, object], ...],
) -> tuple[LocalArtifactStore, ArtifactRef]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = LocalArtifactStore(tmp_path / "cas")
    qualification = _put_json(
        store,
        tmp_path,
        name="qualification",
        schema_version="harness.skill_qualification.v1",
        value={"fixture": True},
    )
    planner_receipt = _put_json(
        store,
        tmp_path,
        name="planner_receipt",
        schema_version="harness.planner_execution_receipt.v1",
        value={"fixture": True},
    )
    planner_decision = _put_json(
        store,
        tmp_path,
        name="planner_decision",
        schema_version="harness.planner_decision.v1",
        value={"fixture": True},
    )
    base_state = _put_json(
        store,
        tmp_path,
        name="base_state",
        schema_version="harness.trusted_world_state.v1",
        value={"fixture": True},
    )
    planner_context = _put_json(
        store,
        tmp_path,
        name="planner_context",
        schema_version="harness.planner_context.v1",
        value={"fixture": True},
    )
    invocation = _put_json(
        store,
        tmp_path,
        name="invocation",
        schema_version="harness.skill_invocation.v1",
        value={"fixture": True},
    )
    run_state = _put_json(
        store,
        tmp_path,
        name="run_state",
        schema_version="harness.run_state.v1",
        value={"fixture": True},
    )
    receipt = TrustedToolReceipt(
        planner_call_id=UUID("00000000-0000-4000-8000-000000000123"),
        planner_context_sha256="1" * 64,
        planner_receipt=planner_receipt,
        planner_decision=planner_decision,
        planner_decision_sha256="2" * 64,
        base_state_sha256=base_state_sha256,
        base_state=base_state,
        planner_context=planner_context,
        skill=SkillExecutionIdentity(
            skill_ref="text2env.compile@1.0.0",
            input_schema="harness.text2env_compile_input.v1",
            output_schema="harness.text2env_compile_output.v1",
            implementation_sha256="3" * 64,
            qualification_artifact=qualification,
            max_attempts=1,
        ),
        status=RunStatus.SUCCEEDED,
        started_at=effective_at - timedelta(milliseconds=1),
        ended_at=effective_at,
        invocation=invocation,
        run_state=run_state,
        invocation_digest="4" * 64,
        typed_output={"fixture": True},
        blocker=None,
        supporting_artifacts=(),
        derived_fact_claims=tuple(
            ReceiptDerivedFactClaim(key=key, value=value) for key, value in sorted(claims)
        ),
    )
    receipt_ref = _put_json(
        store,
        tmp_path,
        name="trusted_receipt",
        schema_version="harness.trusted_tool_receipt.v1",
        value=receipt.model_dump(mode="json"),
    )
    return store, receipt_ref


def _receipt_fact(
    key: str,
    value: object,
    *,
    receipt: ArtifactRef,
    observed_at: datetime,
    valid_until: datetime | None = None,
) -> WorldFact:
    return WorldFact(
        key=key,
        value=value,
        source_kind="trusted_receipt",
        source_artifact=receipt,
        observed_at=observed_at,
        valid_until=valid_until,
    )


def _valid_compile_transition(tmp_path: Path):
    from tests.self_improving.harness.test_system2_dispatcher import _fixture

    fixture = _fixture(tmp_path / "real-compile")
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    assert isinstance(fixture.state, TrustedWorldState)
    return fixture.store, fixture.state, result


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


@pytest.mark.parametrize(
    "observed_at,valid_until,message",
    [
        (
            datetime(2026, 8, 31, 16, 0, tzinfo=timezone(timedelta(hours=8))),
            None,
            "UTC",
        ),
        (_T0, _T0 - timedelta(microseconds=1), "cannot precede"),
    ],
)
def test_world_fact_evidence_requires_utc_and_nonnegative_validity_window(
    observed_at: datetime,
    valid_until: datetime | None,
    message: str,
) -> None:
    """Non-receipt evidence must be a usable time-bounded observation."""

    with pytest.raises(ValidationError, match=message):
        WorldFactEvidence(
            source_kind="fresh_observation",
            key="scene.visibility",
            value=True,
            observed_at=observed_at,
            valid_until=valid_until,
        )


def test_world_fact_evidence_allows_an_unbounded_utc_observation() -> None:
    """Evidence with no expiry remains valid when its observation time is UTC."""

    evidence = WorldFactEvidence(
        source_kind="fresh_observation",
        key="scene.visibility",
        value=True,
        observed_at=_T0,
        valid_until=None,
    )

    assert evidence.valid_until is None


def test_world_fact_evidence_allows_a_future_utc_expiry() -> None:
    """A bounded observation is valid when its expiry follows its UTC observation."""

    evidence = WorldFactEvidence(
        source_kind="fresh_observation",
        key="scene.visibility",
        value=True,
        observed_at=_T0,
        valid_until=_T0 + timedelta(seconds=1),
    )

    assert evidence.valid_until == _T0 + timedelta(seconds=1)


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


def test_apply_state_delta_requires_compare_and_swap_and_is_deterministic(
    tmp_path: Path,
) -> None:
    store, state, result = _valid_compile_transition(tmp_path)

    updated = apply_state_delta(state, result.state_delta, artifact_store=store)
    repeated = apply_state_delta(state, result.state_delta, artifact_store=store)

    assert updated.version == 2
    assert updated.predecessor_state_sha256 == state.state_sha256
    assert tuple(fact.key for fact in updated.facts) == (
        "assets.catalog",
        "environment.package",
        "task.objective",
    )
    assert updated.state_sha256 == repeated.state_sha256


def test_apply_state_delta_rejects_a_missing_trusted_receipt(tmp_path) -> None:
    state = build_world_state((), as_of=_T0)
    fact = WorldFact(
        key="physical.validation",
        value={"status": "pass"},
        source_kind="trusted_receipt",
        source_artifact=_receipt(),
        observed_at=_T0 + timedelta(seconds=1),
        valid_until=None,
    )
    delta = build_state_delta(
        base_state_sha256=state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        receipt=_receipt(),
        mutations=(
            StateMutation(
                operation="upsert",
                key=fact.key,
                expected_fact_sha256=None,
                fact=fact,
            ),
        ),
    )

    with pytest.raises(ValueError, match="receipt"):
        apply_state_delta(
            state,
            delta,
            artifact_store=LocalArtifactStore(tmp_path / "cas"),
        )


def test_apply_state_delta_requires_exact_store_and_receipt_binding(tmp_path: Path) -> None:
    store, state, result = _valid_compile_transition(tmp_path)

    with pytest.raises(TypeError, match="LocalArtifactStore"):
        apply_state_delta(  # type: ignore[arg-type]
            state,
            result.state_delta,
            artifact_store=object(),
        )
    unbound = build_state_delta(
        base_state_sha256="8" * 64,
        effective_at=result.state_delta.effective_at,
        receipt=result.trusted_receipt,
        mutations=result.state_delta.mutations,
    )
    with pytest.raises(ValueError, match="not bound"):
        apply_state_delta(state, unbound, artifact_store=store)


def test_apply_state_delta_requires_complete_receipt_closure(tmp_path: Path) -> None:
    store, state, result = _valid_compile_transition(tmp_path)
    parsed = TrustedToolReceipt.model_validate_json(
        store.resolve(result.trusted_receipt).path.read_bytes()
    )
    store.resolve(parsed.skill.qualification_artifact).path.unlink()

    with pytest.raises(ValueError, match="unavailable|invalid"):
        apply_state_delta(state, result.state_delta, artifact_store=store)


@pytest.mark.parametrize(
    "drift",
    ["claim_set", "source_kind", "source_ref", "time", "value"],
)
def test_apply_state_delta_cross_binds_each_upsert_to_receipt_claim(
    tmp_path: Path,
    drift: str,
) -> None:
    store, state, result = _valid_compile_transition(tmp_path)
    mutations = list(result.state_delta.mutations)
    if drift == "claim_set":
        mutations.pop()
    else:
        first = mutations[0]
        assert first.fact is not None
        fact = first.fact
        if drift == "source_kind":
            fact = fact.model_copy(update={"source_kind": "fresh_observation"})
        elif drift == "source_ref":
            fact = fact.model_copy(update={"source_artifact": _receipt("8" * 64)})
        elif drift == "time":
            fact = fact.model_copy(update={"observed_at": _T0})
        else:
            fact = fact.model_copy(update={"value": {"changed": True}})
        mutations[0] = first.model_copy(update={"fact": fact})
    delta = build_state_delta(
        base_state_sha256=state.state_sha256,
        effective_at=result.state_delta.effective_at,
        receipt=result.trusted_receipt,
        mutations=tuple(mutations),
    )

    with pytest.raises(ValueError, match="claims|upsert"):
        apply_state_delta(state, delta, artifact_store=store)


def test_receipt_retract_claim_requires_an_explicit_null_value() -> None:
    with pytest.raises(ValidationError, match="null"):
        ReceiptDerivedFactClaim(operation="retract", key="scene.temporary", value=True)


@pytest.mark.parametrize("attack", ["base", "prior", "stale_result"])
def test_apply_state_delta_rejects_stale_or_blind_mutations(
    tmp_path: Path,
    attack: str,
) -> None:
    store, state, result = _valid_compile_transition(tmp_path)
    mutations = list(result.state_delta.mutations)
    delta_base = "7" * 64 if attack == "base" else state.state_sha256
    if attack == "prior":
        mutations[0] = mutations[0].model_copy(update={"expected_fact_sha256": "8" * 64})
    elif attack == "stale_result":
        assert mutations[0].fact is not None
        mutations[0] = mutations[0].model_copy(
            update={"fact": mutations[0].fact.model_copy(update={"observed_at": _T0})}
        )
    delta = build_state_delta(
        base_state_sha256=delta_base,
        effective_at=result.state_delta.effective_at,
        receipt=result.trusted_receipt,
        mutations=tuple(mutations),
    )

    with pytest.raises(ValueError):
        apply_state_delta(state, delta, artifact_store=store)


def test_apply_state_delta_rejects_a_forged_receipt_closure(tmp_path: Path) -> None:
    state = build_world_state((), as_of=_T0)
    store, receipt = _publish_state_receipt(
        tmp_path,
        base_state_sha256=state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        claims=(),
    )
    delta = build_state_delta(
        base_state_sha256=state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        receipt=receipt,
        mutations=(),
    )

    with pytest.raises(ValueError, match="unavailable|invalid"):
        apply_state_delta(state, delta, artifact_store=store)


def test_state_delta_rejects_duplicate_keys_and_invalid_operation_shapes() -> None:
    fact = _fact("scene.pose", 1)
    mutation = StateMutation(
        operation="upsert",
        key=fact.key,
        expected_fact_sha256=None,
        fact=fact,
    )
    with pytest.raises(ValidationError, match="unique"):
        build_state_delta(
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

    assert (
        StateMutation(
            operation="retract",
            key=fact.key,
            expected_fact_sha256=fact_sha256(fact),
            fact=None,
        ).operation
        == "retract"
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
        build_state_delta(
            base_state_sha256="1" * 64,
            effective_at=_T0,
            receipt=_artifact("receipt", "9" * 64, "wrong.receipt.v1"),
            mutations=(),
        )
    with pytest.raises(ValidationError, match="sorted"):
        build_state_delta(
            base_state_sha256="1" * 64,
            effective_at=_T0,
            receipt=_receipt(),
            mutations=(right_mutation, left_mutation),
        )


def test_apply_state_delta_rechecks_retained_state_after_nested_mutation(tmp_path: Path) -> None:
    original = _fact("scene.pose", {"z": 0.1})
    state = build_world_state((original,), as_of=_T0)
    assert isinstance(state.facts[0].value, dict)
    state.facts[0].value["z"] = 9.9
    effective_at = _T0 + timedelta(seconds=1)
    store = LocalArtifactStore(tmp_path / "cas")
    delta = build_state_delta(
        base_state_sha256=state.state_sha256,
        effective_at=effective_at,
        receipt=_receipt(),
        mutations=(),
    )

    with pytest.raises(ValueError, match="integrity"):
        apply_state_delta(state, delta, artifact_store=store)


def test_apply_state_delta_rechecks_retained_delta_after_nested_mutation(tmp_path: Path) -> None:
    state = build_world_state((), as_of=_T0)
    effective_at = _T0 + timedelta(seconds=1)
    store = LocalArtifactStore(tmp_path / "cas")
    fact = _fact("scene.pose", {"z": 0.1}, observed_at=effective_at)
    delta = build_state_delta(
        base_state_sha256=state.state_sha256,
        effective_at=effective_at,
        receipt=_receipt(),
        mutations=(
            StateMutation(
                operation="upsert",
                key=fact.key,
                expected_fact_sha256=None,
                fact=fact,
            ),
        ),
    )
    assert delta.mutations[0].fact is not None
    assert isinstance(delta.mutations[0].fact.value, dict)
    delta.mutations[0].fact.value["z"] = 9.9

    with pytest.raises(ValueError, match="state delta failed its canonical integrity"):
        apply_state_delta(state, delta, artifact_store=store)


@pytest.mark.parametrize(
    "attack,expected",
    [
        ("base", "base does not match"),
        ("backward_time", "time backwards"),
        ("blind_overwrite", "blind overwrite"),
        ("prior_mismatch", "prior fact identity"),
        ("claim_operation", "operation does not equal"),
        ("retract_value", "requires a null"),
    ],
)
def test_apply_state_delta_preserves_cas_guards_after_receipt_is_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
    expected: str,
) -> None:
    """CAS guards reject an otherwise verified receipt when its proposed transition drifts."""

    store, state, result = _valid_compile_transition(tmp_path)
    receipt = TrustedToolReceipt.model_validate_json(
        store.resolve(result.trusted_receipt).path.read_bytes()
    )
    delta = result.state_delta
    checked_state = state
    verified_receipt = receipt
    mutations = list(delta.mutations)

    if attack == "base":
        checked_state = build_world_state(
            (*state.facts, _fact("scene.guard", True, digest="8" * 64)),
            as_of=state.as_of,
        )
    elif attack == "backward_time":
        earlier = state.as_of - timedelta(microseconds=1)
        mutations = [
            mutation.model_copy(
                update={"fact": mutation.fact.model_copy(update={"observed_at": earlier})}
            )
            if mutation.fact is not None
            else mutation
            for mutation in mutations
        ]
        delta = build_state_delta(
            base_state_sha256=state.state_sha256,
            effective_at=earlier,
            receipt=result.trusted_receipt,
            mutations=tuple(mutations),
        )
        verified_receipt = receipt.model_copy(update={"ended_at": delta.effective_at})
    elif attack == "blind_overwrite":
        first = mutations[0]
        assert first.fact is not None
        existing = first.fact.model_copy(update={"observed_at": state.as_of})
        checked_state = build_world_state((*state.facts, existing), as_of=state.as_of)
        delta = build_state_delta(
            base_state_sha256=checked_state.state_sha256,
            effective_at=result.state_delta.effective_at,
            receipt=result.trusted_receipt,
            mutations=tuple(mutations),
        )
        verified_receipt = receipt.model_copy(
            update={"base_state_sha256": checked_state.state_sha256}
        )
    elif attack == "prior_mismatch":
        first = mutations[0]
        mutations[0] = first.model_copy(update={"expected_fact_sha256": "0" * 64})
        delta = build_state_delta(
            base_state_sha256=state.state_sha256,
            effective_at=result.state_delta.effective_at,
            receipt=result.trusted_receipt,
            mutations=tuple(mutations),
        )
    else:
        existing = _fact("scene.temporary", True)
        checked_state = build_world_state((existing,), as_of=state.as_of)
        claim = ReceiptDerivedFactClaim(
            operation="upsert" if attack == "claim_operation" else "retract",
            key=existing.key,
            value=(True if attack == "claim_operation" else None),
        )
        mutation = StateMutation(
            operation="retract",
            key=existing.key,
            expected_fact_sha256=fact_sha256(existing),
            fact=None,
        )
        delta = build_state_delta(
            base_state_sha256=checked_state.state_sha256,
            effective_at=result.state_delta.effective_at,
            receipt=result.trusted_receipt,
            mutations=(mutation,),
        )
        if attack == "retract_value":
            claim = claim.model_copy(update={"value": True})
        verified_receipt = receipt.model_copy(
            update={
                "base_state_sha256": checked_state.state_sha256,
                "derived_fact_claims": (claim,),
            }
        )

    monkeypatch.setattr(
        "self_improving.harness.system2.dispatcher.verify_trusted_tool_receipt",
        lambda artifact_store, receipt_ref: SimpleNamespace(receipt=verified_receipt),
    )

    with pytest.raises(ValueError, match=expected):
        apply_state_delta(checked_state, delta, artifact_store=store)


@pytest.mark.parametrize("operation", ["upsert", "retract"])
def test_apply_state_delta_commits_each_receipt_authorized_cas_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Once receipt verification succeeds, a matching CAS operation is applied exactly once."""

    store, _, result = _valid_compile_transition(tmp_path)
    receipt = TrustedToolReceipt.model_validate_json(
        store.resolve(result.trusted_receipt).path.read_bytes()
    )
    existing = _fact("scene.temporary", True)
    state = build_world_state((existing,), as_of=_T0)
    effective_at = _T0 + timedelta(seconds=1)
    if operation == "upsert":
        replacement = WorldFact(
            key="scene.temporary",
            value=False,
            source_kind="trusted_receipt",
            source_artifact=result.trusted_receipt,
            observed_at=effective_at,
            valid_until=None,
        )
        mutation = StateMutation(
            operation="upsert",
            key=replacement.key,
            expected_fact_sha256=fact_sha256(existing),
            fact=replacement,
        )
        claim = ReceiptDerivedFactClaim(key=replacement.key, value=False)
    else:
        mutation = StateMutation(
            operation="retract",
            key=existing.key,
            expected_fact_sha256=fact_sha256(existing),
            fact=None,
        )
        claim = ReceiptDerivedFactClaim(operation="retract", key=existing.key, value=None)
    delta = build_state_delta(
        base_state_sha256=state.state_sha256,
        effective_at=effective_at,
        receipt=result.trusted_receipt,
        mutations=(mutation,),
    )
    verified_receipt = receipt.model_copy(
        update={
            "base_state_sha256": state.state_sha256,
            "ended_at": effective_at,
            "derived_fact_claims": (claim,),
        }
    )
    monkeypatch.setattr(
        "self_improving.harness.system2.dispatcher.verify_trusted_tool_receipt",
        lambda artifact_store, receipt_ref: SimpleNamespace(receipt=verified_receipt),
    )

    updated = apply_state_delta(state, delta, artifact_store=store)

    assert {fact.key: fact.value for fact in updated.facts} == (
        {"scene.temporary": False} if operation == "upsert" else {}
    )


def test_tool_result_carries_typed_delta_receipt_diagnostics_and_fresh_observation() -> None:
    state = build_world_state((), as_of=_T0)
    observation = _fact(
        "scene.visible",
        True,
        observed_at=_T0 + timedelta(seconds=1),
    )
    receipt = _receipt()
    delta = build_state_delta(
        base_state_sha256=state.state_sha256,
        effective_at=_T0 + timedelta(seconds=1),
        receipt=receipt,
        mutations=(),
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
    receipt = _receipt()
    result = System2ToolResult(
        status=RunStatus.FAILED,
        started_at=_T0,
        ended_at=_T0 + timedelta(seconds=1),
        invocation=_artifact("invocation", "4" * 64, "harness.skill_invocation.v1"),
        run_state=_artifact("run_state", "5" * 64, "harness.run_state.v1"),
        typed_output=None,
        state_delta=None,
        diagnostics=(),
        trusted_receipt=receipt,
        fresh_observations=(),
        blocker=_blocker(),
    )

    assert result.status is RunStatus.FAILED
    assert result.state_delta is None


@pytest.mark.parametrize(
    "attack",
    [
        "time_order",
        "delta_time",
        "observation_order",
        "observation_expired",
        "observation_provenance",
        "observation_overlap",
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
    mutations: tuple[StateMutation, ...] = ()
    if attack == "observation_overlap":
        mutations = (
            StateMutation(
                operation="upsert",
                key=first.key,
                expected_fact_sha256=None,
                fact=first,
            ),
        )
    delta = build_state_delta(
        base_state_sha256=state.state_sha256,
        effective_at=effective_at,
        receipt=receipt,
        mutations=mutations,
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
    "attack",
    [
        "running",
        "success_blocker",
        "failed_output",
        "receipt",
        "old_observation",
        "missing_invocation",
        "missing_success_delta",
        "failed_delta",
    ],
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
    delta = build_state_delta(
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
    elif attack == "failed_delta":
        status = RunStatus.FAILED
        output = None
        blocker = _blocker()

    with pytest.raises(ValidationError):
        System2ToolResult(
            status=status,
            started_at=_T0,
            ended_at=_T0 + timedelta(seconds=2),
            invocation=(
                None
                if attack == "missing_invocation"
                else _artifact("invocation", "4" * 64, "harness.skill_invocation.v1")
            ),
            run_state=_artifact("run_state", "5" * 64, "harness.run_state.v1"),
            typed_output=output,
            state_delta=None if attack == "missing_success_delta" else delta,
            diagnostics=(),
            trusted_receipt=receipt,
            fresh_observations=(observation,),
            blocker=blocker,
        )
