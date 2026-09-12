"""Development mode is an unreleased deployment record, never a fake Skill qualification."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.limited_image_preview_development import (
    DevelopmentArtifactStore,
    build_development_execution_record,
    capture_development_execution_context,
    capture_git_source_identity,
)
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


def _artifact(
    store: LocalArtifactStore,
    root: Path,
    name: str,
    schema: str | None,
    *,
    media_type: str = "application/json",
) -> ArtifactRef:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps({"name": name}, sort_keys=True, separators=(",", ":")).encode())
    return store.put_file(
        path,
        name=Path(name).name,
        media_type=media_type,
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
    review_refs = (
        _artifact(store, tmp_path, "preview.png", None, media_type="image/png"),
        _artifact(store, tmp_path, "preview.mp4", None, media_type="video/mp4"),
        _artifact(store, tmp_path, "environment.json", "harness.x2env_environment_package.v1"),
        _artifact(store, tmp_path, "validation.json", "harness.limited_scene_validation.v1"),
        _artifact(store, tmp_path, "genesis-scene.json", "harness.genesis_scene_spec.v1"),
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
        output_artifacts=(refs[2], *review_refs),
        log_artifacts=(refs[3],),
        physical_evidence=(refs[4], review_refs[3]),
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

    materialized = reopened.materialize(workflow_id, tmp_path / "development-review")
    assert materialized == tmp_path / "development-review"
    index_raw = (materialized / "index.json").read_bytes()
    index = json.loads(index_raw)
    assert (
        index_raw
        == json.dumps(
            index, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode()
        + b"\n"
    )
    assert index["kind"] == "limited_preview_development_materialization.v1"
    assert index["qualification_status"] == "development_unqualified"

    def path_for(ref):
        return (
            f"objects/sha256/{ref.sha256[:2]}/{ref.sha256}"
            + {
                "application/json": ".json",
                "image/png": ".png",
                "video/mp4": ".mp4",
            }[ref.media_type]
        )

    assert index["entrypoints"] == {
        "context": "development-context.json",
        "environment_packages": sorted([path_for(review_refs[2]), path_for(review_refs[4])]),
        "images": [path_for(review_refs[0])],
        "inputs": [f"objects/sha256/{refs[1].sha256[:2]}/{refs[1].sha256}.json"],
        "logs": [f"objects/sha256/{refs[3].sha256[:2]}/{refs[3].sha256}.json"],
        "outputs": sorted(path_for(ref) for ref in (refs[2], *review_refs)),
        "physical_evidence": sorted(path_for(ref) for ref in (refs[4], review_refs[3])),
        "record": "development-record.json",
        "validation_reports": [path_for(review_refs[3])],
        "videos": [path_for(review_refs[1])],
    }
    assert (materialized / "development-record.json").read_bytes() == store.resolve(
        committed
    ).path.read_bytes()
    assert (materialized / "development-context.json").read_bytes() == store.resolve(
        capture.ref
    ).path.read_bytes()
    for member in index["members"]:
        payload = materialized / member["path"]
        assert payload.is_file() and not payload.is_symlink()
        assert (
            payload.read_bytes()
            == store.resolve(ArtifactRef.model_validate(member["refs"][0])).path.read_bytes()
        )
    scene_path = path_for(review_refs[4])
    scene_member = next(member for member in index["members"] if member["path"] == scene_path)
    assert scene_member["sha256"] == review_refs[4].sha256
    assert scene_member["bytes"] == review_refs[4].bytes
    assert (materialized / scene_path).read_bytes() == store.resolve(
        review_refs[4]
    ).path.read_bytes()
    assert path_for(refs[2]) not in index["entrypoints"]["environment_packages"]
    assert not formal_library.exists()

    with pytest.raises(ValueError, match="absolute new directory"):
        reopened.materialize(workflow_id, materialized)

    changed = build_development_execution_record(
        workflow_run_id=workflow_id,
        principal_id="test/development",
        idempotency_key="different-key",
        workflow_head=refs[0],
        context=capture.context,
        context_ref=capture.ref,
        input_artifacts=(refs[1],),
        output_artifacts=(refs[2], *review_refs),
        log_artifacts=(refs[3],),
        physical_evidence=(refs[4], review_refs[3]),
        exact_skill_evidence=(refs[5],),
        created_at=datetime(2026, 9, 12, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="already bound"):
        reopened.commit(changed)

    (repository / "tracked.py").write_text("VALUE = 9\n")
    other_capture = capture_development_execution_context(
        artifact_store=store,
        repository_root=repository,
        settings_path=settings,
    )
    wrong_context = DevelopmentArtifactStore(
        artifact_store=store,
        root=tmp_path / "development-artifacts",
        context_ref=other_capture.ref,
    )
    with pytest.raises(ValueError, match="another execution context"):
        wrong_context.commit(record)
    with pytest.raises(ValueError, match="association is corrupt"):
        wrong_context.read(workflow_id)

    association = tmp_path / "development-artifacts" / f"{workflow_id}.json"
    original_association = association.read_bytes()
    association.write_text(json.dumps(json.loads(original_association), indent=2))
    with pytest.raises(ValueError, match="association is corrupt"):
        reopened.read(workflow_id)
    association.write_bytes(original_association)

    record_path = store.resolve(committed).path
    record_path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="association is corrupt"):
        reopened.read(workflow_id)


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
