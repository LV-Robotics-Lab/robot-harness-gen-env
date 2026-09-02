from __future__ import annotations

import json
import math
import shutil
from dataclasses import fields, replace
from pathlib import Path

import pytest

import self_improving.validate_v2_snapshot as snapshot_module
from scene_gen.builder import build_scene_package, generated_module_source
from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.schema import RelationType, ResolvedSceneSpec
from scene_gen.solver import solve_scene
from scene_gen.support_geometry import footprint_2d
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.package_store import PackageStore
from self_improving.harness.runtime_assets import (
    RuntimeAssetStore,
    canonical_runtime_asset_manifest_bytes,
)
from self_improving.harness.schemas import ArtifactRef, EnvironmentPackage, ValidationStatus
from self_improving.validate_v2_snapshot import (
    SnapshotValidationError,
    SnapshotValidationRequest,
    ValidateV2SnapshotAdapter,
)

ROOT = Path(__file__).resolve().parents[2]


def _canonical_json_bytes(value: object) -> bytes:
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


def _put_json(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    schema_version: str,
    value: object,
) -> ArtifactRef:
    source = root / f"{name}.json"
    source.write_bytes(_canonical_json_bytes(value))
    return store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _put_raw(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    schema_version: str,
    payload: bytes,
) -> ArtifactRef:
    source = root / f"{name}.raw"
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _put_catalog(
    store: LocalArtifactStore,
    root: Path,
    catalog: AssetCatalog,
    *,
    trailing_newline: bool = False,
) -> ArtifactRef:
    source = root / (
        "asset_catalog_with_newline.json" if trailing_newline else "asset_catalog.json"
    )
    payload = _canonical_json_bytes(catalog.canonical_dict())
    source.write_bytes(payload if trailing_newline else payload[:-1])
    return store.put_file(
        source,
        name="asset_catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )


def _asset_fixture(
    tmp_path: Path,
    *,
    request_text: str = "Place a can on the table.",
    generated_provenance: bool = False,
) -> tuple[ResolvedSceneSpec, AssetCatalog, Path]:
    source_root = tmp_path / "private-source-root"
    robotwin_root = source_root / "RoboTwin"
    objects_root = robotwin_root / "assets" / "objects"
    base_catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    spec = parse_rule_based(request_text, seed=19)
    resolved = solve_scene(spec, base_catalog)
    selected = {(item.asset_id, item.model_id) for item in resolved.objects}
    entries = []
    sources_by_model: dict[tuple[str, int], tuple[str, ...]] = {}
    for entry in base_catalog.entries:
        selected_models = [
            model for model in entry.models if (entry.asset_id, model.model_id) in selected
        ]
        if not selected_models:
            continue
        asset_root = objects_root / entry.asset_id
        models = []
        for model in selected_models:
            model_id = model.model_id
            metadata = asset_root / f"model_data{model_id}.json"
            visual = asset_root / "visual" / f"base{model_id}.obj"
            collision = asset_root / "collision" / f"base{model_id}.obj"
            material = asset_root / "materials" / f"base{model_id}.mtl"
            texture = asset_root / "textures" / f"base{model_id}.png"
            payloads = {
                metadata: b'{"scale":[1,1,1]}\n',
                visual: f"mtllib ../materials/base{model_id}.mtl\n".encode(),
                collision: b"v 0 0 0\n",
                material: f"map_Kd ../textures/base{model_id}.png\n".encode(),
                texture: f"texture-{entry.asset_id}-{model_id}".encode(),
            }
            for path, payload in payloads.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            source_files = tuple(str(path) for path in (collision, metadata, visual))
            sources_by_model[(entry.asset_id, model_id)] = source_files
            models.append(
                model.model_copy(
                    update={
                        "model_path": str(asset_root),
                        "metadata_path": str(metadata),
                        "visual_path": str(visual),
                        "collision_path": str(collision),
                        "urdf_path": None,
                    }
                )
            )
        if generated_provenance:
            (asset_root / "generation_provenance.json").write_text("{}\n", encoding="utf-8")
        entries.append(
            entry.model_copy(
                update={
                    "asset_path": str(asset_root),
                    "models": tuple(models),
                    "source_notes": (
                        (*entry.source_notes, "procedural_generated")
                        if generated_provenance
                        else entry.source_notes
                    ),
                }
            )
        )
    catalog = AssetCatalog(
        robotwin_root=str(robotwin_root),
        objects_root=str(objects_root),
        entries=tuple(entries),
    )
    if generated_provenance:
        resolved = solve_scene(spec, catalog)
    else:
        resolved = resolved.model_copy(
            update={
                "asset_catalog_sha256": catalog.digest(),
                "objects": tuple(
                    item.model_copy(
                        update={"source_files": sources_by_model[(item.asset_id, item.model_id)]}
                    )
                    for item in resolved.objects
                ),
            }
        )
    return resolved, catalog, source_root


def _passing_runtime_evidence(
    resolved: ResolvedSceneSpec,
    *,
    snapshot_sha256: str,
) -> dict[str, object]:
    by_id = {item.object_id: item for item in resolved.objects}
    relation_evidence: dict[str, dict[str, object]] = {}
    for relation in resolved.relations:
        if relation.target == "table" or relation.relation in {
            RelationType.ON_TOP_OF,
            RelationType.INSIDE,
        }:
            continue
        source = by_id[relation.source]
        target = by_id[relation.target]
        source_footprint = footprint_2d(
            source.dimensions_m,
            source.pose.yaw_rad,
            source.footprint_shape,
        )
        target_footprint = footprint_2d(
            target.dimensions_m,
            target.pose.yaw_rad,
            target.footprint_shape,
        )
        dx = source.pose.position_m[0] - target.pose.position_m[0]
        dy = source.pose.position_m[1] - target.pose.position_m[1]
        distance = math.hypot(dx, dy)
        gap = 0.015
        if relation.relation == RelationType.LEFT_OF:
            passed = dx + source_footprint.half_x + gap <= -target_footprint.half_x
        elif relation.relation == RelationType.RIGHT_OF:
            passed = dx - source_footprint.half_x - gap >= target_footprint.half_x
        elif relation.relation == RelationType.FRONT_OF:
            passed = dy - source_footprint.half_y - gap >= target_footprint.half_y
        elif relation.relation == RelationType.BEHIND:
            passed = dy + source_footprint.half_y + gap <= -target_footprint.half_y
        elif relation.relation == RelationType.NEAR:
            passed = distance <= (relation.max_distance_m or 0.25)
        else:
            assert relation.relation == RelationType.DISTANCE_AT_LEAST
            passed = distance >= (relation.min_distance_m or 0.0)
        key = f"{relation.relation.value}:{relation.source}:{relation.target}"
        relation_evidence[key] = {
            "relation": relation.relation.value,
            "source": relation.source,
            "target": relation.target,
            "pass": passed,
            "center_delta_m": [dx, dy],
            "center_distance_m": distance,
            "source_half_extent_m": [
                source_footprint.half_x,
                source_footprint.half_y,
            ],
            "target_half_extent_m": [
                target_footprint.half_x,
                target_footprint.half_y,
            ],
        }
    return {
        "schema_version": "robotwin.scene_runtime_evidence.v2",
        "scene_id": resolved.scene_id,
        "seed": resolved.seed,
        "resolved_scene_sha256": resolved.digest(),
        "runtime_asset_snapshot_sha256": snapshot_sha256,
        "status": "pass",
        "robot_initial_collision_count": 0,
        "video_frame_count": 3,
        "unique_video_frame_count": 3,
        "base_simulation_step_count": 3,
        "simulation_step_count": 3,
        "settle_extra_steps": 0,
        "video_sample_step_indices": [0, 1, 2],
        "relations": relation_evidence,
        "objects": {
            item.object_id: {
                "translation_drift_m": 0.06,
                "rotation_drift_deg": 45.0,
                "resolved_translation_error_m": 0.06,
                "resolved_rotation_error_deg": 45.0,
                "penetration_count": 0,
                "still_moving": False,
                "support_contact": True,
                "support_contact_fraction": 1.0,
                "unexpected_contact_fraction": 0.0,
                "unexpected_contact_targets": [],
                "support_mode": (
                    "fixed_static_pose"
                    if item.is_static and item.support_relation.value == "on_table"
                    else f"{item.support_relation.value}_contact"
                ),
                "support_target": item.support_target,
                "support_footprint_margin_m": (
                    1.0 if item.support_relation.value == "on_top_of" else None
                ),
                "inside_contained": (True if item.support_relation.value == "inside" else None),
                "dropped": False,
                "visible_pixels": 512,
            }
            for item in resolved.objects
        },
    }


def _validation_request(
    tmp_path: Path,
    *,
    request_text: str = "Place a can on the table.",
    generated_provenance: bool = False,
) -> tuple[LocalArtifactStore, SnapshotValidationRequest, Path, Path]:
    resolved, catalog, source_root = _asset_fixture(
        tmp_path,
        request_text=request_text,
        generated_provenance=generated_provenance,
    )
    store = LocalArtifactStore(tmp_path / "cas")
    package_root = tmp_path / "package"
    spec = parse_rule_based(request_text, seed=19)
    build_scene_package(spec, resolved, package_root)
    published_package = PackageStore(store).publish(package_root)
    catalog_ref = _put_catalog(store, tmp_path, catalog)
    snapshot = RuntimeAssetStore(store).snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(source_root,),
    )
    runtime_ref = _put_json(
        store,
        tmp_path,
        name="runtime_evidence",
        schema_version="robotwin.scene_runtime_evidence.v2",
        value=_passing_runtime_evidence(
            resolved,
            snapshot_sha256=snapshot.manifest.sha256,
        ),
    )
    environment_package = EnvironmentPackage(
        package_id=resolved.digest(),
        route_id="text2env",
        producer_skill_ref="text2env.compile@1.0.0",
        seed=resolved.seed,
        scene_spec_sha256=spec.digest(),
        resolved_scene_sha256=resolved.digest(),
        asset_catalog=catalog_ref,
        package_manifest=published_package.manifest,
    )
    request = SnapshotValidationRequest(
        environment_package=environment_package,
        runtime_evidence=runtime_ref,
        runtime_asset_snapshot_manifest=snapshot.manifest,
    )
    return store, request, source_root, package_root


