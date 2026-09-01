#!/usr/bin/env python3
"""Settle repair for rescale casualties: multi-orientation retry + late-window
criterion, ledger-native.

Why the old criterion failed honestly-good assets: total xy-drift condemns
anything that ROLLS before stopping -- a real 2.5 cm cherry rolls; a 25 cm
rod rolls about its axis. What actually matters is whether it comes to REST:
the late-window displacement (same standard the import gate and the runtime
use). And a fail at the declared pose only proves that POSE, not the asset:
the calibration campaign's multi-start-orientation retry (441/534) applies
unchanged here.

On success: update the ledger's default stable pose to the measured rest
orientation (+z_policy from measured origin height), refresh dims to the
rest-pose bbox, and append a settle PASS. On exhaustion: append settle fail
(honest; the model stays excluded).
"""

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

DEV = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DEV / "1_asset_reuse"))

from lib import ledger as L  # noqa: E402
from lib import ledger_writes  # noqa: E402

RUN_ID = "settle-repair-20260816"
R2 = math.sqrt(0.5)
CANDIDATE_Q = [
    [1, 0, 0, 0],
    [R2, R2, 0, 0],
    [R2, -R2, 0, 0],
    [R2, 0, R2, 0],
    [R2, 0, -R2, 0],
]


class SourceIdentityError(ValueError):
    """The shadow object is not the model qualified in the library ledger."""


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
        raise SourceIdentityError(f"{field} cannot be resolved safely: {raw_path!r}") from exc


def _samefile(left, right):
    try:
        return Path(left).samefile(Path(right))
    except OSError:
        return False


def _require_shadow_asset_matches_library(shadow, loaded):
    shadow_asset = _existing_path(
        Path(shadow) / "assets" / "objects" / loaded.asset_key,
        field="shadow asset",
    )
    library_asset = _existing_path(loaded.asset_dir, field="authoritative library asset")
    if (
        not shadow_asset.is_dir()
        or not library_asset.is_dir()
        or not _samefile(shadow_asset, library_asset)
    ):
        raise SourceIdentityError(
            f"shadow asset is not the authoritative library asset tree: {shadow_asset}"
        )


def _sapien_representation_inputs(model):
    inputs = {}
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
        for file_index, member in enumerate(files):
            if not isinstance(member, dict):
                raise SourceIdentityError("SAPIEN representation files[] has a non-object member")
            if not L.artifact_file_record_is_current(member):
                raise SourceIdentityError(
                    f"SAPIEN representation file is stale, missing, or unsafe: "
                    f"{member.get('uri')!r}"
                )
            inputs[f"sapien_representation_{rep_index}_{file_index}"] = dict(member)
    if not inputs:
        raise SourceIdentityError("ledger model has no SAPIEN loader-visible representation")
    return inputs


def _repair_source_identity(shadow, loaded, model):
    violations = L.validate_ledger(loaded.document, check_files=True)
    if violations:
        first = violations[0]
        raise SourceIdentityError(f"authoritative ledger is invalid: {first.path}:{first.code}")
    _require_shadow_asset_matches_library(shadow, loaded)
    return {
        "asset_dir": str(loaded.asset_dir.resolve(strict=True)),
        "ledger_path": str(loaded.ledger_path.resolve(strict=True)),
        "digest": L.reps_digest(model, "sapien"),
        "inputs": _sapien_representation_inputs(model),
    }


def _same_source_identity(left, right):
    return (
        left["asset_dir"] == right["asset_dir"]
        and left["ledger_path"] == right["ledger_path"]
        and left["digest"] == right["digest"]
        and left["inputs"] == right["inputs"]
    )


def _bounds_min_z(bounds):
    if hasattr(bounds, "minimum"):
        return float(bounds.minimum[2])
    if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
        return float(bounds[0][2])
    array = __import__("numpy").asarray(bounds, dtype=float)
    if array.shape == (2, 3):
        return float(array[0, 2])
    raise SourceIdentityError("runtime actor exposes no canonical world AABB")


