from __future__ import annotations

import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

import self_improving.harness.system2.domain as domain_module
import self_improving.harness.system2.history as history_module
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas import ArtifactRef, RunState
from self_improving.harness.system2.context import PlannerHistoryEntry
from self_improving.harness.system2.dispatcher import TrustedToolReceipt
from self_improving.harness.system2.domain import (
    TrustedWorldState,
    apply_state_delta,
    build_world_state,
)
from self_improving.harness.system2.history import (
    PLANNER_HISTORY_AUTHORITY_SCHEMA,
    PlannerHistoryAuthority,
    publish_planner_history_authority,
    verify_planner_history_authority,
)


def _put_model(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    schema_version: str,
    value: object,
) -> ArtifactRef:
    path = root / f"{name}.json"
    path.write_bytes(history_module._canonical_json_bytes(value))
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _authority(
    *,
    current: TrustedWorldState,
    lineage: tuple[ArtifactRef, ...],
    entries: tuple[PlannerHistoryEntry, ...],
) -> PlannerHistoryAuthority:
    unbound = PlannerHistoryAuthority.model_construct(
        schema_version=PLANNER_HISTORY_AUTHORITY_SCHEMA,
        world_state_sha256=current.state_sha256,
        world_state_version=current.version,
        as_of=current.as_of,
        lineage=lineage,
        entries=entries,
        authority_sha256="0" * 64,
    )
    return PlannerHistoryAuthority.model_validate(
        {
            **unbound.model_dump(mode="python"),
            "authority_sha256": history_module._authority_sha256(unbound),
        }
    )


def _publish_authority(
    store: LocalArtifactStore,
    root: Path,
    authority: PlannerHistoryAuthority,
) -> ArtifactRef:
    return _put_model(
        store,
        root,
        name="authority",
        schema_version=PLANNER_HISTORY_AUTHORITY_SCHEMA,
        value=authority.model_dump(mode="json"),
    )


def _real_lineage(
    tmp_path: Path,
    *,
    request: str = "Place a can on top of a plate.",
):
    from tests.self_improving.harness.test_system2_dispatcher import _fixture

    fixture = _fixture(tmp_path / "compile", request=request)
    assert isinstance(fixture.state, TrustedWorldState)
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    current = apply_state_delta(
        fixture.state,
        result.state_delta,
        artifact_store=fixture.store,
    )
    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    run_state = RunState.model_validate_json(
        fixture.store.resolve(receipt.run_state).path.read_bytes()
    )
    entry = PlannerHistoryEntry(
        run_id=run_state.run_id,
        skill_ref=receipt.skill.skill_ref,
        status=receipt.status,
        ended_at=receipt.ended_at,
        receipt=result.trusted_receipt,
        blocker_code=None,
        blocker_stage=None,
    )
    return fixture.store, fixture.state, current, entry


def test_history_authority_round_trips_a_complete_receipt_bound_lineage(
    tmp_path: Path,
) -> None:
    store, initial, current, entry = _real_lineage(tmp_path)

    authority_ref = publish_planner_history_authority(
        artifact_store=store,
        scratch_root=tmp_path / "authority",
        lineage=(initial, current),
        entries=(entry,),
    )
    verified = verify_planner_history_authority(
        artifact_store=store,
        authority_ref=authority_ref,
        current_state=current,
    )

    assert verified.authority_ref == authority_ref
    assert verified.entries == (entry,)
    assert verified.lineage == (initial, current)


def test_history_authority_requires_every_lineage_transition_receipt_entry(
    tmp_path: Path,
) -> None:
    store, initial, current, _ = _real_lineage(tmp_path)

    with pytest.raises(ValueError, match="transition receipt entry"):
        publish_planner_history_authority(
            artifact_store=store,
            scratch_root=tmp_path / "authority",
            lineage=(initial, current),
            entries=(),
        )


