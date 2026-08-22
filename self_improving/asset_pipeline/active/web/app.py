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
import sys
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, request, send_file

# --- constants -------------------------------------------------------------

ACTIVE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
DEV = Path(os.environ.get("ASSET_PIPELINE_ROOT", ACTIVE_ROOT)).expanduser().resolve()
PY = os.environ.get("ASSET_PIPELINE_PYTHON", sys.executable)
UP = Path(os.environ.get("GEN_ENV_ROOT", REPO_ROOT)).expanduser().resolve()
PORT = int(os.environ.get("ASSET_PIPELINE_PORT", "8811"))

WEB_RUNS = DEV / "results" / "web_runs"
WEB_THUMBS = DEV / "results" / "web_thumbs"
ASSET_LIB = DEV / "data" / "asset_library"
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
    ".py",
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
            "scripts/1_search/scene_acquire.py",
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
                # our acceptance callers opt in to the adaptive horizon; the
                # script default stays 0 so upstream behaviour is unchanged
                # (s2/s5/s7 two-sided validation, 2026-08-20)
                "--settle-converge-max",
                "1800",
                "--contact-window-steps",
                "60",
                "--video-frames",
                "48",
                "--fps",
                "12",
            ]
            # rebuilt curobo (vendored, pinned version) hits an async CUDA
            # launch race on sm_120 during planner warmup; render survives
            # only with serialized launches (2026-08-13). Scoped to this
            # stage -- the acquire stage's VLM would pay real latency for it.
            render_env = {**env, "CUDA_LAUNCH_BLOCKING": "1"}
            try:
                render_rc = run_subprocess_logged(
                    render_cmd, UP, render_env, run_dir, "render"
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

    return jsonify({"web": web, "history": history, "current": CURRENT["run_id"]})


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
                "match": cat.get("match"),
            }
        )
    result = {"categories": categories}
    if "categories_sha256" in raw:
        result["categories_sha256"] = raw["categories_sha256"]
    if raw.get("input_warnings"):
        result["input_warnings"] = raw["input_warnings"]
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


# --- stage timeline ---------------------------------------------------

STAGE_TITLES = [
    (1, "coverage", "覆盖检查"),
    (2, "gap", "缺口引进"),
    (3, "convert", "下载转换"),
    (4, "qc", "物理质检"),
    (5, "rebuild", "catalog 重建"),
    (6, "scene", "场景生成"),
    (7, "render", "回放渲染"),
]

# (needle, stage) — 运行中细化 active 阶段用；取日志中最后出现的标记
TL_LOG_MARKERS = [
    ("simulation app startup", 3),
    ("app ready", 3),
    ("accepted ", 4),
    ("rejected ", 4),
    ("=== stage: render ===", 7),
]


