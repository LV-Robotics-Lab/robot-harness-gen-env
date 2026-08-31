"""Complete, receipt-bound history authority for System 2 context projection."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from ..artifacts import ArtifactResolutionError, LocalArtifactStore
from ..schemas import ArtifactRef, RunStatus
from ..schemas.base import HarnessModel, Sha256
from .context import PlannerHistoryEntry
from .domain import (
    TrustedWorldState,
    WorldFact,
    WorldFactEvidence,
    _parse_canonical_model,
    fact_sha256,
)

PLANNER_HISTORY_AUTHORITY_SCHEMA = "harness.planner_history_authority.v1"
_WORLD_STATE_SCHEMA = "harness.trusted_world_state.v1"
_CAS_PREFIX = "artifact://sha256/"


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _json_equal(left: object, right: object) -> bool:
    return _canonical_json_bytes(left) == _canonical_json_bytes(right)


def _authority_sha256(authority: "PlannerHistoryAuthority") -> str:
    payload = {
        "domain": "harness.planner_history_authority.identity.v1",
        "authority": authority.model_dump(mode="json", exclude={"authority_sha256"}),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _history_sort_key(entry: PlannerHistoryEntry) -> tuple[float, str, str]:
    return (-entry.ended_at.timestamp(), entry.skill_ref, entry.run_id.hex)


class PlannerHistoryAuthority(HarnessModel):
    """One immutable full-history snapshot bound to a complete state lineage."""

    schema_version: Literal["harness.planner_history_authority.v1"] = (
        PLANNER_HISTORY_AUTHORITY_SCHEMA
    )
    world_state_sha256: Sha256
    world_state_version: Annotated[int, Field(strict=True, ge=1)]
    as_of: AwareDatetime
    lineage: tuple[ArtifactRef, ...]
    entries: tuple[PlannerHistoryEntry, ...]
    authority_sha256: Sha256

    @model_validator(mode="after")
    def authority_is_canonical(self) -> "PlannerHistoryAuthority":
        if self.as_of.utcoffset() != timezone.utc.utcoffset(self.as_of):
            raise ValueError("history authority as_of must use UTC")
        if not self.lineage:
            raise ValueError("history authority requires a complete state lineage")
        for ref in self.lineage:
            if (
                ref.uri != f"{_CAS_PREFIX}{ref.sha256}"
                or ref.media_type != "application/json"
                or ref.schema_version != _WORLD_STATE_SCHEMA
            ):
                raise ValueError("history lineage must contain canonical world-state CAS refs")
        identities = [(ref.sha256, ref.bytes) for ref in self.lineage]
        if len(identities) != len(set(identities)):
            raise ValueError("history lineage refs must be unique")
        if list(self.entries) != sorted(self.entries, key=_history_sort_key):
            raise ValueError("history authority entries must use canonical newest-first order")
        run_ids = [entry.run_id for entry in self.entries]
        receipts = [entry.receipt.sha256 for entry in self.entries]
        if len(run_ids) != len(set(run_ids)) or len(receipts) != len(set(receipts)):
            raise ValueError(
                "history authority entries must have unique run and receipt identities"
            )
        if self.authority_sha256 != _authority_sha256(self):
            raise ValueError("history authority digest is not canonical")
        return self


@dataclass(frozen=True, slots=True)
class VerifiedPlannerHistoryAuthority:
    """Path-free verified authority facts safe for context reprojection."""

    authority_ref: ArtifactRef
    authority: PlannerHistoryAuthority
    lineage: tuple[TrustedWorldState, ...]
    entries: tuple[PlannerHistoryEntry, ...]


def publish_planner_history_authority(
    *,
    artifact_store: LocalArtifactStore,
    scratch_root: Path,
    lineage: tuple[TrustedWorldState, ...],
    entries: tuple[PlannerHistoryEntry, ...],
) -> ArtifactRef:
    """Validate and publish one complete state/history authority snapshot."""

    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be an exact LocalArtifactStore")
    trusted_lineage = tuple(
        TrustedWorldState.model_validate(state.model_dump(mode="python")) for state in lineage
    )
    if not trusted_lineage:
        raise ValueError("history authority requires a complete state lineage")
    lineage_refs = tuple(
        _publish_bytes(
            artifact_store,
            scratch_root,
            payload=_canonical_json_bytes(state.model_dump(mode="json")),
            name=f"trusted_world_state_v{state.version}",
            schema_version=_WORLD_STATE_SCHEMA,
        )
        for state in trusted_lineage
    )
    ordered_entries = tuple(sorted(entries, key=_history_sort_key))
    unbound = PlannerHistoryAuthority.model_construct(
        schema_version=PLANNER_HISTORY_AUTHORITY_SCHEMA,
        world_state_sha256=trusted_lineage[-1].state_sha256,
        world_state_version=trusted_lineage[-1].version,
        as_of=trusted_lineage[-1].as_of,
        lineage=lineage_refs,
        entries=ordered_entries,
        authority_sha256="0" * 64,
    )
    authority = PlannerHistoryAuthority.model_validate(
        {
            **unbound.model_dump(mode="python"),
            "authority_sha256": _authority_sha256(unbound),
        }
    )
    authority_ref = _publish_bytes(
        artifact_store,
        scratch_root,
        payload=_canonical_json_bytes(authority.model_dump(mode="json")),
        name="planner_history_authority",
        schema_version=PLANNER_HISTORY_AUTHORITY_SCHEMA,
    )
    verify_planner_history_authority(
        artifact_store=artifact_store,
        authority_ref=authority_ref,
        current_state=trusted_lineage[-1],
    )
    return authority_ref


def verify_planner_history_authority(
    *,
    artifact_store: LocalArtifactStore,
    authority_ref: ArtifactRef,
    current_state: TrustedWorldState,
) -> VerifiedPlannerHistoryAuthority:
    """Rebuild a full history snapshot and all receipt/source evidence from CAS."""

    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be an exact LocalArtifactStore")
    if (
        authority_ref.uri != f"{_CAS_PREFIX}{authority_ref.sha256}"
        or authority_ref.media_type != "application/json"
        or authority_ref.schema_version != PLANNER_HISTORY_AUTHORITY_SCHEMA
    ):
        raise ValueError("history authority must be a canonical CAS JSON ref")
    authority_bytes = _read_verified_ref(artifact_store, authority_ref)
    authority = _parse_canonical_model(
        PlannerHistoryAuthority,
        authority_bytes,
        label="history authority",
    )
    trusted_current = TrustedWorldState.model_validate(current_state.model_dump(mode="python"))
    if (
        authority.world_state_sha256 != trusted_current.state_sha256
        or authority.world_state_version != trusted_current.version
        or authority.as_of != trusted_current.as_of
    ):
        raise ValueError("history authority is not bound to the current world state")
    lineage = tuple(
        _parse_canonical_model(
            TrustedWorldState,
            _read_verified_ref(artifact_store, ref),
            label="history lineage world state",
        )
        for ref in authority.lineage
    )
    transition_receipts = _verify_lineage(artifact_store, lineage, trusted_current)
    _verify_entries(
        artifact_store,
        authority.entries,
        lineage,
        trusted_current,
        transition_receipts=transition_receipts,
    )
    return VerifiedPlannerHistoryAuthority(
        authority_ref=authority_ref,
        authority=authority,
        lineage=lineage,
        entries=authority.entries,
    )


def _verify_lineage(
    artifact_store: LocalArtifactStore,
    lineage: tuple[TrustedWorldState, ...],
    current_state: TrustedWorldState,
) -> frozenset[str]:
    if (
        not lineage
        or lineage[0].version != 1
        or lineage[0].predecessor_state_sha256 is not None
        or len(lineage) != current_state.version
        or lineage[-1] != current_state
    ):
        raise ValueError("history authority does not contain the complete state lineage")
    if any(fact.source_kind == "trusted_receipt" for fact in lineage[0].facts):
        raise ValueError("initial world state cannot contain receipt-derived facts")
    transition_receipts: set[str] = set()
    for index, state in enumerate(lineage):
        if state.version != index + 1:
            raise ValueError("history state versions are not contiguous")
        if index:
            prior = lineage[index - 1]
            if state.predecessor_state_sha256 != prior.state_sha256 or state.as_of < prior.as_of:
                raise ValueError("history state predecessor or time lineage is invalid")
            transition_receipts.update(_verify_transition(artifact_store, prior, state))
        _verify_state_sources(artifact_store, state)
    return frozenset(transition_receipts)


def _verify_state_sources(
    artifact_store: LocalArtifactStore,
    state: TrustedWorldState,
) -> None:
    from .dispatcher import verify_trusted_tool_receipt

    for fact in state.facts:
        if fact.source_kind == "trusted_receipt":
            receipt = verify_trusted_tool_receipt(
                artifact_store,
                fact.source_artifact,
            ).receipt
            if receipt.status is not RunStatus.SUCCEEDED or not any(
                claim.operation == "upsert"
                and claim.key == fact.key
                and _json_equal(claim.value, fact.value)
                for claim in receipt.derived_fact_claims
            ):
                raise ValueError("world-state fact is not justified by its trusted receipt")
        elif fact.source_kind in {"user_input", "fresh_observation"}:
            evidence = _parse_canonical_model(
                WorldFactEvidence,
                _read_verified_ref(artifact_store, fact.source_artifact),
                label="history world-fact evidence",
            )
            if (
                evidence.source_kind != fact.source_kind
                or evidence.key != fact.key
                or not _json_equal(evidence.value, fact.value)
                or evidence.observed_at != fact.observed_at
                or evidence.valid_until != fact.valid_until
            ):
                raise ValueError("world-state fact source evidence is not cross-bound")
        else:
            raise ValueError("derived world-state facts require a re-computable authority")


def _verify_transition(
    artifact_store: LocalArtifactStore,
    prior: TrustedWorldState,
    current: TrustedWorldState,
) -> frozenset[str]:
    from .dispatcher import verify_trusted_tool_receipt

    prior_facts = {fact.key: fact for fact in prior.facts}
    current_facts = {fact.key: fact for fact in current.facts}
    if set(prior_facts) - set(current_facts):
        raise ValueError("state lineage retracts facts without an explicit transition authority")
    changed = tuple(
        fact
        for key, fact in current_facts.items()
        if key not in prior_facts or fact_sha256(prior_facts[key]) != fact_sha256(fact)
    )
    if not changed:
        raise ValueError("state lineage advanced without a receipt-backed mutation")
    by_receipt: dict[str, list[WorldFact]] = {}
    receipt_refs: dict[str, ArtifactRef] = {}
    for fact in changed:
        if fact.source_kind != "trusted_receipt":
            raise ValueError("state lineage mutation is not receipt-backed")
        receipt_refs[fact.source_artifact.sha256] = fact.source_artifact
        by_receipt.setdefault(fact.source_artifact.sha256, []).append(fact)
    for digest, facts in by_receipt.items():
        receipt = verify_trusted_tool_receipt(artifact_store, receipt_refs[digest]).receipt
        if receipt.base_state_sha256 != prior.state_sha256 or receipt.ended_at != current.as_of:
            raise ValueError("state lineage mutation receipt targets another state transition")
        expected_upserts = {
            claim.key for claim in receipt.derived_fact_claims if claim.operation == "upsert"
        }
        expected_retracts = {
            claim.key for claim in receipt.derived_fact_claims if claim.operation == "retract"
        }
        actual_upserts = {fact.key for fact in facts}
        if expected_retracts or actual_upserts != expected_upserts:
            raise ValueError("state lineage transition does not apply the complete receipt claims")
    return frozenset(by_receipt)


def _verify_entries(
    artifact_store: LocalArtifactStore,
    entries: tuple[PlannerHistoryEntry, ...],
    lineage: tuple[TrustedWorldState, ...],
    current_state: TrustedWorldState,
    *,
    transition_receipts: frozenset[str],
) -> None:
    from .dispatcher import verify_trusted_tool_receipt

    state_digests = {state.state_sha256 for state in lineage}
    entry_receipts = {entry.receipt.sha256 for entry in entries}
    if not transition_receipts.issubset(entry_receipts):
        raise ValueError("history authority omits a transition receipt entry")
    for entry in entries:
        verified = verify_trusted_tool_receipt(artifact_store, entry.receipt)
        receipt = verified.receipt
        run_state = verified.run_state
        blocker_code = receipt.blocker.code if receipt.blocker is not None else None
        blocker_stage = receipt.blocker.stage if receipt.blocker is not None else None
        if (
            run_state.run_id != entry.run_id
            or receipt.skill.skill_ref != entry.skill_ref
            or receipt.status is not entry.status
            or receipt.ended_at != entry.ended_at
            or blocker_code != entry.blocker_code
            or blocker_stage != entry.blocker_stage
            or entry.ended_at > current_state.as_of
            or receipt.base_state_sha256 not in state_digests
        ):
            raise ValueError("planner history entry is not bound to its receipt and state lineage")


def _read_verified_ref(store: LocalArtifactStore, ref: ArtifactRef) -> bytes:
    try:
        payload = store.resolve(ref).path.read_bytes()
    except (ArtifactResolutionError, OSError) as error:
        raise ValueError("history authority artifact is unavailable") from error
    if len(payload) != ref.bytes or hashlib.sha256(payload).hexdigest() != ref.sha256:
        raise ValueError("history authority artifact changed while it was read")
    return payload


def _publish_bytes(
    store: LocalArtifactStore,
    scratch_root: Path,
    *,
    payload: bytes,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    root = scratch_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=root, prefix=".history-", suffix=".json")
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return store.put_file(
            temporary_path,
            name=name,
            media_type="application/json",
            schema_version=schema_version,
        )
    finally:
        temporary_path.unlink(missing_ok=True)
