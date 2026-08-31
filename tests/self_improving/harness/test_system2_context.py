from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.schemas import ArtifactRef, RunStatus
from self_improving.harness.system2.context import (
    ContextBudget,
    ContextCompilationError,
    PlannerContext,
    PlannerDecision,
    PlannerHistoryEntry,
    PlannerSkillCard,
    build_planner_prompt,
    compile_planner_context,
    decision_sha256,
    parse_planner_decision,
    planner_context_sha256,
    verify_planner_context,
)
from self_improving.harness.system2.domain import WorldFact, build_world_state

_T0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)


def _artifact(name: str, digest: str, schema: str) -> ArtifactRef:
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=10,
        schema_version=schema,
    )


def _fact(
    key: str,
    value: object,
    *,
    source_kind: str = "fresh_observation",
    observed_at: datetime = _T0,
    digest: str = "1" * 64,
) -> WorldFact:
    return WorldFact(
        key=key,
        value=value,
        source_kind=source_kind,
        source_artifact=_artifact("fact", digest, f"harness.{source_kind}.v1"),
        observed_at=observed_at,
        valid_until=None,
    )


def _skill(name: str, digest: str = "2" * 64) -> PlannerSkillCard:
    route = name.split("@", maxsplit=1)[0].split(".", maxsplit=1)[1]
    return PlannerSkillCard(
        skill_ref=name,
        purpose=f"Use {name} for its exact typed operation.",
        input_schema=f"harness.text2env_{route}_input.v1",
        output_schema=f"harness.text2env_{route}_output.v1",
        qualification_sha256=digest,
        max_attempts=1,
    )


def _compile_parameters() -> dict[str, object]:
    return {
        "request": "put a can on a plate",
        "seed": 7,
        "asset_catalog": _artifact(
            "asset_catalog",
            "a" * 64,
            "robotwin.asset_catalog.v1",
        ).model_dump(mode="json"),
        "config": {"generate_missing_assets": False},
    }


def _history(
    run: int,
    *,
    status: RunStatus = RunStatus.SUCCEEDED,
    ended_at: datetime = _T0,
) -> PlannerHistoryEntry:
    return PlannerHistoryEntry(
        run_id=UUID(int=run),
        skill_ref="text2env.compile@1.0.0",
        status=status,
        ended_at=ended_at,
        receipt=_artifact("run_receipt", f"{run:064x}", "harness.trusted_tool_receipt.v1"),
        blocker_code=(None if status is RunStatus.SUCCEEDED else "T2E_COMPILE_FAILED"),
        blocker_stage=(None if status is RunStatus.SUCCEEDED else "compile"),
    )


def _context():
    objective = _fact(
        "task.objective",
        "put a can on a plate",
        source_kind="user_input",
        digest="1" * 64,
    )
    pose = _fact(
        "scene.pose",
        {"can": "unknown"},
        observed_at=_T0 + timedelta(seconds=1),
        digest="3" * 64,
    )
    state = build_world_state((pose, objective), as_of=_T0 + timedelta(seconds=1))
    context = compile_planner_context(
        state=state,
        skills=(_skill("text2env.replay@1.0.0", "4" * 64), _skill("text2env.compile@1.0.0")),
        history=(_history(1),),
        required_fact_keys=("scene.pose",),
        budget=ContextBudget(max_facts=4, max_history=4, max_context_bytes=32_000),
    )
    return state, context