def _mtime(p):
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def _parse_iso_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def compute_stage_timeline(run_dir, meta, state, log_text):
    """七阶段状态/时间轴。信号 = 产物文件 mtime + run.log 全文标记 + run_state。"""
    acq_cat = run_dir / "acquire_categories.json"
    coverage_p = run_dir / "coverage_report.json"
    evidence_p = run_dir / "acquire" / "selection_evidence.json"
    if not evidence_p.exists():
        evidence_p = run_dir / "selection_evidence.json"
    blocker_p = run_dir / "asset_gap_blocker.json"
    scene_matches = sorted(
        glob.glob(str(run_dir / "scenes" / "*" / "resolved_scene.json"))
    )
    shots = []
    for sub in ("acquire/shots", "shots"):
        d = run_dir / sub
        if d.is_dir():
            shots.extend(sorted(d.glob("*.png")))
    runtime_mtimes = []
    runtime_dir = run_dir / "runtime"
    if runtime_dir.is_dir():
        for p in runtime_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".mp4"):
                m = _mtime(p)
                if m:
                    runtime_mtimes.append(m)

    log_low = (log_text or "").lower()
    gap_ran = acq_cat.exists() or evidence_p.exists()
    rebuild_done = ("pass s9" in log_low) or bool(meta.get("exclude_category"))
    phase = state.get("phase")
    live = phase in ("pipeline", "render")
    outcome = state.get("outcome")

    m_acq = _mtime(acq_cat)
    m_cov = _mtime(coverage_p)
    m_evi = _mtime(evidence_p)
    m_blk = _mtime(blocker_p)
    m_scene = _mtime(Path(scene_matches[0])) if scene_matches else None
    shot_ms = [m for m in (_mtime(p) for p in shots) if m]

    stages = {}
    s1_sigs = [m for m in (m_acq, m_cov, m_evi) if m]
    stages[1] = ("done", min(s1_sigs)) if s1_sigs else ("pending", None)
    if gap_ran:
        if m_cov or (m_evi and not live):
            stages[2] = ("done", m_evi or m_cov)
        else:
            stages[2] = ("pending", None)
    else:
        stages[2] = (
            ("skipped", None)
            if (coverage_p.exists() or live)
            else (
                "pending",
                None,
            )
        )
    if not gap_ran:
        stages[3] = ("skipped", None)
        stages[4] = ("skipped", None)
    else:
        stages[3] = ("done", min(shot_ms)) if shot_ms else ("pending", None)
        stages[4] = ("done", max(shot_ms)) if shot_ms else ("pending", None)
    if rebuild_done:
        stages[5] = ("done", m_cov)
    elif live and gap_ran:
        stages[5] = ("pending", None)
    else:
        stages[5] = ("skipped", None)
    if blocker_p.exists():
        stages[6] = ("blocked", m_blk)
    elif scene_matches:
        stages[6] = ("done", m_scene)
    else:
        stages[6] = ("pending", None)
    if blocker_p.exists():
        stages[7] = ("skipped", None)
    elif not scene_matches:
        stages[7] = ("pending", None)
    elif not live and (runtime_mtimes or state.get("render_rc") == 0):
        end7 = (
            max(runtime_mtimes)
            if runtime_mtimes
            else _parse_iso_ts(state.get("finished_at"))
        )
        stages[7] = ("done", end7)
    else:
        stages[7] = ("pending", None)

    if live:
        if phase == "render":
            active_n = 7
        elif gap_ran and stages[2][0] == "pending":
            # ② 进行中：③④只反映「当前候选」的进度——上一个候选失败
            # （REJECTED 行）后从灰重新开始，不因它留下的截图而常绿
            # （2026-08-20 用户反馈：多候选重试时后面步骤不该保持绿色）。
            tail = log_text or ""
            last = None
            for last in re.finditer(r"REJECTED \S+ m\d+ \(", tail):
                pass
            if last is not None:
                tail = tail[last.end() :]
            tl_low = tail.lower()
            s4_seen = ("accepted " in tl_low) or ("rejected " in tl_low)
            s3_seen = ("simulation app startup" in tl_low) or ("app ready" in tl_low)
            stages[3] = ("active" if (s3_seen and not s4_seen) else "pending", None)
            stages[4] = ("active" if s4_seen else "pending", None)
            active_n = 2
        else:
            active_n = next((n for n in range(1, 8) if stages[n][0] == "pending"), None)
            best = None
            for needle, sn in TL_LOG_MARKERS:
                idx = log_low.rfind(needle)
                if idx >= 0 and (best is None or idx > best[0]):
                    best = (idx, sn)
            if (
                best
                and active_n
                and best[1] > active_n
                and stages[best[1]][0] == "pending"
            ):
                active_n = best[1]
        if active_n:
            stages[active_n] = ("active", None)
    elif outcome == "failed":
        fail_n = next((n for n in range(1, 8) if stages[n][0] == "pending"), None)
        if fail_n:
            stages[fail_n] = ("failed", None)

    if (
        not live
        and state.get("render_rc") not in (None, 0)
        and stages[7][0]
        in (
            "done",
            "skipped",
        )
    ):
        stages[7] = ("failed", stages[7][1])

    if not live:
        for n in range(1, 8):
            if stages[n][0] == "pending":
                stages[n] = ("skipped", stages[n][1])

    t0 = _parse_iso_ts(meta.get("started_at"))
    if t0 is None:
        known = [e for (_s, e) in stages.values() if e]
        t0 = min(known) if known else None
    now_ts = now_sgt().timestamp()

    details = {}
    if stages[2][0] == "skipped" and coverage_p.exists():
        details[2] = "全部类别已覆盖"
    if stages[5][0] == "done":
        details[5] = (
            f"exclude={meta.get('exclude_category')}"
            if meta.get("exclude_category")
            else "日志确认重建完成 (PASS s9)"
        )
    if stages[6][0] == "blocked":
        details[6] = "资产缺口阻塞"
    if stages[6][0] == "failed":
        details[6] = f"rc={state.get('pipeline_rc')}"
    if stages[7][0] == "failed":
        details[7] = f"render_rc={state.get('render_rc')}"

    total_s = None
    fin = _parse_iso_ts(state.get("finished_at"))
    if t0 and fin and fin >= t0:
        total_s = fin - t0

    def _valid(ts):
        # 早于 run 开始的产物 mtime（复制/恢复的 fixture）不参与时间轴
        return bool(ts) and not (t0 and ts < t0 - 1)

    # ③④ 是②内部的子活动（真实时序 ①→③④→②收尾），用独立锚点
    sub_durs = {}
    shot_lo = min(shot_ms) if shot_ms else None
    shot_hi = max(shot_ms) if shot_ms else None
    if _valid(shot_lo):
        base3 = m_acq if _valid(m_acq) else t0
        if base3 and shot_lo >= base3:
            sub_durs[3] = shot_lo - base3
        sub_durs[4] = shot_hi - shot_lo

    out = []
    anchor = t0
    for n, key, title in STAGE_TITLES:
        st, end = stages[n]
        end_valid = _valid(end)
        dur = None
        if st in ("done", "blocked", "failed"):
            if n in (3, 4):
                dur = sub_durs.get(n)
            elif end_valid and anchor and end >= anchor:
                dur = end - anchor
        elif st == "active" and anchor:
            dur = max(0.0, now_ts - anchor)
        if dur is not None and total_s is not None and dur > total_s + 1:
            dur = None
        if end_valid and n not in (3, 4) and st in ("done", "blocked", "failed"):
            anchor = max(anchor, end) if anchor else end
        out.append(
            {
                "n": n,
                "key": key,
                "title": title,
                "status": st,
                "ended_at": end,
                "duration_s": dur,
                "detail": details.get(n),
            }
        )
    return out


