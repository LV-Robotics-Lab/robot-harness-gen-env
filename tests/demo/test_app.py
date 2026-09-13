from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from demo.app import DEFAULT_SETTLE_STEPS, create_app, utc_now
from demo.harness_feed import HarnessEventFeed
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.events import RunRecorder


def configured_app(tmp_path: Path, monkeypatch, config=None):
    repo = tmp_path / "repo"
    robotwin = tmp_path / "RoboTwin"
    python = tmp_path / "python"
    catalog = tmp_path / "catalog.json"
    jobs = tmp_path / "jobs"
    repo.mkdir()
    robotwin.mkdir()
    python.write_text("", encoding="utf-8")
    catalog.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SCENE_DEMO_REPO_ROOT", str(repo))
    monkeypatch.setenv("ROBOTWIN_ROOT", str(robotwin))
    monkeypatch.setenv("ROBOTWIN_PYTHON", str(python))
    monkeypatch.setenv("SCENE_ASSET_CATALOG", str(catalog))
    monkeypatch.setenv("SCENE_DEMO_JOBS_ROOT", str(jobs))
    return create_app({"TESTING": True, **(config or {})})


def test_health_and_job_input_boundary(tmp_path: Path, monkeypatch) -> None:
    assert DEFAULT_SETTLE_STEPS == 900
    app = configured_app(tmp_path, monkeypatch)
    client = app.test_client()
    assert client.get("/api/health").json["status"] == "ready"
    assert client.post("/api/jobs", json={"prompt": "x", "seed": 0}).status_code == 400
    assert client.post("/api/jobs", json={"prompt": "valid scene", "seed": -1}).status_code == 400

    pipeline = app.extensions["scene_pipeline"]
    monkeypatch.setattr(
        pipeline,
        "submit",
        lambda prompt, seed: {
            "job_id": "abcdef0123456789",
            "prompt": prompt,
            "seed": seed,
            "status": "queued",
        },
    )
    response = client.post("/api/jobs", json={"prompt": "Put a cup inside a basket.", "seed": 9})
    assert response.status_code == 202
    assert response.json["seed"] == 9


def test_artifact_route_serves_only_registered_job_files(tmp_path: Path, monkeypatch) -> None:
    app = configured_app(tmp_path, monkeypatch)
    store = app.extensions["scene_store"]
    job_id = "abcdef0123456789"
    directory = store.directory(job_id)
    directory.mkdir(parents=True)
    (directory / "preview.png").write_bytes(b"png")
    (directory / "secret.txt").write_text("secret", encoding="utf-8")
    now = utc_now()
    store.write(
        {
            "job_id": job_id,
            "created_at": now,
            "updated_at": now,
            "status": "completed",
            "stages": {},
            "artifacts": {"head": "preview.png"},
        }
    )
    client = app.test_client()
    assert client.get(f"/api/jobs/{job_id}/artifacts/preview.png").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/artifacts/secret.txt").status_code == 404


def test_harness_events_fail_closed_when_feed_is_not_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = configured_app(tmp_path, monkeypatch).test_client()

    response = client.get("/api/harness/events")

    assert response.status_code == 503
    assert response.json == {
        "error": {
            "code": "harness_event_feed_unavailable",
            "message": "Harness event feed is not configured",
        }
    }


def test_harness_events_resume_from_the_requested_global_cursor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_id = UUID("12345678-1234-4234-9234-123456789abc")
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    recorder = RunRecorder(
        run_id=run_id,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    )
    recorder.start(stage="invoke.started", attempt=1)
    recorder.progress(stage="compile.parse.started")
    app = configured_app(
        tmp_path,
        monkeypatch,
        {"HARNESS_EVENT_FEED": HarnessEventFeed.from_journal(journal)},
    )
    client = app.test_client()

    first = client.get(f"/api/harness/events?after=0&run_id={run_id}&limit=1")
    second = client.get(f"/api/harness/events?after=1&run_id={run_id}&limit=1")

    assert first.status_code == 200
    assert [item["event_id"] for item in first.json["events"]] == ["1"]
    assert first.json["last_event_id"] == "1"
    assert first.json["has_more"] is True
    assert second.status_code == 200
    assert [item["event_id"] for item in second.json["events"]] == ["2"]
    assert second.json["last_event_id"] == "2"
    assert second.json["has_more"] is False


