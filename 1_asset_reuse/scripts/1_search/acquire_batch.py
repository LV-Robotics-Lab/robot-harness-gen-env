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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import a1_providers as a1  # noqa: E402
from lib import a2_selection as a2  # noqa: E402
from lib import a5_alias as a5  # noqa: E402

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


def _materialize_gate(out_dir, asset, model):
    """Extract a short gate keyword from import_materialize.py's import_matrix.json
    for the given asset/model, or None if unavailable/unrecognized."""
    path = Path(out_dir) / "import_matrix.json"
    if not path.is_file():
        return None
    try:
        matrix = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(matrix, list):
        return None
    for row in matrix:
        if not isinstance(row, dict):
            continue
        if row.get("asset") == asset and row.get("model") == model:
            text = " ".join(str(x) for x in row.get("reasons", [])).lower()
            for keywords, gate in (
                (("settle", "settled"), "settle"),
                (("penetration",), "penetration"),
                (("tilt",), "tilt"),
                (("height", "plausib"), "height"),
            ):
                if any(k in text for k in keywords):
                    return gate
            return None
    return None


def _make_expand_fn(cache_path, llm_cfg, llm_fn):
    """Alias expansion always runs (degraded mode: cache hit or compound_split
    fallback), independent of whether an LLM is configured."""

    def expand_fn(category, aliases):
        cache = a5.load_alias_cache(cache_path)
        result = a5.expand_terms(category, aliases, llm_cfg, llm_fn=llm_fn, cache=cache)
        if result["source"] == "llm":
            # Reload-merge-save right before persisting (rather than saving
            # the `cache` dict loaded at the top of this call) to shrink the
            # lost-update window against a concurrent acquire_batch run that
            # wrote a different entry in between.
            fresh = a5.load_alias_cache(cache_path)
            fresh[category.lower()] = list(result["added"])
            a5.save_alias_cache(cache_path, fresh)
        return result

    return expand_fn


def _make_screen_fn(llm_cfg, llm_fn):
    def screen_fn(category, candidates):
        return a5.screen_candidates(category, candidates, llm_cfg, llm_fn=llm_fn)

    return screen_fn


