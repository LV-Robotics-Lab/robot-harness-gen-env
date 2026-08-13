#!/usr/bin/env python3
"""One-off (work/oneoff/): overnight FULL sweep.

Phase A  every usable catalog category x "Place a {cat} on the table."
Phase B  relation forms over measured-suitable pairs (on-top targets = flat
         survey verdicts; inside targets = declared/measured interiors) +
         near/left/Chinese samples.
Phase C  NVIDIA corpus sampling: category words extracted from the 10,696
         thumbnailed asset names, random sample driven through the FULL
         acquisition chain. (A complete per-asset traversal of the corpus is
         ~weeks of GPU; the sample is sized to visit every crash class.)

Each row: acquire rc, coverage, blocker reasons, runtime fail count. Disk
hygiene: per-case runtime video and staging are deleted after the verdict is
recorded. Output: ~/yuxin/sweep_out/sweep_report.json (rewritten after every
case; kept OUT of /tmp because a mid-sweep reboot on 2026-08-13 wiped the
first run's report). If a previous report exists, its rows are reused and
only missing tags run.
"""

import json
import os
import random
import re
import subprocess
import sys
import time
import shutil
import urllib.parse
from pathlib import Path

DEV = Path("/home/jingxiang/yuxin/env-gen-dev")
WT = Path("/home/jingxiang/yuxin/wt-main/1_asset_reuse")
UP = Path("/home/jingxiang/yuxin/env-gen-github")
PY = sys.executable
OUT = Path("/home/jingxiang/yuxin/sweep_out")
CATALOG = DEV / "data/scene_gen_ext/asset_catalog.json"


