from __future__ import annotations

import inspect
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from demo.app import create_app
from demo.harness_compile import (
    WorkbenchCompile,
    WorkbenchCompileAuthorityError,
    WorkbenchCompileInputError,
    WorkbenchCompileUnavailableError,
)
from demo.harness_feed import HarnessEventFeedCorruptionError
from scene_gen.catalog import AssetCatalog
from self_improving.harness.application import (
    CompileApplication,
    CompileApplicationSettings,
    create_compile_application,
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