def _rejected(candidate, code, detail):
    """Build the standard rejected-candidate record: candidate_dict fields plus
    verdict/rejection. `candidate` may be an AssetCandidate or an already
    candidate_dict-shaped mapping (used before an AssetCandidate exists)."""
    base = candidate if isinstance(candidate, dict) else a2.candidate_dict(candidate)
    return {
        **base,
        "verdict": "rejected",
        "rejection": {"code": code, "detail": detail},
    }


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
                    paths["scripts"] / "2_convert" / "import_fetch_convert.py",
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
                paths["scripts"] / "3_materialize" / "import_materialize.py",
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
        gate_name = _materialize_gate(out, asset, model)
        code = (
            f"validation_failed:{gate_name}"
            if gate_name
            else "validation_failed:materialize"
        )
        r["verdict"] = "rejected"
        r["rejection"] = {
            "code": code,
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


def process_entry(
    entry, tiers, globals_cfg, paths, runner, expand_fn=None, screen_fn=None
):
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
        stub = {
            "candidate_id": f"local:{src}",
            "provider": "local",
            "url": str(src),
            "format": src.suffix.lstrip("."),
            "license": "user-provided",
            "score": 0.0,
        }
        if not src.is_file():
            rec["status"] = "exhausted"
            rec["candidates"] = [_rejected(stub, a2.REJ_FETCH, "file missing")]
            return rec
        try:
            record = a3w.stage_source(
                src,
                a3w._sha256(src),
                entry,
                asset,
                model,
                staging,
                up_axis=entry["local"].get("up_axis", "Y"),
            )
        except a3w.ConvertError as exc:
            rec["status"] = "exhausted"
            rec["candidates"] = [_rejected(stub, a2.REJ_CONVERT, str(exc))]
            return rec
        except Exception as exc:  # noqa: BLE001
            # stage_source's own staging.mkdir sits outside its try/except (so the
            # github path can still classify mkdir failures as fetch_failed, not
            # convert_failed) -- catch that and any other unwrapped exception here
            # too, so the local path still degrades to a clean rejection instead of
            # leaking to main()'s per-entry entry_error handler.
            rec["status"] = "exhausted"
            rec["candidates"] = [
                _rejected(stub, a2.REJ_CONVERT, f"{type(exc).__name__}: {exc}")
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
                "size_bytes": Path(record["glb"]).stat().st_size,
            },
        )
        # Note: stage_source already wrote staging_manifest.json above, so if the
        # gate below rejects this candidate, that manifest file is left on disk
        # under `staging` for a candidate that never gets imported.
        gated = a2.gate_candidates([candidate], globals_cfg)
        if gated[0]["verdict"] != "viable":
            rec["status"] = "exhausted"
            rec["candidates"] = [_rejected(candidate, **gated[0]["rejection"])]
            return rec
        rec["attempts"] = 1
        fragment = Path(paths["fragment_dir"]) / f"{asset}_m{model}.yml"
        fragment.parent.mkdir(parents=True, exist_ok=True)
        runner(
            [
                paths["py_sap"],
                paths["scripts"] / "3_materialize" / "import_materialize.py",
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
            gate_name = _materialize_gate(out, asset, model)
            code = (
                f"validation_failed:{gate_name}"
                if gate_name
                else "validation_failed:materialize"
            )
            rec["status"] = "exhausted"
            rec["candidates"] = [
                _rejected(
                    candidate,
                    code,
                    f"{asset} m{model} not materialized; see import matrix under {out}",
                )
            ]
        return rec
    aliases_in = entry.get("aliases", [])
    added_terms = None
    if expand_fn is None:
        query = " ".join([category, *entry.get("colors", []), *aliases_in])
    else:
        expansion = expand_fn(category, aliases_in)
        added_terms = expansion["added"]
        query = " ".join([*expansion["terms"], *entry.get("colors", [])])
    rec = {
        "query": {"category": category, "aliases": entry.get("aliases", [category])},
        "entry_mode": "searched",
        "candidates": [],
        "attempts": 0,
    }
    if expand_fn is not None:
        rec["query"]["expanded"] = added_terms
    res = a1.tiered_search(
        tiers,
        query,
        viable_fn=lambda c: a2.gate(c, globals_cfg) is None,
        limit=int(globals_cfg.get("top_k", 5)),
    )
    rec["tiers_consulted"] = res["tiers_consulted"]
    rec["provider_errors"] = res["provider_errors"]
    rec["provider_stats"] = res.get("provider_stats", [])
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
    if screen_fn is not None:
        screened = screen_fn(category, [r["candidate"] for r in viable])
        rejected_n = 0
        for r in viable:
            verdict = screened.get(r["candidate"].candidate_id)
            if verdict is not None and not verdict.get("ok", True):
                r["verdict"] = "rejected"
                r["rejection"] = {
                    "code": a2.REJ_SEMANTIC,
                    "detail": verdict.get("reason", ""),
                }
                rejected_n += 1
        rec["semantic_screen"] = {"screened": len(viable), "rejected": rejected_n}
        viable = [r for r in viable if r["verdict"] == "viable"]

    if expand_fn is not None and added_terms:
        existing = entry.get("aliases", [])
        merged = existing + [t for t in added_terms if t not in existing]
        entry = {**entry, "aliases": merged}

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
    ap.add_argument(
        "--tier0-catalog",
        default=None,
        help="absolute path overriding robotwin_local's catalog after load, so "
        "tier-0 dedup and coverage checks read the same catalog",
    )
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
                if a.tier0_catalog:
                    t.provider.catalog_path = Path(a.tier0_catalog).resolve()
    else:
        globals_cfg = cfg.get("globals", {})
    llm_cfg = cfg.get("llm", {})
    llm_fn = a5.default_llm_fn(llm_cfg) if llm_cfg.get("enabled") else None
    alias_cache_path = Path(a.providers).resolve().parent / "query_aliases.json"
    expand_fn = _make_expand_fn(alias_cache_path, llm_cfg, llm_fn)
    screen_fn = _make_screen_fn(llm_cfg, llm_fn) if llm_cfg.get("enabled") else None
    paths = {
        "py_sap": PY_SAP,
        "py_isa": PY_ISA,
        "scripts": Path(__file__).resolve().parents[1],
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
            results.append(
                process_entry(
                    e,
                    tiers,
                    globals_cfg,
                    paths,
                    runner,
                    expand_fn=expand_fn,
                    screen_fn=screen_fn,
                )
            )
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
                paths["scripts"] / "5_catalog" / "s9_build_shadow_root.py",
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
