#!/usr/bin/env python3
"""Pipeline Studio — Flask web frontend over the asset-adaptive scene pipeline.

Only touches results/web_runs/. Invokes scene_acquire.py and run_scene_runtime.py
as subprocesses; never imports or modifies pipeline code.
"""

import copy
import glob
import json
import os
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_file, abort

# --- constants -------------------------------------------------------------

DEV = Path("/home/jingxiang/yuxin/env-gen-dev")
PY = "/home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python"
UP = Path("/home/jingxiang/yuxin/env-gen-github")
PORT = 8811

WEB_RUNS = DEV / "results" / "web_runs"
HISTORY_ROOT = DEV / "results" / "_test"
DEFAULT_CATALOG = DEV / "data" / "scene_gen_ext" / "asset_catalog.json"
DEFAULT_PROVIDERS = DEV / "1_asset_reuse" / "configs" / "providers.json"
ROBOTWIN_ROOT = DEV / "data" / "robotwin_shadow"

SGT = ZoneInfo("Asia/Singapore")
PYTHONPATH = f"{DEV}/1_asset_reuse:{DEV}/shared/openxsim/source/agenticsim:{UP}"

ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
EXCLUDE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
ALLOWED_FILE_SUFFIXES = {
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".mp4",
    ".log",
    ".txt",
    ".yml",
}

WEB_RUNS.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=None)

# --- run-lock state ----------------------------------------------------

_lock = threading.Lock()
CURRENT = {"run_id": None, "phase": None}


def now_sgt():
    return datetime.now(SGT)


def slugify(prompt):
    s = re.sub(r"[^a-z0-9]+", "_", prompt.lower()).strip("_")
    return s[:40].strip("_") or "run"


# --- run_state.json helper --------------------------------------------


def write_state(run_dir, **fields):
    state_path = run_dir / "run_state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            state = {}
    state.update(fields)
    state_path.write_text(json.dumps(state, indent=2))
    return state


def append_log(run_dir, text):
    with open(run_dir / "run.log", "a") as f:
        f.write(text)


def run_subprocess_logged(cmd, cwd, env, run_dir, stage_name):
    append_log(run_dir, f"\n=== stage: {stage_name} ===\ncmd: {cmd}\ncwd: {cwd}\n")
    with open(run_dir / "run.log", "a") as f:
        proc = subprocess.run(
            cmd, cwd=str(cwd), env=env, stdout=f, stderr=subprocess.STDOUT
        )
    return proc.returncode


# --- pipeline thread -----------------------------------------------------


