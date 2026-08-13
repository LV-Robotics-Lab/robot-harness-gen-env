#!/usr/bin/env python3
"""C line step 1 (env-gen-yuxin env): build the RoboTwin shadow root (symlinks
to the real read-only RoboTwin + injected external assets from asset_library),
write extended overrides, and run the UPSTREAM catalog scanner over it.

Upstream repos and the RoboTwin checkout are never written to. The default
root is OUR OWN copy under ~/yuxin/robotwin_upstream: the previous default
pointed into a colleague's workspace, which was deleted on 2026-08-13 and
took every shadow symlink down with it mid-sweep.
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
    default="/home/jingxiang/yuxin/robotwin_upstream",
)
parser.add_argument("--library-dir", required=True)
parser.add_argument("--shadow", required=True)
parser.add_argument("--ext-dir", required=True, help="data/scene_gen_ext output dir")
parser.add_argument(
    "--upstream", default="/home/jingxiang/yuxin/env-gen-dev/external/env-gen-github"
)
parser.add_argument(
    "--admission",
    choices=["report", "enforce"],
    default=None,
    help="run s14 catalog-view admission after build; enforce filters not_admitted externals from THIS view",
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
n_skipped = 0
for item in (real / "assets" / "objects").iterdir():
    if item.name.startswith("900_"):
        # residue of earlier generated/derived proxies (900_gen_*, 900_scaled_*):
        # not canonical assets; keeping them out enforces reuse-before-generation
        n_skipped += 1
        continue
    (objects / item.name).symlink_to(item)  # dirs AND plain files (same.json etc.)
    n_real += 1
n_ext = 0
for item in lib.iterdir():
    if item.is_dir() and not item.name.startswith("_"):
        (objects / item.name).symlink_to(item)
        n_ext += 1
print(
    f"shadow root: {n_real} robotwin + {n_ext} external asset dirs (skipped {n_skipped} 900_* proxy residues)"
)

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

# ---- native origin calibration patches ----
# Measured 2026-08-12: the natives' mesh origins are NOT one convention
# (110_basket loads origin-at-bottom, 035_apple origin-at-center) while the
# upstream overrides declare origin_on_table across the board -- a
# center-origin apple placed origin-on-plane spawns half-sunk, and INSIDE a
# basket the resolver impulse ejects it. Also ~110 natives have no override
# entry at all, leaving them unusable ("dumbbell-rack blocker" while
# 013_dumbbell-rack sits in the library). The calibration file is produced by
# work/oneoff/calibrate_native_origins.py (SAPIEN drop-settle + reverify per
# model); here we correct contradicted z_policy on declared entries and add
# measured entries for natives that had none. Upstream files stay untouched.
calib_path = ext / "native_origin_calibration.json"
if calib_path.exists():
    import yaml

    calib = json.loads(calib_path.read_text())
    data = yaml.safe_load(ext_overrides.read_text()) or {}
    root = data.setdefault("assets", {})  # the scanner reads overrides["assets"] only
    n_patch = n_add = 0
    n_replace = n_revoke = n_skip = 0
    for aid, models in calib.get("models", {}).items():
        for mid, row in models.items():
            asset = root.setdefault(aid, {})
            entry = asset.setdefault("models", {}).setdefault(str(mid), {})
            declared_bad = (
                row.get("had_override") and row.get("declared_trusted") is False
            )
            if row.get("verdict") != "ok":
                # Declarations get no free pass either: if placing the model
                # exactly as declared failed its own reverify AND no reliable
                # measured pose exists, the declaration is revoked -- the
                # model goes unusable and acquisition finds a replacement.
                # (020_hammer's upstream lie_flat left its origin hovering
                # 3.5 cm; a bare hammer scene toppled at runtime while the
                # declaration was trusted -- prompt-matrix 2026-08-13.)
                if declared_bad and "stable_pose_id" in entry:
                    del entry["stable_pose_id"]
                    entry["placement_revoked"] = (
                        "declared pose failed reverify; no measured pose"
                    )
                    n_revoke += 1
                continue
            dims = row.get("dims_m") or []
            infeasible = dims and (max(dims[0], dims[1]) > 0.42 or dims[2] > 0.55)
            if infeasible and "stable_pose_id" not in entry:
                # Admission must align with the solver's placement envelope
                # (workspace 0.70 x 0.50 m + margins + robot keepout): the
                # 0.65 x 0.74 m dustbin passed the 0.8 m bbox guard yet can
                # NEVER be placed -- solver refused x96 and the user saw a
                # bare error (2026-08-13). Better an honest gap that
                # acquisition can fill with a tabletop-sized one.
                entry["placement_infeasible"] = (
                    f"footprint {dims[0]:.2f}x{dims[1]:.2f} h {dims[2]:.2f}"
                    " exceeds tabletop envelope"
                )
                n_skip += 1
                continue
            if "stable_pose_id" not in entry:
                entry.update(
                    stable_pose_id="measured_rest",
                    stable_orientation_wxyz=row["rest_orientation_wxyz"],
                    z_policy=row["z_policy"],
                    footprint_shape="box",
                    dimensions_m=row["dims_m"],
                )
                n_add += 1
            elif declared_bad:
                entry.update(
                    stable_pose_id="measured_rest",
                    stable_orientation_wxyz=row["rest_orientation_wxyz"],
                    z_policy=row["z_policy"],
                    dimensions_m=row["dims_m"],
                )
                n_replace += 1
            elif entry.get("z_policy", "origin_on_table") != row["z_policy"]:
                entry["z_policy"] = row["z_policy"]
                n_patch += 1
    header = (
        "# GENERATED by s9_build_shadow_root: upstream asset_overrides.yml\n"
        "# + env-gen-dev extensions + native origin calibration patches\n"
        "# (native_origin_calibration.json). Do not edit by hand.\n"
    )
    ext_overrides.write_text(
        header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    )
    print(
        f"calibration patch: {n_patch} z_policy corrected, {n_add} added, "
        f"{n_replace} untrusted declarations replaced, {n_revoke} revoked, "
        f"{n_skip} tabletop-infeasible skipped"
    )

# ---- top-support survey patches (HOLLOW verdicts only) ----
# Measured 2026-08-13: the solver treats any bbox top as a flat platform, so
# a duck was placed on a cup's rim and toppled at runtime. probe_top_support
# drops a 3x3 probe grid on every usable model; models whose probes SINK are
# containers: they gain measured interior data (enabling INSIDE) and a
# 2x2 mm support surface no real footprint can pass, so "duck on cup" is
# refused honestly at solve time. "unsupportive" verdicts (probes slide off)
# stay ADVISORY-ONLY: the 2 cm probe under-approximates what wider-based
# objects can rest on -- a mug bridged a duck's back in a run the Studio
# recorded as stable -- so closing those tops would regress working scenes;
# genuinely unstable stacks remain the runtime check's honest verdict.
# Hand-declared support/interior data always wins.
survey_path = ext / "top_support_survey.json"
if survey_path.exists():
    import yaml

    survey = json.loads(survey_path.read_text())
    data = yaml.safe_load(ext_overrides.read_text()) or {}
    root = data.setdefault("assets", {})
    n_int = n_closed = 0
    for aid, models in survey.get("models", {}).items():
        for mid, row in models.items():
            if row.get("verdict") != "hollow":
                continue
            entry = (
                root.setdefault(aid, {})
                .setdefault("models", {})
                .setdefault(str(mid), {})
            )
            if "stable_pose_id" not in entry:
                continue  # only annotate otherwise-declared models
            dims = entry.get("dimensions_m")
            top_z = (
                dims[2]
                if dims
                else max((c["bottom_z"] for c in row.get("cells", [])), default=None)
            )
            if (
                "interior_dimensions_m" not in entry
                and row["interior"]["dimensions_m"][2] >= 0.03
            ):
                # a cavity shallower than 3 cm holds nothing INSIDE-worthy
                # (a plate's 2 cm "cavity" made cup-in-plate solvable on
                # paper and refused x4608 in practice, 2026-08-13)
                entry["interior_dimensions_m"] = row["interior"]["dimensions_m"]
                entry["interior_floor_z_offset_m"] = row["interior"]["floor_z_offset_m"]
                n_int += 1
            if "support_surface_dimensions_m" not in entry and top_z:
                entry["support_surface_dimensions_m"] = [0.002, 0.002]
                entry["support_surface_shape"] = "box"
                entry["support_surface_z_offset_m"] = round(float(top_z), 4)
                n_closed += 1
    ext_overrides.write_text(
        "# GENERATED (see calibration header above)\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    )
    print(
        f"top-support patch: {n_int} interiors measured, {n_closed} hollow tops closed"
    )
# ---- final envelope + consistency pass over ALL declared entries ----
# The per-branch feasibility check missed the replace path: 034_knife's
# untrusted declaration was REPLACED with its measured 0.46 m lie-flat pose,
# which cannot share a 0.70x0.50 m workspace with anything (left_of refused
# x2496, 2026-08-13). One final sweep over every entry closes every path in;
# it also repairs upstream declarations that carry interior dims with a null
# floor offset (003_plate), which the solver consumes as numbers.
import yaml as _yaml

_data = _yaml.safe_load(ext_overrides.read_text()) or {}
_root = _data.get("assets") or {}
# override entries do not always carry dimensions (fragment entries leave
# them to bundle metadata: 314_cabinet slipped through exactly this hole);
# fall back to the PREVIOUS catalog's resolved dims for those.
_prev_dims = {}
_prev_cat = ext / "asset_catalog.json"
if _prev_cat.exists():
    _pc = json.loads(_prev_cat.read_text())
    for _e in _pc["entries"] if isinstance(_pc, dict) else _pc:
        for _m in _e.get("models", []):
            if _m.get("dimensions_m"):
                _prev_dims[(_e["asset_id"], str(_m["model_id"]))] = _m["dimensions_m"]
_n_env = _n_floor = 0
for _aid, _asset in _root.items():
    for _mid, _entry in (_asset.get("models") or {}).items():
        if not isinstance(_entry, dict):
            continue
        _dims = _entry.get("dimensions_m") or _prev_dims.get((_aid, str(_mid)))
        if (
            _entry.get("stable_pose_id")
            and _dims
            and (max(_dims[0], _dims[1]) > 0.42 or _dims[2] > 0.55)
        ):
            del _entry["stable_pose_id"]
            _entry["placement_infeasible"] = (
                f"footprint {_dims[0]:.2f}x{_dims[1]:.2f} h {_dims[2]:.2f}"
                " exceeds tabletop envelope"
            )
            _n_env += 1
        if (
            _entry.get("interior_dimensions_m")
            and _entry.get("interior_floor_z_offset_m") is None
        ):
            _entry["interior_floor_z_offset_m"] = 0.005
            _n_floor += 1
if _n_env or _n_floor:
    ext_overrides.write_text(
        "# GENERATED (see calibration header above)\n"
        + _yaml.safe_dump(_data, sort_keys=False, allow_unicode=True)
    )
print(f"envelope pass: {_n_env} entries revoked, {_n_floor} null floors repaired")
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
            # an entry the envelope pass deliberately revoked is EXPECTED to
            # be unusable -- that is the pass doing its job, not a failure
            revoked = any(
                m.get("placement_infeasible")
                for m in ((_root.get(asset_id) or {}).get("models") or {}).values()
                if isinstance(m, dict)
            )
            if revoked:
                print(f"{asset_id}: revoked by envelope pass (expected)")
            else:
                ok = False
if ok and args.admission:
    import subprocess as sp

    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "s14_catalog_admission.py"),
        "--catalog",
        str(cat_out),
        "--upstream",
        args.upstream,
        "--work-dir",
        str(ext / "_admission_work"),
        "--report",
        str(ext / "catalog_admission.json"),
    ]
    if args.admission == "enforce":
        cmd.append("--enforce")
    r = sp.run(cmd, text=True, capture_output=True)
    for line in r.stdout.strip().splitlines()[-14:]:
        print(line)
    if r.returncode != 0:
        print(r.stderr[-400:])
        print("FAIL s9: admission step errored")
        ok = False
print("PASS s9" if ok else "FAIL s9")
sys.exit(0 if ok else 1)
