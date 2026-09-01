from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from flask import jsonify, request
from werkzeug.serving import make_server

from demo.app import create_app
from demo.harness_compile import WorkbenchCompileUnavailableError
from demo.harness_feed import HarnessEventFeed
from self_improving.harness import RunRecorder, RunStatus, SQLiteEventJournal


def _configured_app(
    tmp_path: Path,
    monkeypatch,
    feed: HarnessEventFeed | None,
    *,
    workbench=None,
):
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
    config = {"TESTING": True}
    if feed is not None:
        config["HARNESS_EVENT_FEED"] = feed
    if workbench is not None:
        config["HARNESS_WORKBENCH"] = workbench
    return create_app(config)


class _JournalSubmittingWorkbench:
    run_id = UUID("32345678-1234-4234-9234-123456789abc")

    def __init__(self, journal: SQLiteEventJournal) -> None:
        self.journal = journal
        self.feed = HarnessEventFeed.from_journal(journal)
        self.submissions: list[tuple[str, int]] = []

    def page(self, **kwargs):
        return self.feed.page(**kwargs)

    def submit(self, *, request: object, seed: object):
        assert type(request) is str
        assert type(seed) is int
        self.submissions.append((request, seed))
        recorder = RunRecorder(
            run_id=self.run_id,
            skill_id="text2env.compile",
            skill_version="1.0.0",
            clock=lambda: datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc),
            sink=self.journal,
        )
        recorder.start(stage="invoke.started", attempt=1)
        recorder.progress(stage="compile.parse.started")
        recorder.finish(status=RunStatus.SUCCEEDED, stage="invoke.succeeded")
        page = self.page(run_id=self.run_id)
        return {
            "schema_version": "harness.workbench_compile_submission.v1",
            "run_id": str(self.run_id),
            "skill_id": "text2env.compile",
            "skill_version": "1.0.0",
            "status": "succeeded",
            "attempt": 1,
            "max_attempts": 1,
            "terminal_event_id": page["last_event_id"],
            "blocker": None,
        }


class _UnboundSubmittingWorkbench:
    run_id = UUID("42345678-1234-4234-9234-123456789abc")

    def __init__(self, journal: SQLiteEventJournal, *, smuggle_events: bool = False) -> None:
        self.feed = HarnessEventFeed.from_journal(journal)
        self.smuggle_events = smuggle_events

    def page(self, **kwargs):
        return self.feed.page(**kwargs)

    def submit(self, **_kwargs):
        submission = {
            "schema_version": "harness.workbench_compile_submission.v1",
            "run_id": str(self.run_id),
            "skill_id": "text2env.compile",
            "skill_version": "1.0.0",
            "status": "succeeded",
            "attempt": 1,
            "max_attempts": 1,
            "terminal_event_id": "7",
            "blocker": None,
        }
        if self.smuggle_events:
            submission["events"] = [{"stage": "forged.progress"}]
        return submission


class _PreflightSubmittingWorkbench:
    run_id = UUID("52345678-1234-4234-9234-123456789abc")

    def __init__(self, journal: SQLiteEventJournal) -> None:
        self.feed = HarnessEventFeed.from_journal(journal)
        self.journal = journal

    def page(self, **kwargs):
        return self.feed.page(**kwargs)

    def submit(self, **_kwargs):
        recorder = RunRecorder(
            run_id=self.run_id,
            skill_id="text2env.compile",
            skill_version="1.0.0",
            clock=lambda: datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc),
            sink=self.journal,
        )
        recorder.start(stage="preflight", attempt=0)
        recorder.finish(status=RunStatus.BLOCKED, stage="preflight")
        page = self.page(run_id=self.run_id)
        return {
            "schema_version": "harness.workbench_compile_submission.v1",
            "run_id": str(self.run_id),
            "skill_id": "text2env.compile",
            "skill_version": "1.0.0",
            "status": "blocked",
            "attempt": 0,
            "max_attempts": 0,
            "terminal_event_id": page["last_event_id"],
            "blocker": {"code": "HARN_DEPENDENCY_UNAVAILABLE", "retryable": False},
        }


