"""Controller-approved immutable asset revisions; no retry or physical authority."""

import hashlib
import json
import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from .contracts import ArtifactRef, Model


class ArtifactStore(Protocol):
    def read_artifact(self, ref: ArtifactRef) -> bytes: ...
    def write_artifact(self, data: bytes, media_type: str) -> ArtifactRef: ...


Unit = Annotated[float, Field(ge=0, le=1)]


class AssetPatch(Model):
    base_color: tuple[Unit, Unit, Unit, Unit] | None = None
    color_mode: Literal["uniform_replace"] | None = None
    mass: float | None = Field(default=None, gt=0)
    friction: float | None = Field(default=None, ge=0)
    roughness: Unit | None = None
    metallic: Unit | None = None
    uniform_scale: float | None = Field(default=None, gt=0)
    density: float | None = Field(default=None, gt=0)
    restitution: Unit | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.color_mode is not None and self.base_color is None:
            raise ValueError("color_mode requires base_color")
        if not self.model_dump(exclude_none=True):
            raise ValueError("asset patch must change at least one field")
        return self


def _validate_color_layers(document, tail, contents, member):
    """Validate layers before removal: replacement cannot launder dangling color inputs."""
    import base64
    from io import BytesIO

    from PIL import Image

    def at(group, index):
        values = document.get(group, [])
        if type(index) is not int or not 0 <= index < len(values):
            raise ValueError("asset_patch_invalid_color_reference")
        return values[index]

    def uri_bytes(uri):
        if uri.startswith("data:"):
            if ";base64," not in uri:
                raise ValueError("asset_patch_invalid_color_reference")
            return base64.b64decode(uri.split(",", 1)[1], validate=True)
        path = Path(uri)
        if path.is_absolute() or ".." in path.parts or "\\" in uri:
            raise ValueError("asset_patch_invalid_color_reference")
        name = str(Path(member).parent / path)
        if name not in contents:
            raise ValueError("asset_patch_invalid_color_reference")
        return contents[name]

    def view_bytes(index):
        view = at("bufferViews", index)
        buffer = at("buffers", view.get("buffer"))
        if "uri" in buffer:
            raw = uri_bytes(buffer["uri"])
        else:
            if view["buffer"] != 0 or len(tail) < 8:
                raise ValueError("asset_patch_invalid_color_reference")
            size, kind = struct.unpack_from("<II", tail)
            if kind != 0x004E4942 or size != len(tail) - 8:
                raise ValueError("asset_patch_invalid_color_reference")
            raw = tail[8:]
        offset, size = view.get("byteOffset", 0), view.get("byteLength")
        declared = buffer.get("byteLength")
        if (
            type(declared) is not int
            or not 0 <= declared <= len(raw)
            or type(offset) is not int
            or type(size) is not int
            or offset < 0
            or size <= 0
            or offset + size > declared
        ):
            raise ValueError("asset_patch_invalid_color_reference")
        return raw[offset : offset + size]

    def accessor(index, types):
        value = at("accessors", index)
        if value.get("type") not in types or value.get("sparse"):
            raise ValueError("asset_patch_invalid_color_reference")
        components = {"VEC2": 2, "VEC3": 3, "VEC4": 4}[value["type"]]
        unit = {5121: 1, 5123: 2, 5126: 4}.get(value.get("componentType"))
        count, offset = value.get("count"), value.get("byteOffset", 0)
        raw = view_bytes(value.get("bufferView"))
        view = at("bufferViews", value["bufferView"])
        stride = view.get("byteStride", components * unit if unit else 0)
        if (
            unit is None
            or type(count) is not int
            or count <= 0
            or type(offset) is not int
            or offset < 0
            or type(stride) is not int
            or stride < components * unit
            or offset + (count - 1) * stride + components * unit > len(raw)
        ):
            raise ValueError("asset_patch_invalid_color_reference")
        return count

    for material in document.get("materials", []):
        texture_info = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if texture_info is None:
            continue
        if texture_info.get("extensions"):
            raise ValueError("unsupported_asset_patch_color_texture")
        texture = at("textures", texture_info.get("index"))
        if texture.get("extensions"):
            raise ValueError("unsupported_asset_patch_color_texture")
        if "sampler" in texture:
            at("samplers", texture["sampler"])
        image = at("images", texture.get("source"))
        data = uri_bytes(image["uri"]) if "uri" in image else view_bytes(image.get("bufferView"))
        try:
            with Image.open(BytesIO(data)) as decoded:
                decoded.verify()
        except OSError as exc:
            raise ValueError("asset_patch_invalid_color_image") from exc
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            attrs = primitive.get("attributes", {})
            count = at("accessors", attrs.get("POSITION")).get("count")
            if "COLOR_0" in attrs and accessor(attrs["COLOR_0"], {"VEC3", "VEC4"}) != count:
                raise ValueError("asset_patch_invalid_color_reference")
            if "material" in primitive:
                material = at("materials", primitive["material"])
                texture = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
                if texture is not None:
                    coord = texture.get("texCoord", 0)
                    if (
                        type(coord) is not int
                        or coord < 0
                        or accessor(attrs.get(f"TEXCOORD_{coord}"), {"VEC2"}) != count
                    ):
                        raise ValueError("asset_patch_invalid_color_reference")


