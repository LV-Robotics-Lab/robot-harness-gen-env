"""The qualified compile@3 application enters Golden Registry without relabeling its evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_execution import create_golden_execution_catalog
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.limited_image_preview_policy import LimitedImagePreviewPolicy
from self_improving.harness.qualification import QualificationReportV1
from self_improving.harness.revised_compile_registry_projection import (
    RevisedCompileRegistryProjectionError,
    issue_revised_compile_registry_projection,
    load_revised_compile_registry_projection,
)
from self_improving.harness.revised_image_compile_application import (
    RevisedImageCompileApplication,
)
from self_improving.harness.schemas.common import ArtifactRef, RunStatus
from self_improving.harness.schemas.registry_snapshot import RegistrySnapshot
from self_improving.harness.schemas.revised_image_compile_application import (
    RevisedCompileInvocationV1,
    RevisedCompileQualificationReportV1,
)
from self_improving.harness.schemas.workflow import RunStartRequest
from self_improving.harness.trusted_state import WorldFactEvidence


def _canonical(value) -> bytes:
    return json.dumps(
        value.model_dump(mode="json") if hasattr(value, "model_dump") else value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _put(store: LocalArtifactStore, root: Path, value, name: str) -> ArtifactRef:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value) + b"\n")
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=value.schema_version,
    )


def _copy_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as reader,
        closing(sqlite3.connect(target)) as writer,
    ):
        reader.backup(writer)


@pytest.fixture(scope="module")
def qualified_j3(tmp_path_factory):
    """Relocate the saved qualified application; this fixture never launches Genesis."""

    configured = os.environ.get("J3_REGISTRY_PROJECTION_CONFIG")
    if not configured:
        pytest.skip("requires saved qualified compile@3 CAS and SQLite evidence")
    source_resume = Path(configured)
    saved = json.loads(source_resume.read_bytes())
    source_config = json.loads(Path(saved["input_config"]).read_bytes())
    root = tmp_path_factory.mktemp("j3-registry-projection")
    source_cas = Path(source_config["cas_root"])
    shutil.copytree(source_cas, root / "cas", copy_function=os.link)
    _copy_database(Path(source_config["asset_state_path"]), root / "asset.sqlite")
    _copy_database(Path(saved["root"]) / "application" / "runs.sqlite", root / "app/runs.sqlite")

    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler

    store = LocalArtifactStore(root / "cas")
    assets = GenesisAssetQualificationApplication(
        root / "asset.sqlite",
        store,
        ArtifactRef.model_validate(source_config["asset_manifest"]),
    )
    compiler = RevisedImage2EnvGenesisCompiler(
        store,
        Image2EnvGenesisCompiler(store, assets),
    )
    qualification = ArtifactRef.model_validate(saved["qualification"])
    application = RevisedImageCompileApplication(
        root / "app",
        compiler,
        qualification=qualification,
    )
    admission = application.admit()
    policy = LimitedImagePreviewPolicy(application, "image2env.compile@3.0.0", {})
    return root, store, application, admission, policy, saved


def test_projection_factory_rejects_caller_substitutes_before_publication(tmp_path):
    before = set(tmp_path.rglob("*"))
    with pytest.raises(TypeError, match="exact qualified revised application"):
        issue_revised_compile_registry_projection(
            application=object(),
            admission=object(),
            policy=object(),
        )
    with pytest.raises(TypeError, match="exact qualified revised application"):
        load_revised_compile_registry_projection(
            application=object(),
            policy=object(),
            admission_ref=object(),
            descriptor_ref=object(),
            qualification_ref=object(),
            qualification_report_ref=object(),
        )
    assert set(tmp_path.rglob("*")) == before


def test_formal_typed_j3_projects_into_registry_and_delegates_existing_terminal(qualified_j3):
    root, store, application, admission, policy, saved = qualified_j3
    projection = issue_revised_compile_registry_projection(
        application=application,
        admission=admission,
        policy=policy,
    )

    report = QualificationReportV1.model_validate_json(
        store.resolve(projection.qualification_report_ref).path.read_bytes()
    )
    typed_report = RevisedCompileQualificationReportV1.model_validate_json(
        store.resolve(admission.admission.qualification_report).path.read_bytes()
    )
    evidence = report.checks[0].evidence
    assert typed_report.schema_version == "harness.revised_compile_qualification_report.v1"
    assert admission.admission.qualification_report.schema_version == typed_report.schema_version
    assert evidence["typed_admission"] == admission.admission_ref.model_dump(mode="json")
    assert evidence["native_qualification_report"] == (
        admission.admission.qualification_report.model_dump(mode="json")
    )
    assert evidence["candidate_run_ids"] == [
        str(item.run_id) for item in admission.admission.candidate_intents
    ]
    assert projection.registry_entry.skill_ref == "image2env.compile@3.0.0"
    assert projection.registry_entry.mcp_tool_name == "image2env_compile_v3_0_0"
    assert projection.binding.application.child_authority is application.run_store
    assert projection.binding.child_authority.path.resolve() == application.run_store.path.resolve()
    assert projection.binding.policy is policy
    catalog = create_golden_execution_catalog(bindings=(projection.binding,))
    assert catalog.application_for("image2env.compile@3.0.0") is projection.binding.application

    registry = RegistrySnapshot(
        schema_version="harness.registry_snapshot.v1",
        qualified_skills=(projection.registry_entry,),
    )
    registry_ref = _put(store, root, registry, "j3-registry.json")
    fact = WorldFactEvidence(
        source_kind="user_input",
        key="request.objective",
        value="retain the current mouse pose",
        observed_at=datetime(2026, 9, 11, tzinfo=timezone.utc),
        valid_until=None,
    )
    fact_ref = _put(store, root, fact, "j3-initial-fact.json")
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(root / "workflow.sqlite"),
        execution_catalog=catalog,
        clock=lambda: datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    started = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="tests/j3-registry-projection",
            idempotency_key="j3-registry-start",
            workspace="ephemeral",
            requested_profile="genesis.rigid_scene@1",
            initial_fact_evidence=(fact_ref,),
            registry_snapshot=registry_ref,
        )
    )
    assert started.registry_snapshot == registry_ref

    registered_run = UUID(saved["registered_run"])
    envelope = RevisedCompileInvocationV1.model_validate(saved["registered_request"])
    before = application.run_store.read_controlled_lifecycle(registered_run)
    returned = projection.binding.application.invoke(envelope, run_id=registered_run)
    assert returned.status is RunStatus.SUCCEEDED
    assert returned == before.terminal
    assert application.run_store.read_controlled_lifecycle(registered_run) == before

    restarted = RevisedImageCompileApplication(
        root / "app",
        application.compiler,
        qualification=ArtifactRef.model_validate(saved["qualification"]),
    )
    restarted_policy = LimitedImagePreviewPolicy(
        restarted,
        "image2env.compile@3.0.0",
        {},
    )
    loaded = load_revised_compile_registry_projection(
        application=restarted,
        policy=restarted_policy,
        admission_ref=projection.admission_ref,
        descriptor_ref=projection.descriptor_ref,
        qualification_ref=projection.qualification_ref,
        qualification_report_ref=projection.qualification_report_ref,
    )
    assert loaded.registry_entry == projection.registry_entry
    assert loaded.binding.application.invoke(envelope, run_id=registered_run) == returned


def test_formal_projection_fails_closed_on_cas_candidate_and_policy_drift(qualified_j3):
    root, store, application, admission, policy, _ = qualified_j3
    projection = issue_revised_compile_registry_projection(
        application=application,
        admission=admission,
        policy=policy,
    )
    foreign_application = RevisedImageCompileApplication(root / "foreign", application.compiler)
    foreign_policy = LimitedImagePreviewPolicy(
        foreign_application,
        "image2env.compile@3.0.0",
        {},
    )
    with pytest.raises(TypeError, match="same exact application"):
        load_revised_compile_registry_projection(
            application=application,
            policy=foreign_policy,
            admission_ref=projection.admission_ref,
            descriptor_ref=projection.descriptor_ref,
            qualification_ref=projection.qualification_ref,
            qualification_report_ref=projection.qualification_report_ref,
        )
    with pytest.raises(RevisedCompileRegistryProjectionError):
        load_revised_compile_registry_projection(
            application=application,
            policy=policy,
            admission_ref=projection.admission_ref,
            descriptor_ref=projection.descriptor_ref,
            qualification_ref=projection.qualification_ref,
            qualification_report_ref=admission.admission.qualification_report,
        )

    source = projection.implementation_sources[0]
    source_path = store.resolve(source).path
    source_payload = source_path.read_bytes()
    source_path.unlink()
    try:
        with pytest.raises(RevisedCompileRegistryProjectionError, match="source"):
            projection.binding.application.skills
    finally:
        source_path.write_bytes(source_payload)
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source.sha256

    candidate = admission.admission.candidate_intents[0].run_id
    backup = root / "candidate-authority-backup.sqlite"
    _copy_database(application.run_store.path, backup)
    with closing(sqlite3.connect(application.run_store.path)) as connection, connection:
        connection.execute("DELETE FROM harness_run_states WHERE run_id=?", (str(candidate),))
    try:
        with pytest.raises(RevisedCompileRegistryProjectionError, match="candidate"):
            projection.binding.application.skills
    finally:
        application.run_store.path.unlink()
        backup.replace(application.run_store.path)
