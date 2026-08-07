#!/usr/bin/env python3
"""Scene-driven adaptive acquisition: prompt -> coverage -> acquire gaps -> generate scene."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import a4_coverage as a4  # noqa: E402

UP = "/home/jingxiang/yuxin/env-gen-github"


def default_runner(cmd, cwd=None, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([str(c) for c in cmd], cwd=cwd, env=e).returncode


def main(argv=None, runner=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--providers", required=True)
    ap.add_argument("--dev-root", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runner = runner or default_runner
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    spec, _ = a4.extract_needs(a.prompt, a.seed)
    records = a4.check_coverage(spec, a.catalog)
    gaps = a4.gaps_to_entries(records)
    catalog = a.catalog
    if gaps:
        (out / "acquire_categories.json").write_text(
            json.dumps(gaps, indent=1, ensure_ascii=False)
        )
        runner(
            [
                sys.executable,
                Path(__file__).with_name("acquire_batch.py"),
                "--categories",
                out / "acquire_categories.json",
                "--providers",
                a.providers,
                "--dev-root",
                a.dev_root,
                "--out",
                out / "acquire",
            ]
        )
        rebuilt = Path(a.dev_root) / "data" / "scene_gen_ext" / "asset_catalog.json"
        if rebuilt.is_file():
            catalog = str(rebuilt)
        records = a4.check_coverage(spec, catalog)
    a4.write_coverage_report(out / "coverage_report.json", a.prompt, a.seed, records)
    remaining = [r for r in records if r["status"] == "gap"]
    if remaining:
        (out / "asset_gap_blocker.json").write_text(
            json.dumps(
                {
                    "schema": "envgen.asset_gap_blocker.v1",
                    "prompt": a.prompt,
                    "seed": a.seed,
                    "unmet": remaining,
                    "note": "retrieval exhausted across all tiers; input for generation fallback",
                },
                indent=1,
                ensure_ascii=False,
            )
        )
        print(
            f"FAIL scene_acquire: {len(remaining)} unmet -> {out / 'asset_gap_blocker.json'}"
        )
        return 1
    scenes_dir = out / "scenes"
    before = set(scenes_dir.glob("*/resolved_scene.json"))
    rc = runner(
        [
            sys.executable,
            "script/generate_scene.py",
            "--prompt",
            a.prompt,
            "--seed",
            str(a.seed),
            "--asset-catalog",
            str(Path(catalog).resolve()),
            "--out-root",
            scenes_dir,
        ],
        cwd=UP,
    )
    new = set(scenes_dir.glob("*/resolved_scene.json")) - before
    if rc == 0 and new:
        newest = sorted(new)[-1]
        print(f"PASS scene_acquire scene={newest.parent.name}")
        return 0
    print("FAIL scene_acquire: no resolved scene produced")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
