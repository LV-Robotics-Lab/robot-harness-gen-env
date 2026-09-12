"""Shared runtime skills must not grow separate authorities or accept caller runners."""

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from self_improving.harness.limited_image_preview_application import LimitedImagePreviewApplication

_SAVED_CBE5B3F_TTL_BOUNDARY = {
    "returned_ttl_seconds": 236.799554,
    "duplicate_qualification_seconds": 196.468709,
    "current_validation_seconds": 55.920832,
}


@dataclass(frozen=True)
class _SavedValidationHandler:
    output: object

    def __call__(self, value, context):
        from self_improving.harness.registry import HandlerResult

        return HandlerResult(self.output)


def _stored_reference(store, root, schema_version, marker):
    path = root / f"evidence-{marker}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"marker": marker}))
    return store.put_file(
        path,
        name=path.name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _preloaded_validation_application(
    tmp_path,
    monkeypatch,
    *,
    artifact_store=None,
    qualification_ref=None,
):
    from self_improving.harness import limited_image_preview_application as application
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.qualification import QualificationCheckV1, QualificationReportV1
    from self_improving.harness.registry import SkillRegistry, StaticDependencyResolver
    from self_improving.harness.run_store import SQLiteRunStore
    from self_improving.harness.schemas.common import DependencyRef, SkillQualification
    from self_improving.harness.schemas.limited_image_preview_execution import (
        LimitedSceneValidateInputV1,
        LimitedSceneValidationV1,
        PreviewLayout,
    )

    store = artifact_store or LocalArtifactStore(tmp_path / "cas")
    shared = object.__new__(LimitedImagePreviewApplication)
    shared.artifact_store = store
    shared._source_identity = shared.implementation_sha256
    shared._qualifications = {}
    shared._bound_qualifications = {}
    dependency = DependencyRef(
        name="limited_image_preview", version="1", sha256=shared._source_identity
    )
    shared._resolver = StaticDependencyResolver({application.VALIDATE: (dependency,)})
    execution_receipt = _stored_reference(
        store, tmp_path, "harness.genesis_scene_execution_receipt.v3", "1"
    )
    parameters = LimitedSceneValidateInputV1(
        schema_version="harness.limited_scene_validate_input.v1",
        execution_receipt=execution_receipt,
        expected_layout=None,
    )
    output = LimitedSceneValidationV1(
        schema_version="harness.limited_scene_validation.v1",
        execution_receipt=execution_receipt,
        scene=_stored_reference(store, tmp_path, "harness.genesis_scene_spec.v1", "2"),
        physical_report=_stored_reference(store, tmp_path, "harness.genesis_scene_report.v1", "3"),
        expected_layout=None,
        actual_layout=PreviewLayout(x_m=0.0, y_m=0.0, yaw_rad=0.0),
        position_tolerance_m=0.001,
        yaw_tolerance_rad=0.001,
        semantic_status="pass",
        status="passed",
        physical_scope="dual_dt_trace_contact_settling_and_media",
    )
    handler = _SavedValidationHandler(output)
    shared._handlers = {application.VALIDATE: handler}
    database = tmp_path / "runs.sqlite"
    shared.run_store = SQLiteRunStore(database)
    shared._registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=shared._resolver,
        event_sink=SQLiteEventJournal(database),
        clock=lambda: datetime(2026, 9, 12, tzinfo=timezone.utc),
        run_id_factory=uuid4,
        run_store=shared.run_store,
        evidence_invariant_policy=shared,
    )
    terminal_reads = []
    shared._terminal = lambda *args, **kwargs: terminal_reads.append((args, kwargs))
    if qualification_ref is None:
        replay_candidate = uuid4()
        validation_candidate = uuid4()
        check = QualificationCheckV1(
            name="fresh_replay_and_saved_validation",
            status="pass",
            evidence={
                "replay_candidate": str(replay_candidate),
                "validation_candidate": str(validation_candidate),
                "receipt": {"saved": True},
            },
        )
    else:
        qualification = SkillQualification.model_validate_json(
            store.resolve(qualification_ref).path.read_bytes()
        )
        saved_report = QualificationReportV1.model_validate_json(
            store.resolve_digest(qualification.report_sha256).read_bytes()
        )
        check = saved_report.checks[0]
    deep_checks = []

    def checks(replay_run_id, validation_run_id):
        deep_checks.append((replay_run_id, validation_run_id))
        return (check,)

    shared._checks = checks
    monkeypatch.setattr(application, "_source_tree_sha256", lambda *args, **kwargs: "a" * 64)
    if qualification_ref is None:
        report = QualificationReportV1(
            schema_version="harness.skill_qualification_report.v1",
            skill_ref=application.VALIDATE,
            status="pass",
            deterministic_case_id="one-fresh-modified-scene",
            regression_command="pytest -q -k limited_image_preview_application",
            implementation_sha256=shared.implementation_sha256,
            scene_gen_tree_sha256="a" * 64,
            ledger_contract_tree_sha256="a" * 64,
            checks=(check,),
        )
        report_ref = shared._put_registry_document(report, "preview-qualification-report.json")
        qualification_ref = shared._put_registry_document(
            SkillQualification(
                skill_ref=application.VALIDATE,
                status="pass",
                deterministic_case_id=report.deterministic_case_id,
                regression_command=report.regression_command,
                report_sha256=report_ref.sha256,
            ),
            "preview-qualification.json",
        )
    shared.bind(qualification_ref)
    return SimpleNamespace(
        application=shared,
        store=store,
        qualification=qualification_ref,
        parameters=parameters,
        deep_checks=deep_checks,
        terminal_reads=terminal_reads,
        handler=handler,
    )


