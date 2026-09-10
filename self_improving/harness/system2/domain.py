"""Trusted world-state and ToolResult contracts for the System 2 harness.

The planner never mutates memory implicitly.  A Skill returns one typed
``System2ToolResult`` whose state delta is compare-and-swap applied to an
immutable ``TrustedWorldState``.  Every fact and every mutation is bound to a
content-addressed artifact; mutable locators are outside this domain.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, TypeVar

from pydantic import AwareDatetime, BaseModel, JsonValue, model_validator

from ..artifacts import ArtifactResolutionError, LocalArtifactStore
from ..schemas import ArtifactRef, Blocker, RunStatus
from ..schemas.base import HarnessModel, JsonObject, Sha256
from ..trusted_state import (
    WORLD_FACT_EVIDENCE_SCHEMA,  # noqa: F401 - compatibility re-export
    WORLD_FACT_SCHEMA,  # noqa: F401 - compatibility re-export
    WORLD_STATE_SCHEMA,  # noqa: F401 - compatibility re-export
    FactKey,  # noqa: F401 - compatibility re-export
    StateMutation,
    TrustedWorldState,
    WorldFact,
    WorldFactEvidence,  # noqa: F401 - compatibility re-export
    _build_world_state_version,  # noqa: F401 - compatibility re-export
    _canonical_json_bytes,
    _require_cas_artifact,
    _require_utc,
    advance_trusted_world_state,
    build_world_state,  # noqa: F401 - compatibility re-export
    fact_sha256,  # noqa: F401 - compatibility re-export
)

STATE_DELTA_SCHEMA = "harness.state_delta.v1"
TOOL_RESULT_SCHEMA = "harness.system2_tool_result.v1"
TRUSTED_RECEIPT_SCHEMA = "harness.trusted_tool_receipt.v1"
INVOCATION_SCHEMA = "harness.skill_invocation.v1"
RUN_STATE_SCHEMA = "harness.run_state.v1"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _strict_json_object(payload: bytes, *, label: str) -> dict[str, JsonValue]:
    def reject_duplicate(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains non-finite JSON constant: {value}")

    try:
        value = json.loads(
            payload,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _parse_canonical_model(
    model_type: type[_ModelT],
    payload: bytes,
    *,
    label: str,
) -> _ModelT:
    document = _strict_json_object(payload, label=label)
    if payload != _canonical_json_bytes(document):
        raise ValueError(f"{label} JSON is not canonical")
    return model_type.model_validate(document)


class StateDelta(HarnessModel):
    """A receipt-bound set of compare-and-swap mutations."""

    schema_version: Literal["harness.state_delta.v1"] = STATE_DELTA_SCHEMA
    base_state_sha256: Sha256
    effective_at: AwareDatetime
    receipt: ArtifactRef
    mutations: tuple[StateMutation, ...]
    delta_sha256: Sha256

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
        if self.delta_sha256 != state_delta_sha256(self):
            raise ValueError("delta_sha256 does not match canonical state delta")
        return self


def state_delta_sha256(delta: StateDelta) -> str:
    """Return the domain-separated identity of a complete state transition."""

    payload = {
        "domain": "harness.state_delta.identity.v1",
        "delta": delta.model_dump(mode="json", exclude={"delta_sha256"}),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def build_state_delta(
    *,
    base_state_sha256: str,
    effective_at: datetime,
    receipt: ArtifactRef,
    mutations: tuple[StateMutation, ...],
) -> StateDelta:
    """Build a canonical, self-bound state delta from validated components."""

    unbound = StateDelta.model_construct(
        schema_version=STATE_DELTA_SCHEMA,
        base_state_sha256=base_state_sha256,
        effective_at=effective_at,
        receipt=receipt,
        mutations=mutations,
        delta_sha256="0" * 64,
    )
    return StateDelta.model_validate(
        {
            **unbound.model_dump(mode="python"),
            "delta_sha256": state_delta_sha256(unbound),
        }
    )


def apply_state_delta(
    state: TrustedWorldState,
    delta: StateDelta,
    *,
    artifact_store: LocalArtifactStore,
) -> TrustedWorldState:
    """Apply a receipt-backed delta whose claims and prior facts still match."""

    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be an exact LocalArtifactStore")
    try:
        trusted_state = TrustedWorldState.model_validate(state.model_dump(mode="python"))
    except ValueError as exc:
        raise ValueError("current world state failed its canonical integrity check") from exc
    try:
        trusted_delta = StateDelta.model_validate(delta.model_dump(mode="python"))
    except ValueError as exc:
        raise ValueError("state delta failed its canonical integrity check") from exc
    _verify_delta_receipt(trusted_delta, artifact_store)
    if trusted_delta.base_state_sha256 != trusted_state.state_sha256:
        raise ValueError("state delta base does not match current world state")
    return advance_trusted_world_state(
        trusted_state,
        effective_at=trusted_delta.effective_at,
        mutations=trusted_delta.mutations,
    )


def _verify_delta_receipt(delta: StateDelta, artifact_store: LocalArtifactStore) -> None:
    # Local import avoids a domain/dispatcher import cycle while retaining the
    # exact receipt model as the single public contract.
    from .dispatcher import verify_trusted_tool_receipt

    try:
        verified = verify_trusted_tool_receipt(artifact_store, delta.receipt)
    except (ArtifactResolutionError, OSError, ValueError) as error:
        raise ValueError("state delta trusted receipt is unavailable or invalid") from error
    receipt = verified.receipt
    if (
        receipt.status is not RunStatus.SUCCEEDED
        or receipt.base_state_sha256 != delta.base_state_sha256
        or receipt.ended_at != delta.effective_at
    ):
        raise ValueError("state delta is not bound to its trusted receipt")
    claims = {claim.key: claim for claim in receipt.derived_fact_claims}
    if set(claims) != {mutation.key for mutation in delta.mutations}:
        raise ValueError("state delta mutations do not equal trusted receipt claims")
    for mutation in delta.mutations:
        claim = claims[mutation.key]
        if claim.operation != mutation.operation:
            raise ValueError("state delta operation does not equal trusted receipt claim")
        if mutation.operation == "upsert":
            fact = mutation.fact
            if (
                fact is None
                or fact.source_kind != "trusted_receipt"
                or fact.source_artifact != delta.receipt
                or fact.observed_at != delta.effective_at
                or _canonical_json_bytes(claim.value) != _canonical_json_bytes(fact.value)
            ):
                raise ValueError("state delta upsert is not justified by its receipt claim")
        elif claim.value is not None:
            raise ValueError("state delta retract requires a null receipt claim")


class System2ToolResult(HarnessModel):
    """One complete Skill result consumed by the System 2 planner."""

    schema_version: Literal["harness.system2_tool_result.v1"] = TOOL_RESULT_SCHEMA
    status: RunStatus
    started_at: AwareDatetime
    ended_at: AwareDatetime
    invocation: ArtifactRef | None
    run_state: ArtifactRef
    typed_output: JsonObject | None
    state_delta: StateDelta | None
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
        if self.invocation is not None:
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
        if self.status == RunStatus.SUCCEEDED and self.invocation is None:
            raise ValueError("succeeded ToolResult requires an invocation artifact")
        if self.status == RunStatus.SUCCEEDED:
            if self.state_delta is None:
                raise ValueError("succeeded ToolResult requires a state delta")
            if self.state_delta.receipt != self.trusted_receipt:
                raise ValueError("state delta receipt must equal the ToolResult trusted receipt")
            if not self.started_at <= self.state_delta.effective_at <= self.ended_at:
                raise ValueError("state delta effective_at must fall within the invocation")
        elif self.state_delta is not None:
            raise ValueError(
                "blocked/failed ToolResult must not expose an inapplicable state delta"
            )
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
        mutated_keys = (
            {mutation.key for mutation in self.state_delta.mutations}
            if self.state_delta is not None
            else set()
        )
        if mutated_keys.intersection(keys):
            raise ValueError("fresh observations are separate from receipt-derived state delta")
        return self