@app.get("/api/run/<group>/<run_id>/status")
def api_run_status(group, run_id):
    run_dir = resolve_run_dir(group, run_id)

    log_tail = None
    log_text_full = ""
    log_path = run_dir / "run.log"
    if log_path.exists():
        try:
            log_text_full = log_path.read_text(errors="replace")
            log_tail = "\n".join(log_text_full.splitlines()[-100:])
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
        "stage_timeline": compute_stage_timeline(
            run_dir,
            _read_json(run_dir / "run_meta.json") or {},
            _read_json(run_dir / "run_state.json") or {},
            log_text_full,
        ),
        "log_size": log_path.stat().st_size if log_path.exists() else 0,
        "server_now": now_sgt().timestamp(),
    }
    return jsonify(payload)


# --- GET /api/run/<group>/<id>/log ----------------------------------------

LOG_CHUNK = 256 * 1024


@app.get("/api/run/<group>/<run_id>/log")
def api_run_log(group, run_id):
    run_dir = resolve_run_dir(group, run_id)
    p = run_dir / "run.log"
    if not p.is_file():
        return jsonify({"offset": 0, "size": 0, "chunk": "", "more": False})
    size = p.stat().st_size
    try:
        offset = max(0, min(int(request.args.get("offset", 0)), size))
    except ValueError:
        offset = 0
    with open(p, "rb") as f:
        f.seek(offset)
        data = f.read(LOG_CHUNK)
    return jsonify(
        {
            "offset": offset,
            "size": size,
            "chunk": data.decode("utf-8", errors="replace"),
            "more": offset + len(data) < size,
        }
    )


# --- GET /api/run/<group>/<id>/files --------------------------------------