class _SecondSubmissionFailsWorkbench(_JournalSubmittingWorkbench):
    def submit(self, *, request: object, seed: object):
        if self.submissions:
            raise WorkbenchCompileUnavailableError("second submission unavailable")
        return super().submit(request=request, seed=seed)


@contextmanager
def _served(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _rendered_dom(
    url: str,
    profile: Path,
    *,
    chrome_args: tuple[str, ...] = (),
    virtual_time_budget_ms: int = 2500,
) -> str:
    chrome = shutil.which("google-chrome")
    if chrome is None:
        pytest.skip("Google Chrome is unavailable")
    completed = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            *chrome_args,
            f"--virtual-time-budget={virtual_time_budget_ms}",
            "--dump-dom",
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_browser_renders_committed_harness_events(tmp_path: Path, monkeypatch) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    recorder = RunRecorder(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    )
    recorder.start(stage="invoke.started", attempt=1)
    recorder.progress(stage="compile.parse.started")
    recorder.finish(status=RunStatus.SUCCEEDED, stage="invoke.succeeded")
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert 'id="harness-event-list"' in dom
    assert 'data-event-id="1"' in dom
    assert 'data-event-id="2"' in dom
    assert 'data-event-id="3"' in dom
    assert "compile.parse.started" in dom
    assert "succeeded" in dom


def test_browser_harness_compile_replays_only_committed_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    unrelated_run = UUID("12345678-1234-4234-9234-123456789abc")
    RunRecorder(
        run_id=unrelated_run,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="replay.started", attempt=1)
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    observed_requests: list[tuple[str, str, str | None, str | None]] = []

    @app.before_request
    def observe_harness_requests():
        observed_requests.append(
            (
                request.method,
                request.path,
                request.args.get("run_id"),
                request.args.get("after"),
            )
        )

    @app.after_request
    def click_compile_after_the_application_script(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    '<script>document.querySelector("#harness-compile-button").click();</script>'
                    "</body>",
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert workbench.submissions == [("Place a can on top of a plate.", 42)]
    assert not any(
        method == "POST" and path == "/api/jobs" for method, path, _, _ in observed_requests
    )
    assert (
        "GET",
        "/api/harness/events",
        str(workbench.run_id),
        "0",
    ) in observed_requests
    assert 'data-event-id="1"' not in dom
    assert 'data-event-id="2"' in dom
    assert 'data-event-id="3"' in dom
    assert 'data-event-id="4"' in dom
    assert dom.count("compile.parse.started") == 1
    assert "replay.started" not in dom
    assert f'value="{workbench.run_id}"' in dom
    assert "Harness Compile 已持久化为 succeeded" in dom


def test_browser_rejects_compile_summary_without_matching_committed_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _UnboundSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def click_compile_after_the_application_script(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    '<script>document.querySelector("#harness-compile-button").click();</script>'
                    "</body>",
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert ">响应损坏</span>" in dom
    assert "Harness Compile 摘要与 committed journal 无法对账" in dom
    assert "Harness Compile 已持久化为 succeeded" not in dom
    assert "data-event-id=" not in dom
    assert '<code id="harness-cursor">0</code>' in dom


def test_browser_rejects_progress_smuggled_in_the_compile_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _UnboundSubmittingWorkbench(journal, smuggle_events=True)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def click_compile_after_the_application_script(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    '<script>document.querySelector("#harness-compile-button").click();</script>'
                    "</body>",
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert "Harness compile response failed integrity checks" in dom
    assert "forged.progress" not in dom
    assert "data-event-id=" not in dom
    assert 'value="42345678-1234-4234-9234-123456789abc"' not in dom


def test_browser_accepts_a_committed_preflight_terminal_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _PreflightSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def click_compile_after_the_application_script(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    '<script>document.querySelector("#harness-compile-button").click();</script>'
                    "</body>",
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert "Harness Compile 已持久化为 blocked" in dom
    assert "Harness compile response failed integrity checks" not in dom
    assert "preflight" in dom


def test_browser_new_submission_invalidates_an_older_terminal_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _SecondSubmissionFailsWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def inject_two_submissions(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const button = document.querySelector('#harness-compile-button');
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      const nativeFetch = window.fetch.bind(window);
                      let releaseOldRead;
                      let oldReadDelivered = false;
                      let oldReadSeen = false;
                      const oldReadGate = new Promise((resolve) => { releaseOldRead = resolve; });
                      window.fetch = async (...args) => {
                        const response = await nativeFetch(...args);
                        const url = String(args[0]);
                        if (url.includes('/api/harness/events?')
                            && url.includes('run_id=32345678-1234-4234-9234-123456789abc')) {
                          oldReadSeen = true;
                          await oldReadGate;
                          oldReadDelivered = true;
                        }
                        return response;
                      };
                      button.click();
                      while (button.disabled || !oldReadSeen) await pause();
                      button.click();
                      while (button.disabled) await pause();
                      releaseOldRead();
                      while (!oldReadDelivered) await pause();
                      await pause();
                      document.body.dataset.raceComplete = 'true';
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert 'data-race-complete="true"' in dom
    assert "Harness compile is unavailable" in dom
    assert "Harness Compile 已持久化为 succeeded" not in dom


def test_browser_retries_a_transient_terminal_read_without_losing_the_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    failed_once = False

    @app.before_request
    def fail_the_first_terminal_read():
        nonlocal failed_once
        if (
            request.path == "/api/harness/events"
            and request.args.get("run_id") == str(workbench.run_id)
            and not failed_once
        ):
            failed_once = True
            return (
                jsonify(
                    {
                        "error": {
                            "code": "temporary_harness_failure",
                            "message": "Temporary event service failure",
                        }
                    }
                ),
                503,
            )
        return None

    @app.after_request
    def click_compile_after_the_application_script(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    '<script>document.querySelector("#harness-compile-button").click();</script>'
                    "</body>",
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(
            url,
            tmp_path / "chrome-profile",
            virtual_time_budget_ms=7_500,
        )

    assert failed_once is True
    assert "Harness Compile 已持久化为 succeeded" in dom
    assert "Temporary event service failure" not in dom


def test_browser_advances_only_after_a_new_event_is_committed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    recorder = RunRecorder(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    )
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))
    first_empty_page_returned = threading.Event()

    @app.after_request
    def signal_first_empty_event_page(response):
        if (
            request.path == "/api/harness/events"
            and response.status_code == 200
            and response.get_json()["events"] == []
        ):
            first_empty_page_returned.set()
        return response

    def publish_after_the_empty_page() -> None:
        assert first_empty_page_returned.wait(timeout=5)
        recorder.start(stage="replay.simulation.started", attempt=1)

    publisher = threading.Thread(target=publish_after_the_empty_page)
    publisher.start()
    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")
    publisher.join(timeout=5)

    assert not publisher.is_alive()
    assert 'data-event-id="1"' in dom
    assert "replay.simulation.started" in dom


def test_browser_reloads_from_its_saved_cursor_without_duplicate_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    recorder = RunRecorder(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    )
    recorder.start(stage="invoke.started", attempt=1)
    recorder.progress(stage="compile.parse.started")
    recorder.finish(status=RunStatus.SUCCEEDED, stage="invoke.succeeded")
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))
    observed_cursors: list[str] = []

    @app.before_request
    def record_event_cursor():
        if request.path == "/api/harness/events":
            observed_cursors.append(request.args["after"])

    with _served(app) as url:
        profile = tmp_path / "chrome-profile"
        first_dom = _rendered_dom(url, profile)
        first_render_request_count = len(observed_cursors)
        second_dom = _rendered_dom(url, profile)

    assert observed_cursors[0] == "0"
    assert observed_cursors[first_render_request_count : first_render_request_count + 2] == [
        "0",
        "3",
    ]
    for event_id in (1, 2, 3):
        assert first_dom.count(f'data-event-id="{event_id}"') == 1
        assert second_dom.count(f'data-event-id="{event_id}"') == 1


def test_browser_filters_the_event_feed_to_one_run(tmp_path: Path, monkeypatch) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    compile_run = UUID("12345678-1234-4234-9234-123456789abc")
    replay_run = UUID("22345678-1234-4234-9234-123456789abc")
    RunRecorder(
        run_id=compile_run,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="compile.started", attempt=1)
    RunRecorder(
        run_id=replay_run,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="replay.started", attempt=1)
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))
    observed_run_ids: list[str | None] = []

    @app.before_request
    def record_event_run_filter():
        if request.path == "/api/harness/events":
            observed_run_ids.append(request.args.get("run_id"))

    with _served(app) as url:
        dom = _rendered_dom(
            f"{url}?harness_run={replay_run}",
            tmp_path / "chrome-profile",
        )

    assert observed_run_ids[0] == str(replay_run)
    assert 'data-event-id="1"' not in dom
    assert 'data-event-id="2"' in dom
    assert "compile.started" not in dom
    assert "replay.started" in dom
    assert f'value="{replay_run}"' in dom


def test_browser_marks_corrupt_event_history_without_rendering_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal_path = tmp_path / "harness.sqlite3"
    journal = SQLiteEventJournal(journal_path)
    RunRecorder(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="compile.started", attempt=1)
    with closing(sqlite3.connect(journal_path)) as connection, connection:
        connection.execute(
            "UPDATE run_events SET envelope_json = ? WHERE event_id = 1",
            ('{"broken":true}',),
        )
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))
    event_request_count = 0

    @app.before_request
    def count_corrupt_event_requests():
        nonlocal event_request_count
        if request.path == "/api/harness/events":
            event_request_count += 1

    with _served(app) as url:
        dom = _rendered_dom(
            url,
            tmp_path / "chrome-profile",
            virtual_time_budget_ms=6500,
        )

    assert ">历史损坏</span>" in dom
    assert "Harness event history failed integrity checks" in dom
    assert 'data-event-id="1"' not in dom
    assert event_request_count == 1


