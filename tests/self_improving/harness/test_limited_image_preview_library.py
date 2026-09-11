"""Public contract for the isolated limited image-preview library."""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.genesis_asset_qualification_application import (
    GenesisAssetQualificationApplication,
)
from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
from self_improving.harness.limited_image_preview_library import (
    LimitedImagePreviewLibraryError,
    admit_limited_image_preview,
    create_limited_image_preview_library,
    load_limited_image_preview_library,
)
from self_improving.harness.package_store import PackageStore, PackageStoreError
from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.genesis_runtime_manifest import (
    GenesisRuntimeManifestV1,
    RuntimeManifestMemberV1,
    RuntimeProcessIdentityV1,
)
from self_improving.harness.schemas.genesis_scene_replay import (
    GenesisSceneReportV1,
    GenesisSceneSpecV1,
)
from self_improving.harness.schemas.limited_image_preview import (
    LimitedImagePreviewManifestV1,
    LimitedImagePreviewStageV1,
    LimitedImagePreviewTimingV1,
    limited_image_preview_manifest_sha256,
)
from self_improving.harness.schemas.limited_image_preview_execution import (
    LimitedSceneValidationV1,
    PreviewLayout,
)
from self_improving.harness.schemas.x2env import ImageSourceMedia
from self_improving.harness.schemas.x2env_compile_evidence import (
    AssetRepresentationV1,
    GenesisAssetQualificationV1,
    GenesisRuntimeLockV1,
    SceneInterpretationProposalV1,
    asset_loader_closure_sha256,
    canonical_evidence_sha256,
)


def _put_bytes(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    source = root / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=Path(name).stem,
        media_type=media_type,
        schema_version=schema_version,
    )