def _world_min_z(entity):
    """Return the physical support bound across every SAPIEN collision component."""

    component_getter = getattr(entity, "get_components", None)
    components = (
        component_getter() if callable(component_getter) else getattr(entity, "components", ())
    )
    collision_minima = []
    for component in components:
        shapes = getattr(component, "collision_shapes", None)
        if shapes is None:
            shapes_getter = getattr(component, "get_collision_shapes", None)
            shapes = shapes_getter() if callable(shapes_getter) else None
        tight_bounds = getattr(component, "compute_global_aabb_tight", None)
        if shapes and callable(tight_bounds):
            collision_minima.append(_bounds_min_z(tight_bounds()))
    if collision_minima:
        return min(collision_minima)

    # Some project-specific wrappers expose an already-unioned physical AABB.
    # Official SAPIEN 3 uses the component path above; this fallback keeps the
    # existing adapter surface without treating render-only bounds as support.
    legacy_getter = getattr(entity, "get_global_aabb", None)
    if callable(legacy_getter):
        return _bounds_min_z(legacy_getter())
    raise SourceIdentityError("runtime actor exposes no collision-component world AABB")


def try_settle(execution_root, runtime_root, asset_dir_name, model_id, q0):
    cwd = Path.cwd()
    os.chdir(execution_root)
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))
    try:
        import numpy as np
        import sapien as sapien_package
        import sapien.core as sapien
        from envs import utils as runtime_utils

        def capture_executed_runtime():
            return ledger_writes.capture_runtime_capability(
                loader_modules=[runtime_utils],
                sapien_module=sapien_package,
                entrypoint="RoboTwin envs.utils.create_actor",
                config={
                    "loader_root": "immutable_snapshot_cwd",
                    "timestep_s": 1 / 250,
                    "settle_steps": 1500,
                    "late_window_steps": 100,
                    "spawn_height_m": 0.30,
                    "convex": True,
                },
            )

        scene = sapien.Scene()
        scene.set_timestep(1 / 250)
        scene.add_ground(0.0)
        a = runtime_utils.create_actor(
            scene,
            pose=sapien.Pose([0, 0, 0.30], q0),
            modelname=asset_dir_name,
            model_id=model_id,
            convex=True,
        )
        if a is None:
            return {
                "measurement": None,
                "runtime_capability": capture_executed_runtime(),
                "result": {
                    "schema": "asset_settle_result.v2",
                    "finite": False,
                    "late_drift_m": None,
                    "support_z_m": None,
                    "tilt_deg": None,
                    "rest_orientation_wxyz": list(map(float, q0)),
                    "origin_z_m": None,
                    "derived_z_policy": None,
                    "details": {"reason": "create_actor returned None"},
                },
            }
        ent = a.actor if hasattr(a, "actor") else a
        for _ in range(1500):
            scene.step()
        p_before = np.array(ent.get_pose().p)
        for _ in range(100):
            scene.step()
        p_after = np.array(ent.get_pose().p)
        q_after = list(map(float, ent.get_pose().q))
        late = float(np.linalg.norm(p_after - p_before))
        finite = bool(np.isfinite(p_after).all() and np.isfinite(q_after).all())
        support_z = _world_min_z(ent) if finite else None
        tilt = math.degrees(2 * math.acos(min(1.0, max(-1.0, abs(q_after[0]))))) if finite else None
        origin_z = float(p_after[2]) if finite else None
        strict_result = {
            "schema": "asset_settle_result.v2",
            "finite": finite,
            "late_drift_m": late if finite else None,
            "support_z_m": support_z,
            "tilt_deg": tilt,
            "rest_orientation_wxyz": [round(value, 6) for value in q_after],
            "origin_z_m": origin_z,
            "derived_z_policy": L.z_policy_from_origin(origin_z) if finite else None,
            "details": {},
        }
        runtime_capability = capture_executed_runtime()
        if late > 0.002:
            return {
                "measurement": None,
                "runtime_capability": runtime_capability,
                "result": strict_result,
            }
        return {
            "measurement": {
                "orientation_wxyz": strict_result["rest_orientation_wxyz"],
                "origin_z": origin_z,
                "late_m": round(late, 5),
            },
            "runtime_capability": runtime_capability,
            "result": strict_result,
        }
    finally:
        os.chdir(cwd)