class AssetRevision:
    def __init__(self, registry, store: ArtifactStore):
        self.registry, self.store = registry, store

    def revise(
        self, parent_version: str, patch: AssetPatch, *, approval: ArtifactRef, output_root: Path
    ):
        patch = AssetPatch.model_validate_json(patch.model_dump_json())
        changes = patch.model_dump(mode="json", exclude_none=True)
        if set(changes) - {"base_color", "color_mode", "mass", "friction"}:
            raise ValueError("unsupported_asset_patch")
        approved = json.loads(self.store.read_artifact(approval))
        if (
            approved.get("authority") != "harness_controller"
            or approved.get("approved") is not True
            or approved.get("parent_version") != parent_version
            or approved.get("patch") != changes
        ):
            raise ValueError("asset_patch_approval_mismatch")
        parent = self.registry.inspect(parent_version)
        contents = {
            member.path: self.store.read_artifact(member.artifact) for member in parent.files
        }
        original_contents = dict(contents)
        root = Path(output_root)
        if (
            not root.is_absolute()
            or any(p.is_symlink() for p in (root, *root.parents))
            or root.exists()
        ):
            raise ValueError("asset_patch_requires_new_safe_output")
        for name in contents:
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("asset_patch_unsafe_member")
        physics = json.loads(contents["physics.json"])
        tree = ET.fromstring(contents[parent.entrypoint])
        physics_changed = False
        if patch.mass is not None:
            inertials = tree.findall(".//inertial")
            if len(inertials) != 1:
                raise ValueError("unsupported_asset_patch_multi_body")
            mass = inertials[0].find("mass")
            inertia = inertials[0].find("inertia")
            if mass is None or inertia is None:
                raise ValueError("asset_patch_missing_inertia")
            old_mass = float(mass.attrib["value"])
            if not math.isfinite(old_mass) or old_mass <= 0 or physics.get("mass_kg") != old_mass:
                raise ValueError("asset_patch_inconsistent_mass")
            ratio = patch.mass / old_mass
            original_inertia = {
                name: float(inertia.attrib[name])
                for name in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")
            }
            mass.set("value", str(patch.mass))
            for name in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz"):
                value = float(inertia.attrib[name]) * ratio
                if not math.isfinite(value):
                    raise ValueError("asset_patch_invalid_inertia")
                inertia.set(name, str(value))
            matrix = physics.get("inertia_kg_m2")
            if (
                not isinstance(matrix, list)
                or len(matrix) != 3
                or any(len(row) != 3 for row in matrix)
            ):
                raise ValueError("asset_patch_missing_inertia")
            for name, i, j in (
                ("ixx", 0, 0),
                ("ixy", 0, 1),
                ("ixz", 0, 2),
                ("iyy", 1, 1),
                ("iyz", 1, 2),
                ("izz", 2, 2),
            ):
                if (
                    not math.isfinite(matrix[i][j])
                    or matrix[i][j] != original_inertia[name]
                    or matrix[i][j] != matrix[j][i]
                ):
                    raise ValueError("asset_patch_inconsistent_inertia")
            physics["inertia_kg_m2"] = [[value * ratio for value in row] for row in matrix]
            physics["mass_kg"] = patch.mass
            if patch.mass != old_mass:
                physics_changed = True
                contents[parent.entrypoint] = ET.tostring(
                    tree, encoding="utf-8", xml_declaration=True
                )
        if patch.friction is not None and patch.friction != physics.get("friction"):
            physics["friction"] = patch.friction
            physics_changed = True
        if physics_changed:
            physics["parameter_basis"] = "controller_supplied_revision_not_measured"
            contents["physics.json"] = json.dumps(physics, sort_keys=True).encode()
        color_replacements = []
        if patch.base_color is not None:
            visuals = tree.findall(".//visual/geometry/mesh")
            names = {
                str(Path(parent.entrypoint).parent / node.attrib["filename"]) for node in visuals
            }
            if not names or any(Path(name).suffix.lower() != ".glb" for name in names):
                raise ValueError("unsupported_asset_patch_visual_format")
            for name in names:
                data = contents[name]
                if len(data) < 20 or data[:4] != b"glTF":
                    raise ValueError("asset_patch_invalid_glb")
                version, size, json_size, kind = struct.unpack_from("<IIII", data, 4)
                if version != 2 or size != len(data) or kind != 0x4E4F534A:
                    raise ValueError("asset_patch_invalid_glb")
                document = json.loads(data[20 : 20 + json_size])
                replaced = {"member": name, "baseColorTexture": 0, "COLOR_0": 0}
                if patch.color_mode == "uniform_replace":
                    _validate_color_layers(document, data[20 + json_size :], contents, name)
                if (
                    any(
                        "COLOR_0" in primitive.get("attributes", {})
                        for mesh in document.get("meshes", [])
                        for primitive in mesh.get("primitives", [])
                    )
                    and patch.color_mode is None
                ):
                    raise ValueError("unsupported_asset_patch_vertex_color")
                materials = document.setdefault("materials", [])
                if any(
                    (
                        "baseColorTexture" in material.get("pbrMetallicRoughness", {})
                        and patch.color_mode is None
                    )
                    or material.get("extensions")
                    for material in materials
                ):
                    raise ValueError("unsupported_asset_patch_color_texture")
                if patch.color_mode == "uniform_replace":
                    for material in materials:
                        pbr = material.get("pbrMetallicRoughness", {})
                        if "baseColorTexture" in pbr:
                            del pbr["baseColorTexture"]
                            replaced["baseColorTexture"] += 1
                    for mesh in document.get("meshes", []):
                        for primitive in mesh.get("primitives", []):
                            attributes = primitive.get("attributes", {})
                            if "COLOR_0" in attributes:
                                del attributes["COLOR_0"]
                                replaced["COLOR_0"] += 1
                    color_replacements.append(replaced)
                if all(
                    material.get("pbrMetallicRoughness", {}).get("baseColorFactor", [1, 1, 1, 1])
                    == list(patch.base_color)
                    for material in materials or [{}]
                ) and not (replaced["baseColorTexture"] or replaced["COLOR_0"]):
                    continue
                if not materials:
                    materials.append({})
                for material in materials:
                    material.setdefault("pbrMetallicRoughness", {})["baseColorFactor"] = list(
                        patch.base_color
                    )
                    material["alphaMode"] = "BLEND" if patch.base_color[3] < 1 else "OPAQUE"
                for mesh in document.get("meshes", []):
                    for primitive in mesh.get("primitives", []):
                        primitive.setdefault("material", 0)
                encoded = json.dumps(document, separators=(",", ":")).encode()
                encoded += b" " * ((-len(encoded)) % 4)
                remainder = data[20 + json_size :]
                contents[name] = (
                    struct.pack(
                        "<4sIIII",
                        b"glTF",
                        2,
                        20 + len(encoded) + len(remainder),
                        len(encoded),
                        kind,
                    )
                    + encoded
                    + remainder
                )
        if contents == original_contents:
            raise ValueError("no_effect_asset_patch")
        report = json.loads(self.store.read_artifact(parent.normalization_report))
        report["files"] = [
            {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
            for name, data in sorted(contents.items())
        ]
        report["asset_revision"] = {
            "parent_version": parent_version,
            "patch": changes,
            "approval": approval.model_dump(),
            "basis": "supplied_not_measured",
            "color_replacement": color_replacements,
        }
        report["checks"] = {
            **report.get("checks", {}),
            "physical_profile": "not_run",
            "genesis_load_step": "not_run",
            "visual_intent": "not_run",
        }
        report_ref = self.store.write_artifact(
            json.dumps(report, sort_keys=True).encode(), "application/json"
        )
        receipt = self.store.write_artifact(
            json.dumps(
                {
                    "parent_version": parent_version,
                    "attribute_provenance": {
                        key: {
                            "kind": "controller_approved_revision",
                            "approval": approval.model_dump(),
                        }
                        for key in changes
                    },
                    "patch": changes,
                    "normalization_report": report_ref.model_dump(),
                    "approval_trust": "delegated_to_harness",
                    "physical_evaluated": False,
                    "visual_intent_evaluated": False,
                    "color_replacement": color_replacements,
                },
                sort_keys=True,
            ).encode(),
            "application/json",
        )
        root.mkdir(parents=True, exist_ok=False)
        for name, data in contents.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (root / "normalization.json").write_bytes(self.store.read_artifact(report_ref))
        return self.registry.register(
            parent.asset_id,
            parent.category,
            root,
            parent.entrypoint,
            files=tuple(contents),
            normalization_report=report_ref,
            license=parent.license,
            source=parent.source,
            receipt=receipt,
            parent_version=parent_version,
        )