def _put_json(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    value: object,
    schema_version: str,
) -> ArtifactRef:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    payload = (
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode()
    return _put_bytes(
        store,
        root,
        name=name,
        payload=payload,
        media_type="application/json",
        schema_version=schema_version,
    )


def _qualified_mouse(
    store: LocalArtifactStore,
    root: Path,
) -> tuple[ArtifactRef, ArtifactRef]:
    urdf = _put_bytes(
        store,
        root,
        name="mouse/mouse.urdf",
        payload=b'<robot name="mouse"/>\n',
        media_type="application/xml",
        schema_version=None,
    )
    collision = _put_bytes(
        store,
        root,
        name="mouse/collision.obj",
        payload=b"o mouse\nv 0 0 0\n",
        media_type="application/octet-stream",
        schema_version=None,
    )
    visual = _put_bytes(
        store,
        root,
        name="mouse/visual.obj",
        payload=b"o mouse\nv 0 0 0\n",
        media_type="application/octet-stream",
        schema_version=None,
    )
    subject = {
        "namespace": "simfoundry",
        "asset_key": "pink_mouse",
        "ledger_asset_id": "legacy_mouse_001",
        "model_id": 0,
        "backend": "genesis",
        "source_record_sha256": "1" * 64,
    }
    members = (
        {"logical_path": "urdf/collision.obj", "role": "collision", "artifact": collision},
        {"logical_path": "urdf/mouse.urdf", "role": "urdf", "artifact": urdf},
        {"logical_path": "urdf/visual.obj", "role": "visual", "artifact": visual},
    )
    loader_sha256 = asset_loader_closure_sha256(subject, "urdf/mouse.urdf", members)
    representation_data = {
        "schema_version": "harness.asset_representation.v1",
        "representation_sha256": "0" * 64,
        "subject": subject,
        "representation_kind": "urdf",
        "root_logical_path": "urdf/mouse.urdf",
        "loader_scale": (1.0, 1.0, 1.0),
        "loader_closure_sha256": loader_sha256,
        "members": members,
        "collision_member_paths": ("urdf/collision.obj",),
        "visual_member_paths": ("urdf/visual.obj",),
    }
    representation_data["representation_sha256"] = canonical_evidence_sha256(
        representation_data,
        "representation_sha256",
    )
    representation = AssetRepresentationV1.model_validate(representation_data)
    representation_ref = _put_json(
        store,
        root,
        name="mouse-representation.json",
        value=representation,
        schema_version=representation.schema_version,
    )
    runtime_data = {
        "schema_version": "harness.genesis_runtime_lock.v1",
        "runtime_lock_sha256": "0" * 64,
        "backend": "genesis",
        "backend_version": "fixed-test-runtime",
        "runtime_implementation_sha256": "2" * 64,
        "loader_contract": "qualified-urdf",
        "requested_profile": "genesis.rigid_scene@1",
        "dependencies": ({"name": "genesis", "version": "fixed", "sha256": "3" * 64},),
    }
    runtime_data["runtime_lock_sha256"] = canonical_evidence_sha256(
        runtime_data,
        "runtime_lock_sha256",
    )
    runtime = GenesisRuntimeLockV1.model_validate(runtime_data)
    runtime_ref = _put_json(
        store,
        root,
        name="asset-runtime.json",
        value=runtime,
        schema_version=runtime.schema_version,
    )
    qualification_data = {
        "schema_version": "harness.asset_qualification.v1",
        "qualification_sha256": "0" * 64,
        "subject": {
            "model_subject": subject,
            "representation_sha256": representation.representation_sha256,
            "loader_closure_sha256": loader_sha256,
        },
        "representation": representation_ref,
        "runtime_lock": runtime_ref,
        "requested_profile": "genesis.rigid_scene@1",
        "issuer_implementation_sha256": "4" * 64,
        "checks": ({"code": "backend_load", "status": "pass", "evidence": (runtime_ref,)},),
        "status": "pass",
        "backend_load_executed": True,
        "collision_probe_executed": True,
        "qualified": True,
    }
    qualification_data["qualification_sha256"] = canonical_evidence_sha256(
        qualification_data,
        "qualification_sha256",
    )
    qualification = GenesisAssetQualificationV1.model_validate(qualification_data)
    qualification_ref = _put_json(
        store,
        root,
        name="mouse-qualification.json",
        value=qualification,
        schema_version=qualification.schema_version,
    )
    return representation_ref, qualification_ref


def _scene(
    store: LocalArtifactStore,
    root: Path,
    *,
    representation: ArtifactRef,
    raw_image: ArtifactRef,
    position_xy_m: tuple[float, float],
    yaw_rad: float,
    name: str,
) -> ArtifactRef:
    geometry = _put_bytes(
        store,
        root,
        name=f"{name}-geometry.obj",
        payload=f"o {name}\nv 0 0 0\n".encode(),
        media_type="application/octet-stream",
        schema_version=None,
    )
    scene = GenesisSceneSpecV1(
        schema_version="harness.genesis_scene_spec.v1",
        source=raw_image,
        representation=representation,
        geometry=geometry,
        object_id="object",
        position_m=(position_xy_m[0], position_xy_m[1], 0.76),
        orientation_wxyz=(math.cos(yaw_rad / 2), 0.0, 0.0, math.sin(yaw_rad / 2)),
        local_vertices_m=(
            (-0.05, -0.03, -0.02),
            (-0.05, 0.03, -0.02),
            (0.05, -0.03, -0.02),
            (0.05, 0.03, 0.02),
        ),
        table_center_m=(0.0, 0.0, 0.37),
        table_size_m=(1.0, 1.0, 0.74),
        table_friction=0.5,
        ground_z_m=0.0,
    )
    return _put_json(
        store,
        root,
        name=f"{name}-scene.json",
        value=scene,
        schema_version=scene.schema_version,
    )


def _normal_manifest(
    store: LocalArtifactStore,
    root: Path,
) -> tuple[LimitedImagePreviewManifestV1, ArtifactRef]:
    raw_image = _put_bytes(
        store,
        root,
        name="input.png",
        payload=b"\x89PNG\r\n\x1a\nlimited-preview",
        media_type="image/png",
        schema_version=None,
    )
    image_probe = _put_json(
        store,
        root,
        name="image-probe.json",
        value={"schema_version": "harness.image_media_probe.v1"},
        schema_version="harness.image_media_probe.v1",
    )
    source_media_model = ImageSourceMedia(
        schema_version="harness.image_source_media.v1",
        source_media_sha256=raw_image.sha256,
        source=raw_image,
        canonical_image=raw_image,
        probe=image_probe,
        width=1,
        height=1,
        color_mode="RGB",
        decoded_frame_count=1,
        unique_frame_count=1,
    )
    source_media = _put_json(
        store,
        root,
        name="source-media.json",
        value=source_media_model,
        schema_version=source_media_model.schema_version,
    )
    prompt_text = "Place the mouse at x=0.10 m, y=0.00 m, yaw=0.00 rad on the table."
    prompt = _put_bytes(
        store,
        root,
        name="prompt.txt",
        payload=prompt_text.encode(),
        media_type="text/plain",
        schema_version="harness.prompt_text.v1",
    )
    query_data = {
        "schema_version": "harness.scene_interpretation_proposal.v1",
        "proposal_sha256": "0" * 64,
        "route_id": "image2env",
        "source_media_sha256": raw_image.sha256,
        "effective_prompt": prompt,
        "objects": (
            {
                "object_id": "mouse",
                "category": "mouse",
                "role": "dynamic",
                "dimensions_m": (0.1, 0.06, 0.04),
            },
        ),
        "relations": ({"relation": "on_table", "source": "mouse", "target": "table"},),
        "unknowns": (),
    }
    query_data["proposal_sha256"] = canonical_evidence_sha256(
        query_data,
        "proposal_sha256",
    )
    query = SceneInterpretationProposalV1.model_validate(query_data)
    query_ref = _put_json(
        store,
        root,
        name="asset-query.json",
        value=query,
        schema_version=query.schema_version,
    )
    representation, qualification = _qualified_mouse(store, root)
    scene = _scene(
        store,
        root,
        representation=representation,
        raw_image=raw_image,
        position_xy_m=(0.1, 0.0),
        yaw_rad=0.0,
        name="initial",
    )
    compile_request = _put_json(
        store,
        root,
        name="compile-request.json",
        value={"schema_version": "harness.image2env_genesis_compile_request.v1"},
        schema_version="harness.image2env_genesis_compile_request.v1",
    )
    compile_receipt = _put_json(
        store,
        root,
        name="compile-receipt.json",
        value={"schema_version": "harness.image2env_genesis_compile_receipt.v1"},
        schema_version="harness.image2env_genesis_compile_receipt.v1",
    )
    runtime_members = []
    for role in ("backend", "distributions", "genesis", "interpreter", "native", "stdlib"):
        source = root / "runtime" / f"{role}.bin"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"{role}-runtime-member".encode())
        artifact = store.put_file(
            source,
            name=f"{role}/member.bin",
            media_type="application/octet-stream",
            schema_version=None,
        )
        runtime_members.append(
            RuntimeManifestMemberV1(root=role, path="member.bin", artifact=artifact)
        )
    runtime_model = GenesisRuntimeManifestV1(
        schema_version="harness.genesis_runtime_manifest.v1",
        authority="declared_byte_closure_only",
        process_identity=RuntimeProcessIdentityV1(
            implementation="cpython",
            version="3.13.12",
            abi="cp313",
            platform="linux-x86_64",
            environment=(("OMP_NUM_THREADS", "1"),),
        ),
        members=tuple(runtime_members),
    )
    runtime = _put_json(
        store,
        root,
        name="runtime.json",
        value=runtime_model,
        schema_version=runtime_model.schema_version,
    )
    replay_receipt = _put_json(
        store,
        root,
        name="replay-receipt.json",
        value={"schema_version": "harness.genesis_scene_execution_receipt.v2"},
        schema_version="harness.genesis_scene_execution_receipt.v2",
    )
    replay = _put_json(
        store,
        root,
        name="replay.json",
        value={"schema_version": "harness.genesis_scene_replay.v1"},
        schema_version="harness.genesis_scene_replay.v1",
    )
    report_model = GenesisSceneReportV1(
        schema_version="harness.genesis_scene_report.v1",
        authority="trace_consistency_only",
        status="passed",
        numerics_tunable=False,
        checks=(),
        media=(),
    )
    report = _put_json(
        store,
        root,
        name="replay-report.json",
        value=report_model,
        schema_version=report_model.schema_version,
    )
    baseline = _put_bytes(
        store,
        root,
        name="baseline.mp4",
        payload=b"baseline-video",
        media_type="video/mp4",
        schema_version=None,
    )
    half_dt = _put_bytes(
        store,
        root,
        name="half-dt.mp4",
        payload=b"half-dt-video",
        media_type="video/mp4",
        schema_version=None,
    )
    media_index = _put_bytes(
        store,
        root,
        name="retained-media.json",
        payload=b'{"execution_authority":false,"files":[],"qualified":false}',
        media_type="application/json",
        schema_version=None,
    )
    observation = _put_json(
        store,
        root,
        name="fresh-observation.json",
        value={"schema_version": "harness.genesis_fresh_capture_receipt.v1"},
        schema_version="harness.genesis_fresh_capture_receipt.v1",
    )
    layout = PreviewLayout(x_m=0.1, y_m=0.0, yaw_rad=0.0)
    validation = LimitedSceneValidationV1(
        schema_version="harness.limited_scene_validation.v1",
        execution_receipt=replay_receipt,
        scene=scene,
        physical_report=report,
        expected_layout=layout,
        actual_layout=layout,
        position_tolerance_m=0.001,
        yaw_tolerance_rad=0.001,
        semantic_status="pass",
        status="passed",
        physical_scope="dual_dt_trace_contact_settling_and_media",
    )
    validation_ref = _put_json(
        store,
        root,
        name="limited-validation.json",
        value=validation,
        schema_version=validation.schema_version,
    )
    workflow_run_id = UUID("20000000-0000-4000-8000-000000000001")
    principal_id = "golden-agent"
    workflow_head = _put_json(
        store,
        root,
        name="workflow-head.json",
        value={
            "schema_version": "harness.workflow_operation_receipt.v2",
            "workflow_run_id": str(workflow_run_id),
            "principal_id": principal_id,
        },
        schema_version="harness.workflow_operation_receipt.v2",
    )
    stage = LimitedImagePreviewStageV1(
        phase="initial",
        compile_skill_ref="image2env.compile@2.0.0",
        compile_request=compile_request,
        compile_receipt=compile_receipt,
        prompt_revision=None,
        scene=scene,
        runtime_manifest=runtime,
        replay_receipt=replay_receipt,
        replay=replay,
        replay_report=report,
        baseline_preview=baseline,
        half_dt_preview=half_dt,
        retained_media_index=media_index,
        fresh_observation=observation,
        validation=validation_ref,
        validation_result=validation,
        workflow_head=workflow_head,
    )
    timings = tuple(
        LimitedImagePreviewTimingV1(segment=segment, duration_ns=index * 1_000)
        for index, segment in enumerate(
            (
                "input_asset",
                "compile",
                "runtime_integrity",
                "simulation_render",
                "cas_deep_read",
                "codex_mcp",
                "validate_publish",
            ),
            start=1,
        )
    )
    manifest_data = {
        "schema_version": "harness.limited_image_preview_manifest.v1",
        "preview_id": "0" * 64,
        "project_id": "golden-preview",
        "workflow_run_id": workflow_run_id,
        "principal_id": principal_id,
        "mode": "normal",
        "scope": "limited_image_exact_reuse_rigid_scene",
        "requested_profile": "genesis.rigid_scene@1",
        "requested_layout": layout,
        "source_media": source_media,
        "raw_image": raw_image,
        "input_text": prompt,
        "asset_query": query_ref,
        "asset_representation": representation,
        "asset_qualification": qualification,
        "initial_stage": stage,
        "revised_stage": None,
        "timings": timings,
        "total_duration_ns": sum(item.duration_ns for item in timings),
        "authority": "validated_limited_preview_only",
        "robot_policy_evaluated": False,
    }
    manifest_data["preview_id"] = limited_image_preview_manifest_sha256(manifest_data)
    manifest = LimitedImagePreviewManifestV1.model_validate(manifest_data)
    manifest_ref = _put_json(
        store,
        root,
        name="limited-preview-manifest.json",
        value=manifest,
        schema_version=manifest.schema_version,
    )
    return manifest, manifest_ref


