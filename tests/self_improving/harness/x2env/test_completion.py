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


def completed_fixture(
    tmp_path,
    input_fault=None,
    grounding=False,
    grounding_fault=None,
    revised=False,
    revision_fault=None,
    reservation_fault=None,
    structural_grounding=False,
    local_color=False,
):
    """Synthetic producer at external execution seam, not a real Genesis run."""
    from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
    from self_improving.harness.x2env.compile import (
        CompiledScene,
        ResolvedAsset,
        ResolvedAssetSet,
        StructuralPolicy,
    )
    from self_improving.harness.x2env.contracts import ArtifactRef, SceneIR, ToolResult
    from self_improving.harness.x2env.diagnosis import DiagnosisProposal, DiagnosisResult
    from self_improving.harness.x2env.genesis_runtime import RuntimeScene
    from self_improving.harness.x2env.observation import observe_replay
    from self_improving.harness.x2env.replay import ReplayFile, ReplayProfile, ReplayResult
    from tests.self_improving.harness.x2env.test_assessment import bound_fixture
    from tests.self_improving.harness.x2env.test_resolver import inputs

    base = tmp_path / "base"
    base.mkdir()
    store, registry, _, scene_ref, _ = inputs(base)
    if local_color:
        scene_bytes = store.read_artifact(scene_ref)
        store = Store(base / "color-state")
        registry = AssetRegistry(store)
        scene_ref = store.write_artifact(scene_bytes, "application/json")
    from self_improving.harness.x2env.input import ingest

    request = X2EnvRequest(
        text="fixture", seed=0, idempotency_key="complete", output_dir=str(tmp_path / "requested")
    )
    input_bundle = ingest(request, store)
    ir = SceneIR.model_validate_json(store.read_artifact(scene_ref))
    entity = ir.entities[0].model_copy(update={"id": "item"})
    if local_color:
        entity = entity.model_copy(update={"color": "pink", "material": "plastic"})
    ir = ir.model_copy(update={"entities": (entity,), "input_sha256": input_bundle.request_sha256})
    if grounding:
        from self_improving.harness.x2env.contracts import Pose, SceneRelation

        entity = entity.model_copy(
            update={
                "dimensions": (0.1, 0.1, 0.1),
                "pose": Pose(frame="world", position=(0.0, 0.0, 0.45), yaw_degrees=0.0),
            }
        )
        if structural_grounding:
            entity = entity.model_copy(
                update={"pose": Pose(frame="table", position=(0.0, 0.0, 0.05), yaw_degrees=0.0)}
            )
        table = entity.model_copy(
            update={
                "id": "table",
                "category": "table",
                "role": "structural_support",
                "dimensions": (1.0, 1.0, 0.1),
                "pose": Pose(frame="world", position=(0.0, 0.0, 0.4), yaw_degrees=0.0),
            }
        )
        ir = ir.model_copy(
            update={
                "entities": (entity, table),
                "relations": (
                    SceneRelation(
                        source="item",
                        target="table",
                        relation="on",
                        provenance=entity.provenance.pose,
                    ),
                ),
            }
        )
    ground_ir = ir
    if revised:
        doc = ir.model_dump(mode="json")
        doc["revision"] += 1
        for row in doc["entities"][0]["provenance"]["pose"]:
            row["kind"] = "override"
        ir = SceneIR.model_validate_json(json.dumps(doc))
        ground_doc = ground_ir.model_dump(mode="json")
        ground_doc["entities"][0]["pose"]["yaw_degrees"] = 20.0
        ground_ir = SceneIR.model_validate_json(json.dumps(ground_doc))
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
    color_snapshot = None
    if local_color:
        from tests.self_improving.harness.x2env.test_completion_local_color import (
            execute_fixture_color,
        )

        parent_version = version
        color_scene = ir
        if grounding:
            color_scene = ir.model_copy(
                update={
                    "entities": tuple(
                        e.model_copy(
                            update={
                                "dimensions": None,
                                "pose": e.pose.model_copy(
                                    update={"position": (None, None, None), "yaw_degrees": None}
                                ),
                            }
                        )
                        for e in ir.entities
                    )
                }
            )
        color_snapshot, version = execute_fixture_color(
            store, registry, request, color_scene, version, tmp_path, grounding=grounding
        )
        if local_color == "parent":
            version = parent_version
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
        if local_color:
            actual = next(m for m in version.files if m.path == member["path"])
            member["sha256"] = actual.artifact.sha256
            member["size_bytes"] = actual.artifact.size_bytes
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
    snapshot = color_snapshot or store.submit(request)
    snapshot = store.claim(snapshot.workflow_id)
    if input_fault == "malformed":
        bundle = put({"fixture_only": True})
    else:
        if input_fault == "different_request":
            input_bundle = ingest(request.model_copy(update={"text": "different request"}), store)
        elif input_fault == "bundle_seed":
            input_bundle = input_bundle.model_copy(update={"seed": request.seed + 1})
        bundle = put(input_bundle.model_dump(mode="json"))
    if local_color:
        bundle = color_snapshot.input_bundle
    stages = [
        ("ingest", {}, bundle),
        ("codex.interpret", {"scene_ir": scene_ref}, scene_ref),
        ("x2env.compile", {"compiled_scene": put(compiled.model_dump(mode="json"))}, None),
        ("x2env.replay", {"replay_result": put(replay.model_dump(mode="json"))}, None),
        ("observe", {"observation": put(observed.model_dump(mode="json"))}, None),
        ("codex.diagnose", {"diagnosis": diagnosis_ref}, diagnosis_ref),
        ("x2env.validate", {"validation": validation}, validation),
    ]
    if grounding:
        from self_improving.harness.x2env.contracts import (
            BackendProposal,
            SceneIntentProposal,
            UnknownField,
        )

        accepted = ground_ir if revised else ir
        accepted_ref = store.write_artifact(accepted.model_dump_json().encode(), "application/json")
        pending = accepted.model_copy(
            update={
                "entities": tuple(
                    e.model_copy(
                        update={
                            "dimensions": None,
                            "pose": e.pose.model_copy(
                                update={"position": (None, None, None), "yaw_degrees": None}
                            ),
                        }
                    )
                    for e in accepted.entities
                )
            }
        )
        if structural_grounding:
            pending = accepted.model_copy(
                update={
                    "entities": (
                        accepted.entities[0].model_copy(
                            update={
                                "pose": accepted.entities[0].pose.model_copy(
                                    update={"position": (0.0, 0.0, None)}
                                )
                            }
                        ),
                        accepted.entities[1].model_copy(
                            update={
                                "dimensions": (1.0, 1.0, None),
                                "pose": accepted.entities[1].pose.model_copy(
                                    update={"position": (None, None, None), "yaw_degrees": None}
                                ),
                            }
                        ),
                    )
                }
            )
        if structural_grounding and grounding_fault == "known_axis":
            pending = pending.model_copy(
                update={
                    "entities": (
                        pending.entities[0].model_copy(
                            update={
                                "pose": pending.entities[0].pose.model_copy(
                                    update={"position": (0.2, 0.0, None)}
                                )
                            }
                        ),
                        pending.entities[1],
                    )
                }
            )
        pending_ref = put(pending.model_dump(mode="json"))
        if local_color:
            pending_ref = color_snapshot.pending_scene_ir

        def execution_double(value):
            """Actual short external process, synthetic payload, not Codex qualification."""
            import subprocess
            import sys

            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import sys,time; print(sys.argv[1],flush=True); time.sleep(.05)",
                    json.dumps(value),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            stat = Path(f"/proc/{process.pid}/stat").read_text()
            evidence = put(
                {
                    "pid": process.pid,
                    "pgid": process.pid,
                    "start_ticks": int(stat.rsplit(")", 1)[1].split()[19]),
                    "executable_sha256": hashlib.sha256(
                        Path(sys.executable).read_bytes()
                    ).hexdigest(),
                    "fixture_only": True,
                }
            )
            output, errors = process.communicate(timeout=10)
            assert process.returncode == 0 and not errors
            terminal = put(
                {
                    "pid": process.pid,
                    "returncode": process.returncode,
                    "reaped": True,
                    "failure": None,
                    "fixture_only": True,
                }
            )
            return (evidence, terminal, put(json.loads(output)))

        original = BackendProposal(
            status="completed",
            proposal=SceneIntentProposal(
                scene=pending,
                unknowns=tuple(
                    UnknownField(
                        field=field,
                        reason="unit unspecified design",
                        critical=True,
                        reason_kind=kind,
                        provenance=entity.provenance.pose,
                    )
                    for field, kind in [
                        ("scene.entities.table.dimensions[2]", "unspecified"),
                        ("scene.entities.table.pose", "unspecified"),
                        ("scene.entities.item.pose.position[2]", "pose_unobservable"),
                    ]
                )
                if structural_grounding
                else (
                    UnknownField(
                        field="scene.entities[*].dimensions",
                        reason="fixture design scale unknown",
                        critical=True,
                        reason_kind="scale_unobservable",
                        provenance=entity.provenance.dimensions,
                    ),
                ),
            ),
            evidence=execution_double({"fixture_original": True}),
            error_code=None,
            elapsed_seconds=0.0,
        )
        original_ref = put(original.model_dump(mode="json"))
        initial_assets = resolved.model_copy(update={"scene_ir": pending_ref})
        initial_ref = put(initial_assets.model_dump(mode="json"))
        if local_color:
            original_ref = color_snapshot.proposal
            original = BackendProposal.model_validate_json(store.read_artifact(original_ref))
            initial_ref = color_snapshot.resolved_assets
        grounded_assets = resolved.model_copy(update={"scene_ir": accepted_ref})
        final_assets = put(grounded_assets.model_dump(mode="json"))
        values = {
            "entities": [
                {
                    "id": e.id,
                    "dimensions": e.dimensions,
                    "frame": e.pose.frame,
                    "position": e.pose.position,
                    "yaw_degrees": e.pose.yaw_degrees,
                }
                for e in accepted.entities
            ]
        }
        ground_ref = put(
            {
                "schema_version": "x2env.scene_grounding.v1",
                "status": "completed",
                "error_code": None,
                "bundle_ref": bundle.model_dump(),
                "proposal_ref": original_ref.model_dump(),
                "assets_ref": initial_ref.model_dump(),
                "policy": {"enabled": True, "mode": "asset_anchored_simulation"},
                "original_unknowns": [
                    u.model_dump(mode="json") for u in original.proposal.unknowns
                ],
                "resolved_unknowns": [0],
                "proposed_scene": accepted.model_dump(mode="json"),
                "real_world_scale_recovered": False,
                "authority": "advisory_design_only",
                "changes": [],
                "evidence": [ref.model_dump() for ref in execution_double(values)],
            }
        )
        if structural_grounding:
            from self_improving.harness.x2env.design_plan import classify_design_unknowns
            from self_improving.harness.x2env.grounding import SceneDesignPolicy

            policy = SceneDesignPolicy(
                enabled=True,
                structural_defaults_enabled=True,
                world_anchor_xy=(0.0, 0.0),
                world_anchor_yaw_degrees=0.0,
            )
            plan = classify_design_unknowns(original.proposal, policy, compiled.policy)
            fixed = {
                r.entity_id + "." + r.path: (0.05 if r.basis == "on_geometry_derived" else r.value)
                for r in plan.rules
            }
            body = json.loads(store.read_artifact(ground_ref))
            body.update(
                policy=policy.model_dump(mode="json"),
                structural_policy=compiled.policy.model_dump(),
                design_plan=plan.model_dump(mode="json"),
                fixed_values=fixed,
                resolved_unknowns=list(plan.resolved_unknown_indices),
            )
            context = {
                "bundle_ref": bundle.model_dump(),
                "original_proposal": original.proposal.model_dump(mode="json"),
                "policy": body["policy"],
                "structural_policy": body["structural_policy"],
                "design_plan": body["design_plan"],
                "fixed_values": fixed,
                "media_selection": None,
                "asset_version": version.model_dump(mode="json"),
                "anchor_dimensions_m": report["dimensions_m"],
            }
            body["evidence"].append(put(context).model_dump())
            if grounding_fault == "default_policy":
                body["structural_policy"]["surface_height_m"] = 0.8
            if grounding_fault == "fixed_height":
                body["fixed_values"]["item.pose.position[2]"] = 0.2
            if grounding_fault == "missing_policy":
                body.pop("structural_policy")
            if grounding_fault == "plan":
                body["design_plan"]["rules"][0]["value"] = 0.9
            ground_ref = put(body)
        if grounding_fault:
            body = json.loads(store.read_artifact(ground_ref))
            if grounding_fault == "model":
                body["evidence"] = body["evidence"][-1:]
            elif grounding_fault in {"executable", "pid", "ticks", "sha"}:
                process = json.loads(
                    store.read_artifact(ArtifactRef.model_validate(body["evidence"][0]))
                )
                if grounding_fault == "executable":
                    process["executable_sha256"] = "b" * 64
                elif grounding_fault == "pid":
                    process["pid"] = process["pgid"] = None
                elif grounding_fault == "ticks":
                    process["start_ticks"] = True
                else:
                    process["executable_sha256"] = "invalid"
                body["evidence"][0] = put(process).model_dump()
            elif grounding_fault == "assets":
                body["assets_ref"] = final_assets.model_dump()
            elif grounding_fault == "original":
                body["proposal_ref"] = pending_ref.model_dump()
            elif grounding_fault == "unknowns":
                body["original_unknowns"][0]["critical"] = False
            elif grounding_fault == "revision":
                body["proposed_scene"]["revision"] += 1
            elif grounding_fault == "output":
                wrong = json.loads(
                    store.read_artifact(ArtifactRef.model_validate(body["evidence"][-1]))
                )
                wrong["entities"][0]["position"][0] = 0.2
                body["evidence"][-1] = put(wrong).model_dump()
            ground_ref = put(body)
        stages[1:2] = [
            (
                "codex.interpret",
                {"pending_scene_ir": pending_ref, "proposal": original_ref},
                original_ref,
            ),
            ("asset.resolve", {"resolved_assets": initial_ref}, initial_ref),
            (
                "codex.ground",
                {
                    "scene_ir": accepted_ref,
                    "resolved_assets": final_assets,
                    "grounding": ground_ref,
                },
                ground_ref,
            ),
        ]
        if revised:
            from self_improving.harness.x2env.diagnosis import ScenePatch
            from self_improving.harness.x2env.revision import apply_revision

            prior_runtime = runtime.model_copy(update={"scene_ir_sha256": accepted_ref.sha256})
            prior_runtime_ref = put(prior_runtime.model_dump(mode="json"))
            prior_report = json.loads(store.read_artifact(observed.physics_report))
            prior_report["scene_sha256"] = hashlib.sha256(
                json.dumps(
                    prior_runtime.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            prior_observed = observed.model_copy(
                update={
                    "observation": observed.observation.model_copy(
                        update={"scene_ir": accepted_ref, "runtime_scene": prior_runtime_ref}
                    ),
                    "physics_report": put(prior_report),
                }
            )
            prior_observed_ref = put(prior_observed.model_dump(mode="json"))

            patch_proposal = DiagnosisProposal(
                base_revision=accepted.revision,
                visual_intent="failed",
                reason="Synthetic layout revision test",
                evidence_sha256=(accepted_ref.sha256,),
                scene_patches=(ScenePatch(entity_id="item", pose=ir.entities[0].pose, joints=()),),
                asset_patches=(),
            )
            repair_evidence = execution_double(patch_proposal.model_dump(mode="json"))
            repair_receipt = put(
                {
                    "status": "completed",
                    "authority": "advisory_only",
                    "executable_sha256": json.loads(store.read_artifact(repair_evidence[0]))[
                        "executable_sha256"
                    ],
                    "scene_ir": accepted_ref.model_dump(),
                    "physical_report": prior_observed.physics_report.model_dump(),
                    "observation": prior_observed.observation.model_dump(mode="json"),
                    "admitted_at": json.loads(store.read_artifact(diagnosis.receipt))[
                        "admitted_at"
                    ],
                    "proposal": patch_proposal.model_dump(mode="json"),
                    "evidence": [r.model_dump() for r in repair_evidence],
                }
            )
            repair = DiagnosisResult(
                status="completed", proposal=patch_proposal, receipt=repair_receipt
            )
            repair_ref = put(repair.model_dump(mode="json"))
            revision = apply_revision(
                store,
                registry,
                accepted_ref,
                grounded_assets,
                repair_ref,
                output_root=tmp_path / "revision",
                history=(),
                failure_fingerprint=hashlib.sha256(
                    json.dumps(
                        {
                            "input": prior_report["input_sha256"],
                            "error_code": prior_report.get("error_code"),
                            "checks": [
                                c
                                for c in prior_report.get("checks", [])
                                if c.get("status") != "passed"
                            ],
                            "images": [f.image.sha256 for f in prior_observed.observation.frames],
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
            )
            assert revision.scene_ir == scene_ref
            revision_ref = revision.receipt
            if revision_fault:
                row = json.loads(store.read_artifact(revision_ref))
                if revision_fault == "base":
                    row["base_scene"] = scene_ref.model_dump()
                elif revision_fault == "history":
                    row["history"] = [ground_ref.model_dump()]
                elif revision_fault == "budget":
                    row["cost"] = 3
                elif revision_fault == "fingerprint":
                    row["failure_fingerprint"] = "e" * 64
                elif revision_fault == "diagnosis":
                    row["diagnosis"] = diagnosis_ref.model_dump()
                elif revision_fault == "assets":
                    row["resolved_assets"]["assets"][0]["version_sha256"] = "e" * 64
                elif revision_fault == "asset_receipt":
                    row["asset_receipts"] = [ground_ref.model_dump()]
                elif revision_fault in {"model", "patch", "stale", "observation"}:
                    body = json.loads(store.read_artifact(repair_receipt))
                    if revision_fault == "model":
                        body["evidence"] = body["evidence"][-1:]
                    elif revision_fault == "stale":
                        body["admitted_at"] = "2020-01-01T00:00:00+00:00"
                    elif revision_fault == "observation":
                        body["observation"]["scene_ir"] = scene_ref.model_dump()
                    else:
                        bad_proposal = patch_proposal.model_copy(
                            update={
                                "scene_patches": (
                                    patch_proposal.scene_patches[0].model_copy(
                                        update={
                                            "pose": patch_proposal.scene_patches[0].pose.model_copy(
                                                update={"yaw_degrees": 40.0}
                                            )
                                        }
                                    ),
                                )
                            }
                        )
                        body["proposal"] = bad_proposal.model_dump(mode="json")
                        body["evidence"] = [
                            r.model_dump() for r in execution_double(body["proposal"])
                        ]
                        repair = repair.model_copy(update={"proposal": bad_proposal})
                    repair = repair.model_copy(update={"receipt": put(body)})
                    repair_ref = put(repair.model_dump(mode="json"))
                    row["diagnosis"] = repair_ref.model_dump()
                revision_ref = put(row)
            stages[4:4] = [
                ("observe", {"observation": prior_observed_ref}, prior_observed_ref),
                ("codex.diagnose", {"diagnosis": repair_ref}, repair_ref),
                (
                    "revise",
                    {
                        "scene_ir": revision.scene_ir,
                        "resolved_assets": put(revision.assets.model_dump(mode="json")),
                        "revision_receipt": revision_ref,
                    },
                    revision_ref,
                ),
            ]
    for index, (stage, fields, ref) in enumerate(stages):
        if local_color and index < (3 if grounding else 2):
            continue
        if index:
            if stage == "revise" and reservation_fault != "missing":
                from self_improving.harness.x2env.contracts import RepairReservation

                # Real Store reservation; synthetic revision payload is not qualification.
                healthy = json.loads(store.read_artifact(revision.receipt))
                if reservation_fault in {"failed_budget", "cancelled_budget"}:
                    failed_approval = put(
                        {
                            "authority": "harness_controller",
                            "approved": True,
                            "workflow_id": snapshot.workflow_id,
                            "base_revision": snapshot.revision,
                            "input_bundle": bundle.model_dump(),
                            "scene_ir": snapshot.scene_ir.model_dump(),
                            "diagnosis": snapshot.diagnosis.model_dump(),
                            "cost": 1,
                            "failure_fingerprint": "a" * 64,
                        }
                    )
                    failed = RepairReservation(
                        kind="scene",
                        cost=1,
                        failure_fingerprint="a" * 64,
                        approval=failed_approval,
                        base_revision=snapshot.revision,
                    )
                    snapshot = store.begin_operation(snapshot, "revise", repair_reservation=failed)
                    snapshot = store.complete_operation(
                        snapshot,
                        ToolResult(
                            operation_id=snapshot.operations[-1].operation_id,
                            status="failed"
                            if reservation_fault == "failed_budget"
                            else "cancelled",
                            outputs=(failed_approval,),
                            error_code="explicit_fixture_failure",
                        ),
                        bundle,
                        status="active",
                    )
                approval = put(
                    {
                        "authority": "harness_controller",
                        "approved": True,
                        "workflow_id": snapshot.workflow_id,
                        "base_revision": snapshot.revision,
                        "input_bundle": bundle.model_dump(),
                        "scene_ir": snapshot.scene_ir.model_dump(),
                        "diagnosis": snapshot.diagnosis.model_dump(),
                        "cost": 1,
                        "failure_fingerprint": healthy["failure_fingerprint"],
                    }
                )
                if reservation_fault == "approval":
                    wrong_approval = json.loads(store.read_artifact(approval))
                    wrong_approval["workflow_id"] = "different-workflow"
                    approval = put(wrong_approval)
                reservation = RepairReservation(
                    kind="scene",
                    cost=1,
                    failure_fingerprint=healthy["failure_fingerprint"],
                    approval=approval,
                    base_revision=snapshot.revision,
                )
                snapshot = store.begin_operation(snapshot, stage, repair_reservation=reservation)
                envelope = {
                    "workflow_id": snapshot.workflow_id,
                    "operation_id": snapshot.operations[-1].operation_id,
                    "reservation": reservation.model_dump(mode="json"),
                    "input_bundle": bundle.model_dump(),
                    "scene_ir": snapshot.scene_ir.model_dump(),
                    "diagnosis": snapshot.diagnosis.model_dump(),
                }
                if reservation_fault == "workflow":
                    envelope["workflow_id"] = "different-workflow"
                elif reservation_fault == "operation":
                    envelope["operation_id"] = "different-operation"
                elif reservation_fault == "cost":
                    envelope["reservation"]["cost"] = 2
                elif reservation_fault == "input":
                    envelope["input_bundle"] = scene_ref.model_dump()
                body = json.loads(store.read_artifact(ref))
                body["reservation"] = put(envelope).model_dump()
                if reservation_fault == "missing_ref":
                    del body["reservation"]
                ref = put(body)
                fields = {**fields, "revision_receipt": ref}
            else:
                snapshot = store.begin_operation(snapshot, stage)
        ref = ref or next(iter(fields.values()))
        snapshot = store.complete_operation(
            snapshot,
            ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status="succeeded",
                outputs=tuple(dict.fromkeys((ref, *fields.values()))),
            ),
            bundle,
            status="active",
            **fields,
        )
    return store, snapshot


def test_completion_accepts_bound_grounding(tmp_path):
    from self_improving.harness.x2env.completion import materialize_completion

    store, snapshot = completed_fixture(tmp_path, grounding=True)
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "materialized", result


@pytest.mark.parametrize(
    "fault", [None, "default_policy", "fixed_height", "missing_policy", "plan", "known_axis"]
)
def test_completion_audits_text_structural_design_against_geometry_and_compile_policy(
    tmp_path, fault
):
    from self_improving.harness.x2env.completion import materialize_completion

    store, snapshot = completed_fixture(
        tmp_path, grounding=True, structural_grounding=True, grounding_fault=fault
    )
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    if fault:
        assert result.status == "failed" and "grounding" in result.error_code, result
    else:
        assert result.status == "materialized", result


def test_completion_accepts_grounding_then_layout_revision(tmp_path):
    from self_improving.harness.x2env.completion import materialize_completion

    store, snapshot = completed_fixture(tmp_path, grounding=True, revised=True)
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "materialized", result


def test_completion_rejects_successful_revision_without_persistent_reservation(tmp_path):
    from self_improving.harness.x2env.completion import materialize_completion

    store, snapshot = completed_fixture(
        tmp_path, grounding=True, revised=True, reservation_fault="missing"
    )
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert (
        result.status == "failed" and result.error_code == "completion_repair_reservation_missing"
    )


@pytest.mark.parametrize(
    "fault", ["workflow", "operation", "cost", "input", "missing_ref", "approval"]
)
def test_completion_rejects_laundered_reservation_envelopes(tmp_path, fault):
    from self_improving.harness.x2env.completion import materialize_completion

    store, snapshot = completed_fixture(
        tmp_path, grounding=True, revised=True, reservation_fault=fault
    )
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "failed" and result.error_code.startswith("completion_repair_")


@pytest.mark.parametrize("fault", ["failed_budget", "cancelled_budget"])
def test_completion_preserves_failed_reservation_budget_and_approval_in_package(tmp_path, fault):
    from self_improving.harness.x2env.completion import materialize_completion

    store, snapshot = completed_fixture(
        tmp_path, grounding=True, revised=True, reservation_fault=fault
    )
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "materialized", result
    reservations = [op.repair_reservation for op in snapshot.operations if op.repair_reservation]
    assert sum(r.cost for r in reservations) == 2
    for reservation in reservations:
        copied = Path(result.package_path) / "evidence" / (reservation.approval.sha256 + ".json")
        assert copied.read_bytes() == store.read_artifact(reservation.approval)
    audits = [
        json.loads(path.read_bytes())
        for path in (Path(result.package_path) / "evidence").glob("*.json")
    ]
    budget_audits = [
        row
        for row in audits
        if isinstance(row, dict) and row.get("schema_version") == "x2env.repair_budget_audit.v1"
    ]
    assert len(budget_audits) == 1 and budget_audits[0]["reserved_cost"] == 2
    assert budget_audits[0]["operations"][0]["status"] == (
        "failed" if fault == "failed_budget" else "cancelled"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "base",
        "history",
        "budget",
        "diagnosis",
        "assets",
        "asset_receipt",
        "model",
        "patch",
        "fingerprint",
        "stale",
        "observation",
    ],
)
def test_completion_rejects_grounded_revision_chain_attacks(tmp_path, fault):
    from self_improving.harness.x2env.completion import materialize_completion

    store, snapshot = completed_fixture(
        tmp_path, grounding=True, revised=True, revision_fault=fault
    )
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "failed" and (
        "grounding" in result.error_code or result.error_code.startswith("completion_repair_")
    )
    assert result.manifest is None


@pytest.mark.parametrize(
    "fault",
    [
        "model",
        "assets",
        "original",
        "unknowns",
        "output",
        "executable",
        "pid",
        "ticks",
        "sha",
        "revision",
    ],
)
def test_completion_rejects_grounding_source_chain_attacks(tmp_path, fault):
    from self_improving.harness.x2env.completion import materialize_completion

    store, snapshot = completed_fixture(tmp_path, grounding=True, grounding_fault=fault)
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "failed" and "grounding" in result.error_code
    assert result.package_path is None and result.manifest is None
    if fault == "revision":
        assert result.error_code == "completion_grounding_revision_chain_not_verified"


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
