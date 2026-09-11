from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid1

import pytest
from PIL import Image

import self_improving.harness as harness
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.image2env_compile import Image2EnvExactReuseCompileHandler
from self_improving.harness.image_ingest import ImageMediaIngestor
from self_improving.harness.qualification import QualificationCheckV1, QualificationReportV1
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas import x2env_compile_evidence as evidence
from self_improving.harness.schemas.common import (
    ArtifactRef,
    RunStatus,
    SkillDescriptor,
    SkillQualification,
)
from self_improving.harness.schemas.x2env import (
    Image2EnvCompileV2Input,
    Image2EnvCompileV2Output,
)

try:
    from self_improving.harness.image2env_compile_application import (
        ControlledImage2EnvCompileApplication,
        ControlledImage2EnvCompileApplicationRunIdentityError,
        ControlledImage2EnvCompileApplicationRunPendingError,
        ControlledImage2EnvCompileApplicationSettings,
        create_controlled_image2env_compile_application,
    )
except ImportError:
    ControlledImage2EnvCompileApplication = None  # type: ignore[assignment,misc]
    ControlledImage2EnvCompileApplicationRunIdentityError = None  # type: ignore[assignment,misc]
    ControlledImage2EnvCompileApplicationRunPendingError = None  # type: ignore[assignment,misc]
    ControlledImage2EnvCompileApplicationSettings = None  # type: ignore[assignment,misc]
    create_controlled_image2env_compile_application = None  # type: ignore[assignment]


_RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")
_PENDING_RUN_ID = UUID("22345678-1234-4234-9234-123456789abc")
_QUALIFICATION_ISSUER_SHA256 = "6" * 64


class _SequenceClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


@dataclass(frozen=True, slots=True)
class _ControlledCase:
    evidence_root: Path
    compile_input: Image2EnvCompileV2Input
    runtime_lock: ArtifactRef
    asset_qualification: ArtifactRef
    implementation_sha256: str

    def handler(self, artifact_store: LocalArtifactStore | None = None):
        store = artifact_store or LocalArtifactStore(self.evidence_root)
        return Image2EnvExactReuseCompileHandler(
            artifact_store=store,
            max_source_bytes=4096,
            max_decoded_pixels=4096,
            expected_implementation_sha256=self.implementation_sha256,
            trusted_qualification_issuer_sha256=_QUALIFICATION_ISSUER_SHA256,
            expected_runtime_lock=self.runtime_lock,
            trusted_asset_qualifications=(self.asset_qualification,),
        )


def _require_application_api() -> None:
    assert ControlledImage2EnvCompileApplication is not None, (
        "controlled durable image2env compile application is absent"
    )
    assert ControlledImage2EnvCompileApplicationSettings is not None
    assert ControlledImage2EnvCompileApplicationRunIdentityError is not None
    assert ControlledImage2EnvCompileApplicationRunPendingError is not None
    assert create_controlled_image2env_compile_application is not None
    assert harness.ControlledImage2EnvCompileApplication is ControlledImage2EnvCompileApplication
    assert (
        harness.ControlledImage2EnvCompileApplicationSettings
        is ControlledImage2EnvCompileApplicationSettings
    )
    assert (
        harness.create_controlled_image2env_compile_application
        is create_controlled_image2env_compile_application
    )


def _canonical_json(value: object) -> bytes:
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


