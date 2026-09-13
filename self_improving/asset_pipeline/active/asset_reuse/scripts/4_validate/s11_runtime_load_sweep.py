#!/usr/bin/env python3
"""Runtime-load sweep (env-gen-yuxin env): every accepted external model is
loaded through RoboTwin's REAL create_actor path (the same code env-gen's
scene replay uses), settled, and checked — closing the gap between "loads in
raw SAPIEN" and "loads through the env-gen runtime".

Run with cwd = shadow root (create_actor resolves ./assets/objects/<name>).

After the sweep, each row is backfilled as a runtime_load verification entry
onto the swept model's authoritative per-asset ledger (--library-dir);
assets with no ledger yet (not backfilled to v1) are reported and skipped.
"""

import argparse
import datetime
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import ledger, ledger_writes  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--shadow", required=True)
parser.add_argument("--catalog", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--library-dir", default="../data/asset_library")
args = parser.parse_args()

# Resolve before os.chdir(args.shadow) below -- the default is relative to
# the invocation cwd (repo convention: run from asset_reuse/), not to the
# shadow root the script chdirs into for create_actor's relative lookups.
library_dir = Path(args.library_dir).resolve()
catalog_path = Path(args.catalog).resolve()
out = Path(args.out).resolve()
shadow_path = Path(args.shadow).resolve()

STABLE_Q = [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0]
cat = json.loads(catalog_path.read_text())
catalog_pairs = set()
try:
    for catalog_entry in cat["entries"]:
        catalog_entry["asset_id"] = ledger.canonical_asset_key(catalog_entry.get("asset_id"))
        for catalog_model in catalog_entry.get("models", []):
            catalog_model["model_id"] = ledger.canonical_model_id(catalog_model.get("model_id"))
            ledger.claim_asset_model(
                catalog_pairs, catalog_entry["asset_id"], catalog_model["model_id"]
            )
except (
    KeyError,
    TypeError,
    ledger.UnsafeAssetKeyError,
    ledger.InvalidModelIdError,
    ledger.DuplicateAssetModelError,
) as exc:
    print(f"FAIL s11: unsafe, malformed, or duplicate catalog identity ({exc})")
    sys.exit(1)
targets = [
    e
    for e in cat["entries"]
    if e["asset_id"].startswith("3")
    and e["asset_id"][0] == "3"
    and e["asset_id"][:3].isdigit()
    and int(e["asset_id"][:3]) >= 301
]


class SourceIdentityError(ValueError):
    """The catalog/shadow object is not the model qualified in the ledger."""


def _existing_path(raw, *, field):
    try:
        raw_path = os.fspath(raw)
    except TypeError as exc:
        raise SourceIdentityError(f"{field} is missing or not a non-empty path") from exc
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise SourceIdentityError(f"{field} is missing or not a non-empty path")
    try:
        return Path(raw_path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SourceIdentityError(f"{field} cannot be resolved safely: {raw!r}") from exc


def _samefile(left, right):
    try:
        return Path(left).samefile(Path(right))
    except OSError:
        return False


def _require_same_directory(left, right, *, field):
    left_path = _existing_path(left, field=field)
    right_path = _existing_path(str(right), field="authoritative library asset")
    if not left_path.is_dir() or not right_path.is_dir() or not _samefile(left_path, right_path):
        raise SourceIdentityError(
            f"{field} is not the authoritative library asset tree: {left_path}"
        )
    return left_path


def _record_path(record, *, field):
    if not ledger.artifact_file_record_is_current(record):
        raise SourceIdentityError(f"{field} is stale, missing, or unsafe")
    try:
        return Path(ledger.resolve_uri(record["uri"])).resolve(strict=True)
    except (OSError, RuntimeError, KeyError, TypeError, ValueError) as exc:
        raise SourceIdentityError(f"{field} cannot be resolved safely") from exc


def _sapien_representation_identity(model):
    """Return trusted input records and primary paths for the SAPIEN model."""

    inputs = {}
    primary_paths_by_role = {}
    for rep_index, representation in enumerate(model.get("representations", [])):
        if (
            not isinstance(representation, dict)
            or representation.get("backend") != "sapien"
            or representation.get("role") == "snapshot"
        ):
            continue
        files = representation.get("files")
        if not isinstance(files, list):
            raise SourceIdentityError("SAPIEN representation has no files[] closure")
        primary = (representation.get("uri"), representation.get("sha256"))
        found_primary = False
        for file_index, member in enumerate(files):
            if not isinstance(member, dict):
                raise SourceIdentityError("SAPIEN representation files[] has a non-object member")
            key = f"sapien_representation_{rep_index}_{file_index}"
            inputs[key] = dict(member)
            member_path = _record_path(member, field=key)
            if (member.get("uri"), member.get("sha256")) == primary:
                primary_paths_by_role.setdefault(representation.get("role"), []).append(member_path)
                found_primary = True
        if not found_primary:
            raise SourceIdentityError("SAPIEN representation primary file is not in files[]")
    if not primary_paths_by_role:
        raise SourceIdentityError("ledger model has no SAPIEN loader-visible representation")
    return inputs, primary_paths_by_role


def _require_catalog_field_matches_roles(catalog_model, field, roles, primary_paths_by_role):
    candidate = _existing_path(catalog_model.get(field), field=f"catalog model {field}")
    primary_paths = [primary for role in roles for primary in primary_paths_by_role.get(role, ())]
    if not primary_paths or not any(_samefile(candidate, primary) for primary in primary_paths):
        raise SourceIdentityError(
            f"catalog model {field} is not one of the authoritative "
            f"library SAPIEN {roles} representation primaries"
        )


def _require_catalog_model_matches_ledger(catalog_model, primary_paths_by_role):
    if catalog_model.get("urdf_path"):
        _require_catalog_field_matches_roles(
            catalog_model,
            "urdf_path",
            ("visual_and_collision",),
            primary_paths_by_role,
        )
    else:
        _require_catalog_field_matches_roles(
            catalog_model,
            "visual_path",
            ("visual", "visual_and_collision"),
            primary_paths_by_role,
        )
        _require_catalog_field_matches_roles(
            catalog_model,
            "collision_path",
            ("collision", "visual_and_collision"),
            primary_paths_by_role,
        )


def _require_catalog_metadata_matches_ledger(catalog_model, loaded, model_id):
    raw = catalog_model.get("metadata_path")
    if raw is None:
        return None
    candidate = _existing_path(raw, field="catalog model metadata_path")
    expected = loaded.asset_dir / f"model_data{model_id}.json"
    if not expected.is_file() or not _samefile(candidate, expected):
        raise SourceIdentityError(
            "catalog model metadata_path is not the authoritative library model_data file"
        )
    return ledger_writes.provenance_file_record(candidate)


def _require_prior_physical_qualification(loaded, model):
    check = "joint_sweep" if loaded.document.get("kind") == "articulated" else "settle"
    receipt = ledger.latest_trusted_verification(model, "sapien", check, loaded.asset_key)
    if receipt is None or receipt.get("verdict") != "pass":
        raise SourceIdentityError(f"library model is not qualified by trusted {check}/pass")
    return receipt


def _ledger_stable_placement(model):
    """Return the sole default placement the runtime is allowed to execute."""

    try:
        conventions_block = model["physical"]["conventions"]
        defaults = [
            pose
            for pose in conventions_block["stable_poses"]
            if isinstance(pose, dict) and pose.get("is_default") is True
        ]
        if len(defaults) != 1:
            raise ValueError("default pose count")
        pose = defaults[0]
        pose_id = pose["pose_id"]
        orientation = pose["orientation_wxyz"]
        z_policy = conventions_block["z_policy"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceIdentityError("authoritative ledger has no canonical stable placement") from exc
    if (
        not isinstance(pose_id, str)
        or not pose_id
        or not isinstance(orientation, list)
        or len(orientation) != 4
        or not all(type(value) in (int, float) and math.isfinite(value) for value in orientation)
        or not isinstance(z_policy, str)
        or not z_policy
    ):
        raise SourceIdentityError("authoritative ledger stable placement is malformed")
    return {
        "stable_pose_id": pose_id,
        "stable_orientation_wxyz": list(orientation),
        "z_policy": z_policy,
    }


def _require_catalog_stable_placement(catalog_model, model):
    """Reject a catalog that claims a placement different from its ledger."""

    placement = _ledger_stable_placement(model)
    fields = tuple(placement)
    claimed = [field for field in fields if field in catalog_model]
    if not claimed:
        return placement
    if len(claimed) != len(fields) or any(
        catalog_model[field] != placement[field] for field in fields
    ):
        raise SourceIdentityError(
            "catalog stable placement does not exactly match the authoritative ledger"
        )
    return placement


def _require_loader_semantics(loaded, catalog_model, model, placement):
    """Bind the catalog loader choice to the physical mode that is executed."""

    try:
        is_static = model["physical"]["conventions"]["is_static"]
    except (KeyError, TypeError) as exc:
        raise SourceIdentityError("authoritative ledger has no static/dynamic convention") from exc
    uses_fixed_root = bool(catalog_model.get("urdf_path"))
    if uses_fixed_root:
        if loaded.document.get("kind") != "articulated" or is_static is not True:
            raise SourceIdentityError(
                "URDF runtime load requires an articulated, fixed-root ledger model"
            )
    elif loaded.document.get("kind") == "articulated" or is_static is not False:
        raise SourceIdentityError(
            "rigid runtime load requires a dynamic, non-articulated ledger model"
        )
    if placement["z_policy"] != "origin_on_table":
        raise SourceIdentityError(
            "runtime load spawn height only implements the origin_on_table z_policy"
        )


def _runtime_source_identity(entry, catalog_model):
    asset = entry["asset_id"]
    model_id = catalog_model["model_id"]
    loaded = ledger.load_asset_ledger(library_dir, asset)
    violations = ledger.validate_ledger(loaded.document, check_files=True)
    if violations:
        first = violations[0]
        raise SourceIdentityError(f"authoritative ledger is invalid: {first.path}:{first.code}")
    model = next((item for item in loaded.document["models"] if item["model_id"] == model_id), None)
    if model is None:
        raise SourceIdentityError(f"authoritative ledger has no model_id={model_id}")

    _require_same_directory(
        shadow_path / "assets" / "objects" / asset,
        loaded.asset_dir,
        field="shadow asset",
    )
    _require_same_directory(entry.get("asset_path"), loaded.asset_dir, field="catalog asset_path")
    representation_inputs, primary_paths_by_role = _sapien_representation_identity(model)
    _require_catalog_model_matches_ledger(catalog_model, primary_paths_by_role)
    metadata_record = _require_catalog_metadata_matches_ledger(catalog_model, loaded, model_id)
    placement = _require_catalog_stable_placement(catalog_model, model)
    _require_loader_semantics(loaded, catalog_model, model, placement)
    _require_prior_physical_qualification(loaded, model)
    digest = ledger.reps_digest(model, "sapien")
    inputs = dict(representation_inputs)
    if metadata_record is not None:
        inputs["catalog_metadata"] = metadata_record
    return {
        "asset_dir": str(loaded.asset_dir.resolve(strict=True)),
        "ledger_path": str(loaded.ledger_path.resolve(strict=True)),
        "digest": digest,
        "inputs": inputs,
        "asset_files": (
            {"model_metadata": ledger.resolve_uri(metadata_record["uri"])}
            if metadata_record is not None
            else {}
        ),
        "loaded": loaded,
        "model": model,
        "placement": placement,
    }


def _same_source_identity(left, right):
    return (
        left["asset_dir"] == right["asset_dir"]
        and left["ledger_path"] == right["ledger_path"]
        and left["digest"] == right["digest"]
        and left["inputs"] == right["inputs"]
        and left["placement"] == right["placement"]
    )


rows = []
source_identities = {}
replay_targets = []
source_identity_failures = 0
for entry in targets:
    for m in entry["models"]:
        if not m.get("usable"):
            continue
        aid, mid = entry["asset_id"], m["model_id"]
        row = {"asset": aid, "model": mid}
        try:
            identity = _runtime_source_identity(entry, m)
            identity["execution_snapshot"] = ledger_writes.publish_execution_snapshot(
                identity["loaded"].asset_dir / "verification_inputs",
                source_asset_dir=identity["loaded"].asset_dir,
                asset_key=aid,
                model_entry=identity["model"],
                asset_files=identity["asset_files"],
                extra_files={"catalog": catalog_path},
            )
            source_identities[(aid, mid)] = identity
        except (OSError, ValueError, json.JSONDecodeError, SourceIdentityError) as exc:
            row.update(
                status="fail",
                error=f"source identity mismatch: {exc}",
                timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
            )
            rows.append(row)
            source_identity_failures += 1
            print(f"FAIL {aid} m{mid} source identity mismatch: {exc}")
            continue
        replay_targets.append((entry, m))

if replay_targets:
    import_cwd = Path.cwd()
    os.chdir(shadow_path)
    sys.path.insert(0, str(shadow_path))

    import sapien  # noqa: E402
    from envs import utils as runtime_utils  # noqa: E402

    create_actor = runtime_utils.create_actor
    create_sapien_urdf_obj = runtime_utils.create_sapien_urdf_obj
    os.chdir(import_cwd)


def _executed_runtime_capability():
    """Capture the runtime only after the real loader path has run.

    Loader functions can import further Python or native modules lazily.  A
    capability captured immediately after importing their public symbols is
    therefore a statement about setup, rather than the code that executed.
    """

    return ledger_writes.capture_runtime_capability(
        loader_modules=[runtime_utils],
        sapien_module=sapien,
        entrypoint="RoboTwin envs.utils create_actor/create_sapien_urdf_obj",
        config={
            "loader_root": "immutable_snapshot_cwd",
            "timestep_s": 0.01,
            "steps": 200,
            "late_window_start_step": 150,
            "convex": True,
            "rigid_pose_wxyz": STABLE_Q,
            "urdf_fix_root_link": True,
        },
    )


for entry, m in replay_targets:
    aid, mid = entry["asset_id"], m["model_id"]
    row = {"asset": aid, "model": mid}
    replay_cwd = Path.cwd()
    try:
        row["fix_root_link"] = bool(m.get("urdf_path"))
        execution_snapshot = source_identities[(aid, mid)]["execution_snapshot"]
        execution_root = Path(ledger.resolve_uri(execution_snapshot["root_uri"]))
        os.chdir(execution_root)
        sc = sapien.Scene()
        sc.set_timestep(1 / 100)
        sc.add_ground(0)
        if m.get("urdf_path"):
            actor = create_sapien_urdf_obj(
                sc,
                sapien.Pose(
                    p=[0, 0, 0.005],
                    q=source_identities[(aid, mid)]["placement"]["stable_orientation_wxyz"],
                ),
                modelname=aid,
                modelid=mid,
                fix_root_link=True,
            )
        else:
            actor = create_actor(
                sc,
                sapien.Pose(
                    p=[0, 0, 0.005],
                    q=source_identities[(aid, mid)]["placement"]["stable_orientation_wxyz"],
                ),
                modelname=aid,
                convex=True,
                is_static=False,
                model_id=mid,
            )
        if actor is None:
            raise RuntimeError("create_actor returned None")
        ent = getattr(actor, "actor", actor)
        spawn_pose = ent.get_pose()
        p0 = np.asarray(spawn_pose.p, dtype=float)
        observed_spawn_orientation = np.asarray(spawn_pose.q, dtype=float)
        expected_spawn_orientation = np.asarray(
            source_identities[(aid, mid)]["placement"]["stable_orientation_wxyz"],
            dtype=float,
        )
        if (
            p0.shape != (3,)
            or observed_spawn_orientation.shape != (4,)
            or not np.isfinite(p0).all()
            or not np.isfinite(observed_spawn_orientation).all()
            or abs(float(np.linalg.norm(observed_spawn_orientation)) - 1.0) > 1e-6
            or abs(float(np.dot(observed_spawn_orientation, expected_spawn_orientation)))
            < 1.0 - 1e-6
        ):
            raise RuntimeError("loader returned a different or invalid spawn orientation")
        row.update(
            observed_spawn_orientation_wxyz=observed_spawn_orientation.tolist(),
            observed_spawn_origin_z_m=float(p0[2]),
            fix_root_link=row["fix_root_link"],
        )
        mid_p = None
        for i in range(200):
            sc.step()
            if i == 150:
                mid_p = np.array(ent.get_pose().p)
        pf = np.array(ent.get_pose().p)
        drift = float(np.linalg.norm(pf - mid_p))
        row.update(
            final_z_m=float(pf[2]),
            late_drift_m=drift,
            finite=bool(np.isfinite(pf).all()),
            settled=drift < 0.002,
            no_penetration=float(pf[2]) > -0.005,
        )
        row["status"] = (
            "pass" if row["finite"] and row["settled"] and row["no_penetration"] else "fail"
        )
    except Exception as exc:  # noqa: BLE001
        row.update(status="fail", error=f"{type(exc).__name__}: {exc}")
    finally:
        os.chdir(replay_cwd)
    row["timestamp"] = datetime.datetime.now().isoformat(timespec="seconds")
    rows.append(row)
    print(
        f"{row['status'].upper()} {aid} m{mid} "
        f"z={row.get('final_z_m', float('nan')):.4f} "
        f"{row.get('error', '')}"
    )

out.parent.mkdir(parents=True, exist_ok=True)

prepared_writebacks = []
preparation_failures = 0
for row in rows:
    preflight = source_identities.get((row["asset"], row["model"]))
    if preflight is None:
        continue
    try:
        entry, catalog_model = next(
            (entry, model)
            for entry, model in replay_targets
            if entry["asset_id"] == row["asset"] and model["model_id"] == row["model"]
        )
        postflight = _runtime_source_identity(entry, catalog_model)
        if not _same_source_identity(preflight, postflight):
            raise SourceIdentityError("authoritative representation source changed during replay")
        if not ledger.execution_snapshot_is_current(preflight["execution_snapshot"]):
            raise SourceIdentityError("immutable execution snapshot changed during replay")
    except (OSError, ValueError, json.JSONDecodeError, SourceIdentityError) as exc:
        row["writeback_error"] = f"source identity mismatch after replay: {exc}"
        preparation_failures += 1
        print(
            f"FAIL backfill {row['asset']} m{row['model']}: "
            f"source identity mismatch after replay: {exc}"
        )
        continue
    loaded = postflight["loaded"]
    digest = postflight["digest"]
    # The physical report itself carries the representation-set digest; the
    # ledger receipt below copies that fact rather than manufacturing a trust
    # binding absent from the evidence producer.
    row["verified_digest"] = digest
    thresholds = {"max_late_drift_m": 0.002, "min_final_z_m": -0.005}
    payload = ledger_writes.issue_qualified_verification(
        issuer="asset.s11_runtime_load.v1",
        asset_key=row["asset"],
        model_id=row["model"],
        run_id=out.stem,
        timestamp=row["timestamp"],
        reps_digest=digest,
        inputs={
            "task": {
                "asset_key": row["asset"],
                "model_id": row["model"],
                "execution_root": preflight["execution_snapshot"]["root_uri"],
                "loader": "RoboTwin create_actor/create_sapien_urdf_obj",
            },
        },
        thresholds=thresholds,
        result={
            "schema": "asset_runtime_load_result.v3",
            "loaded": "error" not in row,
            "finite": bool(row.get("finite", False)),
            "late_drift_m": row.get("late_drift_m"),
            "final_z_m": row.get("final_z_m"),
            "spawn_pose_id": postflight["placement"]["stable_pose_id"],
            "spawn_orientation_wxyz": postflight["placement"]["stable_orientation_wxyz"],
            "observed_spawn_orientation_wxyz": row.get("observed_spawn_orientation_wxyz"),
            "observed_spawn_origin_z_m": row.get("observed_spawn_origin_z_m"),
            "fix_root_link": bool(row.get("fix_root_link", False)),
            "z_policy": postflight["placement"]["z_policy"],
            "details": {
                key: value
                for key, value in row.items()
                if key not in {"verified_digest", "writeback_error", "evidence"}
            },
        },
        model_entry=postflight["model"],
        execution_snapshot=preflight["execution_snapshot"],
        runtime_capability=_executed_runtime_capability(),
    )
    evidence = ledger_writes.publish_verification_evidence(
        loaded.asset_dir / "verification_evidence", payload
    )
    row["evidence"] = evidence
    prepared_writebacks.append(
        (
            row,
            loaded.ledger_path,
            ledger_writes.receipt_from_evidence(payload, evidence),
        )
    )

writeback_failures = 0
for row, lp, receipt in prepared_writebacks:
    try:
        ledger_writes.append_validated_verification(
            lp,
            row["model"],
            receipt,
            asset_key=row["asset"],
        )
    except (
        ledger_writes.LedgerWriteError,
        ledger_writes.EvidenceDigestError,
        ledger.VerificationConflictError,
    ) as exc:
        detail = (
            f"{exc.violations[0].path}:{exc.violations[0].code}"
            if isinstance(exc, ledger_writes.LedgerWriteError)
            else str(exc)
        )
        row["writeback_error"] = detail
        writeback_failures += 1
        print(f"FAIL backfill {row['asset']} m{row['model']}: {detail}")

# The aggregate report is written exactly once and is never named by a
# receipt.  Per-model immutable artifacts above are the trust-bearing record.
ledger._atomic_write_json(out, rows)

npass = sum(1 for r in rows if r["status"] == "pass")
print(f"SWEEP {npass}/{len(rows)} pass via RoboTwin create_actor")
sys.exit(
    0
    if npass == len(rows)
    and writeback_failures == 0
    and preparation_failures == 0
    and source_identity_failures == 0
    else 1
)
