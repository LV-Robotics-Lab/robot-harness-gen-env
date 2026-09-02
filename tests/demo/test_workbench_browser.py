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
from self_improving.harness import ArtifactRef, RunRecorder, RunStatus, SQLiteEventJournal


def _compile_dependency_rows() -> list[dict[str, str]]:
    return [
        {"name": "asset-library-state", "version": "1", "sha256": "b" * 64},
        {"name": "catalog-selected-assets", "version": "1", "sha256": "c" * 64},
        {"name": "ledger-contract", "version": "1", "sha256": "d" * 64},
        {"name": "scene-gen", "version": "0.1.0", "sha256": "e" * 64},
        {"name": "text2env-compile-config", "version": "1", "sha256": "f" * 64},
    ]


def _compile_artifact_rows(marker: str | None = None) -> list[dict[str, object]]:
    rows = [
        {
            "name": "scene_spec",
            "media_type": "application/json",
            "schema_version": "robotwin.scene_spec.v1",
            "sha256": "1" * 64,
            "bytes": "975",
            "bindings": [{"direction": "output", "role": "scene_spec"}],
        },
        {
            "name": "resolved_scene",
            "media_type": "application/json",
            "schema_version": "robotwin.resolved_scene.v1",
            "sha256": "2" * 64,
            "bytes": "3663",
            "bindings": [{"direction": "output", "role": "resolved_scene"}],
        },
        {
            "name": "effective_asset_catalog",
            "media_type": "application/json",
            "schema_version": "robotwin.asset_catalog.v1",
            "sha256": "3" * 64,
            "bytes": "1528",
            "bindings": [
                {"direction": "input", "role": "asset_catalog"},
                {"direction": "output", "role": "environment_package.asset_catalog"},
            ],
        },
        {
            "name": "package_manifest",
            "media_type": "application/json",
            "schema_version": "robotwin.generated_scene_package.v1",
            "sha256": "4" * 64,
            "bytes": "1240",
            "bindings": [{"direction": "output", "role": "environment_package.package_manifest"}],
        },
        {
            "name": "validation_report",
            "media_type": "application/json",
            "schema_version": "robotwin.scene_validation.v1",
            "sha256": "5" * 64,
            "bytes": "3885",
            "bindings": [{"direction": "output", "role": "static_validation"}],
        },
        {
            "name": "request",
            "media_type": "text/plain",
            "schema_version": None,
            "sha256": "6" * 64,
            "bytes": "48",
            "bindings": [],
        },
    ]
    if marker is not None:
        first = int(marker[0], 16)
        for index, row in enumerate(rows):
            row["sha256"] = f"{(first + index) % 16:x}" * 64
    return rows


def _compile_artifact_refs(marker: str | None = None) -> tuple[ArtifactRef, ...]:
    return tuple(
        ArtifactRef(
            name=row["name"],
            uri=f"artifact://sha256/{row['sha256']}",
            media_type=row["media_type"],
            schema_version=row["schema_version"],
            sha256=row["sha256"],
            bytes=int(row["bytes"]),
        )
        for row in _compile_artifact_rows(marker)
    )