def _replace_request_ref(
    request: SnapshotValidationRequest,
    field: str,
    ref: ArtifactRef,
) -> SnapshotValidationRequest:
    if field in {"asset_catalog", "package_manifest"}:
        return replace(
            request,
            environment_package=request.environment_package.model_copy(update={field: ref}),
        )
    return replace(request, **{field: ref})


def _replace_package_member(
    store: LocalArtifactStore,
    tmp_path: Path,
    request: SnapshotValidationRequest,
    *,
    path: str,
    payload: bytes,
) -> SnapshotValidationRequest:
    source = tmp_path / f"attacked-{Path(path).name}"
    source.write_bytes(payload)
    media_type, schema_version = {
        "request.txt": ("text/plain", None),
        "generated_scene.py": ("text/x-python", None),
    }[path]
    member_ref = store.put_file(
        source,
        name=Path(path).stem,
        media_type=media_type,
        schema_version=schema_version,
    )
    manifest = json.loads(
        store.resolve(request.environment_package.package_manifest).path.read_bytes()
    )
    record = next(value for value in manifest["files"] if value["path"] == path)
    record["sha256"] = member_ref.sha256
    record["bytes"] = member_ref.bytes
    manifest_ref = _put_json(
        store,
        tmp_path,
        name=f"package_manifest_attacked_{Path(path).stem}",
        schema_version="robotwin.generated_scene_package.v1",
        value=manifest,
    )
    return _replace_request_ref(request, "package_manifest", manifest_ref)