@app.get("/api/run/<group>/<run_id>/files")
def api_run_files(group, run_id):
    run_dir = resolve_run_dir(group, run_id)
    files = []
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in ALLOWED_FILE_SUFFIXES:
            continue
        st = p.stat()
        files.append(
            {"p": str(p.relative_to(run_dir)), "size": st.st_size, "mtime": st.st_mtime}
        )
        if len(files) >= 500:
            break
    return jsonify({"files": files})


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
    ".py": "text/plain",
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


# --- asset library stats ---------------------------------------------------


def _thumb_available(asset_id, asset_lib):
    if (WEB_THUMBS / f"{asset_id}.png").is_file():
        return True
    return (Path(asset_lib) / asset_id / "snapshots" / "m0_default.png").is_file()


def _tier_scale(providers_in_tier, n_assets):
    parts = []
    for name, p in providers_in_tier:
        if name == "robotwin_local":
            parts.append(f"{n_assets} 资产")
        ip = p.get("index_path")
        if ip:
            data = _read_json(DEV / ip)
            try:
                if (
                    isinstance(data, dict)
                    and data
                    and all(isinstance(v, list) for v in data.values())
                ):
                    n = sum(len(v) for v in data.values())
                elif isinstance(data, dict):
                    n = len(data.get("keys", data))
                else:
                    n = len(data)
                parts.append(f"索引 {n:,}")
            except Exception:
                pass
        td = p.get("thumbs_dir")
        if td and (DEV / td).is_dir():
            n = sum(1 for f in (DEV / td).iterdir() if f.is_file())
            parts.append(f"视觉语料 {n:,} 张")
        dd = p.get("data_dir")
        if dd:
            # Objaverse LVIS 子集：物体/类别数取自注解文件
            try:
                import gzip

                with gzip.open(DEV / dd / "lvis-annotations.json.gz", "rt") as f:
                    lvis = json.load(f)
                if isinstance(lvis, dict) and lvis:
                    parts.append(
                        f"LVIS {sum(len(v) for v in lvis.values()):,} 物体"
                        f" · {len(lvis):,} 类"
                    )
            except Exception:
                pass
        if p.get("per_category_cap"):
            parts.append(f"每类取 ≤{p['per_category_cap']}")
        for r in p.get("repositories") or []:
            parts.append(r.get("repository", "?").split("/")[-1])
        if "repository_limit" in p:
            parts.append(f"动态发现 ≤{p['repository_limit']} repo")
    # 同层多个 provider 可能指向同一索引 —— 去重并保持顺序
    seen = set()
    deduped = [x for x in parts if not (x in seen or seen.add(x))]
    return " · ".join(deduped)


