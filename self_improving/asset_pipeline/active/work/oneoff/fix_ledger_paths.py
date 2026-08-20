#!/usr/bin/env python3
"""One-shot: repoint ledger uris at the provider-grouped library layout.

The path-hygiene contract (2026-08-16) made every ledger uri ACTIVE_ROOT-
relative, e.g. `data/asset_library/301_cup/visual/base0.glb`. Grouping the
library by provider inserts one segment, so those relative uris now dangle.

Rewrites `data/asset_library/<id>/` -> `data/asset_library/<provider>/<id>/`
everywhere in the JSON, keyed by where <id> ACTUALLY lives on disk now (not by
the ledger being rewritten), so cross-asset references land correctly too.
`_source/` is untouched: it did not move, and `_source` does not match the
`<digits>_` asset-id shape the pattern requires.

Idempotent: a uri that already carries a provider segment does not match.
Usage: fix_ledger_paths.py <library_dir> [--apply]
"""

import argparse
import json
import pathlib
import re
import sys

ASSET_NAME = re.compile(r"^\d+_")
URI = re.compile(r"(data/asset_library/)(\d+_[^/\"]+)/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("library")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    lib = pathlib.Path(a.library)

    # asset id -> provider, read off the filesystem as it stands now
    where = {}
    for prov in sorted(
        p for p in lib.iterdir() if p.is_dir() and not p.name.startswith("_")
    ):
        if ASSET_NAME.match(prov.name):
            continue  # still flat; nothing to prepend
        for asset in sorted(prov.iterdir()):
            if asset.is_dir() and ASSET_NAME.match(asset.name):
                where[asset.name] = prov.name
    print("已分层资产: %d" % len(where))

    unknown, changed, untouched = set(), [], 0

    def repl(m):
        prov = where.get(m.group(2))
        if prov is None:
            unknown.add(m.group(2))
            return m.group(0)
        return "%s%s/%s/" % (m.group(1), prov, m.group(2))

    targets = sorted(lib.glob("*/*/ledger.json")) + sorted(lib.glob("*/ledger.json"))
    for lp in targets:
        before = lp.read_text()
        after = URI.sub(repl, before)
        if after == before:
            untouched += 1
            continue
        n = len(URI.findall(before))
        changed.append((lp.parent.name, n))
        if a.apply:
            json.loads(after)  # refuse to write anything that is not valid JSON
            lp.write_text(after)

    print(
        "改写 %d 份账本，共 %d 处 uri；无需改动 %d 份"
        % (len(changed), sum(n for _, n in changed), untouched)
    )
    if unknown:
        print("⚠️ 无法定位的资产 id（uri 保持原样）: %s" % sorted(unknown))
    if not a.apply:
        print("(dry-run; 加 --apply 执行)")
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
