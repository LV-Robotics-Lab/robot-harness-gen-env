#!/usr/bin/env python3
"""Batch acquire engine: category entries -> tiered search -> gates -> existing import pipeline.

Content-judged: per-category PASS/FAIL lines + SUMMARY; artifacts decide, not exit codes
of Kit subprocesses. Writes selection_evidence.json per run.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import a1_providers as a1  # noqa: E402
from lib import a2_selection as a2  # noqa: E402

PY_SAP = "/home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python"
PY_ISA = "/home/jingxiang/miniconda3/envs/isaac-smoke/bin/python"
MAIN_TREE_CATALOG_FALLBACK = (
    "/home/jingxiang/yuxin/env-gen-dev/data/scene_gen_ext/asset_catalog.json"
)


def default_runner(cmd, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([str(c) for c in cmd], env=e).returncode


def resolve_catalog_path(dev_root, catalog_cfg, fallback):
    """Prefer the dev-root's own rebuilt catalog; fall back to the main-tree one."""
    p = Path(catalog_cfg)
    resolved = p if p.is_absolute() else Path(dev_root) / p
    return resolved if resolved.is_file() else Path(fallback)


def check_imported(library_dir, asset, model):
    d = Path(library_dir) / asset
    return (d / f"model_data{model}.json").is_file() and (
        d / "visual" / f"base{model}.glb"
    ).is_file()


def process_entry(entry, tiers, globals_cfg, paths, runner):
    category = entry["category"]
    query = " ".join([category, *entry.get("colors", []), *entry.get("aliases", [])])
    rec = {
        "query": {"category": category, "aliases": entry.get("aliases", [category])},
        "entry_mode": "searched",
        "candidates": [],
        "attempts": 0,
    }
    res = a1.tiered_search(
        tiers,
        query,
        viable_fn=lambda c: a2.gate(c, globals_cfg) is None,
        limit=int(globals_cfg.get("top_k", 5)),
    )
    rec["tiers_consulted"] = res["tiers_consulted"]
    rec["provider_errors"] = res["provider_errors"]
    if res["tier0_hit"] is not None:
        rec["status"] = "reused_local"
        rec["local_reuse"] = {
            "asset_id": res["tier0_hit"].metadata.get("asset_id"),
            "reason": a2.ALREADY,
        }
        return rec
    gated = a2.gate_candidates(res["candidates"], globals_cfg)
    viable = [r for r in gated if r["verdict"] == "viable"]
    if not viable:
        rec["status"] = "search_failed" if not res["candidates"] else "exhausted"
        rec["candidates"] = [
            {
                **a2.candidate_dict(r["candidate"]),
                "verdict": r["verdict"],
                "rejection": r["rejection"],
            }
            for r in gated
        ]
        return rec
    max_attempts = 1 + int(globals_cfg.get("max_fallback", 2))
    imported = None
    for r in viable[:max_attempts]:
        candidate = r["candidate"]
        rec["attempts"] += 1
        asset, model = a2.allocate_asset(category, paths["library"], paths["manifest"])
        group = a2.build_manifest_group(candidate, asset, model, entry)
        out = Path(paths["out"])
        out.mkdir(parents=True, exist_ok=True)
        staging = out / f"staging_{asset}_m{model}"
        tmp_manifest = out / f"manifest_{asset}_m{model}.json"
        tmp_manifest.write_text(json.dumps({"groups": [group]}, indent=1))
        fragment = Path(paths["fragment_dir"]) / f"{asset}_m{model}.yml"
        fragment.parent.mkdir(parents=True, exist_ok=True)
        runner(
            [
                paths["py_isa"],
                "-u",
                paths["scripts"] / "import_fetch_convert.py",
                "--manifest",
                tmp_manifest,
                "--source-root",
                paths["source"],
                "--staging",
                staging,
            ],
            env={"OMNI_KIT_ACCEPT_EULA": "YES"},
        )
        runner(
            [
                paths["py_sap"],
                paths["scripts"] / "import_materialize.py",
                "--staging",
                staging,
                "--library-dir",
                paths["library"],
                "--out",
                out,
                "--overrides-fragment",
                fragment,
            ]
        )
        if check_imported(paths["library"], asset, model):
            r["verdict"] = "selected"
            imported = (candidate, asset, model)
            a2.append_manifest(paths["manifest"], group)
            break
        r["verdict"] = "rejected"
        r["rejection"] = {
            "code": "validation_failed:materialize",
            "detail": f"{asset} m{model} not materialized; see import matrix under {out}",
        }
    used = rec["attempts"]
    for r in viable[used if imported is None else max(used, 1) :]:
        if r["verdict"] == "viable":
            r["verdict"] = "outranked"
            r["rejection"] = {
                "code": a2.REJ_OUTRANKED,
                "detail": "ranked below selected",
            }
    rec["candidates"] = [
        {
            **a2.candidate_dict(r["candidate"]),
            "verdict": r["verdict"],
            "rejection": r.get("rejection"),
        }
        for r in gated
    ]
    if imported:
        candidate, asset, model = imported
        rec["status"] = "imported"
        rec["selected"] = {
            **a2.candidate_dict(candidate),
            "asset": asset,
            "model": model,
        }
    else:
        rec["status"] = "exhausted"
    return rec


