#!/usr/bin/env python3
"""One-shot: group data/asset_library/<asset>/ by source provider.

Why provider and not category/license/usable: provider is the only property of
an asset that never changes. Category gains aliases, license status goes
unknown -> declared, usable flips when a measurement campaign unlocks an asset
-- any of those in the path means moving directories later. Provider is fixed
at acquisition time and is also the unit of upstream re-sync and of the
licence family, so it is the one axis worth spending path depth on.

Provenance chain, strongest first:
  1. ledger models[*].source.library  -- the contract, authoritative
  2. acquired/external manifest group prefix -- for assets with no ledger yet.
     Rule validated against all 65 ledgered assets: `Assets/...` (or empty) is
     the NVIDIA Isaac server layout, `Models/<name>/glTF-Binary` is the Khronos
     glTF-Sample-Models tree (github). Objaverse prefixes are object titles and
     never need this path -- every objaverse asset has a ledger.
  3. nothing -> REFUSE. An unclassifiable asset stays flat and is reported;
     iter_assets() reads both layouts, so a leftover is visible, not broken.

Idempotent: assets already under a provider dir are left alone.
Usage: split_by_provider.py <library_dir> [--apply]
"""

import argparse
import collections
import json
import pathlib
import re
import sys

ASSET_NAME = re.compile(r"^\d+_")
PROVIDERS = ("nvidia", "objaverse", "github")


def provider_from_library(lib):
    if lib.startswith("NVIDIA"):
        return "nvidia"
    if "objaverse" in lib:
        return "objaverse"
    if "github" in lib:
        return "github"
    return None


def provider_from_prefix(prefix):
    if prefix.startswith("Assets/") or not prefix:
        return "nvidia"
    if prefix.startswith("Models/"):
        return "github"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("library")
    ap.add_argument("--configs", default=None, help="dir holding *_manifest.json")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    lib = pathlib.Path(a.library).resolve()
    cfg = (
        pathlib.Path(a.configs)
        if a.configs
        else lib.parents[1] / "1_asset_reuse/configs"
    )

    prefixes = {}
    for name in ("acquired_manifest.json", "external_manifest.json"):
        f = cfg / name
        if not f.is_file():
            continue
        for g in json.loads(f.read_text()).get("groups", []):
            for item in g.get("items", []):
                prefixes.setdefault(item.get("asset"), g.get("prefix", ""))

    plan, refused, already = [], [], []
    for entry in sorted(lib.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        if not ASSET_NAME.match(entry.name):
            already.append(entry.name)  # already a provider dir
            continue
        led = entry / "ledger.json"
        prov = evidence = None
        if led.is_file():
            libs = {
                (m.get("source") or {}).get("library", "")
                for m in json.loads(led.read_text()).get("models", [])
            }
            provs = {provider_from_library(x) for x in libs} - {None}
            if len(provs) > 1:
                refused.append((entry.name, "多来源: %s" % sorted(provs)))
                continue
            if provs:
                prov, evidence = provs.pop(), "ledger"
        if prov is None:
            prov = provider_from_prefix(prefixes.get(entry.name, ""))
            evidence = "manifest prefix=%r" % prefixes.get(entry.name, "")
        if prov is None:
            refused.append((entry.name, "无来源证据"))
            continue
        plan.append((entry, prov, evidence))

    counts = collections.Counter(p for _, p, _ in plan)
    print("=== 归类计划 (%d 个待搬, 已在子夹: %s)" % (len(plan), already or "无"))
    for prov in PROVIDERS:
        rows = [(e.name, ev) for e, p, ev in plan if p == prov]
        print("  %-10s %3d" % (prov + "/", len(rows)))
        for name, ev in rows:
            if not ev.startswith("ledger"):
                print("        %-22s <- %s" % (name, ev))
    if refused:
        print("=== 拒绝归类 (保持平铺):")
        for name, why in refused:
            print("  %-22s %s" % (name, why))

    if not a.apply:
        print("\n(dry-run; 加 --apply 执行)")
        return 0

    for prov in counts:
        (lib / prov).mkdir(exist_ok=True)
    for entry, prov, _ in plan:
        dest = lib / prov / entry.name
        if dest.exists():
            print("SKIP 已存在: %s" % dest)
            continue
        entry.rename(dest)  # same filesystem -> atomic rename, no data copy
    print("\n搬迁完成: %s" % dict(counts))
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