def _put(
    store: LocalArtifactStore,
    staging: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    path = staging / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return store.put_file(
        path,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


def _publish_model(
    store: LocalArtifactStore,
    staging: Path,
    name: str,
    value: Any,
    *,
    schema_version: str | None = None,
) -> ArtifactRef:
    return _put(
        store,
        staging,
        name=f"{name}.json",
        payload=_canonical_json(value.model_dump(mode="json")),
        media_type="application/json",
        schema_version=schema_version or value.schema_version,
    )


def _self_hashed(model: Any, digest_field: str, payload: dict[str, object]) -> Any:
    trusted = dict(payload)
    trusted[digest_field] = evidence.canonical_evidence_sha256(trusted, digest_field)
    return model.model_validate(trusted)


def _controlled_case(tmp_path: Path) -> _ControlledCase:
    store = LocalArtifactStore(tmp_path / "evidence")
    staging = tmp_path / "staging"
    source_path = staging / "source.png"
    source_path.parent.mkdir(parents=True)
    Image.new("RGB", (3, 2), (12, 34, 56)).save(source_path, format="PNG")
    source = store.put_file(
        source_path,
        name="source.png",
        media_type="image/png",
        schema_version=None,
    )
    media = ImageMediaIngestor(store, 4096, 4096).ingest(source)
    prompt = _put(
        store,
        staging,
        name="prompt.txt",
        payload=b"Put one can on the table.\n",
        media_type="text/plain",
        schema_version="harness.prompt_text.v1",
    )
    proposal = _self_hashed(
        evidence.SceneInterpretationProposalV1,
        "proposal_sha256",
        {
            "schema_version": "harness.scene_interpretation_proposal.v1",
            "route_id": "image2env",
            "source_media_sha256": source.sha256,
            "effective_prompt": prompt,
            "objects": [
                {
                    "object_id": "can",
                    "category": "can",
                    "role": "dynamic",
                    "dimensions_m": [0.07, 0.07, 0.12],
                }
            ],
            "relations": [{"relation": "on_table", "source": "can", "target": "table"}],
            "unknowns": [],
        },
    )
    proposal_ref = _publish_model(store, staging, "proposal", proposal)
    runtime = _self_hashed(
        evidence.GenesisRuntimeLockV1,
        "runtime_lock_sha256",
        {
            "schema_version": "harness.genesis_runtime_lock.v1",
            "backend": "genesis",
            "backend_version": "0.3.3",
            "runtime_implementation_sha256": "3" * 64,
            "loader_contract": "harness.genesis_rigid_loader.v1",
            "requested_profile": "genesis.rigid_scene@1",
            "dependencies": [{"name": "genesis", "version": "0.3.3", "sha256": "4" * 64}],
        },
    )
    runtime_ref = _publish_model(store, staging, "runtime-lock", runtime)
    subject = {
        "namespace": "robotwin",
        "asset_key": "071_can",
        "ledger_asset_id": "071_can",
        "model_id": 0,
        "backend": "genesis",
        "source_record_sha256": "5" * 64,
    }
    member_refs = (
        _put(
            store,
            staging,
            name="071_can/collision/base.glb",
            payload=b"collision-mesh",
            media_type="model/gltf-binary",
            schema_version=None,
        ),
        _put(
            store,
            staging,
            name="071_can/model_data0.json",
            payload=b'{"scale":[1,1,1]}\n',
            media_type="application/json",
            schema_version=None,
        ),
        _put(
            store,
            staging,
            name="071_can/visual/base.glb",
            payload=b"visual-mesh",
            media_type="model/gltf-binary",
            schema_version=None,
        ),
    )
    members = [
        {
            "logical_path": "071_can/collision/base.glb",
            "role": "collision",
            "artifact": member_refs[0],
        },
        {
            "logical_path": "071_can/model_data0.json",
            "role": "metadata",
            "artifact": member_refs[1],
        },
        {
            "logical_path": "071_can/visual/base.glb",
            "role": "visual",
            "artifact": member_refs[2],
        },
    ]
    loader_sha256 = evidence.asset_loader_closure_sha256(
        subject,
        "071_can/collision/base.glb",
        members,
    )
    representation = _self_hashed(
        evidence.AssetRepresentationV1,
        "representation_sha256",
        {
            "schema_version": "harness.asset_representation.v1",
            "subject": subject,
            "representation_kind": "rigid_mesh",
            "root_logical_path": "071_can/collision/base.glb",
            "loader_scale": [1.0, 1.0, 1.0],
            "loader_closure_sha256": loader_sha256,
            "members": members,
            "collision_member_paths": ["071_can/collision/base.glb"],
            "visual_member_paths": ["071_can/visual/base.glb"],
        },
    )
    representation_ref = _publish_model(store, staging, "representation", representation)
    qualified_subject = {
        "model_subject": subject,
        "representation_sha256": representation.representation_sha256,
        "loader_closure_sha256": loader_sha256,
    }
    qualification = _self_hashed(
        evidence.GenesisAssetQualificationV1,
        "qualification_sha256",
        {
            "schema_version": "harness.asset_qualification.v1",
            "subject": qualified_subject,
            "representation": representation_ref,
            "runtime_lock": runtime_ref,
            "requested_profile": "genesis.rigid_scene@1",
            "issuer_implementation_sha256": _QUALIFICATION_ISSUER_SHA256,
            "checks": [
                {
                    "code": code,
                    "status": "pass",
                    "evidence": sorted(
                        [runtime_ref, representation_ref],
                        key=lambda item: (item.sha256, item.bytes, item.media_type),
                    ),
                }
                for code in ("backend_load", "collision_probe")
            ],
            "status": "pass",
            "backend_load_executed": True,
            "collision_probe_executed": True,
            "qualified": True,
        },
    )
    qualification_ref = _publish_model(store, staging, "qualification", qualification)
    catalog = _self_hashed(
        evidence.PortableAssetCatalogV1,
        "catalog_sha256",
        {
            "schema_version": "harness.portable_asset_catalog.v1",
            "runtime_lock": runtime_ref,
            "entries": [
                {
                    "subject": subject,
                    "semantic_name": "can",
                    "aliases": ["tin_can"],
                    "representation": representation_ref,
                    "representation_sha256": representation.representation_sha256,
                    "qualification": qualification_ref,
                    "qualified_subject": qualified_subject,
                    "available": True,
                }
            ],
        },
    )
    catalog_ref = _publish_model(store, staging, "catalog", catalog)
    compile_input = Image2EnvCompileV2Input(
        schema_version="harness.image2env_compile_input.v2",
        source_media=media,
        interpretation_proposal=proposal_ref,
        requested_profile="genesis.rigid_scene@1",
        acquisition_policy="exact_only",
        asset_catalog=catalog_ref,
        seed=7,
        predecessor_package=None,
        repair_evidence_refs=(),
        model_execution_allowed=False,
        attempt={"schema_version": "harness.initial_compile_attempt.v1", "kind": "initial"},
    )
    return _ControlledCase(
        evidence_root=store.root,
        compile_input=compile_input,
        runtime_lock=runtime_ref,
        asset_qualification=qualification_ref,
        implementation_sha256=hashlib.sha256(
            Path(inspect.getfile(Image2EnvExactReuseCompileHandler)).read_bytes()
        ).hexdigest(),
    )


def _settings(tmp_path: Path, case: _ControlledCase):
    assert ControlledImage2EnvCompileApplicationSettings is not None
    return ControlledImage2EnvCompileApplicationSettings(
        state_root=tmp_path / "state",
        evidence_artifact_root=case.evidence_root,
    )


def _create(
    settings: Any,
    handler: Image2EnvExactReuseCompileHandler,
    *,
    clock: Callable[[], datetime] | None = None,
    run_ids: tuple[UUID, ...] = (
        UUID("32345678-1234-4234-9234-123456789abc"),
        UUID("42345678-1234-4234-9234-123456789abc"),
    ),
):
    assert create_controlled_image2env_compile_application is not None
    identities = iter(run_ids)
    return create_controlled_image2env_compile_application(
        settings,
        handler=handler,
        clock=clock or _SequenceClock(),
        run_id_factory=lambda: next(identities),
    )


def _cas_manifest(root: Path) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (
            str(path.relative_to(root)),
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _controlled_qualification(
    case: _ControlledCase,
    evaluation: Any,
    root: Path,
) -> SkillDescriptor:
    assert evaluation.mode == "qualification_candidate"
    assert evaluation.state.status is RunStatus.SUCCEEDED
    assert evaluation.invocation is not None
    output = Image2EnvCompileV2Output.model_validate(evaluation.state.output)
    assert output.simulator_executed is False
    assert output.publishable is False
    repo_root = Path(__file__).resolve().parents[3]
    report = QualificationReportV1(
        schema_version="harness.skill_qualification_report.v1",
        skill_ref="image2env.compile@1.0.0",
        status="pass",
        deterministic_case_id="controlled-image-exact-reuse-candidate",
        regression_command=(
            "pytest -q tests/self_improving/harness/test_image2env_compile_application.py"
        ),
        implementation_sha256=case.implementation_sha256,
        scene_gen_tree_sha256=_tree_sha256(repo_root / "scene_gen"),
        ledger_contract_tree_sha256=hashlib.sha256(
            (repo_root / "docs/integration-provenance/LEDGER.md").read_bytes()
        ).hexdigest(),
        checks=(
            QualificationCheckV1(
                name="candidate_execution",
                status="pass",
                evidence={
                    "controlled": True,
                    "mode": evaluation.mode,
                    "run_id": str(evaluation.state.run_id),
                    "invocation_digest": evaluation.invocation.invocation_digest,
                    "status": evaluation.state.status.value,
                    "simulator_executed": output.simulator_executed,
                    "publishable": output.publishable,
                },
            ),
            QualificationCheckV1(
                name="output_closure",
                status="pass",
                evidence={
                    "output_sha256": hashlib.sha256(
                        _canonical_json(evaluation.state.output)
                    ).hexdigest(),
                    "artifact_sha256s": sorted(
                        artifact.sha256 for artifact in evaluation.state.artifacts
                    ),
                    "artifact_count": len(evaluation.state.artifacts),
                    "compile_receipt_sha256": output.compile_receipt.sha256,
                },
            ),
        ),
    )
    store = LocalArtifactStore(case.evidence_root)
    report_ref = _publish_model(store, root, "controlled-report", report)
    qualification = SkillQualification(
        skill_ref="image2env.compile@1.0.0",
        status="pass",
        deterministic_case_id=report.deterministic_case_id,
        regression_command=report.regression_command,
        report_sha256=report_ref.sha256,
    )
    qualification_ref = _publish_model(
        store,
        root,
        "controlled-qualification",
        qualification,
        schema_version="harness.skill_qualification.v1",
    )
    return SkillDescriptor(
        skill_id="image2env.compile",
        version="1.0.0",
        mcp_tool_name="image2env_compile_v1_0_0",
        input_schema="harness.image2env_compile_input.v2",
        output_schema="harness.image2env_compile_output.v2",
        implementation_name="controlled.image2env_exact_reuse",
        implementation_version="1",
        implementation_sha256=case.implementation_sha256,
        deterministic=True,
        max_attempts=1,
        qualification_artifact=qualification_ref,
    )


def test_controlled_factory_evaluates_an_unregistered_deterministic_candidate(
    tmp_path: Path,
) -> None:
    _require_application_api()
    case = _controlled_case(tmp_path)
    application = _create(_settings(tmp_path, case), case.handler())

    assert application.durable_run_state is True
    assert application.skills == ()
    first = application.evaluate(case.compile_input)
    assert first.mode == "qualification_candidate"
    assert first.candidate.skill_id == "image2env.compile"
    assert first.candidate.version == "1.0.0"
    assert first.state.status is RunStatus.SUCCEEDED
    assert first.invocation is not None
    output = Image2EnvCompileV2Output.model_validate(first.state.output)
    assert output.simulator_executed is False
    assert output.publishable is False
    assert output.environment_package.simulator_ready is False
    assert output.environment_package.collision_qualified is False
    assert application.invocation(first.state.run_id) == first.invocation
    assert application.run_state(first.state.run_id) == first.state
    assert application.resolve_artifact(output.compile_receipt).is_file()
    after_first = _cas_manifest(case.evidence_root)

    second = application.evaluate(case.compile_input)

    assert second.mode == "qualification_candidate"
    assert second.state.run_id != first.state.run_id
    assert second.state.status is RunStatus.SUCCEEDED
    assert second.state.output == first.state.output
    assert second.state.artifacts == first.state.artifacts
    assert application.skills == ()
    assert _cas_manifest(case.evidence_root) == after_first


def test_real_controlled_candidate_qualification_enables_durable_restart(
    tmp_path: Path,
) -> None:
    _require_application_api()
    case = _controlled_case(tmp_path)
    settings = _settings(tmp_path, case)
    first_application = _create(settings, case.handler())
    evaluation = first_application.evaluate(case.compile_input)
    descriptor = _controlled_qualification(case, evaluation, tmp_path / "qualification")

    assert first_application.skills == ()
    first_application.bind_controlled_qualification(descriptor)
    assert first_application.skills == (descriptor,)
    with pytest.raises(RuntimeError, match="not a candidate evaluator"):
        first_application.evaluate(case.compile_input)
    first = first_application.invoke_typed(case.compile_input, run_id=_RUN_ID)
    recovered_live = first_application.invoke_typed(case.compile_input, run_id=_RUN_ID)
    assert first.status is RunStatus.SUCCEEDED
    assert recovered_live == first
    output = Image2EnvCompileV2Output.model_validate(first.output)
    assert output.simulator_executed is False
    assert output.publishable is False

    authority = SQLiteRunStore(first_application.authority_path)
    lifecycle = authority.read_controlled_lifecycle(_RUN_ID)
    assert lifecycle.intent is not None
    assert lifecycle.intent.implementation_sha256 == case.implementation_sha256
    assert lifecycle.intent.qualification_sha256 == descriptor.qualification_artifact.sha256
    assert lifecycle.invocation == first_application.invocation(_RUN_ID)
    assert lifecycle.terminal == first_application.run_state(_RUN_ID) == first
    before_cas = _cas_manifest(case.evidence_root)
    before_events = SQLiteEventJournal(first_application.authority_path).read(run_id=_RUN_ID)
    del first_application

    reopened = _create(settings, case.handler())
    reopened.bind_controlled_qualification(descriptor)
    recovered = reopened.invoke_typed(case.compile_input, run_id=_RUN_ID)

    assert reopened.skills == (descriptor,)
    assert recovered == first
    assert SQLiteRunStore(reopened.authority_path).read_controlled_lifecycle(_RUN_ID) == lifecycle
    assert SQLiteEventJournal(reopened.authority_path).read(run_id=_RUN_ID) == before_events
    assert _cas_manifest(case.evidence_root) == before_cas


def test_controlled_factory_rejects_wrong_exact_types_and_foreign_cas(tmp_path: Path) -> None:
    _require_application_api()
    case = _controlled_case(tmp_path)
    settings = _settings(tmp_path, case)
    assert ControlledImage2EnvCompileApplicationSettings is not None
    assert ControlledImage2EnvCompileApplicationRunIdentityError is not None

    with pytest.raises(TypeError, match="exact ControlledImage2EnvCompileApplicationSettings"):
        _create(object(), case.handler())
    with pytest.raises(TypeError, match="exact Image2EnvExactReuseCompileHandler"):
        _create(settings, object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="clock.*callable"):
        assert create_controlled_image2env_compile_application is not None
        create_controlled_image2env_compile_application(
            settings,
            handler=case.handler(),
            clock=None,  # type: ignore[arg-type]
            run_id_factory=lambda: _RUN_ID,
        )
    foreign_handler = case.handler(LocalArtifactStore(tmp_path / "foreign-cas"))
    with pytest.raises(ValueError, match="handler.*evidence.*CAS"):
        _create(settings, foreign_handler)

    application = _create(settings, case.handler())
    with pytest.raises(TypeError, match="exact Image2EnvCompileV2Input"):
        application.evaluate(object())
    with pytest.raises(ControlledImage2EnvCompileApplicationRunIdentityError, match="UUID4"):
        evaluation = application.evaluate(case.compile_input)
        descriptor = _controlled_qualification(case, evaluation, tmp_path / "qualification")
        application.bind_controlled_qualification(descriptor)
        application.invoke_typed(case.compile_input, run_id=uuid1())


def test_same_run_id_with_different_canonical_input_fails_before_handler_execution(
    tmp_path: Path,
) -> None:
    _require_application_api()
    case = _controlled_case(tmp_path)
    settings = _settings(tmp_path, case)
    application = _create(settings, case.handler())
    evaluation = application.evaluate(case.compile_input)
    descriptor = _controlled_qualification(case, evaluation, tmp_path / "qualification")
    application.bind_controlled_qualification(descriptor)
    first = application.invoke_typed(case.compile_input, run_id=_RUN_ID)
    assert first.status is RunStatus.SUCCEEDED
    lifecycle = SQLiteRunStore(application.authority_path).read_controlled_lifecycle(_RUN_ID)
    events = SQLiteEventJournal(application.authority_path).read(run_id=_RUN_ID)
    cas = _cas_manifest(case.evidence_root)
    changed = case.compile_input.model_copy(update={"seed": 8})

    assert ControlledImage2EnvCompileApplicationRunIdentityError is not None
    with pytest.raises(
        ControlledImage2EnvCompileApplicationRunIdentityError,
        match="another intent",
    ):
        application.invoke_typed(changed, run_id=_RUN_ID)

    assert (
        SQLiteRunStore(application.authority_path).read_controlled_lifecycle(_RUN_ID) == lifecycle
    )
    assert SQLiteEventJournal(application.authority_path).read(run_id=_RUN_ID) == events
    assert _cas_manifest(case.evidence_root) == cas
    authority = SQLiteRunStore(application.authority_path)
    completed_intent = authority.read_controlled_intent(_RUN_ID)
    assert completed_intent is not None
    pending_intent = completed_intent.model_copy(update={"run_id": _PENDING_RUN_ID})
    assert authority.reserve_controlled_intent(pending_intent)
    pending_lifecycle = authority.read_controlled_lifecycle(_PENDING_RUN_ID)
    assert pending_lifecycle.intent == pending_intent
    assert pending_lifecycle.invocation is None
    assert pending_lifecycle.terminal is None
    before_cas = _cas_manifest(case.evidence_root)
    before_events = SQLiteEventJournal(application.authority_path).read(run_id=_PENDING_RUN_ID)
    del application

    reopened = _create(settings, case.handler())
    reopened.bind_controlled_qualification(descriptor)
    assert ControlledImage2EnvCompileApplicationRunPendingError is not None
    with pytest.raises(
        ControlledImage2EnvCompileApplicationRunPendingError,
        match=str(_PENDING_RUN_ID),
    ):
        reopened.invoke_typed(case.compile_input, run_id=_PENDING_RUN_ID)

    assert (
        SQLiteRunStore(reopened.authority_path).read_controlled_lifecycle(_PENDING_RUN_ID)
        == pending_lifecycle
    )
    assert SQLiteEventJournal(reopened.authority_path).read(run_id=_PENDING_RUN_ID) == before_events
    assert _cas_manifest(case.evidence_root) == before_cas
