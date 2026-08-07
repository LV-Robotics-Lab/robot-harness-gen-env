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


def _attempt_import(
    rec,
    viable,
    entry,
    category,
    globals_cfg,
    paths,
    runner,
    max_attempts,
    preallocated=None,
):
    imported = None
    for i, r in enumerate(viable[:max_attempts]):
        candidate = r["candidate"]
        rec["attempts"] += 1
        if i == 0 and preallocated is not None:
            asset, model = preallocated
        else:
            asset, model = a2.allocate_asset(
                category, paths["library"], paths["manifest"]
            )
        group = a2.build_manifest_group(candidate, asset, model, entry)
        out = Path(paths["out"])
        out.mkdir(parents=True, exist_ok=True)
        staging = out / f"staging_{asset}_m{model}"
        fragment = Path(paths["fragment_dir"]) / f"{asset}_m{model}.yml"
        fragment.parent.mkdir(parents=True, exist_ok=True)
        if str(candidate.provider).startswith("github"):
            from lib import a3_webfetch as a3w

            try:
                a3w.stage_web_candidate(
                    candidate, entry, asset, model, staging, out / "webcache"
                )
            except a3w.ConvertError as exc:
                r["verdict"] = "rejected"
                r["rejection"] = {
                    "code": a2.REJ_CONVERT,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
                continue
            except Exception as exc:  # noqa: BLE001
                r["verdict"] = "rejected"
                r["rejection"] = {
                    "code": a2.REJ_FETCH,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
                continue
        else:
            tmp_manifest = out / f"manifest_{asset}_m{model}.json"
            tmp_manifest.write_text(json.dumps({"groups": [group]}, indent=1))
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
    outranked_detail = (
        "ranked below selected"
        if imported
        else "not attempted; fallback budget exhausted"
    )
    for r in viable[used if imported is None else max(used, 1) :]:
        if r["verdict"] == "viable":
            r["verdict"] = "outranked"
            r["rejection"] = {
                "code": a2.REJ_OUTRANKED,
                "detail": outranked_detail,
            }
    rec["candidates"] = [
        {
            **a2.candidate_dict(r["candidate"]),
            "verdict": r["verdict"],
            "rejection": r.get("rejection"),
        }
        for r in viable
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


def process_entry(entry, tiers, globals_cfg, paths, runner):
    category = entry["category"]
    if "pinned" in entry:
        rec = {
            "query": {"category": category},
            "entry_mode": "pinned",
            "candidates": [],
            "attempts": 0,
            "tiers_consulted": [],
            "provider_errors": [],
        }
        asset, model = a2.allocate_asset(category, paths["library"], paths["manifest"])
        if model > 0:
            rec["status"] = "reused_local"
            rec["local_reuse"] = {"asset_id": asset, "reason": a2.ALREADY}
            return rec
        candidate = a2.pinned_candidate(entry)
        gated = a2.gate_candidates([candidate], globals_cfg)
        if gated[0]["verdict"] != "viable":
            rec["candidates"] = [
                {
                    **a2.candidate_dict(candidate),
                    "verdict": "rejected",
                    "rejection": gated[0]["rejection"],
                }
            ]
            rec["status"] = "exhausted"
            return rec
        return _attempt_import(
            rec,
            [gated[0]],
            entry,
            category,
            globals_cfg,
            paths,
            runner,
            max_attempts=1,
            preallocated=(asset, model),
        )
    if "local" in entry:
        rec = {
            "query": {"category": category},
            "entry_mode": "local",
            "candidates": [],
            "attempts": 0,
            "tiers_consulted": [],
            "provider_errors": [],
        }
        from lib import a3_webfetch as a3w
        from agenticsim.openxsim.assets import AssetCandidate

        asset, model = a2.allocate_asset(category, paths["library"], paths["manifest"])
        if model > 0:
            rec["status"] = "reused_local"
            rec["local_reuse"] = {"asset_id": asset, "reason": a2.ALREADY}
            return rec
        out = Path(paths["out"])
        staging = out / f"staging_{asset}_m{model}"
        src = Path(entry["local"]["path"])
        if not src.is_file():
            rec["status"] = "exhausted"
            rec["candidates"] = [
                {
                    "candidate_id": f"local:{src}",
                    "provider": "local",
                    "url": str(src),
                    "format": src.suffix.lstrip("."),
                    "license": "user-provided",
                    "score": 0.0,
                    "verdict": "rejected",
                    "rejection": {"code": a2.REJ_FETCH, "detail": "file missing"},
                }
            ]
            return rec
        try:
            glb = a3w.to_glb(src, staging / f"{asset}_m{model}.glb")
            record = a3w.synth_staging_record(
                glb,
                src,
                a3w._sha256(src),
                asset,
                model,
                entry,
                up_axis=entry["local"].get("up_axis", "Y"),
            )
        except Exception as exc:  # noqa: BLE001
            rec["status"] = "exhausted"
            rec["candidates"] = [
                {
                    "candidate_id": f"local:{src}",
                    "provider": "local",
                    "url": str(src),
                    "format": src.suffix.lstrip("."),
                    "license": "user-provided",
                    "score": 0.0,
                    "verdict": "rejected",
                    "rejection": {
                        "code": a2.REJ_CONVERT,
                        "detail": f"{type(exc).__name__}: {exc}",
                    },
                }
            ]
            return rec
        candidate = AssetCandidate(
            candidate_id=f"local:{src}",
            name=src.name,
            category=category,
            download_url=str(src),
            source_page=str(src),
            format="glb",
            provider="local",
            license="user-provided",
            score=0.0,
            metadata={
                "key": str(src),
                "path": str(src),
                "size_bytes": Path(glb).stat().st_size,
            },
        )
        gated = a2.gate_candidates([candidate], globals_cfg)
        if gated[0]["verdict"] != "viable":
            rec["status"] = "exhausted"
            rec["candidates"] = [
                {
                    **a2.candidate_dict(candidate),
                    "verdict": "rejected",
                    "rejection": gated[0]["rejection"],
                }
            ]
            return rec
        rec["attempts"] = 1
        (staging / "staging_manifest.json").write_text(json.dumps([record], indent=1))
        fragment = Path(paths["fragment_dir"]) / f"{asset}_m{model}.yml"
        fragment.parent.mkdir(parents=True, exist_ok=True)
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
            rec["status"] = "imported"
            rec["selected"] = {
                **a2.candidate_dict(candidate),
                "asset": asset,
                "model": model,
            }
            group = a2.build_manifest_group(candidate, asset, model, entry)
            a2.append_manifest(paths["manifest"], group)
        else:
            rec["status"] = "exhausted"
            rec["candidates"] = [
                {
                    **a2.candidate_dict(candidate),
                    "verdict": "rejected",
                    "rejection": {
                        "code": "validation_failed:materialize",
                        "detail": f"{asset} m{model} not materialized; see import matrix under {out}",
                    },
                }
            ]
        return rec
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
    rec = _attempt_import(
        rec, viable, entry, category, globals_cfg, paths, runner, max_attempts
    )
    rec["candidates"] = [
        {
            **a2.candidate_dict(r["candidate"]),
            "verdict": r["verdict"],
            "rejection": r.get("rejection"),
        }
        for r in gated
    ]
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
    results = []
    for e in entries:
        try:
            results.append(process_entry(e, tiers, globals_cfg, paths, runner))
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "query": {"category": str(e.get("category", "<invalid>"))},
                    "entry_mode": "error",
                    "status": "entry_error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "candidates": [],
                    "attempts": 0,
                }
            )
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
        categories_input=entries,
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
