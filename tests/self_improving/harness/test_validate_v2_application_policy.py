"""Behavioral integrity tests for the qualified validate-v2 production seams."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import BaseModel

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_execution import QualifiedApplicationRunPendingError
from self_improving.harness.golden_validate_execution import (
    create_text2env_validate_execution_binding,
)
from self_improving.harness.golden_validate_policy import Text2EnvValidateV2EvidencePolicy
from self_improving.harness.handlers import text2env_validate_v2 as handler_module
from self_improving.harness.handlers.text2env_validate_v2 import (
    Text2EnvValidateV2Handler,
    verify_validate_v2_lineage,
)
from self_improving.harness.registry import (
    SkillBlocked,
    _canonical_json_sha256,
    _normalize_controlled_parameters,
)
from self_improving.harness.run_receipts import (
    PortableRunReceipt,
    load_portable_run_receipt,
)
from self_improving.harness.run_store import ControlledRunIntent, SQLiteRunStore
from self_improving.harness.schemas.common import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    RunStatus,
    ValidationStatus,
)
from self_improving.harness.schemas.text2env import EnvironmentPackage
from self_improving.harness.schemas.text2env_validate_v2 import Text2EnvValidateV2Input
from self_improving.harness.schemas.workflow_operation import (
    ToolResultV2,
    TrustedToolReceiptV2,
)
from self_improving.harness.trusted_state import TrustedWorldState
from self_improving.harness.validate_application import (
    ValidateApplicationConfigurationError,
    ValidateApplicationInputError,
    ValidateApplicationRunIdentityError,
    ValidateApplicationSettings,
    create_validate_application,
)
from self_improving.harness.validate_v2 import ValidateV2EvidenceError
from self_improving.harness.validate_v2_dependencies import (
    TEXT2ENV_VALIDATE_V2_SKILL_REF,
    Text2EnvValidateV2DependencyResolver,
    validate_v2_policy_sha256,
)
from tests.self_improving.harness import test_golden_validate_submit as fixture

RUN_ID = UUID("44000000-0000-4000-8000-000000000001")


class _WrongParameters(BaseModel):
    value: int = 1


def _read(store: LocalArtifactStore, ref: ArtifactRef, model):
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _completed_case(tmp_path: Path):
    case = fixture._case(tmp_path)
    operation = case.harness.submit(case.validate_command)
    assert operation.status is RunStatus.SUCCEEDED
    assert operation.tool_result is not None
    result = _read(case.store, operation.tool_result, ToolResultV2)
    state = case.validate_application.run_state(fixture.CHILD_IDS[2])
    assert state is not None
    receipt = _read(case.store, result.trusted_receipt, TrustedToolReceiptV2)
    return case, operation, state, result, receipt


def _put_document(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    schema_version: str,
    value: object,
) -> ArtifactRef:
    return fixture._put_model(
        store,
        root,
        name=name,
        value=value,
        schema_version=schema_version,
    )


def _put_portable_model(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    schema_version: str,
    value: BaseModel,
) -> ArtifactRef:
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    path = root / f"{name}.json"
    path.write_bytes(payload)
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _world_source(
    world: TrustedWorldState,
    *,
    key: str,
    source: ArtifactRef,
) -> TrustedWorldState:
    return world.model_copy(
        update={
            "facts": tuple(
                fact.model_copy(update={"source_artifact": source}) if fact.key == key else fact
                for fact in world.facts
            )
        }
    )


def _compile_source(world: TrustedWorldState, source: ArtifactRef) -> TrustedWorldState:
    return _world_source(
        _world_source(world, key="assets.catalog", source=source),
        key="environment.package",
        source=source,
    )


def _canonical_refs(refs: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    by_digest: dict[str, ArtifactRef] = {}
    for ref in refs:
        current = by_digest.get(ref.sha256)
        key = (ref.sha256, ref.media_type, ref.schema_version or "", ref.name)
        if current is None or key < (
            current.sha256,
            current.media_type,
            current.schema_version or "",
            current.name,
        ):
            by_digest[ref.sha256] = ref
    return tuple(
        sorted(
            by_digest.values(),
            key=lambda ref: (ref.sha256, ref.media_type, ref.schema_version or "", ref.name),
        )
    )


def _replace_ref(ref: ArtifactRef, old: ArtifactRef, new: ArtifactRef) -> ArtifactRef:
    return new if ref.sha256 == old.sha256 else ref


def _replace_json_ref(value: object, old: ArtifactRef, new: ArtifactRef) -> object:
    if isinstance(value, dict):
        if value.get("sha256") == old.sha256:
            return new.model_dump(mode="json")
        return {key: _replace_json_ref(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_json_ref(item, old, new) for item in value]
    return value


def _rebuild_replay_receipt(
    case,
    root: Path,
    *,
    old: ArtifactRef,
    new: ArtifactRef,
) -> ArtifactRef:
    loaded = load_portable_run_receipt(case.store, case.validate_input.replay_run_receipt)
    events = tuple(
        event.model_copy(
            update={
                "artifact_refs": tuple(_replace_ref(ref, old, new) for ref in event.artifact_refs)
            }
        )
        for event in loaded.run_state.events
    )
    state = loaded.run_state.model_copy(
        update={
            "events": events,
            "output": _replace_json_ref(loaded.run_state.output, old, new),
            "artifacts": _canonical_refs(
                tuple(_replace_ref(ref, old, new) for ref in loaded.run_state.artifacts)
            ),
        }
    )
    state_ref = _put_portable_model(
        case.store,
        root,
        name="mutated-replay-run-state",
        schema_version="harness.run_state.v1",
        value=state,
    )
    transcript_events = tuple(
        item.model_copy(
            update={"envelope": item.envelope.model_copy(update={"event": events[index]})}
        )
        for index, item in enumerate(loaded.event_transcript.events)
    )
    transcript = loaded.event_transcript.model_copy(update={"events": transcript_events})
    transcript_ref = _put_portable_model(
        case.store,
        root,
        name="mutated-replay-event-transcript",
        schema_version="harness.run_event_transcript.v1",
        value=transcript,
    )
    artifacts = _canonical_refs(
        tuple(_replace_ref(ref, old, new) for ref in loaded.receipt.artifacts)
    )
    closure = _canonical_refs(
        tuple(
            new
            if ref.sha256 == old.sha256
            else state_ref
            if ref == loaded.receipt.run_state
            else transcript_ref
            if ref == loaded.receipt.event_transcript
            else ref
            for ref in loaded.receipt.closure
        )
    )
    receipt = PortableRunReceipt.model_validate(
        loaded.receipt.model_copy(
            update={
                "run_state": state_ref,
                "event_transcript": transcript_ref,
                "artifacts": artifacts,
                "closure": closure,
            }
        )
    )
    return _put_portable_model(
        case.store,
        root,
        name="mutated-replay-portable-receipt",
        schema_version="harness.portable_run_receipt.v1",
        value=receipt,
    )


def test_validate_binding_exposes_the_exact_live_application_and_restart_authority(
    tmp_path: Path,
) -> None:
    case = fixture._case(tmp_path)
    application = case.validate_application
    binding = create_text2env_validate_execution_binding(application=application)
    adapter = binding.application
    assert adapter is not None
    assert adapter.artifact_root == case.store.root
    assert adapter.authority_path == application.journal_path
    assert adapter.skills == application.skills
    assert adapter.invocation(RUN_ID) is None
    assert adapter.run_state(RUN_ID) is None
    with pytest.raises(TypeError, match="Text2EnvValidateV2Input"):
        adapter.invoke(_WrongParameters(), run_id=RUN_ID)
    completed = adapter.invoke(case.validate_input, run_id=RUN_ID)
    assert completed.status is RunStatus.SUCCEEDED
    assert adapter.invocation(RUN_ID) is not None
    assert adapter.run_state(RUN_ID) == completed

    restart = create_text2env_validate_execution_binding(
        child_authority=SQLiteRunStore(application.journal_path),
        artifact_store=case.store,
    )
    assert restart.application is None
    assert restart.child_authority is not None


def test_validate_binding_rejects_crossed_authorities_and_inexact_types(tmp_path: Path) -> None:
    case = fixture._case(tmp_path)
    with pytest.raises(TypeError, match="exact ValidateApplication"):
        create_text2env_validate_execution_binding(application=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact SQLiteRunStore"):
        create_text2env_validate_execution_binding(child_authority=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact LocalArtifactStore"):
        create_text2env_validate_execution_binding(artifact_store=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="evidence artifact_store"):
        create_text2env_validate_execution_binding()
    with pytest.raises(ValueError, match="differs from validate application CAS"):
        create_text2env_validate_execution_binding(
            application=case.validate_application,
            artifact_store=LocalArtifactStore(tmp_path / "other-cas"),
        )

    adapter_type = type(
        create_text2env_validate_execution_binding(
            application=case.validate_application
        ).application
    )
    with pytest.raises(TypeError, match="exact ValidateApplication"):
        adapter_type(application=object(), policy=Text2EnvValidateV2EvidencePolicy(case.store))


def test_validate_adapter_translates_a_real_durable_pending_intent(tmp_path: Path) -> None:
    case = fixture._case(tmp_path)
    application = case.validate_application
    adapter = create_text2env_validate_execution_binding(application=application).application
    assert adapter is not None
    descriptor = application.skills[0]
    payload = case.validate_input.model_dump(mode="json")
    normalized, raw_sha = _normalize_controlled_parameters(payload)
    preview = Text2EnvValidateV2Input.model_validate(normalized)
    dependencies = application._registry._dependency_resolver.resolve(
        TEXT2ENV_VALIDATE_V2_SKILL_REF, preview
    )
    intent = ControlledRunIntent(
        run_id=RUN_ID,
        skill_id=descriptor.skill_id,
        skill_version=descriptor.version,
        input_schema=descriptor.input_schema,
        phase="validated_input",
        raw_parameters_sha256=raw_sha,
        effective_parameters_sha256=_canonical_json_sha256(preview.model_dump(mode="json")),
        descriptor_sha256=_canonical_json_sha256(descriptor.model_dump(mode="json")),
        implementation_sha256=descriptor.implementation_sha256,
        qualification_sha256=descriptor.qualification_artifact.sha256,
        max_attempts=descriptor.max_attempts,
        dependencies=dependencies,
    )
    assert application._run_store.reserve_controlled_intent(intent)
    with pytest.raises(QualifiedApplicationRunPendingError, match=str(RUN_ID)):
        adapter.invoke(case.validate_input, run_id=RUN_ID)


def test_validate_application_public_surface_checks_input_identity_and_reads_evidence(
    tmp_path: Path,
) -> None:
    case = fixture._case(tmp_path)
    application = case.validate_application
    assert application.skills[0].skill_id == "text2env.validate"
    assert application.artifact_root == case.store.root
    assert application.journal_path.name == "harness.sqlite3"
    assert application.invocation(RUN_ID) is None
    assert application.run_state(RUN_ID) is None
    assert application.events(run_id=RUN_ID).events == ()
    assert application.resolve_artifact(case.validate_input.environment_package).is_file()
    with pytest.raises(ValidateApplicationInputError, match="Text2EnvValidateV2Input"):
        application.invoke_typed(_WrongParameters())  # type: ignore[arg-type]
    with pytest.raises(ValidateApplicationRunIdentityError, match="UUID4"):
        application.invoke_typed(
            case.validate_input,
            run_id=UUID("00000000-0000-0000-0000-000000000000"),
        )
    missing = case.validate_input.model_copy(
        update={
            "environment_package": case.validate_input.environment_package.model_copy(
                update={"sha256": "f" * 64, "uri": f"artifact://sha256/{'f' * 64}"}
            )
        }
    )
    with pytest.raises(ValidateApplicationInputError, match="unavailable or corrupt"):
        application.invoke_typed(missing)
    completed = application.invoke_typed(case.validate_input)
    assert completed.status is RunStatus.SUCCEEDED


def test_validate_application_translates_controlled_identity_conflict(tmp_path: Path) -> None:
    case = fixture._case(tmp_path)
    application = case.validate_application
    assert (
        application.invoke_typed(case.validate_input, run_id=RUN_ID).status is RunStatus.SUCCEEDED
    )
    different = case.validate_input.model_copy(
        update={
            "environment_package": case.validate_input.environment_package.model_copy(
                update={"name": "same-content-different-intent"}
            )
        }
    )
    with pytest.raises(ValidateApplicationRunIdentityError, match="another intent"):
        application.invoke_typed(different, run_id=RUN_ID)


def test_validate_factory_rejects_invalid_settings_roots_and_state_entries(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="ValidateApplicationSettings"):
        create_validate_application(object())  # type: ignore[arg-type]

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    defaults = ValidateApplicationSettings(
        state_root=tmp_path / "state",
        evidence_artifact_root=evidence,
    )
    with pytest.raises(
        ValidateApplicationConfigurationError, match="qualification_root is missing"
    ):
        create_validate_application(
            replace(defaults, qualification_bundle_root=tmp_path / "missing-qualification")
        )

    state = tmp_path / "unexpected-state"
    state.mkdir()
    (state / "foreign.txt").write_text("not owned by validate\n")
    with pytest.raises(ValidateApplicationConfigurationError, match="unexpected entry"):
        create_validate_application(replace(defaults, state_root=state))

    symlink = tmp_path / "evidence-link"
    symlink.symlink_to(evidence, target_is_directory=True)
    with pytest.raises(ValidateApplicationConfigurationError, match="canonical directory"):
        create_validate_application(replace(defaults, evidence_artifact_root=symlink))

    with pytest.raises(ValidateApplicationConfigurationError, match="factory authority"):
        type(case := fixture._case(tmp_path / "sealed").validate_application)._from_factory(
            authority=object(),
            settings=case._settings,
            artifact_store=case._artifact_store,
            event_journal=case._event_journal,
            run_store=case._run_store,
            registry=case._registry,
        )

    corrupt_state = tmp_path / "corrupt-state"
    corrupt_state.mkdir()
    (corrupt_state / "harness.sqlite3").write_bytes(b"not sqlite")
    with pytest.raises(ValidateApplicationConfigurationError, match="SQLite authority"):
        create_validate_application(replace(defaults, state_root=corrupt_state))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("factory", "factory-issued authority"),
        ("settings", "settings type"),
        ("store_type", "artifact store"),
        ("store_root", "configured evidence CAS"),
        ("journal", "share the state database"),
        ("registry", "exact production Registry"),
        ("registry_authority", "factory authority set"),
        ("resolver", "dependency resolver"),
        ("registrations", "one exact validate Skill"),
        ("handler", "exact validate v2 handler"),
        ("descriptor", "descriptor type"),
        ("descriptor_contract", "fixed validate contract"),
        ("handler_binding", "dependency identity"),
        ("assembly_identity", "changed after construction"),
    ],
)
def test_validate_application_detects_post_factory_authority_tampering(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    case = fixture._case(tmp_path)
    application = case.validate_application
    if mutation == "factory":
        object.__setattr__(application, "_factory_authority", object())
    elif mutation == "settings":
        object.__setattr__(application, "_settings", object())
    elif mutation == "store_type":
        object.__setattr__(application, "_artifact_store", object())
    elif mutation == "store_root":
        object.__setattr__(
            application,
            "_settings",
            replace(application._settings, evidence_artifact_root=tmp_path / "other"),
        )
    elif mutation == "journal":
        object.__setattr__(application, "_event_journal", object())
    elif mutation == "registry":
        object.__setattr__(application, "_registry", object())
    elif mutation == "registry_authority":
        application._registry._clock = lambda: None
    elif mutation == "resolver":
        application._registry._dependency_resolver = object()
    elif mutation == "registrations":
        application._registry._registrations.clear()
    elif mutation == "handler":
        object.__setattr__(
            application._registry._registrations[("text2env.validate", "2.0.0")],
            "handler",
            object(),
        )
    elif mutation == "descriptor":
        object.__setattr__(
            application._registry._registrations[("text2env.validate", "2.0.0")],
            "descriptor",
            object(),
        )
    elif mutation == "descriptor_contract":
        registration = application._registry._registrations[("text2env.validate", "2.0.0")]
        object.__setattr__(
            registration,
            "descriptor",
            registration.descriptor.model_copy(update={"max_attempts": 2}),
        )
    elif mutation == "handler_binding":
        registration = application._registry._registrations[("text2env.validate", "2.0.0")]
        object.__setattr__(registration.handler, "scratch_parent", tmp_path / "wrong-scratch")
    else:
        object.__setattr__(application, "_assembly_identity", ("changed",))
    with pytest.raises(ValidateApplicationConfigurationError, match=message):
        _ = application.skills


def test_validate_policy_normalizes_authorizes_and_exposes_only_validation_claim(
    tmp_path: Path,
) -> None:
    case, _, state, _, _ = _completed_case(tmp_path)
    policy = Text2EnvValidateV2EvidencePolicy(case.store)
    normalized = policy.normalize_parameters(case.validate_input.model_dump(mode="json"))
    world = _read(case.store, case.revision_two.state_ref, TrustedWorldState)
    policy.authorize(world, normalized)
    claims = policy.claims(state)
    assert [claim.key for claim in claims] == ["environment.validation"]
    assert policy.diagnostics(state) == ()
    assert len(policy.evidence_policy_implementation_sha256) == 64
    assert Text2EnvValidateV2EvidencePolicy().skill_ref == TEXT2ENV_VALIDATE_V2_SKILL_REF
    with pytest.raises(TypeError, match="exact LocalArtifactStore"):
        Text2EnvValidateV2EvidencePolicy(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Text2EnvValidateV2Input"):
        policy.authorize(world, _WrongParameters())
    with pytest.raises(ValueError, match="exact evidence CAS"):
        Text2EnvValidateV2EvidencePolicy().authorize(world, normalized)


def test_validate_policy_rejects_non_revision_two_and_untrusted_world_facts(tmp_path: Path) -> None:
    case = fixture._case(tmp_path)
    policy = Text2EnvValidateV2EvidencePolicy(case.store)
    world = _read(case.store, case.revision_two.state_ref, TrustedWorldState)
    with pytest.raises(ValueError, match="exact revision-2"):
        policy.authorize(world.model_copy(update={"version": 4}), case.validate_input)
    facts = list(world.facts)
    facts[0] = facts[0].model_copy(update={"source_kind": "user_input"})
    with pytest.raises(ValueError, match="exact trusted receipts"):
        policy.authorize(world.model_copy(update={"facts": tuple(facts)}), case.validate_input)

    package_facts = list(world.facts)
    package_index = next(
        i for i, fact in enumerate(package_facts) if fact.key == "environment.package"
    )
    package_facts[package_index] = package_facts[package_index].model_copy(update={"value": {}})
    with pytest.raises(ValueError, match="package differs"):
        policy.authorize(
            world.model_copy(update={"facts": tuple(package_facts)}), case.validate_input
        )

    catalog_facts = list(world.facts)
    catalog_index = next(i for i, fact in enumerate(catalog_facts) if fact.key == "assets.catalog")
    catalog_facts[catalog_index] = catalog_facts[catalog_index].model_copy(update={"value": {}})
    with pytest.raises(ValueError, match="catalog differs"):
        policy.authorize(
            world.model_copy(update={"facts": tuple(catalog_facts)}), case.validate_input
        )


def test_validate_policy_rejects_real_cas_receipt_record_and_order_counterexamples(
    tmp_path: Path,
) -> None:
    case = fixture._case(tmp_path)
    policy = Text2EnvValidateV2EvidencePolicy(case.store)
    world = _read(case.store, case.revision_two.state_ref, TrustedWorldState)
    compile_ref = next(
        fact.source_artifact for fact in world.facts if fact.key == "environment.package"
    )
    replay_ref = next(
        fact.source_artifact for fact in world.facts if fact.key == "replay.runtime_evidence"
    )
    compile_receipt = _read(case.store, compile_ref, TrustedToolReceiptV2)
    replay_receipt = _read(case.store, replay_ref, TrustedToolReceiptV2)

    bad_child = compile_receipt.model_copy(
        update={"child_run_id": UUID("45000000-0000-4000-8000-000000000001")}
    )
    bad_child_ref = _put_document(
        case.store,
        tmp_path,
        name="compile-receipt-bad-child",
        schema_version="harness.trusted_tool_receipt.v2",
        value=bad_child,
    )
    with pytest.raises(ValueError, match="does not bind portable"):
        policy.authorize(_compile_source(world, bad_child_ref), case.validate_input)

    unordered = compile_receipt.model_copy(update={"base_revision": 2})
    unordered_ref = _put_document(
        case.store,
        tmp_path,
        name="compile-receipt-unordered",
        schema_version="harness.trusted_tool_receipt.v2",
        value=unordered,
    )
    with pytest.raises(ValueError, match="not one ordered"):
        policy.authorize(_compile_source(world, unordered_ref), case.validate_input)

    wrong_state = compile_receipt.model_copy(update={"run_state": replay_receipt.run_state})
    wrong_state_ref = _put_document(
        case.store,
        tmp_path,
        name="compile-receipt-wrong-state",
        schema_version="harness.trusted_tool_receipt.v2",
        value=wrong_state,
    )
    with pytest.raises(ValueError, match="child records differ"):
        policy.authorize(_compile_source(world, wrong_state_ref), case.validate_input)

    payload = case.store.resolve(compile_ref).path.read_bytes().rstrip() + b"  \n"
    noncanonical_path = tmp_path / "noncanonical-trusted-receipt.json"
    noncanonical_path.write_bytes(payload)
    noncanonical_ref = case.store.put_file(
        noncanonical_path,
        name="compile-receipt-noncanonical",
        media_type="application/json",
        schema_version="harness.trusted_tool_receipt.v2",
    )
    with pytest.raises(ValueError, match="not canonical JSON"):
        policy.authorize(_compile_source(world, noncanonical_ref), case.validate_input)


def test_validate_policy_rejects_provenance_and_history_cas_counterexamples(tmp_path: Path) -> None:
    case = fixture._case(tmp_path)
    policy = Text2EnvValidateV2EvidencePolicy(case.store)
    world = _read(case.store, case.revision_two.state_ref, TrustedWorldState)
    replay_ref = next(
        fact.source_artifact for fact in world.facts if fact.key == "replay.runtime_evidence"
    )
    provenance = json.loads(
        case.store.resolve(case.validate_input.request_provenance).path.read_bytes()
    )

    provenance["compile_trusted_receipt"] = replay_ref.model_dump(mode="json")
    wrong_receipt_provenance = _put_document(
        case.store,
        tmp_path,
        name="provenance-wrong-compile-receipt",
        schema_version="harness.text2env_request_provenance.v1",
        value=provenance,
    )
    with pytest.raises(ValueError, match="another compile trusted receipt"):
        policy.authorize(
            world,
            case.validate_input.model_copy(update={"request_provenance": wrong_receipt_provenance}),
        )

    provenance = json.loads(
        case.store.resolve(case.validate_input.request_provenance).path.read_bytes()
    )
    provenance["compile_history_authority"] = case.revision_two.receipt_head.model_dump(mode="json")
    wrong_history_provenance = _put_document(
        case.store,
        tmp_path,
        name="provenance-wrong-history",
        schema_version="harness.text2env_request_provenance.v1",
        value=provenance,
    )
    with pytest.raises(ValueError, match="does not close compile into replay"):
        policy.authorize(
            world,
            case.validate_input.model_copy(update={"request_provenance": wrong_history_provenance}),
        )


def test_validate_policy_checks_result_claim_diagnostic_and_supporting_closure(
    tmp_path: Path,
) -> None:
    case, _, state, result, receipt = _completed_case(tmp_path)
    policy = Text2EnvValidateV2EvidencePolicy(case.store)
    claims = policy.claims(state)
    policy.verify_result(
        state=state,
        claims=claims,
        result=result,
        supporting=receipt.supporting_artifacts,
    )
    observation = _read(case.store, case.revision_two.state_ref, TrustedWorldState).facts[0]
    bad_result = result.model_copy(update={"fresh_observations": (observation,)})
    with pytest.raises(ValueError, match="fresh observations"):
        policy.verify_result(
            state=state, claims=claims, result=bad_result, supporting=receipt.supporting_artifacts
        )
    for update, message in [
        ({"typed_output": {"wrong": True}}, "output differs"),
        ({"diagnostics": (case.validate_input.environment_package,)}, "diagnostics differ"),
    ]:
        with pytest.raises(ValueError, match=message):
            policy.verify_result(
                state=state,
                claims=claims,
                result=result.model_copy(update=update),
                supporting=receipt.supporting_artifacts,
            )
    with pytest.raises(ValueError, match="claims differ"):
        policy.verify_result(
            state=state, claims=(), result=result, supporting=receipt.supporting_artifacts
        )
    with pytest.raises(ValueError, match="output references absent"):
        policy.verify_result(state=state, claims=claims, result=result, supporting=())


def test_validate_policy_returns_sorted_blocker_evidence_for_terminal_failure(
    tmp_path: Path,
) -> None:
    case, _, state, completed_result, _ = _completed_case(tmp_path)
    first = case.validate_input.environment_package
    second = case.validate_input.runtime_evidence
    blocked = state.model_copy(
        update={
            "status": RunStatus.BLOCKED,
            "output": None,
            "blocker": Blocker(
                code="T2E_VALIDATION_EVIDENCE_INVALID",
                message="invalid evidence",
                stage="validation.evidence",
                retryable=False,
                details={},
                unknowns=(),
                artifact_refs=(second, first),
            ),
        }
    )
    policy = Text2EnvValidateV2EvidencePolicy(case.store)
    assert policy.claims(blocked) == ()
    assert policy.diagnostics(blocked) == tuple(
        sorted(
            (second, first), key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type)
        )
    )

    diagnostics = policy.diagnostics(blocked)
    blocked_result = completed_result.model_copy(
        update={
            "status": RunStatus.BLOCKED,
            "typed_output": None,
            "blocker": blocked.blocker,
            "diagnostics": diagnostics,
            "fresh_observations": (),
        }
    )
    with pytest.raises(ValueError, match="diagnostics are absent"):
        policy.verify_result(state=blocked, claims=(), result=blocked_result, supporting=())
    policy.verify_result(state=blocked, claims=(), result=blocked_result, supporting=diagnostics)


def test_validate_dependency_identity_is_source_bound_and_rejects_other_skills(
    tmp_path: Path,
) -> None:
    resolver = Text2EnvValidateV2DependencyResolver()
    assert resolver.resolve("text2env.other@1.0.0", _WrongParameters()) == ()
    dependencies = resolver.resolve(TEXT2ENV_VALIDATE_V2_SKILL_REF, _WrongParameters())
    assert len(dependencies) == 1
    assert dependencies[0].sha256 == validate_v2_policy_sha256()

    distribution = tmp_path / "distribution"
    distribution.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("not part of validate\n")
    target = distribution / "self_improving/harness/handlers/text2env_validate_v2.py"
    target.parent.mkdir(parents=True)
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="implementation path is unsafe"):
        validate_v2_policy_sha256(distribution_root=distribution)


def test_validate_handler_rejects_wrong_construction_and_dependency_identity(
    tmp_path: Path,
) -> None:
    case = fixture._case(tmp_path)
    dependency = case.validate_application._registry._dependency_resolver.resolve(
        TEXT2ENV_VALIDATE_V2_SKILL_REF,
        case.validate_input,
    )[0]
    with pytest.raises(TypeError, match="exact LocalArtifactStore"):
        Text2EnvValidateV2Handler(object(), tmp_path, dependency)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact DependencyRef"):
        Text2EnvValidateV2Handler(case.store, tmp_path, object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="identify validate v2"):
        Text2EnvValidateV2Handler(
            case.store,
            tmp_path,
            DependencyRef(name="wrong", version="2", sha256="a" * 64),
        )


def test_validate_lineage_rejects_changed_qualification_runtime_and_closure(tmp_path: Path) -> None:
    case = fixture._case(tmp_path)
    verify_validate_v2_lineage(case.store, case.validate_input)
    wrong_qualification = case.validate_input.model_copy(
        update={"compile_qualification": case.validate_input.replay_qualification}
    )
    with pytest.raises(ValueError, match="qualifications"):
        verify_validate_v2_lineage(case.store, wrong_qualification)
    wrong_runtime = case.validate_input.model_copy(
        update={"runtime_evidence": case.validate_input.runtime_validation_report}
    )
    with pytest.raises(ValidateV2EvidenceError, match="strict typed schema") as error:
        verify_validate_v2_lineage(case.store, wrong_runtime)
    assert error.value.reason == "invalid_reference"
    missing_closure = case.validate_input.model_copy(
        update={
            "runtime_event_transcript": case.validate_input.runtime_event_transcript.model_copy(
                update={
                    "sha256": case.validate_input.media_verification_receipt.sha256,
                    "uri": case.validate_input.media_verification_receipt.uri,
                    "bytes": case.validate_input.media_verification_receipt.bytes,
                }
            )
        }
    )
    with pytest.raises(ValueError, match="absent from the replay run closure"):
        verify_validate_v2_lineage(case.store, missing_closure)


def test_validate_lineage_rejects_package_output_media_and_provenance_counterexamples(
    tmp_path: Path,
) -> None:
    case = fixture._case(tmp_path)
    package = _read(case.store, case.validate_input.environment_package, EnvironmentPackage)
    changed_package = package.model_copy(update={"seed": package.seed + 1})
    changed_package_ref = _put_document(
        case.store,
        tmp_path,
        name="changed-environment-package",
        schema_version="harness.environment_package.v1",
        value=changed_package,
    )
    with pytest.raises(ValueError, match="compile and replay do not bind"):
        verify_validate_v2_lineage(
            case.store,
            case.validate_input.model_copy(update={"environment_package": changed_package_ref}),
        )

    renamed_runtime = case.validate_input.runtime_evidence.model_copy(
        update={"name": "renamed-runtime-evidence"}
    )
    with pytest.raises(ValueError, match="replay output does not bind"):
        verify_validate_v2_lineage(
            case.store,
            case.validate_input.model_copy(update={"runtime_evidence": renamed_runtime}),
        )

    media_document = json.loads(
        case.store.resolve(case.validate_input.media_verification_receipt).path.read_bytes()
    )
    media_document["verification"] = {}
    changed_media = _put_document(
        case.store,
        tmp_path,
        name="changed-media-verification",
        schema_version="harness.replay_media_verification.v1",
        value=media_document,
    )
    with pytest.raises(ValueError, match="media verification receipt does not match"):
        verify_validate_v2_lineage(
            case.store,
            case.validate_input.model_copy(update={"media_verification_receipt": changed_media}),
        )

    provenance = json.loads(
        case.store.resolve(case.validate_input.request_provenance).path.read_bytes()
    )
    provenance["request"] = "a different request"
    changed_provenance = _put_document(
        case.store,
        tmp_path,
        name="changed-request-provenance",
        schema_version="harness.text2env_request_provenance.v1",
        value=provenance,
    )
    with pytest.raises(ValueError, match="provenance does not bind"):
        verify_validate_v2_lineage(
            case.store,
            case.validate_input.model_copy(update={"request_provenance": changed_provenance}),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("schema", "schema is unsupported"),
        ("package", "does not bind package/runtime"),
        ("runtime", "does not bind runtime_evidence"),
        ("assets", "does not bind runtime assets"),
    ],
)
def test_validate_lineage_rejects_replay_execution_cas_counterexamples(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    case = fixture._case(tmp_path)
    old = case.validate_input.replay_execution_receipt
    execution = json.loads(case.store.resolve(old).path.read_bytes())
    if mutation == "schema":
        execution["schema_version"] = "harness.text2env_replay_receipt.v0"
    elif mutation == "package":
        execution["environment_package_id"] = "f" * 64
    elif mutation == "runtime":
        execution["runtime_evidence"]["bytes"] += 1
    else:
        execution["runtime_assets"]["manifest"]["bytes"] += 1
    new = _put_document(
        case.store,
        tmp_path,
        name=f"changed-replay-execution-{mutation}",
        schema_version="harness.text2env_replay_receipt.v1",
        value=execution,
    )
    replay_receipt = _rebuild_replay_receipt(case, tmp_path, old=old, new=new)
    changed = case.validate_input.model_copy(
        update={"replay_execution_receipt": new, "replay_run_receipt": replay_receipt}
    )
    with pytest.raises(ValueError, match=message):
        verify_validate_v2_lineage(case.store, changed)


def test_validate_handler_blocks_real_dependency_and_lineage_failures(tmp_path: Path) -> None:
    case = fixture._case(tmp_path)
    registration = case.validate_application._registry._registrations[
        ("text2env.validate", "2.0.0")
    ]
    handler = registration.handler
    with pytest.raises(SkillBlocked, match="dependency identity changed") as dependency_error:
        handler(case.validate_input, SimpleNamespace(dependencies=()))
    assert dependency_error.value.blocker.details == {"reason": "dependency_mismatch"}

    context = SimpleNamespace(
        dependencies=(handler.policy_dependency,), emit=lambda *_a, **_k: None
    )
    bad = case.validate_input.model_copy(
        update={"compile_qualification": case.validate_input.replay_qualification}
    )
    with pytest.raises(SkillBlocked, match="evidence closure is invalid") as lineage_error:
        handler(bad, context)
    assert lineage_error.value.blocker.details == {"reason": "ValueError"}


def test_validate_handler_integrity_helpers_cover_media_assets_json_and_status(
    tmp_path: Path,
) -> None:
    case = fixture._case(tmp_path)
    execution = json.loads(
        case.store.resolve(case.validate_input.replay_execution_receipt).path.read_bytes()
    )
    replay = load_portable_run_receipt(case.store, case.validate_input.replay_run_receipt)
    artifacts = {handler_module._ref_key(ref): ref for ref in replay.receipt.artifacts}
    handler_module._verify_media_members(execution, artifacts)
    handler_module._verify_runtime_asset_members(execution["runtime_assets"], artifacts)

    with pytest.raises(ValueError, match="no media closure"):
        handler_module._verify_media_members({"media": []}, artifacts)
    with pytest.raises(ValueError, match="media identity is invalid"):
        handler_module._verify_media_members({"media": [None]}, artifacts)
    bad_media = dict(execution["media"][0])
    bad_media["locator"] = "nested/file.mp4"
    with pytest.raises(ValueError, match="absent or inconsistently bound"):
        handler_module._verify_media_members({"media": [bad_media]}, artifacts)

    with pytest.raises(ValueError, match="no runtime asset members"):
        handler_module._verify_runtime_asset_members({"members": []}, artifacts)
    with pytest.raises(ValueError, match="member identity is invalid"):
        handler_module._verify_runtime_asset_members({"members": [None]}, artifacts)
    bad_member = dict(execution["runtime_assets"]["members"][0])
    bad_member["name"] = "changed"
    with pytest.raises(ValueError, match="absent or inconsistently bound"):
        handler_module._verify_runtime_asset_members({"members": [bad_member]}, artifacts)

    assert handler_module._identity_matches(None, case.validate_input.runtime_evidence) is False
    assert handler_module._strict_json(b'{"a":1}') == {"a": 1}
    with pytest.raises(ValueError, match="duplicate JSON member"):
        handler_module._strict_json(b'{"a":1,"a":2}')
    with pytest.raises(ValueError, match="must be a JSON object"):
        handler_module._strict_json(b"[]")
    with pytest.raises(ValueError, match="not canonical"):
        handler_module._canonical_model(
            b'{ "schema_version":"harness.replay_media_verification.v1", "verification":{} }',
            handler_module._MediaVerificationReceipt,
        )

    fail_blocker = handler_module._gate_blocker(
        ValidationStatus.FAIL, case.validate_input.runtime_validation_report
    )
    incomplete = handler_module._gate_blocker(
        ValidationStatus.INCOMPLETE, case.validate_input.runtime_validation_report
    )
    assert fail_blocker.code == "T2E_VALIDATION_FAILED"
    assert incomplete.code == "T2E_VALIDATION_INCOMPLETE"

    report = json.loads(
        case.store.resolve(case.validate_input.runtime_validation_report).path.read_bytes()
    )
    report["status"] = "fail"
    changed_report = _put_document(
        case.store,
        tmp_path,
        name="changed-validation-report",
        schema_version="robotwin.scene_validation.v1",
        value=report,
    )
    with pytest.raises(ValueError, match="recomputed validation status differs"):
        handler_module._verify_recomputed_status(case.store, case.validate_input, changed_report)

    compile_run = load_portable_run_receipt(case.store, case.validate_input.compile_run_receipt)
    wrong_receipt = compile_run.receipt.model_copy(update={"skill_ref": "text2env.replay@1.0.0"})
    with pytest.raises(ValueError, match="not a successful"):
        handler_module._require_successful_run(
            replace(compile_run, receipt=wrong_receipt), "text2env.compile@1.0.0"
        )
