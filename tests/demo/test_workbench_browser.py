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
from flask import request
from werkzeug.serving import make_server

from demo.app import create_app
from demo.harness_feed import HarnessEventFeed
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.events import RunRecorder
from self_improving.harness.schemas import RunStatus


def _configured_app(
    tmp_path: Path,
    monkeypatch,
    feed: HarnessEventFeed | None,
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
    return create_app(config)


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


def test_browser_groups_committed_compile_replay_and_validate_run_activity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    compile_run = UUID("12345678-1234-4234-9234-123456789abc")
    replay_run = UUID("22345678-1234-4234-9234-123456789abc")
    validate_run = UUID("32345678-1234-4234-9234-123456789abc")

    def clock() -> datetime:
        return datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)

    RunRecorder(
        run_id=compile_run,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=clock,
        sink=journal,
    ).start(stage="compile.started", attempt=1)
    replay = RunRecorder(
        run_id=replay_run,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=clock,
        sink=journal,
    )
    replay.start(stage="replay.started", attempt=1)
    RunRecorder(
        run_id=validate_run,
        skill_id="text2env.validate",
        skill_version="1.0.0",
        clock=clock,
        sink=journal,
    ).start(stage="validate.started", attempt=1)
    replay.progress(stage="replay.artifacts.published")
    replay.finish(status=RunStatus.SUCCEEDED, stage="replay.succeeded")

    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))
    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert 'id="harness-run-board"' in dom
    assert 'data-run-lane="compile"' in dom
    assert 'data-run-lane="replay"' in dom
    assert 'data-run-lane="validate"' in dom
    assert dom.count(f'data-run-id="{compile_run}"') == 1
    assert dom.count(f'data-run-id="{replay_run}"') == 1
    assert dom.count(f'data-run-id="{validate_run}"') == 1
    assert (
        f'data-run-id="{replay_run}" data-latest-event-id="5" data-visible-event-count="3"'
    ) in dom
    assert "text2env.replay@1.0.0" in dom
    assert "replay.succeeded" in dom
    assert "3 个可见事件" in dom