def pipeline_worker(run_id, run_dir, prompt, seed, catalog_used, providers_used):
    env = os.environ.copy()
    env["PYTHONPATH"] = PYTHONPATH
    env["OMNI_KIT_ACCEPT_EULA"] = "YES"

    pipeline_rc = None
    render_rc = None
    try:
        write_state(
            run_dir,
            phase="pipeline",
            outcome=None,
            pipeline_rc=None,
            render_rc=None,
            finished_at=None,
        )

        cmd = [
            PY,
            "scripts/d_acquire/scene_acquire.py",
            "--prompt",
            prompt,
            "--seed",
            str(seed),
            "--catalog",
            catalog_used,
            "--providers",
            providers_used,
            "--dev-root",
            str(DEV),
            "--out",
            str(run_dir),
        ]
        pipeline_rc = run_subprocess_logged(
            cmd, DEV / "1_asset_reuse", env, run_dir, "pipeline"
        )
        write_state(run_dir, pipeline_rc=pipeline_rc)

        resolved_scene = None
        matches = sorted(
            glob.glob(str(run_dir / "scenes" / "*" / "resolved_scene.json"))
        )
        if matches:
            resolved_scene = matches[0]

        if pipeline_rc == 0 and resolved_scene:
            write_state(run_dir, phase="render")
            render_cmd = [
                PY,
                "script/run_scene_runtime.py",
                "--robotwin-root",
                str(ROBOTWIN_ROOT),
                "--resolved-scene",
                resolved_scene,
                "--asset-catalog",
                str(DEFAULT_CATALOG),
                "--out-dir",
                str(run_dir / "runtime"),
                "--settle-steps",
                "600",
                "--contact-window-steps",
                "60",
                "--video-frames",
                "48",
                "--fps",
                "12",
            ]
            try:
                render_rc = run_subprocess_logged(
                    render_cmd, UP, env, run_dir, "render"
                )
            except Exception as exc:
                append_log(run_dir, f"\nrender step raised: {exc!r}\n")
                render_rc = None
            write_state(run_dir, render_rc=render_rc)

        if (run_dir / "asset_gap_blocker.json").exists():
            outcome = "blocker"
        elif resolved_scene and pipeline_rc == 0:
            outcome = "scene"
        else:
            outcome = "failed"

        write_state(
            run_dir,
            phase="done",
            outcome=outcome,
            pipeline_rc=pipeline_rc,
            render_rc=render_rc,
            finished_at=now_sgt().isoformat(),
        )
    except Exception as exc:
        append_log(run_dir, f"\nworker crashed: {exc!r}\n")
        write_state(
            run_dir,
            phase="done",
            outcome="failed",
            pipeline_rc=pipeline_rc,
            render_rc=render_rc,
            finished_at=now_sgt().isoformat(),
        )
    finally:
        with _lock:
            if CURRENT.get("run_id") == run_id:
                CURRENT["run_id"] = None
                CURRENT["phase"] = None


# --- POST /api/run ---------------------------------------------------------


@app.post("/api/run")
def api_run():
    body = request.get_json(silent=True) or {}

    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not (1 <= len(prompt.strip()) <= 300):
        return jsonify(
            {"error": "prompt must be a non-empty string up to 300 chars"}
        ), 400
    prompt = prompt.strip()

    seed = body.get("seed", 42)
    if (
        isinstance(seed, bool)
        or not isinstance(seed, int)
        or not (0 <= seed <= 2**31 - 1)
    ):
        return jsonify({"error": "seed must be an int in [0, 2**31-1]"}), 400

    exclude_category = body.get("exclude_category")
    if exclude_category is not None:
        if not isinstance(exclude_category, str) or not EXCLUDE_RE.match(
            exclude_category
        ):
            return jsonify({"error": "exclude_category invalid"}), 400

    with _lock:
        if CURRENT["run_id"] is not None:
            return jsonify({"error": "busy", "current": CURRENT["run_id"]}), 409
        run_id = f"{now_sgt():%Y%m%d_%H%M%S}_{slugify(prompt)}"
        CURRENT["run_id"] = run_id
        CURRENT["phase"] = "pipeline"

    try:
        run_dir = WEB_RUNS / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        if exclude_category:
            catalog_data = json.loads(DEFAULT_CATALOG.read_text())
            filtered = dict(catalog_data)
            filtered["entries"] = [
                e
                for e in catalog_data.get("entries", [])
                if e.get("category") != exclude_category
                and exclude_category not in (e.get("aliases") or [])
            ]
            catalog_path = run_dir / "catalog_filtered.json"
            catalog_path.write_text(json.dumps(filtered, indent=2))

            providers_data = json.loads(DEFAULT_PROVIDERS.read_text())
            providers_variant = copy.deepcopy(providers_data)
            providers_variant["providers"]["robotwin_local"]["catalog"] = str(
                catalog_path.resolve()
            )
            providers_path = run_dir / "providers_variant.json"
            providers_path.write_text(json.dumps(providers_variant, indent=2))

            catalog_used = str(catalog_path.resolve())
            providers_used = str(providers_path.resolve())
        else:
            catalog_used = str(DEFAULT_CATALOG)
            providers_used = str(DEFAULT_PROVIDERS)

        run_meta = {
            "prompt": prompt,
            "seed": seed,
            "exclude_category": exclude_category,
            "catalog": catalog_used,
            "providers": providers_used,
            "started_at": now_sgt().isoformat(),
        }
        (run_dir / "run_meta.json").write_text(json.dumps(run_meta, indent=2))

        t = threading.Thread(
            target=pipeline_worker,
            args=(run_id, run_dir, prompt, seed, catalog_used, providers_used),
            daemon=True,
        )
        t.start()
    except Exception as exc:
        with _lock:
            if CURRENT.get("run_id") == run_id:
                CURRENT["run_id"] = None
                CURRENT["phase"] = None
        return jsonify({"error": f"failed to start run: {exc!r}"}), 500

    return jsonify({"run_id": run_id}), 202