def _coordinated_resolved_semantic_attack(
    store: LocalArtifactStore,
    tmp_path: Path,
    request: SnapshotValidationRequest,
    *,
    forge_object_id: bool,
) -> SnapshotValidationRequest:
    manifest = json.loads(
        store.resolve(request.environment_package.package_manifest).path.read_bytes()
    )
    resolved_record = next(
        value for value in manifest["files"] if value["path"] == "resolved_scene.json"
    )
    resolved = ResolvedSceneSpec.model_validate_json(
        _cas_object_path(store, resolved_record["sha256"]).read_bytes()
    )
    old_id = resolved.objects[0].object_id
    forged_id = "forged_object" if forge_object_id else old_id
    forged = resolved.model_copy(
        update={
            "objects": tuple(
                item.model_copy(
                    update=(
                        {"object_id": forged_id} if forge_object_id else {"color": "forged_color"}
                    )
                )
                if item.object_id == old_id
                else item
                for item in resolved.objects
            ),
            "relations": tuple(
                relation.model_copy(
                    update={
                        "source": forged_id if relation.source == old_id else relation.source,
                        "target": forged_id if relation.target == old_id else relation.target,
                    }
                )
                for relation in resolved.relations
            ),
            "solver_trace": resolved.solver_trace.model_copy(
                update={
                    "attempts": tuple(
                        attempt.model_copy(update={"object_id": forged_id})
                        if attempt.object_id == old_id
                        else attempt
                        for attempt in resolved.solver_trace.attempts
                    )
                }
            ),
        }
    )
    resolved_payload = _canonical_json_bytes(forged.canonical_dict())
    generated_payload = generated_module_source(forged).encode("utf-8")
    replacement_payloads = {
        "resolved_scene.json": (resolved_payload, "application/json"),
        "generated_scene.py": (generated_payload, "text/x-python"),
    }
    for path, (payload, media_type) in replacement_payloads.items():
        source = tmp_path / f"forged-{Path(path).name}"
        source.write_bytes(payload)
        member_ref = store.put_file(
            source,
            name=Path(path).stem,
            media_type=media_type,
            schema_version=("robotwin.resolved_scene.v1" if path.endswith(".json") else None),
        )
        record = next(value for value in manifest["files"] if value["path"] == path)
        record["sha256"] = member_ref.sha256
        record["bytes"] = member_ref.bytes
    manifest["resolved_scene_sha256"] = forged.digest()
    manifest_ref = _put_json(
        store,
        tmp_path,
        name="forged_package_manifest",
        schema_version="robotwin.generated_scene_package.v1",
        value=manifest,
    )
    environment_package = request.environment_package.model_copy(
        update={
            "package_id": forged.digest(),
            "resolved_scene_sha256": forged.digest(),
            "package_manifest": manifest_ref,
        }
    )
    snapshot_document = json.loads(
        store.resolve(request.runtime_asset_snapshot_manifest).path.read_bytes()
    )
    snapshot_document["resolved_scene_sha256"] = forged.digest()
    snapshot_ref = _put_raw(
        store,
        tmp_path,
        name="forged_runtime_asset_snapshot",
        schema_version="harness.runtime_asset_snapshot.v1",
        payload=canonical_runtime_asset_manifest_bytes(snapshot_document),
    )
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    runtime["resolved_scene_sha256"] = forged.digest()
    runtime["runtime_asset_snapshot_sha256"] = snapshot_ref.sha256
    if forge_object_id:
        runtime["objects"][forged_id] = runtime["objects"].pop(old_id)
    runtime_ref = _put_json(
        store,
        tmp_path,
        name="forged_runtime_evidence",
        schema_version="robotwin.scene_runtime_evidence.v2",
        value=runtime,
    )
    return SnapshotValidationRequest(
        environment_package=environment_package,
        runtime_evidence=runtime_ref,
        runtime_asset_snapshot_manifest=snapshot_ref,
    )


def _cas_object_names(store: LocalArtifactStore) -> frozenset[str]:
    root = store.root / "sha256"
    return frozenset(path.name for path in root.glob("*/*") if path.is_file())


def _cas_object_path(store: LocalArtifactStore, sha256: str) -> Path:
    return store.root / "sha256" / sha256[:2] / sha256


def _assert_recompute_error(
    tmp_path: Path,
    store: LocalArtifactStore,
    request: SnapshotValidationRequest,
    *,
    reason: str,
) -> None:
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir(exist_ok=True)
    before = _cas_object_names(store)
    with pytest.raises(SnapshotValidationError) as error:
        ValidateV2SnapshotAdapter(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).recompute(request)
    assert error.value.reason == reason
    assert _cas_object_names(store) == before
    assert list(scratch_parent.iterdir()) == []


def test_recompute_uses_only_cas_snapshot_and_returns_path_free_canonical_report(
    tmp_path: Path,
) -> None:
    store, request, source_root, package_root = _validation_request(tmp_path)
    shutil.rmtree(source_root)
    shutil.rmtree(package_root)
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()

    result = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).recompute(request)

    assert result.validation_status is ValidationStatus.PASS
    assert result.fail_count == 0
    assert result.not_run_count == 0
    report_payload = store.resolve(result.validation_report).path.read_bytes()
    report = json.loads(report_payload)
    assert report_payload == _canonical_json_bytes(report)
    assert set(report) == {
        "schema_version",
        "scene_id",
        "resolved_scene_sha256",
        "status",
        "fail_count",
        "not_run_count",
        "checks",
    }
    assert report["status"] == "pass"
    assert report["resolved_scene_sha256"] == request.environment_package.resolved_scene_sha256
    source_check = next(
        check for check in report["checks"] if check["name"].startswith("real_asset_files:")
    )
    assert source_check["status"] == "pass"
    assert set(source_check["evidence"]) == {
        "asset_id",
        "model_id",
        "required_files",
        "runtime_asset_snapshot_sha256",
        "tree_sha256",
    }
    binding_check = next(
        check for check in report["checks"] if check["name"] == "snapshot_validation_binding"
    )
    assert binding_check == {
        "name": "snapshot_validation_binding",
        "status": "pass",
        "evidence": {
            "asset_catalog_sha256": request.environment_package.asset_catalog.sha256,
            "environment_package_id": request.environment_package.package_id,
            "gate_profile": request.gate_profile,
            "package_manifest_sha256": request.environment_package.package_manifest.sha256,
            "runtime_asset_snapshot_sha256": request.runtime_asset_snapshot_manifest.sha256,
            "runtime_evidence_sha256": request.runtime_evidence.sha256,
        },
    }
    assert str(tmp_path) not in report_payload.decode("utf-8")
    assert "/opt/robotwin-fixture" not in report_payload.decode("utf-8")
    assert list(scratch_parent.iterdir()) == []


