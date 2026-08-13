#!/usr/bin/env python3
"""One-off (work/oneoff/): prompt-matrix stress campaign over the full
scene pipeline -- scene_acquire (parse -> coverage -> acquire -> solve) plus
the runtime replay validation for every resolved scene.

Each prompt row records: coverage statuses, acquire outcome, scene/blocker,
solver reasons if blocked, runtime fail count + failing checks. The summary
lands in campaign_report.json; failures are the campaign's product -- each
one either maps to a known-honest refusal class or becomes a new fix."""

import json
import subprocess
import sys
import time
from pathlib import Path

DEV = Path("/home/jingxiang/yuxin/env-gen-dev")
WT = Path("/home/jingxiang/yuxin/wt-main/1_asset_reuse")
UP = Path("/home/jingxiang/yuxin/env-gen-github")
PY = sys.executable
OUTROOT = Path("/tmp/campaign")

PROMPTS = [
    # --- singles: natives + externals + freshly imported ---
    ("single_apple", "Place an apple on the table."),
    ("single_hammer", "Place a hammer on the table."),
    ("single_bottle", "Place a bottle on the table."),
    ("single_plate", "Place a plate on the table."),
    ("single_tray", "Place a tray on the table."),
    ("single_crate", "Place a crate on the table."),
    ("single_duck", "Place a duck on the table."),
    ("single_tissuebox", "Place a tissue-box on the table."),
    ("single_basket", "Place a basket on the table."),
    ("single_block", "Place a block on the table."),
    ("single_scissors", "Place a scissors on the table."),
    # --- on top of ---
    ("on_cup_crate", "Place a cup on top of the crate."),
    ("on_apple_plate", "Put an apple on the plate."),
    ("on_block_tray", "Place a block on the tray."),
    ("on_duck_crate", "Put a duck on top of the crate."),
    # --- inside (bowl/cup interiors newly measured) ---
    ("in_apple_basket", "Put an apple inside the basket."),
    ("in_block_basket", "Put a block inside the basket."),
    ("in_apple_bowl", "Put an apple inside the bowl."),
    # --- near / positional ---
    ("near_bottle_bowl", "Place a bottle near the bowl."),
    ("near_tissue_bowl", "Place a tissue-box near the bowl."),
    ("left_knife_plate", "Place a knife to the left of the plate."),
    ("behind_hammer_block", "Place a hammer behind the block."),
    # --- Chinese ---
    ("zh_apple_plate", "把苹果放在盘子上"),
    ("zh_hammer_basket", "把锤子放进篮子里"),
    # --- boundary: expected-honest behaviours ---
    ("bd_duck_cup", "put a duck on the cup"),
    ("bd_mug_laptop", "Put a mug on the laptop."),
    ("bd_tea_cup", "Place a tea in the cup"),
    # --- acquisition triggers (objaverse) ---
    ("acq_snowman", "Place a snowman on the table."),
    ("acq_teddy", "Place a teddy bear on the table."),
]


def sh(cmd, cwd, timeout):
    try:
        r = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout[-3000:] + r.stderr[-1500:]
    except subprocess.TimeoutExpired:
        return -9, "TIMEOUT"


def main():
    OUTROOT.mkdir(exist_ok=True)
    rows = []
    for tag, prompt in PROMPTS:
        t0 = time.time()
        out = OUTROOT / tag
        subprocess.run(["rm", "-rf", str(out)])
        rc, log = sh(
            [
                PY,
                WT / "scripts/1_search/scene_acquire.py",
                "--prompt",
                prompt,
                "--seed",
                "42",
                "--catalog",
                DEV / "data/scene_gen_ext/asset_catalog.json",
                "--providers",
                WT / "configs/providers.json",
                "--dev-root",
                DEV,
                "--out",
                out,
            ],
            cwd=WT,
            timeout=1500,
        )
        row = {"tag": tag, "prompt": prompt, "acquire_rc": rc}
        cov = out / "coverage_report.json"
        if cov.exists():
            c = json.loads(cov.read_text())
            row["coverage"] = [(o["object_id"], o["status"]) for o in c["objects"]]
        for fname in ("asset_gap_blocker.json", "solver_blocker.json"):
            f = out / fname
            if f.exists():
                data = json.loads(f.read_text())
                row[fname.split(".")[0]] = data.get("top_reasons") or [
                    u.get("detail", "")[:120] for u in data.get("unmet", [])
                ]
        scenes = sorted(out.glob("scenes/*/resolved_scene.json"))
        if rc == 0 and scenes:
            rrc, rlog = sh(
                [
                    PY,
                    UP / "script/run_scene_runtime.py",
                    "--robotwin-root",
                    DEV / "data/robotwin_shadow",
                    "--resolved-scene",
                    scenes[-1],
                    "--asset-catalog",
                    DEV / "data/scene_gen_ext/asset_catalog.json",
                    "--out-dir",
                    out / "runtime",
                    "--settle-steps",
                    "600",
                    "--contact-window-steps",
                    "60",
                    "--video-frames",
                    "24",
                    "--fps",
                    "12",
                ],
                cwd=UP,
                timeout=900,
            )
            row["runtime_rc"] = rrc
            rep = out / "runtime/runtime_validation_report.json"
            if rep.exists():
                r = json.loads(rep.read_text())
                row["runtime_fail"] = r["fail_count"]
                row["runtime_failing"] = [
                    c["name"]
                    for c in r["checks"]
                    if c["status"] not in ("pass", "not_applicable")
                ]
        row["seconds"] = round(time.time() - t0, 1)
        rows.append(row)
        ok = row.get("runtime_fail") == 0 if "runtime_fail" in row else rc != 0
        print(
            f"[{len(rows)}/{len(PROMPTS)}] {tag}: acquire_rc={rc} "
            f"runtime_fail={row.get('runtime_fail', '-')} "
            f"{'OK' if row.get('runtime_fail') == 0 else '...'} "
            f"({row['seconds']}s)",
            flush=True,
        )
        (OUTROOT / "campaign_report.json").write_text(
            json.dumps({"rows": rows}, indent=1, ensure_ascii=False)
        )
    n_scene_ok = sum(1 for r in rows if r.get("runtime_fail") == 0)
    n_blocked = sum(
        1 for r in rows if "asset_gap_blocker" in r or "solver_blocker" in r
    )
    print(
        f"\n完成: {len(rows)} prompts | 场景+回放全过 {n_scene_ok} | "
        f"blocker {n_blocked} | 其余为失败待研究"
    )


if __name__ == "__main__":
    main()
