"""Completion gates use actual Store/CAS; synthetic evidence is not qualification."""

import hashlib
import json
from pathlib import Path

import pytest

from self_improving.harness.x2env.contracts import X2EnvRequest
from self_improving.harness.x2env.store import Store


def test_unexecuted_workflow_cannot_materialize_success(tmp_path):
    from self_improving.harness.x2env.completion import materialize_completion

    store = Store(tmp_path / "state")
    snapshot = store.submit(
        X2EnvRequest(
            text="box", seed=1, idempotency_key="empty", output_dir=str(tmp_path / "requested")
        )
    )
    result = materialize_completion(snapshot, store, tmp_path / "result")
    assert result.status == "failed"
    assert result.error_code == "incomplete_completion_evidence"
    assert result.manifest is None
    assert result.package_path is None
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["workflow_id"] == snapshot.workflow_id
    assert receipt["copy_run"] == "not_run"
    assert store.status(snapshot.workflow_id) == snapshot


def completed_fixture(tmp_path, input_fault=None):
    """Synthetic producer at external execution seam, not a real Genesis run."""
    from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
    from self_improving.harness.x2env.compile import (
        CompiledScene,
        ResolvedAsset,
        ResolvedAssetSet,
        StructuralPolicy,
    )
    from self_improving.harness.x2env.contracts import SceneIR, ToolResult
    from self_improving.harness.x2env.diagnosis import DiagnosisProposal, DiagnosisResult
    from self_improving.harness.x2env.genesis_runtime import RuntimeScene
    from self_improving.harness.x2env.observation import observe_replay
    from self_improving.harness.x2env.replay import ReplayFile, ReplayProfile, ReplayResult
    from tests.self_improving.harness.x2env.test_assessment import bound_fixture
    from tests.self_improving.harness.x2env.test_resolver import inputs

    base = tmp_path / "base"
    base.mkdir()
    store, registry, _, scene_ref, _ = inputs(base)
    from self_improving.harness.x2env.input import ingest

    request = X2EnvRequest(
        text="fixture", seed=0, idempotency_key="complete", output_dir=str(tmp_path / "requested")
    )
    input_bundle = ingest(request, store)
    ir = SceneIR.model_validate_json(store.read_artifact(scene_ref))
    entity = ir.entities[0].model_copy(update={"id": "item"})
    ir = ir.model_copy(update={"entities": (entity,), "input_sha256": input_bundle.request_sha256})
    if input_fault == "scene_input":
        ir = ir.model_copy(update={"input_sha256": "f" * 64})

    def put(value):
        return store.write_artifact(json.dumps(value).encode(), "application/json")

    scene_ref = store.write_artifact(ir.model_dump_json().encode(), "application/json")
    runroot = tmp_path / "synthetic"
    runroot.mkdir()
    data = bound_fixture(runroot)
    normalized = data["package_root"]
    report = json.loads((normalized / "normalization.json").read_bytes())
    evidence = store.write_artifact(b"explicit synthetic fixture", "text/plain")
    version = AssetRegistry(store).register(
        "fixture-item",
        "box",
        normalized,
        "asset.urdf",
        files=tuple(row["path"] for row in report["files"]),
        normalization_report=put(report),
        license=AssetLicense(
            spdx="CC0-1.0", source_url="https://example.org/fixture", evidence=evidence
        ),
        source=AssetSource(kind="local", provider="test", source_ref="fixture", evidence=evidence),
        receipt=evidence,
    )
    runtime = data["scene"]
    prefix = f"assets/item/{version.version_sha256}"
    runtime["scene_ir_sha256"] = scene_ref.sha256
    runtime["seed"] = request.seed + (1 if input_fault == "runtime_seed" else 0)
    for item in runtime["entities"]:
        if item["id"] == "item":
            item.update(
                urdf_path=prefix + "/asset.urdf",
                physics_path=prefix + "/physics.json",
                version_sha256=version.version_sha256,
            )
    for member in runtime["members"]:
        member["path"] = prefix + "/" + member["path"]
    runtime = RuntimeScene.model_validate(runtime)
    runtime_ref = store.write_artifact(runtime.model_dump_json().encode(), "application/json")
    compile_receipt = put({"scene_ir": scene_ref.model_dump(), "fixture_only": True})
    resolved = ResolvedAssetSet(
        scene_ir=scene_ref,
        assets=(
            ResolvedAsset(
                entity_id="item",
                version_sha256=version.version_sha256,
                acquisition_source="local",
                selection="exact",
            ),
        ),
    )
    compiled = CompiledScene(
        scene_ir=scene_ref,
        runtime_scene=runtime,
        resolved_assets=resolved,
        policy=StructuralPolicy(thickness_m=0.1, surface_height_m=0.4, friction=0.6),
        defaults_applied=(),
        receipt=compile_receipt,
    )
    digest = hashlib.sha256(
        json.dumps(runtime.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    profiles = []
    for name, entry in data["profiles"].items():
        root = Path(entry["root"])
        for filename in ("job.json", "result.json"):
            value = json.loads((root / filename).read_bytes())
            value["scene_sha256"] = digest
            if filename == "job.json":
                value["scene"] = runtime.model_dump(mode="json")
            (root / filename).write_text(json.dumps(value))
        profiles.append(
            ReplayProfile(
                profile=name,
                root=str(root),
                status="passed",
                files=tuple(
                    ReplayFile(
                        path=p.relative_to(root).as_posix(),
                        artifact=store.write_artifact(
                            p.read_bytes(),
                            "image/png"
                            if p.suffix == ".png"
                            else "video/mp4"
                            if p.suffix == ".mp4"
                            else "application/json"
                            if p.suffix == ".json"
                            else "application/octet-stream",
                        ),
                    )
                    for p in sorted(root.rglob("*"))
                    if p.is_file()
                ),
            )
        )
    replay = ReplayResult(
        status="succeeded",
        profiles=tuple(profiles),
        error_code=None,
        receipt=put(
            {
                "scene": runtime.model_dump(mode="json"),
                "status": "succeeded",
                "profiles": [p.model_dump(mode="json") for p in profiles],
            }
        ),
    )
    # An explicit package copy gives the real assessor the compiled member paths.
    for member in version.files:
        target = runroot / "assessment-assets" / prefix / member.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(store.read_artifact(member.artifact))
    observed = observe_replay(
        store, scene_ref, runtime_ref, replay, package_root=runroot / "assessment-assets"
    )
    assert json.loads(store.read_artifact(observed.physics_report))["physical_status"] == "passed"
    proposal = DiagnosisProposal(
        base_revision=ir.revision,
        visual_intent="passed",
        reason="explicit model boundary fixture",
        evidence_sha256=(scene_ref.sha256, observed.physics_report.sha256),
        scene_patches=(),
        asset_patches=(),
    )
    diagnosis = DiagnosisResult(
        status="completed",
        proposal=proposal,
        receipt=put(
            {
                "authority": "advisory_only",
                "status": "completed",
                "error_code": None,
                "scene_ir": scene_ref.model_dump(),
                "physical_report": observed.physics_report.model_dump(),
                "observation": observed.observation.model_dump(mode="json"),
                "proposal": proposal.model_dump(mode="json"),
                "external_agent_executed": True,
                "model": "explicit-process-boundary-fixture",
                "executable_sha256": "a" * 64,
                "admitted_at": observed.observation.frames[0].captured_at,
                "ttl_seconds": 300,
                "freshness_basis": "original_camera_captured_at_at_admission",
                "evidence": [
                    put(
                        {
                            "pid": 12,
                            "pgid": 12,
                            "start_ticks": 1,
                            "executable_sha256": "a" * 64,
                            "fixture_only": True,
                        }
                    ).model_dump(),
                    put(
                        {
                            "pid": 12,
                            "returncode": 0,
                            "reaped": True,
                            "failure": None,
                            "fixture_only": True,
                        }
                    ).model_dump(),
                    put(proposal.model_dump(mode="json")).model_dump(),
                ],
            }
        ),
    )
    diagnosis_ref = store.write_artifact(diagnosis.model_dump_json().encode(), "application/json")
    validation = put(
        {
            "status": "succeeded",
            "error_code": None,
            "physical_status": "passed",
            "visual_status": "passed",
            "physics_report": observed.physics_report.model_dump(),
            "diagnosis": diagnosis_ref.model_dump(),
            "scene_ir": scene_ref.model_dump(),
            "sim_ready": False,
        }
    )
    snapshot = store.submit(request)
    snapshot = store.claim(snapshot.workflow_id)
    if input_fault == "malformed":
        bundle = put({"fixture_only": True})
    else:
        if input_fault == "different_request":
            input_bundle = ingest(request.model_copy(update={"text": "different request"}), store)
        elif input_fault == "bundle_seed":
            input_bundle = input_bundle.model_copy(update={"seed": request.seed + 1})
        bundle = put(input_bundle.model_dump(mode="json"))
    stages = [
        ("ingest", {}, bundle),
        ("codex.interpret", {"scene_ir": scene_ref}, scene_ref),
        ("x2env.compile", {"compiled_scene": put(compiled.model_dump(mode="json"))}, None),
        ("x2env.replay", {"replay_result": put(replay.model_dump(mode="json"))}, None),
        ("observe", {"observation": put(observed.model_dump(mode="json"))}, None),
        ("codex.diagnose", {"diagnosis": diagnosis_ref}, diagnosis_ref),
        ("x2env.validate", {"validation": validation}, validation),
    ]
    for index, (stage, fields, ref) in enumerate(stages):
        if index:
            snapshot = store.begin_operation(snapshot, stage)
        ref = ref or next(iter(fields.values()))
        snapshot = store.complete_operation(
            snapshot,
            ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status="succeeded",
                outputs=(ref,),
            ),
            bundle,
            status="active",
            **fields,
        )
    return store, snapshot


@pytest.mark.parametrize(
    "fault", ["malformed", "different_request", "scene_input", "bundle_seed", "runtime_seed"]
)
def test_completion_rejects_untrusted_input_even_with_self_consistent_runtime(tmp_path, fault):
    from self_improving.harness.x2env.completion import materialize_completion

    store, snapshot = completed_fixture(tmp_path, input_fault=fault)
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "failed", (
        "self-consistent downstream evidence cannot authorize a different input"
    )
    assert result.package_path is None and result.manifest is None
    assert store.status(snapshot.workflow_id) == snapshot


def test_bound_completion_materializes_review_without_qualification(tmp_path):
    from self_improving.harness.x2env.completion import materialize_completion
    from self_improving.harness.x2env.package_loader import verify_package

    store, snapshot = completed_fixture(tmp_path)
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "materialized", result
    manifest = verify_package(Path(result.package_path))
    assert manifest["sim_ready"] is False
    assert manifest["checks"]["copy_run"] == "not_run"
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["physical_profile"] == receipt["visual_intent"] == "passed"
    assert store.status(snapshot.workflow_id) == snapshot
    before = (Path(result.package_path) / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        materialize_completion(snapshot, store, tmp_path / "completion")
    assert (Path(result.package_path) / "manifest.json").read_bytes() == before


def test_only_named_legacy_materialization_block_can_complete(tmp_path):
    from self_improving.harness.x2env.completion import materialize_completion
    from self_improving.harness.x2env.contracts import ToolResult

    store, snapshot = completed_fixture(tmp_path)
    report = json.loads(store.read_artifact(snapshot.validation))
    report.update(status="blocked", error_code="environment_package_materializer")
    ref = store.write_artifact(json.dumps(report).encode(), "application/json")
    snapshot = store.begin_operation(snapshot, "x2env.validate")
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="blocked",
            outputs=(ref,),
            error_code="environment_package_materializer",
        ),
        snapshot.input_bundle,
        status="blocked",
        reason="environment_package_materializer",
        validation=ref,
    )
    result = materialize_completion(snapshot, store, tmp_path / "legacy-completion")
    assert result.status == "materialized", result
    assert store.status(snapshot.workflow_id).status == "blocked"