def _scene_preview_payload(
    *,
    run_id: UUID,
    event_count: int,
    terminal_event_id: str,
    invocation_digest: str = "a" * 64,
    scene_id: str = "stacked_can_preview",
) -> dict[str, object]:
    return {
        "schema_version": "harness.workbench_compile_scene_preview.v1",
        "run": {
            "run_id": str(run_id),
            "invocation_digest": invocation_digest,
            "event_count": event_count,
            "terminal_event_id": terminal_event_id,
        },
        "artifact": _compile_artifact_rows()[0],
        "scene": {
            "scene_id": scene_id,
            "language": "en",
            "frame": {
                "name": "robotwin_world",
                "x_axis": "right",
                "y_axis": "front",
                "z_axis": "up",
                "handedness": "right_handed",
            },
            "unit": "m",
            "seed": 42,
            "workspace": {
                "support_surface": "table",
                "table_height_m": 0.741,
                "x_bounds_m": [-0.35, 0.35],
                "y_bounds_m": [-0.2, 0.3],
                "robot_keepout_x_m": [-0.16, 0.16],
                "robot_keepout_y_m": [-0.2, -0.08],
            },
            "objects": [
                {
                    "object_id": "plate",
                    "category": "plate",
                    "color": None,
                    "material": None,
                    "region": "center",
                    "articulation": None,
                },
                {
                    "object_id": "can",
                    "category": "can",
                    "color": "red",
                    "material": None,
                    "region": "center",
                    "articulation": None,
                },
            ],
            "relations": [
                {
                    "relation": "on_table",
                    "source": "plate",
                    "target": "table",
                    "max_distance_m": None,
                    "min_distance_m": None,
                },
                {
                    "relation": "on_top_of",
                    "source": "can",
                    "target": "plate",
                    "max_distance_m": None,
                    "min_distance_m": None,
                },
            ],
        },
    }


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
        self.audit_runs: list[UUID] = []
        self.preview_runs: list[UUID] = []
        self.static_validation_preview_runs: list[UUID] = []

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
        recorder.finish(
            status=RunStatus.SUCCEEDED,
            stage="invoke.succeeded",
            artifact_refs=_compile_artifact_refs(),
        )
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

    def audit(self, *, run_id: UUID):
        assert run_id == self.run_id
        self.audit_runs.append(run_id)
        page = self.page(run_id=run_id, limit=500)
        events = page["events"]
        return {
            "schema_version": "harness.workbench_compile_audit.v2",
            "run": {
                "run_id": str(run_id),
                "skill_id": "text2env.compile",
                "skill_version": "1.0.0",
                "status": "succeeded",
                "attempt": 1,
                "max_attempts": 1,
                "started_at": events[0]["event"]["timestamp"],
                "ended_at": events[-1]["event"]["timestamp"],
                "event_count": len(events),
                "terminal_event_id": page["last_event_id"],
                "blocker": None,
            },
            "invocation": {
                "status": "bound",
                "digest": "a" * 64,
                "dependencies": _compile_dependency_rows(),
            },
            "artifacts": _compile_artifact_rows(),
        }

    def scene_preview(self, *, run_id: UUID):
        assert run_id == self.run_id
        self.preview_runs.append(run_id)
        page = self.page(run_id=run_id, limit=500)
        return _scene_preview_payload(
            run_id=run_id,
            event_count=len(page["events"]),
            terminal_event_id=page["last_event_id"],
        )

    def static_validation_preview(self, *, run_id: UUID):
        assert run_id == self.run_id
        self.static_validation_preview_runs.append(run_id)
        page = self.page(run_id=run_id, limit=500)
        return {
            "schema_version": "harness.workbench_compile_static_validation_preview.v1",
            "run": {
                "run_id": str(run_id),
                "invocation_digest": "a" * 64,
                "event_count": len(page["events"]),
                "terminal_event_id": page["last_event_id"],
            },
            "artifact": _compile_artifact_rows()[4],
            "validation": {
                "claim_scope": "committed_report_content_and_binding_only",
                "mode": "compile_static_without_runtime_evidence",
                "scene_id": "stacked_can_preview",
                "resolved_scene_sha256": "9" * 64,
                "status": "incomplete",
                "counts": {"checks": 7, "pass": 6, "fail": 0, "not_run": 1},
                "checks": [
                    {"name": "workspace_bounds:plate", "status": "pass"},
                    {"name": "table_support_height:plate", "status": "pass"},
                    {"name": "real_asset_files:plate", "status": "pass"},
                    {"name": "relation:on_table:plate", "status": "pass"},
                    {"name": "resolved_only_roundtrip", "status": "pass"},
                    {"name": "package_manifest", "status": "pass"},
                    {"name": "runtime_evidence", "status": "not_run"},
                ],
            },
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
        self.audit_runs: list[UUID] = []

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

    def audit(self, *, run_id: UUID):
        assert run_id == self.run_id
        self.audit_runs.append(run_id)
        page = self.page(run_id=run_id, limit=500)
        events = page["events"]
        return {
            "schema_version": "harness.workbench_compile_audit.v2",
            "run": {
                "run_id": str(run_id),
                "skill_id": "text2env.compile",
                "skill_version": "1.0.0",
                "status": "blocked",
                "attempt": 0,
                "max_attempts": 0,
                "started_at": events[0]["event"]["timestamp"],
                "ended_at": events[-1]["event"]["timestamp"],
                "event_count": len(events),
                "terminal_event_id": page["last_event_id"],
                "blocker": {"code": "HARN_DEPENDENCY_UNAVAILABLE", "retryable": False},
            },
            "invocation": {
                "status": "not_created_preflight",
                "digest": None,
                "dependencies": [],
            },
            "artifacts": [],
        }


class _SecondSubmissionFailsWorkbench(_JournalSubmittingWorkbench):
    def submit(self, *, request: object, seed: object):
        if self.submissions:
            raise WorkbenchCompileUnavailableError("second submission unavailable")
        return super().submit(request=request, seed=seed)


class _ReadonlyAuditWorkbench:
    def __init__(self, journal: SQLiteEventJournal, digests: dict[UUID, str]) -> None:
        self.feed = HarnessEventFeed.from_journal(journal)
        self.digests = digests
        self.audit_runs: list[UUID] = []

    def page(self, **kwargs):
        page = self.feed.page(**kwargs)
        run_id = kwargs.get("run_id")
        if run_id in self.digests and page["events"]:
            terminal = page["events"][-1]["event"]
            if terminal["to_status"] == "succeeded":
                terminal["artifact_refs"] = [
                    ref.model_dump(mode="json")
                    for ref in _compile_artifact_refs(self.digests[run_id])
                ]
        return page

    def audit(self, *, run_id: UUID):
        self.audit_runs.append(run_id)
        page = self.page(run_id=run_id, limit=500)
        events = page["events"]
        terminal = events[-1]["event"]
        return {
            "schema_version": "harness.workbench_compile_audit.v2",
            "run": {
                "run_id": str(run_id),
                "skill_id": "text2env.compile",
                "skill_version": "1.0.0",
                "status": terminal["to_status"],
                "attempt": terminal["attempt"],
                "max_attempts": 1,
                "started_at": events[0]["event"]["timestamp"],
                "ended_at": terminal["timestamp"],
                "event_count": len(events),
                "terminal_event_id": page["last_event_id"],
                "blocker": None,
            },
            "invocation": {
                "status": "bound",
                "digest": self.digests[run_id],
                "dependencies": _compile_dependency_rows(),
            },
            "artifacts": _compile_artifact_rows(self.digests[run_id]),
        }


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


def test_browser_renders_dependency_and_artifact_audit_after_the_terminal_journal_matches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    observed_paths: list[str] = []

    @app.before_request
    def observe_audit_request():
        observed_paths.append(request.path)

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

    audit_path = f"/api/harness/compile-runs/{workbench.run_id}/audit"
    assert workbench.audit_runs == [workbench.run_id]
    assert audit_path in observed_paths
    audit_index = observed_paths.index(audit_path)
    assert any(
        path == "/api/harness/events"
        for path in observed_paths[observed_paths.index("/api/harness/compile") + 1 : audit_index]
    )
    assert 'id="harness-audit-panel"' in dom
    assert 'id="harness-audit-panel"' in dom and 'id="harness-audit-panel" hidden' not in dom
    assert "asset-library-state@1" in dom
    assert "ledger-contract@1" in dom
    assert "scene-gen@0.1.0" in dom
    assert "a" * 64 in dom
    assert "b" * 64 in dom
    assert "c" * 64 in dom
    assert "scene_spec" in dom
    assert "robotwin.scene_spec.v1" in dom
    assert "application/json" in dom
    assert "975 bytes" in dom
    assert "1" * 64 in dom
    assert "output · scene_spec" in dom
    assert "artifact://" not in dom
    assert "operations" not in dom


def test_browser_offers_scene_preview_after_audit_without_fetching_it_automatically(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    preview_paths: list[str] = []

    @app.before_request
    def observe_preview_requests():
        if request.path.endswith("/scene-preview"):
            preview_paths.append(request.path)

    @app.after_request
    def compile_and_wait_for_the_preview_offer(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const preview = document.querySelector('#harness-scene-preview-button');
                      while (preview.hidden) await pause();
                      document.body.dataset.scenePreviewOffered = 'true';
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=4_000)

    assert 'data-scene-preview-offered="true"' in dom
    assert 'id="harness-scene-preview-button"' in dom
    assert 'aria-expanded="false"' in dom
    assert preview_paths == []
    assert workbench.preview_runs == []


def test_browser_offers_static_validation_preview_without_fetching_it_automatically(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    preview_paths: list[str] = []

    @app.before_request
    def observe_static_validation_preview_requests():
        if request.path.endswith("/static-validation-preview"):
            preview_paths.append(request.path)

    @app.after_request
    def compile_and_wait_for_the_static_validation_offer(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const preview = document.querySelector(
                        '#harness-static-validation-button'
                      );
                      while (!preview || preview.hidden) await pause();
                      document.body.dataset.staticValidationOffered = 'true';
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=4_000)

    assert 'data-static-validation-offered="true"' in dom
    assert 'id="harness-static-validation-button"' in dom
    assert 'aria-expanded="false"' in dom
    assert preview_paths == []
    assert workbench.static_validation_preview_runs == []


def test_browser_renders_an_accessible_incomplete_static_validation_preview_after_click(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_and_open_the_static_validation_preview(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector(
                        '#harness-static-validation-button'
                      );
                      while (button.hidden) await pause();
                      button.click();
                      const panel = document.querySelector(
                        '#harness-static-validation-panel'
                      );
                      while (panel.hidden || button.disabled) await pause();
                      document.body.dataset.staticValidationRendered = 'true';
                      document.body.dataset.staticValidationFocused = String(
                        document.activeElement
                          === document.querySelector('#harness-static-validation-title')
                      );
                      document.body.dataset.staticValidationAccessible = String(
                        button.getAttribute('aria-expanded') === 'true'
                          && panel.getAttribute('aria-busy') === 'false'
                          && panel.getAttribute('aria-describedby')
                            === 'harness-static-validation-boundary'
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=4_000)

    assert workbench.static_validation_preview_runs == [workbench.run_id]
    assert 'data-static-validation-rendered="true"' in dom
    assert 'data-static-validation-focused="true"' in dom
    assert 'data-static-validation-accessible="true"' in dom
    assert 'id="harness-static-validation-panel"' in dom
    assert 'id="harness-static-validation-panel" hidden' not in dom
    assert "stacked_can_preview" in dom
    assert "workspace_bounds:plate" in dom
    assert "通过" in dom
    assert "runtime_evidence" in dom
    assert "未运行" in dom
    assert "incomplete" in dom
    assert "未重跑 validator" in dom
    assert "未使用物理回放" in dom
    assert "不作发布判断" in dom
    assert '"evidence"' not in dom
    assert "artifact://" not in dom


def test_browser_accepts_the_full_server_valid_static_validation_check_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    original_preview = workbench.static_validation_preview

    def full_budget_preview(*, run_id: UUID):
        preview = original_preview(run_id=run_id)
        additional = [
            {"name": f"future_check_{index:03d}", "status": "pass"} for index in range(166)
        ]
        preview["validation"]["counts"] = {
            "checks": 169,
            "pass": 168,
            "fail": 0,
            "not_run": 1,
        }
        preview["validation"]["checks"] = [
            {"name": "runtime_evidence", "status": "not_run"},
            *additional[:83],
            {"name": "package_manifest", "status": "pass"},
            *additional[83:],
            {"name": "resolved_only_roundtrip", "status": "pass"},
        ]
        return preview

    monkeypatch.setattr(workbench, "static_validation_preview", full_budget_preview)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_and_open_the_full_budget_static_validation(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector(
                        '#harness-static-validation-button'
                      );
                      const panel = document.querySelector(
                        '#harness-static-validation-panel'
                      );
                      const checks = document.querySelector(
                        '#harness-static-validation-checks'
                      );
                      while (button.hidden) await pause();
                      button.click();
                      while (button.disabled) await pause();
                      const names = [...checks.querySelectorAll('strong')]
                        .map((element) => element.textContent);
                      document.body.dataset.fullBudgetStaticValidationAccepted = String(
                        !panel.hidden
                          && checks.childElementCount === 169
                          && names[0] === 'runtime_evidence'
                          && names[84] === 'package_manifest'
                          && names[168] === 'resolved_only_roundtrip'
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=5_000)

    assert workbench.static_validation_preview_runs == [workbench.run_id]
    assert 'data-full-budget-static-validation-accepted="true"' in dom
    assert "future_check_165" in dom
    assert "编译期检查预览无法验证。" not in dom


def test_browser_renders_a_failed_committed_static_validation_without_overclaiming(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    original_preview = workbench.static_validation_preview

    def failed_preview(*, run_id: UUID):
        preview = original_preview(run_id=run_id)
        preview["validation"]["status"] = "fail"
        preview["validation"]["counts"] = {
            "checks": 7,
            "pass": 5,
            "fail": 1,
            "not_run": 1,
        }
        preview["validation"]["checks"][0]["status"] = "fail"
        return preview

    monkeypatch.setattr(workbench, "static_validation_preview", failed_preview)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_and_open_the_failed_static_validation(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector(
                        '#harness-static-validation-button'
                      );
                      while (button.hidden) await pause();
                      button.click();
                      const panel = document.querySelector(
                        '#harness-static-validation-panel'
                      );
                      while (panel.hidden || button.disabled) await pause();
                      document.body.dataset.staticValidationFailRendered = String(
                        panel.textContent.includes('fail')
                          && panel.textContent.includes('失败')
                          && panel.textContent.includes('workspace_bounds:plate')
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=4_000)

    assert workbench.static_validation_preview_runs == [workbench.run_id]
    assert 'data-static-validation-fail-rendered="true"' in dom
    assert "检测到 1 项失败" in dom
    assert "报告内容身份、结构及与当前审计的绑定已核对" in dom
    assert "未重跑 validator" in dom
    assert "未执行物理回放" in dom
    assert "不作发布判断" in dom


def test_browser_rejects_hostile_or_mismatched_static_validation_previews(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    original_preview = workbench.static_validation_preview
    hostile = (
        '</strong><img id="static-validation-xss" src="x" '
        "onerror=\"document.body.dataset.staticValidationXssExecuted='true'\">"
        "/tmp/private-validation"
    )

    def hostile_preview(*, run_id: UUID):
        preview = original_preview(run_id=run_id)
        case = len(workbench.static_validation_preview_runs) - 1
        if case == 0:
            preview["evidence"] = {"path": "/tmp/private-evidence"}
        elif case == 1:
            preview["run"]["run_id"] = "82345678-1234-4234-9234-123456789abc"
        elif case == 2:
            preview["run"]["terminal_event_id"] = "999"
        elif case == 3:
            preview["artifact"]["sha256"] = "7" * 64
        elif case == 4:
            preview["artifact"]["bindings"][0]["role"] = "scene_spec"
        elif case == 5:
            preview["validation"]["claim_scope"] = "validator_rerun"
        elif case == 6:
            preview["validation"]["mode"] = "physical_replay"
        elif case == 7:
            preview["validation"]["checks"][0]["name"] = hostile
        elif case == 8:
            preview["validation"]["checks"][1]["name"] = "workspace_bounds:plate"
        elif case == 9:
            preview["validation"]["counts"]["checks"] = True
        elif case == 10:
            preview["validation"]["counts"]["pass"] = 5
        elif case == 11:
            preview["validation"]["checks"][5]["name"] = "package_manifest_v2"
        elif case == 12:
            preview["validation"]["checks"] = [
                {"name": f"check_{index}", "status": "pass"} for index in range(170)
            ]
            preview["validation"]["counts"] = {
                "checks": 170,
                "pass": 169,
                "fail": 0,
                "not_run": 1,
            }
        elif case == 13:
            preview["validation"]["checks"][-1]["status"] = "pass"
            preview["validation"]["counts"] = {
                "checks": 7,
                "pass": 7,
                "fail": 0,
                "not_run": 0,
            }
        elif case == 14:
            preview["validation"]["resolved_scene_sha256"] = "file:///tmp/resolved"
        else:
            preview["validation"]["unexpected"] = "/tmp/private-field"
        return preview

    monkeypatch.setattr(workbench, "static_validation_preview", hostile_preview)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_and_reject_each_hostile_static_validation(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector(
                        '#harness-static-validation-button'
                      );
                      const panel = document.querySelector(
                        '#harness-static-validation-panel'
                      );
                      const message = document.querySelector(
                        '#harness-static-validation-message'
                      );
                      const summary = document.querySelector(
                        '#harness-static-validation-summary'
                      );
                      const checks = document.querySelector(
                        '#harness-static-validation-checks'
                      );
                      while (button.hidden) await pause();
                      let rejected = 0;
                      for (let index = 0; index < 16; index += 1) {
                        button.click();
                        while (button.disabled) await pause();
                        if (panel.hidden
                            && summary.childElementCount === 0
                            && checks.childElementCount === 0
                            && message.textContent === '编译期检查预览无法验证。'
                            && document.querySelector('#static-validation-xss') === null) {
                          rejected += 1;
                        }
                      }
                      document.body.dataset.hostileStaticValidationRejected = String(rejected);
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=6_000)

    assert workbench.static_validation_preview_runs == [workbench.run_id] * 16
    assert 'data-hostile-static-validation-rejected="16"' in dom
    assert "/tmp/private-validation" not in dom
    assert "/tmp/private-evidence" not in dom
    assert "data-static-validation-xss-executed" not in dom


@pytest.mark.parametrize(
    "wire_case",
    [
        "oversized",
        "wrong_content_type",
        "utf8_bom",
        "invalid_utf8",
        "invalid_json",
        "non_2xx",
    ],
)
def test_browser_static_validation_fails_closed_on_malformed_wire_responses(
    wire_case: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    preview_path = f"/api/harness/compile-runs/{workbench.run_id}/static-validation-preview"
    observed_requests: list[tuple[str, str, bytes, str | None]] = []

    @app.before_request
    def observe_static_validation_request_boundary():
        if request.path.endswith("/static-validation-preview"):
            observed_requests.append(
                (request.method, request.path, request.query_string, request.headers.get("Accept"))
            )

    @app.after_request
    def corrupt_the_static_validation_wire_response(response):
        if request.path != preview_path or response.status_code != 200:
            return response
        response.direct_passthrough = False
        valid_payload = response.get_data()
        status_code = 200
        content_type = "application/json"
        payload = valid_payload
        if wire_case == "oversized":
            payload = b'{"injected":"/tmp/private-wire","padding":"' + b"x" * 262_200 + b'"}'
        elif wire_case == "wrong_content_type":
            content_type = "text/plain; charset=utf-8"
        elif wire_case == "utf8_bom":
            payload = b"\xef\xbb\xbf" + valid_payload
        elif wire_case == "invalid_utf8":
            payload = b"\xff" + valid_payload
        elif wire_case == "invalid_json":
            payload = b'{"injected":"/tmp/private-wire"'
        else:
            status_code = 503
        response.set_data(payload)
        response.status_code = status_code
        response.headers["Content-Type"] = content_type
        return response

    @app.after_request
    def compile_and_request_the_corrupt_static_validation(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector(
                        '#harness-static-validation-button'
                      );
                      const panel = document.querySelector(
                        '#harness-static-validation-panel'
                      );
                      const summary = document.querySelector(
                        '#harness-static-validation-summary'
                      );
                      const checks = document.querySelector(
                        '#harness-static-validation-checks'
                      );
                      const message = document.querySelector(
                        '#harness-static-validation-message'
                      );
                      while (button.hidden) await pause();
                      button.click();
                      while (button.disabled) await pause();
                      document.body.dataset.malformedStaticValidationRejected = String(
                        panel.hidden
                          && summary.childElementCount === 0
                          && checks.childElementCount === 0
                          && message.textContent === '编译期检查预览无法验证。'
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=4_000)

    assert observed_requests == [("GET", preview_path, b"", "application/json")]
    assert workbench.static_validation_preview_runs == [workbench.run_id]
    assert 'data-malformed-static-validation-rejected="true"' in dom
    assert "编译期检查预览无法验证。" in dom
    assert "stacked_can_preview" not in dom
    assert "/tmp/private-wire" not in dom


def test_browser_aborts_and_discards_late_static_validation_after_filter_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    preview_started = threading.Event()
    release_preview = threading.Event()

    @app.before_request
    def delay_static_validation_preview():
        if request.path.endswith("/static-validation-preview"):
            preview_started.set()
            assert release_preview.wait(timeout=5)

    @app.get("/__test__/wait-static-validation-preview")
    def wait_for_static_validation_preview():
        assert preview_started.wait(timeout=5)
        return "", 204

    @app.get("/__test__/release-static-validation-preview")
    def release_static_validation_preview_response():
        release_preview.set()
        return "", 204

    @app.after_request
    def compile_then_change_filter_during_static_validation(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector(
                        '#harness-static-validation-button'
                      );
                      while (button.hidden) await pause();
                      button.click();
                      await fetch('/__test__/wait-static-validation-preview');
                      selectHarnessRun('');
                      await fetch('/__test__/release-static-validation-preview');
                      await pause();
                      const panel = document.querySelector(
                        '#harness-static-validation-panel'
                      );
                      const summary = document.querySelector(
                        '#harness-static-validation-summary'
                      );
                      const checks = document.querySelector(
                        '#harness-static-validation-checks'
                      );
                      document.body.dataset.staleStaticValidationDiscarded = String(
                        panel.hidden
                          && button.hidden
                          && summary.childElementCount === 0
                          && checks.childElementCount === 0
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=5_000)

    assert preview_started.is_set()
    assert release_preview.is_set()
    assert workbench.static_validation_preview_runs == [workbench.run_id]
    assert 'data-stale-static-validation-discarded="true"' in dom
    assert "stacked_can_preview" not in dom


def test_browser_close_clears_static_validation_and_reopen_refetches_without_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_open_close_and_reopen_static_validation(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector(
                        '#harness-static-validation-button'
                      );
                      const close = document.querySelector(
                        '#harness-static-validation-close'
                      );
                      const panel = document.querySelector(
                        '#harness-static-validation-panel'
                      );
                      const summary = document.querySelector(
                        '#harness-static-validation-summary'
                      );
                      const checks = document.querySelector(
                        '#harness-static-validation-checks'
                      );
                      while (button.hidden) await pause();
                      button.click();
                      while (panel.hidden || button.disabled) await pause();
                      close.click();
                      document.body.dataset.staticValidationCloseCleared = String(
                        panel.hidden
                          && summary.childElementCount === 0
                          && checks.childElementCount === 0
                          && button.getAttribute('aria-expanded') === 'false'
                      );
                      document.body.dataset.staticValidationCloseFocusedSource = String(
                        document.activeElement === button
                      );
                      button.click();
                      while (panel.hidden || button.disabled) await pause();
                      document.body.dataset.staticValidationReopened = 'true';
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=5_000)

    assert workbench.static_validation_preview_runs == [workbench.run_id, workbench.run_id]
    assert 'data-static-validation-close-cleared="true"' in dom
    assert 'data-static-validation-close-focused-source="true"' in dom
    assert 'data-static-validation-reopened="true"' in dom
    assert "stacked_can_preview" in dom


def test_browser_does_not_offer_an_oversized_static_validation_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    original_page = workbench.page
    original_audit = workbench.audit

    def oversized_page(**kwargs):
        page = original_page(**kwargs)
        for envelope in page["events"]:
            for artifact in envelope["event"]["artifact_refs"]:
                if artifact["sha256"] == "5" * 64:
                    artifact["bytes"] = 262_145
        return page

    def oversized_audit(*, run_id: UUID):
        audit = original_audit(run_id=run_id)
        audit["artifacts"][4]["bytes"] = "262145"
        return audit

    monkeypatch.setattr(workbench, "page", oversized_page)
    monkeypatch.setattr(workbench, "audit", oversized_audit)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_and_wait_for_the_oversized_static_validation_audit(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const panel = document.querySelector('#harness-audit-panel');
                      while (panel.hidden) await pause();
                      document.body.dataset.oversizedStaticValidationAuditReady = 'true';
                      document.body.dataset.oversizedStaticValidationHidden = String(
                        document.querySelector('#harness-static-validation-button').hidden
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=4_000)

    assert 'data-oversized-static-validation-audit-ready="true"' in dom
    assert 'data-oversized-static-validation-hidden="true"' in dom
    assert "262145 bytes" in dom
    assert workbench.static_validation_preview_runs == []


def test_browser_fetches_and_renders_a_verified_scene_preview_only_after_click(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_and_open_the_scene_preview(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector('#harness-scene-preview-button');
                      while (button.hidden) await pause();
                      button.click();
                      const panel = document.querySelector('#harness-scene-preview-panel');
                      while (panel.hidden || button.disabled) await pause();
                      document.body.dataset.scenePreviewRendered = 'true';
                      document.body.dataset.scenePreviewOmittedRequest = String(
                        !panel.textContent.includes('request')
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=4_000)

    assert workbench.preview_runs == [workbench.run_id]
    assert 'data-scene-preview-rendered="true"' in dom
    assert 'id="harness-scene-preview-panel"' in dom
    assert 'id="harness-scene-preview-panel"' in dom and (
        'id="harness-scene-preview-panel" hidden' not in dom
    )
    assert 'aria-expanded="true"' in dom
    assert "stacked_can_preview" in dom
    assert "plate · plate · center" in dom
    assert "can · can · red · center" in dom
    assert "can on_top_of plate" in dom
    assert "场景结构已验证；内容按字段投影显示。" in dom
    assert 'data-scene-preview-omitted-request="true"' in dom
    assert "artifact://" not in dom


def test_browser_scene_preview_rejects_hostile_text_smuggled_into_an_allowed_field(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    hostile = (
        '</dd><img id="scene-preview-xss" src="x" '
        "onerror=\"document.body.dataset.scenePreviewXssExecuted='true'\">"
        "/tmp/private-preview"
    )
    original_preview = workbench.scene_preview

    def hostile_preview(*, run_id: UUID):
        preview = original_preview(run_id=run_id)
        case = len(workbench.preview_runs) - 1
        if case == 0:
            preview["scene"]["scene_id"] = hostile
        elif case == 1:
            preview["scene"]["objects"][0]["object_id"] = "../private_plate"
        elif case == 2:
            preview["scene"]["relations"][0]["source"] = "/tmp/private_source"
        elif case == 3:
            preview["scene"]["relations"][0]["target"] = "file:///tmp/private_target"
        elif case == 4:
            preview["scene"]["objects"][0]["category"] = "<img>"
        elif case == 5:
            preview["scene"]["objects"][0]["color"] = "file:///tmp/private_color"
        else:
            preview["scene"]["objects"][0]["material"] = "red<script>"
        return preview

    monkeypatch.setattr(workbench, "scene_preview", hostile_preview)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_and_reject_the_hostile_preview(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector('#harness-scene-preview-button');
                      while (button.hidden) await pause();
                      const panel = document.querySelector('#harness-scene-preview-panel');
                      const message = document.querySelector('#harness-scene-preview-message');
                      let rejected = 0;
                      for (let index = 0; index < 7; index += 1) {
                        button.click();
                        while (button.disabled) await pause();
                        if (panel.hidden
                            && message.textContent === '场景结构预览无法验证。'
                            && document.querySelector('#scene-preview-xss') === null) {
                          rejected += 1;
                        }
                      }
                      document.body.dataset.hostilePreviewRejected = String(rejected);
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=4_000)

    assert workbench.preview_runs == [workbench.run_id] * 7
    assert 'data-hostile-preview-rejected="7"' in dom
    assert "&lt;img" not in dom
    assert "/tmp/private-preview" not in dom
    assert "data-scene-preview-xss-executed" not in dom


@pytest.mark.parametrize(
    "wire_case",
    [
        "oversized",
        "wrong_content_type",
        "utf8_bom",
        "invalid_utf8",
        "invalid_json",
        "non_2xx",
    ],
)
def test_browser_scene_preview_fails_closed_on_malformed_wire_responses(
    wire_case: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    preview_path = f"/api/harness/compile-runs/{workbench.run_id}/scene-preview"
    observed_preview_requests: list[tuple[str, str, bytes]] = []

    @app.before_request
    def observe_preview_request_boundary():
        if request.path.endswith("/scene-preview"):
            observed_preview_requests.append((request.method, request.path, request.query_string))

    @app.after_request
    def corrupt_the_scene_preview_wire_response(response):
        if request.path != preview_path or response.status_code != 200:
            return response
        response.direct_passthrough = False
        valid_payload = response.get_data()
        status_code = 200
        content_type = "application/json"
        payload = valid_payload
        if wire_case == "oversized":
            payload = b'{"injected":"/tmp/private-wire","padding":"' + b"x" * 262_200 + b'"}'
        elif wire_case == "wrong_content_type":
            content_type = "text/plain; charset=utf-8"
        elif wire_case == "utf8_bom":
            payload = b"\xef\xbb\xbf" + valid_payload
        elif wire_case == "invalid_utf8":
            payload = b"\xff" + valid_payload
        elif wire_case == "invalid_json":
            payload = b'{"injected":"/tmp/private-wire"'
        else:
            status_code = 503
        response.set_data(payload)
        response.status_code = status_code
        response.headers["Content-Type"] = content_type
        return response

    @app.after_request
    def compile_and_request_the_corrupt_preview(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector('#harness-scene-preview-button');
                      const panel = document.querySelector('#harness-scene-preview-panel');
                      const contents = document.querySelector('#harness-scene-preview-content');
                      const message = document.querySelector('#harness-scene-preview-message');
                      while (button.hidden) await pause();
                      button.click();
                      while (button.disabled) await pause();
                      document.body.dataset.malformedWireRejected = String(
                        panel.hidden
                          && contents.childElementCount === 0
                          && message.textContent === '场景结构预览无法验证。'
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=4_000)

    assert observed_preview_requests == [("GET", preview_path, b"")]
    assert workbench.preview_runs == [workbench.run_id]
    assert 'data-malformed-wire-rejected="true"' in dom
    assert "场景结构预览无法验证。" in dom
    assert "stacked_can_preview" not in dom
    assert "/tmp/private-wire" not in dom


def test_browser_rejects_scene_preview_responses_that_do_not_match_the_live_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    original_preview = workbench.scene_preview

    def mismatched_preview(*, run_id: UUID):
        preview = original_preview(run_id=run_id)
        case = len(workbench.preview_runs) - 1
        if case == 0:
            preview["artifact"]["sha256"] = "7" * 64
        elif case == 1:
            preview["run"]["run_id"] = "82345678-1234-4234-9234-123456789abc"
        elif case == 2:
            preview["run"]["terminal_event_id"] = "999"
        elif case == 3:
            preview["run"]["invocation_digest"] = "8" * 64
        else:
            preview["scene"]["raw"] = "/tmp/private-scene.json"
        return preview

    monkeypatch.setattr(workbench, "scene_preview", mismatched_preview)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_and_try_each_mismatched_preview(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector('#harness-scene-preview-button');
                      const panel = document.querySelector('#harness-scene-preview-panel');
                      const message = document.querySelector('#harness-scene-preview-message');
                      while (button.hidden) await pause();
                      let rejected = 0;
                      for (let index = 0; index < 5; index += 1) {
                        button.click();
                        while (button.disabled) await pause();
                        if (panel.hidden && message.textContent === '场景结构预览无法验证。') {
                          rejected += 1;
                        }
                      }
                      document.body.dataset.rejectedPreviewCount = String(rejected);
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=5_000)

    assert workbench.preview_runs == [workbench.run_id] * 5
    assert 'data-rejected-preview-count="5"' in dom
    assert "场景结构预览无法验证。" in dom
    assert "/tmp/private-scene.json" not in dom


def test_browser_aborts_and_discards_a_late_scene_preview_after_the_run_filter_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    preview_started = threading.Event()
    release_preview = threading.Event()

    @app.before_request
    def delay_scene_preview():
        if request.path.endswith("/scene-preview"):
            preview_started.set()
            assert release_preview.wait(timeout=5)

    @app.get("/__test__/wait-scene-preview")
    def wait_for_scene_preview():
        assert preview_started.wait(timeout=5)
        return "", 204

    @app.get("/__test__/release-scene-preview")
    def release_scene_preview_response():
        release_preview.set()
        return "", 204

    @app.after_request
    def compile_then_change_filter_during_preview(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector('#harness-scene-preview-button');
                      while (button.hidden) await pause();
                      button.click();
                      await fetch('/__test__/wait-scene-preview');
                      selectHarnessRun('');
                      await fetch('/__test__/release-scene-preview');
                      await pause();
                      const panel = document.querySelector('#harness-scene-preview-panel');
                      const contents = document.querySelector('#harness-scene-preview-content');
                      document.body.dataset.stalePreviewDiscarded = String(
                        panel.hidden && button.hidden && contents.childElementCount === 0
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=5_000)

    assert preview_started.is_set()
    assert release_preview.is_set()
    assert workbench.preview_runs == [workbench.run_id]
    assert 'data-stale-preview-discarded="true"' in dom
    assert "stacked_can_preview" not in dom


def test_browser_close_clears_scene_preview_and_reopen_refetches_without_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_open_close_and_reopen_preview(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const button = document.querySelector('#harness-scene-preview-button');
                      const close = document.querySelector('#harness-scene-preview-close');
                      const panel = document.querySelector('#harness-scene-preview-panel');
                      const contents = document.querySelector('#harness-scene-preview-content');
                      while (button.hidden) await pause();
                      button.click();
                      while (panel.hidden || button.disabled) await pause();
                      close.click();
                      document.body.dataset.previewCloseCleared = String(
                        panel.hidden && contents.childElementCount === 0
                          && button.getAttribute('aria-expanded') === 'false'
                      );
                      document.body.dataset.previewCloseFocusedSource = String(
                        document.activeElement === button
                      );
                      button.click();
                      while (panel.hidden || button.disabled) await pause();
                      document.body.dataset.previewReopened = 'true';
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=5_000)

    assert workbench.preview_runs == [workbench.run_id, workbench.run_id]
    assert 'data-preview-close-cleared="true"' in dom
    assert 'data-preview-close-focused-source="true"' in dom
    assert 'data-preview-reopened="true"' in dom
    assert "stacked_can_preview" in dom


def test_browser_does_not_offer_preview_for_an_oversized_scene_spec(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    original_page = workbench.page
    original_audit = workbench.audit

    def oversized_page(**kwargs):
        page = original_page(**kwargs)
        for envelope in page["events"]:
            for artifact in envelope["event"]["artifact_refs"]:
                if artifact["sha256"] == "1" * 64:
                    artifact["bytes"] = 65_537
        return page

    def oversized_audit(*, run_id: UUID):
        audit = original_audit(run_id=run_id)
        audit["artifacts"][0]["bytes"] = "65537"
        return audit

    monkeypatch.setattr(workbench, "page", oversized_page)
    monkeypatch.setattr(workbench, "audit", oversized_audit)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def compile_and_wait_for_the_oversized_audit(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const panel = document.querySelector('#harness-audit-panel');
                      while (panel.hidden) await pause();
                      document.body.dataset.oversizedAuditReady = 'true';
                      document.body.dataset.oversizedPreviewHidden = String(
                        document.querySelector('#harness-scene-preview-button').hidden
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile", virtual_time_budget_ms=4_000)

    assert 'data-oversized-audit-ready="true"' in dom
    assert 'data-oversized-preview-hidden="true"' in dom
    assert "65537 bytes" in dom
    assert workbench.preview_runs == []


def test_browser_accepts_an_authorized_input_alias_for_the_same_committed_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    original_page = workbench.page

    def page_with_input_alias(**kwargs):
        page = original_page(**kwargs)
        if kwargs.get("run_id") == workbench.run_id and len(page["events"]) >= 2:
            canonical = _compile_artifact_refs()[2]
            alias = canonical.model_copy(update={"name": "input_asset_catalog"})
            page["events"][1]["event"]["artifact_refs"] = [alias.model_dump(mode="json")]
        return page

    monkeypatch.setattr(workbench, "page", page_with_input_alias)
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

    assert "摘要已与完整 committed journal 对账" in dom
    assert "effective_asset_catalog" in dom
    assert "input · asset_catalog" in dom
    assert "input_asset_catalog" not in dom
    assert "终态与 committed journal 无法对账" not in dom


@pytest.mark.parametrize("view", ["all", "running_compile", "terminal_replay"])
def test_browser_never_requests_compile_audit_for_an_ineligible_view(
    view: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    compile_run = UUID("62345678-1234-4234-9234-123456789abc")
    replay_run = UUID("72345678-1234-4234-9234-123456789abc")
    RunRecorder(
        run_id=compile_run,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    ).start(stage="compile.started", attempt=1)
    replay = RunRecorder(
        run_id=replay_run,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    )
    replay.start(stage="replay.started", attempt=1)
    replay.finish(status=RunStatus.SUCCEEDED, stage="replay.succeeded")
    workbench = _ReadonlyAuditWorkbench(journal, {compile_run: "d" * 64})
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    selected = {
        "all": "",
        "running_compile": f"?harness_run={compile_run}",
        "terminal_replay": f"?harness_run={replay_run}",
    }[view]

    with _served(app) as url:
        dom = _rendered_dom(f"{url}{selected}", tmp_path / f"chrome-{view}")

    assert workbench.audit_runs == []
    assert "摘要已与完整 committed journal 对账" not in dom


def test_browser_rejects_audit_fields_that_could_smuggle_a_local_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    original_audit = workbench.audit

    def corrupt_audit(*, run_id: UUID):
        audit = original_audit(run_id=run_id)
        audit["invocation"]["dependencies"][0]["version"] = "/tmp/private-staging"
        return audit

    monkeypatch.setattr(workbench, "audit", corrupt_audit)
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

    assert "终态与 committed journal 无法对账" in dom
    assert "/tmp/private-staging" not in dom
    assert "asset-library-state@1" not in dom


@pytest.mark.parametrize(
    "mutation",
    ["locator_name", "numeric_bytes", "uri_key", "swapped_typed_roles"],
)
def test_browser_rejects_malformed_compile_artifact_metadata(
    mutation: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    original_audit = workbench.audit

    def corrupt_audit(*, run_id: UUID):
        audit = original_audit(run_id=run_id)
        artifact = audit["artifacts"][0]
        if mutation == "locator_name":
            artifact["name"] = "/tmp/private-staging"
        elif mutation == "numeric_bytes":
            artifact["bytes"] = 975
        elif mutation == "swapped_typed_roles":
            audit["artifacts"][0]["bindings"], audit["artifacts"][1]["bindings"] = (
                audit["artifacts"][1]["bindings"],
                audit["artifacts"][0]["bindings"],
            )
        else:
            artifact["uri"] = f"artifact://sha256/{artifact['sha256']}"
        return audit

    monkeypatch.setattr(workbench, "audit", corrupt_audit)
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
        dom = _rendered_dom(url, tmp_path / f"chrome-{mutation}")

    assert "终态与 committed journal 无法对账" in dom
    assert "/tmp/private-staging" not in dom
    assert "artifact://" not in dom
    assert "scene_spec" not in dom
    assert "摘要已与完整 committed journal 对账" not in dom


def test_browser_rejects_microsecond_drift_in_the_audit_timestamps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    original_audit = workbench.audit

    def drift_audit_timestamp(*, run_id: UUID):
        audit = original_audit(run_id=run_id)
        audit["run"]["started_at"] = "2026-09-01T05:00:00.000999+00:00"
        return audit

    monkeypatch.setattr(workbench, "audit", drift_audit_timestamp)
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

    assert "终态与 committed journal 无法对账" in dom
    assert ".000999" not in dom
    assert "摘要已与完整 committed journal 对账" not in dom


def test_browser_rejects_attempt_zero_inside_a_bound_execution_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)

    @app.after_request
    def corrupt_the_first_execution_attempt(response):
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    '<script>document.querySelector("#harness-compile-button").click();</script>'
                    "</body>",
                )
            )
        if (
            request.path == "/api/harness/events"
            and request.args.get("run_id") == str(workbench.run_id)
            and response.status_code == 200
        ):
            page = response.get_json()
            if page["events"]:
                page["events"][0]["event"]["attempt"] = 0
                response.set_data(json.dumps(page))
                response.content_type = "application/json"
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert workbench.audit_runs == [workbench.run_id]
    assert "终态与 committed journal 无法对账" in dom
    assert "a" * 64 not in dom
    assert "摘要已与完整 committed journal 对账" not in dom


def test_browser_retries_a_transient_compile_audit_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    audit_requests = 0

    @app.before_request
    def fail_the_first_audit_request():
        nonlocal audit_requests
        if request.path == f"/api/harness/compile-runs/{workbench.run_id}/audit":
            audit_requests += 1
            if audit_requests == 1:
                return (
                    jsonify(
                        {
                            "error": {
                                "code": "harness_compile_audit_unavailable",
                                "message": "Harness compile audit is unavailable",
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

    assert audit_requests == 2
    assert workbench.audit_runs == [workbench.run_id]
    assert "摘要已与完整 committed journal 对账" in dom
    assert "asset-library-state@1" in dom


def test_browser_discards_a_late_audit_response_from_the_previous_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    run_a = UUID("82345678-1234-4234-9234-123456789abc")
    run_b = UUID("92345678-1234-4234-9234-123456789abc")
    for run_id in (run_a, run_b):
        recorder = RunRecorder(
            run_id=run_id,
            skill_id="text2env.compile",
            skill_version="1.0.0",
            clock=lambda: datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc),
            sink=journal,
        )
        recorder.start(stage="compile.started", attempt=1)
        recorder.finish(status=RunStatus.SUCCEEDED, stage="compile.succeeded")
    workbench = _ReadonlyAuditWorkbench(journal, {run_a: "1" * 64, run_b: "2" * 64})
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    audit_a_started = threading.Event()
    release_a = threading.Event()

    @app.get("/__test__/wait-audit-a")
    def wait_for_audit_a():
        assert audit_a_started.wait(timeout=5)
        return "", 204

    @app.before_request
    def delay_audit_a():
        if request.path == f"/api/harness/compile-runs/{run_a}/audit":
            audit_a_started.set()
            assert release_a.wait(timeout=5)

    @app.after_request
    def switch_runs_and_release_the_old_audit(response):
        if request.path == f"/api/harness/compile-runs/{run_b}/audit":
            release_a.set()
        if request.path == "/" and response.status_code == 200:
            response.direct_passthrough = False
            response.set_data(
                response.get_data(as_text=True).replace(
                    "</body>",
                    f"""
                    <script>
                    (async () => {{
                      await fetch('/__test__/wait-audit-a');
                      selectHarnessRun('{run_b}');
                    }})();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(
            f"{url}?harness_run={run_a}",
            tmp_path / "chrome-profile",
            virtual_time_budget_ms=4_000,
        )

    assert audit_a_started.is_set()
    assert release_a.is_set()
    assert run_a in workbench.audit_runs
    assert run_b in workbench.audit_runs
    assert "2" * 64 in dom
    assert "1" * 64 not in dom
    assert f'value="{run_b}"' in dom


def test_browser_never_restores_a_previous_audit_from_local_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    workbench = _JournalSubmittingWorkbench(journal)
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
        profile = tmp_path / "chrome-profile"
        first_dom = _rendered_dom(url, profile)

        def fail_audit(**_kwargs):
            raise WorkbenchCompileUnavailableError("secret path: /tmp/private-audit")

        monkeypatch.setattr(workbench, "audit", fail_audit)
        second_dom = _rendered_dom(f"{url}?harness_run={workbench.run_id}", profile)

    assert "a" * 64 in first_dom
    assert "摘要已与完整 committed journal 对账" in first_dom
    assert "a" * 64 not in second_dom
    assert "/tmp/private-audit" not in second_dom
    assert "终态与 committed journal 无法对账" in second_dom


def test_browser_replays_more_than_the_cache_window_before_auditing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    run_id = UUID("a2345678-1234-4234-9234-123456789abc")
    recorder = RunRecorder(
        run_id=run_id,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc),
        sink=journal,
    )
    recorder.start(stage="compile.started", attempt=1)
    for index in range(199):
        recorder.progress(stage=f"compile.progress.{index:03d}")
    recorder.finish(status=RunStatus.SUCCEEDED, stage="compile.succeeded")
    workbench = _ReadonlyAuditWorkbench(journal, {run_id: "f" * 64})
    app = _configured_app(tmp_path, monkeypatch, None, workbench=workbench)
    observed_limits: list[str] = []

    @app.before_request
    def observe_selected_page_size():
        if request.path == "/api/harness/events" and request.args.get("run_id") == str(run_id):
            observed_limits.append(request.args["limit"])

    with _served(app) as url:
        dom = _rendered_dom(
            f"{url}?harness_run={run_id}",
            tmp_path / "chrome-profile",
            virtual_time_budget_ms=4_000,
        )

    assert observed_limits[0] == "500"
    assert workbench.audit_runs == [run_id]
    assert dom.count("data-event-id=") == 201
    assert 'data-event-id="201"' in dom
    assert '<code id="harness-cursor">201</code>' in dom
    assert "摘要已与完整 committed journal 对账" in dom


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
                    """
                    <script>
                    (async () => {
                      const pause = () => new Promise((resolve) => setTimeout(resolve, 10));
                      document.querySelector('#harness-compile-button').click();
                      const panel = document.querySelector('#harness-audit-panel');
                      while (panel.hidden) await pause();
                      document.body.dataset.preflightPreviewHidden = String(
                        document.querySelector('#harness-scene-preview-button').hidden
                      );
                    })();
                    </script>
                    </body>
                    """,
                )
            )
        return response

    with _served(app) as url:
        dom = _rendered_dom(url, tmp_path / "chrome-profile")

    assert "Harness Compile 已持久化为 blocked" in dom
    assert "Harness compile response failed integrity checks" not in dom
    assert "preflight" in dom
    assert workbench.audit_runs == [workbench.run_id]
    assert "未创建 Invocation" in dom
    assert 'id="harness-dependency-list"><li class="empty">' in dom
    assert "预检终止前未解析依赖" in dom
    assert 'id="harness-artifact-list"' in dom
    assert '<li class="empty">预检终止前未产出 artifact。' in dom
    assert "预检终止前未产出 artifact" in dom
    assert 'data-preflight-preview-hidden="true"' in dom


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
