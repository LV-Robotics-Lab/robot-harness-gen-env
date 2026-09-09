#!/usr/bin/env python3
"""Hash-bound staged-only RoboTwin/SAPIEN loader and physics smoke."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", os.fspath(root), *args),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def cas_payload(cas_root: Path, ref: dict[str, object]) -> tuple[Path, bytes]:
    digest = str(ref["sha256"])
    path = cas_root / "sha256" / digest[:2] / digest
    payload = path.read_bytes()
    if len(payload) != ref["bytes"] or sha256(payload) != digest:
        raise ValueError(f"CAS object is not exact: {digest}")
    return path, payload


def file_identity(path: Path) -> tuple[int, int, str]:
    payload = path.read_bytes()
    status = path.stat()
    return status.st_size, status.st_mtime_ns, sha256(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-result", type=Path, required=True)
    parser.add_argument("--cas-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--robotwin-root", type=Path, required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=900)
    args = parser.parse_args()
    if args.steps != 900:
        raise ValueError("this fixed probe requires exactly 900 steps per model")

    result_bytes = args.stage_result.read_bytes()
    result = json.loads(result_bytes)
    if result.get("schema_version") != "harness.asset_stage_result.v1":
        raise ValueError("unexpected stage result schema")
    binding_payload = {key: value for key, value in result.items() if key != "stage_binding_sha256"}
    if sha256(canonical_bytes(binding_payload)) != result.get("stage_binding_sha256"):
        raise ValueError("stage result binding is inconsistent")
    expected_flags = {
        "exact_source_bytes_staged": True,
        "loader_closure_enumerated": True,
        "simulator_executed": False,
        "runtime_qualification_executed": False,
        "promotion_executed": False,
        "authoritative_ledger_writes_performed": False,
    }
    if any(result.get(key) is not value for key, value in expected_flags.items()):
        raise ValueError("stage result has an unexpected authority boundary")

    input_payloads: dict[str, bytes] = {}
    for name in ("inventory_ref", "repair_plan_ref", "source_snapshot_manifest_ref"):
        _, input_payloads[name] = cas_payload(args.cas_root, result[name])
    inventory = json.loads(input_payloads["inventory_ref"])

    expected_pairs = {
        ("003_plate", 0),
        ("071_can", 0),
        ("071_can", 1),
        ("071_can", 2),
        ("071_can", 3),
        ("071_can", 5),
        ("071_can", 6),
    }
    pair_contracts: dict[tuple[str, int], dict[str, object]] = {}
    member_records: list[dict[str, object]] = []
    member_paths: set[str] = set()
    cas_before: dict[str, tuple[int, int, str]] = {}
    for asset in result["assets"]:
        members = {member["logical_path"]: member for member in asset["members"]}
        for member in members.values():
            ref = member["artifact_ref"]
            if (
                ref["sha256"] != member["source_sha256"]
                or ref["bytes"] != member["source_bytes"]
                or ref["schema_version"] is not None
            ):
                raise ValueError("stage member identity is inconsistent")
            cas_path, _ = cas_payload(args.cas_root, ref)
            cas_before[ref["sha256"]] = file_identity(cas_path)
            member_paths.add(member["logical_path"])
            member_records.append(
                {
                    "logical_path": member["logical_path"],
                    "sha256": ref["sha256"],
                    "bytes": ref["bytes"],
                    "media_type": ref["media_type"],
                }
            )
        for closure in asset["loader_closures"]:
            logical = Path(closure["root_logical_path"])
            if len(logical.parts) != 4 or logical.parts[0] != "objects":
                raise ValueError("unexpected loader root")
            pair = (logical.parts[1], closure["model_id"])
            contract = pair_contracts.setdefault(
                pair,
                {
                    "asset_id": asset["asset_id"],
                    "model_id": closure["model_id"],
                    "model_sidecar_logical_path": closure["model_sidecar_logical_path"],
                    "loader_scale": closure["loader_scale"],
                    "roles": {},
                },
            )
            if (
                contract["model_sidecar_logical_path"]
                != closure["model_sidecar_logical_path"]
                or contract["loader_scale"] != closure["loader_scale"]
            ):
                raise ValueError("representation closures disagree on their model sidecar")
            contract["roles"][closure["role"]] = members[closure["root_logical_path"]]
            if closure["model_sidecar_logical_path"] not in closure["member_logical_paths"]:
                raise ValueError("loader closure omits its model sidecar")
    if len(member_records) != 21 or len(member_paths) != 21:
        raise ValueError("expected exactly 21 unique staged members")
    if set(pair_contracts) != expected_pairs:
        raise ValueError("stage result does not enumerate the expected seven model pairs")
    incomplete_roles = any(
        set(contract["roles"]) != {"collision", "visual"}
        for contract in pair_contracts.values()
    )
    if incomplete_roles:
        raise ValueError("each runtime model must bind collision and visual roots")

    source_before: dict[str, tuple[int, int, str]] = {}
    for record in member_records:
        source_path = args.source_root / str(record["logical_path"])
        identity = file_identity(source_path)
        if identity[0] != record["bytes"] or identity[2] != record["sha256"]:
            raise ValueError("source member no longer matches its staged identity")
        source_before[str(record["logical_path"])] = identity

    ledger_before: dict[str, tuple[int, int, str]] = {}
    for entry in inventory["entries"]:
        if entry["asset_id"] not in {"robotwin_003_plate", "robotwin_071_can"}:
            continue
        ledger_path = args.feature_root / entry["ledger_path"]
        identity = file_identity(ledger_path)
        if identity[2] != entry["ledger_sha256"]:
            raise ValueError("inventory ledger identity does not match the worktree")
        ledger_before[entry["ledger_path"]] = identity
    if len(ledger_before) != 2:
        raise ValueError("expected two authoritative ledgers")

    implementation_paths = (
        "script/probe_staged_robotwin_assets.py",
        "self_improving/harness/asset_staging.py",
        "self_improving/harness/runtime_assets.py",
        "self_improving/harness/schemas/asset_staging.py",
        "tests/fixtures/asset_repair_tracer_source_snapshot.json",
    )
    implementation_files = [
        {
            "path": relative,
            "sha256": sha256((args.feature_root / relative).read_bytes()),
        }
        for relative in implementation_paths
    ]
    robotwin_commit = git_output(args.robotwin_root, "rev-parse", "HEAD")
    robotwin_status = git_output(args.robotwin_root, "status", "--porcelain=v1")
    loader_source = args.robotwin_root / "envs" / "utils" / "create_actor.py"

    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="p6-cas-robotwin-sidecar-smoke-") as name:
        runtime_root = Path(name)
        for record in member_records:
            digest = str(record["sha256"])
            source = args.cas_root / "sha256" / digest[:2] / digest
            destination = runtime_root / "assets" / str(record["logical_path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            identity = file_identity(destination)
            if identity[0] != record["bytes"] or identity[2] != digest:
                raise ValueError("runtime materialization changed staged bytes")

        previous_cwd = Path.cwd()
        try:
            os.chdir(args.robotwin_root)
            sys.path.insert(0, os.fspath(args.robotwin_root))
            import sapien
            from envs.utils.create_actor import create_actor

            for asset_name, model_id in sorted(expected_pairs):
                contract = pair_contracts[(asset_name, model_id)]
                asset_directory = (runtime_root / "assets" / "objects" / asset_name).resolve()
                scene = sapien.Scene()
                scene.set_timestep(0.01)
                scene.add_ground(0)
                actor = create_actor(
                    scene,
                    sapien.Pose([0.0, 0.0, 0.25]),
                    os.fspath(asset_directory),
                    convex=True,
                    is_static=False,
                    model_id=model_id,
                )
                if actor is None:
                    raise RuntimeError(f"create_actor returned None for {asset_name}/{model_id}")
                observed_scale = (
                    actor.config.get("scale") if isinstance(actor.config, dict) else None
                )
                if observed_scale != contract["loader_scale"]:
                    raise RuntimeError(
                        "create_actor did not consume staged sidecar scale for "
                        f"{asset_name}/{model_id}"
                    )
                entity = actor.actor
                initial_pose = entity.get_pose()
                initial = [float(value) for value in (*initial_pose.p, *initial_pose.q)]
                contact_step_count = 0
                contact_point_count = 0
                max_impulse = 0.0
                for _ in range(args.steps):
                    scene.step()
                    contacts = [
                        contact
                        for contact in scene.get_contacts()
                        if any(getattr(body, "entity", None) is entity for body in contact.bodies)
                    ]
                    if contacts:
                        contact_step_count += 1
                    for contact in contacts:
                        contact_point_count += len(contact.points)
                        for point in contact.points:
                            impulse = [float(value) for value in point.impulse]
                            max_impulse = max(
                                max_impulse,
                                math.sqrt(sum(value * value for value in impulse)),
                            )
                final_pose = entity.get_pose()
                final = [float(value) for value in (*final_pose.p, *final_pose.q)]
                if not all(math.isfinite(value) for value in (*initial, *final)):
                    raise RuntimeError("non-finite pose after SAPIEN steps")
                if contact_step_count == 0 or contact_point_count == 0 or max_impulse <= 0:
                    raise RuntimeError(f"no nonzero ground contact for {asset_name}/{model_id}")
                roles = contract["roles"]
                sidecar_path = str(contract["model_sidecar_logical_path"])
                sidecar_record = next(
                    record for record in member_records if record["logical_path"] == sidecar_path
                )
                rows.append(
                    {
                        "asset_id": contract["asset_id"],
                        "model_id": model_id,
                        "loader": "RoboTwin envs.utils.create_actor.create_actor",
                        "modelname_form": "absolute_staged_materialized_asset_directory",
                        "caller_supplied_scale": False,
                        "sidecar_logical_path": sidecar_path,
                        "sidecar_sha256": sidecar_record["sha256"],
                        "manifest_loader_scale": contract["loader_scale"],
                        "create_actor_observed_scale": observed_scale,
                        "collision_sha256": roles["collision"]["source_sha256"],
                        "visual_sha256": roles["visual"]["source_sha256"],
                        "scene_step_count": args.steps,
                        "initial_pose_pq": initial,
                        "final_pose_pq": final,
                        "finite_final_pose": True,
                        "contact_step_count": contact_step_count,
                        "contact_point_count": contact_point_count,
                        "max_contact_impulse_norm": max_impulse,
                    }
                )
                del actor, entity, scene
                gc.collect()
        finally:
            os.chdir(previous_cwd)

    cas_after = {
        digest: file_identity(args.cas_root / "sha256" / digest[:2] / digest)
        for digest in cas_before
    }
    source_after = {
        logical_path: file_identity(args.source_root / logical_path)
        for logical_path in source_before
    }
    ledger_after = {
        logical_path: file_identity(args.feature_root / logical_path)
        for logical_path in ledger_before
    }
    if cas_after != cas_before:
        raise RuntimeError("CAS inputs changed during runtime probe")
    if source_after != source_before:
        raise RuntimeError("source members changed during runtime probe")
    if ledger_after != ledger_before:
        raise RuntimeError("authoritative ledgers changed during runtime probe")

    report = {
        "schema_version": "asset_stage_real_robotwin_loader_smoke.v2",
        "feature_commit": git_output(args.feature_root, "rev-parse", "HEAD"),
        "implementation_files": implementation_files,
        "implementation_tree_sha256": sha256(canonical_bytes(implementation_files)),
        "stage_result_sha256": sha256(result_bytes),
        "stage_binding_sha256": result["stage_binding_sha256"],
        "inventory_ref": result["inventory_ref"],
        "repair_plan_ref": result["repair_plan_ref"],
        "source_snapshot_manifest_ref": result["source_snapshot_manifest_ref"],
        "runtime": {
            "python_version": sys.version.split()[0],
            "sapien_version": sapien.__version__,
            "robotwin_commit": robotwin_commit,
            "robotwin_worktree_dirty": bool(robotwin_status),
            "robotwin_status_sha256": sha256((robotwin_status + "\n").encode("utf-8")),
            "create_actor_source_sha256": sha256(loader_source.read_bytes()),
        },
        "staged_member_count": len(member_records),
        "staged_total_bytes": sum(int(record["bytes"]) for record in member_records),
        "member_tree_sha256": sha256(
            canonical_bytes(sorted(member_records, key=lambda row: str(row["logical_path"])))
        ),
        "model_variant_count": len(rows),
        "steps_per_variant": args.steps,
        "total_scene_steps": sum(int(row["scene_step_count"]) for row in rows),
        "models": rows,
        "real_robotwin_create_actor_executed": True,
        "sapien_headless_scene_stepped": True,
        "all_21_stage_members_rehashed": True,
        "all_7_sidecar_scales_observed": True,
        "all_7_variants_completed_900_steps": True,
        "all_final_poses_finite": True,
        "all_variants_observed_nonzero_ground_contact_impulse": True,
        "runtime_loader_input": "materialized_stage_cas_only",
        "cas_sources_unchanged": True,
        "target_source_members_unchanged": True,
        "authoritative_ledgers_unchanged": True,
        "source_asset_writes_performed": False,
        "authoritative_ledger_writes_performed": False,
        "can_on_plate_scene_executed": False,
        "settle_qualification_executed": False,
        "runtime_qualification_executed": False,
        "genesis_executed": False,
        "promotion_executed": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(canonical_bytes(report))
    print(
        f"PASS variants={len(rows)} members={len(member_records)} "
        f"steps={report['total_scene_steps']}"
    )


if __name__ == "__main__":
    main()
