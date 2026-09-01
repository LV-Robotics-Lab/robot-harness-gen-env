from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

import demo.harness_compile as harness_compile_module
from demo.app import create_app
from demo.harness_compile import (
    WorkbenchCompile,
    WorkbenchCompileAuthorityError,
    WorkbenchCompileInputError,
    WorkbenchCompileRunNotFoundError,
    WorkbenchCompileSceneNotPreviewableError,
    WorkbenchCompileSceneTooLargeError,
    WorkbenchCompileStaticValidationNotPreviewableError,
    WorkbenchCompileStaticValidationTooLargeError,
    WorkbenchCompileUnavailableError,
)
from demo.harness_feed import HarnessEventFeedCorruptionError
from scene_gen.catalog import AssetCatalog
from scene_gen.schema import SceneSpec
from self_improving.harness.application import (
    CompileApplication,
    CompileApplicationSettings,
    create_compile_application,
)
from self_improving.harness.schemas import (
    ArtifactRef,
    RunStatus,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _workbench(
    tmp_path: Path,
    *,
    generate_missing_assets: bool = True,
) -> tuple[WorkbenchCompile, CompileApplication]:
    catalogs = tmp_path / "catalogs"
    assets = tmp_path / "external-assets"
    catalogs.mkdir(parents=True)
    assets.mkdir(parents=True)
    catalog_path = catalogs / "empty.json"
    catalog = AssetCatalog(
        robotwin_root=str(tmp_path / "RoboTwin"),
        objects_root=str(assets / "objects"),
        entries=(),
    )
    _write_json(catalog_path, catalog.canonical_dict())
    application = create_compile_application(
        CompileApplicationSettings(
            state_root=tmp_path / "state",
            external_catalog_roots=(catalogs,),
            allowed_asset_roots=(assets,),
            admission_date=date(2026, 9, 1),
            asset_library_root=tmp_path / "asset-library",
        )
    )
    return (
        WorkbenchCompile(
            application=application,
            asset_catalog_path=catalog_path,
            generate_missing_assets=generate_missing_assets,
        ),
        application,
    )


def test_submit_returns_only_a_durable_terminal_summary_and_its_committed_events(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)

    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )

    assert submission == {
        "schema_version": "harness.workbench_compile_submission.v1",
        "run_id": submission["run_id"],
        "skill_id": "text2env.compile",
        "skill_version": "1.0.0",
        "status": "succeeded",
        "attempt": 1,
        "max_attempts": 1,
        "terminal_event_id": submission["terminal_event_id"],
        "blocker": None,
    }
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    assert persisted.status.value == submission["status"]
    page = workbench.page(run_id=run_id)
    assert page["schema_version"] == "harness.workbench_event_page.v1"
    assert page["has_more"] is False
    assert page["last_event_id"] == submission["terminal_event_id"]
    assert [item["event"] for item in page["events"]] == [
        event.model_dump(mode="json") for event in persisted.events
    ]
    assert all(
        set(item) == {"event_id", "run_id", "skill_id", "skill_version", "event"}
        for item in page["events"]
    )
    assert "output" not in submission
    assert "artifacts" not in submission