# --- GET /api/runs -----------------------------------------------------


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _dir_outcome_from_files(d):
    """Best-effort outcome derived purely from files on disk (used for history,
    which has no run_state.json, and as a fallback)."""
    if (d / "asset_gap_blocker.json").exists():
        return "blocker"
    if sorted(glob.glob(str(d / "scenes" / "*" / "resolved_scene.json"))):
        return "scene"
    if (d / "coverage_report.json").exists() or (
        d / "selection_evidence.json"
    ).exists():
        return "evidence"
    return None


@app.get("/api/runs")
def api_runs():
    web = []
    if WEB_RUNS.exists():
        for d in sorted(
            WEB_RUNS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            if not d.is_dir():
                continue
            meta = _read_json(d / "run_meta.json") or {}
            state = _read_json(d / "run_state.json") or {}
            web.append(
                {
                    "id": d.name,
                    "group": "web",
                    "label": meta.get("prompt") or d.name,
                    "prompt": meta.get("prompt"),
                    "outcome": state.get("outcome"),
                    "mtime": d.stat().st_mtime,
                }
            )

    history = []
    if HISTORY_ROOT.exists():
        for d in sorted(
            HISTORY_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            if not d.is_dir():
                continue
            if not (
                (d / "coverage_report.json").exists()
                or (d / "selection_evidence.json").exists()
            ):
                continue
            coverage = _read_json(d / "coverage_report.json") or {}
            history.append(
                {
                    "id": d.name,
                    "group": "history",
                    "label": d.name,
                    "prompt": coverage.get("prompt"),
                    "outcome": _dir_outcome_from_files(d),
                    "mtime": d.stat().st_mtime,
                }
            )

    return jsonify({"web": web, "history": history})


# --- run dir resolution / containment ------------------------------------

GROUP_ROOTS = {"web": WEB_RUNS, "history": HISTORY_ROOT}


def resolve_run_dir(group, run_id):
    root = GROUP_ROOTS.get(group)
    if root is None:
        abort(404)
    if not ID_RE.match(run_id):
        abort(404)
    root_resolved = root.resolve()
    full = (root / run_id).resolve()
    if not (full == root_resolved or str(full).startswith(str(root_resolved) + os.sep)):
        abort(404)
    if not full.is_dir():
        abort(404)
    return full


# --- GET /api/run/<group>/<id>/status -------------------------------------


def _short_candidate_id(cid):
    if not cid:
        return cid
    return re.split(r"[/:]", cid)[-1]


def _read_evidence(run_dir):
    path = run_dir / "acquire" / "selection_evidence.json"
    if not path.exists():
        path = run_dir / "selection_evidence.json"
    if not path.exists():
        return None
    raw = _read_json(path)
    if raw is None:
        return None
    categories = []
    for cat in raw.get("categories", []):
        query = cat.get("query", {})
        candidates = [
            {
                "id": _short_candidate_id(c.get("candidate_id")),
                "candidate_id": c.get("candidate_id"),
                "verdict": c.get("verdict"),
                "rejection": c.get("rejection"),
            }
            for c in cat.get("candidates", [])
        ]
        categories.append(
            {
                "category": query.get("category"),
                "status": cat.get("status"),
                "tiers_consulted": cat.get("tiers_consulted"),
                "attempts": cat.get("attempts"),
                "candidates": candidates,
                "selected": cat.get("selected"),
            }
        )
    result = {"categories": categories}
    if "categories_sha256" in raw:
        result["categories_sha256"] = raw["categories_sha256"]
    return result


def _list_shots(run_dir):
    shots = []
    for sub in ("acquire/shots", "shots"):
        for p in (
            sorted((run_dir / sub).glob("*.png")) if (run_dir / sub).is_dir() else []
        ):
            shots.append(str(p.relative_to(run_dir)))
    return shots


def _read_scene(run_dir):
    matches = sorted(glob.glob(str(run_dir / "scenes" / "*" / "resolved_scene.json")))
    if not matches:
        return None
    scene_path = Path(matches[0])
    resolved = _read_json(scene_path) or {}
    objects = [
        {
            "object_id": o.get("object_id"),
            "asset_id": o.get("asset_id"),
            "model_id": o.get("model_id"),
        }
        for o in resolved.get("objects", [])
    ]
    validation_status = None
    validation_path = scene_path.parent / "validation_report.json"
    if validation_path.exists():
        vr = _read_json(validation_path) or {}
        validation_status = vr.get("status")
    return {
        "dir": str(scene_path.parent.relative_to(run_dir)),
        "objects": objects,
        "validation": validation_status,
    }


def _list_render(run_dir):
    runtime_dir = run_dir / "runtime"
    images, videos = [], []
    if runtime_dir.is_dir():
        for p in sorted(runtime_dir.rglob("*")):
            if not p.is_file():
                continue
            suf = p.suffix.lower()
            if suf in (".png", ".jpg", ".jpeg") and len(images) < 12:
                images.append(str(p.relative_to(run_dir)))
            elif suf == ".mp4":
                videos.append(str(p.relative_to(run_dir)))
    return {"images": images, "videos": videos}


@app.get("/api/run/<group>/<run_id>/status")
def api_run_status(group, run_id):
    run_dir = resolve_run_dir(group, run_id)

    log_tail = None
    log_path = run_dir / "run.log"
    if log_path.exists():
        try:
            lines = log_path.read_text(errors="replace").splitlines()
            log_tail = "\n".join(lines[-100:])
        except Exception:
            log_tail = None

    payload = {
        "meta": _read_json(run_dir / "run_meta.json"),
        "state": _read_json(run_dir / "run_state.json"),
        "stages": {
            "coverage": _read_json(run_dir / "coverage_report.json"),
            "evidence": _read_evidence(run_dir),
            "shots": _list_shots(run_dir),
            "scene": _read_scene(run_dir),
            "render": _list_render(run_dir),
            "blocker": _read_json(run_dir / "asset_gap_blocker.json"),
        },
        "log_tail": log_tail,
    }
    return jsonify(payload)


# --- GET /api/run/<group>/<id>/file ---------------------------------------

MIME_BY_SUFFIX = {
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".mp4": "video/mp4",
    ".log": "text/plain",
    ".txt": "text/plain",
    ".yml": "text/yaml",
}


@app.get("/api/run/<group>/<run_id>/file")
def api_run_file(group, run_id):
    run_dir = resolve_run_dir(group, run_id)
    rel = request.args.get("p", "")
    if not rel:
        abort(404)
    full = (run_dir / rel).resolve()
    if not str(full).startswith(str(run_dir) + os.sep):
        abort(404)
    if full.suffix.lower() not in ALLOWED_FILE_SUFFIXES:
        abort(404)
    if not full.is_file():
        abort(404)
    mimetype = MIME_BY_SUFFIX.get(full.suffix.lower(), "application/octet-stream")
    return send_file(str(full), mimetype=mimetype)


# --- GET / ------------------------------------------------------------


@app.get("/")
def index():
    return send_file(str(Path(__file__).parent / "index.html"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
