#!/usr/bin/env python3
"""Asset/model retirement (H2 hardening 2): the "手工删库文件不安全" gap the
README has flagged since T5's review -- ledger.json / results/**/bundles/
snapshots / _source/ mirrors cross-reference each other by sha256 /
source_manifest_path, so a bare `rm` on a library file leaves the ledger
entry that still points at it dangling (silently, until the next
ledger_audit.py sweep catches the resulting file_missing).

Dry-run by default: prints the files + ledger change that would happen and
exits 0 without touching disk. --apply executes it.

Model-level (--model N): removes that model's own files (visual/collision
mesh, model_data<N>.json, snapshots/m<N>_*.png, and the articulated
--instance-dir subdir <N>/ if s13b's own layout is in use) and prunes the
models[] entry via lib.ledger.write_ledger (atomic + locked). If pruning
empties models[], the whole asset is retired instead -- an empty ledger
shell left behind would still get symlinked into the shadow root by
s9_build_shadow_root.py, same "no ledger.json and no model_data*.json left"
reasoning import_materialize's own empty-shell cleanup uses.

Asset-level (no --model): retires every model + the ledger + the asset dir
in one shot.

Safety valve: an asset dir with no ledger.json is out of scope for this
tool -- it only manages v1-ledger assets. Handle it manually (see README).

Containment guard (review round 1, I-1/I-2): --asset is meant to be a bare
directory name under --library-dir, not a path. pathlib's `/` silently
discards the left operand when the right one is absolute (so
`--asset /etc` or `--asset ../../etc` would otherwise resolve OUTSIDE
--library-dir instead of erroring), and if --library-dir is accidentally
pointed at a symlink-based tree (e.g. data/robotwin_shadow/, whose
per-asset entries are symlinks into the real pool), operating on that
entry follows the symlink and mutates the real pool it points at. Both are
rejected with exit code 2 (distinct from the exit-1 "known, ordinary"
failures below -- no ledger.json / unknown --model) before anything on
disk is touched.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger


def _containment_error(lib, asset):
    """None if `lib/asset` is safe to operate on; an error message
    otherwise. Two checks, in order:
    (1) `lib/asset` is not itself a symlink -- refuses to follow one into a
        tree outside the caller's intended library_dir (e.g. a shadow root
        symlinked back into the real pool).
    (2) once resolved, its parent is exactly the resolved library_dir --
        catches both `--asset ../escape` (resolves to a different parent)
        and `--asset /abs/path` (pathlib's `/` drops the left operand
        entirely for an absolute right operand, so this is the only thing
        that catches it) and any other multi-component --asset value
        (a bare asset name never contains a path separator).
    Deliberately checked on the *unresolved* `lib/asset` for (1) --
    resolving first would follow the symlink away and make it
    unobservable."""
    raw = lib / asset
    if raw.is_symlink():
        return (
            f"{asset!r} resolves to a symlink under {lib} -- refusing "
            "(possible shadow-root escape)"
        )
    resolved = raw.resolve()
    if resolved.parent != lib.resolve():
        return f"{asset!r} escapes --library-dir {lib} (resolves to {resolved})"
    return None


def _model_files(lib, asset, model_id):
    """Every file/dir one model's retirement should remove: the rigid-layout
    triple (visual/collision mesh + model_data<N>.json, same naming
    convention as import_materialize's own quarantine loop), its snapshot(s),
    and the articulated --instance-dir subdir <N>/ (s13b layout) when
    present. Only paths that actually exist are returned."""
    asset_dir = lib / asset
    paths = [
        p
        for p in (
            asset_dir / "visual" / f"base{model_id}.glb",
            asset_dir / "collision" / f"base{model_id}.glb",
            asset_dir / f"model_data{model_id}.json",
        )
        if p.exists()
    ]
    paths += sorted((asset_dir / "snapshots").glob(f"m{model_id}_*.png"))
    inst_dir = asset_dir / str(model_id)
    if inst_dir.is_dir():
        paths.append(inst_dir)
    return paths


def plan(library_dir, asset, model_id=None):
    """Compute what would be deleted, without touching disk. Returns a dict;
    on failure {"error": <message>, "exit_code": 1 or 2}, on success
    {"error": None, "asset_dir", "whole_asset" (bool), "files" (list[Path]),
    "remaining_models" (list[dict]), "led" (dict, only for the model-level
    non-whole-asset case -- the caller needs the parsed ledger to prune and
    rewrite it)}."""
    lib = Path(library_dir)

    containment_error = _containment_error(lib, asset)
    if containment_error:
        return {"error": containment_error, "exit_code": 2}

    asset_dir = lib / asset
    lp = asset_dir / "ledger.json"
    if not lp.exists():
        return {
            "error": (
                f"{asset!r} has no ledger.json -- this tool only manages "
                "v1-ledger assets; retire it manually"
            ),
            "exit_code": 1,
        }

    led = json.loads(lp.read_text())
    models = led.get("models", [])

    if model_id is None:
        files = []
        for m in models:
            files += _model_files(lib, asset, m.get("model_id"))
        return {
            "error": None,
            "asset_dir": asset_dir,
            "whole_asset": True,
            "files": files,
            "remaining_models": [],
        }

    if not any(m.get("model_id") == model_id for m in models):
        return {
            "error": f"model_id {model_id} not found in {asset!r}'s ledger",
            "exit_code": 1,
        }

    remaining = [m for m in models if m.get("model_id") != model_id]
    return {
        "error": None,
        "asset_dir": asset_dir,
        "whole_asset": len(remaining) == 0,
        "files": _model_files(lib, asset, model_id),
        "remaining_models": remaining,
        "led": led,
    }


def execute(asset_dir, p):
    if p["whole_asset"]:
        if asset_dir.exists():
            # M-2 (review round 1): informational only, not a second gate --
            # the containment check above is what decides whether this
            # rmtree is safe to run at all. This just tells the operator the
            # real on-disk count (which can exceed len(p["files"]): that
            # list is derived from the ledger's own models[], so it misses
            # untracked leftovers -- e.g. a stray file from an interrupted
            # earlier run -- that this rmtree will also remove).
            actual = [f for f in asset_dir.rglob("*") if f.is_file()]
            print(
                f"deleting {len(actual)} file(s) under {asset_dir} "
                "(includes any untracked leftovers not listed above)"
            )
            shutil.rmtree(asset_dir)
        return

    # Files first, ledger second: a crash between the two leaves a dangling
    # (file_missing) ledger entry, not a silent orphan file with no ledger
    # trace at all -- the former is what ledger_audit.py's next sweep is
    # designed to catch, the latter isn't.
    for f in p["files"]:
        if f.is_dir():
            shutil.rmtree(f)
        else:
            f.unlink()
    led = p["led"]
    led["models"] = p["remaining_models"]
    ledger.write_ledger(asset_dir / "ledger.json", led)


def _print_plan(asset, model_id, p):
    if model_id is None:
        print(f"will retire asset {asset!r} ({len(p['files'])} file(s), all models):")
    elif p["whole_asset"]:
        print(
            f"will retire asset {asset!r} model {model_id} "
            "(last remaining model -- whole asset retired):"
        )
    else:
        print(f"will retire asset {asset!r} model {model_id}:")
    for f in p["files"]:
        print(f"  {f}")
    if p["whole_asset"]:
        print(f"  {p['asset_dir']} (ledger.json + .lock + entire asset directory)")
    else:
        print(
            f"  ledger.json: prune model_id={model_id} "
            f"({len(p['remaining_models'])} model(s) remain)"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--model", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    p = plan(args.library_dir, args.asset, args.model)
    if p["error"]:
        print(f"ERROR: {p['error']}", file=sys.stderr)
        sys.exit(p["exit_code"])

    _print_plan(args.asset, args.model, p)

    if not args.apply:
        print("(dry-run -- pass --apply to execute)")
        sys.exit(0)

    execute(p["asset_dir"], p)
    print("done")
    sys.exit(0)


if __name__ == "__main__":
    main()
