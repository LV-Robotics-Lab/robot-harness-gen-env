"""Development mode is an unreleased deployment record, never a fake Skill qualification."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from self_improving.harness.limited_image_preview_development import (
    DevelopmentArtifactStore,
    build_development_execution_record,
    capture_development_execution_context,
    capture_git_source_identity,
)

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.limited_image_preview_service import LimitedImagePreviewService
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.trusted_state import TrustedWorldState
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401
from tests.self_improving.harness.test_limited_image_preview_inputs import inputs  # noqa: F401


def _git_repository(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Preview Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "preview@example.invalid"],
        check=True,
    )
    (root / "tracked.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(root), "add", "tracked.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    return root


def _artifact(store: LocalArtifactStore, root: Path, name: str, schema: str | None) -> ArtifactRef:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps({"name": name}, sort_keys=True, separators=(",", ":")).encode())
    return store.put_file(
        path,
        name=Path(name).name,
        media_type="application/json",
        schema_version=schema,
    )


def test_git_identity_binds_clean_dirty_tracked_and_untracked_bytes(tmp_path):
    repository = _git_repository(tmp_path / "repository")

    clean = capture_git_source_identity(repository)
    assert clean.dirty is False
    assert (
        clean.tracked_diff_sha256
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert clean.untracked_manifest_sha256 == clean.tracked_diff_sha256

    (repository / "tracked.py").write_text("VALUE = 2\n")
    (repository / "untracked.py").write_text("NEW = 1\n")
    dirty = capture_git_source_identity(repository)
    assert dirty.commit == clean.commit
    assert dirty.dirty is True
    assert dirty.tracked_diff_sha256 != clean.tracked_diff_sha256
    assert dirty.untracked_manifest_sha256 != clean.untracked_manifest_sha256
    assert dirty.tree_state_sha256 != clean.tree_state_sha256

    (repository / "untracked.py").write_text("NEW = 2\n")
    assert capture_git_source_identity(repository).tree_state_sha256 != dirty.tree_state_sha256
    with pytest.raises(ValueError, match="repository root"):
        capture_git_source_identity(repository / "tracked.py")


def test_development_record_is_recoverable_and_never_writes_formal_library(tmp_path):
    repository = _git_repository(tmp_path / "repository")
    settings = tmp_path / "service-settings.json"
    settings.write_text('{"execution_mode":"development"}\n')
    store = LocalArtifactStore(tmp_path / "cas")
    capture = capture_development_execution_context(
        artifact_store=store,
        repository_root=repository,
        settings_path=settings,
    )
    refs = tuple(
        _artifact(store, tmp_path, name, schema)
        for name, schema in (
            ("workflow-head.json", "harness.workflow_operation_receipt.v2"),
            ("input.json", None),
            ("output.json", None),
            ("codex-log.json", None),
            ("physics.json", "harness.genesis_scene_report.v1"),
            ("qualified.json", "harness.qualified_skill_execution.v2"),
        )
    )
    workflow_id = UUID("10000000-0000-4000-8000-000000000001")
    record = build_development_execution_record(
        workflow_run_id=workflow_id,
        principal_id="test/development",
        idempotency_key="development-result-1",
        workflow_head=refs[0],
        context=capture.context,
        context_ref=capture.ref,
        input_artifacts=(refs[1],),
        output_artifacts=(refs[2],),
        log_artifacts=(refs[3],),
        physical_evidence=(refs[4],),
        exact_skill_evidence=(refs[5],),
        created_at=datetime(2026, 9, 12, tzinfo=UTC),
    )
    formal_library = tmp_path / "formal-library"
    artifacts = DevelopmentArtifactStore(
        artifact_store=store,
        root=tmp_path / "development-artifacts",
        context_ref=capture.ref,
    )

    committed = artifacts.commit(record)
    reopened = DevelopmentArtifactStore(
        artifact_store=store,
        root=tmp_path / "development-artifacts",
        context_ref=capture.ref,
    )
    assert reopened.read(workflow_id) == (committed, record)
    assert reopened.commit(record) == committed
    assert record.qualification_status == "development_unqualified"
    assert record.exact_skills_qualified is True
    assert record.changed_exact_skill_closure_allowed is False
    assert record.formal_library_written is False
    assert [check.status for check in record.checks[-3:]] == ["omitted"] * 3
    assert not formal_library.exists()

    changed = build_development_execution_record(
        workflow_run_id=workflow_id,
        principal_id="test/development",
        idempotency_key="different-key",
        workflow_head=refs[0],
        context=capture.context,
        context_ref=capture.ref,
        input_artifacts=(refs[1],),
        output_artifacts=(refs[2],),
        log_artifacts=(refs[3],),
        physical_evidence=(refs[4],),
        exact_skill_evidence=(refs[5],),
        created_at=datetime(2026, 9, 12, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="already bound"):
        reopened.commit(changed)


def test_development_workflow_mode_and_source_identity_survive_restart(inputs, tmp_path):  # noqa: F811
    from tests.self_improving.harness.test_golden_run import _qualified_registry

    repository = _git_repository(tmp_path / "repository")
    settings = tmp_path / "service-settings.json"
    settings.write_text('{"execution_mode":"development"}\n')
    capture = capture_development_execution_context(
        artifact_store=inputs.store,
        repository_root=repository,
        settings_path=settings,
    )
    journal_path = tmp_path / "golden.sqlite"
    development_root = tmp_path / "development-artifacts"

    def service(context):
        return LimitedImagePreviewService(
            artifact_store=inputs.store,
            workflow_store=SQLiteGoldenRunStore(journal_path),
            input_builder=inputs.builder,
            registry_snapshot=_qualified_registry(inputs.store, tmp_path),
            principal_id="test/development-mode",
            execution_mode="development",
            development_context=context,
            development_artifacts=DevelopmentArtifactStore(
                artifact_store=inputs.store,
                root=development_root,
                context_ref=context.ref,
            ),
        )

    initial = service(capture)
    started = initial.start(
        raw_image_ref=inputs.raw,
        user_text="mouse on table",
        idempotency_key="development-workflow",
    )
    assert started["execution_mode"] == "development"
    assert started["qualification_status"] == "development_unqualified"
    assert started["snapshot"]["workspace"] == "ephemeral"
    state = TrustedWorldState.model_validate_json(
        inputs.store.resolve(
            ArtifactRef.model_validate(started["snapshot"]["state_ref"])
        ).path.read_bytes()
    )
    context_facts = [fact for fact in state.facts if fact.key == "request.development_context"]
    assert len(context_facts) == 1
    assert ArtifactRef.model_validate(context_facts[0].value) == capture.ref

    assert (
        service(capture).status(workflow_id=started["workflow_id"])["snapshot"]
        == started["snapshot"]
    )
    (repository / "tracked.py").write_text("VALUE = 3\n")
    changed = capture_development_execution_context(
        artifact_store=inputs.store,
        repository_root=repository,
        settings_path=settings,
    )
    with pytest.raises(ValueError, match="development context"):
        service(changed).status(workflow_id=started["workflow_id"])

    release = LimitedImagePreviewService(
        artifact_store=inputs.store,
        workflow_store=SQLiteGoldenRunStore(journal_path),
        input_builder=inputs.builder,
        registry_snapshot=_qualified_registry(inputs.store, tmp_path),
        principal_id="test/development-mode",
    )
    with pytest.raises(ValueError, match="execution mode"):
        release.status(workflow_id=started["workflow_id"])
