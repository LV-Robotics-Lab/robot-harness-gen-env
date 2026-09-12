"""Immutable asset versions in the existing Store, without physical authority."""

import hashlib
import json
import mimetypes
import shlex
import struct
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .contracts import ArtifactRef, Model, Sha256


class AssetStore(Protocol):
    def read_artifact(self, ref: ArtifactRef) -> bytes: ...
    def write_artifact(self, data: bytes, media_type: str) -> ArtifactRef: ...
    def register_asset(
        self, version_sha256: str, asset_id: str, category: str, record_json: str
    ) -> None: ...
    def asset_version(self, sha: str) -> str: ...
    def asset_versions(self, category: str) -> tuple[str, ...]: ...


class AssetLicense(Model):
    spdx: Literal["CC0-1.0", "CC-BY-4.0"]
    attribution: str | None = None
    source_url: str = Field(min_length=1)
    evidence: ArtifactRef

    @model_validator(mode="after")
    def attribution_required(self):
        if self.spdx == "CC-BY-4.0" and not (self.attribution or "").strip():
            raise ValueError("CC-BY requires original author attribution")
        return self


class AssetSource(Model):
    kind: Literal["local", "web", "reconstruction"]
    provider: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    evidence: ArtifactRef


class AssetFile(Model):
    path: str
    artifact: ArtifactRef


class AssetVersion(Model):
    schema_version: Literal["x2env.asset_version.v1"] = "x2env.asset_version.v1"
    version_sha256: Sha256
    asset_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    entrypoint: str
    files: tuple[AssetFile, ...]
    geometry_sha256: Sha256
    normalization_report: ArtifactRef
    license: AssetLicense
    source: AssetSource
    receipt: ArtifactRef
    parent_version: Sha256 | None = None
    physical_evaluated: Literal[False] = False
    sim_ready: Literal[False] = False


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _logical(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or str(path) != name or "\\" in name:
        raise ValueError("invalid relative asset path")
    return path


class AssetRegistry:
    def __init__(self, store: AssetStore):
        self.store = store

    def register(
        self,
        asset_id: str,
        category: str,
        normalized_root: Path,
        entrypoint: str,
        *,
        files: tuple[str, ...],
        normalization_report: ArtifactRef,
        license: AssetLicense,
        source: AssetSource,
        receipt: ArtifactRef,
        parent_version: str | None = None,
    ):
        root = Path(normalized_root)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("normalized root must be absolute and non-symbolic")
        if not files or len(set(files)) != len(files) or entrypoint not in files:
            raise ValueError("invalid declared file closure")
        contents = {}
        for name in sorted(files):
            path = root / str(_logical(name))
            if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
                raise ValueError("asset member must be a regular non-symbolic file")
            contents[name] = path.read_bytes()
        if Path(entrypoint).suffix != ".urdf":
            raise ValueError("asset entrypoint must be URDF")
        raw = contents[entrypoint]
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            raise ValueError("XML declarations are not allowed")
        tree = ET.fromstring(raw)
        for element in tree.iter():
            if "filename" in element.attrib:
                reference = str(
                    PurePosixPath(entrypoint).parent / _logical(element.attrib["filename"])
                )
                if reference not in contents:
                    raise ValueError("asset dependency missing from closure")
        for name, data in contents.items():
            references = []
            suffix = Path(name).suffix.lower()
            if suffix in {".gltf", ".dae"}:
                raise ValueError("unimplemented normalized mesh reference format")
            if suffix in {".obj", ".mtl"}:
                for line in data.decode().splitlines():
                    tokens = shlex.split(line, comments=True)
                    if tokens and tokens[0] == "mtllib":
                        references.extend(tokens[1:])
                    elif tokens and (
                        tokens[0].lower().startswith("map_")
                        or tokens[0].lower() in {"bump", "disp", "decal", "refl"}
                    ):
                        if len(tokens) != 2:
                            raise ValueError(
                                "texture options require normalization before registry"
                            )
                        references.append(tokens[1])
            elif suffix == ".glb":
                if len(data) < 20 or data[:4] != b"glTF":
                    raise ValueError("invalid GLB member")
                version, length, json_length, chunk_type = struct.unpack_from("<IIII", data, 4)
                if version != 2 or length != len(data) or chunk_type != 0x4E4F534A:
                    raise ValueError("invalid GLB header")
                document = json.loads(data[20 : 20 + json_length])
                references.extend(
                    item["uri"]
                    for group in ("buffers", "images")
                    for item in document.get(group, [])
                    if "uri" in item and not item["uri"].startswith("data:")
                )
            for reference in references:
                logical = str(PurePosixPath(name).parent / _logical(reference))
                if logical not in contents:
                    raise ValueError("mesh dependency missing from closure")
        report = json.loads(self.store.read_artifact(normalization_report))
        expected = [
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
            for name, raw in contents.items()
        ]
        if (
            report.get("schema_version") != "x2env.normalization.v1"
            or report.get("status") != "passed"
            or report.get("entrypoint") != entrypoint
            or sorted(report.get("files", []), key=lambda f: f["path"]) != expected
        ):
            raise ValueError("normalization report does not bind exact asset closure")
        for ref in (license.evidence, source.evidence, receipt):
            self.store.read_artifact(ref)
        if parent_version:
            parent = self.inspect(parent_version)
            if parent.asset_id != asset_id or parent.category != category:
                raise ValueError("parent belongs to another asset")
        geometry = [
            row
            for row in expected
            if Path(row["path"]).suffix.lower() in {".obj", ".stl", ".ply", ".glb", ".gltf", ".dae"}
        ]
        if not geometry:
            raise ValueError("asset requires explicit mesh geometry")
        members = tuple(
            AssetFile(
                path=name,
                artifact=self.store.write_artifact(
                    raw, mimetypes.guess_type(name)[0] or "application/octet-stream"
                ),
            )
            for name, raw in contents.items()
        )
        version = AssetVersion(
            version_sha256="0" * 64,
            asset_id=asset_id,
            category=category,
            entrypoint=entrypoint,
            files=members,
            geometry_sha256=_digest(geometry),
            normalization_report=normalization_report,
            license=license,
            source=source,
            receipt=receipt,
            parent_version=parent_version,
        )
        digest = _digest(version.model_dump(mode="json", exclude={"version_sha256"}))
        version = version.model_copy(update={"version_sha256": digest})
        self.store.register_asset(digest, asset_id, category, version.model_dump_json())
        return version

    def inspect(self, version_sha: str):
        version = AssetVersion.model_validate_json(self.store.asset_version(version_sha))
        if (
            version.version_sha256 != version_sha
            or _digest(version.model_dump(mode="json", exclude={"version_sha256"})) != version_sha
        ):
            raise ValueError("asset version integrity mismatch")
        for member in version.files:
            self.store.read_artifact(member.artifact)
        for ref in (
            version.normalization_report,
            version.license.evidence,
            version.source.evidence,
            version.receipt,
        ):
            self.store.read_artifact(ref)
        return version

    def find(self, category: str):
        return tuple(
            self.inspect(AssetVersion.model_validate_json(raw).version_sha256)
            for raw in self.store.asset_versions(category)
        )
