#!/usr/bin/env python3
"""Run a hash-bound, staged-only RoboTwin/SAPIEN loader and physics smoke."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(REPOSITORY_ROOT))

from self_improving.harness.artifacts import LocalArtifactStore  # noqa: E402
from self_improving.harness.asset_stage_verification import (  # noqa: E402
    RootedFileAttestation,
    VerifiedAssetStageAuthority,
    attest_rooted_regular_file,
    materialize_verified_asset_stage,
    read_rooted_regular_file,
    verify_asset_stage_result_authority,
)
from self_improving.harness.schemas.asset_staging import StagedAssetMember  # noqa: E402


@dataclass(frozen=True, slots=True)
class ModelProbeContract:
    asset_id: str
    asset_name: str
    model_id: int
    sidecar_logical_path: str
    loader_scale: tuple[float, float, float]
    sidecar: StagedAssetMember
    collision: StagedAssetMember
    visual: StagedAssetMember


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


def require_external_report_path(*, feature_root: Path, requested_path: Path) -> Path:
    """Resolve one new report path that cannot dirty the feature checkout."""

    if not isinstance(feature_root, Path) or not isinstance(requested_path, Path):
        raise TypeError("feature_root and requested_path must be Paths")
    resolved_feature_root = feature_root.expanduser().resolve(strict=True)
    if not resolved_feature_root.is_dir():
        raise ValueError("feature_root must identify a directory")
    if requested_path.is_symlink():
        raise ValueError("probe report must use a new output path, not a symlink")
    resolved_path = requested_path.expanduser().resolve(strict=False)
    if resolved_path == resolved_feature_root or resolved_feature_root in resolved_path.parents:
        raise ValueError("probe report must resolve outside feature_root")
    if not resolved_path.parent.is_dir():
        raise ValueError("probe report parent must already exist")
    if resolved_path.exists() or resolved_path.is_symlink():
        raise ValueError("probe report must use a new output path")
    return resolved_path


def write_new_report(path: Path, payload: bytes) -> None:
    """Publish one new report with no-follow/exclusive creation and complete writes."""

    file_descriptor: int | None = None
    created = False
    try:
        file_descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        created = True
        pending = memoryview(payload)
        while pending:
            written = os.write(file_descriptor, pending)
            if written <= 0:
                raise OSError("short report write")
            pending = pending[written:]
        os.fsync(file_descriptor)
    except OSError:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)


def git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", os.fspath(root), *args),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def attestation_record(value: RootedFileAttestation) -> dict[str, object]:
    return {
        "logical_path": value.logical_path,
        "sha256": value.sha256,
        "bytes": value.bytes,
    }


def model_probe_contracts(
    authority: VerifiedAssetStageAuthority,
) -> tuple[ModelProbeContract, ...]:
    contracts: list[ModelProbeContract] = []
    for asset in authority.result.assets:
        members = {member.logical_path: member for member in asset.members}
        source_asset = next(
            value for value in authority.source_manifest.assets if value.asset_id == asset.asset_id
        )
        asset_name = PurePosixPath(source_asset.logical_root).name
        closures: dict[int, dict[str, object]] = {}
        for closure in asset.loader_closures:
            value = closures.setdefault(
                closure.model_id,
                {
                    "sidecar": closure.model_sidecar_logical_path,
                    "scale": closure.loader_scale,
                    "roles": {},
                },
            )
            if (
                value["sidecar"] != closure.model_sidecar_logical_path
                or value["scale"] != closure.loader_scale
            ):
                raise RuntimeError("verified representation closures disagree")
            value["roles"][closure.role.value] = members[closure.root_logical_path]
        for model_id, value in closures.items():
            roles = value["roles"]
            if set(roles) != {"collision", "visual"}:
                raise RuntimeError("verified model does not have collision and visual roots")
            contracts.append(
                ModelProbeContract(
                    asset_id=asset.asset_id,
                    asset_name=asset_name,
                    model_id=model_id,
                    sidecar_logical_path=value["sidecar"],
                    loader_scale=value["scale"],
                    sidecar=members[value["sidecar"]],
                    collision=roles["collision"],
                    visual=roles["visual"],
                )
            )
    return tuple(sorted(contracts, key=lambda value: (value.asset_name, value.model_id)))


def attest_stage_members(
    *,
    root: Path,
    authority: VerifiedAssetStageAuthority,
    cas_layout: bool,
) -> dict[str, RootedFileAttestation]:
    values: dict[str, RootedFileAttestation] = {}
    for asset in authority.result.assets:
        for member in asset.members:
            logical_path = (
                f"sha256/{member.source_sha256[:2]}/{member.source_sha256}"
                if cas_layout
                else member.logical_path
            )
            attestation = attest_rooted_regular_file(root=root, logical_path=logical_path)
            if (
                attestation.sha256 != member.source_sha256
                or attestation.bytes != member.source_bytes
            ):
                raise RuntimeError(f"staged member identity drifted: {member.logical_path}")
            values[member.logical_path] = attestation
    return values


def attest_authoritative_ledgers(
    *,
    feature_root: Path,
    authority: VerifiedAssetStageAuthority,
) -> dict[str, RootedFileAttestation]:
    selected = set(authority.result.selected_asset_ids)
    values: dict[str, RootedFileAttestation] = {}
    for entry in authority.inventory.entries:
        if entry.asset_id not in selected:
            continue
        attestation = attest_rooted_regular_file(
            root=feature_root,
            logical_path=entry.ledger_path,
        )
        if attestation.sha256 != entry.ledger_sha256 or attestation.bytes != entry.ledger_bytes:
            raise RuntimeError(f"authoritative ledger identity drifted: {entry.asset_id}")
        values[entry.ledger_path] = attestation
    if len(values) != len(selected):
        raise RuntimeError("selected authoritative ledger closure is incomplete")
    return values


def loaded_robotwin_source_closure(
    *,
    robotwin_root: Path,
) -> tuple[tuple[str, RootedFileAttestation], ...]:
    paths: dict[str, set[str]] = {}
    absolute_root = robotwin_root.absolute()
    for module_name, module in tuple(sys.modules.items()):
        if not isinstance(module, ModuleType):
            continue
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            continue
        path = Path(module_file)
        if path.suffix == ".pyc":
            path = Path(os.fspath(path)[:-1])
        try:
            relative = path.absolute().relative_to(absolute_root).as_posix()
        except ValueError:
            continue
        if not relative.startswith("envs/") or not relative.endswith(".py"):
            continue
        paths.setdefault(relative, set()).add(module_name)
    required = {
        "envs/__init__.py",
        "envs/utils/__init__.py",
        "envs/utils/actor_utils.py",
        "envs/utils/create_actor.py",
        "envs/utils/transforms.py",
    }
    if not required.issubset(paths):
        raise RuntimeError("RoboTwin loader source closure is incomplete")
    rows = []
    for relative in sorted(paths):
        attestation = attest_rooted_regular_file(root=robotwin_root, logical_path=relative)
        rows.append((",".join(sorted(paths[relative])), attestation))
    return tuple(rows)


def loaded_package_file_closure(
    *,
    module_prefix: str,
    package_root: Path,
) -> tuple[tuple[str, RootedFileAttestation], ...]:
    paths: dict[str, set[str]] = {}
    absolute_root = package_root.absolute()
    for module_name, module in tuple(sys.modules.items()):
        if module_name != module_prefix and not module_name.startswith(module_prefix + "."):
            continue
        if not isinstance(module, ModuleType):
            continue
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            continue
        try:
            relative = Path(module_file).absolute().relative_to(absolute_root).as_posix()
        except ValueError:
            continue
        paths.setdefault(relative, set()).add(module_name)
    if not paths:
        raise RuntimeError(f"loaded {module_prefix} package closure is empty")
    return tuple(
        (
            ",".join(sorted(paths[relative])),
            attest_rooted_regular_file(root=package_root, logical_path=relative),
        )
        for relative in sorted(paths)
    )


def run_physics_probe(
    *,
    contracts: tuple[ModelProbeContract, ...],
    materialized_assets_root: Path,
    steps: int,
    sapien: object,
    create_actor: object,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for contract in contracts:
        asset_directory = (materialized_assets_root / "objects" / contract.asset_name).absolute()
        scene = sapien.Scene()
        scene.set_timestep(0.01)
        scene.add_ground(0)
        actor = create_actor(
            scene,
            sapien.Pose([0.0, 0.0, 0.25]),
            os.fspath(asset_directory),
            convex=True,
            is_static=False,
            model_id=contract.model_id,
        )
        if actor is None:
            raise RuntimeError(
                f"create_actor returned None for {contract.asset_name}/{contract.model_id}"
            )
        observed_scale = actor.config.get("scale") if isinstance(actor.config, dict) else None
        if observed_scale != list(contract.loader_scale):
            raise RuntimeError(
                "create_actor did not consume staged sidecar scale for "
                f"{contract.asset_name}/{contract.model_id}"
            )
        entity = actor.actor
        initial_pose = entity.get_pose()
        initial = [float(value) for value in (*initial_pose.p, *initial_pose.q)]
        contact_step_count = 0
        contact_point_count = 0
        max_impulse = 0.0
        for _ in range(steps):
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
            raise RuntimeError(
                f"no nonzero ground contact for {contract.asset_name}/{contract.model_id}"
            )
        rows.append(
            {
                "asset_id": contract.asset_id,
                "model_id": contract.model_id,
                "loader": "RoboTwin envs.utils.create_actor.create_actor",
                "modelname_form": "absolute_staged_materialized_asset_directory",
                "caller_supplied_scale": False,
                "sidecar_logical_path": contract.sidecar_logical_path,
                "sidecar_sha256": contract.sidecar.source_sha256,
                "manifest_loader_scale": contract.loader_scale,
                "create_actor_observed_scale": observed_scale,
                "collision_sha256": contract.collision.source_sha256,
                "visual_sha256": contract.visual.source_sha256,
                "scene_step_count": steps,
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
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-result", type=Path, required=True)
    parser.add_argument("--cas-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--robotwin-root", type=Path, required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=900)
    parser.add_argument("--expected-stage-result-sha256", required=True)
    parser.add_argument("--expected-stage-binding-sha256", required=True)
    parser.add_argument("--expected-inventory-sha256", required=True)
    parser.add_argument("--expected-repair-plan-sha256", required=True)
    parser.add_argument("--expected-source-manifest-sha256", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.steps != 900:
        raise ValueError("this fixed probe requires exactly 900 steps per model")
    if args.feature_root.absolute() != REPOSITORY_ROOT:
        raise ValueError("feature-root must identify the checkout containing this runner")
    output_path = require_external_report_path(
        feature_root=args.feature_root,
        requested_path=args.out,
    )
    feature_status_before = git_output(args.feature_root, "status", "--porcelain=v1")
    if feature_status_before:
        raise RuntimeError("feature checkout must be clean before the fixed probe")
    runner_source_before = attest_rooted_regular_file(
        root=args.feature_root,
        logical_path="script/probe_staged_robotwin_assets.py",
    )
    result_payload, result_attestation = read_rooted_regular_file(
        root=args.stage_result.absolute().parent,
        logical_path=args.stage_result.name,
    )
    if result_attestation.sha256 != args.expected_stage_result_sha256:
        raise ValueError("stage result file differs from the explicit probe trust input")
    probe_request = {
        "schema_version": "asset_stage_robotwin_probe_request.v1",
        "steps_per_variant": args.steps,
        "expected_stage_result_sha256": args.expected_stage_result_sha256,
        "expected_stage_binding_sha256": args.expected_stage_binding_sha256,
        "expected_inventory_sha256": args.expected_inventory_sha256,
        "expected_repair_plan_sha256": args.expected_repair_plan_sha256,
        "expected_source_manifest_sha256": args.expected_source_manifest_sha256,
    }
    store = LocalArtifactStore(args.cas_root)
    authority = verify_asset_stage_result_authority(
        artifact_store=store,
        result_payload=result_payload,
        expected_result_sha256=args.expected_stage_result_sha256,
        expected_stage_binding_sha256=args.expected_stage_binding_sha256,
        expected_inventory_sha256=args.expected_inventory_sha256,
        expected_repair_plan_sha256=args.expected_repair_plan_sha256,
        expected_source_snapshot_manifest_sha256=args.expected_source_manifest_sha256,
    )
    contracts = model_probe_contracts(authority)
    expected_pairs = {
        ("003_plate", 0),
        ("071_can", 0),
        ("071_can", 1),
        ("071_can", 2),
        ("071_can", 3),
        ("071_can", 5),
        ("071_can", 6),
    }
    if {(value.asset_name, value.model_id) for value in contracts} != expected_pairs:
        raise RuntimeError("verified authority does not contain the fixed seven model variants")

    source_before = attest_stage_members(
        root=args.source_root,
        authority=authority,
        cas_layout=False,
    )
    cas_before = attest_stage_members(
        root=store.root,
        authority=authority,
        cas_layout=True,
    )
    ledger_before = attest_authoritative_ledgers(
        feature_root=args.feature_root,
        authority=authority,
    )
    robotwin_commit = git_output(args.robotwin_root, "rev-parse", "HEAD")
    robotwin_status = git_output(args.robotwin_root, "status", "--porcelain=v1")

    with tempfile.TemporaryDirectory(prefix="p6-cas-robotwin-sidecar-smoke-") as name:
        runtime_root = Path(name)
        materialized = materialize_verified_asset_stage(
            artifact_store=store,
            authority=authority,
            destination_root=runtime_root / "assets",
        )
        if len(materialized) != 21:
            raise RuntimeError("materialized loader closure does not contain 21 members")
        previous_cwd = Path.cwd()
        try:
            os.chdir(args.robotwin_root)
            sys.path.insert(0, os.fspath(args.robotwin_root))
            import sapien
            from envs.utils.create_actor import create_actor

            loader_sources_before = loaded_robotwin_source_closure(
                robotwin_root=args.robotwin_root
            )
            rows = run_physics_probe(
                contracts=contracts,
                materialized_assets_root=runtime_root / "assets",
                steps=args.steps,
                sapien=sapien,
                create_actor=create_actor,
            )
            loader_sources_after = loaded_robotwin_source_closure(
                robotwin_root=args.robotwin_root
            )
            if loader_sources_after != loader_sources_before:
                raise RuntimeError("RoboTwin loader source closure changed during probe")
            sapien_root = Path(sapien.__file__).absolute().parent
            sapien_end_of_run_sources = loaded_package_file_closure(
                module_prefix="sapien",
                package_root=sapien_root,
            )
            if (
                loaded_package_file_closure(
                    module_prefix="sapien",
                    package_root=sapien_root,
                )
                != sapien_end_of_run_sources
            ):
                raise RuntimeError("loaded SAPIEN package closure changed during attestation")
        finally:
            os.chdir(previous_cwd)

    source_after = attest_stage_members(
        root=args.source_root,
        authority=authority,
        cas_layout=False,
    )
    cas_after = attest_stage_members(
        root=store.root,
        authority=authority,
        cas_layout=True,
    )
    ledger_after = attest_authoritative_ledgers(
        feature_root=args.feature_root,
        authority=authority,
    )
    if source_after != source_before:
        raise RuntimeError("target source members changed during runtime probe")
    if cas_after != cas_before:
        raise RuntimeError("CAS members changed during runtime probe")
    if ledger_after != ledger_before:
        raise RuntimeError("authoritative ledgers changed during runtime probe")

    member_records = [
        {
            **attestation_record(attestation),
            "media_type": member.artifact_ref.media_type,
        }
        for asset in authority.result.assets
        for member in asset.members
        for attestation in (source_before[member.logical_path],)
    ]
    loader_source_records = [
        {"modules": modules, **attestation_record(attestation)}
        for modules, attestation in loader_sources_before
    ]
    sapien_end_of_run_source_records = [
        {"modules": modules, **attestation_record(attestation)}
        for modules, attestation in sapien_end_of_run_sources
    ]
    python_executable = Path(sys.executable).resolve()
    python_attestation = attest_rooted_regular_file(
        root=python_executable.parent,
        logical_path=python_executable.name,
    )
    runner_source_after = attest_rooted_regular_file(
        root=args.feature_root,
        logical_path="script/probe_staged_robotwin_assets.py",
    )
    if runner_source_after != runner_source_before:
        raise RuntimeError("committed probe runner changed during execution")
    if git_output(args.feature_root, "status", "--porcelain=v1"):
        raise RuntimeError("feature checkout changed during the fixed probe")
    report = {
        "schema_version": "asset_stage_real_robotwin_loader_smoke.v2",
        "feature_commit": git_output(args.feature_root, "rev-parse", "HEAD"),
        "probe_request": probe_request,
        "probe_request_sha256": sha256(canonical_bytes(probe_request)),
        "stage_result_sha256": result_attestation.sha256,
        "stage_binding_sha256": authority.result.stage_binding_sha256,
        "inventory_ref": authority.result.inventory_ref.model_dump(mode="json"),
        "repair_plan_ref": authority.result.repair_plan_ref.model_dump(mode="json"),
        "source_snapshot_manifest_ref": (
            authority.result.source_snapshot_manifest_ref.model_dump(mode="json")
        ),
        "runtime": {
            "python_version": sys.version.split()[0],
            "python_executable_basename": python_executable.name,
            "python_executable_sha256": python_attestation.sha256,
            "python_executable_bytes": python_attestation.bytes,
            "sapien_version": sapien.__version__,
            "sapien_package_directory_name": sapien_root.name,
            "sapien_end_of_run_loaded_file_closure": sapien_end_of_run_source_records,
            "sapien_end_of_run_loaded_file_closure_sha256": sha256(
                canonical_bytes(sapien_end_of_run_source_records)
            ),
            "robotwin_commit": robotwin_commit,
            "robotwin_worktree_dirty": bool(robotwin_status),
            "robotwin_status_sha256": sha256((robotwin_status + "\n").encode("utf-8")),
            "loader_source_closure": loader_source_records,
            "loader_source_closure_sha256": sha256(canonical_bytes(loader_source_records)),
        },
        "runner_source": attestation_record(runner_source_before),
        "feature_worktree_clean_at_preflight_and_prepublication": True,
        "staged_member_count": len(member_records),
        "staged_total_bytes": sum(int(value["bytes"]) for value in member_records),
        "member_tree_sha256": sha256(canonical_bytes(member_records)),
        "model_variant_count": len(rows),
        "steps_per_variant": args.steps,
        "total_scene_steps": sum(int(row["scene_step_count"]) for row in rows),
        "models": rows,
        "deep_stage_authority_verified": True,
        "real_robotwin_create_actor_executed": True,
        "sapien_headless_scene_stepped": True,
        "all_21_stage_members_rehashed": True,
        "all_7_sidecar_scales_observed": True,
        "all_7_variants_completed_900_steps": True,
        "all_final_poses_finite": True,
        "all_variants_observed_nonzero_ground_contact_impulse": True,
        "runtime_loader_input": "contained_materialized_stage_cas_only",
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
    write_new_report(output_path, canonical_bytes(report))
    print(
        f"PASS variants={len(rows)} members={len(member_records)} "
        f"steps={report['total_scene_steps']}"
    )


if __name__ == "__main__":
    main()
