"""Trusted world-state and ToolResult contracts for the System 2 harness.

The planner never mutates memory implicitly.  A Skill returns one typed
``System2ToolResult`` whose state delta is compare-and-swap applied to an
immutable ``TrustedWorldState``.  Every fact and every mutation is bound to a
content-addressed artifact; mutable locators are outside this domain.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from ..schemas import ArtifactRef, Blocker, RunStatus
from ..schemas.base import HarnessModel, JsonObject, Sha256

WORLD_STATE_SCHEMA = "harness.trusted_world_state.v1"
WORLD_FACT_SCHEMA = "harness.world_fact.v1"
STATE_DELTA_SCHEMA = "harness.state_delta.v1"
TOOL_RESULT_SCHEMA = "harness.system2_tool_result.v1"
TRUSTED_RECEIPT_SCHEMA = "harness.trusted_tool_receipt.v1"
INVOCATION_SCHEMA = "harness.skill_invocation.v1"
RUN_STATE_SCHEMA = "harness.run_state.v1"

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
    """One trusted, time-bounded fact available to the planner."""

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
    """An immutable, canonically hashed planner state."""

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


class StateDelta(HarnessModel):
    """A receipt-bound set of compare-and-swap mutations."""

    schema_version: Literal["harness.state_delta.v1"] = STATE_DELTA_SCHEMA
    base_state_sha256: Sha256
    effective_at: AwareDatetime
    receipt: ArtifactRef
    mutations: tuple[StateMutation, ...]

    @model_validator(mode="after")
    def delta_is_canonical(self) -> "StateDelta":
        _require_utc(self.effective_at, label="effective_at")
        _require_cas_artifact(
            self.receipt,
            label="state delta receipt",
            schema_version=TRUSTED_RECEIPT_SCHEMA,
        )
        keys = [mutation.key for mutation in self.mutations]
        if keys != sorted(keys):
            raise ValueError("state mutations must be sorted by key")
        if len(keys) != len(set(keys)):
            raise ValueError("state mutation keys must be unique")
        return self


def apply_state_delta(state: TrustedWorldState, delta: StateDelta) -> TrustedWorldState:
    """Apply a delta only when every prior fact identity still matches."""

    try:
        trusted_state = TrustedWorldState.model_validate(state.model_dump(mode="python"))
    except ValueError as exc:
        raise ValueError("current world state failed its canonical integrity check") from exc
    if delta.base_state_sha256 != trusted_state.state_sha256:
        raise ValueError("state delta base does not match current world state")
    current = {fact.key: fact for fact in trusted_state.facts}
    for mutation in delta.mutations:
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
        as_of=delta.effective_at,
        version=trusted_state.version + 1,
        predecessor_state_sha256=trusted_state.state_sha256,
    )


class System2ToolResult(HarnessModel):
    """One complete Skill result consumed by the System 2 planner."""

    schema_version: Literal["harness.system2_tool_result.v1"] = TOOL_RESULT_SCHEMA
    status: RunStatus
    started_at: AwareDatetime
    ended_at: AwareDatetime
    invocation: ArtifactRef
    run_state: ArtifactRef
    typed_output: JsonObject | None
    state_delta: StateDelta
    diagnostics: tuple[ArtifactRef, ...]
    trusted_receipt: ArtifactRef
    fresh_observations: tuple[WorldFact, ...]
    blocker: Blocker | None

    @model_validator(mode="after")
    def result_is_complete_and_consistent(self) -> "System2ToolResult":
        _require_utc(self.started_at, label="started_at")
        _require_utc(self.ended_at, label="ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        if self.status == RunStatus.RUNNING:
            raise ValueError("ToolResult must be terminal")
        if self.status == RunStatus.SUCCEEDED:
            if self.typed_output is None or self.blocker is not None:
                raise ValueError("succeeded ToolResult requires output and no blocker")
        elif self.typed_output is not None or self.blocker is None:
            raise ValueError("blocked/failed ToolResult requires blocker and no output")
        _require_cas_artifact(
            self.invocation,
            label="invocation",
            schema_version=INVOCATION_SCHEMA,
        )
        _require_cas_artifact(
            self.run_state,
            label="run_state",
            schema_version=RUN_STATE_SCHEMA,
        )
        _require_cas_artifact(
            self.trusted_receipt,
            label="trusted_receipt",
            schema_version=TRUSTED_RECEIPT_SCHEMA,
        )
        for artifact in self.diagnostics:
            _require_cas_artifact(artifact, label="diagnostic")
        if self.state_delta.receipt != self.trusted_receipt:
            raise ValueError("state delta receipt must equal the ToolResult trusted receipt")
        if not self.started_at <= self.state_delta.effective_at <= self.ended_at:
            raise ValueError("state delta effective_at must fall within the invocation")
        keys = [observation.key for observation in self.fresh_observations]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("fresh observations must be sorted and unique by key")
        for observation in self.fresh_observations:
            if observation.source_kind != "fresh_observation":
                raise ValueError("fresh observation must declare fresh_observation provenance")
            if not self.started_at <= observation.observed_at <= self.ended_at:
                raise ValueError("fresh observation was not captured during this invocation")
            if observation.valid_until is not None and observation.valid_until < self.ended_at:
                raise ValueError("fresh observation expired before invocation completion")
        return self
