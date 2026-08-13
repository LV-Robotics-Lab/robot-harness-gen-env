#!/usr/bin/env python3
"""One-off (work/oneoff/): migrate every asset_ledger.v1 ledger to v2.

Dry-run by default; --apply writes. Idempotent: a ledger already declaring v2
is reported as `already_v2` and left alone.

What it does, per ledger:

  profile            derived ONCE here, from evidence already on disk: an
                     asset whose every model owns a non-snapshot isaacsim
                     representation is cross_backend, everything else is
                     sapien_only. Derived at migration and declared from then
                     on -- later writers must state it, never infer it.
  semantics.identity basis attributed from the manifests that actually
                     recorded the decision; anything not positively
                     attributable becomes `unknown` rather than a guess.
  source.kind        "retrieved" for every existing model. Nothing in the
                     library was generated, so there is no inference to make.
  scale_applied      deleted, after asserting it equals
                     size_resolution["scale"] on that same model. If any
                     model disagrees the migration ABORTS: an unequal pair
                     would mean the two fields had drifted apart and the
                     deletion would be destroying information.
  actual_max_dim_m   repaired where backfill_upstream wrote the post-scale
                     reading (its own max(mesh_bbox_m)) instead of the
                     pre-scale one conventions.resolve_size produces. The
                     repair is exact arithmetic -- max(bbox)/scale -- and is
                     applied ONLY to models carrying that exact signature;
                     any other invariant failure aborts rather than guesses.
  physical.inertial  added as a structured unknown to cross_backend assets
                     (basis=urdf_inertial for articulated, engine_derived for
                     rigid). No values are invented; the field states where
                     the mass distribution comes from, which is precisely
                     what a transfer compiler needs and what nothing on disk
                     records today.

Nothing is written unless the migrated document passes validate_ledger with
zero violations.
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "1_asset_reuse"))
from lib import ledger as L  # noqa: E402


def _has_isaac(model):
    return any(
        r.get("backend") == "isaacsim" and r.get("role") != "snapshot"
        for r in model.get("representations") or []
    )


def derive_profile(led):
    models = led.get("models") or []
    if models and all(_has_isaac(m) for m in models):
        return "cross_backend"
    return "sapien_only"


def load_manifest_assets(path):
    p = Path(path)
    if not p.exists():
        return set()
    data = json.loads(p.read_text())
    out = set()
    for group in data.get("groups") or []:
        for item in group.get("items") or []:
            if item.get("asset"):
                out.add(item["asset"])
    return out


def derive_identity(led, dir_name, is_upstream, hand, acquired, evidence_paths):
    if is_upstream:
        basis, evidence = "upstream_catalog", evidence_paths.get("upstream")
    elif dir_name in acquired:
        basis, evidence = "requested_by_acquire", evidence_paths.get("acquired")
    elif dir_name in hand:
        basis, evidence = "manifest_human", evidence_paths.get("human")
    else:
        basis, evidence = "unknown", None
    return {"basis": basis, "evidence": evidence, "verified": False}


def migrate_model(model, *, kind, profile, asset_id, notes):
    physical = model.get("physical") or {}
    sizing = physical.get("size_resolution") or {}

    # scale_applied: delete only after proving it is the duplicate we think.
    if "scale_applied" in physical:
        if physical["scale_applied"] != sizing.get("scale"):
            raise SystemExit(
                f"ABORT {asset_id} model={model.get('model_id')}: "
                f"scale_applied={physical['scale_applied']!r} != "
                f"size_resolution.scale={sizing.get('scale')!r}; the two fields "
                f"have drifted, deleting one would lose information"
            )
        del physical["scale_applied"]

    bbox = physical.get("mesh_bbox_m")
    actual, scale = sizing.get("actual_max_dim_m"), sizing.get("scale")
    if (
        isinstance(bbox, list)
        and bbox
        and isinstance(actual, (int, float))
        and isinstance(scale, (int, float))
        and scale
    ):
        measured, expected = max(bbox), actual * scale
        if abs(measured - expected) > 1e-3 * max(abs(expected), 1e-9):
            # backfill_upstream's signature: actual_max_dim_m IS max(bbox),
            # i.e. the post-scale reading under a pre-scale field name.
            if abs(actual - measured) <= 1e-9:
                sizing["actual_max_dim_m"] = measured / scale
                notes.append(
                    f"{asset_id}/m{model.get('model_id')}: actual_max_dim_m "
                    f"{actual:.6f} -> {sizing['actual_max_dim_m']:.6f} "
                    f"(post-scale reading repaired to pre-scale)"
                )
            else:
                raise SystemExit(
                    f"ABORT {asset_id} model={model.get('model_id')}: size "
                    f"invariant fails (max(bbox)={measured!r}, "
                    f"actual*scale={expected!r}) and does not match the known "
                    f"post-scale signature -- refusing to guess a repair"
                )

    source = model.setdefault("source", {})
    source.setdefault("kind", "retrieved")

    if profile == "cross_backend" and "inertial" not in physical:
        basis = "urdf_inertial" if kind == "articulated" else "engine_derived"
        physical["inertial"] = L.unknown_inertial(basis)
        notes.append(
            f"{asset_id}/m{model.get('model_id')}: inertial added (basis={basis})"
        )
    return model


def migrate(led, *, dir_name, is_upstream, hand, acquired, evidence_paths, notes):
    led["schema_version"] = L.SCHEMA_VERSION
    profile = derive_profile(led)
    led["profile"] = profile
    semantics = led.setdefault("semantics", {})
    semantics["identity"] = derive_identity(
        led, dir_name, is_upstream, hand, acquired, evidence_paths
    )
    for m in led.get("models") or []:
        migrate_model(
            m,
            kind=led.get("kind"),
            profile=profile,
            asset_id=led.get("asset_id"),
            notes=notes,
        )
    return led


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger-dir", action="append", required=True)
    ap.add_argument("--upstream-dirname", default="upstream_ledgers")
    ap.add_argument("--manifest-human", default="")
    ap.add_argument("--manifest-acquired", default="")
    ap.add_argument("--apply", action="store_true", help="write; default is dry-run")
    args = ap.parse_args()

    hand = load_manifest_assets(args.manifest_human) if args.manifest_human else set()
    acquired = (
        load_manifest_assets(args.manifest_acquired)
        if args.manifest_acquired
        else set()
    )
    evidence_paths = {
        "human": args.manifest_human or None,
        "acquired": args.manifest_acquired or None,
        "upstream": "external/env-gen-github/data/scene_gen/asset_catalog.json",
    }

    migrated, skipped, failed, notes = [], [], [], []
    profiles, bases = {}, {}

    for d in args.ledger_dir:
        root = Path(d)
        is_upstream = root.name == args.upstream_dirname
        for asset_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            lp = asset_dir / "ledger.json"
            if not lp.exists():
                continue
            led = json.loads(lp.read_text())
            if led.get("schema_version") == L.SCHEMA_VERSION:
                skipped.append(asset_dir.name)
                continue

            led = migrate(
                led,
                dir_name=asset_dir.name,
                is_upstream=is_upstream,
                hand=hand,
                acquired=acquired,
                evidence_paths=evidence_paths,
                notes=notes,
            )
            violations = L.validate_ledger(led, check_files=False)
            profiles[led["profile"]] = profiles.get(led["profile"], 0) + 1
            b = led["semantics"]["identity"]["basis"]
            bases[b] = bases.get(b, 0) + 1
            if violations:
                failed.append((asset_dir.name, violations))
                continue
            migrated.append((lp, led))

    for note in notes:
        print(f"  note: {note}")
    print(f"\nmigrated ok : {len(migrated)}")
    print(f"already v2  : {len(skipped)}")
    print(f"FAILED      : {len(failed)}")
    for name, violations in failed:
        print(f"  {name}")
        for v in violations[:6]:
            print(f"    {v.code:<26} {v.path}: {v.message}")
    print(f"profiles    : {profiles}")
    print(f"identity    : {bases}")

    if failed:
        raise SystemExit("refusing to write: some ledgers do not validate as v2")

    if not args.apply:
        print("\ndry-run (pass --apply to write)")
        return
    for lp, led in migrated:
        L.write_ledger(lp, led)
    print(f"\nwrote {len(migrated)} ledger(s)")


if __name__ == "__main__":
    main()
