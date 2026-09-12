"""Materialize a development review package from canonical immutable artifacts.

This exporter never grants sim-ready from caller-supplied report fields. It owns no
workflow or publication authority. Kernel provenance: canonical genesis_child,
Harness c0236bd and fixed Gujie eb0b710 standard-URDF audit semantics.
"""

import hashlib
import json
from pathlib import Path

from .artifacts import artifact_closure
from .compile import CompiledScene
from .contracts import ArtifactRef
from .package_loader import _safe, _write, verify_package

MAX_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 2048


def build_package(
    compiled,
    *,
    registry,
    store,
    input_refs,
    replay_refs,
    assessment_ref,
    output,
    evidence_refs=None,
):
    """Export explicit typed-ref graph, not arbitrary hash-looking JSON strings."""
    compiled = CompiledScene.model_validate(compiled)
    output = Path(output).absolute()
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("package output must be nonsymbolic")
    output.mkdir(parents=True, exist_ok=False)
    members = {}
    artifact_index = {}
    versions = []
    total = 0

    def put(name, data):
        nonlocal total
        if name in members:
            if members[name]["sha256"] != hashlib.sha256(data).hexdigest():
                raise ValueError("conflicting package member")
            return
        total += len(data)
        if total > MAX_BYTES or len(members) >= MAX_MEMBERS:
            raise ValueError("package explicit closure exceeds budget")
        path = _safe(output, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        members[name] = {
            "path": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }

    def read(ref):
        ref = ArtifactRef.model_validate(ref)
        if ref.size_bytes > MAX_BYTES - total:
            raise ValueError("artifact graph exceeds byte budget")
        data = store.read_artifact(ref)
        if len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ValueError("CAS artifact differs")
        return ref, data

    def collect(ref):
        ref = ArtifactRef.model_validate(ref)
        key = (ref.sha256, ref.media_type)
        if key in artifact_index:
            return artifact_index[key]["path"]
        ref, data = read(ref)
        suffix = {
            "application/json": ".json",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "video/mp4": ".mp4",
            "video/x-matroska": ".mkv",
            "text/plain": ".txt",
            "application/x-ndjson": ".ndjson",
        }.get(ref.media_type, ".bin")
        name = f"evidence/{ref.sha256}{suffix}"
        put(name, data)
        artifact_index[key] = {"ref": ref.model_dump(), "path": name}
        return name

    try:
        graph_roots = [compiled.scene_ir, compiled.receipt]
        for refs in (input_refs, replay_refs, evidence_refs or {}):
            graph_roots.extend(ArtifactRef.model_validate(ref) for ref in refs.values())
        if assessment_ref is not None:
            graph_roots.append(ArtifactRef.model_validate(assessment_ref))
        audited_versions = set()

        def audit_version(version, chain=()):
            if version.version_sha256 in chain or len(chain) >= 32:
                raise ValueError("invalid or excessive parent asset lineage")
            if version.version_sha256 in audited_versions:
                return
            audited_versions.add(version.version_sha256)
            versions.append(version.model_dump(mode="json"))
            graph_roots.extend(member.artifact for member in version.files)
            graph_roots.extend(
                [
                    version.normalization_report,
                    version.license.evidence,
                    version.source.evidence,
                    version.receipt,
                ]
            )
            if version.parent_version:
                audit_version(
                    registry.inspect(version.parent_version), (*chain, version.version_sha256)
                )

        scene = compiled.runtime_scene
        if compiled.scene_ir.sha256 != scene.scene_ir_sha256:
            raise ValueError("compiled sceneIR identity differs")
        rigid = {entity.id: entity for entity in scene.entities if entity.kind == "rigid"}
        resolved = compiled.resolved_assets.assets
        if (
            compiled.resolved_assets.scene_ir != compiled.scene_ir
            or len(resolved) != len(rigid)
            or {asset.entity_id for asset in resolved} != set(rigid)
            or any(
                rigid[asset.entity_id].version_sha256 != asset.version_sha256 for asset in resolved
            )
        ):
            raise ValueError("compiled resolved assets differ from runtime scene")
        wanted = {m.path: m for m in scene.members}
        copied = set()
        for asset in compiled.resolved_assets.assets:
            version = registry.inspect(asset.version_sha256)
            if version.version_sha256 != asset.version_sha256:
                raise ValueError("asset version identity differs")
            audit_version(version)
            prefix = f"assets/{asset.entity_id}/{version.version_sha256}"
            for member in version.files:
                name = f"{prefix}/{member.path}"
                ref, data = read(member.artifact)
                if (
                    name not in wanted
                    or wanted[name].sha256 != ref.sha256
                    or wanted[name].size_bytes != ref.size_bytes
                ):
                    raise ValueError("compiled member differs from immutable asset version")
                put(name, data)
                copied.add(name)
                artifact_index[(ref.sha256, ref.media_type)] = {
                    "ref": ref.model_dump(),
                    "path": name,
                }
        if copied != set(wanted):
            raise ValueError("compiled asset closure incomplete")
        for ref in artifact_closure(
            store, graph_roots, max_bytes=256 * 1024 * 1024, max_refs=MAX_MEMBERS
        ):
            collect(ref)
        put("scene.json", scene.model_dump_json().encode())
        put("compiled.json", compiled.model_dump_json().encode())
        put("asset-versions.json", json.dumps(versions, sort_keys=True).encode())
        roots = {
            "scene_ir": collect(compiled.scene_ir),
            "compile_receipt": collect(compiled.receipt),
        }
        for group, refs in [
            ("inputs", input_refs),
            ("replay", replay_refs),
            ("additional", evidence_refs or {}),
        ]:
            roots[group] = {str(name): collect(ref) for name, ref in refs.items()}
        if assessment_ref is not None:
            roots["assessment"] = collect(assessment_ref)
        for filename in ["genesis_child.py", "package_loader.py"]:
            put(filename, Path(__file__).with_name(filename).read_bytes())
        runtime = {
            "python": "3.12",
            "python_observed": "3.12.3",
            "genesis_version": "1.3.3",
            "genesis_commit_declared": "0e74bf392781884ccad765c3f344419c86b872ca",
            "roles": ["interpreter", "stdlib", "distributions", "native", "genesis"],
            "external_runtime_required": True,
            "runtime_copied": False,
            "profiles": ["baseline", "half_dt", "load_step_smoke"],
            "renderer": "OSMesa llvmpipe",
        }
        put("runtime.json", json.dumps(runtime, sort_keys=True).encode())
        put(
            "README.txt",
            b"Run: python package_loader.py --runtime /absolute/runtime_roots.json "
            b"--output /absolute/NEW --profile baseline --deny-root /absolute/OLD\n"
            b"Development review package; no qualification, robot policy or data collection "
            b"capability.\n",
        )
        manifest = {
            "schema_version": "x2env.development_package.v1",
            "status": "development_review",
            "sim_ready": False,
            "robot_policy_evaluated": False,
            "data_collection_evaluated": False,
            "scene": "scene.json",
            "entrypoint": "package_loader.py",
            "child": "genesis_child.py",
            "members": sorted(members.values(), key=lambda m: m["path"]),
            "artifact_index": list(artifact_index.values()),
            "roots": roots,
            "checks": {
                "member_integrity": "passed",
                "copy_run": "not_run",
                "physical_profile": "not_run",
                "visual_intent": "not_run",
                "parent_asset_integrity": "passed"
                if any(v["parent_version"] for v in versions)
                else "not_run",
            },
            "capability_basis": "review_export_not_workflow_promotion",
        }
        _write(output / "manifest.json", manifest)
        verify_package(output)
        return manifest
    except Exception as exc:
        _write(
            output / "export-failure.json",
            {"status": "failed", "reason": str(exc), "completed_members": list(members)},
        )
        raise
