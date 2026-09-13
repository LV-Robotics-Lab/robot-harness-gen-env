"""Retired compile routes cannot revive a second workflow or event authority."""

from demo.harness_feed import HarnessEventFeed
from self_improving.harness.event_journal import SQLiteEventJournal
from tests.demo.test_app import configured_app


def test_retired_compile_routes_and_page_controls_are_absent(tmp_path, monkeypatch):
    client = configured_app(tmp_path, monkeypatch, {"HARNESS_WORKBENCH": object()}).test_client()
    response = client.post("/api/harness/compile", json={"request": "a mouse", "seed": 11})
    assert response.status_code == 404
    run = "12345678-1234-4234-9234-123456789abc"
    for suffix in ("audit", "scene-preview", "static-validation-preview"):
        assert client.get(f"/api/harness/compile-runs/{run}/{suffix}").status_code == 404
    page = client.get("/").get_data(as_text=True)
    assert 'id="harness-compile-button"' not in page
    assert 'id="harness-audit-panel"' not in page
    assert 'id="generate-button"' in page
    assert 'id="harness-run-board"' in page
    assert client.get("/api/jobs").status_code == 200


def test_legacy_workbench_cannot_supply_event_authority(tmp_path, monkeypatch):
    class LegacyWorkbench:
        def page(self, **kwargs):
            raise AssertionError("legacy workflow must never be consulted")

    client = configured_app(
        tmp_path, monkeypatch, {"HARNESS_WORKBENCH": LegacyWorkbench()}
    ).test_client()
    response = client.get("/api/harness/events")
    assert response.status_code == 503
    assert response.json["error"]["code"] == "harness_event_feed_unavailable"


def test_explicit_feed_is_the_only_event_authority(tmp_path, monkeypatch):
    journal = SQLiteEventJournal(tmp_path / "events.sqlite")
    feed = HarnessEventFeed.from_journal(journal)
    client = configured_app(
        tmp_path, monkeypatch, {"HARNESS_WORKBENCH": object(), "HARNESS_EVENT_FEED": feed}
    ).test_client()
    response = client.get("/api/harness/events")
    assert response.status_code == 200
    assert response.json == feed.page()
