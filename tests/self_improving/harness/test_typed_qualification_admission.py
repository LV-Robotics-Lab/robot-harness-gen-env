"""Typed M1 qualification reports enter a portable admission without translation."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.registry import (
    HandlerResult,
    QualificationCandidate,
    SkillRegistry,
    StaticDependencyResolver,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas.common import (
    ArtifactRef,
    DependencyRef,
    SkillDescriptor,
    SkillDescriptorV2,
    SkillQualification,
)
from self_improving.harness.schemas.genesis_fresh_capture import (
    GenesisFreshCaptureInputV1,
    GenesisFreshCaptureQualificationV1,
    GenesisFreshCaptureReceiptV1,
)
from self_improving.harness.schemas.genesis_scene_application_v2 import (
    GenesisSceneReplayInputV2,
    GenesisSceneSkillCaseV2,
    GenesisSceneSkillQualificationReportV2,
)
from self_improving.harness.schemas.genesis_scene_assessment import (
    GenesisSceneValidateInputV1,
    GenesisSceneValidationQualificationV1,
    GenesisSceneValidationV1,
)
from self_improving.harness.schemas.genesis_scene_execution_v2 import (
    GenesisSceneExecutionReceiptV2,
)
from self_improving.harness.schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileReceiptV1,
    Image2EnvGenesisCompileRequestV1,
)
from self_improving.harness.schemas.image2env_genesis_compile_application import (
    Image2EnvGenesisSkillQualificationReportV1,
    NativeCompileQualificationCaseV1,
)
from self_improving.harness.schemas.typed_qualification_admission import (
    QualificationImplementationMemberV1,
)
from self_improving.harness.typed_qualification_admission import (
    TypedQualificationAdmissionError,
    freeze_typed_qualification_admission,
    verify_typed_qualification_admission,
)

STAMP = datetime(2026, 9, 11, tzinfo=timezone.utc)
RUN_IDS = tuple(UUID(f"81000000-0000-4000-8000-{index:012d}") for index in range(1, 4))


def _canonical(value: object) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _put(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    schema_version: str | None,
    media_type: str = "application/json",
) -> ArtifactRef:
    path = root / f"put-{hashlib.sha256(name.encode()).hexdigest()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return store.put_file(
        path,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


def _blob(
    store: LocalArtifactStore,
    root: Path,
    name: str,
    schema_version: str | None,
    media_type: str = "application/json",
) -> ArtifactRef:
    return _put(
        store,
        root,
        name=name,
        payload=f"fixture:{name}".encode(),
        schema_version=schema_version,
        media_type=media_type,
    )


def _output(
    kind: str,
    index: int,
    parameters: object,
    store: LocalArtifactStore,
    root: Path,
    implementation: tuple[ArtifactRef, ...],
):
    if kind == "compile":
        request = _put(
            store,
            root,
            name=f"compile-request-{index}.json",
            payload=_canonical(parameters),
            schema_version="harness.image2env_genesis_compile_request.v1",
        )
        return Image2EnvGenesisCompileReceiptV1(
            schema_version="harness.image2env_genesis_compile_receipt.v1",
            request=request,
            scene=_blob(store, root, f"scene-{index}.json", "harness.genesis_scene_spec.v1"),
            qualification=_blob(
                store,
                root,
                "asset-qualification.json",
                "harness.asset_qualification.v1",
            ),
            source_object_id="mouse",
            proposal_object_id="mouse",
            scene_object_id="object",
            implementation_sha256="c" * 64,
            implementation=implementation,
            scope="static_scene_candidate",
            simulator_executed=False,
        )
    if kind == "replay":
        return GenesisSceneExecutionReceiptV2(
            schema_version="harness.genesis_scene_execution_receipt.v2",
            authority="observed_execution_only",
            request=parameters.execution_request,
            launch_result=_blob(
                store,
                root,
                f"launch-{index}.json",
                "harness.genesis_scene_launch_result.v2",
            ),
            replay=_blob(store, root, f"replay-{index}.json", "harness.genesis_scene_replay.v1"),
            report=_blob(store, root, f"report-{index}.json", "harness.genesis_scene_report.v1"),
            baseline_preview=_blob(store, root, f"baseline-{index}.mp4", None, "video/mp4"),
            half_dt_preview=_blob(store, root, f"half-dt-{index}.mp4", None, "video/mp4"),
            retained_index=_blob(store, root, f"retained-{index}.json", None),
        )
    if kind == "observe":
        return GenesisFreshCaptureReceiptV1(
            schema_version="harness.genesis_fresh_capture_receipt.v1",
            replay_run_id=parameters.replay_run_id,
            replay_receipt=_blob(
                store,
                root,
                f"replay-receipt-{index}.json",
                "harness.genesis_scene_execution_receipt.v2",
            ),
            request=_blob(
                store,
                root,
                f"capture-request-{index}.json",
                "harness.genesis_fresh_capture_request.v1",
            ),
            capture_result=_blob(
                store,
                root,
                f"capture-result-{index}.json",
                "harness.genesis_fresh_capture_result.v1",
            ),
            image=_blob(store, root, f"capture-{index}.png", None, "image/png"),
            captured_at=STAMP + timedelta(seconds=index),
            checked_at=STAMP + timedelta(seconds=index + 1),
            expires_at=STAMP + timedelta(seconds=index + 300),
            ttl_seconds=300,
            authority="observed_restored_final_pose",
            process_continuation=False,
        )
    assert kind == "validate"
    return GenesisSceneValidationV1(
        schema_version="harness.genesis_scene_validation.v1",
        scope="genesis.rigid_scene@1",
        status="passed",
        replay_run_id=parameters.replay_run_id,
        invocation_digest=f"{index + 3:x}" * 64,
        execution_receipt=_blob(
            store,
            root,
            f"validated-execution-{index}.json",
            "harness.genesis_scene_execution_receipt.v2",
        ),
        scene=_blob(store, root, f"validated-scene-{index}.json", "harness.genesis_scene_spec.v1"),
        replay=_blob(
            store,
            root,
            f"validated-replay-{index}.json",
            "harness.genesis_scene_replay.v1",
        ),
        report=_blob(
            store,
            root,
            f"validated-report-{index}.json",
            "harness.genesis_scene_report.v1",
        ),
        assessed_at=STAMP + timedelta(seconds=index),
        simulator_rerun=False,
        robot_policy_evaluated=False,
        candidate_only=True,
    )


def _parameters(kind: str, index: int, store: LocalArtifactStore, root: Path):
    if kind == "compile":
        from tests.self_improving.harness.test_x2env_compile_closure import _case

        case = _case(root / f"compile-input-{index}")
        return Image2EnvGenesisCompileRequestV1(
            schema_version="harness.image2env_genesis_compile_request.v1",
            compile_input=case.compile_input,
            qualification_run_id=UUID(f"82000000-0000-4000-8000-{index:012d}"),
            source_scene=case.compile_input.interpretation_proposal.model_copy(
                update={"schema_version": None}
            ),
            table_center_m=(0.0, 0.0, 0.2),
            table_size_m=(1.0, 0.8, 0.4),
            table_friction=0.5,
        )
    if kind == "replay":
        return GenesisSceneReplayInputV2(
            schema_version="harness.genesis_scene_replay_input.v2",
            compile_run_id=UUID(f"83000000-0000-4000-8000-{index:012d}"),
            execution_request=_blob(
                store,
                root,
                f"execution-request-{index}.json",
                "harness.genesis_scene_execution_request.v2",
            ),
        )
    if kind == "observe":
        return GenesisFreshCaptureInputV1(
            schema_version="harness.genesis_fresh_capture_input.v1",
            replay_run_id=UUID(f"84000000-0000-4000-8000-{index:012d}"),
            ttl_seconds=300,
        )
    assert kind == "validate"
    return GenesisSceneValidateInputV1(
        schema_version="harness.genesis_scene_validate_input.v1",
        replay_run_id=UUID(f"85000000-0000-4000-8000-{index:012d}"),
    )


@dataclass(frozen=True)
class AdmissionFixture:
    store: LocalArtifactStore
    authority: SQLiteRunStore
    descriptor: ArtifactRef
    qualification: ArtifactRef
    report: ArtifactRef
    implementation: tuple[QualificationImplementationMemberV1, ...]


def _fixture(tmp_path: Path, kind: str) -> AdmissionFixture:
    store = LocalArtifactStore(tmp_path / "cas")
    authority_path = tmp_path / "children.sqlite3"
    authority = SQLiteRunStore(authority_path)
    implementation_names = (
        (
            "genesis_scene_assessment.py",
            "genesis_scene_validation_application.py",
            "schemas/genesis_scene_assessment.py",
            "registry.py",
            "run_store.py",
            "event_journal.py",
            "artifacts.py",
        )
        if kind == "validate"
        else (f"{kind}_application.py", f"{kind}_schema.py")
    )
    implementation = tuple(
        QualificationImplementationMemberV1(
            logical_path=name,
            artifact=_blob(
                store,
                tmp_path,
                name.replace("/", "__"),
                None,
                "text/x-python",
            ),
        )
        for name in implementation_names
    )
    refs = tuple(item.artifact for item in implementation)
    if kind == "compile":
        implementation_sha256 = hashlib.sha256(
            _canonical(
                {
                    "sources": [ref.model_dump(mode="json") for ref in refs],
                    "compile": "c" * 64,
                }
            )
        ).hexdigest()
        skill_id, version = "image2env.compile", "2.0.0"
        input_schema = "harness.image2env_genesis_compile_request.v1"
        output_schema = "harness.image2env_genesis_compile_receipt.v1"
        count = 3
    elif kind == "replay":
        implementation_sha256 = hashlib.sha256(
            _canonical(
                {
                    "numeric_identity": {
                        "numpy_version": "2.3.2",
                        "python_abi": "cpython-313-x86_64-linux-gnu",
                    },
                    "members": [ref.model_dump(mode="json") for ref in refs],
                }
            )
        ).hexdigest()
        skill_id, version = "image2env.replay", "2.0.0"
        input_schema = "harness.genesis_scene_replay_input.v2"
        output_schema = "harness.genesis_scene_execution_receipt.v2"
        count = 2
    elif kind == "observe":
        implementation_sha256 = hashlib.sha256(
            _canonical([ref.model_dump(mode="json") for ref in refs])
        ).hexdigest()
        skill_id, version = "genesis.observe", "3.0.0"
        input_schema = "harness.genesis_fresh_capture_input.v1"
        output_schema = "harness.genesis_fresh_capture_receipt.v1"
        count = 2
    else:
        implementation_sha256 = hashlib.sha256(
            _canonical(tuple((item.logical_path, item.artifact.sha256) for item in implementation))
        ).hexdigest()
        skill_id, version = "image2env.validate", "2.0.0"
        input_schema = "harness.genesis_scene_validate_input.v1"
        output_schema = "harness.genesis_scene_validation.v1"
        count = 2

    run_ids = iter(RUN_IDS[:count])
    dependency = DependencyRef(name="fixture_implementation", version="1", sha256="d" * 64)
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=StaticDependencyResolver(
            {f"{skill_id}@{version}": (dependency,)}
        ),
        event_sink=SQLiteEventJournal(authority_path),
        clock=lambda: STAMP,
        run_id_factory=lambda: next(run_ids),
        run_store=authority,
    )
    candidate = QualificationCandidate(
        skill_id=skill_id,
        version=version,
        input_schema=input_schema,
        output_schema=output_schema,
        max_attempts=1,
    )
    outputs: list[ArtifactRef] = []

    def handler(parameters, _context):
        index = len(outputs) + 1
        value = _output(kind, index, parameters, store, tmp_path, refs)
        output_ref = _put(
            store,
            tmp_path,
            name=f"{kind}-output-{index}.json",
            payload=_canonical(value),
            schema_version=output_schema,
        )
        outputs.append(output_ref)
        return HandlerResult(value, (output_ref,))

    evaluations = tuple(
        registry.evaluate_candidate(
            candidate,
            handler,
            _parameters(kind, index, store, tmp_path).model_dump(mode="json"),
        )
        for index in range(1, count + 1)
    )
    assert all(item.state.status.value == "succeeded" for item in evaluations)
    invocations = tuple(item.invocation for item in evaluations)
    assert all(item is not None for item in invocations)
    if kind == "compile":
        report_model = Image2EnvGenesisSkillQualificationReportV1(
            schema_version="harness.image2env_genesis_skill_qualification_report.v1",
            skill_ref="image2env.compile@2.0.0",
            status="pass",
            scope="static_scene_candidate",
            cases=tuple(
                NativeCompileQualificationCaseV1(
                    run_id=item.state.run_id,
                    invocation_digest=item.invocation.invocation_digest,
                    compile_receipt=output,
                )
                for item, output in zip(evaluations, outputs, strict=True)
            ),
            application_source=refs[0],
            implementation_sources=refs,
            compile_implementation_sha256="c" * 64,
            implementation_sha256=implementation_sha256,
        )
    elif kind == "replay":
        report_model = GenesisSceneSkillQualificationReportV2(
            schema_version="harness.genesis_scene_skill_qualification_report.v2",
            skill_ref="image2env.replay@2.0.0",
            cases=tuple(
                GenesisSceneSkillCaseV2(
                    candidate_run_id=item.state.run_id,
                    compile_run_id=UUID(
                        f"83000000-0000-4000-8000-{index:012d}"
                    ),
                    invocation_digest=item.invocation.invocation_digest,
                    execution_request=_parameters(
                        kind, index, store, tmp_path
                    ).execution_request,
                    execution_receipt=output,
                )
                for index, (item, output) in enumerate(
                    zip(evaluations, outputs, strict=True), start=1
                )
            ),
            approved_manifest=_blob(
                store,
                tmp_path,
                "runtime-manifest.json",
                "harness.genesis_runtime_manifest.v1",
            ),
            compile_qualification=_blob(
                store,
                tmp_path,
                "compile-qualification.json",
                "harness.skill_qualification.v1",
            ),
            implementation=refs,
            implementation_sha256=implementation_sha256,
            numpy_version="2.3.2",
            python_abi="cpython-313-x86_64-linux-gnu",
            invariant=(
                "registered_compile_dual_profile_34_checks_and_41_exact_decoded_frames_each"
            ),
        )
    elif kind == "observe":
        report_model = GenesisFreshCaptureQualificationV1(
            schema_version="harness.genesis_fresh_capture_qualification.v1",
            skill_ref="genesis.observe@3.0.0",
            candidate_runs=tuple(item.state.run_id for item in evaluations),
            receipts=tuple(outputs),
            capture_manifest=_blob(
                store,
                tmp_path,
                "capture-manifest.json",
                "harness.genesis_runtime_manifest.v1",
            ),
            replay_qualification=_blob(
                store,
                tmp_path,
                "replay-qualification.json",
                "harness.skill_qualification.v1",
            ),
            implementation=refs,
            implementation_sha256=implementation_sha256,
        )
    else:
        report_model = GenesisSceneValidationQualificationV1(
            schema_version="harness.genesis_scene_validation_qualification.v1",
            skill_ref="image2env.validate@2.0.0",
            candidate_runs=tuple(item.state.run_id for item in evaluations),
            validations=tuple(outputs),
            implementation_sha256=implementation_sha256,
        )
    report = _put(
        store,
        tmp_path,
        name=f"{kind}-qualification-report.json",
        payload=_canonical(report_model),
        schema_version=report_model.schema_version,
    )
    qualification_model = SkillQualification(
        skill_ref=f"{skill_id}@{version}",
        status="pass",
        deterministic_case_id=f"fixture-{kind}",
        regression_command=f"pytest -q fixture-{kind}",
        report_sha256=report.sha256,
    )
    qualification = _put(
        store,
        tmp_path,
        name=f"{kind}-qualification.json",
        payload=_canonical(qualification_model),
        schema_version="harness.skill_qualification.v1",
    )
    descriptor_model = (
        SkillDescriptor(
            skill_id=skill_id,
            version=version,
            mcp_tool_name=f"{skill_id.replace('.', '_')}_v2_0_0",
            input_schema=input_schema,
            output_schema=output_schema,
            implementation_name=f"fixture.{kind}",
            implementation_version="1",
            implementation_sha256=implementation_sha256,
            deterministic=True,
            max_attempts=1,
            qualification_artifact=qualification,
        )
        if kind == "compile"
        else SkillDescriptorV2(
            skill_id=skill_id,
            version=version,
            mcp_tool_name=(
                "genesis_observe_v3_0_0"
                if kind == "observe"
                else f"{skill_id.replace('.', '_')}_v2_0_0"
            ),
            input_schema=input_schema,
            output_schema=output_schema,
            implementation_name=f"fixture.{kind}",
            implementation_version="1",
            implementation_sha256=implementation_sha256,
            reproducibility="evidence_invariant_repeatable",
            max_attempts=1,
            qualification_artifact=qualification,
        )
    )
    descriptor = _put(
        store,
        tmp_path,
        name=f"{kind}-descriptor.json",
        payload=_canonical(descriptor_model),
        schema_version=descriptor_model.model_config["json_schema_extra"]["$id"],
    )
    return AdmissionFixture(
        store=store,
        authority=authority,
        descriptor=descriptor,
        qualification=qualification,
        report=report,
        implementation=implementation,
    )


@pytest.mark.parametrize("kind", ["compile", "replay", "observe", "validate"])
def test_freezes_each_native_m1_report_without_generic_translation(
    tmp_path: Path,
    kind: str,
) -> None:
    fixture = _fixture(tmp_path / kind, kind)

    verified = freeze_typed_qualification_admission(
        artifact_store=fixture.store,
        child_authority=fixture.authority,
        descriptor_ref=fixture.descriptor,
        qualification_ref=fixture.qualification,
        qualification_report_ref=fixture.report,
        implementation_members=fixture.implementation,
    )

    admission = verified.admission
    assert admission.qualification_report == fixture.report
    assert admission.qualification_report.schema_version != (
        "harness.skill_qualification_report.v1"
    )
    assert admission.descriptor == fixture.descriptor
    assert admission.qualification == fixture.qualification
    assert tuple(item.run_id for item in admission.candidate_intents) == RUN_IDS[
        : len(admission.candidate_intents)
    ]
    assert verify_typed_qualification_admission(
        artifact_store=fixture.store,
        admission=admission,
    ).admission == admission

    relocated_root = tmp_path / f"{kind}-relocated-cas"
    shutil.copytree(fixture.store.root, relocated_root)
    assert verify_typed_qualification_admission(
        artifact_store=LocalArtifactStore(relocated_root),
        admission=admission,
    ).admission == admission


def test_rejects_extra_implementation_and_child_intent_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "compile", "compile")
    extra = QualificationImplementationMemberV1(
        logical_path="extra.py",
        artifact=_blob(fixture.store, tmp_path, "extra.py", None, "text/x-python"),
    )
    with pytest.raises(TypedQualificationAdmissionError, match="implementation"):
        freeze_typed_qualification_admission(
            artifact_store=fixture.store,
            child_authority=fixture.authority,
            descriptor_ref=fixture.descriptor,
            qualification_ref=fixture.qualification,
            qualification_report_ref=fixture.report,
            implementation_members=(*fixture.implementation, extra),
        )

    verified = freeze_typed_qualification_admission(
        artifact_store=fixture.store,
        child_authority=fixture.authority,
        descriptor_ref=fixture.descriptor,
        qualification_ref=fixture.qualification,
        qualification_report_ref=fixture.report,
        implementation_members=fixture.implementation,
    )
    first = verified.admission.candidate_intents[0]
    drifted = verified.admission.model_copy(
        update={
            "candidate_intents": (
                first.model_copy(update={"invocation_digest": "f" * 64}),
                *verified.admission.candidate_intents[1:],
            )
        }
    )
    with pytest.raises(TypedQualificationAdmissionError, match="intent"):
        verify_typed_qualification_admission(
            artifact_store=fixture.store,
            admission=drifted,
        )


def test_rejects_generic_report_substitution_and_missing_cas_bytes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "observe", "observe")
    generic = fixture.report.model_copy(
        update={"schema_version": "harness.skill_qualification_report.v1"}
    )
    with pytest.raises(TypedQualificationAdmissionError, match="typed report"):
        freeze_typed_qualification_admission(
            artifact_store=fixture.store,
            child_authority=fixture.authority,
            descriptor_ref=fixture.descriptor,
            qualification_ref=fixture.qualification,
            qualification_report_ref=generic,
            implementation_members=fixture.implementation,
        )

    fixture.store.resolve(fixture.implementation[0].artifact).path.unlink()
    with pytest.raises(TypedQualificationAdmissionError, match="implementation"):
        freeze_typed_qualification_admission(
            artifact_store=fixture.store,
            child_authority=fixture.authority,
            descriptor_ref=fixture.descriptor,
            qualification_ref=fixture.qualification,
            qualification_report_ref=fixture.report,
            implementation_members=fixture.implementation,
        )