def test_browser_run_activity_card_selects_and_replays_that_committed_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    replay_run = UUID("22345678-1234-4234-9234-123456789abc")
    RunRecorder(
        run_id=replay_run,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="replay.started", attempt=1)
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))
    observed_run_filters: list[str | None] = []

    @app.before_request
    def observe_event_filters():
        if request.path == "/api/harness/events":
            observed_run_filters.append(request.args.get("run_id"))

    @app.after_request
    def click_the_replay_card(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    f"""
                    <script>
                    (async () => {{
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      let card;
                      while (!(card = document.querySelector(
                        '[data-run-id="{replay_run}"]'
                      ))) await pause();
                      card.focus();
                      card.click();
                      while (!(card = document.querySelector(
                        '[data-run-id="{replay_run}"][aria-current="true"]'
                      ))) await pause();
                      document.body.dataset.runCardSearch = window.location.search;
                      document.body.dataset.runCardSelected = 'true';
                      document.body.dataset.runCardFocusPreserved = String(
                        document.activeElement === card
                      );
                      document.body.dataset.runCardFocusOutline = getComputedStyle(
                        card
                      ).outlineStyle;
                    }})();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert observed_run_filters[0] is None
    assert str(replay_run) in observed_run_filters
    assert f'data-run-card-search="?harness_run={replay_run}"' in dom
    assert 'data-run-card-selected="true"' in dom
    assert 'data-run-card-focus-preserved="true"' in dom
    assert 'data-run-card-focus-outline="solid"' in dom
    assert (
        f'data-run-id="{replay_run}" data-latest-event-id="1" '
        'data-visible-event-count="1" aria-current="true"'
    ) in dom


def test_browser_run_activity_preserves_focus_without_stealing_it_after_blur(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    replay_run = UUID("22345678-1234-4234-9234-123456789abc")
    RunRecorder(
        run_id=replay_run,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="replay.started", attempt=1)
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))

    @app.after_request
    def focus_the_replay_card_across_one_render(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    f"""
                    <script>
                    (() => {{
                      try {{
                        const fixture = document.createElement('section');
                        ['compile', 'replay', 'validate', 'other'].forEach((lane) => {{
                          const list = document.createElement('ol');
                          list.dataset.runLaneList = lane;
                          fixture.appendChild(list);
                        }});
                        document.body.appendChild(fixture);
                        const board = window.HarnessRunBoard.mount({{
                          root: fixture,
                          onInspect: () => {{}},
                        }});
                        const replayEvent = {{
                            event_id: '1',
                            run_id: '{replay_run}',
                            skill_id: 'text2env.replay',
                            skill_version: '1.0.0',
                            event: {{
                              stage: 'replay.started',
                              to_status: 'running',
                            }},
                          }};
                        const compileEvent = {{
                          event_id: '2',
                          run_id: '12345678-1234-4234-9234-123456789abc',
                          skill_id: 'text2env.compile',
                          skill_version: '1.0.0',
                          event: {{
                            stage: 'compile.started',
                            to_status: 'running',
                          }},
                        }};
                        const view = {{
                          events: [replayEvent, compileEvent],
                          selectedRunId: '',
                        }};
                        board.render(view);
                        const card = fixture.querySelector('[data-run-id="{replay_run}"]');
                        card.focus();
                        const compileProgress = JSON.parse(JSON.stringify(compileEvent));
                        compileProgress.event_id = '3';
                        compileProgress.event.stage = 'compile.progress';
                        board.render({{
                          events: [replayEvent, compileEvent, compileProgress],
                          selectedRunId: '',
                        }});
                        const current = fixture.querySelector('[data-run-id="{replay_run}"]');
                        document.body.dataset.runCardNodePreserved = String(current === card);
                        document.body.dataset.runCardFocusPreserved = String(
                          document.activeElement === card
                        );
                        card.blur();
                        const compileMoreProgress = JSON.parse(
                          JSON.stringify(compileProgress)
                        );
                        compileMoreProgress.event_id = '4';
                        compileMoreProgress.event.stage = 'compile.more_progress';
                        board.render({{
                          events: [
                            replayEvent,
                            compileEvent,
                            compileProgress,
                            compileMoreProgress,
                          ],
                          selectedRunId: '',
                        }});
                        document.body.dataset.runCardBlurPreserved = String(
                          document.activeElement !== card
                        );
                      }} catch (error) {{
                        document.body.dataset.runCardError = String(error);
                      }}
                    }})();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(
            url,
            tmp_path / "chrome-profile",
            virtual_time_budget_ms=2_500,
        )

    body_tag = dom[dom.index("<body") : dom.index(">", dom.index("<body"))]
    assert "data-run-card-error" not in body_tag, body_tag
    assert 'data-run-card-node-preserved="true"' in body_tag, body_tag
    assert 'data-run-card-focus-preserved="true"' in body_tag, body_tag
    assert 'data-run-card-blur-preserved="true"' in body_tag, body_tag


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


def test_browser_run_activity_fails_closed_when_one_run_changes_skill_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    run_id = UUID("12345678-1234-4234-9234-123456789abc")
    recorder = RunRecorder(
        run_id=run_id,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    )
    recorder.start(stage="compile.started", attempt=1)
    recorder.progress(stage="compile.parsed")
    app = _configured_app(tmp_path, monkeypatch, HarnessEventFeed.from_journal(journal))

    @app.after_request
    def change_the_second_visible_skill(response):
        if request.path == "/api/harness/events" and response.status_code == 200:
            payload = response.get_json()
            if len(payload["events"]) == 2:
                payload["events"][1]["skill_id"] = "text2env.validate"
                response.set_data(json.dumps(payload))
                response.content_type = "application/json"
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert ">响应损坏</span>" in dom
    assert f'data-run-id="{run_id}"' not in dom
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
    assert 'data-run-id="12345678-1234-4234-9234-123456789abc"' in first_dom
    assert ">历史损坏</span>" in second_dom
    assert 'data-event-id="1"' not in second_dom
    assert 'data-run-id="12345678-1234-4234-9234-123456789abc"' not in second_dom


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