@pytest.mark.parametrize("fault", ["stale", "visual", "admission", "model", "physics", "scene"])
def test_completion_rejects_forged_or_failed_evidence(tmp_path, fault):
    from self_improving.harness.x2env.completion import materialize_completion
    from self_improving.harness.x2env.contracts import ArtifactRef, ToolResult

    store, snapshot = completed_fixture(tmp_path)
    original = snapshot
    if fault == "stale":
        snapshot = snapshot.model_copy(update={"revision": snapshot.revision + 1})
    else:

        def put(value):
            return store.write_artifact(json.dumps(value).encode(), "application/json")

        observed = json.loads(store.read_artifact(snapshot.observation))
        diagnosis = json.loads(store.read_artifact(snapshot.diagnosis))
        advisory = json.loads(store.read_artifact(ArtifactRef.model_validate(diagnosis["receipt"])))
        physical_ref = ArtifactRef.model_validate(observed["physics_report"])
        physical = json.loads(store.read_artifact(physical_ref))
        validation = json.loads(store.read_artifact(snapshot.validation))
        if fault == "visual":
            diagnosis["proposal"]["visual_intent"] = "failed"
            advisory["proposal"] = diagnosis["proposal"]
        elif fault == "admission":
            advisory["admitted_at"] = "2026-01-02T00:00:00+00:00"
        elif fault == "model":
            advisory["evidence"] = []
        elif fault == "scene":
            physical["scene_sha256"] = "0" * 64
        elif fault == "physics":
            # Rebind every report, retaining passed labels but deleting actual assertions.
            physical["checks"] = []
        new_physics = put(physical)
        if fault == "physics":
            diagnosis["proposal"]["evidence_sha256"] = [
                snapshot.scene_ir.sha256,
                new_physics.sha256,
            ]
            advisory["proposal"] = diagnosis["proposal"]
            advisory["evidence"].append(put(diagnosis["proposal"]).model_dump())
        observed["physics_report"] = new_physics.model_dump()
        observed["receipt"] = put(
            {
                "observation": observed["observation"],
                "physics_report": new_physics.model_dump(),
                "authority": "harness_observation",
                "camera_time_renewed": False,
            }
        ).model_dump()
        advisory["physical_report"] = new_physics.model_dump()
        diagnosis["receipt"] = put(advisory).model_dump()
        new_diagnosis = put(diagnosis)
        validation["physics_report"] = new_physics.model_dump()
        validation["diagnosis"] = new_diagnosis.model_dump()
        fields = {
            "observation": put(observed),
            "diagnosis": new_diagnosis,
            "validation": put(validation),
        }
        for (field, ref), stage in zip(
            fields.items(), ("observe", "codex.diagnose", "x2env.validate")
        ):
            snapshot = store.begin_operation(snapshot, stage)
            snapshot = store.complete_operation(
                snapshot,
                ToolResult(
                    operation_id=snapshot.operations[-1].operation_id,
                    status="succeeded",
                    outputs=(ref,),
                ),
                snapshot.input_bundle,
                status="active",
                **{field: ref},
            )
    result = materialize_completion(snapshot, store, tmp_path / "rejected")
    assert result.status == "failed"
    assert result.package_path is None
    expected = {
        "stale": "stale_completion_snapshot",
        "visual": "completion_visual_not_passed",
        "admission": "completion_stale_or_unbound_observation",
        "model": "completion_missing_model_execution",
        "physics": "completion_physical_reassessment_failed",
        "scene": "completion_physical_binding_mismatch",
    }
    assert result.error_code == expected[fault]
    assert store.status(snapshot.workflow_id) == (original if fault == "stale" else snapshot)