def test_recompute_rejects_catalog_ref_whose_bytes_are_not_its_semantic_digest(
    tmp_path: Path,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    catalog = AssetCatalog.model_validate_json(
        store.resolve(request.environment_package.asset_catalog).path.read_bytes()
    )
    noncanonical_ref = _put_catalog(
        store,
        tmp_path,
        catalog,
        trailing_newline=True,
    )
    attacked = replace(
        request,
        environment_package=request.environment_package.model_copy(
            update={"asset_catalog": noncanonical_ref}
        ),
    )
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()

    with pytest.raises(SnapshotValidationError) as error:
        ValidateV2SnapshotAdapter(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).recompute(attacked)

    assert error.value.reason == "package_binding"
    assert list(scratch_parent.iterdir()) == []


def test_report_binding_uses_cas_manifest_bytes_not_post_verify_scratch_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    original_manifest = json.loads(
        store.resolve(request.runtime_asset_snapshot_manifest).path.read_bytes()
    )
    original_tree_sha256 = original_manifest["assets"][0]["tree_sha256"]
    real_verify = snapshot_module.verify_runtime_asset_snapshot

    def verify_then_mutate(**kwargs: object):
        verified = real_verify(**kwargs)
        manifest_path = Path(kwargs["manifest_path"])
        attacked = json.loads(manifest_path.read_bytes())
        attacked["assets"][0]["tree_sha256"] = "f" * 64
        manifest_path.chmod(0o644)
        manifest_path.write_bytes(canonical_runtime_asset_manifest_bytes(attacked))
        return verified

    monkeypatch.setattr(snapshot_module, "verify_runtime_asset_snapshot", verify_then_mutate)
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()

    result = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).recompute(request)

    report = json.loads(store.resolve(result.validation_report).path.read_bytes())
    source_check = next(
        check for check in report["checks"] if check["name"].startswith("real_asset_files:")
    )
    assert source_check["evidence"]["tree_sha256"] == original_tree_sha256
    assert list(scratch_parent.iterdir()) == []


def test_adapter_rejects_scratch_parent_beneath_a_symlink_ancestor(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    scratch_parent = real_parent / "scratch"
    scratch_parent.mkdir(parents=True)
    symlink_parent = tmp_path / "symlink-parent"
    symlink_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(SnapshotValidationError) as error:
        ValidateV2SnapshotAdapter(
            artifact_store=LocalArtifactStore(tmp_path / "cas"),
            scratch_parent=symlink_parent / "scratch",
        )

    assert error.value.reason == "scratch_unsafe"


def test_adapter_rejects_invalid_constructor_arguments(tmp_path: Path) -> None:
    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()
    store = LocalArtifactStore(tmp_path / "cas")

    with pytest.raises(TypeError, match="artifact_store"):
        ValidateV2SnapshotAdapter(
            artifact_store=object(),  # type: ignore[arg-type]
            scratch_parent=scratch_parent,
        )
    with pytest.raises(TypeError, match="scratch_parent"):
        ValidateV2SnapshotAdapter(
            artifact_store=store,
            scratch_parent=str(scratch_parent),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_adapter_rejects_unavailable_or_nondirectory_scratch_parent(
    tmp_path: Path,
    kind: str,
) -> None:
    scratch_parent = tmp_path / "scratch"
    if kind == "file":
        scratch_parent.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SnapshotValidationError) as error:
        ValidateV2SnapshotAdapter(
            artifact_store=LocalArtifactStore(tmp_path / "cas"),
            scratch_parent=scratch_parent,
        )

    assert error.value.reason == ("scratch_unavailable" if kind == "missing" else "scratch_unsafe")


def test_adapter_maps_scratch_resolution_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()

    def unavailable(*_args: object, **_kwargs: object) -> Path:
        raise OSError("resolution unavailable")

    monkeypatch.setattr(Path, "resolve", unavailable)
    with pytest.raises(SnapshotValidationError) as error:
        ValidateV2SnapshotAdapter(
            artifact_store=store,
            scratch_parent=scratch_parent,
        )

    assert error.value.reason == "scratch_unavailable"


def test_adapter_maps_scratch_symlink_loop_to_unsafe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    scratch_parent = tmp_path / "scratch-loop"
    scratch_parent.mkdir()

    def symlink_loop(*_args: object, **_kwargs: object) -> Path:
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(Path, "resolve", symlink_loop)

    with pytest.raises(SnapshotValidationError) as error:
        ValidateV2SnapshotAdapter(
            artifact_store=store,
            scratch_parent=scratch_parent,
        )

    assert error.value.reason == "scratch_unsafe"


def test_recompute_maps_scratch_allocation_failure_without_cleanup_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()

    def unavailable(*_args: object, **_kwargs: object) -> str:
        raise OSError("scratch full")

    monkeypatch.setattr(snapshot_module.tempfile, "mkdtemp", unavailable)
    with pytest.raises(SnapshotValidationError) as error:
        ValidateV2SnapshotAdapter(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).recompute(request)

    assert error.value.reason == "scratch_unavailable"
    assert list(scratch_parent.iterdir()) == []


def test_recompute_rejects_wrong_request_type_and_gate_profile(tmp_path: Path) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()
    adapter = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=scratch_parent,
    )

    with pytest.raises(TypeError, match="request"):
        adapter.recompute(object())  # type: ignore[arg-type]
    with pytest.raises(SnapshotValidationError) as error:
        adapter.recompute(replace(request, gate_profile="unsupported"))

    assert error.value.reason == "gate_profile_unsupported"
    assert list(scratch_parent.iterdir()) == []


def test_recompute_rejects_untyped_nested_request(tmp_path: Path) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    attacked = replace(request, environment_package=None)  # type: ignore[arg-type]

    _assert_recompute_error(tmp_path, store, attacked, reason="invalid_request")


def test_validator_never_rereads_materialized_package_as_an_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_without_package_path(*args: object, **kwargs: object):
        assert kwargs["package_root"] is None
        return real_validate(*args, **kwargs)

    monkeypatch.setattr(snapshot_module, "validate_resolved_scene", validate_without_package_path)
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()

    result = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).recompute(request)

    report = json.loads(store.resolve(result.validation_report).path.read_bytes())
    package_check = next(check for check in report["checks"] if check["name"] == "package_manifest")
    assert package_check["status"] == "pass"
    assert [item["path"] for item in package_check["evidence"]["checks"]] == [
        "request.txt",
        "scene_spec.json",
        "resolved_scene.json",
        "generated_scene.py",
        "resolved_scene.json#canonical_digest",
    ]
    assert list(scratch_parent.iterdir()) == []


def test_recompute_rejects_verifier_returning_another_snapshot_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_verify = snapshot_module.verify_runtime_asset_snapshot

    def verify_with_wrong_identity(**kwargs: object):
        return replace(real_verify(**kwargs), manifest_sha256="f" * 64)

    monkeypatch.setattr(
        snapshot_module,
        "verify_runtime_asset_snapshot",
        verify_with_wrong_identity,
    )

    _assert_recompute_error(tmp_path, store, request, reason="snapshot_binding")


