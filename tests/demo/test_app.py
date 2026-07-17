from __future__ import annotations

from pathlib import Path

from demo.app import create_app, utc_now


def configured_app(tmp_path: Path, monkeypatch):
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
    return create_app({"TESTING": True})


def test_health_and_job_input_boundary(tmp_path: Path, monkeypatch) -> None:
    app = configured_app(tmp_path, monkeypatch)
    client = app.test_client()
    assert client.get("/api/health").json["status"] == "ready"
    assert client.post("/api/jobs", json={"prompt": "x", "seed": 0}).status_code == 400
    assert client.post("/api/jobs", json={"prompt": "valid scene", "seed": -1}).status_code == 400

    pipeline = app.extensions["scene_pipeline"]
    monkeypatch.setattr(
        pipeline,
        "submit",
        lambda prompt, seed: {"job_id": "abcdef0123456789", "prompt": prompt, "seed": seed, "status": "queued"},
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