def test_browser_marks_an_unconfigured_feed_as_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _configured_app(tmp_path, monkeypatch, None)

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert ">未配置</span>" in dom
    assert "Harness event feed is not configured" in dom
    assert "data-event-id=" not in dom


def test_browser_still_reads_the_journal_when_local_storage_is_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    RunRecorder(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="compile.started", attempt=1)
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))

    with _served(app) as url:
        dom = _rendered_dom(
            url,
            tmp_path / "chrome-profile",
            chrome_args=("--disable-local-storage",),
        )

    assert 'data-event-id="1"' in dom
    assert ">已连接</span>" in dom


def test_browser_rejects_an_inconsistent_event_page(tmp_path: Path, monkeypatch) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    RunRecorder(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="compile.started", attempt=1)
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))

    @app.after_request
    def corrupt_the_wire_cursor(response):
        if request.path == "/api/harness/events" and response.status_code == 200:
            payload = response.get_json()
            payload["last_event_id"] = 999
            response.set_data(json.dumps(payload))
            response.content_type = "application/json"
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert ">响应损坏</span>" in dom
    assert 'data-event-id="1"' not in dom
    assert '<code id="harness-cursor">0</code>' in dom


def test_browser_revalidates_cached_events_before_rendering_them(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal_path = tmp_path / "harness.sqlite3"
    journal = SQLiteEventJournal(journal_path)
    RunRecorder(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="compile.started", attempt=1)
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))

    with _served(app) as url:
        profile = tmp_path / "chrome-profile"
        first_dom = _rendered_dom(url, profile)
        with closing(sqlite3.connect(journal_path)) as connection, connection:
            connection.execute(
                "UPDATE run_events SET envelope_json = ? WHERE event_id = 1",
                ('{"broken":true}',),
            )
        second_dom = _rendered_dom(url, profile)

    assert 'data-event-id="1"' in first_dom
    assert ">历史损坏</span>" in second_dom
    assert 'data-event-id="1"' not in second_dom


