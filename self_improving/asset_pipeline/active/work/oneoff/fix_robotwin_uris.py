#!/usr/bin/env python3
"""One-shot: repoint ledger uris at the RoboTwin natives' new home in the
library.

  data/robotwin_assets/objects/<id>/<tail>   -> data/asset_library/robotwin/<id>/<tail>
  data/robotwin_assets/usd/<id>/<file>       -> data/asset_library/robotwin/<id>/usd/<file>

Every rewritten uri is checked to actually resolve afterwards; a rewrite that
would dangle is reported and NOT written (the whole file is left alone), so a
mapping mistake cannot quietly turn 33 good pointers into 33 dead ones.

Usage: fix_robotwin_uris.py <active_root> [--apply]
"""

import argparse
import json
import pathlib
import re
import sys

OBJ = re.compile(r"data/robotwin_assets/objects/")
USD = re.compile(r"data/robotwin_assets/usd/([^/\"]+)/")


def rewrite(text):
    text = USD.sub(r"data/asset_library/robotwin/\1/usd/", text)
    return OBJ.sub("data/asset_library/robotwin/", text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("active")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    active = pathlib.Path(a.active).resolve()

    total_changed = total_uris = 0
    dangling = []
    for lp in sorted((active / "data").glob("*/*/ledger.json")) + sorted(
        (active / "data").glob("*/*/*/ledger.json")
    ):
        before = lp.read_text()
        if "data/robotwin_assets/" not in before:
            continue
        after = rewrite(before)
        led = json.loads(after)
        bad = []
        for model in led.get("models", []):
            for rep in model.get("representations", []):
                uri = rep.get("uri") or ""
                if not uri.startswith("data/asset_library/robotwin/"):
                    continue
                if not (active / uri).exists():
                    bad.append(uri)
        n = before.count("data/robotwin_assets/")
        total_uris += n
        if bad:
            dangling.append((lp.parent.name, bad))
            continue
        total_changed += 1
        print("  %-42s %d 处" % (lp.parent.name, n))
        if a.apply:
            lp.write_text(after)

    print("\n可改写 %d 份账本 / %d 处 uri" % (total_changed, total_uris))
    if dangling:
        print("⚠️ 改写后会悬空，已跳过（整份不动）:")
        for asset, uris in dangling:
            print("  %s: %s" % (asset, uris[:3]))
    if not a.apply:
        print("(dry-run; 加 --apply 执行)")
    return 1 if dangling else 0


if __name__ == "__main__":
    sys.exit(main())
