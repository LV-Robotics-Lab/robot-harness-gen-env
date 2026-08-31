"""Bounded context compilation and strict decision parsing for System 2.

The language model receives one deterministic projection of trusted state and
qualified Skill cards.  Its output is an untrusted proposal: this module only
accepts one strict decision bound to that exact projection, and it never turns
free-form model text into an executable function name.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, ValidationError, model_validator

from ..schemas import ArtifactRef, RunStatus
from ..schemas.base import (
    CanonicalSkillRef,
    HarnessModel,
    JsonObject,
    SchemaId,
    Sha256,
)
from .domain import FactKey, TrustedWorldState, WorldFact, fact_sha256

PLANNER_SKILL_SCHEMA = "harness.planner_skill_card.v1"
PLANNER_HISTORY_SCHEMA = "harness.planner_history_entry.v1"
PLANNER_CONTEXT_SCHEMA = "harness.planner_context.v1"
PLANNER_DECISION_SCHEMA = "harness.planner_decision.v1"

_CAS_PREFIX = "artifact://sha256/"
_SOURCE_PRIORITY = {
    "fresh_observation": 0,
    "trusted_receipt": 1,
    "user_input": 2,
    "derived": 3,
}
_Purpose = Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
_Summary = Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
_StopReason = Annotated[str, Field(strict=True, min_length=1, max_length=2048)]


class ContextCompilationError(ValueError):
    """Trusted state cannot be projected without violating a fixed boundary."""


class _ContextTooLarge(ValueError):
    """An otherwise canonical projection exceeds its declared byte budget."""


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


def _require_cas(ref: ArtifactRef, *, label: str, schema: str | None = None) -> None:
    if ref.uri != f"{_CAS_PREFIX}{ref.sha256}":
        raise ValueError(f"{label} must use its content-addressed artifact URI")
    if schema is not None and ref.schema_version != schema:
        raise ValueError(f"{label} must have schema_version={schema}")


def _set_sha256(domain: str, values: tuple[str, ...]) -> str:
    return hashlib.sha256(
        _canonical_json_bytes({"domain": domain, "values": sorted(values)})
    ).hexdigest()


class ContextBudget(HarnessModel):
    """Hard, identity-bearing limits applied before any provider call."""

    max_facts: Annotated[int, Field(strict=True, ge=1, le=4096)]
    max_history: Annotated[int, Field(strict=True, ge=0, le=4096)]
    max_context_bytes: Annotated[int, Field(strict=True, ge=256, le=16_777_216)]


class PlannerSkillCard(HarnessModel):
    """The only exact Skill identity an LLM may propose invoking."""

    schema_version: Literal["harness.planner_skill_card.v1"] = PLANNER_SKILL_SCHEMA
    skill_ref: CanonicalSkillRef
    purpose: _Purpose
    input_schema: SchemaId
    output_schema: SchemaId
    qualification_sha256: Sha256
    max_attempts: Annotated[int, Field(strict=True, ge=1, le=100)]

    @model_validator(mode="after")
    def purpose_is_canonical(self) -> "PlannerSkillCard":
        if self.purpose.strip() != self.purpose:
            raise ValueError("purpose must not have leading or trailing whitespace")
        return self


class PlannerHistoryEntry(HarnessModel):
    """One compact, receipt-bound prior execution outcome."""

    schema_version: Literal["harness.planner_history_entry.v1"] = PLANNER_HISTORY_SCHEMA
    run_id: UUID
    skill_ref: CanonicalSkillRef
    status: RunStatus
    ended_at: AwareDatetime
    receipt: ArtifactRef
    blocker_code: str | None
    blocker_stage: str | None

    @model_validator(mode="after")
    def history_is_terminal_and_receipt_bound(self) -> "PlannerHistoryEntry":
        _require_utc(self.ended_at, label="ended_at")
        _require_cas(
            self.receipt,
            label="history receipt",
            schema="harness.trusted_tool_receipt.v1",
        )
        if self.status is RunStatus.RUNNING:
            raise ValueError("planner history must be terminal")
        has_blocker = self.blocker_code is not None or self.blocker_stage is not None
        if self.status is RunStatus.SUCCEEDED:
            if has_blocker:
                raise ValueError("successful history cannot carry a blocker")
        elif self.blocker_code is None or self.blocker_stage is None:
            raise ValueError("blocked or failed history must carry a blocker")
        for label, value in (
            ("blocker_code", self.blocker_code),
            ("blocker_stage", self.blocker_stage),
        ):
            if value is not None and (not value or value.strip() != value):
                raise ValueError(f"{label} must be a non-empty canonical string")
        return self


def _history_sort_key(entry: PlannerHistoryEntry) -> tuple[float, str, str]:
    return (-entry.ended_at.timestamp(), entry.skill_ref, entry.run_id.hex)


def _planner_context_payload(context: "PlannerContext") -> dict[str, JsonValue]:
    return context.model_dump(mode="json", exclude={"context_sha256"})


def planner_context_sha256(context: "PlannerContext") -> str:
    """Return the domain-separated identity of one planner projection."""

    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "domain": "harness.planner_context.identity.v1",
                "context": _planner_context_payload(context),
            }
        )
    ).hexdigest()


class PlannerContext(HarnessModel):
    """One deterministic, size-bounded projection given to a planner model."""

    schema_version: Literal["harness.planner_context.v1"] = PLANNER_CONTEXT_SCHEMA
    world_state_sha256: Sha256
    world_state_version: Annotated[int, Field(strict=True, ge=1)]
    as_of: AwareDatetime
    objective: WorldFact
    required_fact_keys: tuple[FactKey, ...]
    facts: tuple[WorldFact, ...]
    skills: tuple[PlannerSkillCard, ...]
    history: tuple[PlannerHistoryEntry, ...]
    budget: ContextBudget
    omitted_fact_count: Annotated[int, Field(strict=True, ge=0)]
    omitted_fact_set_sha256: Sha256
    omitted_history_count: Annotated[int, Field(strict=True, ge=0)]
    omitted_history_set_sha256: Sha256
    context_sha256: Sha256

    @model_validator(mode="after")
    def context_is_canonical_and_self_bound(self) -> "PlannerContext":
        _require_utc(self.as_of, label="as_of")
        if self.objective.key != "task.objective":
            raise ValueError("objective must be the task.objective fact")
        required = list(self.required_fact_keys)
        if required != sorted(required) or len(required) != len(set(required)):
            raise ValueError("required fact keys must be sorted and unique")
        fact_keys = [fact.key for fact in self.facts]
        if fact_keys != sorted(fact_keys) or len(fact_keys) != len(set(fact_keys)):
            raise ValueError("planner facts must be sorted and unique")
        if self.objective.key in fact_keys:
            raise ValueError("objective must not be duplicated in planner facts")
        if not set(required).issubset(fact_keys):
            raise ValueError("every required fact must be present")
        skill_refs = [skill.skill_ref for skill in self.skills]
        if skill_refs != sorted(skill_refs) or len(skill_refs) != len(set(skill_refs)):
            raise ValueError("planner Skill cards must be sorted and unique")
        if not self.skills:
            raise ValueError("planner context requires at least one qualified Skill")
        if list(self.history) != sorted(self.history, key=_history_sort_key):
            raise ValueError("planner history must use canonical newest-first order")
        run_ids = [entry.run_id for entry in self.history]
        receipts = [entry.receipt.sha256 for entry in self.history]
        if len(run_ids) != len(set(run_ids)) or len(receipts) != len(set(receipts)):
            raise ValueError("planner history run and receipt identities must be unique")
        if len(self.facts) + 1 > self.budget.max_facts:
            raise ValueError("planner facts exceed their count budget")
        if len(self.history) > self.budget.max_history:
            raise ValueError("planner history exceeds its count budget")
        if self.context_sha256 != planner_context_sha256(self):
            raise ValueError("context_sha256 does not match canonical planner context")
        if len(_canonical_json_bytes(self.model_dump(mode="json"))) > self.budget.max_context_bytes:
            raise ValueError("planner context exceeds its byte budget")
        return self


def verify_planner_context(context: PlannerContext) -> PlannerContext:
    """Recheck a possibly retained context and return a validated snapshot."""

    try:
        return PlannerContext.model_validate(context.model_dump(mode="python"))
    except (ValidationError, ValueError) as exc:
        raise ContextCompilationError("planner context failed its integrity check") from exc


def _fact_rank(fact: WorldFact) -> tuple[int, float, str]:
    return (
        _SOURCE_PRIORITY[fact.source_kind],
        -fact.observed_at.timestamp(),
        fact.key,
    )


def _make_context(
    *,
    state: TrustedWorldState,
    objective: WorldFact,
    required_fact_keys: tuple[str, ...],
    facts: tuple[WorldFact, ...],
    skills: tuple[PlannerSkillCard, ...],
    history: tuple[PlannerHistoryEntry, ...],
    budget: ContextBudget,
    all_optional_facts: tuple[WorldFact, ...],
    all_history: tuple[PlannerHistoryEntry, ...],
) -> PlannerContext:
    selected_fact_sha = {fact_sha256(fact) for fact in facts}
    omitted_facts = tuple(
        fact_sha256(fact)
        for fact in all_optional_facts
        if fact_sha256(fact) not in selected_fact_sha
    )
    selected_history = {entry.receipt.sha256 for entry in history}
    omitted_history = tuple(
        entry.receipt.sha256
        for entry in all_history
        if entry.receipt.sha256 not in selected_history
    )
    unbound = PlannerContext.model_construct(
        schema_version=PLANNER_CONTEXT_SCHEMA,
        world_state_sha256=state.state_sha256,
        world_state_version=state.version,
        as_of=state.as_of,
        objective=objective,
        required_fact_keys=required_fact_keys,
        facts=tuple(sorted(facts, key=lambda fact: fact.key)),
        skills=skills,
        history=history,
        budget=budget,
        omitted_fact_count=len(omitted_facts),
        omitted_fact_set_sha256=_set_sha256(
            "harness.planner_context.omitted_facts.v1",
            omitted_facts,
        ),
        omitted_history_count=len(omitted_history),
        omitted_history_set_sha256=_set_sha256(
            "harness.planner_context.omitted_history.v1",
            omitted_history,
        ),
        context_sha256="0" * 64,
    )
    candidate = unbound.model_copy(update={"context_sha256": planner_context_sha256(unbound)})
    if len(_canonical_json_bytes(candidate.model_dump(mode="json"))) > budget.max_context_bytes:
        raise _ContextTooLarge("planner context exceeds its byte budget")
    return PlannerContext.model_validate(candidate.model_dump(mode="python"))


def compile_planner_context(
    *,
    state: TrustedWorldState,
    skills: tuple[PlannerSkillCard, ...],
    history: tuple[PlannerHistoryEntry, ...],
    required_fact_keys: tuple[str, ...],
    budget: ContextBudget,
) -> PlannerContext:
    """Project a trusted state into one canonical, bounded LLM context."""

    try:
        trusted_state = TrustedWorldState.model_validate(state.model_dump(mode="python"))
    except (ValidationError, ValueError) as exc:
        raise ContextCompilationError("world state failed its canonical integrity check") from exc
    by_key = {fact.key: fact for fact in trusted_state.facts}
    objective = by_key.get("task.objective")
    if objective is None:
        raise ContextCompilationError("trusted world state has no task.objective")
    if len(required_fact_keys) != len(set(required_fact_keys)):
        raise ContextCompilationError("required fact keys must be unique")
    canonical_required = tuple(sorted(required_fact_keys))
    if "task.objective" in canonical_required:
        raise ContextCompilationError("task.objective is implicit and cannot be repeated")
    missing = tuple(key for key in canonical_required if key not in by_key)
    if missing:
        raise ContextCompilationError(f"required trusted facts are missing: {missing}")
    skill_refs = [skill.skill_ref for skill in skills]
    if not skills or len(skill_refs) != len(set(skill_refs)):
        raise ContextCompilationError("qualified Skill cards must be non-empty and unique")
    ordered_skills = tuple(sorted(skills, key=lambda skill: skill.skill_ref))
    ordered_history = tuple(sorted(history, key=_history_sort_key))
    run_ids = [entry.run_id for entry in ordered_history]
    receipts = [entry.receipt.sha256 for entry in ordered_history]
    if len(run_ids) != len(set(run_ids)) or len(receipts) != len(set(receipts)):
        raise ContextCompilationError("planner history run and receipt identities must be unique")
    required_facts = tuple(by_key[key] for key in canonical_required)
    optional_facts = tuple(
        sorted(
            (
                fact
                for fact in trusted_state.facts
                if fact.key not in {*canonical_required, "task.objective"}
            ),
            key=_fact_rank,
        )
    )
    optional_capacity = budget.max_facts - 1 - len(required_facts)
    if optional_capacity < 0:
        raise ContextCompilationError("required facts exceed the fact budget")
    selected_optional = list(optional_facts[:optional_capacity])
    selected_history = list(ordered_history[: budget.max_history])

    while True:
        selected_facts = (*required_facts, *selected_optional)
        try:
            return _make_context(
                state=trusted_state,
                objective=objective,
                required_fact_keys=canonical_required,
                facts=selected_facts,
                skills=ordered_skills,
                history=tuple(selected_history),
                budget=budget,
                all_optional_facts=(*required_facts, *optional_facts),
                all_history=ordered_history,
            )
        except _ContextTooLarge:
            pass
        if selected_history:
            selected_history.pop()
        elif selected_optional:
            selected_optional.pop()
        else:
            raise ContextCompilationError(
                "mandatory planner context exceeds the byte budget"
            ) from None


def build_planner_prompt(context: PlannerContext) -> bytes:
    """Build the exact JSON prompt document consumed by a planner provider."""

    trusted_context = verify_planner_context(context)
    contract = {
        "schema_version": "harness.planner_prompt.v1",
        "role": (
            "Select one safe next action from trusted world state. "
            "You propose; the Harness validates and executes."
        ),
        "rules": [
            "Return exactly one JSON object and no markdown.",
            "Bind base_state_sha256 and context_sha256 exactly as supplied.",
            "Invoke only a skill_ref listed in context.skills.",
            "Never claim execution, validation, physical success, or publication.",
            "Request a fresh observation when trusted state is insufficient.",
            "Use summary only for a short auditable decision explanation.",
        ],
        "context": trusted_context.model_dump(mode="json"),
        "output_schema": {
            "schema_version": PLANNER_DECISION_SCHEMA,
            "base_state_sha256": trusted_context.world_state_sha256,
            "context_sha256": trusted_context.context_sha256,
            "action": ["invoke_skill", "request_observation", "stop"],
            "skill_ref": "listed exact skill_ref or null",
            "parameters": "JSON object for invoke_skill, otherwise null",
            "observation_keys": "sorted fact keys for request_observation, otherwise []",
            "stop_reason": "non-empty string for stop, otherwise null",
            "summary": "short auditable explanation",
        },
    }
    return _canonical_json_bytes(contract)


class PlannerDecision(HarnessModel):
    """One untrusted model proposal after structural validation."""

    schema_version: Literal["harness.planner_decision.v1"] = PLANNER_DECISION_SCHEMA
    base_state_sha256: Sha256
    context_sha256: Sha256
    action: Literal["invoke_skill", "request_observation", "stop"]
    skill_ref: CanonicalSkillRef | None
    parameters: JsonObject | None
    observation_keys: tuple[FactKey, ...]
    stop_reason: _StopReason | None
    summary: _Summary

    @model_validator(mode="after")
    def action_has_one_exact_shape(self) -> "PlannerDecision":
        if self.summary.strip() != self.summary:
            raise ValueError("summary must not have leading or trailing whitespace")
        keys = list(self.observation_keys)
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("observation_keys must be sorted and unique")
        if self.action == "invoke_skill":
            if (
                self.skill_ref is None
                or self.parameters is None
                or self.observation_keys
                or self.stop_reason is not None
            ):
                raise ValueError("invoke_skill has an invalid decision shape")
        elif self.action == "request_observation":
            if (
                self.skill_ref is not None
                or self.parameters is not None
                or not self.observation_keys
                or self.stop_reason is not None
            ):
                raise ValueError("request_observation has an invalid decision shape")
        elif (
            self.skill_ref is not None
            or self.parameters is not None
            or self.observation_keys
            or self.stop_reason is None
        ):
            raise ValueError("stop has an invalid decision shape")
        if self.stop_reason is not None and self.stop_reason.strip() != self.stop_reason:
            raise ValueError("stop_reason must not have leading or trailing whitespace")
        return self


def _strict_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def parse_planner_decision(
    raw: bytes,
    *,
    context: PlannerContext,
    max_response_bytes: int = 65_536,
) -> PlannerDecision:
    """Parse and bind one model response without repair or free-text recovery."""

    if type(raw) is not bytes:
        raise TypeError("planner response must be bytes")
    if not 1 <= len(raw) <= max_response_bytes:
        raise ValueError("planner response violates its byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("planner response is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("planner response is not one strict JSON value") from exc
    if not isinstance(value, dict):
        raise ValueError("planner response must be a JSON object")
    trusted_context = verify_planner_context(context)
    decision = PlannerDecision.model_validate(value)
    if decision.base_state_sha256 != trusted_context.world_state_sha256:
        raise ValueError("planner decision is bound to a different world state")
    if decision.context_sha256 != trusted_context.context_sha256:
        raise ValueError("planner decision is bound to a different context")
    if decision.action == "invoke_skill":
        advertised = {skill.skill_ref for skill in trusted_context.skills}
        if decision.skill_ref not in advertised:
            raise ValueError("planner selected a Skill that was not advertised")
    return decision


def decision_sha256(decision: PlannerDecision) -> str:
    """Return the domain-separated identity of one validated proposal."""

    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "domain": "harness.planner_decision.identity.v1",
                "decision": decision.model_dump(mode="json"),
            }
        )
    ).hexdigest()
