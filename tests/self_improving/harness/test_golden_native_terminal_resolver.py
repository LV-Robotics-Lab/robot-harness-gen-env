"""Golden owns terminal admission roots without inlining native read closures."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_execution import create_golden_execution_catalog
from self_improving.harness.golden_native_terminal_resolver import (
    NativeImageExecutionBindings,
    NativeTerminalEvidenceRoots,
    NativeTerminalResolver,
    create_native_image_execution_binding_set,
    create_native_terminal_resolver,
    verify_same_native_sqlite_authority,
)
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.native_skill_projection import (
    issue_native_skill_projection,
    load_native_skill_projection,
    publish_native_terminal_registry_snapshot,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas.common import ArtifactRef, RunStatus
from self_improving.harness.schemas.native_terminal_admission import (
    NativeReadClosureV1,
    NativeTerminalAdmissionV1,
)
from self_improving.harness.schemas.registry_snapshot import RegistrySnapshot
from self_improving.harness.schemas.typed_qualification_admission import (
    TypedQualificationAdmissionV1,
)
from self_improving.harness.schemas.workflow import (
    RunCommandRequest,
    RunReadRequest,
    RunStartRequest,
)
from self_improving.harness.schemas.workflow_operation import (
    ToolResultV2,
    TrustedToolReceiptV2,
)
from self_improving.harness.schemas.x2env_compile_evidence import (
    SceneInterpretationProposalV1,
)
from self_improving.harness.trusted_state import TrustedWorldState, WorldFactEvidence
from self_improving.harness.typed_qualification_admission import (
    verify_typed_qualification_admission,
)
from tests.self_improving.harness.test_golden_image2env_execution import restored_apps  # noqa: F401

_RUN_IDS = tuple(
    map(
        UUID,
        (
            "a42a3a83-98ea-4c01-a35f-3003a9109c49",
            "bfc30903-0a69-478b-94ed-3ad2c9bbea03",
            "90af3aa7-6d77-4c5e-87d8-185b3bea36ea",
            "510c3c7d-c020-4623-b2bc-67e0dd045212",
        ),
    )
)


def _read(store: LocalArtifactStore, ref: ArtifactRef, model):
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _put_model(store: LocalArtifactStore, tmp_path: Path, name: str, model) -> ArtifactRef:
    payload = (
        json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        + b"\n"
    )
    path = tmp_path / name
    path.write_bytes(payload)
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=model.schema_version,
    )


def _formal_admissions(store: LocalArtifactStore):
    summary = os.environ.get("NATIVE_TERMINAL_ADMISSION_SUMMARY")
    if not summary:
        pytest.skip("requires the fixed M2-I terminal admission summary")
    rows = json.loads(Path(summary).read_bytes())["rows"]
    assert tuple(row["skill"] for row in rows) == (
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
        "genesis.observe@3.0.0",
        "image2env.validate@2.0.0",
    )
    result = []
    for row in rows:
        ref = ArtifactRef.model_validate(row["qualification"])
        admission = _read(store, ref, TypedQualificationAdmissionV1)
        result.append(
            verify_typed_qualification_admission(
                artifact_store=store,
                admission=admission,
            )
        )
    return tuple(result)


def test_public_native_terminal_resolver_surface_is_sealed() -> None:
    assert NativeTerminalEvidenceRoots is not None
    with pytest.raises(TypeError, match="factory"):
        NativeImageExecutionBindings((), object(), token=object())
    with pytest.raises(TypeError, match="factory"):
        NativeTerminalResolver(
            binding_set=object(),
            entries={},
            identity_sha256="0" * 64,
            token=object(),
        )


def test_sqlite_authority_uses_one_canonical_regular_file(tmp_path: Path) -> None:
    first = SQLiteRunStore(tmp_path / "runs.sqlite3")
    reopened = SQLiteRunStore(tmp_path / "runs.sqlite3")
    verify_same_native_sqlite_authority(first, reopened)

    foreign = SQLiteRunStore(tmp_path / "foreign.sqlite3")
    with pytest.raises(ValueError, match="different authority"):
        verify_same_native_sqlite_authority(first, foreign)
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(first.path.name)
    symlinked = SQLiteRunStore(alias)
    with pytest.raises(ValueError, match="canonical regular"):
        verify_same_native_sqlite_authority(first, symlinked)
    with pytest.raises(TypeError, match="exact stores"):
        verify_same_native_sqlite_authority(first, object())


def test_canonical_fact_helper_enters_public_golden_start(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    instant = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
    keyed_evidence = tuple(
        (
            key,
            _put_model(
                store,
                tmp_path,
                f"fact-{index}.json",
                WorldFactEvidence(
                    source_kind="user_input",
                    key=key,
                    value=value,
                    observed_at=instant,
                    valid_until=None,
                ),
            ),
        )
        for index, (key, value) in enumerate(
            (
                ("request.image_source", {"kind": "image"}),
                ("request.objective", "build the scene 1"),
            )
        )
    )
    evidence = tuple(
        sorted(
            (ref for _, ref in keyed_evidence),
            key=lambda ref: (ref.sha256, ref.bytes, ref.media_type, ref.schema_version or ""),
        )
    )
    evidence_keys = {ref.sha256: key for key, ref in keyed_evidence}
    assert tuple(evidence_keys[ref.sha256] for ref in evidence) != tuple(
        sorted(key for key, _ in keyed_evidence)
    )
    registry = _put_model(
        store,
        tmp_path,
        "registry.json",
        RegistrySnapshot(
            schema_version="harness.registry_snapshot.v1",
            qualified_skills=(),
        ),
    )
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden-start.sqlite3"),
        execution_catalog=create_golden_execution_catalog(),
        clock=lambda: instant,
        workflow_id_factory=lambda: UUID("98000000-0000-4000-8000-000000000098"),
    )
    snapshot = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="tests/canonical-terminal-start",
            idempotency_key="canonical-start",
            workspace="ephemeral",
            requested_profile="genesis.rigid_scene@1",
            initial_fact_evidence=evidence,
            registry_snapshot=registry,
        )
    )
    assert snapshot.revision == 0
    assert all(store.resolve(ref).path.read_bytes().endswith(b"\n") for ref in evidence)
    with pytest.raises(TypeError, match="four-binding"):
        create_native_image_execution_binding_set(bindings=())
    with pytest.raises(TypeError, match="sealed"):
        create_native_terminal_resolver(
            native_bindings=object(),
            qualification_admissions=(),
        )


def test_formal_terminal_projection_closes_four_existing_children_and_restart(
    restored_apps,  # noqa: F811
    tmp_path: Path,
) -> None:
    """No Genesis rerun: admit four already-terminal exact applications into Golden."""

    bindings, store, _, _ = restored_apps
    admissions = _formal_admissions(store)
    binding_set = create_native_image_execution_binding_set(bindings=bindings)
    resolver = create_native_terminal_resolver(
        native_bindings=binding_set,
        qualification_admissions=admissions,
    )
    with pytest.raises(TypeError, match="four exact verified"):
        create_native_terminal_resolver(
            native_bindings=binding_set,
            qualification_admissions=tuple(reversed(admissions)),
        )
    compile_lifecycle = bindings[0].child_authority.read_controlled_lifecycle(_RUN_IDS[0])
    with pytest.raises(ValueError, match="exact successful"):
        resolver.admit(
            lifecycle=compile_lifecycle,
            skill_ref=bindings[0].skill_ref,
            child_run_id=UUID("a42a3a83-98ea-4c01-a35f-3003a9109c50"),
        )

    projections = tuple(
        issue_native_skill_projection(
            artifact_store=store,
            admission=admission,
            native_binding=binding,
            terminal_resolver=resolver,
        )
        for binding, admission in zip(bindings, admissions, strict=True)
    )
    registry = publish_native_terminal_registry_snapshot(
        artifact_store=store,
        projections=projections,
    )
    invocations = tuple(
        binding.child_authority.read_invocation(run_id)
        for binding, run_id in zip(bindings, _RUN_IDS, strict=True)
    )
    states = tuple(
        binding.child_authority.read_run_state(run_id)
        for binding, run_id in zip(bindings, _RUN_IDS, strict=True)
    )
    assert all(invocations) and all(states)
    compile_input = invocations[0].effective_parameters["compile_input"]
    proposal = _read(
        store,
        ArtifactRef.model_validate(compile_input["interpretation_proposal"]),
        SceneInterpretationProposalV1,
    )
    prompt = store.resolve(proposal.effective_prompt).path.read_text(encoding="utf-8")
    started_at = states[0].started_at - timedelta(seconds=1)
    initial = tuple(
        sorted(
            (
                _put_model(
                    store,
                    tmp_path,
                    f"initial-{index}.json",
                    WorldFactEvidence(
                        source_kind="user_input",
                        key=key,
                        value=value,
                        observed_at=started_at,
                        valid_until=None,
                    ),
                )
                for index, (key, value) in enumerate(
                    (
                        ("request.objective", prompt),
                        ("request.image_source", compile_input["source_media"]),
                    )
                )
            ),
            key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
        )
    )
    database = tmp_path / "golden.sqlite3"
    now = [started_at]

    def harness_for(active_projections, ids):
        return GoldenRunHarness(
            artifact_store=store,
            workflow_store=SQLiteGoldenRunStore(database),
            execution_catalog=create_golden_execution_catalog(
                bindings=tuple(item.binding for item in active_projections)
            ),
            clock=lambda: now[0],
            workflow_id_factory=lambda: UUID("98000000-0000-4000-8000-000000000099"),
            child_run_id_factory=iter(ids).__next__,
        )

    harness = harness_for(projections, _RUN_IDS)
    snapshot = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="tests/native-terminal-resolver",
            idempotency_key="terminal-workflow-start",
            workspace="ephemeral",
            requested_profile="genesis.rigid_scene@1",
            initial_fact_evidence=initial,
            registry_snapshot=registry,
        )
    )
    receipts = []
    for index in range(2):
        now[0] = states[index].started_at
        operation = harness.submit(
            _command(snapshot, projections[index].binding.skill_ref, invocations[index], index)
        )
        assert operation.status is RunStatus.SUCCEEDED
        receipt = _read(
            store,
            _read(store, operation.tool_result, ToolResultV2).trusted_receipt,
            TrustedToolReceiptV2,
        )
        receipts.append(receipt)
        assert len(receipt.supporting_artifacts) == len(states[index].artifacts) + 2
        snapshot = harness.read(_read_request(snapshot))

    restarted_set = create_native_image_execution_binding_set(bindings=bindings)
    restarted_resolver = create_native_terminal_resolver(
        native_bindings=restarted_set,
        qualification_admissions=admissions,
    )
    restarted = tuple(
        load_native_skill_projection(
            artifact_store=store,
            admission_ref=projection.admission_ref,
            descriptor_ref=projection.descriptor_ref,
            qualification_ref=projection.qualification_ref,
            qualification_report_ref=projection.qualification_report_ref,
            native_binding=binding,
            terminal_resolver=restarted_resolver,
        )
        for projection, binding in zip(projections, bindings, strict=True)
    )
    harness = harness_for(restarted, _RUN_IDS[2:])
    snapshot = harness.read(_read_request(snapshot))
    for index in range(2, 4):
        now[0] = states[index].started_at
        operation = harness.submit(
            _command(snapshot, restarted[index].binding.skill_ref, invocations[index], index)
        )
        assert operation.status is RunStatus.SUCCEEDED
        result = _read(store, operation.tool_result, ToolResultV2)
        receipt = _read(store, result.trusted_receipt, TrustedToolReceiptV2)
        receipts.append(receipt)
        assert len(receipt.supporting_artifacts) == len(states[index].artifacts) + 2
        assert len(store.resolve(result.trusted_receipt).path.read_bytes()) < 100_000
        snapshot = harness.read(_read_request(snapshot))

    world = _read(store, snapshot.state_ref, TrustedWorldState)
    assert tuple(fact.key for fact in world.facts if fact.key.startswith("image.")) == (
        "image.compile",
        "image.fresh_capture",
        "image.observation",
        "image.replay",
        "image.validation",
    )
    observe_result = _read(store, receipts[2].run_state, type(states[2]))
    assert observe_result == states[2]

    compile_roots = _roots(receipts[0], states[0])
    compile_admission = _read(store, compile_roots.admission, NativeTerminalAdmissionV1)
    index = _read(store, compile_roots.read_closure, NativeReadClosureV1)
    assert len(index.artifacts) > len(receipts[0].supporting_artifacts)
    member_ref = compile_admission.admission_implementation
    delivered_ref, delivered = harness.read_workflow_artifact(
        _read_request(snapshot),
        sha256=member_ref.sha256,
    )
    assert delivered_ref == member_ref
    assert delivered == store.resolve(member_ref).path.read_bytes()


def _command(snapshot, skill_ref, invocation, index):
    return RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        command_id=UUID(f"99000000-0000-4000-8000-{index + 1:012d}"),
        principal_id=snapshot.principal_id,
        workflow_run_id=snapshot.workflow_run_id,
        idempotency_key=f"terminal-stage-{index}",
        expected_workflow_revision=snapshot.revision,
        expected_state_sha256=snapshot.state_sha256,
        expected_receipt_head_sha256=snapshot.receipt_head.sha256,
        skill_ref=skill_ref,
        parameters=invocation.effective_parameters,
    )


def _read_request(snapshot):
    return RunReadRequest(
        schema_version="harness.run_read_request.v1",
        principal_id=snapshot.principal_id,
        workflow_run_id=snapshot.workflow_run_id,
    )


def _roots(receipt: TrustedToolReceiptV2, state) -> NativeTerminalEvidenceRoots:
    native = {(ref.sha256, ref.schema_version or "", ref.media_type) for ref in state.artifacts}
    evidence = tuple(
        ref
        for ref in receipt.supporting_artifacts
        if (ref.sha256, ref.schema_version or "", ref.media_type) not in native
    )
    return NativeTerminalEvidenceRoots(
        admission=next(
            ref for ref in evidence if ref.schema_version == "harness.native_terminal_admission.v1"
        ),
        read_closure=next(
            ref for ref in evidence if ref.schema_version == "harness.native_read_closure.v1"
        ),
    )
