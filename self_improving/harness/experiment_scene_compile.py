"""Harness-owned single-body intent compilation through the measured tabletop adapter."""

import hashlib
import io
import json
import math
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .experiment_assets import AssetVersion
from .gujie_scene_adapter import adapt_gujie_tabletop_scene
from .schemas.common import ArtifactRef
from .schemas.x2env_compile_evidence import (
    AssetModelSubjectV1,
    AssetRepresentationMemberV1,
    AssetRepresentationV1,
    asset_loader_closure_sha256,
    canonical_evidence_sha256,
)


@dataclass(frozen=True)
class CompiledExperimentScene:
    scene_ref: ArtifactRef
    representation_ref: ArtifactRef
    receipt_ref: ArtifactRef


def _bytes(value, newline=False):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + ("\n" if newline else "")
    ).encode()


def _put(store, payload, name, media="application/json", schema=None):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "payload"
        path.write_bytes(payload)
        return store.put_file(path, name=name, media_type=media, schema_version=schema)


def compile_scene(store, input_ref, intent: dict, asset: AssetVersion) -> CompiledExperimentScene:
    """Compile actual immutable bytes; no asset qualification or simulator claim."""
    import trimesh
    from trimesh.exchange.obj import export_obj

    if set(intent) != {
        "category",
        "color",
        "source",
        "selection",
        "dimensions_m",
        "xy_m",
        "yaw_deg",
        "support",
        "object_count",
        "input_sha256",
        "unknowns",
    }:
        raise ValueError("unsupported intent fields")
    store.resolve(input_ref)
    if intent.get("input_sha256") != input_ref.sha256:
        raise ValueError("intent input binding mismatch")
    if (
        intent.get("support") != "table"
        or intent.get("object_count") != 1
        or intent.get("unknowns")
    ):
        raise ValueError("requires resolved single-body tabletop intent")
    xy = intent["xy_m"]
    yaw = intent["yaw_deg"]
    if len(xy) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in [*xy, yaw]):
        raise ValueError("invalid layout numbers")
    raw = (asset.root / "receipt.json").read_bytes()
    receipt = json.loads(raw)
    if hashlib.sha256(raw).hexdigest() != asset.version_id:
        raise ValueError("asset receipt changed")
    if any(
        intent.get(k) != receipt.get(v)
        for k, v in (
            ("category", "category"),
            ("source", "source_kind"),
            ("selection", "selection_strategy"),
        )
    ):
        raise ValueError("intent differs from acquired asset")
    files = {}
    for member in receipt["members"]:
        path = asset.root / member["path"]
        if path.is_symlink() or path.resolve().parent != asset.root.resolve():
            raise ValueError("asset member path escape")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != member["sha256"]:
            raise ValueError("asset member changed")
        files[member["path"]] = payload
    visual = trimesh.load(io.BytesIO(files["visual.glb"]), file_type="glb", force="mesh")
    uniform_scale = 1.0
    dimensions = intent.get("dimensions_m")
    if dimensions is not None:
        if len(dimensions) != 3 or any(
            type(v) not in (float, int) or not math.isfinite(v) or v <= 0 for v in dimensions
        ):
            raise ValueError("invalid requested dimensions")
        ratios = [float(want) / float(actual) for want, actual in zip(dimensions, visual.extents)]
        if max(ratios) - min(ratios) > 1e-5:
            raise ValueError("anisotropic asset scaling is unsupported")
        uniform_scale = ratios[0]
        if not 0.01 <= uniform_scale <= 100:
            raise ValueError("uniform scale out of bounds")
    # Measure/render identical transformed geometry. URDF origin remains at the original bottom.
    visual.apply_scale(uniform_scale)
    obj, textures = export_obj(visual, return_texture=True, write_texture=False, mtl_name="visual")
    members_bytes = {"asset/urdf/visual.obj": obj.encode()}
    for name, data in (textures or {}).items():
        if Path(name).name != name:
            raise ValueError("material path escape")
        members_bytes[f"asset/urdf/{name}"] = data.encode() if isinstance(data, str) else data
    collision = trimesh.load(io.BytesIO(files["collision.obj"]), file_type="obj", force="mesh")
    collision.apply_scale(uniform_scale)
    members_bytes["asset/urdf/collision.obj"] = collision.export(file_type="obj").encode()
    tree = ET.fromstring(files["asset.urdf"])
    tree.find("link/visual/geometry/mesh").set("filename", "visual.obj")
    if uniform_scale != 1:
        origin = tree.find("link/inertial/origin")
        origin.set(
            "xyz", " ".join(str(float(v) * uniform_scale) for v in origin.get("xyz").split())
        )
        inertia = tree.find("link/inertial/inertia")
        for key, value in inertia.attrib.items():
            inertia.set(key, str(float(value) * uniform_scale**2))
    members_bytes["asset/urdf/asset.urdf"] = ET.tostring(tree)
    members_bytes["asset/physics.json"] = _bytes(
        {"mass_kg": receipt["mass_kg"], "friction": receipt["friction"]}
    )
    members_bytes["asset/source-receipt.json"] = raw
    members = tuple(
        AssetRepresentationMemberV1(
            logical_path=name,
            role="urdf"
            if name.endswith(".urdf")
            else "collision"
            if name.endswith("collision.obj")
            else "metadata"
            if name.endswith(".json")
            else "visual",
            artifact=_put(store, data, Path(name).name, "application/octet-stream", None),
        )
        for name, data in sorted(members_bytes.items())
    )
    subject = AssetModelSubjectV1(
        namespace="experiment",
        asset_key=asset.version_id,
        ledger_asset_id="experiment_" + asset.version_id,
        model_id=0,
        backend="genesis",
        source_record_sha256=asset.version_id,
    )
    body = dict(
        schema_version="harness.asset_representation.v1",
        representation_sha256="0" * 64,
        subject=subject.model_dump(mode="json"),
        representation_kind="urdf",
        root_logical_path="asset/urdf/asset.urdf",
        loader_scale=(1.0, 1.0, 1.0),
        loader_closure_sha256=asset_loader_closure_sha256(
            subject, "asset/urdf/asset.urdf", members
        ),
        members=[m.model_dump(mode="json") for m in members],
        collision_member_paths=("asset/urdf/collision.obj",),
        visual_member_paths=("asset/urdf/visual.obj",),
    )
    body["representation_sha256"] = canonical_evidence_sha256(body, "representation_sha256")
    representation = AssetRepresentationV1.model_validate(body)
    representation_ref = _put(
        store,
        _bytes(representation.model_dump(mode="json"), True),
        "experiment-representation.json",
        schema=representation.schema_version,
    )
    angle = math.radians(yaw) / 2
    source = {
        "representation": representation_ref.model_dump(mode="json"),
        "metadata": {"name": "object", "category": receipt["category"], "model": asset.version_id},
        "relation": {"relation": "on_table", "source": "object", "target": "table"},
        "scene": {
            "objects_info": {
                "init_info": {
                    "object": {
                        "args": {
                            "name": "object",
                            "category": receipt["category"],
                            "model": asset.version_id,
                        }
                    }
                }
            },
            "state": {
                "registry": {
                    "object_registry": {
                        "object": {
                            "root_link": {
                                "pos": [*xy, 0.0],
                                "ori": [0.0, 0.0, math.sin(angle), math.cos(angle)],
                            }
                        }
                    }
                }
            },
        },
    }
    source_ref = _put(store, _bytes(source), "experiment-source.json")
    scene_ref = adapt_gujie_tabletop_scene(
        store,
        source_ref=source_ref,
        representation_ref=representation_ref,
        table_center_m=(0.0, 0.0, 0.35),
        table_size_m=(1.2, 1.0, 0.1),
        table_friction=0.6,
    )
    record = {
        "schema_version": "harness.experiment_compile.v1",
        "input": input_ref.model_dump(mode="json"),
        "intent": intent,
        "asset_version": asset.version_id,
        "representation": representation_ref.model_dump(mode="json"),
        "scene": scene_ref.model_dump(mode="json"),
        "uniform_scale": uniform_scale,
        "scale_basis": "requested_uniform_estimate" if uniform_scale != 1 else "measured_asset",
        "checks": {"static_compile": "passed", "physics": "not_run"},
        "qualified": False,
    }
    return CompiledExperimentScene(
        scene_ref, representation_ref, _put(store, _bytes(record), "experiment-compile.json")
    )
