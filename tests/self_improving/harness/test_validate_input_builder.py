"""Public tracer tests for constructing validate-v2 input from a replay head."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas.workflow import RunSnapshot
from self_improving.harness.schemas.workflow_operation import (
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.validate_input_builder import (
    Text2EnvValidateV2InputBuilder,
    ValidateInputBuildError,
)
from tests.self_improving.harness import test_golden_validate_submit as validate_fixture


@dataclass(frozen=True)
class _Cases:
    primary: validate_fixture.ValidateCase
    foreign: validate_fixture.ValidateCase
    root: Path


@pytest.fixture(scope="module")
def cases(tmp_path_factory: pytest.TempPathFactory) -> _Cases:
    root = tmp_path_factory.mktemp("validate-input-builder")
    return _Cases(
        primary=validate_fixture._case(root / "primary"),
        foreign=validate_fixture._case(root / "foreign"),
        root=root,
    )


def _builder(case: validate_fixture.ValidateCase) -> Text2EnvValidateV2InputBuilder:
    return Text2EnvValidateV2InputBuilder(
        artifact_store=case.store,
        compile_application=case.compile_application,
        replay_application=case.replay_application,
    )


def _read(case: validate_fixture.ValidateCase, ref, model):
    return model.model_validate_json(case.store.resolve(ref).path.read_bytes())


def _put_model(
    cases: _Cases,
    *,
    name: str,
    value,
    schema_version: str,
):
    return validate_fixture._put_model(
        cases.primary.store,
        cases.root,
        name=name,
        value=value,
        schema_version=schema_version,
    )


def _put_bytes(cases: _Cases, *, name: str, payload: bytes, schema_version: str):
    source = cases.root / name
    source.write_bytes(payload)
    return cases.primary.store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _histories(case: validate_fixture.ValidateCase):
    replay = _read(case, case.revision_two.receipt_head, WorkflowOperationReceiptV2)
    compile_ = _read(case, replay.predecessor_receipt, WorkflowOperationReceiptV2)
    return compile_, replay


def _snapshot_with_chain(
    cases: _Cases,
    *,
    compile_history: WorkflowOperationReceiptV2,
    replay_history: WorkflowOperationReceiptV2,
    label: str,
) -> RunSnapshot:
    compile_ref = _put_model(
        cases,
        name=f"{label}-compile-history.json",
        value=compile_history,
        schema_version="harness.workflow_operation_receipt.v2",
    )
    replay_ref = _put_model(
        cases,
        name=f"{label}-replay-history.json",
        value=replay_history.model_copy(update={"predecessor_receipt": compile_ref}),
        schema_version="harness.workflow_operation_receipt.v2",
    )
    return cases.primary.revision_two.model_copy(update={"receipt_head": replay_ref})


def test_builder_returns_the_exact_existing_validate_input(cases: _Cases) -> None:
    builder = _builder(cases.primary)
    built = builder.build(cases.primary.revision_two)
    repeated = builder.build(cases.primary.revision_two)

    assert repeated == built
    assert built.gate_profile == "robotwin.scene_validation.v1"
    for field in (
        "environment_package",
        "compile_run_receipt",
        "compile_qualification",
        "replay_run_receipt",
        "replay_qualification",
        "replay_execution_receipt",
        "runtime_evidence",
        "runtime_validation_report",
        "runtime_asset_snapshot_manifest",
        "media_verification_receipt",
        "runtime_event_transcript",
    ):
        assert (
            getattr(built, field).sha256
            == getattr(
                cases.primary.validate_input,
                field,
            ).sha256
        )
    provenance = json.loads(cases.primary.store.resolve(built.request_provenance).path.read_bytes())
    assert provenance["request"] == validate_fixture.REQUEST
    assert provenance["seed"] == 7
    assert provenance["environment_package"] == built.environment_package.model_dump(mode="json")


@pytest.mark.parametrize(
    ("argument", "message"),
    [
        ("artifact_store", "artifact_store must be the exact LocalArtifactStore"),
        ("compile_application", "compile_application must be the exact CompileApplication"),
        ("replay_application", "replay_application must be the exact ReplayApplication"),
    ],
)
def test_builder_rejects_non_authority_dependencies(
    cases: _Cases,
    argument: str,
    message: str,
) -> None:
    values = {
        "artifact_store": cases.primary.store,
        "compile_application": cases.primary.compile_application,
        "replay_application": cases.primary.replay_application,
    }
    values[argument] = object()

    with pytest.raises(TypeError, match=message):
        Text2EnvValidateV2InputBuilder(**values)


@pytest.mark.parametrize("foreign_dependency", ["compile", "replay"])
def test_builder_rejects_an_application_from_another_cas(
    cases: _Cases,
    foreign_dependency: str,
) -> None:
    compile_application = cases.primary.compile_application
    replay_application = cases.primary.replay_application
    if foreign_dependency == "compile":
        compile_application = cases.foreign.compile_application
    else:
        replay_application = cases.foreign.replay_application

    with pytest.raises(ValueError, match="must share one artifact store"):
        Text2EnvValidateV2InputBuilder(
            artifact_store=cases.primary.store,
            compile_application=compile_application,
            replay_application=replay_application,
        )


def test_builder_requires_an_exact_run_snapshot(cases: _Cases) -> None:
    with pytest.raises(TypeError, match="snapshot must be the exact RunSnapshot"):
        _builder(cases.primary).build(object())


@pytest.mark.parametrize(
    "snapshot",
    [
        pytest.param(
            lambda case: case.revision_two.model_copy(update={"revision": 1}),
            id="wrong-revision",
        ),
        pytest.param(
            lambda case: case.revision_two.model_copy(
                update={"active_operation": UUID("42000000-0000-4000-8000-000000000099")}
            ),
            id="active-operation",
        ),
    ],
)
def test_builder_requires_a_terminal_replay_revision_two(cases: _Cases, snapshot) -> None:
    with pytest.raises(
        ValidateInputBuildError,
        match="requires a terminal replay revision 2",
    ):
        _builder(cases.primary).build(snapshot(cases.primary))


@pytest.mark.parametrize(
    "attack",
    [
        "compile-skill",
        "replay-skill",
        "compile-workflow",
        "replay-workflow",
        "compile-revisions",
        "replay-revisions",
        "same-operation",
    ],
)
def test_builder_rejects_every_non_exact_compile_replay_chain(
    cases: _Cases,
    attack: str,
) -> None:
    compile_history, replay_history = _histories(cases.primary)
    if attack == "compile-skill":
        compile_tool = _read(
            cases.primary,
            compile_history.tool_receipt,
            TrustedToolReceiptV2,
        )
        changed_skill = compile_tool.skill.model_copy(update={"skill_ref": "text2env.replay@1.0.0"})
        changed_tool = compile_tool.model_copy(update={"skill": changed_skill})
        tool_ref = _put_model(
            cases,
            name="wrong-compile-tool.json",
            value=changed_tool,
            schema_version="harness.trusted_tool_receipt.v2",
        )
        compile_history = compile_history.model_copy(update={"tool_receipt": tool_ref})
    elif attack == "replay-skill":
        replay_tool = _read(
            cases.primary,
            replay_history.tool_receipt,
            TrustedToolReceiptV2,
        )
        changed_skill = replay_tool.skill.model_copy(update={"skill_ref": "text2env.compile@1.0.0"})
        changed_tool = replay_tool.model_copy(update={"skill": changed_skill})
        tool_ref = _put_model(
            cases,
            name="wrong-replay-tool.json",
            value=changed_tool,
            schema_version="harness.trusted_tool_receipt.v2",
        )
        replay_history = replay_history.model_copy(update={"tool_receipt": tool_ref})
    elif attack == "compile-workflow":
        compile_history = compile_history.model_copy(
            update={"workflow_run_id": UUID("10000000-0000-4000-8000-000000000099")}
        )
    elif attack == "replay-workflow":
        replay_history = replay_history.model_copy(
            update={"workflow_run_id": UUID("10000000-0000-4000-8000-000000000099")}
        )
    elif attack == "compile-revisions":
        compile_history = compile_history.model_copy(
            update={"base_revision": 1, "committed_revision": 2}
        )
    elif attack == "replay-revisions":
        replay_history = replay_history.model_copy(
            update={"base_revision": 2, "committed_revision": 3}
        )
    else:
        compile_history = compile_history.model_copy(
            update={"operation_id": replay_history.operation_id}
        )
    snapshot = _snapshot_with_chain(
        cases,
        compile_history=compile_history,
        replay_history=replay_history,
        label=attack,
    )

    with pytest.raises(
        ValidateInputBuildError,
        match="workflow receipt chain is not exact compile then replay",
    ):
        _builder(cases.primary).build(snapshot)


def test_builder_rejects_noncanonical_workflow_evidence(cases: _Cases) -> None:
    _, replay_history = _histories(cases.primary)
    payload = (replay_history.model_dump_json(indent=2) + "\n").encode()
    replay_ref = _put_bytes(
        cases,
        name="noncanonical-replay-history.json",
        payload=payload,
        schema_version="harness.workflow_operation_receipt.v2",
    )
    snapshot = cases.primary.revision_two.model_copy(update={"receipt_head": replay_ref})

    with pytest.raises(
        ValidateInputBuildError,
        match="workflow evidence must be canonical JSON",
    ):
        _builder(cases.primary).build(snapshot)


def test_builder_does_not_relabel_invalid_workflow_json(cases: _Cases) -> None:
    replay_ref = _put_bytes(
        cases,
        name="invalid-replay-history.json",
        payload=b"[]\n",
        schema_version="harness.workflow_operation_receipt.v2",
    )
    snapshot = cases.primary.revision_two.model_copy(update={"receipt_head": replay_ref})

    with pytest.raises(ValidationError, match="Input should be an object"):
        _builder(cases.primary).build(snapshot)


def test_builder_rejects_an_artifact_store_that_is_not_shared(cases: _Cases) -> None:
    other_store = LocalArtifactStore(cases.root / "unrelated-cas")

    with pytest.raises(ValueError, match="must share one artifact store"):
        Text2EnvValidateV2InputBuilder(
            artifact_store=other_store,
            compile_application=cases.primary.compile_application,
            replay_application=cases.primary.replay_application,
        )