def test_history_authority_rejects_applying_only_part_of_one_receipts_claims(
    tmp_path: Path,
) -> None:
    store, initial, current, entry = _real_lineage(tmp_path)
    partial_facts = tuple(fact for fact in current.facts if fact.key != "environment.package")
    identity = {
        "domain": "harness.trusted_world_state.identity.v1",
        "schema_version": "harness.trusted_world_state.v1",
        "version": current.version,
        "predecessor_state_sha256": current.predecessor_state_sha256,
        "as_of": current.as_of.isoformat(),
        "facts": [fact.model_dump(mode="json") for fact in partial_facts],
    }
    digest = hashlib.sha256(
        (json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    partial = TrustedWorldState(
        version=current.version,
        predecessor_state_sha256=current.predecessor_state_sha256,
        as_of=current.as_of,
        facts=partial_facts,
        state_sha256=digest,
    )

    with pytest.raises(ValueError, match="complete receipt claims"):
        publish_planner_history_authority(
            artifact_store=store,
            scratch_root=tmp_path / "authority",
            lineage=(initial, partial),
            entries=(entry,),
        )


@pytest.mark.parametrize(
    "attack",
    [
        "incomplete_lineage",
        "wrong_current_state",
        "future_entry",
        "foreign_branch_entry",
        "missing_fact_source",
    ],
)
def test_history_authority_rejects_incomplete_branch_or_unbound_evidence(
    tmp_path: Path,
    attack: str,
) -> None:
    store, initial, current, entry = _real_lineage(tmp_path / "left")
    if attack == "incomplete_lineage":
        with pytest.raises(ValueError, match="complete state lineage"):
            publish_planner_history_authority(
                artifact_store=store,
                scratch_root=tmp_path / "authority",
                lineage=(current,),
                entries=(entry,),
            )
        return
    if attack == "future_entry":
        future = entry.model_copy(update={"ended_at": current.as_of + timedelta(seconds=1)})
        with pytest.raises(ValueError, match=r"receipt.*(entry|state lineage)"):
            publish_planner_history_authority(
                artifact_store=store,
                scratch_root=tmp_path / "authority",
                lineage=(initial, current),
                entries=(future,),
            )
        return
    if attack == "foreign_branch_entry":
        other_store, _, _, other_entry = _real_lineage(
            tmp_path / "right",
            request="Place an apple on top of a plate.",
        )
        shutil.copytree(
            other_store.root / "sha256",
            store.root / "sha256",
            dirs_exist_ok=True,
        )
        with pytest.raises(ValueError, match=r"receipt.*(entry|state lineage)"):
            publish_planner_history_authority(
                artifact_store=store,
                scratch_root=tmp_path / "authority",
                lineage=(initial, current),
                entries=(other_entry,),
            )
        return

    authority_ref = publish_planner_history_authority(
        artifact_store=store,
        scratch_root=tmp_path / "authority",
        lineage=(initial, current),
        entries=(entry,),
    )
    if attack == "missing_fact_source":
        store.resolve(initial.facts[0].source_artifact).path.unlink()
        expected_current = current
    else:
        expected_current = initial
    with pytest.raises(ValueError):
        verify_planner_history_authority(
            artifact_store=store,
            authority_ref=authority_ref,
            current_state=expected_current,
        )


def test_history_authority_requires_its_exact_cas_json_contract(tmp_path: Path) -> None:
    store, initial, current, entry = _real_lineage(tmp_path)
    authority_ref = publish_planner_history_authority(
        artifact_store=store,
        scratch_root=tmp_path / "authority",
        lineage=(initial, current),
        entries=(entry,),
    )

    with pytest.raises(ValueError, match="canonical CAS JSON"):
        verify_planner_history_authority(
            artifact_store=store,
            authority_ref=authority_ref.model_copy(update={"media_type": "text/plain"}),
            current_state=current,
        )


@pytest.mark.parametrize(
    "attack,message",
    [
        ("non_utc", "UTC"),
        ("empty_lineage", "complete state lineage"),
        ("noncanonical_ref", "canonical world-state"),
        ("duplicate_lineage", "lineage refs must be unique"),
        ("unordered_entries", "newest-first"),
        ("duplicate_entry", "unique run and receipt"),
        ("digest", "digest is not canonical"),
    ],
)
def test_history_authority_model_rejects_noncanonical_published_envelopes(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    """The public authority document is canonical before it is admitted to CAS."""

    store, initial, current, entry = _real_lineage(tmp_path)
    first = _put_model(
        store,
        tmp_path,
        name="initial-state",
        schema_version="harness.trusted_world_state.v1",
        value=initial.model_dump(mode="json"),
    )
    second = _put_model(
        store,
        tmp_path,
        name="current-state",
        schema_version="harness.trusted_world_state.v1",
        value=current.model_dump(mode="json"),
    )
    authority = _authority(current=current, lineage=(first, second), entries=(entry,))
    as_of = authority.as_of
    lineage = authority.lineage
    entries = authority.entries
    if attack == "non_utc":
        as_of = current.as_of.astimezone(history_module.timezone(timedelta(hours=8)))
    elif attack == "empty_lineage":
        lineage = ()
    elif attack == "noncanonical_ref":
        lineage = (first.model_copy(update={"media_type": "text/plain"}), second)
    elif attack == "duplicate_lineage":
        lineage = (first, first)
    elif attack == "unordered_entries":
        older = entry.model_copy(
            update={
                "run_id": UUID("00000000-0000-4000-8000-000000000124"),
                "receipt": entry.receipt.model_copy(
                    update={"sha256": "8" * 64, "uri": f"artifact://sha256/{'8' * 64}"}
                ),
                "ended_at": entry.ended_at - timedelta(seconds=1),
            }
        )
        entries = (older, entry)
    elif attack == "duplicate_entry":
        entries = (entry, entry)
    else:
        payload = authority.model_dump(mode="python")
        payload["authority_sha256"] = "0" * 64
        with pytest.raises(ValueError, match=message):
            PlannerHistoryAuthority.model_validate(payload)
        return
    unbound = authority.model_copy(
        update={
            "as_of": as_of,
            "lineage": lineage,
            "entries": entries,
            "authority_sha256": "0" * 64,
        }
    )
    payload = unbound.model_dump(mode="python")
    payload["authority_sha256"] = history_module._authority_sha256(unbound)

    with pytest.raises(ValueError, match=message):
        PlannerHistoryAuthority.model_validate(payload)


def test_history_publication_and_verification_require_an_exact_local_cas(tmp_path: Path) -> None:
    """History cannot be published or read through a protocol-shaped foreign store."""

    store, initial, current, entry = _real_lineage(tmp_path)
    with pytest.raises(TypeError, match="LocalArtifactStore"):
        publish_planner_history_authority(  # type: ignore[arg-type]
            artifact_store=object(),
            scratch_root=tmp_path / "authority",
            lineage=(initial, current),
            entries=(entry,),
        )
    authority_ref = publish_planner_history_authority(
        artifact_store=store,
        scratch_root=tmp_path / "authority",
        lineage=(initial, current),
        entries=(entry,),
    )
    with pytest.raises(TypeError, match="LocalArtifactStore"):
        verify_planner_history_authority(  # type: ignore[arg-type]
            artifact_store=object(),
            authority_ref=authority_ref,
            current_state=current,
        )


def test_history_verification_rejects_noncanonical_authority_bytes(tmp_path: Path) -> None:
    """A hash-valid CAS object still cannot use noncanonical JSON encoding."""

    store, initial, current, entry = _real_lineage(tmp_path)
    authority_ref = publish_planner_history_authority(
        artifact_store=store,
        scratch_root=tmp_path / "authority",
        lineage=(initial, current),
        entries=(entry,),
    )
    payload = store.resolve(authority_ref).path.read_text(encoding="utf-8")
    path = tmp_path / "noncanonical-authority.json"
    path.write_text(payload.replace(",", ", ", 1), encoding="utf-8")
    noncanonical_ref = store.put_file(
        path,
        name="noncanonical_authority",
        media_type="application/json",
        schema_version=PLANNER_HISTORY_AUTHORITY_SCHEMA,
    )

    with pytest.raises(ValueError, match="JSON is not canonical"):
        verify_planner_history_authority(
            artifact_store=store,
            authority_ref=noncanonical_ref,
            current_state=current,
        )


def test_history_verification_rejects_noncanonical_lineage_state_bytes(tmp_path: Path) -> None:
    store, initial, current, entry = _real_lineage(tmp_path)
    pretty_path = tmp_path / "pretty-initial-state.json"
    pretty_path.write_text(
        json.dumps(initial.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    pretty_initial = store.put_file(
        pretty_path,
        name="pretty_initial_state",
        media_type="application/json",
        schema_version="harness.trusted_world_state.v1",
    )
    current_ref = _put_model(
        store,
        tmp_path,
        name="canonical_current_state",
        schema_version="harness.trusted_world_state.v1",
        value=current.model_dump(mode="json"),
    )
    authority_ref = _publish_authority(
        store,
        tmp_path,
        _authority(current=current, lineage=(pretty_initial, current_ref), entries=(entry,)),
    )

    with pytest.raises(ValueError, match="lineage world state JSON is not canonical"):
        verify_planner_history_authority(
            artifact_store=store,
            authority_ref=authority_ref,
            current_state=current,
        )


def test_history_publication_rejects_noncanonical_world_fact_evidence(tmp_path: Path) -> None:
    store, initial, _, _ = _real_lineage(tmp_path)
    fact = initial.facts[0]
    evidence = json.loads(store.resolve(fact.source_artifact).path.read_bytes())
    pretty_path = tmp_path / "pretty-world-fact-evidence.json"
    pretty_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    pretty_evidence = store.put_file(
        pretty_path,
        name="pretty_world_fact_evidence",
        media_type="application/json",
        schema_version="harness.world_fact_evidence.v1",
    )
    rebound = build_world_state(
        (fact.model_copy(update={"source_artifact": pretty_evidence}),),
        as_of=initial.as_of,
    )

    with pytest.raises(ValueError, match="world-fact evidence JSON is not canonical"):
        publish_planner_history_authority(
            artifact_store=store,
            scratch_root=tmp_path / "pretty-evidence-authority",
            lineage=(rebound,),
            entries=(),
        )


def test_history_publication_rejects_an_empty_lineage(tmp_path: Path) -> None:
    """A history snapshot has no meaning without its complete state chain."""

    store = LocalArtifactStore(tmp_path / "cas")
    with pytest.raises(ValueError, match="complete state lineage"):
        publish_planner_history_authority(
            artifact_store=store,
            scratch_root=tmp_path / "authority",
            lineage=(),
            entries=(),
        )


@pytest.mark.parametrize(
    "attack,message",
    [
        ("receipt_in_initial", "initial world state"),
        ("noncontiguous_version", "versions are not contiguous"),
        ("wrong_predecessor", "predecessor or time lineage"),
        ("receipt_claim_mismatch", "not justified"),
        ("fresh_evidence_mismatch", "source evidence is not cross-bound"),
        ("derived_fact", "re-computable authority"),
        ("implicit_retract", "retracts facts"),
        ("empty_transition", "without a receipt-backed mutation"),
        ("nonreceipt_transition", "mutation is not receipt-backed"),
        ("wrong_receipt_target", "targets another state transition"),
    ],
)
def test_history_verification_rejects_every_unjustified_lineage_transition(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    """Each lineage version must remain fully explained by source evidence and receipts."""

    store, initial, current, entry = _real_lineage(tmp_path)
    initial_ref = _put_model(
        store,
        tmp_path,
        name="lineage-initial",
        schema_version="harness.trusted_world_state.v1",
        value=initial.model_dump(mode="json"),
    )
    current_ref = _put_model(
        store,
        tmp_path,
        name="lineage-current",
        schema_version="harness.trusted_world_state.v1",
        value=current.model_dump(mode="json"),
    )
    checked_current = current
    entries: tuple[PlannerHistoryEntry, ...] = (entry,)
    lineage_refs = (initial_ref, current_ref)

    if attack == "receipt_in_initial":
        forged_fact = initial.facts[0].model_copy(
            update={"source_kind": "trusted_receipt", "source_artifact": entry.receipt}
        )
        forged_initial = build_world_state((forged_fact,), as_of=initial.as_of)
        forged_ref = _put_model(
            store,
            tmp_path,
            name="receipt-in-initial",
            schema_version="harness.trusted_world_state.v1",
            value=forged_initial.model_dump(mode="json"),
        )
        lineage_refs = (forged_ref, current_ref)
    elif attack == "noncontiguous_version":
        forged_middle = domain_module._build_world_state_version(
            (),
            as_of=current.as_of,
            version=3,
            predecessor_state_sha256="1" * 64,
        )
        middle_ref = _put_model(
            store,
            tmp_path,
            name="noncontiguous-middle",
            schema_version="harness.trusted_world_state.v1",
            value=forged_middle.model_dump(mode="json"),
        )
        checked_current = domain_module._build_world_state_version(
            current.facts,
            as_of=current.as_of,
            version=3,
            predecessor_state_sha256=forged_middle.state_sha256,
        )
        final_ref = _put_model(
            store,
            tmp_path,
            name="noncontiguous-final",
            schema_version="harness.trusted_world_state.v1",
            value=checked_current.model_dump(mode="json"),
        )
        lineage_refs = (initial_ref, middle_ref, final_ref)
        entries = ()
    elif attack == "wrong_predecessor":
        checked_current = domain_module._build_world_state_version(
            current.facts,
            as_of=current.as_of,
            version=2,
            predecessor_state_sha256="2" * 64,
        )
        forged_ref = _put_model(
            store,
            tmp_path,
            name="wrong-predecessor",
            schema_version="harness.trusted_world_state.v1",
            value=checked_current.model_dump(mode="json"),
        )
        lineage_refs = (initial_ref, forged_ref)
        entries = ()
    elif attack == "receipt_claim_mismatch":
        facts = list(current.facts)
        facts[0] = facts[0].model_copy(update={"value": {"forged": True}})
        checked_current = domain_module._build_world_state_version(
            tuple(facts),
            as_of=current.as_of,
            version=2,
            predecessor_state_sha256=initial.state_sha256,
        )
        forged_ref = _put_model(
            store,
            tmp_path,
            name="claim-mismatch",
            schema_version="harness.trusted_world_state.v1",
            value=checked_current.model_dump(mode="json"),
        )
        lineage_refs = (initial_ref, forged_ref)
    elif attack in {"fresh_evidence_mismatch", "derived_fact"}:
        source_kind = "fresh_observation" if attack == "fresh_evidence_mismatch" else "derived"
        forged_fact = initial.facts[0].model_copy(
            update={"source_kind": source_kind, "value": "forged objective"}
        )
        checked_current = build_world_state((forged_fact,), as_of=initial.as_of)
        forged_ref = _put_model(
            store,
            tmp_path,
            name=f"{attack}-initial",
            schema_version="harness.trusted_world_state.v1",
            value=checked_current.model_dump(mode="json"),
        )
        lineage_refs = (forged_ref,)
        entries = ()
    elif attack == "implicit_retract":
        checked_current = domain_module._build_world_state_version(
            (),
            as_of=current.as_of,
            version=2,
            predecessor_state_sha256=initial.state_sha256,
        )
        forged_ref = _put_model(
            store,
            tmp_path,
            name="implicit-retract",
            schema_version="harness.trusted_world_state.v1",
            value=checked_current.model_dump(mode="json"),
        )
        lineage_refs = (initial_ref, forged_ref)
        entries = ()
    elif attack == "empty_transition":
        checked_current = domain_module._build_world_state_version(
            initial.facts,
            as_of=current.as_of,
            version=2,
            predecessor_state_sha256=initial.state_sha256,
        )
        forged_ref = _put_model(
            store,
            tmp_path,
            name="empty-transition",
            schema_version="harness.trusted_world_state.v1",
            value=checked_current.model_dump(mode="json"),
        )
        lineage_refs = (initial_ref, forged_ref)
        entries = ()
    elif attack == "nonreceipt_transition":
        observed = initial.facts[0].model_copy(update={"key": "scene.extra", "value": True})
        checked_current = domain_module._build_world_state_version(
            (*initial.facts, observed),
            as_of=current.as_of,
            version=2,
            predecessor_state_sha256=initial.state_sha256,
        )
        forged_ref = _put_model(
            store,
            tmp_path,
            name="nonreceipt-transition",
            schema_version="harness.trusted_world_state.v1",
            value=checked_current.model_dump(mode="json"),
        )
        lineage_refs = (initial_ref, forged_ref)
        entries = ()
    else:
        checked_current = domain_module._build_world_state_version(
            current.facts,
            as_of=current.as_of + timedelta(seconds=1),
            version=2,
            predecessor_state_sha256=initial.state_sha256,
        )
        forged_ref = _put_model(
            store,
            tmp_path,
            name="wrong-receipt-target",
            schema_version="harness.trusted_world_state.v1",
            value=checked_current.model_dump(mode="json"),
        )
        lineage_refs = (initial_ref, forged_ref)

    authority = _authority(current=checked_current, lineage=lineage_refs, entries=entries)
    authority_ref = _publish_authority(store, tmp_path, authority)

    with pytest.raises(ValueError, match=message):
        verify_planner_history_authority(
            artifact_store=store,
            authority_ref=authority_ref,
            current_state=checked_current,
        )


def test_history_verification_detects_a_cas_object_changed_after_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified authority reference cannot be redirected to changed bytes."""

    store, initial, current, entry = _real_lineage(tmp_path)
    authority_ref = publish_planner_history_authority(
        artifact_store=store,
        scratch_root=tmp_path / "authority",
        lineage=(initial, current),
        entries=(entry,),
    )
    changed = tmp_path / "changed-authority.json"
    changed.write_bytes(b"changed")
    resolve = store.resolve
    monkeypatch.setattr(
        store,
        "resolve",
        lambda ref: (
            type("Resolved", (), {"path": changed})() if ref == authority_ref else resolve(ref)
        ),
    )

    with pytest.raises(ValueError, match="changed while it was read"):
        verify_planner_history_authority(
            artifact_store=store,
            authority_ref=authority_ref,
            current_state=current,
        )