def compute_library_stats(catalog_path, asset_lib, providers_path):
    cat = _read_json(Path(catalog_path)) or {}
    entries = cat.get("entries", [])
    asset_lib = Path(asset_lib)
    lib_resolved = str(asset_lib.resolve())

    def imported(e):
        p = e.get("asset_path") or ""
        return p.startswith(lib_resolved) or "/data/asset_library/" in p

    categories = {}
    reasons = {}
    load_types = {}
    sources = {"robotwin_native": [], "imported": []}
    assets = {}
    n_models = 0
    n_avail = 0
    ann = {"materials": 0, "colors": 0, "aliases": 0}
    for e in entries:
        aid = e.get("asset_id")
        if not aid:
            continue
        n_models += len(e.get("models") or [])
        if e.get("available"):
            n_avail += 1
        else:
            for r in e.get("availability_reasons") or ["unknown"]:
                reasons.setdefault(str(r), []).append(aid)
        categories.setdefault(e.get("category") or "未分类", []).append(aid)
        load_types.setdefault(e.get("load_type") or "unknown", []).append(aid)
        sources["imported" if imported(e) else "robotwin_native"].append(aid)
        for k in ann:
            if e.get(k):
                ann[k] += 1
        assets[aid] = {
            "category": e.get("category"),
            "available": bool(e.get("available")),
            "load_type": e.get("load_type"),
            "models": len(e.get("models") or []),
            "thumb": _thumb_available(aid, asset_lib),
        }

    depth_buckets = {"1": 0, "2": 0, "3+": 0}
    for ids in categories.values():
        n = len(ids)
        depth_buckets["1" if n == 1 else "2" if n == 2 else "3+"] += 1
    top_cats = sorted(categories.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:12]

    recent = []
    for aid in sources["imported"]:
        d = asset_lib / aid
        try:
            recent.append(
                {
                    "asset_id": aid,
                    "category": assets[aid]["category"],
                    "mtime": d.stat().st_mtime,
                }
            )
        except OSError:
            continue
    recent.sort(key=lambda x: -x["mtime"])

    prov = _read_json(Path(providers_path)) or {}
    by_tier = {}
    for name, p in (prov.get("providers") or {}).items():
        if not isinstance(p, dict) or "tier" not in p:
            continue
        by_tier.setdefault(p["tier"], []).append((name, p))
    tiers = []
    for t in sorted(by_tier):
        ps = by_tier[t]
        tiers.append(
            {
                "tier": t,
                "providers": [n for n, _ in ps],
                "enabled": any(p.get("enabled") for _, p in ps),
                "scale": _tier_scale(ps, len(entries)),
            }
        )

    return {
        "catalog_mtime": _mtime(Path(catalog_path)),
        "kpis": {
            "assets": len(entries),
            "categories": len(categories),
            "model_variants": n_models,
            "available": n_avail,
            "imported": len(sources["imported"]),
        },
        "retrieval": {"tiers": tiers},
        "availability": {
            "available": n_avail,
            "total": len(entries),
            "reasons": [
                {"reason": r, "count": len(ids), "asset_ids": sorted(ids)}
                for r, ids in sorted(
                    reasons.items(), key=lambda kv: (-len(kv[1]), kv[0])
                )
            ],
        },
        "sources": [
            {
                "key": "robotwin_native",
                "label": "RoboTwin 原生",
                "count": len(sources["robotwin_native"]),
                "asset_ids": sorted(sources["robotwin_native"]),
            },
            {
                "key": "imported",
                "label": "缺口引进",
                "count": len(sources["imported"]),
                "asset_ids": sorted(sources["imported"]),
            },
        ],
        "load_types": [
            {"key": k, "count": len(ids), "asset_ids": sorted(ids)}
            for k, ids in sorted(
                load_types.items(), key=lambda kv: (-len(kv[1]), kv[0])
            )
        ],
        "category_depth": {
            "buckets": [
                {"depth": d, "categories": n} for d, n in depth_buckets.items()
            ],
            "singletons": depth_buckets["1"],
            "top": [
                {"category": c, "count": len(ids), "asset_ids": sorted(ids)}
                for c, ids in top_cats
            ],
        },
        "annotation": {"total": len(entries), **ann},
        "recent_imports": recent[:10],
        "assets": assets,
    }


_LIB_CACHE = {"mtime": None, "data": None}


@app.get("/api/library/stats")
def api_library_stats():
    key = (_mtime(DEFAULT_CATALOG), _mtime(WEB_THUMBS))
    if _LIB_CACHE["data"] is None or _LIB_CACHE["mtime"] != key:
        _LIB_CACHE["data"] = compute_library_stats(
            DEFAULT_CATALOG, ASSET_LIB, DEFAULT_PROVIDERS
        )
        _LIB_CACHE["mtime"] = key
    payload = dict(_LIB_CACHE["data"])
    payload["generated_at"] = now_sgt().timestamp()
    return jsonify(payload)


@app.get("/api/library/thumb/<asset_id>")
def api_library_thumb(asset_id):
    if not ID_RE.match(asset_id):
        abort(404)
    p = WEB_THUMBS / f"{asset_id}.png"
    if not p.is_file():
        p = ASSET_LIB / asset_id / "snapshots" / "m0_default.png"
    if not p.is_file():
        abort(404)
    return send_file(str(p), mimetype="image/png")


# --- GET / ------------------------------------------------------------


@app.get("/")
def index():
    return send_file(str(Path(__file__).parent / "index.html"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
