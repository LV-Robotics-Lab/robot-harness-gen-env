#!/usr/bin/env python3
"""C line step 1 (env-gen-yuxin env): build the RoboTwin shadow root (symlinks
to the real read-only RoboTwin + injected external assets from asset_library),
write extended overrides, and run the UPSTREAM catalog scanner over it.

Upstream repos and jingxiang's RoboTwin are never written to.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument(
    "--robotwin-root",
    default="/home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin",
)
parser.add_argument("--library-dir", required=True)
parser.add_argument("--shadow", required=True)
parser.add_argument("--ext-dir", required=True, help="data/scene_gen_ext output dir")
parser.add_argument(
    "--upstream", default="/home/jingxiang/yuxin/env-gen-dev/external/env-gen-github"
)
parser.add_argument(
    "--extra-overrides",
    default=None,
    help="yml fragment appended to upstream overrides (replaces built-in 301_cup block)",
)
args = parser.parse_args()

real = Path(args.robotwin_root)
lib = Path(args.library_dir)
shadow = Path(args.shadow)
ext = Path(args.ext_dir)
ext.mkdir(parents=True, exist_ok=True)

# ---- shadow root ----
if shadow.exists():
    shutil.rmtree(shadow)
shadow.mkdir(parents=True)
for item in real.iterdir():
    if item.name == "assets":
        continue
    (shadow / item.name).symlink_to(item)
(shadow / "assets").mkdir()
for item in (real / "assets").iterdir():
    if item.name == "objects":
        continue
    (shadow / "assets" / item.name).symlink_to(item)
objects = shadow / "assets" / "objects"
objects.mkdir()
n_real = 0
for item in (real / "assets" / "objects").iterdir():
    (objects / item.name).symlink_to(item)  # dirs AND plain files (same.json etc.)
    n_real += 1
n_ext = 0
for item in lib.iterdir():
    if item.is_dir() and not item.name.startswith("_"):
        (objects / item.name).symlink_to(item)
        n_ext += 1
print(f"shadow root: {n_real} robotwin + {n_ext} external asset dirs")

# ---- extended overrides (copy upstream + append ours) ----
upstream_overrides = Path(args.upstream) / "scene_gen" / "asset_overrides.yml"
ext_overrides = ext / "asset_overrides_ext.yml"
text = upstream_overrides.read_text()
if args.extra_overrides:
    append = (
        "\n  # --- external assets injected by env-gen-dev asset_reuse ---\n"
        + Path(args.extra_overrides).read_text()
    )
else:
    append = """
  # --- external assets injected by env-gen-dev asset_reuse (301+) ---
  301_cup:
    category: cup
    aliases: [cup, mug]
    colors: [red]
    materials: [ceramic]
    models:
      "0":
        stable_pose_id: upright
        stable_orientation_wxyz: [0.7071067811865476, 0.7071067811865476, 0.0, 0.0]
        z_policy: origin_on_table
        footprint_shape: circle
"""
ext_overrides.write_text(text + append)
print(f"extended overrides -> {ext_overrides}")

# ---- run upstream catalog scanner over the shadow ----
src_commit = (
    subprocess.run(
        ["git", "-C", str(real), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    or "unknown"
)
cat_out = ext / "asset_catalog.json"
cmd = [
    sys.executable,
    "-m",
    "scene_gen.catalog",
    "--robotwin-root",
    str(shadow),
    "--overrides",
    str(ext_overrides),
    "--source-commit",
    f"{src_commit}+ext301",
    "--out",
    str(cat_out),
    "--missing-out",
    str(ext / "missing_assets.json"),
]
res = subprocess.run(cmd, capture_output=True, text=True, cwd=args.upstream)
print(res.stdout.strip())
if res.returncode != 0:
    print(res.stderr[-800:])
    print("FAIL s9: catalog scan failed")
    sys.exit(1)

# ---- verify injected entries ----
cat = json.loads(cat_out.read_text())
if args.extra_overrides:
    wanted = [
        line.strip().rstrip(":")
        for line in Path(args.extra_overrides).read_text().splitlines()
        if line.startswith("  ")
        and not line.startswith("    ")
        and line.strip().endswith(":")
    ]
else:
    wanted = ["301_cup"]
ok = True
for asset_id in wanted:
    entry = next((e for e in cat["entries"] if e["asset_id"] == asset_id), None)
    usable = [m for m in entry["models"] if m.get("usable")] if entry else []
    if entry is None:
        print(f"{asset_id}: MISSING")
        ok = False
    else:
        print(f"{asset_id}: category={entry['category']} usable={len(usable)}")
        if not usable:
            ok = False
print("PASS s9" if ok else "FAIL s9")
sys.exit(0 if ok else 1)