def test_shared_application_rejects_unqualified_compiler_before_state_creation(tmp_path):
    root = tmp_path / "shared"
    with pytest.raises(TypeError, match="exact"):
        LimitedImagePreviewApplication(root, object(), object(), roots={})
    assert not root.exists()


def test_shared_qualification_producer_writes_golden_canonical_bytes(tmp_path, monkeypatch):
    from self_improving.harness import limited_image_preview_application as application
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.qualification import QualificationCheckV1, QualificationReportV1
    from self_improving.harness.schemas.common import SkillQualification

    store = LocalArtifactStore(tmp_path / "cas")
    shared = object.__new__(LimitedImagePreviewApplication)
    shared.artifact_store = store
    shared._qualifications = {}
    monkeypatch.setattr(application, "_source_tree_sha256", lambda *args, **kwargs: "a" * 64)
    shared._checks = lambda *args: (
        QualificationCheckV1(
            name="fresh_replay_and_saved_validation",
            status="pass",
            evidence={"saved": True},
        ),
    )

    def bind(ref):
        value = SkillQualification.model_validate_json(store.resolve(ref).path.read_bytes())
        shared._qualifications[value.skill_ref] = ref

    shared.bind = bind
    qualifications = shared.qualify(uuid4(), uuid4())
    manifest = SimpleNamespace(preview_id="canonical-preview")
    shared.library = SimpleNamespace(get=lambda preview_id: SimpleNamespace(manifest=manifest))
    shared._candidate = lambda run_id, skill_ref: (None, manifest)
    publication = shared.qualify_publish(uuid4())

    def canonical(value):
        return (
            json.dumps(
                value.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode()

    for ref in (*qualifications.values(), publication):
        payload = store.resolve(ref).path.read_bytes()
        qualification = SkillQualification.model_validate_json(payload)
        assert payload == canonical(qualification)
        report_payload = store.resolve_digest(qualification.report_sha256).read_bytes()
        report = QualificationReportV1.model_validate_json(report_payload)
        assert report_payload == canonical(report)

    shared._handlers = {
        "image2env.preview_publish@1.0.0": lambda value, context: None,
    }
    shared._resolver = object()
    registration = shared.verify_and_build(publication, artifact_resolver=store)
    assert registration.descriptor.qualification_artifact == publication
    noncanonical_path = tmp_path / "noncanonical-qualification.json"
    noncanonical_path.write_bytes(store.resolve(publication).path.read_bytes().rstrip(b"\n"))
    noncanonical = store.put_file(
        noncanonical_path,
        name=noncanonical_path.name,
        media_type="application/json",
        schema_version="harness.skill_qualification.v1",
    )
    with pytest.raises(ValueError, match="qualification identity"):
        shared.verify_and_build(noncanonical, artifact_resolver=store)


def test_saved_candidate_dependency_drift_is_rejected_before_requalification(tmp_path, monkeypatch):
    from self_improving.harness import limited_image_preview_library as library
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.schemas.common import DependencyRef, RunStatus
    from tests.self_improving.harness.test_limited_image_preview_library import _normal_manifest

    store = LocalArtifactStore(tmp_path / "cas")
    manifest, _ = _normal_manifest(store, tmp_path)
    shared = object.__new__(LimitedImagePreviewApplication)
    shared.artifact_store = store
    manifest_ref = shared.put(manifest, "candidate-manifest.json")
    invocation = SimpleNamespace(
        skill_id="image2env.preview_publish",
        skill_version="1.0.0",
        dependencies=(DependencyRef(name="limited_image_preview", version="1", sha256="b" * 64),),
        effective_parameters=manifest.model_dump(mode="json"),
    )
    terminal = SimpleNamespace(
        status=RunStatus.SUCCEEDED,
        events=(SimpleNamespace(stage="qualification_candidate.succeeded"),),
        output=manifest.model_dump(mode="json"),
        artifacts=(manifest_ref,),
    )
    shared.run_store = SimpleNamespace(
        read_controlled_lifecycle=lambda run_id: SimpleNamespace(
            invocation=invocation,
            terminal=terminal,
        )
    )
    shared._resolver = SimpleNamespace(
        resolve=lambda skill_ref, version: (
            DependencyRef(name="limited_image_preview", version="1", sha256="c" * 64),
        )
    )
    shared.compiler_application = SimpleNamespace(compiler=object())
    verified = []

    def admit(**values):
        verified.append(values["manifest"])
        return SimpleNamespace(manifest=manifest)

    monkeypatch.setattr(library, "admit_limited_image_preview", admit)
    with pytest.raises(ValueError, match="implementation"):
        shared._candidate(uuid4(), "image2env.preview_publish@1.0.0")
    assert verified == []


def test_replay_candidate_missing_evidence_is_durably_blocked(tmp_path):
    from uuid import uuid4

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution_v3 import GenesisSceneExecutionProducerV3
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.revised_image_compile_application import (
        RevisedImageCompileApplication,
    )
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler
    from self_improving.harness.schemas.genesis_scene_execution_v3 import (
        GenesisSceneCompileBindingV2,
    )
    from tests.self_improving.harness.test_genesis_scene_execution import reference

    store = LocalArtifactStore(tmp_path / "cas")
    compiler = RevisedImage2EnvGenesisCompiler(store, Image2EnvGenesisCompiler(store, object()))
    compile_app = RevisedImageCompileApplication(tmp_path / "compile", compiler)
    producer = GenesisSceneExecutionProducerV3(
        store, compiler, reference("harness.genesis_runtime_manifest.v1")
    )
    shared = LimitedImagePreviewApplication(tmp_path / "shared", compile_app, producer, roots={})
    request = GenesisSceneCompileBindingV2(
        schema_version="harness.genesis_scene_compile_binding.v2",
        compile_skill_ref="image2env.compile@3.0.0",
        compile_run_id=uuid4(),
        compile_request=reference("harness.image2env_genesis_compile_request.v2"),
        compile_receipt=reference("harness.image2env_genesis_compile_receipt.v2"),
        execution_request=reference("harness.genesis_scene_execution_request.v3"),
    )
    evaluated = shared.evaluate("image2env.replay@3.0.0", request)
    assert evaluated.state.status.value == "blocked"
    assert shared.run_state(evaluated.state.run_id) == evaluated.state
    assert shared.invocation(evaluated.state.run_id) == evaluated.invocation
    assert shared.skills == ()
    assert (tmp_path / "shared/runs.sqlite").is_file()
    with pytest.raises(ValueError, match="successful candidate"):
        shared.qualify(evaluated.state.run_id, evaluated.state.run_id)
    from tests.self_improving.harness.test_limited_image_preview_library import _normal_manifest

    manifest, _ = _normal_manifest(store, tmp_path)
    rejected = shared.evaluate("image2env.preview_publish@1.0.0", manifest)
    assert rejected.state.status.value == "failed"
    assert "configured verified library" in rejected.state.blocker.details["error"]
    assert shared.run_state(rejected.state.run_id) == rejected.state
    with pytest.raises(ValueError, match="successful candidate"):
        shared.qualify_publish(rejected.state.run_id)


def test_formal_semantic_mismatch_retains_diagnostic_in_shared_registry():
    import json
    import os
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.genesis_scene_execution_v3 import GenesisSceneExecutionProducerV3
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.revised_image_compile_application import (
        RevisedImageCompileApplication,
    )
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.limited_image_preview_execution import (
        LimitedSceneValidateInputV1,
    )

    path = os.environ.get("LIMITED_PREVIEW_VALIDATION_CONFIG")
    if not path:
        pytest.skip("requires immutable saved M1 evidence; no Genesis execution")
    config = json.loads(Path(path).read_bytes())
    root = Path(
        tempfile.mkdtemp(
            prefix="golden-preview-semantic-registry-", dir="/home/jingxiang/bingsheng"
        )
    )
    shutil.copytree(config["cas_root"], root / "cas", copy_function=os.link)
    source = sqlite3.connect("file:" + config["asset_state_path"] + "?mode=ro", uri=True)
    target = sqlite3.connect(root / "asset.sqlite")
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    store = LocalArtifactStore(root / "cas")
    asset = GenesisAssetQualificationApplication(
        root / "asset.sqlite", store, ArtifactRef.model_validate(config["asset_manifest"])
    )
    compiler = RevisedImage2EnvGenesisCompiler(store, Image2EnvGenesisCompiler(store, asset))
    revised = RevisedImageCompileApplication(root / "compile", compiler)
    producer = GenesisSceneExecutionProducerV3(
        store, compiler, ArtifactRef.model_validate(config["asset_manifest"])
    )
    shared = LimitedImagePreviewApplication(root / "shared", revised, producer, roots={})
    outcome = shared.evaluate(
        "image2env.validate@3.0.0",
        LimitedSceneValidateInputV1(
            schema_version="harness.limited_scene_validate_input.v1",
            execution_receipt=config["execution_receipt"],
            expected_layout={"x_m": 9.0, "y_m": 9.0, "yaw_rad": 0.0},
        ),
    )
    assert outcome.state.status.value == "blocked"
    assert outcome.state.blocker.code == "PREVIEW_LAYOUT_MISMATCH"
    assert outcome.state.blocker.retryable is False
    refs = [
        ref
        for ref in outcome.state.blocker.artifact_refs
        if ref.schema_version == "harness.limited_scene_validation.v1"
    ]
    assert len(refs) == 1
    assert refs[0] in outcome.state.blocker.artifact_refs
    result = json.loads(store.resolve(refs[0]).path.read_bytes())
    assert result["semantic_status"] == "mismatch"
    assert any(refs[0] in event.artifact_refs for event in outcome.state.events)
    assert shared.run_state(outcome.state.run_id) == outcome.state
    (root / "result.json").write_text(
        json.dumps(
            {
                "run_id": str(outcome.state.run_id),
                "diagnostic": refs[0].model_dump(mode="json"),
                "state": outcome.state.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    Path("/tmp/preview-semantic-registry-root.txt").write_text(str(root))


def test_preloaded_exact_qualification_is_not_deep_verified_again_during_invoke(
    tmp_path, monkeypatch
):
    from self_improving.harness.limited_image_preview_application import VALIDATE

    case = _preloaded_validation_application(tmp_path, monkeypatch)
    assert len(case.deep_checks) == 1

    state = case.application.invoke(VALIDATE, case.parameters, run_id=uuid4())

    assert state.status.value == "succeeded"
    assert len(case.deep_checks) == 1
    assert len(case.terminal_reads) == 1
    boundary = _SAVED_CBE5B3F_TTL_BOUNDARY
    post_capture_seconds = (
        boundary["current_validation_seconds"]
        + (len(case.deep_checks) - 1) * boundary["duplicate_qualification_seconds"]
    )
    assert (
        boundary["duplicate_qualification_seconds"] + boundary["current_validation_seconds"]
        > boundary["returned_ttl_seconds"]
    )
    assert post_capture_seconds < boundary["returned_ttl_seconds"]


@pytest.mark.parametrize("member", ["qualification", "report"])
def test_preloaded_qualification_rechecks_top_level_canonical_cas_before_invoke(
    tmp_path, monkeypatch, member
):
    from self_improving.harness.limited_image_preview_application import VALIDATE
    from self_improving.harness.schemas.common import SkillQualification

    case = _preloaded_validation_application(tmp_path, monkeypatch)
    qualification_path = case.store.resolve(case.qualification).path
    qualification = SkillQualification.model_validate_json(qualification_path.read_bytes())
    path = (
        qualification_path
        if member == "qualification"
        else case.store.resolve_digest(qualification.report_sha256)
    )
    payload = path.read_bytes()
    path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))

    with pytest.raises(Exception, match="qualification|SHA-256"):
        case.application.invoke(VALIDATE, case.parameters, run_id=uuid4())
    assert len(case.deep_checks) == 1
    assert case.terminal_reads == []


@pytest.mark.parametrize("member", ["qualification_ref", "descriptor", "handler", "source"])
def test_preloaded_qualification_identity_drift_fails_closed_before_handler(
    tmp_path, monkeypatch, member
):
    from self_improving.harness.limited_image_preview_application import VALIDATE

    case = _preloaded_validation_application(tmp_path, monkeypatch)
    shared = case.application
    if member == "qualification_ref":
        shared._qualifications[VALIDATE] = case.qualification.model_copy(
            update={"bytes": case.qualification.bytes + 1}
        )
    elif member == "descriptor":
        key = ("image2env.validate", "3.0.0")
        registered = shared._registry._registrations[key]
        shared._registry._registrations[key] = replace(
            registered,
            descriptor=registered.descriptor.model_copy(update={"implementation_version": "2"}),
        )
    elif member == "handler":
        shared._handlers[VALIDATE] = _SavedValidationHandler(case.handler.output)
    else:
        shared._source_identity = "f" * 64

    with pytest.raises(
        Exception, match="identity|immutable|implementation|byte count|verification"
    ):
        shared.invoke(VALIDATE, case.parameters, run_id=uuid4())
    assert case.terminal_reads == []


def test_new_application_process_must_deep_bind_the_same_qualification(tmp_path, monkeypatch):
    first = _preloaded_validation_application(tmp_path / "first", monkeypatch)
    second = _preloaded_validation_application(
        tmp_path / "second",
        monkeypatch,
        artifact_store=first.store,
        qualification_ref=first.qualification,
    )

    assert len(first.deep_checks) == 1
    assert len(second.deep_checks) == 1
