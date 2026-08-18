#!/usr/bin/env python3
"""C line step 1 (env-gen-yuxin env): build the RoboTwin shadow root (symlinks
to the real read-only RoboTwin + injected external assets from asset_library),
write extended overrides, and run the UPSTREAM catalog scanner over it.

Upstream repositories and the RoboTwin checkout are never written to. Runtime
locations come from the shared configuration layer or explicit CLI arguments,
so deleting an old contributor workspace cannot invalidate newly built links.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_config import GEN_ENV_ROOT, ROBOTWIN_ROOT  # noqa: E402
from lib import ledger  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument(
    "--robotwin-root",
    default=str(ROBOTWIN_ROOT),
)
parser.add_argument("--library-dir", required=True)
parser.add_argument("--shadow", required=True)
parser.add_argument("--ext-dir", required=True, help="data/scene_gen_ext output dir")
parser.add_argument("--upstream", default=str(GEN_ENV_ROOT))
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
# iter_assets, not iterdir: with the library grouped by provider the direct
# children are nvidia/ objaverse/ github/, and symlinking THOSE would hand the
# upstream scanner three bogus "assets" and hide all 65 real ones.
for item in ledger.iter_assets(lib):
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
            measured_int = row.get("interior")
            # v4 survey may SUPPRESS an interior (interior_suppressed): the
            # load test tipped the container, so publishing the cavity would
            # invite runtime rollovers (basket m3, B11 2026-08-13)
            if (
                measured_int
                and "interior_dimensions_m" not in entry
                and measured_int["dimensions_m"][2] >= 0.03
            ):
                # a cavity shallower than 3 cm holds nothing INSIDE-worthy
                # (a plate's 2 cm "cavity" made cup-in-plate solvable on
                # paper and refused x4608 in practice, 2026-08-13)
                entry["interior_dimensions_m"] = measured_int["dimensions_m"]
                entry["interior_floor_z_offset_m"] = measured_int["floor_z_offset_m"]
                # the floor is published check-safe (payload rest MINUS
                # pad); anti-wedging at spawn time is this clearance's job
                entry["support_spawn_clearance_m"] = 0.02
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
# ---- measured appearance attributes (colors) ----
# Upstream has supported colour-qualified retrieval all along (parser lifts
# "red"/"红色" into query.color, grounding matches it against entry.colors,
# the runtime tints by it) -- we simply never measured a single asset, so
# every query hit the "color metadata unknown" branch and any colour of cup
# satisfied "a red cup". measure_asset_attributes.py renders each usable
# model and votes its pixels into the ten canonical colours.
#
# Publishing rule: entry.colors is ASSET-level upstream, and a non-empty
# value makes grounding REJECT every mismatching query. So a colour is
# published only when all usable models of the asset agree; a multi-colour
# native like 110_basket (yellow m1, red m2, white m3) stays unknown rather
# than claiming a colour the spawned model may not have.
attr_path = ext / "asset_attributes.json"
if attr_path.exists():
    import yaml

    _attrs = json.loads(attr_path.read_text())
    _data_a = yaml.safe_load(ext_overrides.read_text()) or {}
    _root_a = _data_a.setdefault("assets", {})
    _n_col = _n_split = 0
    for _aid, _models in _attrs.get("models", {}).items():
        _sets = [
            tuple(r.get("colors") or ()) for r in _models.values() if not r.get("error")
        ]
        _sets = [s for s in _sets if s]
        if not _sets:
            continue
        _entry_a = _root_a.setdefault(_aid, {})
        if "colors" in _entry_a:
            continue  # hand-declared wins
        if len(set(_sets)) == 1:
            _entry_a["colors"] = list(_sets[0])
            _n_col += 1
        else:
            _entry_a["colors_disagree"] = [list(s) for s in sorted(set(_sets))]
            _n_split += 1
        # model-level colours regardless of asset-level agreement: the
        # grounder's model loop consumes CatalogModel.colors, which is what
        # lets "a yellow basket" pick basket m1 (yellow) out of an asset
        # whose other models are red and white (2026-08-16)
        for _mid_a, _row_a in _models.items():
            if _row_a.get("error") or not _row_a.get("colors"):
                continue
            _m_entry = _entry_a.setdefault("models", {}).setdefault(str(_mid_a), {})
            _m_entry.setdefault("colors", list(_row_a["colors"]))
    ext_overrides.write_text(
        "# GENERATED (see calibration header above)\n"
        + yaml.safe_dump(_data_a, sort_keys=False, allow_unicode=True)
    )
    print(
        f"attribute patch: {_n_col} assets got measured colors, "
        f"{_n_split} left unknown (models disagree)"
    )

# ---- runtime-verified revocations ----
# Our offline rigs are more forgiving than the RoboTwin runtime: a pose that
# settles here can keep micro-rocking there (339_panel 1.1 deg, 343_ceiling
# 3.1 deg in the late window, both after a 1500-step settle -- 2026-08-13).
# The runtime is what the user's Studio actually shows, so its verdict wins:
# models listed here lose their placement declaration and acquisition looks
# for a replacement. Entries are appended from real runtime evidence only.
revoke_path = ext / "runtime_revocations.json"
if revoke_path.exists():
    import yaml

    _rev = json.loads(revoke_path.read_text())
    _data_r = yaml.safe_load(ext_overrides.read_text()) or {}
    _root_r = _data_r.get("assets") or {}
    _n_rev = 0
    for _row in _rev.get("models", []):
        _e = (_root_r.get(_row["asset_id"], {}).get("models") or {}).get(
            str(_row["model_id"])
        )
        if isinstance(_e, dict) and "stable_pose_id" in _e:
            del _e["stable_pose_id"]
            _e["placement_revoked"] = _row.get("reason", "runtime verification failed")
            _n_rev += 1
    if _n_rev:
        ext_overrides.write_text(
            "# GENERATED (see calibration header above)\n"
            + yaml.safe_dump(_data_r, sort_keys=False, allow_unicode=True)
        )
    print(f"runtime revocations: {_n_rev} models revoked")

# ---- tabletop-view category exclusions (size-table plan C) ----
# The size table records each category's typical REAL size -- an
# environment-neutral fact. Whether that fits a 0.70x0.50 m table is THIS
# view's ruling, so categories whose typical size exceeds the view's refuse
# threshold lose their placement declarations HERE, in the generated view,
# not in the ledger: the asset keeps its real-size semantics for future
# non-tabletop environments (born from "everything looks basket-sized",
# 2026-08-15 -- a 2 m sofa served as a 25 cm miniature is a lie, and
# deleting the sofa would be waste; the view just declines to serve it).
_cs_path = Path(__file__).resolve().parents[2] / "configs" / "category_sizes.yml"
if _cs_path.is_file():
    import yaml as _yaml_cs

    _cs = _yaml_cs.safe_load(_cs_path.read_text()) or {}
    _refuse_over = float(
        ((_cs.get("views") or {}).get("tabletop") or {}).get("refuse_over_m", 0.84)
    )
    _oversize_cats = {
        c for c, r in (_cs.get("sizes") or {}).items()
        if float(r.get("size_m", 0)) > _refuse_over
    }
    _data_cs = _yaml_cs.safe_load(ext_overrides.read_text()) or {}
    _n_view = 0
    for _aid, _asset_cs in (_data_cs.get("assets") or {}).items():
        if (_asset_cs or {}).get("category") not in _oversize_cats:
            continue
        for _mid, _entry_cs in (_asset_cs.get("models") or {}).items():
            if isinstance(_entry_cs, dict) and "stable_pose_id" in _entry_cs:
                del _entry_cs["stable_pose_id"]
                _entry_cs["placement_infeasible"] = (
                    "typical real size exceeds tabletop view "
                    f"(category {_asset_cs.get('category')!r} > {_refuse_over} m); "
                    "asset retained for non-tabletop environments"
                )
                _n_view += 1
    if _n_view:
        ext_overrides.write_text(
            "# GENERATED (see calibration header above)\n"
            + _yaml_cs.safe_dump(_data_cs, sort_keys=False, allow_unicode=True)
        )
    print(f"tabletop-view exclusions: {_n_view} models across {len(_oversize_cats)} oversize categories")

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
# ---- external-only catalog view (s12's gate reads this) ----
# Nothing used to write this file. It was produced by hand on 2026-08-03 and
# then rotted silently: by 2026-08-14 it still carried the PREVIOUS machine's
# absolute paths, so every s12 case failed its real_asset_files check while the
# physics underneath was fine (runtime status=pass, drift ~1mm). Deriving it
# here, unconditionally, from the catalog this run just built is the fix -- a
# view regenerated with its source cannot drift from it. Derived last, so it
# also reflects whatever `--admission enforce` filtered out of the main view.
if ok:
    _full = json.loads(cat_out.read_text())
    _lib_prefix = str(lib.resolve()) + "/"
    _ext_entries = [
        e
        for e in _full["entries"]
        if str(Path(e["asset_path"]).resolve()).startswith(_lib_prefix)
    ]
    _ext_only_out = ext / "asset_catalog_external_only.json"
    with _ext_only_out.open("w", encoding="utf-8") as _s:
        json.dump({**_full, "entries": _ext_entries}, _s, indent=2, ensure_ascii=False)
        _s.write("\n")
    print(f"external-only catalog: {len(_ext_entries)} entries -> {_ext_only_out}")
    if not _ext_entries:
        print("FAIL s9: external-only view is empty (s12 would have nothing to ground)")
        ok = False

print("PASS s9" if ok else "FAIL s9")
sys.exit(0 if ok else 1)