@pytest.mark.parametrize(
    "value",
    ["not-an-integer", "-1", str(2**63)],
)
def test_harness_events_reject_an_invalid_cursor(
    value: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    app = configured_app(
        tmp_path,
        monkeypatch,
        {"HARNESS_EVENT_FEED": HarnessEventFeed.from_journal(journal)},
    )

    response = app.test_client().get(f"/api/harness/events?after={value}")

    assert response.status_code == 400
    assert response.json == {
        "error": {
            "code": "invalid_harness_event_query",
            "message": "after must be a nonnegative integer",
        }
    }


@pytest.mark.parametrize(
    "value",
    ["", "not-a-uuid", "12345678123442349234123456789abc"],
)
def test_harness_events_reject_an_invalid_run_id(
    value: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    app = configured_app(
        tmp_path,
        monkeypatch,
        {"HARNESS_EVENT_FEED": HarnessEventFeed.from_journal(journal)},
    )

    response = app.test_client().get(f"/api/harness/events?run_id={value}")

    assert response.status_code == 400
    assert response.json == {
        "error": {
            "code": "invalid_harness_event_query",
            "message": "run_id must be a UUID",
        }
    }


@pytest.mark.parametrize(
    "value",
    ["0", "501", "not-an-integer", "-1", pytest.param("9" * 5000, id="too-many-digits")],
)
def test_harness_events_reject_an_invalid_page_limit(
    value: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    app = configured_app(
        tmp_path,
        monkeypatch,
        {"HARNESS_EVENT_FEED": HarnessEventFeed.from_journal(journal)},
    )

    response = app.test_client().get(f"/api/harness/events?limit={value}")

    assert response.status_code == 400
    assert response.json == {
        "error": {
            "code": "invalid_harness_event_query",
            "message": "limit must be an integer from 1 to 500",
        }
    }


def test_harness_events_fail_closed_on_corrupt_history(tmp_path: Path, monkeypatch) -> None:
    journal_path = tmp_path / "harness.sqlite3"
    journal = SQLiteEventJournal(journal_path)
    recorder = RunRecorder(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    )
    recorder.start(stage="invoke.started", attempt=1)
    with closing(sqlite3.connect(journal_path)) as connection, connection:
        connection.execute(
            "UPDATE run_events SET envelope_json = ? WHERE event_id = 1",
            ('{"broken":true}',),
        )
    app = configured_app(
        tmp_path,
        monkeypatch,
        {"HARNESS_EVENT_FEED": HarnessEventFeed.from_journal(journal)},
    )

    response = app.test_client().get("/api/harness/events")

    assert response.status_code == 503
    assert response.json == {
        "error": {
            "code": "harness_event_feed_corrupt",
            "message": "Harness event history failed integrity checks",
        }
    }


def test_harness_events_fail_closed_when_the_sqlite_file_is_not_a_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal_path = tmp_path / "harness.sqlite3"
    journal = SQLiteEventJournal(journal_path)
    journal_path.write_bytes(b"not a sqlite database")
    app = configured_app(
        tmp_path,
        monkeypatch,
        {
            "HARNESS_EVENT_FEED": HarnessEventFeed.from_journal(journal),
            "PROPAGATE_EXCEPTIONS": False,
        },
    )

    response = app.test_client().get("/api/harness/events")

    assert response.status_code == 503
    assert response.json == {
        "error": {
            "code": "harness_event_feed_corrupt",
            "message": "Harness event history failed integrity checks",
        }
    }