def test_unverified_json_cannot_enter_the_isolated_preview_library(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    manifest, manifest_ref = _normal_manifest(store, tmp_path / "sources")

    with pytest.raises(PackageStoreError) as unsupported:
        PackageStore(store).materialize(manifest_ref, tmp_path / "old-package-store")
    assert unsupported.value.reason == "manifest_schema_mismatch"

    asset_application = GenesisAssetQualificationApplication(
        tmp_path / "asset-state.sqlite3",
        store,
        manifest.initial_stage.runtime_manifest,
    )
    compiler = RevisedImage2EnvGenesisCompiler(
        store,
        Image2EnvGenesisCompiler(store, asset_application),
    )
    with pytest.raises(LimitedImagePreviewLibraryError):
        admit_limited_image_preview(
            artifact_store=store,
            compiler=compiler,
            manifest=manifest_ref,
        )
    library = create_limited_image_preview_library(
        artifact_store=store,
        compiler=compiler,
        root=tmp_path / "preview-library",
        project_id=manifest.project_id,
    )
    assert library.list() == ()

    reopened = load_limited_image_preview_library(
        artifact_store=store,
        compiler=compiler,
        root=tmp_path / "preview-library",
        project_id=manifest.project_id,
    )
    assert reopened.list() == ()
    with pytest.raises(LimitedImagePreviewLibraryError, match="not indexed"):
        reopened.get(manifest.preview_id)


def test_normal_preview_preserves_the_deep_actual_layout_without_inventing_a_target(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    manifest, _ = _normal_manifest(store, tmp_path / "sources")
    data = manifest.model_dump(mode="python")
    data["initial_stage"]["validation_result"]["expected_layout"] = None
    data["preview_id"] = limited_image_preview_manifest_sha256(data)

    normal = LimitedImagePreviewManifestV1.model_validate(data)

    assert normal.initial_stage.validation_result.expected_layout is None
    assert normal.requested_layout == normal.initial_stage.validation_result.actual_layout

    changed = normal.model_dump(mode="python")
    changed["requested_layout"]["x_m"] += 0.01
    changed["preview_id"] = limited_image_preview_manifest_sha256(changed)
    with pytest.raises(ValueError, match="actual layout"):
        LimitedImagePreviewManifestV1.model_validate(changed)