def test_recompute_wraps_unexpected_validator_error_and_cleans_scratch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)

    def invalid_validator(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AttributeError("invalid runtime container")

    monkeypatch.setattr(snapshot_module, "validate_resolved_scene", invalid_validator)

    _assert_recompute_error(tmp_path, store, request, reason="recompute_failed")


def test_recompute_rejects_report_beyond_fixed_byte_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    monkeypatch.setattr(snapshot_module, "_MAX_REPORT_BYTES", 1)

    _assert_recompute_error(tmp_path, store, request, reason="report_too_large")


def test_recompute_requires_exact_report_cas_reread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()
    adapter = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=scratch_parent,
    )
    adapter.recompute(request)
    before = _cas_object_names(store)
    real_read = snapshot_module._read_cas_ref

    def mismatched_report_read(
        artifact_store: LocalArtifactStore,
        ref: ArtifactRef,
        *,
        limit: int,
    ) -> bytes:
        payload = real_read(artifact_store, ref, limit=limit)
        if ref.schema_version == "robotwin.scene_validation.v1":
            return payload + b" "
        return payload

    monkeypatch.setattr(snapshot_module, "_read_cas_ref", mismatched_report_read)

    with pytest.raises(SnapshotValidationError) as error:
        adapter.recompute(request)

    assert error.value.reason == "report_mismatch"
    assert _cas_object_names(store) == before
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.parametrize(
    "field",
    [
        "asset_catalog",
        "package_manifest",
        "runtime_evidence",
        "runtime_asset_snapshot_manifest",
    ],
)
def test_recompute_rejects_every_non_cas_input_reference(
    tmp_path: Path,
    field: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    original = (
        getattr(request.environment_package, field)
        if field in {"asset_catalog", "package_manifest"}
        else getattr(request, field)
    )
    attacked_ref = original.model_copy(update={"uri": "/tmp/not-a-cas-authority"})

    _assert_recompute_error(
        tmp_path,
        store,
        _replace_request_ref(request, field, attacked_ref),
        reason="invalid_reference",
    )


@pytest.mark.parametrize(
    ("declared_bytes", "reason"),
    [
        (16 * 1024 * 1024 + 1, "artifact_too_large"),
        (1, "artifact_mismatch"),
    ],
)
def test_recompute_enforces_runtime_cas_size_identity_without_echoing_ref_name(
    tmp_path: Path,
    declared_bytes: int,
    reason: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    attacked_ref = request.runtime_evidence.model_copy(
        update={
            "name": "FILE:/private/operator/secret",
            "bytes": declared_bytes,
        }
    )
    attacked = _replace_request_ref(request, "runtime_evidence", attacked_ref)
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()
    before = _cas_object_names(store)

    with pytest.raises(SnapshotValidationError) as error:
        ValidateV2SnapshotAdapter(
            artifact_store=store,
            scratch_parent=scratch_parent,
        ).recompute(attacked)

    assert error.value.reason == reason
    assert "private" not in str(error.value).casefold()
    assert _cas_object_names(store) == before
    assert list(scratch_parent.iterdir()) == []


def test_recompute_rejects_empty_json_cas_object(tmp_path: Path) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    empty = _put_raw(
        store,
        tmp_path,
        name="empty_runtime",
        schema_version="robotwin.scene_runtime_evidence.v2",
        payload=b"",
    )
    attacked = _replace_request_ref(request, "runtime_evidence", empty)

    _assert_recompute_error(tmp_path, store, attacked, reason="json_invalid")


def test_recompute_rejects_premature_cas_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    monkeypatch.setattr(snapshot_module.os, "read", lambda *_args: b"")

    _assert_recompute_error(tmp_path, store, request, reason="artifact_mismatch")


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("package", "package_binding"),
        ("resolved", "package_binding"),
        ("catalog", "package_binding"),
        ("manifest", "package_binding"),
        ("runtime_snapshot", "runtime_binding"),
    ],
)
def test_recompute_rejects_cross_artifact_binding_mismatches(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    if attack == "package":
        attacked = replace(
            request,
            environment_package=request.environment_package.model_copy(
                update={"scene_spec_sha256": "f" * 64}
            ),
        )
    elif attack == "resolved":
        attacked = replace(
            request,
            environment_package=request.environment_package.model_copy(
                update={
                    "package_id": "f" * 64,
                    "resolved_scene_sha256": "f" * 64,
                }
            ),
        )
    elif attack == "catalog":
        catalog = AssetCatalog.model_validate_json(
            store.resolve(request.environment_package.asset_catalog).path.read_bytes()
        )
        changed_catalog = catalog.model_copy(update={"robotwin_root": "/different/root"})
        attacked = _replace_request_ref(
            request,
            "asset_catalog",
            _put_catalog(store, tmp_path, changed_catalog),
        )
    elif attack == "manifest":
        manifest = json.loads(
            store.resolve(request.environment_package.package_manifest).path.read_bytes()
        )
        manifest["scene_id"] = "different-scene"
        attacked = _replace_request_ref(
            request,
            "package_manifest",
            _put_json(
                store,
                tmp_path,
                name="changed_package_manifest",
                schema_version="robotwin.generated_scene_package.v1",
                value=manifest,
            ),
        )
    else:
        runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
        runtime["runtime_asset_snapshot_sha256"] = "f" * 64
        attacked = _replace_request_ref(
            request,
            "runtime_evidence",
            _put_json(
                store,
                tmp_path,
                name="changed_runtime_evidence",
                schema_version="robotwin.scene_runtime_evidence.v2",
                value=runtime,
            ),
        )

    _assert_recompute_error(tmp_path, store, attacked, reason=reason)


@pytest.mark.parametrize(
    ("member_kind", "mutation", "reason"),
    [
        ("package", "missing", "artifact_unavailable"),
        ("package", "corrupt", "artifact_mismatch"),
        ("package", "swapped", "artifact_unsafe"),
        ("snapshot", "missing", "runtime_asset_snapshot_invalid"),
        ("snapshot", "corrupt", "runtime_asset_snapshot_invalid"),
        ("snapshot", "swapped", "runtime_asset_snapshot_invalid"),
    ],
)
def test_recompute_rejects_missing_corrupt_and_swapped_nested_cas_members(
    tmp_path: Path,
    member_kind: str,
    mutation: str,
    reason: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    if member_kind == "package":
        manifest = json.loads(
            store.resolve(request.environment_package.package_manifest).path.read_bytes()
        )
        member = manifest["files"][0]
    else:
        manifest = json.loads(
            store.resolve(request.runtime_asset_snapshot_manifest).path.read_bytes()
        )
        member = manifest["assets"][0]["files"][0]
    target = _cas_object_path(store, member["sha256"])
    if mutation == "missing":
        target.unlink()
    elif mutation == "corrupt":
        payload = target.read_bytes()
        target.chmod(0o600)
        target.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    else:
        replacement = next(
            path
            for path in (store.root / "sha256").glob("*/*")
            if path.is_file() and path.name != member["sha256"]
        )
        target.unlink()
        target.symlink_to(replacement)

    _assert_recompute_error(tmp_path, store, request, reason=reason)


def test_recompute_rejects_json_beyond_the_fixed_nesting_depth(tmp_path: Path) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    nested: object = 0
    for _ in range(65):
        nested = [nested]
    runtime["ignored_but_bounded"] = nested
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name="overdeep_runtime_evidence",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(tmp_path, store, attacked, reason="json_invalid")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":"robotwin.scene_runtime_evidence.v2","schema_version":"duplicate"}',
        b'{"schema_version":NaN}',
        b'{"schema_version":"robotwin.scene_runtime_evidence.v2","overflow":1e999}',
        b"[]",
        b"{",
        b"\xff",
    ],
)
def test_recompute_rejects_non_strict_runtime_json(tmp_path: Path, payload: bytes) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_raw(
            store,
            tmp_path,
            name="invalid_runtime_evidence",
            schema_version="robotwin.scene_runtime_evidence.v2",
            payload=payload,
        ),
    )

    _assert_recompute_error(tmp_path, store, attacked, reason="json_invalid")


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value))
    return set()