def main():
    shadow = DEV / "data" / "robotwin_shadow"
    lib = DEV / "data" / "asset_library"
    targets = sys.argv[1:] or ["333_cherry", "341_tool", "345_alarm", "356_ahead"]
    seen_models = set()
    for name in targets:
        try:
            loaded = L.load_asset_ledger(lib, name)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"SKIP write {name!r}: unsafe or invalid ledger ({exc})")
            continue
        name = loaded.asset_key
        lp = loaded.ledger_path
        led = loaded.document
        expected = json.loads(json.dumps(led))
        existing_violations = L.validate_ledger(led, check_files=True)
        if existing_violations:
            first = existing_violations[0]
            print(f"SKIP write {name}: {first.path}:{first.code}")
            continue
        for m in led["models"]:
            try:
                _asset, mid = L.claim_asset_model(seen_models, name, m["model_id"])
            except (
                L.UnsafeAssetKeyError,
                L.InvalidModelIdError,
                L.DuplicateAssetModelError,
            ) as exc:
                print(f"SKIP write {name}: {exc}")
                continue
            try:
                preflight = _repair_source_identity(shadow, loaded, m)
            except (OSError, ValueError, json.JSONDecodeError, SourceIdentityError) as exc:
                print(f"SKIP write {name} m{mid}: source identity mismatch: {exc}")
                continue
            model_data = loaded.asset_dir / f"model_data{mid}.json"
            execution_snapshot = ledger_writes.publish_execution_snapshot(
                loaded.asset_dir / "verification_inputs",
                source_asset_dir=loaded.asset_dir,
                asset_key=name,
                model_entry=m,
                asset_files={"model_metadata": model_data} if model_data.is_file() else {},
            )
            execution_root = Path(L.resolve_uri(execution_snapshot["root_uri"]))
            conv = m["physical"]["conventions"]
            declared = next((p for p in conv["stable_poses"] if p.get("is_default")), None)
            tried = ([declared["orientation_wxyz"]] if declared else []) + [q for q in CANDIDATE_Q]
            res = None
            physical_result = None
            runtime_capability = None
            for q0 in tried:
                attempt = try_settle(execution_root, shadow, name, mid, q0)
                res = attempt["measurement"]
                physical_result = attempt["result"]
                runtime_capability = attempt["runtime_capability"]
                if res:
                    break
            thresholds = {
                "max_late_drift_m": 0.002,
                "min_support_z_m": -0.005,
                "max_tilt_deg": 181.0,
            }
            verdict = L.qualified_verification_verdict(
                "sapien", "settle", thresholds, physical_result
            )
            try:
                current_loaded = L.load_asset_ledger(lib, name)
                current_model = next(
                    item for item in current_loaded.document["models"] if item["model_id"] == mid
                )
                postflight = _repair_source_identity(shadow, current_loaded, current_model)
                if not _same_source_identity(preflight, postflight):
                    raise SourceIdentityError(
                        "authoritative representation source changed during replay"
                    )
            except (OSError, ValueError, json.JSONDecodeError, SourceIdentityError) as exc:
                print(f"SKIP write {name} m{mid}: source identity mismatch after replay: {exc}")
                continue
            digest = postflight["digest"]
            timestamp = datetime.now().isoformat(timespec="seconds")
            if verdict == "pass" and res and declared:
                declared["orientation_wxyz"] = list(res["orientation_wxyz"])
                declared["pose_id"] = "measured_rest"
                declared["measured_against"] = {
                    "backend": "sapien",
                    "run_id": RUN_ID,
                    "note": "settle repair: multi-start retry, late-window rest",
                }
                # origin height at rest decides z_policy honestly
                conv["z_policy"] = physical_result["derived_z_policy"]
            evidence_payload = ledger_writes.issue_qualified_verification(
                issuer="asset.settle_repair.v1",
                asset_key=name,
                model_id=mid,
                run_id=RUN_ID,
                timestamp=timestamp,
                reps_digest=digest,
                inputs={
                    "task": {
                        "asset_key": name,
                        "model_id": mid,
                        "candidate_orientations_wxyz": tried,
                        "execution_root": execution_snapshot["root_uri"],
                    },
                },
                thresholds=thresholds,
                result=physical_result,
                model_entry=m,
                execution_snapshot=execution_snapshot,
                runtime_capability=runtime_capability,
            )
            evidence_record = ledger_writes.publish_verification_evidence(
                loaded.asset_dir / "verification_evidence", evidence_payload
            )
            m.setdefault("verification", []).append(
                ledger_writes.receipt_from_evidence(evidence_payload, evidence_record)
            )
            print(
                f"{name} m{mid}: {verdict}"
                + (
                    f" (late={res['late_m']}m, origin_z={res['origin_z']:.3f})"
                    if res
                    else " (all orientations still moving)"
                )
            )
        hard = L.validate_ledger(led, check_files=True)
        if hard:
            print(f"SKIP write {name}: {hard[0].path}:{hard[0].code}")
            continue
        ledger_writes.write_validated(lp, led, expected=expected)


if __name__ == "__main__":
    main()