def test_browser_does_not_treat_an_empty_cache_as_authoritative_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))

    with _served(app) as url:
        profile = tmp_path / "chrome-profile"
        first_dom = _rendered_dom(url, profile)
        RunRecorder(
            run_id=UUID("12345678-1234-4234-9234-123456789abc"),
            skill_id="text2env.compile",
            skill_version="1.0.0",
            clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
            sink=journal,
        ).start(stage="compile.started", attempt=1)
        second_dom = _rendered_dom(url, profile)

    assert "data-event-id=" not in first_dom
    assert 'data-event-id="1"' in second_dom
    assert ">已连接</span>" in second_dom
    assert ">响应损坏</span>" not in second_dom


def test_browser_ignores_a_late_page_from_the_previous_run_filter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    compile_run = UUID("12345678-1234-4234-9234-123456789abc")
    replay_run = UUID("22345678-1234-4234-9234-123456789abc")
    RunRecorder(
        run_id=compile_run,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="compile.started", attempt=1)
    RunRecorder(
        run_id=replay_run,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="replay.started", attempt=1)
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))
    filtered_request_started = threading.Event()
    all_runs_response_ready = threading.Event()
    observed_event_queries: list[tuple[str | None, str]] = []

    @app.before_request
    def delay_the_first_filtered_page():
        if request.path != "/api/harness/events":
            return
        observed_event_queries.append((request.args.get("run_id"), request.args.get("after", "")))
        if request.args.get("run_id") == str(compile_run):
            filtered_request_started.set()
            assert all_runs_response_ready.wait(timeout=5)
            time.sleep(0.1)

    @app.after_request
    def switch_to_all_runs_while_the_filtered_page_is_in_flight(response):
        if request.path == "/api/harness/events" and request.args.get("run_id") is None:
            all_runs_response_ready.set()
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            body = response.get_data(as_text=True)
            response.set_data(
                body.replace(
                    "</body>",
                    "<script>selectHarnessRun('');</script></body>",
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(
            f"{url}?harness_run={compile_run}",
            tmp_path / "chrome-profile",
        )

    assert filtered_request_started.is_set()
    assert dom.count('data-event-id="1"') == 1
    assert dom.count('data-event-id="2"') == 1
    assert '<code id="harness-cursor">2</code>' in dom
    assert (str(compile_run), "0") in observed_event_queries
    assert (None, "0") in observed_event_queries
    assert (None, "1") not in observed_event_queries
    assert (None, "2") in observed_event_queries


def test_browser_preserves_event_cursors_above_javascript_safe_integer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal_path = tmp_path / "harness.sqlite3"
    journal = SQLiteEventJournal(journal_path)
    RunRecorder(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="compile.started", attempt=1)
    with closing(sqlite3.connect(journal_path)) as connection, connection:
        connection.execute(
            "UPDATE sqlite_sequence SET seq = ? WHERE name = 'run_events'",
            (2**53,),
        )
    RunRecorder(
        run_id=UUID("22345678-1234-4234-9234-123456789abc"),
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="replay.started", attempt=1)
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert 'data-event-id="9007199254740993"' in dom
    assert 'data-event-id="9007199254740992"' not in dom
    assert '<code id="harness-cursor">9007199254740993</code>' in dom
    assert ">响应损坏</span>" not in dom