def run_case(tag, prompt, do_runtime=True, timeout=1500):
    t0 = time.time()
    d = OUT / tag
    shutil.rmtree(d, ignore_errors=True)
    try:
        r = subprocess.run(
            [
                PY,
                str(WT / "scripts/1_search/scene_acquire.py"),
                "--prompt",
                prompt,
                "--seed",
                "42",
                "--catalog",
                str(CATALOG),
                "--providers",
                str(WT / "configs/providers.json"),
                "--dev-root",
                str(DEV),
                "--out",
                str(d),
            ],
            cwd=str(WT),
            capture_output=True,
            text=True,
            timeout=timeout,
            # the retrieval walk imports agenticsim from shared/; the old
            # runs inherited it from an interactive shell's PYTHONPATH,
            # which a clean nohup relaunch does not have (2026-08-13)
            env={
                **os.environ,
                "PYTHONPATH": f"{WT}:{WT.parent}/shared/openxsim/source/agenticsim:{UP}",
            },
        )
    except subprocess.TimeoutExpired:
        # a hung download (C_taxi, 30 min dead network) must cost one row,
        # not the whole sweep
        return {
            "tag": tag,
            "prompt": prompt,
            "acquire_rc": -9,
            "timeout": True,
            "seconds": round(time.time() - t0, 1),
        }
    row = {"tag": tag, "prompt": prompt, "acquire_rc": r.returncode}
    tail = (r.stdout + r.stderr)[-600:]
    if "Traceback" in r.stdout + r.stderr:
        row["crash"] = tail
    cov = d / "coverage_report.json"
    if cov.exists():
        row["coverage"] = [
            (o["object_id"], o["status"], o.get("asset_id"))
            for o in json.loads(cov.read_text())["objects"]
        ]
    for f in ("asset_gap_blocker.json", "solver_blocker.json"):
        ff = d / f
        if ff.exists():
            data = json.loads(ff.read_text())
            row[f.split(".")[0]] = data.get("top_reasons") or [
                u.get("detail", "")[:100] for u in data.get("unmet", [])
            ]
    scenes = sorted(d.glob("scenes/*/resolved_scene.json"))
    if r.returncode == 0 and scenes and do_runtime:
        try:
            rr = subprocess.run(
                [
                    PY,
                    str(UP / "script/run_scene_runtime.py"),
                    "--robotwin-root",
                    str(DEV / "data/robotwin_shadow"),
                    "--resolved-scene",
                    str(scenes[-1]),
                    "--asset-catalog",
                    str(CATALOG),
                    "--out-dir",
                    str(d / "runtime"),
                    "--settle-steps",
                    "600",
                    "--contact-window-steps",
                    "60",
                    "--video-frames",
                    "12",
                    "--fps",
                    "6",
                ],
                cwd=str(UP),
                capture_output=True,
                text=True,
                timeout=900,
                # rebuilt curobo (vendored, pinned version) hits an async
                # CUDA launch race on sm_120; serialized launches are the
                # only mode that survives its warmup (verified 2026-08-13)
                env={**os.environ, "CUDA_LAUNCH_BLOCKING": "1"},
            )
        except subprocess.TimeoutExpired:
            row["runtime_fail"] = -2
            row["crash_rt"] = "runtime timeout (900s)"
            row["seconds"] = round(time.time() - t0, 1)
            return row
        rep = d / "runtime/runtime_validation_report.json"
        if rep.exists():
            rj = json.loads(rep.read_text())
            row["runtime_fail"] = rj["fail_count"]
            row["failing"] = [
                c["name"]
                for c in rj["checks"]
                if c["status"] not in ("pass", "not_applicable")
            ]
        else:
            row["runtime_fail"] = -1
            row["crash_rt"] = (rr.stdout + rr.stderr)[-400:]
    row["seconds"] = round(time.time() - t0, 1)
    # disk hygiene: keep verdict files, drop bulk
    for sub in ("runtime/observer_runtime.mp4", "acquire"):
        shutil.rmtree(d / sub, ignore_errors=True)
        (d / sub).unlink(missing_ok=True) if (d / sub).is_file() else None
    return row


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    random.seed(20260813)
    rows = []

    prev = {}
    rp = OUT / "sweep_report.json"
    if rp.exists():
        try:
            prev = {r["tag"]: r for r in json.loads(rp.read_text())["rows"]}
            print(f"续跑：沿用已有 {len(prev)} 行，只跑缺失 tag", flush=True)
        except Exception:
            prev = {}

    def run_or_prev(tag, prompt, **kw):
        if tag in prev:
            return prev[tag]
        return run_case(tag, prompt, **kw)

    def flush():
        (OUT / "sweep_report.json").write_text(
            json.dumps({"rows": rows}, indent=1, ensure_ascii=False)
        )

    cat = json.loads(CATALOG.read_text())
    entries = cat["entries"] if isinstance(cat, dict) else cat
    usable_cats = sorted(
        {
            e["category"]
            for e in entries
            if any(m.get("usable") for m in e.get("models", []))
        }
    )
    print(f"Phase A: {len(usable_cats)} 类目单放", flush=True)
    for i, c in enumerate(usable_cats):
        word = c.replace("_", " ")
        row = run_or_prev(f"A_{c}", f"Place a {word} on the table.")
        rows.append(row)
        flush()
        ok = row.get("runtime_fail") == 0
        print(
            f"[A {i + 1}/{len(usable_cats)}] {c}: rc={row['acquire_rc']} "
            f"rt={row.get('runtime_fail', '-')} {'OK' if ok else '<<'} "
            f"({row['seconds']}s)",
            flush=True,
        )

    # Phase B: relation forms from measured data
    survey = json.loads(
        (DEV / "data/scene_gen_ext/top_support_survey.json").read_text()
    )
    flat_assets = sorted(
        {
            aid
            for aid, ms in survey["models"].items()
            for mid, r in ms.items()
            if r.get("verdict") == "flat"
        }
    )
    import yaml

    ov = yaml.safe_load(
        (DEV / "data/scene_gen_ext/asset_overrides_ext.yml").read_text()
    )["assets"]
    interior_assets = sorted(
        aid
        for aid, a in ov.items()
        if any("interior_dimensions_m" in m for m in (a.get("models") or {}).values())
    )
    cat_of = {e["asset_id"]: e["category"] for e in entries}
    tops = [cat_of[a].replace("_", " ") for a in flat_assets if a in cat_of][:6]
    containers = [cat_of[a].replace("_", " ") for a in interior_assets if a in cat_of][
        :6
    ]
    smalls = ["apple", "block", "can", "cup", "banana", "hammer"]
    b_cases = []
    for t in tops[:4]:
        b_cases.append(("on", f"Place a {random.choice(smalls)} on top of the {t}."))
    for cont in containers[:4]:
        b_cases.append(("in", f"Put a {random.choice(smalls)} inside the {cont}."))
    b_cases += [
        ("near", "Place a bottle near the bowl."),
        ("left", "Place a knife to the left of the plate."),
        ("zh1", "把苹果放在盘子上"),
        ("zh2", "把积木放进篮子里"),
    ]
    print(f"Phase B: {len(b_cases)} 关系形式", flush=True)
    for i, (k, p) in enumerate(b_cases):
        row = run_or_prev(f"B{i}_{k}", p)
        rows.append(row)
        flush()
        print(
            f"[B {i + 1}/{len(b_cases)}] {p[:40]}: rc={row['acquire_rc']} "
            f"rt={row.get('runtime_fail', '-')} ({row['seconds']}s)",
            flush=True,
        )

    # Phase C: NVIDIA corpus sampling via thumbnail-backed asset names
    thumbs = DEV / "data/asset_index/thumbs_50k"
    words = {}
    for f in thumbs.glob("*.png"):
        stem = urllib.parse.unquote(f.name)[:-4].rsplit("/", 1)[-1]
        stem = re.sub(r"\.usd$", "", stem)
        stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
        toks = [
            w.lower()
            for w in re.split(r"[^A-Za-z]+", stem)
            if len(w) > 3 and not w.isdigit()
        ]
        for t in toks:
            words[t] = words.get(t, 0) + 1
    stop = {
        "assets",
        "isaac",
        "nvidia",
        "instanceable",
        "base",
        "prop",
        "props",
        "mesh",
        "model",
        "small",
        "large",
        "left",
        "right",
    }
    pool = sorted(w for w, n in words.items() if n >= 2 and w not in stop)
    sample = random.sample(pool, min(120, len(pool)))
    print(
        f"Phase C: 采样 {len(sample)} 个 NVIDIA 语料词 (词池 {len(pool)})", flush=True
    )
    for i, w in enumerate(sample):
        row = run_or_prev(f"C_{w}", f"Place a {w} on the table.", timeout=1800)
        rows.append(row)
        flush()
        print(
            f"[C {i + 1}/{len(sample)}] {w}: rc={row['acquire_rc']} "
            f"rt={row.get('runtime_fail', '-')} ({row['seconds']}s)",
            flush=True,
        )

    n_ok = sum(1 for r in rows if r.get("runtime_fail") == 0)
    n_crash = sum(1 for r in rows if "crash" in r or "crash_rt" in r)
    print(f"\nSWEEPDONE 总{len(rows)} 全绿{n_ok} 崩溃{n_crash}", flush=True)


if __name__ == "__main__":
    main()
