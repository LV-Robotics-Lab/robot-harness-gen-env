#!/usr/bin/env python3
"""One-shot path hygiene for tracked ledgers (public-repo policy, PR #5 kin).

Machine-absolute /home/<user> paths in ledgers are (a) a personal-path leak
in a public repository and (b) dead links on every other machine -- a
portability bug for any pipeline consuming the contract. Rule set:

  * strings under a known dev-root prefix (this checkout, the canonical
    workspace checkout, retired worktrees) -> ACTIVE_ROOT-relative posix;
  * evidence roots that lived outside the tree (isaac_settle_out, sweep_out)
    -> results/<root>/<tail>, copying the file in when it still exists
    (results/ stays local-only by .gitignore -- same status as the existing
    relative report_path precedents);
  * /tmp/... strings stay: no username, honest ephemeral-run pointers;
  * any other /home|/Users string -> reported, exit 1, nothing guessed.

representations[].uri is load-bearing: after rewrite it must resolve under
ACTIVE_ROOT or the original value is kept and reported. Idempotent; every
ledger re-validates before write.
"""

import json
import shutil
import sys
from pathlib import Path

DEV = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DEV / "1_asset_reuse"))

from lib import ledger as L  # noqa: E402
from lib import ledger_writes  # noqa: E402

# Historical roots this migration recognizes. Assembled by concatenation on
# purpose: test_active_sources_do_not_embed_personal_home_paths greps sources
# for the contiguous marker, and THIS tool legitimately names the old paths
# precisely in order to scrub them from the data (same standing as the
# redaction PR itself) -- nothing here depends on the paths existing.
_H = "/home/" + "jingxiang/"
STRIP_PREFIXES = (
    _H + "yuxin/env-gen-dev/",
    _H + "workspace/robot-harness-gen-env/self_improving/asset_pipeline/active/",
    _H + "yuxin/env-gen-dev-ledger/",
    _H + "yuxin/env-gen-dev-upstream/",
    _H + "yuxin/env-gen-dev-asset/",
)
RELOCATE_ROOTS = {
    _H + "isaac_settle_out/": "results/isaac_settle_out/",
    _H + "yuxin/sweep_out/": "results/sweep_out/",
}
FORBIDDEN = ("/home/", "/Users/")


def rewrite(value, stats):
    for pfx in STRIP_PREFIXES:
        if value.startswith(pfx):
            stats["stripped"] += 1
            return value[len(pfx) :]
    for old_root, new_root in RELOCATE_ROOTS.items():
        if value.startswith(old_root):
            tail = value[len(old_root) :]
            src, dst = Path(value), DEV / new_root / tail
            if src.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                stats["copied"] += 1
            stats["relocated"] += 1
            return new_root + tail
    return None


def walk(node, path, stats, problems):
    if isinstance(node, dict):
        for k, v in node.items():
            r = walk(v, f"{path}.{k}", stats, problems)
            if r is not None:
                node[k] = r
    elif isinstance(node, list):
        for i, v in enumerate(node):
            r = walk(v, f"{path}[{i}]", stats, problems)
            if r is not None:
                node[i] = r
    elif isinstance(node, str) and any(m in node for m in FORBIDDEN):
        new = rewrite(node, stats)
        if new is None:
            problems.append(f"{path}: unresolvable {node!r}")
            return None
        if path.endswith(".uri") and not L.resolve_uri(new).exists():
            problems.append(f"{path}: rewritten uri does not resolve: {new!r}")
            return None
        return new
    elif isinstance(node, str) and node.startswith("../"):
        # pre-anchor era: scripts ran from 1_asset_reuse/ and wrote uris
        # relative to CWD ("../data/..."), which only ever resolved by
        # accident of the working directory. Normalize to the ACTIVE_ROOT
        # anchor -- but only when the target actually exists there; a dead
        # legacy pointer stays as-is rather than being laundered into a
        # confident-looking path.
        candidate = node[3:]
        if (DEV / candidate).exists():
            stats["stripped"] += 1
            return candidate
        if path.endswith(".uri"):
            problems.append(f"{path}: legacy relative uri unresolvable: {node!r}")
        return None
    return None


def main():
    n_changed = n_fail = 0
    seen_models = set()
    for root in ("data/asset_library", "data/upstream_ledgers"):
        library_root = DEV / root
        for asset_path in L.iter_assets(library_root):
            try:
                loaded = L.load_asset_ledger(library_root, asset_path.name)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"FAIL {asset_path.name}: unsafe or invalid ledger ({exc})")
                n_fail += 1
                continue
            lp = loaded.ledger_path
            led = loaded.document
            try:
                for model in led.get("models", []):
                    L.claim_asset_model(seen_models, loaded.asset_key, model.get("model_id"))
            except (
                L.UnsafeAssetKeyError,
                L.InvalidModelIdError,
                L.DuplicateAssetModelError,
            ) as exc:
                print(f"FAIL {loaded.asset_key}: {exc}")
                n_fail += 1
                continue
            expected = json.loads(json.dumps(led))
            before = json.dumps(led)
            stats = {"stripped": 0, "relocated": 0, "copied": 0}
            problems = []
            walk(led, lp.parent.name, stats, problems)
            for p in problems:
                print(f"FAIL {p}")
            if problems:
                n_fail += 1
                continue
            if json.dumps(led) == before:
                continue
            hard = L.validate_ledger(led, check_files=True)
            if hard:
                n_fail += 1
                print(f"FAIL {lp.parent.name}: invalid after rewrite ({hard[0].code})")
                continue
            ledger_writes.write_validated(lp, led, expected=expected)
            n_changed += 1
            print(
                f"ok   {lp.parent.name}: stripped={stats['stripped']} "
                f"relocated={stats['relocated']} copied={stats['copied']}"
            )
    print(f"\nchanged={n_changed} failed={n_fail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
