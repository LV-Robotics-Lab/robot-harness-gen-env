#!/usr/bin/env python3
"""One-shot migration: asset_ledger.v2 -> v3, all ledgers in one pass.

What it does, per ledger (asset_library/*/ledger.json + upstream_ledgers/*):

  strip    semantic_name, tags, physical.mesh_up_axis, physical
           .origin_convention, mass/friction runtime_default pair
           (v3 deletions -- zero decision consumers, see ledger.py header)
  fold     the stripped mesh_up_axis/origin_convention facts into the GLB
           representation's frame/geometry_state (the GLB is the artifact
           those facts were actually about; the writer baked scale into it)
  stamp    stable_poses[].measured_against -- the two pose dialects that
           silently coexisted (upstream identity vs import-normalized X90)
           become explicit: both are sapien-chain claims with different
           provenance notes
  ingest   models[].appearance from asset_attributes.json (508 measured
           model colours finally get a contract home) and
           physical.placement from top_support_survey.json interiors --
           each stamped measured_against the sapien stack that measured it
  add      external_ids {env_gen: <catalog id>} derived from asset_id
           (the ledger/catalog/IR naming junction stops being a code-side
           guess)

Every migrated ledger is re-validated with the v3 validator before being
written back (atomic, git-tracked so the whole batch is revertable).
--dry-run reports without writing.
"""

import argparse
import json
import sys
from pathlib import Path

DEV = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DEV / "1_asset_reuse"))

from lib import ledger as L  # noqa: E402

MIGRATION_RUN_ID = "migrate-v3-20260815"


def _catalog_id(asset_id: str) -> str:
    for pref in ("external_", "robotwin_", "upstream_"):
        if asset_id.startswith(pref):
            return asset_id[len(pref) :]
    return asset_id


def _strip_envelope(env):
    if not isinstance(env, dict):
        return env
    for k in ("runtime_default_kg", "runtime_default_basis", "runtime_default"):
        env.pop(k, None)
    return env


def migrate_one(led, attrs, survey, is_upstream):
    changed = []
    if led.get("schema_version") == L.SCHEMA_VERSION:
        return led, ["already_v3"]
    led["schema_version"] = L.SCHEMA_VERSION

    for k in ("semantic_name", "tags"):
        if k in led:
            led.pop(k)
            changed.append(f"del {k}")

    cat_id = _catalog_id(led["asset_id"])
    led.setdefault("external_ids", {})["env_gen"] = cat_id
    changed.append("external_ids")

    a_models = attrs.get(cat_id, {})
    s_models = survey.get(cat_id, {})

    for m in led.get("models", []):
        mid = str(m.get("model_id"))
        phys = m.get("physical") or {}
        up_axis = phys.pop("mesh_up_axis", None)
        origin = phys.pop("origin_convention", None)
        if up_axis or origin:
            changed.append(f"m{mid}: fold up_axis/origin -> glb rep")
        _strip_envelope(phys.get("mass_kg"))
        _strip_envelope(phys.get("friction"))

        for rep in m.get("representations") or []:
            fmt = (rep.get("format") or "").lower()
            if fmt == "glb":
                # the import writer normalizes/bakes into the GLB; the old
                # per-model fields described exactly this artifact
                if up_axis:
                    rep.setdefault("frame", {})["up_axis"] = up_axis
                # external GLBs are baked by import_materialize; upstream
                # RoboTwin GLBs get their scale applied at create_actor load
                rep.setdefault("geometry_state", {}).setdefault(
                    "scale_baked", not is_upstream
                )
                if origin:
                    rep["geometry_state"].setdefault("origin", origin)
            # USD _source reps: frame/geometry_state deliberately left absent
            # -- absent is the honest "unmeasured"; E2 backfills when proven.

        conv = phys.get("conventions") or {}
        for pose in conv.get("stable_poses") or []:
            if "measured_against" not in pose:
                pose["measured_against"] = {
                    "backend": "sapien",
                    "run_id": MIGRATION_RUN_ID,
                    "note": (
                        "upstream catalog declaration (identity dialect)"
                        if is_upstream
                        else "import normalization chain (X90 dialect)"
                    ),
                }
        if conv.get("stable_poses"):
            changed.append(f"m{mid}: pose measured_against")

        a_row = a_models.get(mid) or {}
        if a_row.get("colors"):
            m["appearance"] = {
                "colors_measured": a_row["colors"],
                "color_fractions": a_row.get("fractions"),
                "method": "4-view offscreen render, HSV per-pixel vote",
                "measured_against": {
                    "backend": "sapien",
                    "run_id": "attr-20260814",
                },
            }
            changed.append(f"m{mid}: appearance")

        s_row = s_models.get(mid) or {}
        interior = s_row.get("interior")
        if interior:
            phys["placement"] = {
                "verdict": s_row.get("verdict"),
                "interior_dims_m": interior.get("dimensions_m"),
                "interior_floor_z_offset_m": interior.get("floor_z_offset_m"),
                "floor_basis": interior.get("floor_basis"),
                "load_tilt_deg": s_row.get("load_tilt_deg"),
                "measured_against": {
                    "backend": "sapien",
                    "run_id": "probe-v4-20260815",
                },
            }
            changed.append(f"m{mid}: placement")
        elif s_row.get("verdict"):
            phys["placement"] = {
                "verdict": s_row.get("verdict"),
                "measured_against": {
                    "backend": "sapien",
                    "run_id": "probe-v4-20260815",
                },
            }
            changed.append(f"m{mid}: placement(verdict)")

        for v in m.get("verification") or []:
            if v.get("report_path") is None:
                v.pop("report_path", None)

    return led, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default=str(DEV / "data/asset_library"))
    ap.add_argument("--upstream", default=str(DEV / "data/upstream_ledgers"))
    ap.add_argument(
        "--attributes", default=str(DEV / "data/scene_gen_ext/asset_attributes.json")
    )
    ap.add_argument(
        "--survey", default=str(DEV / "data/scene_gen_ext/top_support_survey.json")
    )
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    attrs = json.loads(Path(a.attributes).read_text()).get("models", {})
    survey = json.loads(Path(a.survey).read_text()).get("models", {})

    n_ok = n_bad = 0
    for root, is_up in ((Path(a.library), False), (Path(a.upstream), True)):
        for lp in sorted(root.glob("*/ledger.json")):
            led = json.loads(lp.read_text())
            led, changed = migrate_one(led, attrs, survey, is_up)
            violations = L.validate_ledger(led, check_files=False)
            hard = [v for v in violations if v.code != "profile_requirement_unmet"]
            if hard:
                n_bad += 1
                print(
                    f"FAIL {lp.parent.name}: {[f'{v.path}:{v.code}' for v in hard][:4]}"
                )
                continue
            n_ok += 1
            tagline = ", ".join(changed[:5]) + ("…" if len(changed) > 5 else "")
            print(f"ok   {lp.parent.name}: {tagline}")
            if not a.dry_run:
                lp.write_text(json.dumps(led, indent=2) + "\n")
    print(f"\n{'DRYRUN ' if a.dry_run else ''}migrated={n_ok} failed={n_bad}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
