"""Single-machine Flask control surface for Text2Env compile, runtime, and VLM review."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

DEFAULT_SETTLE_STEPS = 900


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobError(RuntimeError):
    pass


class JobStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def directory(self, job_id: str) -> Path:
        if not job_id or any(character not in "0123456789abcdef" for character in job_id):
            raise JobError("invalid job id")
        return self.root / job_id

    def read(self, job_id: str) -> dict[str, Any]:
        path = self.directory(job_id) / "job.json"
        if not path.is_file():
            raise JobError("job not found")
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def write(self, job: dict[str, Any]) -> None:
        directory = self.directory(str(job["job_id"]))
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "job.json"
        temporary = directory / "job.json.tmp"
        with self._lock:
            temporary.write_text(
                json.dumps(job, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(target)

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            job = self.read(job_id)
            job.update(changes)
            job["updated_at"] = utc_now()
            self.write(job)
            return job

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for path in self.root.glob("*/job.json"):
            try:
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        jobs.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return jobs[:limit]


class ScenePipeline:
    def __init__(
        self,
        *,
        repo_root: Path,
        robotwin_root: Path,
        python: Path,
        catalog: Path,
        store: JobStore,
        vlm_model: str,
    ):
        self.repo_root = repo_root.expanduser().resolve()
        self.robotwin_root = robotwin_root.expanduser().resolve()
        self.python = python.expanduser().resolve()
        self.catalog = catalog.expanduser().resolve()
        self.store = store
        self.vlm_model = vlm_model
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scene-pipeline")

    def submit(self, prompt: str, seed: int) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:16]
        now = utc_now()
        job = {
            "schema_version": "robotwin.scene_demo_job.v1",
            "job_id": job_id,
            "prompt": prompt,
            "seed": seed,
            "status": "queued",
            "active_stage": "queued",
            "created_at": now,
            "updated_at": now,
            "stages": {
                "compile": {"status": "pending"},
                "runtime": {"status": "pending"},
                "critic": {"status": "pending"},
            },
            "artifacts": {},
        }
        self.store.write(job)
        self.executor.submit(self._run, job_id)
        return job

    def _stage(self, job_id: str, stage: str, status: str, **details: Any) -> None:
        job = self.store.read(job_id)
        stages = dict(job["stages"])
        stage_value = dict(stages[stage])
        stage_value.update(details)
        stage_value["status"] = status
        stage_value["updated_at"] = utc_now()
        stages[stage] = stage_value
        self.store.update(job_id, stages=stages, active_stage=stage)

    def _command(
        self,
        *,
        job_id: str,
        stage: str,
        command: list[str],
        allowed_codes: set[int] = {0},
        timeout: int = 1800,
    ) -> subprocess.CompletedProcess[str]:
        directory = self.store.directory(job_id)
        log_path = directory / f"{stage}.log"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.repo_root)
        completed = subprocess.run(
            command,
            cwd=self.repo_root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        log_path.write_text(
            "$ " + " ".join(command) + "\n\nSTDOUT\n" + completed.stdout + "\nSTDERR\n" + completed.stderr,
            encoding="utf-8",
        )
        self._stage(
            job_id,
            stage,
            "completed" if completed.returncode in allowed_codes else "failed",
            returncode=completed.returncode,
            log=f"{stage}.log",
            output=(completed.stdout + completed.stderr)[-1600:],
        )
        if completed.returncode not in allowed_codes:
            raise JobError(f"{stage} exited with code {completed.returncode}")
        return completed

    def _run(self, job_id: str) -> None:
        job = self.store.read(job_id)
        directory = self.store.directory(job_id)
        compile_root = directory / "compile"
        runtime_root = directory / "runtime"
        try:
            self.store.update(job_id, status="running", active_stage="compile", started_at=utc_now())
            self._stage(job_id, "compile", "running", started_at=utc_now())
            self._command(
                job_id=job_id,
                stage="compile",
                command=[
                    str(self.python),
                    str(self.repo_root / "script" / "generate_scene.py"),
                    "--prompt",
                    str(job["prompt"]),
                    "--seed",
                    str(job["seed"]),
                    "--asset-catalog",
                    str(self.catalog),
                    "--out-root",
                    str(compile_root),
                    "--generate-missing-assets",
                    "--generated-objects-root",
                    str(self.robotwin_root / "assets" / "objects"),
                ],
            )
            packages = sorted(
                path for path in compile_root.iterdir() if path.is_dir() and path.name != "_failures"
            )
            if len(packages) != 1 or not (packages[0] / "resolved_scene.json").is_file():
                raise JobError("compiler did not produce exactly one resolved scene package")
            package = packages[0]
            effective_catalog = package / "effective_asset_catalog.json"
            runtime_catalog = effective_catalog if effective_catalog.is_file() else self.catalog

            self._stage(job_id, "runtime", "running", started_at=utc_now())
            self._command(
                job_id=job_id,
                stage="runtime",
                command=[
                    str(self.python),
                    str(self.repo_root / "script" / "run_scene_runtime.py"),
                    "--robotwin-root",
                    str(self.robotwin_root),
                    "--resolved-scene",
                    str(package / "resolved_scene.json"),
                    "--asset-catalog",
                    str(runtime_catalog),
                    "--out-dir",
                    str(runtime_root),
                    "--precheck-steps",
                    "0",
                    "--settle-steps",
                    str(DEFAULT_SETTLE_STEPS),
                    "--contact-window-steps",
                    "120",
                    "--video-frames",
                    "120",
                    "--fps",
                    "12",
                ],
            )

            critic_path = directory / "rendered_critic.json"
            critic_images = [
                runtime_root / "preview_head.png",
                runtime_root / "preview_world_left.png",
                runtime_root / "preview_world_right.png",
                runtime_root / "observer_start.png",
            ]
            self._stage(job_id, "critic", "running", started_at=utc_now())
            self._command(
                job_id=job_id,
                stage="critic",
                allowed_codes={0, 2},
                timeout=2400,
                command=[
                    str(self.python),
                    str(self.repo_root / "script" / "run_rendered_critic.py"),
                    "--resolved-scene",
                    str(package / "resolved_scene.json"),
                    *[value for image in critic_images for value in ("--image", str(image))],
                    "--model",
                    self.vlm_model,
                    "--out",
                    str(critic_path),
                ],
            )
            critic = json.loads(critic_path.read_text(encoding="utf-8"))
            artifact_paths = {
                "scene_spec": package / "scene_spec.json",
                "resolved_scene": package / "resolved_scene.json",
                "static_validation": package / "validation_report.json",
                "asset_generation": package / "asset_generation_report.json",
                "runtime_evidence": runtime_root / "runtime_evidence.json",
                "runtime_validation": runtime_root / "runtime_validation_report.json",
                "critic": critic_path,
                "head": runtime_root / "preview_head.png",
                "world_left": runtime_root / "preview_world_left.png",
                "world_right": runtime_root / "preview_world_right.png",
                "observer_start": runtime_root / "observer_start.png",
                "observer_mid": runtime_root / "observer_mid.png",
                "observer_end": runtime_root / "observer_end.png",
                "video": runtime_root / "observer_runtime.mp4",
            }
            artifacts = {
                key: str(path.resolve().relative_to(directory))
                for key, path in artifact_paths.items()
                if path.is_file()
            }
            self.store.update(
                job_id,
                status="completed",
                active_stage="completed",
                completed_at=utc_now(),
                scene_id=package.name,
                verdict="pass" if critic.get("status") == "pass" else "critic_failed",
                artifacts=artifacts,
            )
        except Exception as error:
            self.store.update(
                job_id,
                status="failed",
                error={"type": type(error).__name__, "message": str(error)},
                failed_at=utc_now(),
            )


def create_app(config: dict[str, Any] | None = None) -> Flask:
    repo_root = Path(os.environ.get("SCENE_DEMO_REPO_ROOT", Path(__file__).resolve().parents[1]))
    robotwin_root = Path(os.environ.get("ROBOTWIN_ROOT", repo_root / "external" / "RoboTwin"))
    python = Path(os.environ.get("ROBOTWIN_PYTHON", sys.executable))
    catalog = Path(
        os.environ.get("SCENE_ASSET_CATALOG", repo_root / "data" / "scene_gen" / "asset_catalog.json")
    )
    jobs_root = Path(os.environ.get("SCENE_DEMO_JOBS_ROOT", repo_root / "data" / "demo_jobs"))
    vlm_model = os.environ.get("SCENE_VLM_MODEL", "Qwen/Qwen2.5-VL-3B-Instruct")
    static_root = Path(__file__).with_name("static")
    app = Flask(__name__, static_folder=str(static_root), static_url_path="/static")
    app.config.update(config or {})
    store = JobStore(jobs_root)
    pipeline = ScenePipeline(
        repo_root=repo_root,
        robotwin_root=robotwin_root,
        python=python,
        catalog=catalog,
        store=store,
        vlm_model=vlm_model,
    )
    app.extensions["scene_store"] = store
    app.extensions["scene_pipeline"] = pipeline

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "public, max-age=300"
        return response

    @app.get("/")
    def index():
        return send_from_directory(static_root, "index.html")

    @app.get("/api/health")
    def health():
        paths = {
            "repo_root": repo_root.is_dir(),
            "robotwin_root": robotwin_root.is_dir(),
            "python": python.is_file(),
            "catalog": catalog.is_file(),
        }
        return jsonify({"status": "ready" if all(paths.values()) else "not_ready", "paths": paths})

    @app.get("/api/jobs")
    def list_jobs():
        return jsonify({"jobs": store.list(limit=min(50, max(1, request.args.get("limit", 20, type=int))))})

    @app.post("/api/jobs")
    def create_job():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": {"code": "invalid_json", "message": "JSON object required"}}), 400
        prompt = str(payload.get("prompt") or "").strip()
        seed = payload.get("seed", 0)
        if not 3 <= len(prompt) <= 2000:
            return jsonify({"error": {"code": "invalid_prompt", "message": "Prompt must contain 3-2000 characters"}}), 400
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2_147_483_647:
            return jsonify({"error": {"code": "invalid_seed", "message": "Seed must be a non-negative 32-bit integer"}}), 400
        return jsonify(pipeline.submit(prompt, seed)), 202

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str):
        try:
            return jsonify(store.read(job_id))
        except JobError as error:
            return jsonify({"error": {"code": "job_not_found", "message": str(error)}}), 404

    @app.get("/api/jobs/<job_id>/artifacts/<path:filename>")
    def artifact(job_id: str, filename: str):
        try:
            directory = store.directory(job_id)
            job = store.read(job_id)
        except JobError as error:
            return jsonify({"error": {"code": "job_not_found", "message": str(error)}}), 404
        allowed = set((job.get("artifacts") or {}).values()) | {
            str(value.get("log")) for value in (job.get("stages") or {}).values() if value.get("log")
        }
        if filename not in allowed:
            return jsonify({"error": {"code": "artifact_not_found", "message": "Artifact is not registered"}}), 404
        return send_from_directory(directory, filename, conditional=True)

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("SCENE_DEMO_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SCENE_DEMO_PORT", "8765")))
    args = parser.parse_args()
    create_app().run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