def test_recompute_is_deterministic_across_scratch_roots_and_makes_no_decision(
    tmp_path: Path,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    first_scratch = tmp_path / "first-scratch"
    second_scratch = tmp_path / "second-scratch"
    first_scratch.mkdir()
    second_scratch.mkdir()

    first = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=first_scratch,
    ).recompute(request)
    second = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=second_scratch,
    ).recompute(request)

    first_payload = store.resolve(first.validation_report).path.read_bytes()
    second_payload = store.resolve(second.validation_report).path.read_bytes()
    report = json.loads(first_payload)
    assert first.validation_report.sha256 == second.validation_report.sha256
    assert first.validation_report.bytes == second.validation_report.bytes
    assert first_payload == second_payload
    assert {field.name for field in fields(first)} == {
        "validation_report",
        "validation_status",
        "fail_count",
        "not_run_count",
    }
    assert "publishable" not in _all_keys(report)
    assert "decision" not in _all_keys(report)
    assert list(first_scratch.iterdir()) == []
    assert list(second_scratch.iterdir()) == []


def test_physical_failure_is_a_path_free_report_not_an_authority_error(tmp_path: Path) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    runtime["status"] = "fail"
    runtime["error"] = "/private/runtime/path/must-not-leak"
    failed_request = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name="failed_runtime_evidence",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()

    result = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).recompute(failed_request)

    payload = store.resolve(result.validation_report).path.read_bytes()
    report = json.loads(payload)
    assert result.validation_status is ValidationStatus.FAIL
    assert result.fail_count == 1
    assert result.not_run_count == 0
    assert b"/private/runtime/path" not in payload
    assert next(check for check in report["checks"] if check["name"] == "runtime_status")[
        "evidence"
    ] == {"reported_status": "fail"}
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.parametrize(
    "attack",
    [
        "extra_field",
        "wrong_schema",
        "wrong_members",
        "bad_member_identity",
        "boolean_seed",
        "non_text_scene_id",
        "non_text_digest",
        "non_text_compiler",
    ],
)
def test_package_manifest_requires_exact_shape_members_and_scalar_types(
    tmp_path: Path,
    attack: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    manifest = json.loads(
        store.resolve(request.environment_package.package_manifest).path.read_bytes()
    )
    if attack == "extra_field":
        manifest["unexpected"] = None
    elif attack == "wrong_schema":
        manifest["schema_version"] = "wrong.package.v1"
    elif attack == "wrong_members":
        manifest["files"] = manifest["files"][:-1]
    elif attack == "bad_member_identity":
        manifest["files"][0]["sha256"] = "g" * 64
    elif attack == "boolean_seed":
        manifest["seed"] = True
    elif attack == "non_text_scene_id":
        manifest["scene_id"] = 1
    elif attack == "non_text_digest":
        manifest["source_scene_spec_sha256"] = 1
    else:
        manifest["compiler_version"] = 1
    attacked = _replace_request_ref(
        request,
        "package_manifest",
        _put_json(
            store,
            tmp_path,
            name=f"package_manifest_{attack}",
            schema_version="robotwin.generated_scene_package.v1",
            value=manifest,
        ),
    )

    _assert_recompute_error(
        tmp_path,
        store,
        attacked,
        reason="package_manifest_invalid",
    )


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("request.txt", b"A different request.\n"),
        ("generated_scene.py", b"def load_scene():\n    return None\n"),
    ],
)
def test_recompute_rejects_semantically_swapped_text_package_member(
    tmp_path: Path,
    path: str,
    payload: bytes,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    attacked = _replace_package_member(
        store,
        tmp_path,
        request,
        path=path,
        payload=payload,
    )

    _assert_recompute_error(tmp_path, store, attacked, reason="package_binding")


@pytest.mark.parametrize("forge_object_id", [False, True], ids=["object-field", "relation"])
def test_recompute_rejects_coordinated_resolved_scene_semantic_forgery(
    tmp_path: Path,
    forge_object_id: bool,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    attacked = _coordinated_resolved_semantic_attack(
        store,
        tmp_path,
        request,
        forge_object_id=forge_object_id,
    )

    _assert_recompute_error(tmp_path, store, attacked, reason="package_binding")


def test_recompute_uses_snapshot_after_generated_asset_source_is_unavailable(
    tmp_path: Path,
) -> None:
    store, request, source_root, _ = _validation_request(
        tmp_path,
        generated_provenance=True,
    )
    shutil.rmtree(source_root)

    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()
    result = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).recompute(request)

    assert result.validation_status is ValidationStatus.PASS
    assert result.fail_count == 0
    assert result.not_run_count == 0


def test_recompute_rejects_validator_report_with_decision_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_with_claims(*args: object, **kwargs: object):
        report = real_validate(*args, **kwargs)
        report["publishable"] = True
        report["decision"] = {"status": "approved"}
        return report

    monkeypatch.setattr(snapshot_module, "validate_resolved_scene", validate_with_claims)

    _assert_recompute_error(tmp_path, store, request, reason="report_invalid")


def test_recompute_rejects_nested_authority_claim_from_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_with_nested_claim(*args: object, **kwargs: object):
        report = real_validate(*args, **kwargs)
        report["checks"][0]["evidence"] = {"nested": {"decision": "publish"}}
        return report

    monkeypatch.setattr(snapshot_module, "validate_resolved_scene", validate_with_nested_claim)

    _assert_recompute_error(tmp_path, store, request, reason="report_invalid")


@pytest.mark.parametrize(
    "evidence",
    [
        {"/private/path-hidden-in-key": 0},
        {"note": "FILE:/private/path-hidden-in-value"},
    ],
)
def test_recompute_rejects_nested_absolute_locator_from_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evidence: dict[str, object],
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_with_locator(*args: object, **kwargs: object):
        report = real_validate(*args, **kwargs)
        report["checks"][0]["evidence"] = evidence
        return report

    monkeypatch.setattr(snapshot_module, "validate_resolved_scene", validate_with_locator)

    _assert_recompute_error(tmp_path, store, request, reason="report_locator")


@pytest.mark.parametrize(
    "attack",
    ["empty_checks", "extra_check_field", "wrong_count", "wrong_status"],
)
def test_recompute_rejects_malformed_raw_validator_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_with_malformed_report(*args: object, **kwargs: object):
        report = real_validate(*args, **kwargs)
        if attack == "empty_checks":
            report["checks"] = []
        elif attack == "extra_check_field":
            report["checks"][0]["unexpected"] = None
        elif attack == "wrong_count":
            report["fail_count"] += 1
        else:
            report["status"] = "incomplete"
        return report

    monkeypatch.setattr(
        snapshot_module,
        "validate_resolved_scene",
        validate_with_malformed_report,
    )

    _assert_recompute_error(tmp_path, store, request, reason="report_invalid")


def test_recompute_rejects_validator_owned_snapshot_binding_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_with_owned_check(*args: object, **kwargs: object):
        report = real_validate(*args, **kwargs)
        report["checks"].append(
            {
                "name": "snapshot_validation_binding",
                "status": "pass",
                "evidence": {},
            }
        )
        return report

    monkeypatch.setattr(snapshot_module, "validate_resolved_scene", validate_with_owned_check)

    _assert_recompute_error(tmp_path, store, request, reason="report_invalid")


@pytest.mark.parametrize(
    "unsafe_name",
    ["workspace:private/path", "workspace:private\\path", "workspace:private\npath"],
)
def test_recompute_rejects_unsafe_validator_check_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_name: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_with_unsafe_name(*args: object, **kwargs: object):
        report = real_validate(*args, **kwargs)
        report["checks"][0]["name"] = unsafe_name
        return report

    monkeypatch.setattr(snapshot_module, "validate_resolved_scene", validate_with_unsafe_name)

    _assert_recompute_error(tmp_path, store, request, reason="report_invalid")


@pytest.mark.parametrize("attack", ["preexisting_package", "missing_roundtrip"])
def test_recompute_rejects_validator_package_boundary_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_with_package_violation(*args: object, **kwargs: object):
        report = real_validate(*args, **kwargs)
        if attack == "preexisting_package":
            report["checks"].append({"name": "package_manifest", "status": "pass", "evidence": {}})
        else:
            report["checks"] = [
                check for check in report["checks"] if check["name"] != "resolved_only_roundtrip"
            ]
        return report

    monkeypatch.setattr(
        snapshot_module,
        "validate_resolved_scene",
        validate_with_package_violation,
    )

    _assert_recompute_error(tmp_path, store, request, reason="report_invalid")


def test_recompute_rejects_invalid_validator_support_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_without_support_evidence(*args: object, **kwargs: object):
        report = real_validate(*args, **kwargs)
        support = next(
            check for check in report["checks"] if check["name"].startswith("support_contact:")
        )
        support["evidence"] = None
        return report

    monkeypatch.setattr(
        snapshot_module,
        "validate_resolved_scene",
        validate_without_support_evidence,
    )

    _assert_recompute_error(tmp_path, store, request, reason="report_invalid")


def test_recompute_rejects_missing_nested_unexpected_contact_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(
        tmp_path,
        request_text="Place a can on top of a plate.",
    )
    real_validate = snapshot_module.validate_resolved_scene

    def validate_without_unexpected_check(*args: object, **kwargs: object):
        report = real_validate(*args, **kwargs)
        report["checks"] = [
            check
            for check in report["checks"]
            if not check["name"].startswith("no_unexpected_support_contact:")
        ]
        return report

    monkeypatch.setattr(
        snapshot_module,
        "validate_resolved_scene",
        validate_without_unexpected_check,
    )

    _assert_recompute_error(tmp_path, store, request, reason="report_invalid")


def test_recompute_rejects_validator_source_check_that_is_already_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    real_validate = snapshot_module.validate_resolved_scene

    def validate_with_authoritative_source(*args: object, **kwargs: object):
        report = real_validate(*args, **kwargs)
        source = next(
            check for check in report["checks"] if check["name"].startswith("real_asset_files:")
        )
        source["status"] = "pass"
        return report

    monkeypatch.setattr(
        snapshot_module,
        "validate_resolved_scene",
        validate_with_authoritative_source,
    )

    _assert_recompute_error(tmp_path, store, request, reason="snapshot_binding")


@pytest.mark.parametrize("attack", ["objects_list", "object_evidence_list", "relations_list"])
def test_recompute_rejects_runtime_container_shape_before_validator(
    tmp_path: Path,
    attack: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    if attack == "objects_list":
        runtime["objects"] = []
    elif attack == "object_evidence_list":
        object_id = next(iter(runtime["objects"]))
        runtime["objects"][object_id] = []
    else:
        runtime["relations"] = []
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name=f"runtime_{attack}",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(
        tmp_path,
        store,
        attacked,
        reason="runtime_evidence_invalid",
    )


def test_recompute_rejects_authority_claim_hidden_in_spatial_relation_evidence(
    tmp_path: Path,
) -> None:
    store, request, _, _ = _validation_request(
        tmp_path,
        request_text="A red can is left of a plastic basket near the center.",
    )
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    relation_evidence = next(iter(runtime["relations"].values()))
    relation_evidence["publishable"] = True
    relation_evidence["decision"] = "publish"
    relation_evidence["note"] = "failed at /private/operator/path"
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name="runtime_relation_with_claim",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(
        tmp_path,
        store,
        attacked,
        reason="runtime_evidence_invalid",
    )


@pytest.mark.parametrize("measurement", [10**400, "not-a-number"])
def test_recompute_rejects_invalid_relation_measurement_without_leaking_error(
    tmp_path: Path,
    measurement: object,
) -> None:
    store, request, _, _ = _validation_request(
        tmp_path,
        request_text="A red can is left of a plastic basket near the center.",
    )
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    next(iter(runtime["relations"].values()))["center_distance_m"] = measurement
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name="runtime_relation_huge_integer",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(
        tmp_path,
        store,
        attacked,
        reason="runtime_evidence_invalid",
    )


def test_recompute_rejects_forged_runtime_relation_pass(tmp_path: Path) -> None:
    store, request, _, _ = _validation_request(
        tmp_path,
        request_text="A red can is left of a plastic basket near the center.",
    )
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    relation = next(iter(runtime["relations"].values()))
    relation["pass"] = not relation["pass"]
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name="runtime_forged_relation_pass",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(
        tmp_path,
        store,
        attacked,
        reason="runtime_evidence_invalid",
    )


@pytest.mark.parametrize(
    "request_text",
    [
        "A red can is right of a plastic basket near the center.",
        "A cup is in front of a wooden block and at least 0.20 m away.",
        "A can is behind a cup near the center.",
        "A red can is near a plastic basket.",
    ],
)
def test_recompute_verifies_each_runtime_spatial_relation_algorithm(
    tmp_path: Path,
    request_text: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path, request_text=request_text)
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()

    result = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).recompute(request)

    assert result.validation_status in {ValidationStatus.PASS, ValidationStatus.FAIL}
    assert list(scratch_parent.iterdir()) == []


@pytest.mark.parametrize(
    "field",
    ["center_distance_m", "source_half_extent_m", "target_half_extent_m"],
)
def test_recompute_rejects_relation_measurements_not_derived_from_geometry(
    tmp_path: Path,
    field: str,
) -> None:
    store, request, _, _ = _validation_request(
        tmp_path,
        request_text="A red can is left of a plastic basket near the center.",
    )
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    relation = next(iter(runtime["relations"].values()))
    if field == "center_distance_m":
        relation[field] += 0.01
    else:
        relation[field][0] += 0.01
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name=f"runtime_relation_wrong_{field}",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(tmp_path, store, attacked, reason="runtime_evidence_invalid")


def test_recompute_rejects_missing_runtime_relation(tmp_path: Path) -> None:
    store, request, _, _ = _validation_request(
        tmp_path,
        request_text="A red can is left of a plastic basket near the center.",
    )
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    runtime["relations"].clear()
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name="runtime_relation_missing",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(tmp_path, store, attacked, reason="runtime_evidence_invalid")


@pytest.mark.parametrize(
    "request_text",
    [
        "Place a can on top of a plate.",
        "Put an apple inside a basket.",
    ],
)
def test_nested_unexpected_contact_targets_are_projected_without_path_leak(
    tmp_path: Path,
    request_text: str,
) -> None:
    store, request, _, _ = _validation_request(tmp_path, request_text=request_text)
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    nested_id = next(
        item_id
        for item_id, evidence in runtime["objects"].items()
        if evidence["support_target"] != "table"
    )
    runtime["objects"][nested_id]["unexpected_contact_fraction"] = 1.0
    runtime["objects"][nested_id]["unexpected_contact_targets"] = ["/private/operator/contact-path"]
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name="runtime_unexpected_contact_path",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()

    result = ValidateV2SnapshotAdapter(
        artifact_store=store,
        scratch_parent=scratch_parent,
    ).recompute(attacked)

    payload = store.resolve(result.validation_report).path.read_bytes()
    report = json.loads(payload)
    check = next(
        value
        for value in report["checks"]
        if value["name"] == f"no_unexpected_support_contact:{nested_id}"
    )
    assert result.validation_status is ValidationStatus.FAIL
    assert check["status"] == "fail"
    assert check["evidence"] == {
        "contact_fraction": 1.0,
        "has_unexpected": True,
        "target_count": 1,
    }
    assert b"/private/operator/contact-path" not in payload
    assert list(scratch_parent.iterdir()) == []


def test_recompute_rejects_container_hidden_in_runtime_scalar(tmp_path: Path) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    object_evidence = next(iter(runtime["objects"].values()))
    object_evidence["penetration_count"] = {"/private/path-in-a-key": 0}
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name="runtime_container_scalar",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(
        tmp_path,
        store,
        attacked,
        reason="runtime_evidence_invalid",
    )


@pytest.mark.parametrize(
    "field",
    [
        "translation_drift_m",
        "rotation_drift_deg",
        "resolved_translation_error_m",
        "resolved_rotation_error_deg",
    ],
)
def test_recompute_rejects_negative_runtime_norms(tmp_path: Path, field: str) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    next(iter(runtime["objects"].values()))[field] = -1.0
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name=f"runtime_negative_{field}",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(
        tmp_path,
        store,
        attacked,
        reason="runtime_evidence_invalid",
    )


def test_recompute_rejects_impossible_unique_video_count(tmp_path: Path) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    runtime["unique_video_frame_count"] = runtime["video_frame_count"] + 1
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name="runtime_impossible_unique_frames",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(
        tmp_path,
        store,
        attacked,
        reason="runtime_evidence_invalid",
    )


@pytest.mark.parametrize("seed", [True, 2_147_483_647])
def test_recompute_rejects_runtime_seed_type_or_binding_mismatch(
    tmp_path: Path,
    seed: object,
) -> None:
    store, request, _, _ = _validation_request(tmp_path)
    runtime = json.loads(store.resolve(request.runtime_evidence).path.read_bytes())
    runtime["seed"] = seed
    attacked = _replace_request_ref(
        request,
        "runtime_evidence",
        _put_json(
            store,
            tmp_path,
            name="runtime_seed_mismatch",
            schema_version="robotwin.scene_runtime_evidence.v2",
            value=runtime,
        ),
    )

    _assert_recompute_error(tmp_path, store, attacked, reason="runtime_binding")