def test_compile_context_is_canonical_bounded_and_state_bound() -> None:
    objective = _fact(
        "task.objective",
        "put a can on a plate",
        source_kind="user_input",
        digest="1" * 64,
    )
    required = _fact("asset.catalog", {"assets": ["071_can"]}, digest="2" * 64)
    newest = _fact(
        "scene.visible",
        True,
        observed_at=_T0 + timedelta(seconds=2),
        digest="3" * 64,
    )
    derived = _fact(
        "scene.summary",
        "cached",
        source_kind="derived",
        observed_at=_T0 + timedelta(seconds=1),
        digest="4" * 64,
    )
    state = build_world_state(
        (derived, newest, objective, required),
        as_of=_T0 + timedelta(seconds=2),
    )

    context = compile_planner_context(
        state=state,
        skills=(_skill("text2env.replay@1.0.0"), _skill("text2env.compile@1.0.0")),
        history=(_history(1), _history(2, ended_at=_T0 + timedelta(seconds=1))),
        required_fact_keys=("asset.catalog",),
        budget=ContextBudget(max_facts=3, max_history=1, max_context_bytes=32_000),
    )

    assert context.world_state_sha256 == state.state_sha256
    assert context.objective.key == "task.objective"
    assert tuple(item.key for item in context.facts) == ("asset.catalog", "scene.visible")
    assert tuple(item.skill_ref for item in context.skills) == (
        "text2env.compile@1.0.0",
        "text2env.replay@1.0.0",
    )
    assert tuple(item.run_id for item in context.history) == (UUID(int=2),)
    assert context.omitted_fact_count == 1
    assert context.omitted_fact_set_sha256 != "0" * 64
    assert context.omitted_history_count == 1
    assert context.omitted_history_set_sha256 != "0" * 64

    repeated = compile_planner_context(
        state=state,
        skills=tuple(reversed(context.skills)),
        history=tuple(reversed((_history(1), _history(2, ended_at=_T0 + timedelta(seconds=1))))),
        required_fact_keys=("asset.catalog",),
        budget=context.budget,
    )
    assert repeated.context_sha256 == context.context_sha256


@pytest.mark.parametrize("attack", ["missing_objective", "missing_required", "duplicate_skill"])
def test_compile_context_rejects_missing_authority_or_ambiguous_skills(attack: str) -> None:
    facts = () if attack == "missing_objective" else (_fact("task.objective", "task"),)
    state = build_world_state(facts, as_of=_T0)
    skills = (_skill("text2env.compile@1.0.0"),)
    required: tuple[str, ...] = ()
    if attack == "missing_required":
        required = ("asset.catalog",)
    elif attack == "duplicate_skill":
        skills = (skills[0], skills[0])

    with pytest.raises(ContextCompilationError):
        compile_planner_context(
            state=state,
            skills=skills,
            history=(),
            required_fact_keys=required,
            budget=ContextBudget(max_facts=2, max_history=0, max_context_bytes=8_000),
        )


@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
def test_compile_context_rejects_unregistered_skill_schemas(field: str) -> None:
    state = build_world_state((_fact("task.objective", "task"),), as_of=_T0)
    payload = _skill("text2env.compile@1.0.0").model_dump(mode="python")
    payload[field] = f"harness.unregistered_{field}.v1"

    with pytest.raises(ContextCompilationError, match="public schema catalog"):
        compile_planner_context(
            state=state,
            skills=(PlannerSkillCard.model_validate(payload),),
            history=(),
            required_fact_keys=(),
            budget=ContextBudget(max_facts=1, max_history=0, max_context_bytes=8_000),
        )


def test_compile_context_fails_when_mandatory_material_exceeds_byte_budget() -> None:
    objective = _fact("task.objective", "x" * 2_000)
    state = build_world_state((objective,), as_of=_T0)

    with pytest.raises(ContextCompilationError, match="byte budget"):
        compile_planner_context(
            state=state,
            skills=(_skill("text2env.compile@1.0.0"),),
            history=(),
            required_fact_keys=(),
            budget=ContextBudget(max_facts=1, max_history=0, max_context_bytes=512),
        )