def main(argv=None, runner=None, tiers=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories", required=True)
    ap.add_argument("--providers", required=True)
    ap.add_argument("--dev-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--refresh-index", action="store_true")
    a = ap.parse_args(argv)
    runner = runner or default_runner
    dev = Path(a.dev_root)
    cfg = json.loads(Path(a.providers).read_text())
    if tiers is None:
        tiers, globals_cfg = a1.load_providers(cfg)
        for t in tiers:
            if getattr(t.provider, "name", "") == "nvidia_server":
                t.provider.index_path = (
                    dev / t.provider.index_path
                    if not Path(t.provider.index_path).is_absolute()
                    else Path(t.provider.index_path)
                )
                t.provider.ensure_index(refresh=a.refresh_index)
            elif getattr(t.provider, "name", "") == "robotwin_local":
                t.provider.catalog_path = resolve_catalog_path(
                    dev, t.provider.catalog_path, MAIN_TREE_CATALOG_FALLBACK
                )
    else:
        globals_cfg = cfg.get("globals", {})
    paths = {
        "py_sap": PY_SAP,
        "py_isa": PY_ISA,
        "scripts": Path(__file__).resolve().parent,
        "library": dev / "data" / "asset_library",
        "source": dev / "data" / "asset_library" / "_source",
        "out": Path(a.out),
        "manifest": dev / "1_asset_reuse" / "configs" / "acquired_manifest.json",
        "fragment_dir": Path(a.out) / "fragments",
    }
    entries = json.loads(Path(a.categories).read_text())
    results = [process_entry(e, tiers, globals_cfg, paths, runner) for e in entries]
    imported = [r for r in results if r["status"] == "imported"]
    if imported:
        merged = Path(a.out) / "overrides_ext_all.yml"
        merged.write_text(
            "\n".join(
                p.read_text() for p in sorted(Path(paths["fragment_dir"]).glob("*.yml"))
            )
        )
        runner(
            [
                PY_SAP,
                paths["scripts"] / "s9_build_shadow_root.py",
                "--library-dir",
                paths["library"],
                "--shadow",
                dev / "data" / "robotwin_shadow",
                "--ext-dir",
                dev / "data" / "scene_gen_ext",
                "--extra-overrides",
                merged,
            ]
        )
    a2.write_evidence(
        Path(a.out) / "selection_evidence.json",
        run_id=Path(a.out).name,
        providers_snapshot=cfg,
        categories=results,
    )
    ok = True
    for r in results:
        good = r["status"] in {"imported", "reused_local"}
        ok = ok and good
        print(
            f"{'PASS' if good else 'FAIL'} {r['query']['category']} status={r['status']}"
        )
    print(f"SUMMARY {'PASS' if ok else 'FAIL'} imported={len(imported)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
