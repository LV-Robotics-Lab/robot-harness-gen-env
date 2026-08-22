#!/usr/bin/env python3
"""One-shot: re-home ledger uris that point at vanished run-scoped caches.

A retrieval run downloads candidates into a run-scoped cache (/tmp/<run>/
webcache/...) and the pool's provenance contract then mirrors the chosen file
under `_source/<group>/`. When a representation's uri was recorded as the
cache path rather than the mirror, the entry dangles the moment /tmp is
cleared -- ledger_audit reports `file_missing` on an asset that is actually
intact. (Path hygiene 2026-08-16 deliberately left /tmp strings alone: they
carry no username and are honest pointers to an ephemeral run. Honest, but
dead.)

Re-homes such a uri to the mirrored copy ONLY when the mirror's sha256 equals
the sha256 the ledger already recorded -- same bytes, new address. A basename
match with a different digest is a different file and is reported, never
rewritten.

Usage: rehome_uris.py <library_dir> [--apply]
"""

import argparse
import hashlib
import json
import pathlib
import sys

ACTIVE = pathlib.Path(__file__).resolve().parents[2]


def resolve(uri):
    p = pathlib.Path(uri)
    return p if p.is_absolute() else ACTIVE / p


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("library")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    lib = pathlib.Path(a.library).resolve()
    source_root = lib / "_source"

    fixed, unfixable = [], []
    for lp in sorted(lib.glob("*/*/ledger.json")) + sorted(lib.glob("*/ledger.json")):
        led = json.loads(lp.read_text())
        dirty = False
        for model in led.get("models", []):
            for rep in model.get("representations", []):
                uri, want = rep.get("uri"), rep.get("sha256")
                if not uri or resolve(uri).exists():
                    continue
                name = pathlib.Path(uri).name
                match = next(
                    (
                        c
                        for c in source_root.rglob(name)
                        if c.is_file() and want and sha256(c) == want
                    ),
                    None,
                )
                if match is None:
                    unfixable.append((lp.parent.name, uri))
                    continue
                rel = match.relative_to(ACTIVE).as_posix()
                fixed.append((lp.parent.name, uri, rel))
                rep["uri"] = rel
                dirty = True
        if dirty and a.apply:
            lp.write_text(json.dumps(led, indent=2, ensure_ascii=False) + "\n")

    print("可修复 %d 条:" % len(fixed))
    for asset, old, new in fixed:
        print("  %s\n    旧 %s\n    新 %s" % (asset, old, new))
    if unfixable:
        print("无法修复 %d 条（镜像里没有同名同哈希的文件）:" % len(unfixable))
        for asset, uri in unfixable:
            print("  %s  %s" % (asset, uri))
    if not a.apply:
        print("\n(dry-run; 加 --apply 执行)")
    return 1 if unfixable else 0


if __name__ == "__main__":
    sys.exit(main())