def test_compile_context_drops_history_then_optional_facts_to_fit_bytes() -> None:
    objective = _fact("task.objective", "task", source_kind="user_input")
    large_optional = _fact("scene.large", "x" * 4_000, digest="3" * 64)
    state = build_world_state((objective, large_optional), as_of=_T0)
    skills = (_skill("text2env.compile@1.0.0"),)
    history = tuple(_history(index) for index in range(1, 5))
    generous = compile_planner_context(
        state=state,
        skills=skills,
        history=(),
        required_fact_keys=(),
        budget=ContextBudget(max_facts=2, max_history=4, max_context_bytes=100_000),
    )
    full_size = (
        len(
            json.dumps(
                generous.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        + 1
    )

    without_optional = compile_planner_context(
        state=state,
        skills=skills,
        history=(),
        required_fact_keys=(),
        budget=ContextBudget(
            max_facts=2,
            max_history=4,
            max_context_bytes=full_size - 2_000,
        ),
    )

    assert without_optional.facts == ()
    assert without_optional.omitted_fact_count == 1

    objective_only = build_world_state((objective,), as_of=_T0)
    history_full = compile_planner_context(
        state=objective_only,
        skills=skills,
        history=history,
        required_fact_keys=(),
        budget=ContextBudget(max_facts=1, max_history=4, max_context_bytes=100_000),
    )
    history_size = (
        len(
            json.dumps(
                history_full.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        + 1
    )
    trimmed_history = compile_planner_context(
        state=objective_only,
        skills=skills,
        history=history,
        required_fact_keys=(),
        budget=ContextBudget(
            max_facts=1,
            max_history=4,
            max_context_bytes=history_size - 100,
        ),
    )

    assert len(trimmed_history.history) < len(history_full.history)
    assert trimmed_history.omitted_history_count > 0


@pytest.mark.parametrize(
    "attack",
    [
        "state_mutation",
        "required_duplicate",
        "objective_required",
        "fact_budget",
        "history_duplicate",
    ],
)
def test_compile_context_rechecks_integrity_and_identity_sets(attack: str) -> None:
    objective = _fact(
        "task.objective",
        {"request": "task"},
        source_kind="user_input",
    )
    required = _fact("asset.catalog", {"asset": "can"}, digest="2" * 64)
    state = build_world_state((objective, required), as_of=_T0)
    required_keys: tuple[str, ...] = ("asset.catalog",)
    history = (_history(1),)
    budget = ContextBudget(max_facts=2, max_history=2, max_context_bytes=16_000)
    if attack == "state_mutation":
        assert isinstance(state.facts[-1].value, dict)
        state.facts[-1].value["request"] = "tampered"
    elif attack == "required_duplicate":
        required_keys = ("asset.catalog", "asset.catalog")
    elif attack == "objective_required":
        required_keys = ("task.objective",)
    elif attack == "fact_budget":
        budget = ContextBudget(max_facts=1, max_history=2, max_context_bytes=16_000)
    else:
        history = (history[0], history[0])

    with pytest.raises(ContextCompilationError):
        compile_planner_context(
            state=state,
            skills=(_skill("text2env.compile@1.0.0"),),
            history=history,
            required_fact_keys=required_keys,
            budget=budget,
        )


@pytest.mark.parametrize("attack", ["purpose", "receipt_uri", "receipt_schema", "blocker_text"])
def test_skill_and_history_cards_reject_noncanonical_provenance(attack: str) -> None:
    if attack == "purpose":
        payload = _skill("text2env.compile@1.0.0").model_dump(mode="python")
        payload["purpose"] = " padded "
        with pytest.raises(ValidationError, match="purpose"):
            PlannerSkillCard.model_validate(payload)
        return
    payload = _history(1, status=RunStatus.FAILED).model_dump(mode="python")
    if attack == "receipt_uri":
        payload["receipt"]["uri"] = "file:///tmp/receipt.json"
    elif attack == "receipt_schema":
        payload["receipt"]["schema_version"] = "wrong.receipt.v1"
    else:
        payload["blocker_code"] = " "

    with pytest.raises(ValidationError):
        PlannerHistoryEntry.model_validate(payload)


@pytest.mark.parametrize(
    "attack", ["running", "success_blocker", "failed_without_blocker", "local_time"]
)
def test_history_entry_is_terminal_and_consistent(attack: str) -> None:
    payload = _history(1).model_dump(mode="python")
    if attack == "running":
        payload["status"] = RunStatus.RUNNING
    elif attack == "success_blocker":
        payload["blocker_code"] = "T2E_FAILED"
        payload["blocker_stage"] = "compile"
    elif attack == "failed_without_blocker":
        payload["status"] = RunStatus.FAILED
    else:
        payload["ended_at"] = datetime(
            2026,
            8,
            31,
            17,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        )

    with pytest.raises(ValidationError):
        PlannerHistoryEntry.model_validate(payload)


@pytest.mark.parametrize(
    "attack",
    [
        "objective",
        "required",
        "facts",
        "objective_duplicate",
        "required_missing",
        "skills",
        "skills_empty",
        "history_order",
        "history_duplicate",
        "fact_count",
        "history_count",
        "digest",
        "timezone",
        "bytes",
    ],
)
def test_planner_context_rejects_forged_or_noncanonical_documents(attack: str) -> None:
    state, context = _context()
    payload = context.model_dump(mode="python")
    if attack == "objective":
        payload["objective"] = _fact("task.other", "forged", digest="7" * 64)
    elif attack == "required":
        payload["required_fact_keys"] = ("scene.pose", "scene.pose")
    elif attack == "facts":
        payload["facts"] = (context.facts[0], context.facts[0])
    elif attack == "objective_duplicate":
        payload["facts"] = tuple(
            sorted((*context.facts, context.objective), key=lambda fact: fact.key)
        )
    elif attack == "required_missing":
        payload["required_fact_keys"] = ("asset.catalog",)
    elif attack == "skills":
        payload["skills"] = tuple(reversed(context.skills))
    elif attack == "skills_empty":
        payload["skills"] = ()
    elif attack == "history_order":
        newer = _history(2, ended_at=_T0 + timedelta(seconds=1))
        payload["history"] = (context.history[0], newer)
    elif attack == "history_duplicate":
        payload["history"] = (context.history[0], context.history[0])
    elif attack == "fact_count":
        payload["budget"] = ContextBudget(
            max_facts=1,
            max_history=context.budget.max_history,
            max_context_bytes=context.budget.max_context_bytes,
        )
    elif attack == "history_count":
        payload["budget"] = ContextBudget(
            max_facts=context.budget.max_facts,
            max_history=0,
            max_context_bytes=context.budget.max_context_bytes,
        )
    elif attack == "digest":
        payload["context_sha256"] = "8" * 64
    elif attack == "timezone":
        payload["as_of"] = datetime(
            2026,
            8,
            31,
            17,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        )
    else:
        small_budget = ContextBudget(
            max_facts=context.budget.max_facts,
            max_history=context.budget.max_history,
            max_context_bytes=256,
        )
        unbound = context.model_copy(update={"budget": small_budget})
        payload = unbound.model_dump(mode="python")
        payload["context_sha256"] = planner_context_sha256(unbound)

    with pytest.raises(ValidationError):
        PlannerContext.model_validate(payload)


def test_planner_prompt_is_stable_and_contains_only_one_decision_contract() -> None:
    _, context = _context()

    prompt = build_planner_prompt(context)
    repeated = build_planner_prompt(context)
    parsed = json.loads(prompt)

    assert prompt == repeated
    assert parsed["context"]["context_sha256"] == context.context_sha256
    assert parsed["rules"][0] == "Return exactly one JSON object and no markdown."
    assert "chain of thought" not in prompt.decode("utf-8").lower()
    assert set(parsed["output_schema"]["action"]) == {"invoke_skill", "request_observation", "stop"}
    assert parsed["output_schema"]["required_keys"] == [
        "schema_version",
        "base_state_sha256",
        "context_sha256",
        "action",
        "skill_ref",
        "parameters",
        "observation_keys",
        "stop_reason",
        "summary",
    ]
    invoke = parsed["output_schema"]["action_contracts"]["invoke_skill"]
    assert invoke["skill_ref"] == {"enum": ["text2env.compile@1.0.0", "text2env.replay@1.0.0"]}
    assert invoke["parameters"] == {
        "must_validate_against": "skill_parameter_contracts[skill_ref].json_schema",
        "type": "object",
    }
    assert invoke["observation_keys"] == {"const": []}
    assert invoke["stop_reason"] == {"const": None}
    assert "COPY_" not in prompt.decode("utf-8")
    assert "SORTED_REQUIRED_FACT_KEY" not in prompt.decode("utf-8")
    assert any("complete parameters object" in rule for rule in parsed["rules"])
    contract = parsed["skill_parameter_contracts"]["text2env.compile@1.0.0"]
    assert contract["input_schema"] == "harness.text2env_compile_input.v1"
    assert set(contract["json_schema"]["required"]) == {
        "request",
        "seed",
        "asset_catalog",
        "config",
    }


@pytest.mark.parametrize("consumer", ["verify", "prompt", "decision"])
def test_consumers_recheck_context_after_nested_value_mutation(consumer: str) -> None:
    state, context = _context()
    assert isinstance(context.facts[0].value, dict)
    context.facts[0].value["can"] = "tampered"
    payload = {
        "schema_version": "harness.planner_decision.v1",
        "base_state_sha256": state.state_sha256,
        "context_sha256": context.context_sha256,
        "action": "stop",
        "skill_ref": None,
        "parameters": None,
        "observation_keys": [],
        "stop_reason": "done",
        "summary": "Stop.",
    }

    with pytest.raises(ContextCompilationError, match="integrity"):
        if consumer == "verify":
            verify_planner_context(context)
        elif consumer == "prompt":
            build_planner_prompt(context)
        else:
            parse_planner_decision(json.dumps(payload).encode(), context=context)


def test_parse_invoke_decision_binds_context_and_advertised_skill() -> None:
    state, context = _context()
    payload = {
        "schema_version": "harness.planner_decision.v1",
        "base_state_sha256": state.state_sha256,
        "context_sha256": context.context_sha256,
        "action": "invoke_skill",
        "skill_ref": "text2env.compile@1.0.0",
        "parameters": _compile_parameters(),
        "observation_keys": [],
        "stop_reason": None,
        "summary": "Compile before replay.",
    }

    decision = parse_planner_decision(json.dumps(payload).encode(), context=context)

    assert isinstance(decision, PlannerDecision)
    assert decision.skill_ref == "text2env.compile@1.0.0"
    assert decision.parameters == _compile_parameters()
    assert decision_sha256(decision) == decision_sha256(decision)


@pytest.mark.parametrize("action", ["request_observation", "stop"])
def test_parse_noninvoke_decisions_have_exact_shapes(action: str) -> None:
    state, context = _context()
    payload = {
        "schema_version": "harness.planner_decision.v1",
        "base_state_sha256": state.state_sha256,
        "context_sha256": context.context_sha256,
        "action": action,
        "skill_ref": None,
        "parameters": None,
        "observation_keys": ["scene.contact"] if action == "request_observation" else [],
        "stop_reason": None
        if action == "request_observation"
        else "Objective is already satisfied.",
        "summary": "Need a fresh contact observation."
        if action == "request_observation"
        else "Stop.",
    }

    decision = parse_planner_decision(json.dumps(payload).encode(), context=context)

    assert decision.action == action


@pytest.mark.parametrize(
    "action,field,value",
    [
        ("invoke_skill", "skill_ref", None),
        ("invoke_skill", "parameters", None),
        ("invoke_skill", "observation_keys", ["scene.contact"]),
        ("invoke_skill", "stop_reason", "stop"),
        ("request_observation", "skill_ref", "text2env.compile@1.0.0"),
        ("request_observation", "parameters", {}),
        ("request_observation", "observation_keys", []),
        ("request_observation", "stop_reason", "stop"),
        ("stop", "skill_ref", "text2env.compile@1.0.0"),
        ("stop", "parameters", {}),
        ("stop", "observation_keys", ["scene.contact"]),
        ("stop", "stop_reason", None),
        ("stop", "stop_reason", " padded "),
        ("invoke_skill", "summary", " padded "),
    ],
)
def test_decision_action_shapes_reject_cross_action_fields(
    action: str,
    field: str,
    value: object,
) -> None:
    state, context = _context()
    payload = {
        "schema_version": "harness.planner_decision.v1",
        "base_state_sha256": state.state_sha256,
        "context_sha256": context.context_sha256,
        "action": action,
        "skill_ref": "text2env.compile@1.0.0" if action == "invoke_skill" else None,
        "parameters": {} if action == "invoke_skill" else None,
        "observation_keys": ["scene.contact"] if action == "request_observation" else [],
        "stop_reason": "done" if action == "stop" else None,
        "summary": "Decide.",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        PlannerDecision.model_validate(payload)


def test_parse_decision_requires_bytes() -> None:
    _, context = _context()

    with pytest.raises(TypeError, match="bytes"):
        parse_planner_decision("{}", context=context)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "attack",
    [
        "state",
        "context",
        "skill",
        "shape",
        "observation_order",
        "duplicate_key",
        "nan",
        "not_object",
        "trailing",
        "utf8",
        "oversize",
    ],
)
def test_parse_decision_fails_closed_on_untrusted_model_output(attack: str) -> None:
    state, context = _context()
    payload = {
        "schema_version": "harness.planner_decision.v1",
        "base_state_sha256": state.state_sha256,
        "context_sha256": context.context_sha256,
        "action": "invoke_skill",
        "skill_ref": "text2env.compile@1.0.0",
        "parameters": _compile_parameters(),
        "observation_keys": [],
        "stop_reason": None,
        "summary": "Compile.",
    }
    if attack == "state":
        payload["base_state_sha256"] = "8" * 64
    elif attack == "context":
        payload["context_sha256"] = "8" * 64
    elif attack == "skill":
        payload["skill_ref"] = "text2env.validate@2.0.0"
    elif attack == "shape":
        payload["parameters"] = None
    elif attack == "observation_order":
        payload.update(
            action="request_observation",
            skill_ref=None,
            parameters=None,
            observation_keys=["scene.z", "scene.a"],
        )
    raw = json.dumps(payload).encode()
    if attack == "duplicate_key":
        raw = (
            b'{"schema_version":"harness.planner_decision.v1",'
            b'"schema_version":"harness.planner_decision.v1"}'
        )
    elif attack == "nan":
        raw = b'{"value":NaN}'
    elif attack == "not_object":
        raw = b"[]"
    elif attack == "trailing":
        raw += b" extra"
    elif attack == "utf8":
        raw = b"\xff"
    elif attack == "oversize":
        raw = b" " * 70_000

    with pytest.raises((ValueError, ValidationError)):
        parse_planner_decision(raw, context=context, max_response_bytes=65_536)


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"request": "task"},
        {**_compile_parameters(), "seed": -1},
        {**_compile_parameters(), "unknown": True},
        {
            **_compile_parameters(),
            "asset_catalog": {
                **cast(dict[str, object], _compile_parameters()["asset_catalog"]),
                "schema_version": "wrong.catalog.v1",
            },
        },
    ],
)
def test_parse_invoke_decision_requires_the_advertised_typed_input_contract(
    parameters: dict[str, object],
) -> None:
    state, context = _context()
    payload = {
        "schema_version": "harness.planner_decision.v1",
        "base_state_sha256": state.state_sha256,
        "context_sha256": context.context_sha256,
        "action": "invoke_skill",
        "skill_ref": "text2env.compile@1.0.0",
        "parameters": parameters,
        "observation_keys": [],
        "stop_reason": None,
        "summary": "Compile.",
    }

    with pytest.raises(ValueError, match="typed input contract"):
        parse_planner_decision(json.dumps(payload).encode(), context=context)
