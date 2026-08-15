#!/usr/bin/env python3
"""Scene-driven adaptive acquisition: prompt -> coverage -> acquire gaps -> generate scene."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from lib import a4_coverage as a4  # noqa: E402
from runtime_config import GEN_ENV_ROOT  # noqa: E402

UP = str(GEN_ENV_ROOT)


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
    # One normalization applied to the whole chain: coverage parse AND the
    # upstream generate_scene call see the same prompt, or a compound like
    # "tissue-box" would ground differently in the two parses. The compound's
    # readable word forms travel as acquisition aliases.
    prompt, compound_aliases = a4.normalize_prompt_ex(a.prompt)
    spec, _ = a4.extract_needs(prompt, a.seed)
    records = a4.check_coverage(spec, a.catalog)
    gaps = a4.gaps_to_entries(records, extra_aliases=compound_aliases)
    # Parse-sanity guard: a gap category containing an article/preposition
    # means the parser fused a broken phrase into a "category" -- the whole
    # rest of a sentence, usually. Buying an asset for it launders a typo
    # into a pool entry: "Place a TVon the table" (an eaten space) became
    # category "tvon_the_table" and imported a sofa (2026-08-15). Refuse
    # loudly instead; the message names the suspect token.
    _STOP = {"the", "a", "an", "on", "in", "at", "of", "into", "onto"}
    bad = [
        (g["category"], w)
        for g in gaps
        for w in g["category"].replace("-", "_").split("_")
        if w in _STOP
    ]
    if bad:
        cat, w = bad[0]
        blocker = {
            "reason": "malformed_category_from_parse",
            "category": cat,
            "suspect_token": w,
            "hint": (
                "解析出的类目里含虚词，通常是 prompt 空格丢失或短语未被词表"
                "识别（如 'TVon the table'）。请检查空格/拼写后重试。"
            ),
        }
        (out / "asset_gap_blocker.json").write_text(
            json.dumps(blocker, indent=1, ensure_ascii=False)
        )
        print(f"FAIL scene_acquire: malformed category {cat!r} (token {w!r}) -- 解析异常，已拒绝采购")
        return 1
    catalog = a.catalog
    if gaps:
        before = records
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
                "--tier0-catalog",
                str(Path(a.catalog).resolve()),
            ]
        )
        rebuilt = Path(a.dev_root) / "data" / "scene_gen_ext" / "asset_catalog.json"
        if rebuilt.is_file():
            catalog = str(rebuilt)
        records = a4.check_coverage(spec, catalog)
        records = a4.mark_acquired(before, records)
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
    scenes_before = set(scenes_dir.glob("*/resolved_scene.json"))
    failures_before = set(scenes_dir.glob("*/failure_report.json"))
    rc = runner(
        [
            sys.executable,
            "script/generate_scene.py",
            "--prompt",
            prompt,
            "--seed",
            str(a.seed),
            "--asset-catalog",
            str(Path(catalog).resolve()),
            "--out-root",
            scenes_dir,
        ],
        cwd=UP,
    )
    new = set(scenes_dir.glob("*/resolved_scene.json")) - scenes_before
    if rc == 0 and new:
        newest = sorted(new)[-1]
        print(f"PASS scene_acquire scene={newest.parent.name}")
        return 0
    # The solver's failure report knows WHY nothing resolved ("footprint does
    # not fit stable support surface" x4656 for a duck on a cup) -- surface
    # the counted reasons instead of a bare "no resolved scene" so the Studio
    # can show what actually blocked the request.
    new_failures = set(scenes_dir.glob("*/failure_report.json")) - failures_before
    if new_failures:
        from collections import Counter

        report = json.loads(sorted(new_failures)[-1].read_text())
        reasons = Counter(
            reason
            for attempt in report.get("attempts", [])
            for reason in attempt.get("reasons", [])
        )
        (out / "solver_blocker.json").write_text(
            json.dumps(
                {
                    "schema": "envgen.solver_blocker.v1",
                    "prompt": a.prompt,
                    "scene_id": report.get("scene_id"),
                    "blocker": report.get("blocker"),
                    "total_attempts": report.get("total_attempts"),
                    "top_reasons": dict(reasons.most_common(5)),
                },
                indent=1,
                ensure_ascii=False,
            )
        )
        top = "; ".join(f"{k} x{v}" for k, v in reasons.most_common(2))
        print(f"FAIL scene_acquire: solver blocked ({report.get('blocker')}) -- {top}")
        return 1
    print("FAIL scene_acquire: no resolved scene produced")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