def test_audit_reconstructs_dependencies_and_terminal_binding_from_one_authority(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    invocation = application.invocation(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    assert invocation is not None
    serialized_state = persisted.model_dump(mode="json")

    audit = workbench.audit(run_id=run_id)

    assert set(audit) == {"schema_version", "run", "invocation", "artifacts"}
    assert audit["schema_version"] == "harness.workbench_compile_audit.v2"
    assert audit["run"] == {
        "run_id": str(run_id),
        "skill_id": "text2env.compile",
        "skill_version": "1.0.0",
        "status": "succeeded",
        "attempt": 1,
        "max_attempts": 1,
        "started_at": serialized_state["started_at"],
        "ended_at": serialized_state["ended_at"],
        "event_count": len(persisted.events),
        "terminal_event_id": str(history.last_event_id),
        "blocker": None,
    }
    assert audit["invocation"] == {
        "status": "bound",
        "digest": invocation.invocation_digest,
        "dependencies": [
            dependency.model_dump(mode="json") for dependency in invocation.dependencies
        ],
    }
    serialized = json.dumps(audit, sort_keys=True)
    assert "effective_parameters" not in serialized
    assert "artifact://" not in serialized
    assert str(tmp_path) not in serialized


def test_audit_projects_reverified_artifact_metadata_without_locators(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    invocation = application.invocation(run_id)
    assert persisted is not None
    assert invocation is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)

    audit = workbench.audit(run_id=run_id)

    assert audit["schema_version"] == "harness.workbench_compile_audit.v2"
    assert len(audit["artifacts"]) == len(persisted.artifacts) == 11
    assert [
        {key: item[key] for key in ("name", "media_type", "schema_version", "sha256")}
        for item in audit["artifacts"]
    ] == [
        {
            "name": ref.name,
            "media_type": ref.media_type,
            "schema_version": ref.schema_version,
            "sha256": ref.sha256,
        }
        for ref in persisted.artifacts
    ]
    assert [item["bytes"] for item in audit["artifacts"]] == [
        str(ref.bytes) for ref in persisted.artifacts
    ]
    by_digest = {item["sha256"]: item for item in audit["artifacts"]}
    assert by_digest[invocation.effective_parameters["asset_catalog"]["sha256"]]["bindings"] == [
        {"direction": "input", "role": "asset_catalog"},
    ]
    assert by_digest[output.scene_spec.sha256]["bindings"] == [
        {"direction": "output", "role": "scene_spec"}
    ]
    assert (
        next(item for item in audit["artifacts"] if item["name"] == "asset_generation_report")[
            "bindings"
        ]
        == []
    )
    assert all(application.resolve_artifact(ref).is_file() for ref in persisted.artifacts)
    serialized = json.dumps(audit, sort_keys=True)
    assert "artifact://" not in serialized
    assert "uri" not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.parametrize("mutation", ["missing", "same_size_corrupt"])
def test_audit_rejects_a_missing_or_corrupt_application_cas_object(
    mutation: str,
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    target = next(artifact for artifact in persisted.artifacts if artifact.name == "request")
    cas_path = application.resolve_artifact(target)
    if mutation == "missing":
        cas_path.unlink()
    else:
        original = cas_path.read_bytes()
        replacement = bytes(byte ^ 0xFF for byte in original)
        assert len(replacement) == len(original)
        cas_path.write_bytes(replacement)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory") as captured:
        workbench.audit(run_id=run_id)

    assert str(tmp_path) not in str(captured.value)


def test_audit_allows_a_bound_internal_failure_without_inventing_input_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    registry = application._registry  # noqa: SLF001 - production failure fixture setup
    registration = next(iter(registry._registrations.values()))  # noqa: SLF001

    def fail_handler(_self, _parameters, _context):
        raise RuntimeError("fixture compile failure")

    monkeypatch.setattr(type(registration.handler), "__call__", fail_handler)

    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    assert persisted.status is RunStatus.FAILED
    assert persisted.invocation_digest is not None
    assert persisted.artifacts == ()

    audit = workbench.audit(run_id=run_id)

    assert audit["invocation"]["status"] == "bound"
    assert audit["artifacts"] == []


def test_audit_binds_a_committed_blocked_input_by_content_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path, generate_missing_assets=False)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    invocation = application.invocation(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    assert invocation is not None
    assert persisted.status is RunStatus.BLOCKED
    assert persisted.blocker is not None
    target = Text2EnvCompileInput.model_validate(invocation.effective_parameters).asset_catalog
    forged = target.model_copy(update={"name": "safe_but_not_the_typed_input_name"})

    def replace_target(ref):
        return forged if ref == target else ref

    forged_events = tuple(
        event.model_copy(
            update={"artifact_refs": tuple(replace_target(ref) for ref in event.artifact_refs)}
        )
        for event in persisted.events
    )
    forged_blocker = persisted.blocker.model_copy(
        update={
            "artifact_refs": tuple(replace_target(ref) for ref in persisted.blocker.artifact_refs)
        }
    )
    forged_state = persisted.model_copy(
        update={
            "artifacts": tuple(replace_target(ref) for ref in persisted.artifacts),
            "events": forged_events,
            "blocker": forged_blocker,
        }
    )
    forged_history = replace(
        history,
        events=tuple(
            replace(stored, envelope=replace(stored.envelope, event=event))
            for stored, event in zip(history.events, forged_events, strict=True)
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    audit = workbench.audit(run_id=run_id)

    item = next(artifact for artifact in audit["artifacts"] if artifact["sha256"] == forged.sha256)
    assert item["name"] == "safe_but_not_the_typed_input_name"
    assert item["bindings"] == [{"direction": "input", "role": "asset_catalog"}]


def test_audit_accepts_a_real_reuse_compile_with_distinct_input_and_output_aliases(
    tmp_path: Path,
) -> None:
    generating_workbench, application = _workbench(tmp_path)
    generated_submission = generating_workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    generated = application.run_state(UUID(generated_submission["run_id"]))
    assert generated is not None
    generated_output = Text2EnvCompileOutput.model_validate(generated.output)
    reusable_catalog = tmp_path / "catalogs" / "reusable.json"
    reusable_catalog.write_bytes(
        application.resolve_artifact(
            generated_output.environment_package.asset_catalog
        ).read_bytes()
    )
    reuse_workbench = WorkbenchCompile(
        application=application,
        asset_catalog_path=reusable_catalog,
        generate_missing_assets=False,
    )

    submission = reuse_workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    invocation = application.invocation(run_id)
    assert persisted is not None
    assert invocation is not None
    assert persisted.status is RunStatus.SUCCEEDED
    typed_input = Text2EnvCompileInput.model_validate(invocation.effective_parameters)
    state_alias = next(
        artifact
        for artifact in persisted.artifacts
        if artifact.sha256 == typed_input.asset_catalog.sha256
    )
    assert typed_input.asset_catalog.name == "input_asset_catalog"
    assert state_alias.name == "effective_asset_catalog"

    audit = reuse_workbench.audit(run_id=run_id)

    item = next(
        artifact
        for artifact in audit["artifacts"]
        if artifact["sha256"] == typed_input.asset_catalog.sha256
    )
    assert item["name"] == "effective_asset_catalog"
    assert item["bindings"] == [
        {"direction": "input", "role": "asset_catalog"},
        {"direction": "output", "role": "environment_package.asset_catalog"},
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "/tmp/private-staging"),
        ("media_type", "/tmp/private-staging"),
        ("schema_version", "/tmp/private-staging"),
        ("bytes", True),
        ("bytes", -1),
        ("sha256", "A" * 64),
        ("uri", "file:///tmp/private-staging"),
    ],
)
def test_audit_rejects_unsafe_artifact_metadata(
    field: str,
    value: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    target = next(ref for ref in persisted.artifacts if ref.name == "asset_generation_report")
    forged = target.model_copy(update={field: value})
    forged_events = tuple(
        event.model_copy(
            update={
                "artifact_refs": tuple(
                    forged if ref == target else ref for ref in event.artifact_refs
                )
            }
        )
        for event in persisted.events
    )
    forged_state = persisted.model_copy(
        update={
            "artifacts": tuple(forged if ref == target else ref for ref in persisted.artifacts),
            "events": forged_events,
        }
    )
    forged_history = replace(
        history,
        events=tuple(
            replace(stored, envelope=replace(stored.envelope, event=event))
            for stored, event in zip(history.events, forged_events, strict=True)
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory"):
        workbench.audit(run_id=run_id)


def test_audit_rejects_an_artifact_byte_count_that_javascript_cannot_represent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    target = next(ref for ref in persisted.artifacts if ref.name == "asset_generation_report")
    forged = target.model_copy(update={"bytes": 9_007_199_254_740_992})

    def replace_target(ref):
        return forged if ref == target else ref

    forged_events = tuple(
        event.model_copy(
            update={"artifact_refs": tuple(replace_target(ref) for ref in event.artifact_refs)}
        )
        for event in persisted.events
    )
    forged_state = persisted.model_copy(
        update={
            "artifacts": tuple(replace_target(ref) for ref in persisted.artifacts),
            "events": forged_events,
        }
    )
    forged_history = replace(
        history,
        events=tuple(
            replace(stored, envelope=replace(stored.envelope, event=event))
            for stored, event in zip(history.events, forged_events, strict=True)
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)
    monkeypatch.setattr(application, "resolve_artifact", lambda _artifact: tmp_path)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory"):
        workbench.audit(run_id=run_id)


def test_audit_rejects_an_unauthorized_alias_in_an_earlier_committed_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    original = persisted.artifacts[-1]
    injected = original.model_copy(update={"name": "unauthorized_event_alias"})
    forged_first = persisted.events[0].model_copy(
        update={"artifact_refs": (*persisted.events[0].artifact_refs, injected)}
    )
    forged_state = persisted.model_copy(update={"events": (forged_first, *persisted.events[1:])})
    forged_history = replace(
        history,
        events=(
            replace(
                history.events[0], envelope=replace(history.events[0].envelope, event=forged_first)
            ),
            *history.events[1:],
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory"):
        workbench.audit(run_id=run_id)


def test_audit_rejects_an_output_artifact_removed_from_the_terminal_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    removed_identity = (
        output.scene_spec.media_type,
        output.scene_spec.schema_version,
        output.scene_spec.sha256,
    )

    def keep(ref) -> bool:
        return (ref.media_type, ref.schema_version, ref.sha256) != removed_identity

    forged_events = tuple(
        event.model_copy(update={"artifact_refs": tuple(filter(keep, event.artifact_refs))})
        for event in persisted.events
    )
    forged_state = persisted.model_copy(
        update={
            "artifacts": tuple(filter(keep, persisted.artifacts)),
            "events": forged_events,
        }
    )
    forged_history = replace(
        history,
        events=tuple(
            replace(
                stored,
                envelope=replace(stored.envelope, event=event),
            )
            for stored, event in zip(history.events, forged_events, strict=True)
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory"):
        workbench.audit(run_id=run_id)


def test_audit_rejects_a_succeeded_run_missing_its_input_content_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    invocation = application.invocation(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    assert invocation is not None
    input_ref = Text2EnvCompileInput.model_validate(invocation.effective_parameters).asset_catalog
    input_identity = (input_ref.media_type, input_ref.schema_version, input_ref.sha256)
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    assert output.environment_package.asset_catalog.sha256 != input_ref.sha256

    def keep(ref) -> bool:
        return (ref.media_type, ref.schema_version, ref.sha256) != input_identity

    forged_events = tuple(
        event.model_copy(update={"artifact_refs": tuple(filter(keep, event.artifact_refs))})
        for event in persisted.events
    )
    forged_state = persisted.model_copy(
        update={
            "artifacts": tuple(filter(keep, persisted.artifacts)),
            "events": forged_events,
        }
    )
    forged_history = replace(
        history,
        events=tuple(
            replace(stored, envelope=replace(stored.envelope, event=event))
            for stored, event in zip(history.events, forged_events, strict=True)
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory"):
        workbench.audit(run_id=run_id)


def test_audit_rejects_safe_metadata_drift_on_a_typed_output_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    target = output.scene_spec
    forged = target.model_copy(update={"name": "safe_but_not_the_typed_output_name"})

    def replace_target(ref):
        return forged if ref == target else ref

    forged_events = tuple(
        event.model_copy(
            update={"artifact_refs": tuple(replace_target(ref) for ref in event.artifact_refs)}
        )
        for event in persisted.events
    )
    forged_state = persisted.model_copy(
        update={
            "artifacts": tuple(replace_target(ref) for ref in persisted.artifacts),
            "events": forged_events,
        }
    )
    forged_history = replace(
        history,
        events=tuple(
            replace(stored, envelope=replace(stored.envelope, event=event))
            for stored, event in zip(history.events, forged_events, strict=True)
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory"):
        workbench.audit(run_id=run_id)


def test_audit_rejects_a_terminal_event_artifact_order_that_differs_from_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    reordered = (persisted.artifacts[1], persisted.artifacts[0], *persisted.artifacts[2:])
    forged_terminal = persisted.events[-1].model_copy(update={"artifact_refs": reordered})
    forged_state = persisted.model_copy(
        update={
            "events": (*persisted.events[:-1], forged_terminal),
        }
    )
    stored_terminal = history.events[-1]
    forged_history = replace(
        history,
        events=(
            *history.events[:-1],
            replace(
                stored_terminal, envelope=replace(stored_terminal.envelope, event=forged_terminal)
            ),
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory"):
        workbench.audit(run_id=run_id)


def test_audit_rejects_duplicate_terminal_artifact_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    duplicate = persisted.artifacts[-1]
    duplicated = (*persisted.artifacts, duplicate)
    forged_terminal = persisted.events[-1].model_copy(update={"artifact_refs": duplicated})
    forged_state = persisted.model_copy(
        update={
            "artifacts": duplicated,
            "events": (*persisted.events[:-1], forged_terminal),
        }
    )
    stored_terminal = history.events[-1]
    forged_history = replace(
        history,
        events=(
            *history.events[:-1],
            replace(
                stored_terminal, envelope=replace(stored_terminal.envelope, event=forged_terminal)
            ),
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory"):
        workbench.audit(run_id=run_id)


def test_audit_rejects_an_unbounded_terminal_artifact_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    template = persisted.artifacts[-1]
    extras = tuple(
        template.model_copy(
            update={
                "name": f"supporting_artifact_{index}",
                "sha256": f"{index:064x}",
                "uri": f"artifact://sha256/{index:064x}",
            }
        )
        for index in range(1, 502 - len(persisted.artifacts))
    )
    oversized = (*persisted.artifacts, *extras)
    assert len(oversized) == 501
    forged_terminal = persisted.events[-1].model_copy(update={"artifact_refs": oversized})
    forged_state = persisted.model_copy(
        update={
            "artifacts": oversized,
            "events": (*persisted.events[:-1], forged_terminal),
        }
    )
    stored_terminal = history.events[-1]
    forged_history = replace(
        history,
        events=(
            *history.events[:-1],
            replace(
                stored_terminal, envelope=replace(stored_terminal.envelope, event=forged_terminal)
            ),
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory"):
        workbench.audit(run_id=run_id)


def test_audit_marks_a_real_preflight_terminal_without_inventing_an_invocation(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    preflight = application._registry.invoke(  # noqa: SLF001 - production fixture setup
        "text2env.compile",
        "1.0.0",
        {},
    )
    assert preflight.attempt == 0
    assert preflight.invocation_digest is None
    assert application.invocation(preflight.run_id) is None

    audit = workbench.audit(run_id=preflight.run_id)

    assert audit["run"]["status"] == "blocked"
    assert audit["run"]["attempt"] == 0
    assert audit["run"]["max_attempts"] == 0
    assert audit["run"]["blocker"] == {
        "code": "HARN_INPUT_INVALID",
        "retryable": False,
    }
    assert audit["invocation"] == {
        "status": "not_created_preflight",
        "digest": None,
        "dependencies": [],
    }
    assert audit["artifacts"] == []


def test_audit_rejects_an_artifact_coordinated_into_a_fixed_preflight_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    succeeded = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    succeeded_state = application.run_state(UUID(succeeded["run_id"]))
    assert succeeded_state is not None
    injected = succeeded_state.artifacts[0]
    preflight = application._registry.invoke(  # noqa: SLF001 - production fixture setup
        "text2env.compile",
        "1.0.0",
        {},
    )
    history = application.events(run_id=preflight.run_id, limit=500)
    assert preflight.blocker is not None
    forged_blocker = preflight.blocker.model_copy(update={"artifact_refs": (injected,)})
    forged_terminal = preflight.events[-1].model_copy(update={"artifact_refs": (injected,)})
    forged_state = preflight.model_copy(
        update={
            "artifacts": (injected,),
            "blocker": forged_blocker,
            "events": (*preflight.events[:-1], forged_terminal),
        }
    )
    stored_terminal = history.events[-1]
    forged_history = replace(
        history,
        events=(
            *history.events[:-1],
            replace(
                stored_terminal, envelope=replace(stored_terminal.envelope, event=forged_terminal)
            ),
        ),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="artifact inventory"):
        workbench.audit(run_id=preflight.run_id)


def test_audit_rejects_non_uuid_and_unknown_runs_before_projecting_history(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)

    with pytest.raises(WorkbenchCompileInputError, match="UUID"):
        workbench.audit(run_id="12345678-1234-4234-9234-123456789abc")  # type: ignore[arg-type]
    assert application.events().events == ()

    with pytest.raises(WorkbenchCompileRunNotFoundError, match="not found"):
        workbench.audit(run_id=UUID("12345678-1234-4234-9234-123456789abc"))


def test_audit_treats_a_partial_persisted_run_as_corrupt_not_missing(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    with closing(sqlite3.connect(application.journal_path)) as connection, connection:
        connection.execute("DELETE FROM harness_run_states WHERE run_id = ?", (str(run_id),))
    assert application.run_state(run_id) is None
    assert application.invocation(run_id) is not None
    assert application.events(run_id=run_id).events

    with pytest.raises(WorkbenchCompileAuthorityError, match="partial run authority"):
        workbench.audit(run_id=run_id)


@pytest.mark.parametrize(
    "mutation",
    ["preflight_attempts", "preflight_status", "execution_attempts"],
)
def test_audit_rejects_terminal_shapes_outside_the_fixed_compile_contract(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    if mutation.startswith("preflight"):
        state = application._registry.invoke(  # noqa: SLF001 - production fixture setup
            "text2env.compile",
            "1.0.0",
            {},
        )
        state = state.model_copy(
            update=(
                {"max_attempts": 1}
                if mutation == "preflight_attempts"
                else {"status": RunStatus.SUCCEEDED}
            )
        )
        monkeypatch.setattr(application, "run_state", lambda _run_id: state)
    else:
        submission = workbench.submit(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
        )
        run_id = UUID(submission["run_id"])
        state = application.run_state(run_id)
        invocation = application.invocation(run_id)
        assert state is not None
        assert invocation is not None
        drifted_state = state.model_copy(update={"max_attempts": 2})
        drifted_invocation = invocation.model_copy(update={"max_attempts": 2})
        monkeypatch.setattr(application, "run_state", lambda _run_id: drifted_state)
        monkeypatch.setattr(application, "invocation", lambda _run_id: drifted_invocation)
        state = drifted_state

    with pytest.raises(WorkbenchCompileAuthorityError, match="fixed compile"):
        workbench.audit(run_id=state.run_id)


@pytest.mark.parametrize("mutation", ["requested_run", "state", "invocation"])
def test_audit_rejects_authority_identity_or_second_read_drift(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    real_run_id = UUID(submission["run_id"])
    requested_run_id = real_run_id
    read_state = application.run_state
    read_invocation = application.invocation

    if mutation == "requested_run":
        requested_run_id = UUID("12345678-1234-4234-9234-123456789abc")

        def return_other_state(_run_id):
            return read_state(real_run_id)

        monkeypatch.setattr(application, "run_state", return_other_state)
    elif mutation == "state":
        state_reads = 0

        def return_drifted_state(run_id):
            nonlocal state_reads
            state_reads += 1
            state = read_state(run_id)
            assert state is not None
            return state if state_reads == 1 else state.model_copy(update={"attempt": 0})

        monkeypatch.setattr(application, "run_state", return_drifted_state)
    else:
        invocation_reads = 0

        def return_drifted_invocation(run_id):
            nonlocal invocation_reads
            invocation_reads += 1
            invocation = read_invocation(run_id)
            assert invocation is not None
            return (
                invocation
                if invocation_reads == 1
                else invocation.model_copy(update={"max_attempts": 2})
            )

        monkeypatch.setattr(application, "invocation", return_drifted_invocation)

    with pytest.raises(WorkbenchCompileAuthorityError, match="changed"):
        workbench.audit(run_id=requested_run_id)


def test_audit_sanitizes_an_unexpected_authority_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )

    def fail_history(**_kwargs):
        raise RuntimeError("secret database: /tmp/operator/harness.sqlite3")

    monkeypatch.setattr(application, "events", fail_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks") as captured:
        workbench.audit(run_id=UUID(submission["run_id"]))

    assert "/tmp" not in str(captured.value)


def test_audit_rejects_event_history_that_changes_between_authority_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    read_events = application.events
    reads = 0

    def drift_on_second_read(**kwargs):
        nonlocal reads
        reads += 1
        page = read_events(**kwargs)
        return page if reads == 1 else replace(page, has_more=True)

    monkeypatch.setattr(application, "events", drift_on_second_read)

    with pytest.raises(WorkbenchCompileAuthorityError, match="history changed"):
        workbench.audit(run_id=UUID(submission["run_id"]))


def test_audit_rejects_an_invocation_attached_to_a_preflight_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    invocation = application.invocation(UUID(submission["run_id"]))
    assert invocation is not None
    preflight = application._registry.invoke(  # noqa: SLF001 - production fixture setup
        "text2env.compile",
        "1.0.0",
        {},
    )
    monkeypatch.setattr(application, "invocation", lambda _run_id: invocation)

    with pytest.raises(WorkbenchCompileAuthorityError, match="preflight terminal state"):
        workbench.audit(run_id=preflight.run_id)


@pytest.mark.parametrize(
    "field",
    ["missing", "run_id", "skill_id", "skill_version", "invocation_digest", "max_attempts"],
)
def test_audit_rejects_a_consistently_invalid_bound_invocation(
    field: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    invocation = application.invocation(run_id)
    assert invocation is not None
    updates = {
        "run_id": UUID("12345678-1234-4234-9234-123456789abc"),
        "skill_id": "text2env.replay",
        "skill_version": "2.0.0",
        "invocation_digest": "0" * 64,
        "max_attempts": 2,
    }
    invalid = None if field == "missing" else invocation.model_copy(update={field: updates[field]})
    monkeypatch.setattr(application, "invocation", lambda _run_id: invalid)

    with pytest.raises(WorkbenchCompileAuthorityError, match="invalid Invocation binding"):
        workbench.audit(run_id=run_id)


@pytest.mark.parametrize("mutation", ["reordered", "duplicate", "locator"])
def test_audit_rejects_a_malformed_dependency_order_instead_of_repairing_it(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    invocation = application.invocation(run_id)
    assert invocation is not None
    assert len(invocation.dependencies) > 1
    dependencies = list(invocation.dependencies)
    if mutation == "reordered":
        dependencies.reverse()
    elif mutation == "duplicate":
        dependencies[1] = dependencies[1].model_copy(update={"name": dependencies[0].name})
    else:
        dependencies[0] = dependencies[0].model_copy(update={"name": "/tmp/private-state"})
    invalid = invocation.model_copy(update={"dependencies": tuple(dependencies)})
    monkeypatch.setattr(application, "invocation", lambda _run_id: invalid)

    with pytest.raises(WorkbenchCompileAuthorityError, match="dependency order"):
        workbench.audit(run_id=run_id)


@pytest.mark.parametrize("mutation", ["dependency_sha", "effective_parameters"])
def test_audit_recomputes_the_invocation_digest_before_exposing_dependencies(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    invocation = application.invocation(run_id)
    assert invocation is not None
    if mutation == "dependency_sha":
        dependencies = list(invocation.dependencies)
        dependencies[0] = dependencies[0].model_copy(update={"sha256": "0" * 64})
        forged = invocation.model_copy(update={"dependencies": tuple(dependencies)})
    else:
        forged = invocation.model_copy(
            update={"effective_parameters": {"request": "incomplete forged parameters"}}
        )
    assert forged.invocation_digest == invocation.invocation_digest
    monkeypatch.setattr(application, "invocation", lambda _run_id: forged)

    with pytest.raises(WorkbenchCompileAuthorityError, match="content identity"):
        workbench.audit(run_id=run_id)


def test_audit_rejects_attempt_zero_events_inside_a_bound_execution_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    first_event = persisted.events[0].model_copy(update={"attempt": 0})
    forged_state = persisted.model_copy(update={"events": (first_event, *persisted.events[1:])})
    first_stored = history.events[0]
    forged_envelope = replace(first_stored.envelope, event=first_event)
    forged_history = replace(
        history,
        events=(replace(first_stored, envelope=forged_envelope), *history.events[1:]),
    )
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)

    with pytest.raises(WorkbenchCompileAuthorityError, match="event attempts"):
        workbench.audit(run_id=run_id)


@pytest.mark.parametrize("mutation", ["has_more", "event", "cursor", "event_id"])
def test_audit_rejects_an_incomplete_or_drifted_event_projection(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    read_events = application.events

    def return_drifted_history(**kwargs):
        page = read_events(**kwargs)
        if mutation == "has_more":
            return replace(page, has_more=True)
        if mutation == "cursor":
            return replace(page, last_event_id=page.last_event_id + 1)
        if mutation == "event_id":
            assert len(page.events) > 1
            return replace(
                page,
                events=(
                    page.events[0],
                    replace(page.events[1], event_id=page.events[0].event_id),
                    *page.events[2:],
                ),
            )
        first = page.events[0]
        drifted_event = first.envelope.event.model_copy(update={"stage": "forged.stage"})
        drifted_envelope = replace(first.envelope, event=drifted_event)
        return replace(
            page,
            events=(replace(first, envelope=drifted_envelope), *page.events[1:]),
        )

    monkeypatch.setattr(application, "events", return_drifted_history)

    expected = {
        "has_more": "incomplete",
        "cursor": "incomplete",
        "event": "differs",
        "event_id": "strictly increasing",
    }[mutation]
    with pytest.raises(WorkbenchCompileAuthorityError, match=expected):
        workbench.audit(run_id=run_id)


@pytest.mark.parametrize(
    ("request_value", "seed"),
    [
        ("xy", 0),
        ("x" * 2001, 0),
        (3, 0),
        ("valid request", True),
        ("valid request", 1.0),
        ("valid request", "0"),
        ("valid request", -1),
        ("valid request", 2_147_483_648),
    ],
)
def test_submit_rejects_invalid_business_input_before_creating_a_run(
    request_value: object,
    seed: object,
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)

    with pytest.raises(WorkbenchCompileInputError):
        workbench.submit(request=request_value, seed=seed)

    assert application.events().events == ()


def test_submit_interface_does_not_expose_trust_or_storage_configuration(tmp_path: Path) -> None:
    workbench, application = _workbench(tmp_path)

    assert set(inspect.signature(workbench.submit).parameters) == {"request", "seed"}
    with pytest.raises(TypeError):
        workbench.submit(  # type: ignore[call-arg]
            request="Place a can on a plate.",
            seed=0,
            asset_catalog_path=tmp_path / "attacker.json",
        )
    assert application.events().events == ()


def test_constructor_requires_the_exact_application_and_fixed_operator_policy(
    tmp_path: Path,
) -> None:
    _, application = _workbench(tmp_path)

    with pytest.raises(TypeError, match="exact CompileApplication"):
        WorkbenchCompile(  # type: ignore[arg-type]
            application=object(),
            asset_catalog_path=tmp_path / "catalog.json",
            generate_missing_assets=True,
        )
    with pytest.raises(TypeError, match="Path"):
        WorkbenchCompile(  # type: ignore[arg-type]
            application=application,
            asset_catalog_path="catalog.json",
            generate_missing_assets=True,
        )
    with pytest.raises(TypeError, match="bool"):
        WorkbenchCompile(  # type: ignore[arg-type]
            application=application,
            asset_catalog_path=tmp_path / "catalog.json",
            generate_missing_assets=1,
        )


def test_submit_projects_missing_operator_catalog_as_unavailable(tmp_path: Path) -> None:
    workbench, application = _workbench(tmp_path)
    (tmp_path / "catalogs" / "empty.json").unlink()

    with pytest.raises(WorkbenchCompileUnavailableError, match="unavailable"):
        workbench.submit(request="Place a can on a plate.", seed=0)

    assert application.events().events == ()


def test_page_projects_compile_authority_drift_as_corrupt_history(tmp_path: Path) -> None:
    workbench, application = _workbench(tmp_path)
    asset_library_root = application.asset_library_root
    asset_library_root.rmdir()

    with pytest.raises(HarnessEventFeedCorruptionError, match="integrity checks"):
        workbench.page()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"after_event_id": True},
        {"limit": True},
        {"run_id": "12345678-1234-4234-9234-123456789abc"},
    ],
)
def test_page_rejects_noncanonical_query_types(
    kwargs: dict[str, object],
    tmp_path: Path,
) -> None:
    workbench, _ = _workbench(tmp_path)

    with pytest.raises(ValueError):
        workbench.page(**kwargs)  # type: ignore[arg-type]


def test_page_preserves_a_verified_feed_corruption_error(tmp_path: Path) -> None:
    workbench, application = _workbench(tmp_path)
    application.journal_path.write_bytes(b"not a sqlite database")

    with pytest.raises(HarnessEventFeedCorruptionError, match="integrity checks"):
        workbench.page()


@pytest.mark.parametrize(
    ("request_value", "seed"),
    [("abc", 0), ("x" * 2000, 2_147_483_647)],
)
def test_submit_accepts_exact_public_input_boundaries_and_persists_them(
    request_value: str,
    seed: int,
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path, generate_missing_assets=False)

    submission = workbench.submit(request=request_value, seed=seed)

    invocation = application.invocation(UUID(submission["run_id"]))
    assert invocation is not None
    assert invocation.effective_parameters["request"] == request_value
    assert invocation.effective_parameters["seed"] == seed
    assert invocation.effective_parameters["config"] == {"generate_missing_assets": False}


def test_submit_fails_closed_when_the_terminal_run_state_cannot_be_reloaded(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    with closing(sqlite3.connect(application.journal_path)) as connection, connection:
        connection.execute(
            """
            CREATE TRIGGER remove_new_terminal_state
            AFTER INSERT ON harness_run_states
            BEGIN
                DELETE FROM harness_run_states WHERE run_id = NEW.run_id;
            END
            """
        )

    with pytest.raises(WorkbenchCompileAuthorityError, match="missing"):
        workbench.submit(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
        )


def test_submit_fails_closed_when_committed_history_does_not_match_run_state(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    with closing(sqlite3.connect(application.journal_path)) as connection, connection:
        connection.execute(
            """
            CREATE TRIGGER remove_new_terminal_event
            AFTER INSERT ON harness_run_states
            BEGIN
                DELETE FROM run_events
                WHERE event_id = (
                    SELECT MAX(event_id) FROM run_events WHERE run_id = NEW.run_id
                );
            END
            """
        )

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.submit(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
        )


def test_submit_rejects_a_return_value_that_differs_from_durable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    compile_scene = application.compile

    def return_drifted_state(**kwargs):
        state = compile_scene(**kwargs)
        return state.model_copy(update={"attempt": 0})

    monkeypatch.setattr(application, "compile", return_drifted_state)

    with pytest.raises(WorkbenchCompileAuthorityError, match="differ"):
        workbench.submit(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
        )


def test_submit_rejects_a_non_compile_terminal_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    compile_scene = application.compile
    drifted = None

    def return_drifted_state(**kwargs):
        nonlocal drifted
        drifted = compile_scene(**kwargs).model_copy(update={"skill_id": "text2env.replay"})
        return drifted

    def read_same_drifted_state(_run_id):
        assert drifted is not None
        return drifted

    monkeypatch.setattr(application, "compile", return_drifted_state)
    monkeypatch.setattr(application, "run_state", read_same_drifted_state)

    with pytest.raises(WorkbenchCompileAuthorityError, match="identity"):
        workbench.submit(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
        )


@pytest.mark.parametrize("mutation", ["has_more", "event", "cursor"])
def test_submit_rejects_an_inconsistent_event_projection(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    read_events = application.events

    def return_drifted_history(**kwargs):
        page = read_events(**kwargs)
        if mutation == "has_more":
            return replace(page, has_more=True)
        if mutation == "cursor":
            return replace(page, last_event_id=page.last_event_id + 1)
        first = page.events[0]
        drifted_event = first.envelope.event.model_copy(update={"stage": "forged.stage"})
        drifted_envelope = replace(first.envelope, event=drifted_event)
        return replace(
            page,
            events=(replace(first, envelope=drifted_envelope), *page.events[1:]),
        )

    monkeypatch.setattr(application, "events", return_drifted_history)

    expected = "incomplete" if mutation in {"has_more", "cursor"} else "differs"
    with pytest.raises(WorkbenchCompileAuthorityError, match=expected):
        workbench.submit(
            request="Place a purple hexagonal pedestal on the table.",
            seed=77,
        )


def test_submit_projects_an_unexpected_application_exception_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)

    def fail_compile(**_kwargs):
        raise RuntimeError("internal path: /tmp/operator-secret")

    monkeypatch.setattr(application, "compile", fail_compile)

    with pytest.raises(WorkbenchCompileUnavailableError, match="unavailable") as captured:
        workbench.submit(request="Place a can on a plate.", seed=0)

    assert "/tmp" not in str(captured.value)


def test_submit_returns_a_sanitized_blocked_terminal_result(tmp_path: Path) -> None:
    workbench, application = _workbench(tmp_path, generate_missing_assets=False)

    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )

    assert submission["status"] == "blocked"
    assert submission["blocker"] == {
        "code": "T2E_ASSET_UNAVAILABLE",
        "retryable": False,
    }
    assert set(submission["blocker"]) == {"code", "retryable"}
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    page = workbench.page(run_id=run_id)
    assert page["last_event_id"] == submission["terminal_event_id"]
    assert page["events"][-1]["event"]["to_status"] == "blocked"


def test_http_submission_and_event_replay_share_one_real_compile_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path / "harness")
    demo_root = tmp_path / "demo"
    repo = demo_root / "repo"
    robotwin = demo_root / "RoboTwin"
    python = demo_root / "python"
    catalog = demo_root / "legacy-catalog.json"
    jobs = demo_root / "jobs"
    repo.mkdir(parents=True)
    robotwin.mkdir()
    python.write_text("", encoding="utf-8")
    catalog.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SCENE_DEMO_REPO_ROOT", str(repo))
    monkeypatch.setenv("ROBOTWIN_ROOT", str(robotwin))
    monkeypatch.setenv("ROBOTWIN_PYTHON", str(python))
    monkeypatch.setenv("SCENE_ASSET_CATALOG", str(catalog))
    monkeypatch.setenv("SCENE_DEMO_JOBS_ROOT", str(jobs))
    client = create_app({"TESTING": True, "HARNESS_WORKBENCH": workbench}).test_client()

    response = client.post(
        "/api/harness/compile",
        json={
            "request": "Place a purple hexagonal pedestal on the table.",
            "seed": 77,
        },
    )

    assert response.status_code == 200
    run_id = UUID(response.json["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    page = client.get(f"/api/harness/events?run_id={run_id}")
    assert page.status_code == 200
    assert [item["event"] for item in page.json["events"]] == [
        event.model_dump(mode="json") for event in persisted.events
    ]
    assert page.json["last_event_id"] == response.json["terminal_event_id"]
    audit = client.get(f"/api/harness/compile-runs/{run_id}/audit")
    assert audit.status_code == 200
    assert audit.json["run"]["terminal_event_id"] == response.json["terminal_event_id"]
    assert audit.json["run"]["event_count"] == len(persisted.events)
    assert audit.json["invocation"]["status"] == "bound"
    assert audit.json["invocation"]["dependencies"]
    assert "artifact://" not in audit.get_data(as_text=True)
    assert str(tmp_path) not in audit.get_data(as_text=True)


def test_scene_preview_projects_only_the_verified_typed_scene_structure(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    request_value = "Place a purple hexagonal pedestal on the table."
    submission = workbench.submit(request=request_value, seed=77)
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    invocation = application.invocation(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    assert invocation is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)

    preview = workbench.scene_preview(run_id=run_id)

    assert set(preview) == {"artifact", "run", "scene", "schema_version"}
    assert preview["schema_version"] == "harness.workbench_compile_scene_preview.v1"
    assert preview["run"] == {
        "run_id": str(run_id),
        "invocation_digest": invocation.invocation_digest,
        "event_count": len(persisted.events),
        "terminal_event_id": str(history.last_event_id),
    }
    assert preview["artifact"] == {
        "name": output.scene_spec.name,
        "media_type": "application/json",
        "schema_version": "robotwin.scene_spec.v1",
        "sha256": output.scene_spec.sha256,
        "bytes": str(output.scene_spec.bytes),
        "bindings": [{"direction": "output", "role": "scene_spec"}],
    }
    assert set(preview["scene"]) == {
        "frame",
        "language",
        "objects",
        "relations",
        "scene_id",
        "seed",
        "unit",
        "workspace",
    }
    assert preview["scene"]["seed"] == 77
    assert preview["scene"]["objects"]
    assert preview["scene"]["relations"]
    serialized = json.dumps(preview, sort_keys=True, ensure_ascii=False)
    assert request_value not in serialized
    assert "request" not in preview["scene"]
    assert "artifact://" not in serialized
    assert str(tmp_path) not in serialized


def test_static_validation_preview_projects_only_the_committed_summary(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    request_value = "Place a purple hexagonal pedestal on the table."
    submission = workbench.submit(request=request_value, seed=77)
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    invocation = application.invocation(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    assert invocation is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)

    preview = workbench.static_validation_preview(run_id=run_id)

    assert set(preview) == {"artifact", "run", "schema_version", "validation"}
    assert preview["schema_version"] == "harness.workbench_compile_static_validation_preview.v1"
    assert preview["run"] == {
        "run_id": str(run_id),
        "invocation_digest": invocation.invocation_digest,
        "event_count": len(persisted.events),
        "terminal_event_id": str(history.last_event_id),
    }
    assert preview["artifact"] == {
        "name": output.static_validation.name,
        "media_type": "application/json",
        "schema_version": "robotwin.scene_validation.v1",
        "sha256": output.static_validation.sha256,
        "bytes": str(output.static_validation.bytes),
        "bindings": [{"direction": "output", "role": "static_validation"}],
    }
    assert preview["validation"] == {
        "claim_scope": "committed_report_content_and_binding_only",
        "mode": "compile_static_without_runtime_evidence",
        "scene_id": "place_a_purple_hexagonal_pedestal_on_the_table_d38c3bf3d4",
        "resolved_scene_sha256": output.environment_package.resolved_scene_sha256,
        "status": "incomplete",
        "counts": {"checks": 7, "pass": 6, "fail": 0, "not_run": 1},
        "checks": [
            {"name": "workspace_bounds:hexagonal_pedestal_1", "status": "pass"},
            {
                "name": "table_support_height:hexagonal_pedestal_1",
                "status": "pass",
            },
            {"name": "real_asset_files:hexagonal_pedestal_1", "status": "pass"},
            {
                "name": "relation:on_table:hexagonal_pedestal_1",
                "status": "pass",
            },
            {"name": "resolved_only_roundtrip", "status": "pass"},
            {"name": "package_manifest", "status": "pass"},
            {"name": "runtime_evidence", "status": "not_run"},
        ],
    }
    serialized = json.dumps(preview, sort_keys=True, ensure_ascii=False)
    assert request_value not in serialized
    assert all(set(item) == {"name", "status"} for item in preview["validation"]["checks"])
    assert "artifact://" not in serialized
    assert str(tmp_path) not in serialized


def test_scene_preview_exposes_only_the_fixed_run_selector() -> None:
    assert tuple(inspect.signature(WorkbenchCompile.scene_preview).parameters) == (
        "self",
        "run_id",
    )


def test_scene_preview_does_not_use_the_unbounded_application_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    calls: list[ArtifactRef] = []

    def reject_unbounded_resolution(artifact: ArtifactRef) -> Path:
        calls.append(artifact)
        raise AssertionError("scene preview must use its bounded descriptor reader")

    monkeypatch.setattr(application, "resolve_artifact", reject_unbounded_resolution)

    preview = workbench.scene_preview(run_id=UUID(submission["run_id"]))

    assert preview["scene"]["seed"] == 77
    assert calls == []


def _install_scene_payload_authority(
    *,
    application: CompileApplication,
    run_id: UUID,
    payload: bytes,
    monkeypatch: pytest.MonkeyPatch,
    package_scene_digest: str | None = None,
    package_seed: int | None = None,
    ref_updates: dict[str, object] | None = None,
) -> Path:
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    original = output.scene_spec
    digest = hashlib.sha256(payload).hexdigest()
    forged = original.model_copy(
        update={
            "sha256": digest,
            "uri": f"artifact://sha256/{digest}",
            "bytes": len(payload),
            **(ref_updates or {}),
        }
    )
    package_updates = {}
    if package_scene_digest is not None:
        package_updates["scene_spec_sha256"] = package_scene_digest
    if package_seed is not None:
        package_updates["seed"] = package_seed
    package = output.environment_package.model_copy(update=package_updates)
    forged_output = output.model_copy(
        update={
            "scene_spec": forged,
            "environment_package": package,
        }
    )

    def replace_ref(ref):
        return forged if ref == original else ref

    forged_events = tuple(
        event.model_copy(
            update={"artifact_refs": tuple(replace_ref(ref) for ref in event.artifact_refs)}
        )
        for event in persisted.events
    )
    forged_state = persisted.model_copy(
        update={
            "output": forged_output.model_dump(mode="json"),
            "artifacts": tuple(replace_ref(ref) for ref in persisted.artifacts),
            "events": forged_events,
        }
    )
    forged_history = replace(
        history,
        events=tuple(
            replace(stored, envelope=replace(stored.envelope, event=event))
            for stored, event in zip(history.events, forged_events, strict=True)
        ),
    )
    target = application.artifact_root / "sha256" / digest[:2] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)
    return target


def _install_static_validation_payload_authority(
    *,
    application: CompileApplication,
    run_id: UUID,
    payload: bytes,
    monkeypatch: pytest.MonkeyPatch,
    ref_updates: dict[str, object] | None = None,
) -> Path:
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    original = output.static_validation
    digest = hashlib.sha256(payload).hexdigest()
    forged = original.model_copy(
        update={
            "sha256": digest,
            "uri": f"artifact://sha256/{digest}",
            "bytes": len(payload),
            **(ref_updates or {}),
        }
    )
    forged_output = output.model_copy(update={"static_validation": forged})

    def replace_ref(ref):
        return forged if ref == original else ref

    forged_events = tuple(
        event.model_copy(
            update={"artifact_refs": tuple(replace_ref(ref) for ref in event.artifact_refs)}
        )
        for event in persisted.events
    )
    forged_state = persisted.model_copy(
        update={
            "output": forged_output.model_dump(mode="json"),
            "artifacts": tuple(replace_ref(ref) for ref in persisted.artifacts),
            "events": forged_events,
        }
    )
    forged_history = replace(
        history,
        events=tuple(
            replace(stored, envelope=replace(stored.envelope, event=event))
            for stored, event in zip(history.events, forged_events, strict=True)
        ),
    )
    target = application.artifact_root / "sha256" / digest[:2] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)
    return target


def _install_resolved_scene_payload_authority(
    *,
    application: CompileApplication,
    run_id: UUID,
    payload: bytes,
    monkeypatch: pytest.MonkeyPatch,
    ref_updates: dict[str, object] | None = None,
) -> Path:
    persisted = application.run_state(run_id)
    history = application.events(run_id=run_id, limit=500)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    original = output.resolved_scene
    digest = hashlib.sha256(payload).hexdigest()
    forged = original.model_copy(
        update={
            "sha256": digest,
            "uri": f"artifact://sha256/{digest}",
            "bytes": len(payload),
            **(ref_updates or {}),
        }
    )
    forged_output = output.model_copy(update={"resolved_scene": forged})

    def replace_ref(ref):
        return forged if ref == original else ref

    forged_events = tuple(
        event.model_copy(
            update={"artifact_refs": tuple(replace_ref(ref) for ref in event.artifact_refs)}
        )
        for event in persisted.events
    )
    forged_state = persisted.model_copy(
        update={
            "output": forged_output.model_dump(mode="json"),
            "artifacts": tuple(replace_ref(ref) for ref in persisted.artifacts),
            "events": forged_events,
        }
    )
    forged_history = replace(
        history,
        events=tuple(
            replace(stored, envelope=replace(stored.envelope, event=event))
            for stored, event in zip(history.events, forged_events, strict=True)
        ),
    )
    target = application.artifact_root / "sha256" / digest[:2] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    monkeypatch.setattr(application, "run_state", lambda _run_id: forged_state)
    monkeypatch.setattr(application, "events", lambda **_kwargs: forged_history)
    return target


def test_scene_preview_rejects_a_real_non_success_terminal(
    tmp_path: Path,
) -> None:
    workbench, _ = _workbench(tmp_path, generate_missing_assets=False)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )

    with pytest.raises(WorkbenchCompileSceneNotPreviewableError, match="no previewable"):
        workbench.scene_preview(run_id=UUID(submission["run_id"]))


def test_scene_preview_rejects_an_unknown_or_non_uuid_run(tmp_path: Path) -> None:
    workbench, _ = _workbench(tmp_path)

    with pytest.raises(WorkbenchCompileRunNotFoundError, match="not found"):
        workbench.scene_preview(run_id=UUID("12345678-1234-4234-9234-123456789abc"))
    with pytest.raises(WorkbenchCompileInputError, match="UUID"):
        workbench.scene_preview(  # type: ignore[arg-type]
            run_id="12345678-1234-4234-9234-123456789abc"
        )


def test_scene_preview_rejects_declared_oversize_before_resolving_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    _install_scene_payload_authority(
        application=application,
        run_id=run_id,
        payload=b"x" * 65_537,
        monkeypatch=monkeypatch,
    )

    def must_not_resolve(_artifact):
        raise AssertionError("oversized SceneSpec must be rejected before CAS resolution")

    monkeypatch.setattr(application, "resolve_artifact", must_not_resolve)

    with pytest.raises(WorkbenchCompileSceneTooLargeError, match="exceeds"):
        workbench.scene_preview(run_id=run_id)


@pytest.mark.parametrize("mutation", ["missing", "same_size", "symlink", "fifo"])
def test_scene_preview_fails_closed_for_an_unavailable_or_replaced_cas_object(
    mutation: str,
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    path = application.resolve_artifact(output.scene_spec)
    original = path.read_bytes()
    if mutation == "missing":
        path.unlink()
    elif mutation == "same_size":
        path.write_bytes(bytes(byte ^ 0xFF for byte in original))
    elif mutation == "symlink":
        target = path.with_name(f"{path.name}.verified")
        path.rename(target)
        path.symlink_to(target)
    else:
        path.unlink()
        os.mkfifo(path)

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.scene_preview(run_id=run_id)


def test_scene_preview_rechecks_the_same_file_descriptor_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    path = application.resolve_artifact(output.scene_spec)
    original = path.read_bytes()
    real_read = os.read
    changed = False

    def change_after_first_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = real_read(descriptor, size)
        if chunk and not changed:
            changed = True
            path.write_bytes(original + b" " * (65_537 - len(original)))
        return chunk

    monkeypatch.setattr("demo.harness_compile.os.read", change_after_first_read)

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.scene_preview(run_id=run_id)
    assert changed is True


@pytest.mark.parametrize(
    "mutation",
    [
        "non_utf8",
        "bom",
        "duplicate",
        "nan",
        "overflow",
        "non_object",
        "forbidden",
        "wrong_schema",
        "deep",
    ],
)
def test_scene_preview_rejects_non_strict_or_invalid_scene_json(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    original = application.resolve_artifact(output.scene_spec).read_bytes()
    document = json.loads(original)
    if mutation == "non_utf8":
        payload = b"\xff"
    elif mutation == "bom":
        payload = b"\xef\xbb\xbf" + original
    elif mutation == "duplicate":
        payload = b'{"schema_version":"robotwin.scene_spec.v1",' + original.lstrip()[1:]
    elif mutation == "nan":
        document["workspace"]["table_height_m"] = float("nan")
        payload = json.dumps(document, allow_nan=True).encode("utf-8")
    elif mutation == "overflow":
        document["workspace"]["table_height_m"] = "NUMBER"
        payload = json.dumps(document).replace('"NUMBER"', "1e400").encode("utf-8")
    elif mutation == "non_object":
        payload = b"[]"
    elif mutation == "forbidden":
        document["asset_path"] = "/tmp/private-scene"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "wrong_schema":
        document["schema_version"] = "robotwin.scene_spec.v999"
        payload = json.dumps(document).encode("utf-8")
    else:
        nested: object = "leaf"
        for _ in range(70):
            nested = [nested]
        document["extra"] = nested
        payload = json.dumps(document).encode("utf-8")
    _install_scene_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
    )

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.scene_preview(run_id=run_id)


@pytest.mark.parametrize(
    "mutation",
    ["request", "request_array", "seed", "seed_float", "semantic_digest"],
)
def test_scene_preview_rejects_a_scene_that_is_not_bound_to_the_invocation_and_package(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    document = json.loads(application.resolve_artifact(output.scene_spec).read_bytes())
    package_digest = None
    package_seed = None
    if mutation == "request":
        document["request"] = "Place a blue sphere on the table."
        package_digest = SceneSpec.model_validate(document).digest()
    elif mutation == "request_array":
        document["request"] = [document["request"]]
        package_digest = SceneSpec.model_validate(
            {**document, "request": document["request"][0]}
        ).digest()
    elif mutation == "seed":
        document["seed"] = 78
        scene = SceneSpec.model_validate(document)
        package_digest = scene.digest()
        package_seed = scene.seed
    elif mutation == "seed_float":
        document["seed"] = 77.0
        package_digest = SceneSpec.model_validate(document).digest()
    else:
        document["workspace"]["table_height_m"] = 0.742
    payload = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    _install_scene_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
        package_scene_digest=package_digest,
        package_seed=package_seed,
    )

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.scene_preview(run_id=run_id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("media_type", "text/plain"),
        ("schema_version", "robotwin.scene_spec.v999"),
    ],
)
def test_scene_preview_rejects_a_typed_output_with_the_wrong_content_contract(
    field: str,
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    payload = application.resolve_artifact(output.scene_spec).read_bytes()
    _install_scene_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
        ref_updates={field: value},
    )
    real_read = harness_compile_module._read_verified_scene_bytes  # noqa: SLF001
    reads = 0

    def observe_read(*, path: Path, ref: ArtifactRef) -> bytes:
        nonlocal reads
        reads += 1
        return real_read(path=path, ref=ref)

    monkeypatch.setattr("demo.harness_compile._read_verified_scene_bytes", observe_read)

    with pytest.raises(WorkbenchCompileAuthorityError):
        workbench.scene_preview(run_id=run_id)
    assert reads == 0


def test_scene_preview_reader_rejects_oversize_even_when_called_directly(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    persisted = application.run_state(UUID(submission["run_id"]))
    assert persisted is not None
    ref = Text2EnvCompileOutput.model_validate(persisted.output).scene_spec
    path = application.resolve_artifact(ref)
    oversize = ref.model_copy(update={"bytes": 65_537})

    with pytest.raises(WorkbenchCompileSceneTooLargeError):
        harness_compile_module._read_verified_scene_bytes(  # noqa: SLF001
            path=path,
            ref=oversize,
        )


@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_NONBLOCK"])
def test_scene_preview_reader_fails_closed_without_required_descriptor_flags(
    flag: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    persisted = application.run_state(UUID(submission["run_id"]))
    assert persisted is not None
    ref = Text2EnvCompileOutput.model_validate(persisted.output).scene_spec
    path = application.resolve_artifact(ref)
    monkeypatch.delattr(harness_compile_module.os, flag)

    with pytest.raises(ValueError, match="required descriptor flags are unavailable"):
        harness_compile_module._read_verified_scene_bytes(  # noqa: SLF001
            path=path,
            ref=ref,
        )


@pytest.mark.parametrize("replacement", ["directory", "wrong_size", "wrong_digest"])
def test_scene_preview_rejects_a_cas_leaf_that_is_not_the_verified_object(
    replacement: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    persisted = application.run_state(UUID(submission["run_id"]))
    assert persisted is not None
    ref = Text2EnvCompileOutput.model_validate(persisted.output).scene_spec
    original = application.resolve_artifact(ref).read_bytes()
    replacement_path = tmp_path / "replacement"
    if replacement == "directory":
        replacement_path.mkdir()
    elif replacement == "wrong_size":
        replacement_path.write_bytes(b"x")
    else:
        replacement_path.write_bytes(bytes(byte ^ 0xFF for byte in original))
    monkeypatch.setattr(
        "demo.harness_compile._scene_cas_path",
        lambda **_kwargs: replacement_path,
    )

    with pytest.raises(WorkbenchCompileAuthorityError):
        workbench.scene_preview(run_id=UUID(submission["run_id"]))


def test_scene_preview_rejects_a_short_read_even_when_file_metadata_is_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, _ = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    monkeypatch.setattr("demo.harness_compile.os.read", lambda _descriptor, _size: b"")

    with pytest.raises(WorkbenchCompileAuthorityError):
        workbench.scene_preview(run_id=UUID(submission["run_id"]))


def test_scene_preview_rejects_a_coherent_authority_change_after_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, _ = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    stable = workbench._terminal_authority(run_id)  # noqa: SLF001
    drifted = replace(
        stable,
        persisted=stable.persisted.model_copy(update={"started_at": stable.persisted.ended_at}),
    )
    reads = iter((stable, drifted))
    monkeypatch.setattr(workbench, "_terminal_authority", lambda _run_id: next(reads))

    with pytest.raises(WorkbenchCompileAuthorityError, match="changed during preview"):
        workbench.scene_preview(run_id=run_id)


def test_scene_preview_treats_post_read_run_deletion_as_authority_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, _ = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    stable = workbench._terminal_authority(run_id)  # noqa: SLF001
    reads = iter((stable, WorkbenchCompileRunNotFoundError("terminal compile run was not found")))

    def authority_then_deleted(_run_id: UUID):
        result = next(reads)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(workbench, "_terminal_authority", authority_then_deleted)

    with pytest.raises(WorkbenchCompileAuthorityError, match="changed during preview"):
        workbench.scene_preview(run_id=run_id)


def test_scene_preview_accepts_the_exact_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    original = application.resolve_artifact(output.scene_spec).read_bytes()
    assert len(original) < 65_536
    payload = original + b" " * (65_536 - len(original))
    _install_scene_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
    )

    preview = workbench.scene_preview(run_id=run_id)

    assert preview["artifact"]["bytes"] == "65536"


def test_scene_preview_reconfirms_the_terminal_authority_after_the_cas_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    stable = application.run_state(run_id)
    assert stable is not None
    drifted = stable.model_copy(update={"attempt": 0})
    real_run_state = application.run_state
    real_read = harness_compile_module._read_verified_scene_bytes  # noqa: SLF001
    state_reads = 0
    reads = 0

    def drift_after_first_authority(_run_id):
        nonlocal state_reads
        state_reads += 1
        return stable if state_reads <= 2 else drifted

    def observe_read(*, path: Path, ref: ArtifactRef) -> bytes:
        nonlocal reads
        reads += 1
        return real_read(path=path, ref=ref)

    monkeypatch.setattr(application, "run_state", drift_after_first_authority)
    monkeypatch.setattr("demo.harness_compile._read_verified_scene_bytes", observe_read)

    with pytest.raises(WorkbenchCompileAuthorityError):
        workbench.scene_preview(run_id=run_id)
    assert reads == 1
    assert real_run_state(run_id) == stable


def test_scene_preview_never_reuses_content_after_the_cas_object_changes(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)

    assert workbench.scene_preview(run_id=run_id)["scene"]["seed"] == 77
    path = application.resolve_artifact(output.scene_spec)
    original = path.read_bytes()
    path.write_bytes(bytes(byte ^ 0xFF for byte in original))

    with pytest.raises(WorkbenchCompileAuthorityError):
        workbench.scene_preview(run_id=run_id)


def test_static_validation_preview_exposes_only_the_fixed_run_selector() -> None:
    assert tuple(inspect.signature(WorkbenchCompile.static_validation_preview).parameters) == (
        "self",
        "run_id",
    )


def test_static_validation_preview_does_not_use_the_unbounded_application_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    calls: list[ArtifactRef] = []

    def reject_unbounded_resolution(artifact: ArtifactRef) -> Path:
        calls.append(artifact)
        raise AssertionError("static preview must use bounded descriptor readers")

    monkeypatch.setattr(application, "resolve_artifact", reject_unbounded_resolution)

    preview = workbench.static_validation_preview(run_id=UUID(submission["run_id"]))

    assert preview["validation"]["status"] == "incomplete"
    assert calls == []


def test_static_validation_preview_rejects_a_real_non_success_terminal(
    tmp_path: Path,
) -> None:
    workbench, _ = _workbench(tmp_path, generate_missing_assets=False)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )

    with pytest.raises(
        WorkbenchCompileStaticValidationNotPreviewableError,
        match="no previewable",
    ):
        workbench.static_validation_preview(run_id=UUID(submission["run_id"]))


def test_static_validation_preview_rejects_an_unknown_or_non_uuid_run(
    tmp_path: Path,
) -> None:
    workbench, _ = _workbench(tmp_path)

    with pytest.raises(WorkbenchCompileRunNotFoundError, match="not found"):
        workbench.static_validation_preview(run_id=UUID("12345678-1234-4234-9234-123456789abc"))
    with pytest.raises(WorkbenchCompileInputError, match="UUID"):
        workbench.static_validation_preview(  # type: ignore[arg-type]
            run_id="12345678-1234-4234-9234-123456789abc"
        )


def test_static_validation_preview_rejects_declared_oversize_before_cas_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    _install_static_validation_payload_authority(
        application=application,
        run_id=run_id,
        payload=b"x" * 262_145,
        monkeypatch=monkeypatch,
    )

    def must_not_locate(**_kwargs):
        raise AssertionError("oversized static report must be rejected before CAS access")

    monkeypatch.setattr(
        "demo.harness_compile._static_validation_cas_path",
        must_not_locate,
    )

    with pytest.raises(WorkbenchCompileStaticValidationTooLargeError, match="exceeds"):
        workbench.static_validation_preview(run_id=run_id)


@pytest.mark.parametrize(
    "mutation",
    [
        "non_utf8",
        "bom",
        "duplicate_member",
        "nan",
        "overflow",
        "non_object",
        "extra_root_field",
        "missing_root_field",
        "wrong_schema",
        "unsafe_scene_id",
        "wrong_digest",
        "pass_status",
        "boolean_fail_count",
        "float_not_run_count",
        "checks_object",
        "deep",
        "too_many_checks",
        "check_non_object",
        "extra_check_field",
        "unsafe_check_name",
        "duplicate_check_name",
        "bad_check_status",
        "runtime_status",
        "runtime_evidence_extra",
        "runtime_required",
        "missing_package_manifest",
        "failed_package_manifest",
        "missing_roundtrip",
        "failed_roundtrip",
        "wrong_fail_count",
        "wrong_not_run_count",
        "wrong_derived_status",
    ],
)
def test_static_validation_preview_rejects_non_strict_or_inconsistent_reports(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    original = application.resolve_artifact(output.static_validation).read_bytes()
    document = json.loads(original)
    if mutation == "non_utf8":
        payload = b"\xff"
    elif mutation == "bom":
        payload = b"\xef\xbb\xbf" + original
    elif mutation == "duplicate_member":
        payload = b'{"schema_version":"robotwin.scene_validation.v1",' + original.lstrip()[1:]
    elif mutation == "nan":
        document["checks"][0]["evidence"]["aabb"][0] = float("nan")
        payload = json.dumps(document, allow_nan=True).encode("utf-8")
    elif mutation == "overflow":
        document["checks"][0]["evidence"]["aabb"][0] = "NUMBER"
        payload = json.dumps(document).replace('"NUMBER"', "1e400").encode("utf-8")
    elif mutation == "non_object":
        payload = b"[]"
    elif mutation == "extra_root_field":
        document["evidence"] = {"path": "/tmp/private"}
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "missing_root_field":
        del document["scene_id"]
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "wrong_schema":
        document["schema_version"] = "robotwin.scene_validation.v2"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "unsafe_scene_id":
        document["scene_id"] = "../private-scene"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "wrong_digest":
        document["resolved_scene_sha256"] = "not-a-digest"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "pass_status":
        document["status"] = "pass"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "boolean_fail_count":
        document["fail_count"] = False
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "float_not_run_count":
        document["not_run_count"] = 1.0
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "checks_object":
        document["checks"] = {}
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "deep":
        nested: object = "leaf"
        for _ in range(70):
            nested = [nested]
        document["checks"][0]["evidence"]["nested"] = nested
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "too_many_checks":
        document["checks"].extend(
            {
                "name": f"extra_check_{index:03d}",
                "status": "pass",
                "evidence": None,
            }
            for index in range(163)
        )
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "check_non_object":
        document["checks"][0] = []
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "extra_check_field":
        document["checks"][0]["detail"] = "private"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "unsafe_check_name":
        document["checks"][0]["name"] = "../../private"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "duplicate_check_name":
        document["checks"][1]["name"] = document["checks"][0]["name"]
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "bad_check_status":
        document["checks"][0]["status"] = "not_applicable"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "runtime_status":
        document["checks"][-1]["status"] = "pass"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "runtime_evidence_extra":
        document["checks"][-1]["evidence"]["path"] = "/tmp/private"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "runtime_required":
        document["checks"][-1]["evidence"]["required"] = True
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "missing_package_manifest":
        document["checks"] = [
            item for item in document["checks"] if item["name"] != "package_manifest"
        ]
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "failed_package_manifest":
        item = next(item for item in document["checks"] if item["name"] == "package_manifest")
        item["status"] = "fail"
        document["fail_count"] = 1
        document["status"] = "fail"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "missing_roundtrip":
        document["checks"] = [
            item for item in document["checks"] if item["name"] != "resolved_only_roundtrip"
        ]
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "failed_roundtrip":
        item = next(
            item for item in document["checks"] if item["name"] == "resolved_only_roundtrip"
        )
        item["status"] = "fail"
        document["fail_count"] = 1
        document["status"] = "fail"
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "wrong_fail_count":
        document["fail_count"] = 1
        payload = json.dumps(document).encode("utf-8")
    elif mutation == "wrong_not_run_count":
        document["not_run_count"] = 0
        payload = json.dumps(document).encode("utf-8")
    else:
        document["status"] = "fail"
        payload = json.dumps(document).encode("utf-8")
    _install_static_validation_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
    )

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)


def test_static_validation_preview_accepts_a_derived_fail_summary_and_keeps_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    document = json.loads(application.resolve_artifact(output.static_validation).read_bytes())
    document["checks"][0]["status"] = "fail"
    document["fail_count"] = 1
    document["status"] = "fail"
    payload = json.dumps(document).encode("utf-8")
    _install_static_validation_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
    )

    validation = workbench.static_validation_preview(run_id=run_id)["validation"]

    assert validation["status"] == "fail"
    assert validation["counts"] == {"checks": 7, "pass": 5, "fail": 1, "not_run": 1}
    assert [item["name"] for item in validation["checks"]] == [
        item["name"] for item in document["checks"]
    ]


def test_static_validation_preview_rejects_a_second_not_run_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    document = json.loads(application.resolve_artifact(output.static_validation).read_bytes())
    document["checks"][0]["status"] = "not_run"
    document["not_run_count"] = 2
    _install_static_validation_payload_authority(
        application=application,
        run_id=run_id,
        payload=json.dumps(document).encode("utf-8"),
        monkeypatch=monkeypatch,
    )

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)


@pytest.mark.parametrize("binding", ["scene_id", "resolved_scene_sha256"])
def test_static_validation_preview_rejects_report_bindings_outside_the_typed_package(
    binding: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    document = json.loads(application.resolve_artifact(output.static_validation).read_bytes())
    document[binding] = "forged_scene" if binding == "scene_id" else "f" * 64
    _install_static_validation_payload_authority(
        application=application,
        run_id=run_id,
        payload=json.dumps(document).encode("utf-8"),
        monkeypatch=monkeypatch,
    )

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)


def test_static_validation_preview_rejects_an_unverified_scene_binding(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    scene_ref = Text2EnvCompileOutput.model_validate(persisted.output).scene_spec
    path = application.resolve_artifact(scene_ref)
    original = path.read_bytes()
    path.write_bytes(bytes(byte ^ 0xFF for byte in original))

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)


def test_static_validation_preview_rejects_a_rebound_resolved_scene_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    resolved_ref = Text2EnvCompileOutput.model_validate(persisted.output).resolved_scene
    document = json.loads(application.resolve_artifact(resolved_ref).read_bytes())
    document["compiler_version"] = "coherently-rebound-but-unrelated"
    _install_resolved_scene_payload_authority(
        application=application,
        run_id=run_id,
        payload=json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8") + b"\n",
        monkeypatch=monkeypatch,
    )

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)


@pytest.mark.parametrize(
    ("ref_updates", "expected_reads"),
    [
        ({"media_type": "text/plain"}, 0),
        ({"bytes": 1_048_577}, 0),
    ],
)
def test_static_validation_preview_rejects_invalid_resolved_content_contract_before_reading(
    ref_updates: dict[str, object],
    expected_reads: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    resolved_ref = Text2EnvCompileOutput.model_validate(persisted.output).resolved_scene
    payload = application.resolve_artifact(resolved_ref).read_bytes()
    _install_resolved_scene_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
        ref_updates=ref_updates,
    )
    reads = 0

    def observe_read(**_kwargs):
        nonlocal reads
        reads += 1
        raise AssertionError("invalid resolved content contract must be rejected before reading")

    monkeypatch.setattr(
        "demo.harness_compile._read_verified_resolved_scene_bytes",
        observe_read,
    )

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)
    assert reads == expected_reads


@pytest.mark.parametrize(
    "payload_case",
    [
        "bom",
        "duplicate_member",
        "nonfinite_float",
        "nonstandard_constant",
        "non_object",
        "too_deep",
        "noncanonical_default",
    ],
)
def test_static_validation_preview_rejects_non_strict_resolved_scene_json(
    payload_case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    resolved_ref = Text2EnvCompileOutput.model_validate(persisted.output).resolved_scene
    document = json.loads(application.resolve_artifact(resolved_ref).read_bytes())
    valid_payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
    if payload_case == "bom":
        payload = b"\xef\xbb\xbf" + valid_payload
    elif payload_case == "duplicate_member":
        payload = b'{"schema_version":"robotwin.resolved_scene.v1",' + valid_payload[1:]
    elif payload_case == "nonfinite_float":
        payload = b'{"x":1e309}'
    elif payload_case == "nonstandard_constant":
        payload = b'{"x":NaN}'
    elif payload_case == "non_object":
        payload = b"[]"
    elif payload_case == "too_deep":
        nested: object = 0
        for _ in range(65):
            nested = {"x": nested}
        payload = json.dumps(nested).encode("utf-8")
    else:
        document.pop("unit")
        payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
    _install_resolved_scene_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
    )

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)


def test_static_validation_preview_rejects_a_non_json_typed_scene_before_reading_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    payload = application.resolve_artifact(output.scene_spec).read_bytes()
    _install_scene_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
        ref_updates={"media_type": "text/plain"},
    )
    reads = 0

    def observe_read(**_kwargs):
        nonlocal reads
        reads += 1
        raise AssertionError("non-JSON SceneSpec must be rejected before reading")

    monkeypatch.setattr("demo.harness_compile._read_verified_scene_bytes", observe_read)

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)
    assert reads == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("media_type", "text/plain"),
        ("schema_version", "robotwin.scene_validation.v2"),
    ],
)
def test_static_validation_preview_rejects_the_wrong_content_contract_before_reading(
    field: str,
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    payload = application.resolve_artifact(output.static_validation).read_bytes()
    _install_static_validation_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
        ref_updates={field: value},
    )
    reads = 0

    def observe_read(**_kwargs):
        nonlocal reads
        reads += 1
        raise AssertionError("invalid content contract must be rejected before reading")

    monkeypatch.setattr(
        "demo.harness_compile._read_verified_static_validation_bytes",
        observe_read,
    )

    with pytest.raises(WorkbenchCompileAuthorityError):
        workbench.static_validation_preview(run_id=run_id)
    assert reads == 0


@pytest.mark.parametrize("mutation", ["missing", "same_size", "symlink", "fifo"])
def test_static_validation_preview_fails_closed_for_a_replaced_cas_object(
    mutation: str,
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    report_ref = Text2EnvCompileOutput.model_validate(persisted.output).static_validation
    path = application.resolve_artifact(report_ref)
    original = path.read_bytes()
    if mutation == "missing":
        path.unlink()
    elif mutation == "same_size":
        path.write_bytes(bytes(byte ^ 0xFF for byte in original))
    elif mutation == "symlink":
        target = path.with_name(f"{path.name}.verified")
        path.rename(target)
        path.symlink_to(target)
    else:
        path.unlink()
        os.mkfifo(path)

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)


def test_static_validation_preview_rechecks_the_same_descriptor_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    report_ref = Text2EnvCompileOutput.model_validate(persisted.output).static_validation
    path = application.resolve_artifact(report_ref)
    original = path.read_bytes()
    real_read = os.read
    changed = False

    def change_after_first_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = real_read(descriptor, size)
        if chunk and not changed:
            changed = True
            path.write_bytes(original + b" " * (262_145 - len(original)))
        return chunk

    monkeypatch.setattr("demo.harness_compile.os.read", change_after_first_read)

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)
    assert changed is True


def test_static_validation_reader_rejects_oversize_even_when_called_directly(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    persisted = application.run_state(UUID(submission["run_id"]))
    assert persisted is not None
    ref = Text2EnvCompileOutput.model_validate(persisted.output).static_validation
    path = application.resolve_artifact(ref)
    oversize = ref.model_copy(update={"bytes": 262_145})

    with pytest.raises(WorkbenchCompileStaticValidationTooLargeError):
        harness_compile_module._read_verified_static_validation_bytes(  # noqa: SLF001
            path=path,
            ref=oversize,
        )


@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_NONBLOCK"])
def test_static_validation_reader_fails_closed_without_required_descriptor_flags(
    flag: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    persisted = application.run_state(UUID(submission["run_id"]))
    assert persisted is not None
    ref = Text2EnvCompileOutput.model_validate(persisted.output).static_validation
    path = application.resolve_artifact(ref)
    monkeypatch.delattr(harness_compile_module.os, flag)

    with pytest.raises(ValueError, match="required descriptor flags are unavailable"):
        harness_compile_module._read_verified_static_validation_bytes(  # noqa: SLF001
            path=path,
            ref=ref,
        )


@pytest.mark.parametrize("replacement", ["directory", "wrong_size", "wrong_digest"])
def test_static_validation_preview_rejects_a_non_verified_cas_leaf(
    replacement: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    persisted = application.run_state(UUID(submission["run_id"]))
    assert persisted is not None
    ref = Text2EnvCompileOutput.model_validate(persisted.output).static_validation
    original = application.resolve_artifact(ref).read_bytes()
    replacement_path = tmp_path / "replacement-static-validation"
    if replacement == "directory":
        replacement_path.mkdir()
    elif replacement == "wrong_size":
        replacement_path.write_bytes(b"x")
    else:
        replacement_path.write_bytes(bytes(byte ^ 0xFF for byte in original))
    monkeypatch.setattr(
        "demo.harness_compile._static_validation_cas_path",
        lambda **_kwargs: replacement_path,
    )

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=UUID(submission["run_id"]))


def test_static_validation_preview_rejects_a_short_read_with_stable_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, _ = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    monkeypatch.setattr("demo.harness_compile.os.read", lambda _descriptor, _size: b"")

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=UUID(submission["run_id"]))


def test_static_validation_preview_rejects_coherent_authority_drift_after_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, _ = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    stable = workbench._terminal_authority(run_id)  # noqa: SLF001
    drifted = replace(
        stable,
        persisted=stable.persisted.model_copy(update={"started_at": stable.persisted.ended_at}),
    )
    reads = iter((stable, drifted))
    monkeypatch.setattr(workbench, "_terminal_authority", lambda _run_id: next(reads))

    with pytest.raises(WorkbenchCompileAuthorityError, match="changed during"):
        workbench.static_validation_preview(run_id=run_id)


def test_static_validation_preview_normalizes_post_read_run_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, _ = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    stable = workbench._terminal_authority(run_id)  # noqa: SLF001
    reads = iter((stable, WorkbenchCompileRunNotFoundError("run deleted")))

    def authority_then_deleted(_run_id: UUID):
        result = next(reads)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(workbench, "_terminal_authority", authority_then_deleted)

    with pytest.raises(WorkbenchCompileAuthorityError, match="changed during"):
        workbench.static_validation_preview(run_id=run_id)


def test_static_validation_preview_accepts_the_exact_byte_and_check_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    output = Text2EnvCompileOutput.model_validate(persisted.output)
    document = json.loads(application.resolve_artifact(output.static_validation).read_bytes())
    document["checks"].extend(
        {
            "name": f"boundary_check_{index:03d}",
            "status": "pass",
            "evidence": None,
        }
        for index in range(162)
    )
    encoded = json.dumps(document).encode("utf-8")
    assert len(encoded) < 262_144
    payload = encoded + b" " * (262_144 - len(encoded))
    _install_static_validation_payload_authority(
        application=application,
        run_id=run_id,
        payload=payload,
        monkeypatch=monkeypatch,
    )

    preview = workbench.static_validation_preview(run_id=run_id)

    assert preview["artifact"]["bytes"] == "262144"
    assert preview["validation"]["counts"] == {
        "checks": 169,
        "pass": 168,
        "fail": 0,
        "not_run": 1,
    }


def test_static_validation_preview_reconfirms_authority_after_all_cas_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    stable = application.run_state(run_id)
    assert stable is not None
    drifted = stable.model_copy(update={"attempt": 0})
    real_run_state = application.run_state
    real_report_read = harness_compile_module._read_verified_static_validation_bytes  # noqa: SLF001
    real_scene_read = harness_compile_module._read_verified_scene_bytes  # noqa: SLF001
    real_resolved_read = harness_compile_module._read_verified_resolved_scene_bytes  # noqa: SLF001
    state_reads = 0
    report_reads = 0
    scene_reads = 0
    resolved_reads = 0

    def drift_after_first_authority(_run_id):
        nonlocal state_reads
        state_reads += 1
        return stable if state_reads <= 2 else drifted

    def observe_report_read(*, path: Path, ref: ArtifactRef) -> bytes:
        nonlocal report_reads
        report_reads += 1
        return real_report_read(path=path, ref=ref)

    def observe_scene_read(*, path: Path, ref: ArtifactRef) -> bytes:
        nonlocal scene_reads
        scene_reads += 1
        return real_scene_read(path=path, ref=ref)

    def observe_resolved_read(*, path: Path, ref: ArtifactRef) -> bytes:
        nonlocal resolved_reads
        resolved_reads += 1
        return real_resolved_read(path=path, ref=ref)

    monkeypatch.setattr(application, "run_state", drift_after_first_authority)
    monkeypatch.setattr(
        "demo.harness_compile._read_verified_static_validation_bytes",
        observe_report_read,
    )
    monkeypatch.setattr(
        "demo.harness_compile._read_verified_scene_bytes",
        observe_scene_read,
    )
    monkeypatch.setattr(
        "demo.harness_compile._read_verified_resolved_scene_bytes",
        observe_resolved_read,
    )

    with pytest.raises(WorkbenchCompileAuthorityError):
        workbench.static_validation_preview(run_id=run_id)
    assert report_reads == 1
    assert scene_reads == 1
    assert resolved_reads == 1
    assert real_run_state(run_id) == stable


def test_static_validation_preview_never_reuses_changed_cas_content(
    tmp_path: Path,
) -> None:
    workbench, application = _workbench(tmp_path)
    submission = workbench.submit(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
    )
    run_id = UUID(submission["run_id"])
    persisted = application.run_state(run_id)
    assert persisted is not None
    report_ref = Text2EnvCompileOutput.model_validate(persisted.output).static_validation

    assert workbench.static_validation_preview(run_id=run_id)["validation"]["status"] == (
        "incomplete"
    )
    path = application.resolve_artifact(report_ref)
    original = path.read_bytes()
    path.write_bytes(bytes(byte ^ 0xFF for byte in original))

    with pytest.raises(WorkbenchCompileAuthorityError, match="integrity checks"):
        workbench.static_validation_preview(run_id=run_id)
