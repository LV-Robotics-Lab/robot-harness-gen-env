"""Actor-neutral trusted world-state types and compare-and-swap transition kernel."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .schemas.base import HarnessModel, Sha256
from .schemas.common import ArtifactRef

WORLD_STATE_SCHEMA = "harness.trusted_world_state.v1"
WORLD_FACT_SCHEMA = "harness.world_fact.v1"
WORLD_FACT_EVIDENCE_SCHEMA = "harness.world_fact_evidence.v1"

_FACT_KEY_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
_ARTIFACT_URI = re.compile(r"^artifact://sha256/([0-9a-f]{64})$")
FactKey = Annotated[str, Field(strict=True, pattern=_FACT_KEY_PATTERN, max_length=255)]


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


def _require_utc(value: datetime, *, label: str) -> None:
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{label} must use UTC")


def _require_cas_artifact(
    artifact: ArtifactRef,
    *,
    label: str,
    schema_version: str | None = None,
) -> None:
    match = _ARTIFACT_URI.fullmatch(artifact.uri)
    if match is None or match.group(1) != artifact.sha256:
        raise ValueError(f"{label} must use its content-addressed artifact URI")
    if schema_version is not None and artifact.schema_version != schema_version:
        raise ValueError(f"{label} must have schema_version={schema_version}")


class WorldFact(HarnessModel):
    """One trusted, time-bounded fact available to an orchestration caller."""

    schema_version: Literal["harness.world_fact.v1"] = WORLD_FACT_SCHEMA
    key: FactKey
    value: JsonValue
    source_kind: Literal[
        "user_input",
        "fresh_observation",
        "trusted_receipt",
        "derived",
    ]
    source_artifact: ArtifactRef
    observed_at: AwareDatetime
    valid_until: AwareDatetime | None

    @model_validator(mode="after")
    def fact_is_trusted_and_temporally_ordered(self) -> "WorldFact":
        _require_utc(self.observed_at, label="observed_at")
        if self.valid_until is not None:
            _require_utc(self.valid_until, label="valid_until")
            if self.valid_until < self.observed_at:
                raise ValueError("valid_until cannot precede observed_at")
        _require_cas_artifact(self.source_artifact, label="source_artifact")
        return self


class WorldFactEvidence(HarnessModel):
    """Content-addressed source envelope for a non-receipt world fact."""

    schema_version: Literal["harness.world_fact_evidence.v1"] = WORLD_FACT_EVIDENCE_SCHEMA
    source_kind: Literal["user_input", "fresh_observation"]
    key: FactKey
    value: JsonValue
    observed_at: AwareDatetime
    valid_until: AwareDatetime | None

    @model_validator(mode="after")
    def evidence_is_temporally_ordered(self) -> "WorldFactEvidence":
        _require_utc(self.observed_at, label="observed_at")
        if self.valid_until is not None:
            _require_utc(self.valid_until, label="valid_until")
            if self.valid_until < self.observed_at:
                raise ValueError("valid_until cannot precede observed_at")
        return self


def fact_sha256(fact: WorldFact) -> str:
    """Return the domain-separated identity of one complete fact."""

    payload = {
        "domain": "harness.world_fact.identity.v1",
        "fact": fact.model_dump(mode="json"),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _world_state_sha256(
    *,
    version: int,
    predecessor_state_sha256: str | None,
    as_of: datetime,
    facts: tuple[WorldFact, ...],
) -> str:
    payload = {
        "domain": "harness.trusted_world_state.identity.v1",
        "schema_version": WORLD_STATE_SCHEMA,
        "version": version,
        "predecessor_state_sha256": predecessor_state_sha256,
        "as_of": as_of.isoformat(),
        "facts": [fact.model_dump(mode="json") for fact in facts],
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


class TrustedWorldState(HarnessModel):
    """An immutable, canonically hashed orchestration state."""

    schema_version: Literal["harness.trusted_world_state.v1"] = WORLD_STATE_SCHEMA
    version: Annotated[int, Field(strict=True, ge=1)]
    predecessor_state_sha256: Sha256 | None
    as_of: AwareDatetime
    facts: tuple[WorldFact, ...]
    state_sha256: Sha256

    @model_validator(mode="after")
    def state_is_canonical_and_fresh(self) -> "TrustedWorldState":
        _require_utc(self.as_of, label="as_of")
        if (self.version == 1) != (self.predecessor_state_sha256 is None):
            raise ValueError("only version 1 may omit predecessor_state_sha256")
        keys = [fact.key for fact in self.facts]
        if keys != sorted(keys):
            raise ValueError("world facts must be sorted by key")
        if len(keys) != len(set(keys)):
            raise ValueError("world fact keys must be unique")
        for fact in self.facts:
            if fact.observed_at > self.as_of:
                raise ValueError(f"world fact is from the future: {fact.key}")
            if fact.valid_until is not None and fact.valid_until < self.as_of:
                raise ValueError(f"world fact is expired: {fact.key}")
        wanted = _world_state_sha256(
            version=self.version,
            predecessor_state_sha256=self.predecessor_state_sha256,
            as_of=self.as_of,
            facts=self.facts,
        )
        if self.state_sha256 != wanted:
            raise ValueError("state_sha256 does not match canonical world state")
        return self


def _build_world_state_version(
    facts: tuple[WorldFact, ...],
    *,
    as_of: datetime,
    version: int,
    predecessor_state_sha256: str | None,
) -> TrustedWorldState:
    ordered = tuple(sorted(facts, key=lambda fact: fact.key))
    digest = _world_state_sha256(
        version=version,
        predecessor_state_sha256=predecessor_state_sha256,
        as_of=as_of,
        facts=ordered,
    )
    return TrustedWorldState(
        version=version,
        predecessor_state_sha256=predecessor_state_sha256,
        as_of=as_of,
        facts=ordered,
        state_sha256=digest,
    )


def build_world_state(
    facts: tuple[WorldFact, ...],
    *,
    as_of: datetime,
) -> TrustedWorldState:
    """Build the first canonical state from an unordered fact collection."""

    return _build_world_state_version(
        facts,
        as_of=as_of,
        version=1,
        predecessor_state_sha256=None,
    )


class StateMutation(HarnessModel):
    """One compare-and-swap fact mutation."""

    operation: Literal["upsert", "retract"]
    key: FactKey
    expected_fact_sha256: Sha256 | None
    fact: WorldFact | None

    @model_validator(mode="after")
    def mutation_shape_matches_operation(self) -> "StateMutation":
        if self.operation == "upsert":
            if self.fact is None or self.fact.key != self.key:
                raise ValueError("upsert requires a fact with the same key")
        elif self.fact is not None or self.expected_fact_sha256 is None:
            raise ValueError("retract requires an expected prior digest and no fact")
        return self


def advance_trusted_world_state(
    state: TrustedWorldState,
    *,
    effective_at: datetime,
    mutations: tuple[StateMutation, ...],
) -> TrustedWorldState:
    """Apply validated CAS mutations after a caller verifies their evidence authority."""

    try:
        trusted_state = TrustedWorldState.model_validate(state.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("current world state failed its canonical integrity check") from error
    _require_utc(effective_at, label="effective_at")
    try:
        trusted_mutations = tuple(
            StateMutation.model_validate(mutation.model_dump(mode="python"))
            for mutation in mutations
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("state mutations failed their canonical integrity check") from error
    keys = [mutation.key for mutation in trusted_mutations]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError("state mutations must be sorted and unique by key")
    if effective_at < trusted_state.as_of:
        raise ValueError("state delta cannot move world-state time backwards")
    current = {fact.key: fact for fact in trusted_state.facts}
    for mutation in trusted_mutations:
        existing = current.get(mutation.key)
        if mutation.expected_fact_sha256 is None:
            if existing is not None:
                raise ValueError(f"blind overwrite is forbidden: {mutation.key}")
        elif existing is None or fact_sha256(existing) != mutation.expected_fact_sha256:
            raise ValueError(f"prior fact identity mismatch: {mutation.key}")
        if mutation.operation == "upsert":
            assert mutation.fact is not None
            current[mutation.key] = mutation.fact
        else:
            del current[mutation.key]
    return _build_world_state_version(
        tuple(current.values()),
        as_of=effective_at,
        version=trusted_state.version + 1,
        predecessor_state_sha256=trusted_state.state_sha256,
    )


__all__ = [
    "FactKey",
    "StateMutation",
    "TrustedWorldState",
    "WorldFact",
    "WorldFactEvidence",
    "advance_trusted_world_state",
    "build_world_state",
    "fact_sha256",
]
