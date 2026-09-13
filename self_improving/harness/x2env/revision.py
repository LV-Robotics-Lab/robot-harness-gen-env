"""Bounded controller revision application; immutable inputs and shared scene/asset budget."""

import json
from pathlib import Path

from .asset_revision import AssetRevision
from .compile import ResolvedAssetSet
from .contracts import ArtifactRef, Model, SceneIR
from .diagnosis import DiagnosisResult


class RevisionResult(Model):
    scene_ir: ArtifactRef
    assets: ResolvedAssetSet
    receipt: ArtifactRef


def apply_revision(
    store, registry, scene_ref, assets, diagnosis_ref, *, output_root, history, failure_fingerprint
):
    """History refs are controller-owned committed revise receipts, never model/request input."""
    scene = SceneIR.model_validate_json(store.read_artifact(scene_ref))
    diagnosis = DiagnosisResult.model_validate_json(store.read_artifact(diagnosis_ref))
    proposal = diagnosis.proposal
    if (
        diagnosis.status != "completed"
        or proposal is None
        or proposal.base_revision != scene.revision
    ):
        raise ValueError("revision_base_mismatch")
    if assets.scene_ir != scene_ref:
        raise ValueError("revision_assets_mismatch")
    prior = [json.loads(store.read_artifact(ref)) for ref in history]
    if len(history) != len(set(r.sha256 for r in history)) or any(
        row.get("schema_version") != "x2env.revision.v1"
        or row.get("input_sha256") != scene.input_sha256
        or type(row.get("cost")) is not int
        or row["cost"] not in (1, 2)
        for row in prior
    ):
        raise ValueError("revision_history_mismatch")
    if any(row["failure_fingerprint"] == failure_fingerprint for row in prior):
        raise ValueError("repeated_failure")
    cost = int(bool(proposal.scene_patches)) + len(proposal.asset_patches)
    if cost < 1 or sum(row["cost"] for row in prior) + cost > 2:
        raise ValueError("revision_budget_exhausted")
    asset_by_id = {a.entity_id: a for a in assets.assets}
    asset_ids = [p.entity_id for p in proposal.asset_patches]
    if len(set(asset_ids)) != len(asset_ids):
        raise ValueError("duplicate_asset_patch")
    for patch in proposal.asset_patches:
        if (
            patch.entity_id not in asset_by_id
            or asset_by_id[patch.entity_id].version_sha256 != patch.parent_version
        ):
            raise ValueError("asset_revision_base_mismatch")
        if set(patch.patch.model_dump(exclude_none=True)) - {
            "base_color", "color_mode", "mass", "friction"
        }:
            raise ValueError("unsupported_asset_patch")
    document = scene.model_dump(mode="json")
    entities = {entity["id"]: entity for entity in document["entities"]}
    seen = set()
    for patch in proposal.scene_patches:
        if patch.entity_id in seen or patch.entity_id not in entities:
            raise ValueError("duplicate_or_unknown_patch_entity")
        seen.add(patch.entity_id)
        if patch.joints:
            raise ValueError("unsupported_joint_revision")
        entity = entities[patch.entity_id]
        if patch.pose is not None:
            # Changing frame with a partial vector would reinterpret untouched coordinates.
            if patch.pose.frame != entity["pose"]["frame"]:
                raise ValueError("unsupported_frame_revision")
            entity["pose"]["position"] = [
                old if new is None else new
                for old, new in zip(entity["pose"]["position"], patch.pose.position, strict=True)
            ]
            if patch.pose.yaw_degrees is not None:
                entity["pose"]["yaw_degrees"] = patch.pose.yaw_degrees
    if document == scene.model_dump(mode="json") and not proposal.asset_patches:
        raise ValueError("revision_has_no_effect")
    for entity_id in seen:
        for row in entities[entity_id]["provenance"]["pose"]:
            row["kind"] = "override"
    document["revision"] += 1
    revised = SceneIR.model_validate_json(json.dumps(document))
    output = Path(output_root)
    if not output.is_absolute() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("unsafe_revision_output")
    output.mkdir(parents=True, exist_ok=False)
    asset_receipts = []
    for index, patch in enumerate(proposal.asset_patches):
        approved = {
            "authority": "harness_controller",
            "approved": True,
            "parent_version": patch.parent_version,
            "patch": patch.patch.model_dump(mode="json", exclude_none=True),
            "diagnosis": diagnosis_ref.model_dump(mode="json"),
            "base_scene": scene_ref.model_dump(mode="json"),
        }
        approval = store.write_artifact(json.dumps(approved).encode(), "application/json")
        child = AssetRevision(registry, store).revise(
            patch.parent_version,
            patch.patch,
            approval=approval,
            output_root=output / f"asset-{index}",
        )
        asset_by_id[patch.entity_id] = asset_by_id[patch.entity_id].model_copy(
            update={"version_sha256": child.version_sha256}
        )
        asset_receipts.append(child.receipt)
    ref = store.write_artifact(revised.model_dump_json().encode(), "application/json")
    resolved = ResolvedAssetSet(
        scene_ir=ref, assets=tuple(asset_by_id[a.entity_id] for a in assets.assets)
    )
    receipt = {
        "schema_version": "x2env.revision.v1",
        "input_sha256": scene.input_sha256,
        "base_scene": scene_ref.model_dump(mode="json"),
        "scene_ir": ref.model_dump(mode="json"),
        "diagnosis": diagnosis_ref.model_dump(mode="json"),
        "cost": cost,
        "failure_fingerprint": failure_fingerprint,
        "history": [r.model_dump(mode="json") for r in history],
        "resolved_assets": resolved.model_dump(mode="json"),
        "physical_evaluated": False,
        "asset_receipts": [r.model_dump(mode="json") for r in asset_receipts],
    }
    raw = json.dumps(receipt, sort_keys=True).encode()
    (output / "receipt.json").write_bytes(raw)
    return RevisionResult(
        scene_ir=ref, assets=resolved, receipt=store.write_artifact(raw, "application/json")
    )
