#!/usr/bin/env python3
"""One-shot: move the vendored RoboTwin assets into the library as a fourth
source provider.

Owner decision 2026-08-18: every asset the project holds lives under
data/asset_library/<provider>/. The RoboTwin natives were vendored into
data/robotwin_assets/ by the 2026-08-09 localization decision and then went
unused when the machine migration repointed ROBOTWIN_ROOT at the
external/RoboTwin submodule; this move ends that split.

Verified before moving: `rsync -rcn` between data/robotwin_assets/objects and
external/RoboTwin/assets/objects reports 0 differing entries (excluding the
900_* proxies, which exist only in the vendored copy). Same bytes, so the
pipeline reading them from their new home cannot silently pick up stale meshes.

Layout mapping:
  robotwin_assets/objects/<id>/       -> asset_library/robotwin/<id>/
  robotwin_assets/usd/<id>/<file>     -> asset_library/robotwin/<id>/usd/<file>
  robotwin_assets/LICENSE.upstream    -> asset_library/robotwin/LICENSE.upstream

Same filesystem, so each move is an atomic rename -- no 4.4 GB copy, and an
interruption leaves whole directories on one side or the other, never halves.
Idempotent: anything already at the destination is skipped.

Usage: move_robotwin_in.py <library_dir> <robotwin_assets_dir> [--apply]
"""

import argparse
import pathlib
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("library")
    ap.add_argument("vendored")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    lib = pathlib.Path(a.library).resolve()
    src = pathlib.Path(a.vendored).resolve()
    dest_root = lib / "robotwin"

    if not (src / "objects").is_dir():
        print("FAIL: %s/objects 不存在" % src, file=sys.stderr)
        return 2

    objects = sorted(p for p in (src / "objects").iterdir() if p.is_dir())
    usds = (
        sorted(p for p in (src / "usd").iterdir() if p.is_dir())
        if (src / "usd").is_dir()
        else []
    )
    license_file = src / "LICENSE.upstream"

    print("待搬:")
    print("  资产目录 %d 个 -> %s/<id>/" % (len(objects), dest_root))
    print(
        "    其中 900_* 代理 %d 个（照搬；它们有账本，只是被 s9 排除出影子视图）"
        % sum(1 for p in objects if p.name.startswith("900_"))
    )
    print("  usd 转换 %d 个 -> %s/<id>/usd/" % (len(usds), dest_root))
    for p in usds:
        print("    %s -> %s/%s/usd/" % (p.name, dest_root.name, p.name))
        if (
            not (dest_root / p.name).exists()
            and not (src / "objects" / p.name).is_dir()
        ):
            print("    ⚠️ %s 没有对应的资产目录，usd 会无处安放" % p.name)
    print("  LICENSE.upstream: %s" % ("有" if license_file.is_file() else "无"))

    if not a.apply:
        print("\n(dry-run; 加 --apply 执行)")
        return 0

    dest_root.mkdir(parents=True, exist_ok=True)
    moved = skipped = 0
    for p in objects:
        dest = dest_root / p.name
        if dest.exists():
            skipped += 1
            continue
        p.rename(dest)
        moved += 1
    for p in usds:
        dest = dest_root / p.name / "usd"
        if dest.exists():
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        p.rename(dest)
        moved += 1
    if license_file.is_file() and not (dest_root / license_file.name).exists():
        license_file.rename(dest_root / license_file.name)
        moved += 1

    leftovers = [p for p in src.rglob("*") if p.is_file()]
    print("\n搬迁完成: %d 项移动, %d 项已存在跳过" % (moved, skipped))
    if leftovers:
        print("⚠️ 源目录仍有 %d 个文件，未删除源目录:" % len(leftovers))
        for p in leftovers[:8]:
            print("   ", p)
        return 1
    for d in sorted((p for p in src.rglob("*") if p.is_dir()), reverse=True):
        d.rmdir()
    src.rmdir()
    print("源目录 %s 已空并删除" % src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
