"""Development acquisition adapters; immutable, portable single-rigid-body assets."""

from __future__ import annotations

import errno
import hashlib
import importlib.util
import io
import json
import math
import re
import struct
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class AssetRequest:
    source: str
    category: str
    selection: str = "exact"
    color: str | None = None
    catalog_path: Path | None = None
    repository: str = "KhronosGroup/glTF-Sample-Assets"
    source_ref: str | None = None
    mass_kg: float = 0.1
    friction: float = 0.6
    scale: float = 1.0


@dataclass(frozen=True)
class AssetVersion:
    version_id: str
    root: Path
    receipt: dict


class AssetAcquisitionError(ValueError):
    def __init__(self, code, detail, partial_root):
        super().__init__(detail)
        self.code, self.partial_root = code, partial_root


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _publish(mesh, root, provenance, mass, friction, parent=None):
    import numpy as np

    if not np.isfinite(mesh.vertices).all() or len(mesh.faces) == 0:
        raise ValueError("invalid mesh geometry")
    bounds = mesh.bounds
    mesh.apply_translation(
        [
            -float((bounds[0, 0] + bounds[1, 0]) / 2),
            -float((bounds[0, 1] + bounds[1, 1]) / 2),
            -float(bounds[0, 2]),
        ]
    )
    dimensions = [float(v) for v in mesh.extents]
    if any(v <= 0 or v > 5 for v in dimensions):
        raise ValueError("asset dimensions outside development bounds (0,5] metres")
    hull = mesh.convex_hull
    files = {
        "visual.glb": mesh.export(file_type="glb"),
        "collision.obj": hull.export(file_type="obj").encode(),
    }
    robot = ET.Element("robot", name="experiment_asset")
    link = ET.SubElement(robot, "link", name="body")
    inertial = ET.SubElement(link, "inertial")
    ET.SubElement(inertial, "origin", xyz=f"0 0 {dimensions[2] / 2}")
    ET.SubElement(inertial, "mass", value=str(mass))
    x, y, z = dimensions
    ET.SubElement(
        inertial,
        "inertia",
        ixx=str(mass * (y * y + z * z) / 12),
        iyy=str(mass * (x * x + z * z) / 12),
        izz=str(mass * (x * x + y * y) / 12),
        ixy="0",
        ixz="0",
        iyz="0",
    )
    for tag, filename in (("visual", "visual.glb"), ("collision", "collision.obj")):
        geometry = ET.SubElement(ET.SubElement(link, tag), "geometry")
        ET.SubElement(geometry, "mesh", filename=filename)
    files["asset.urdf"] = ET.tostring(robot)
    receipt = {
        "schema_version": "harness.experiment_asset.v1",
        **provenance,
        "parent_version": parent,
        "entrypoint": "asset.urdf",
        "dimensions_m": dimensions,
        "mass_kg": mass,
        "friction": friction,
        "physical_parameters_basis": "estimate",
        "collision_method": "convex_hull",
        "inertia_method": "bounding_box_estimate",
        "checks": {"geometry": "passed", "references": "passed", "physics": "not_run"},
        "members": [
            {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
            for name, data in sorted(files.items())
        ],
    }
    version = hashlib.sha256(_json(receipt)).hexdigest()
    target = root / "versions" / version
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("version directory symlink")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as temp:
            directory = Path(temp) / "candidate"
            directory.mkdir()
            for name, data in files.items():
                (directory / name).write_bytes(data)
            (directory / "receipt.json").write_bytes(_json(receipt))
            try:
                directory.rename(target)
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    raise
                # A concurrent publisher may have won; trust only the complete bytes below.
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("version directory symlink")
    if (target / "receipt.json").is_symlink() or (target / "receipt.json").read_bytes() != _json(
        receipt
    ):
        raise ValueError("existing version receipt changed")
    if {p.name for p in target.iterdir()} != {*files, "receipt.json"}:
        raise ValueError("existing version member set changed")
    for name, data in files.items():
        if (target / name).is_symlink() or (target / name).read_bytes() != data:
            raise ValueError("existing version member changed")
    return AssetVersion(version, target, receipt)


def _parameters(mass, friction, scale):
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (mass, friction, scale)):
        raise ValueError("physical parameters must be finite numbers")
    if not (0 < mass <= 100 and 0 <= friction <= 2 and 0.01 <= scale <= 100):
        raise ValueError("physical parameters outside development bounds")


def _provider_module(name):
    active = Path(__file__).resolve().parents[1] / "asset_pipeline" / "active"
    source = str(active / "shared/openxsim/source/agenticsim")
    if source not in sys.path:
        sys.path.insert(0, source)
    spec = importlib.util.spec_from_file_location(
        f"experiment_{name}", active / "1_asset_reuse/lib" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_glb(path):
    import trimesh

    payload = path.read_bytes()
    if len(payload) < 20:
        raise ValueError("invalid GLB header")
    magic, version, length, chunk_size, chunk_type = struct.unpack("<4sIII4s", payload[:20])
    if magic != b"glTF" or version != 2 or length != len(payload) or chunk_type != b"JSON":
        raise ValueError("invalid GLB header")
    document = json.loads(payload[20 : 20 + chunk_size])
    for record in document.get("buffers", []) + document.get("images", []):
        uri = record.get("uri")
        if uri is not None and not uri.startswith("data:"):
            raise ValueError("external GLB resource references are forbidden")
    return trimesh.load(io.BytesIO(payload), file_type="glb", force="mesh")


def _local(request):
    if request.catalog_path is None:
        raise ValueError("local source requires catalog_path")
    provider = _provider_module("a1_providers").RoboTwinLocalProvider(request.catalog_path)
    candidates = provider.search_phrases([request.category])
    if not candidates:
        raise ValueError("local_asset_not_found")
    candidate = candidates[0]
    document = json.loads(Path(request.catalog_path).read_text())
    entries = document["entries"] if isinstance(document, dict) else document
    entry = next(e for e in entries if e["asset_id"] == candidate.metadata["asset_id"])
    model = next(m for m in entry["models"] if m.get("usable"))
    root = Path(entry["asset_path"]).resolve()
    path = Path(model["visual_path"])
    if not path.is_absolute():
        path = root / path
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("local asset path escapes catalog asset root")
    # Embedded GLB avoids implicit untracked OBJ/MTL/external texture reads.
    if path.suffix.lower() != ".glb":
        raise ValueError("local adapter currently accepts self-contained GLB only")
    return path, {
        "candidate_id": candidate.candidate_id,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "license": entry.get("license", "local_registration_license_unverified"),
    }


def _web(request, attempt):
    stage = _provider_module("a3_webfetch")
    from agenticsim.openxsim.assets import GitHubTreeSearchProvider

    if request.repository != "KhronosGroup/glTF-Sample-Assets":
        raise ValueError("web repository is outside configured development allowlist")
    ref = request.source_ref
    if ref is None:
        with urllib.request.urlopen(
            f"https://api.github.com/repos/{request.repository}/commits/main", timeout=20
        ) as response:
            ref = json.load(response)["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("web source_ref must be an immutable commit")
    provider = GitHubTreeSearchProvider(request.repository, branch=ref, timeout_s=20)
    candidates = provider.search(request.category, limit=100)
    candidates = [
        c
        for c in candidates
        if c.format == "glb" and Path(c.name).stem.lower() == request.category.lower()
    ]
    if not candidates:
        raise ValueError("web_asset_not_found")
    candidate = candidates[0]
    license_info = stage._lookup_model_license(candidate.download_url)
    if not license_info or license_info["spdx"] not in {"CC0-1.0", "CC-BY-4.0"}:
        raise ValueError("asset_license_not_allowed")
    candidate = replace(candidate, license=license_info["spdx"])
    from agenticsim.openxsim.assets import download_candidate

    record = stage.stage_web_candidate(
        candidate,
        {"category": request.category},
        "download",
        0,
        attempt / "converted",
        attempt / "download",
        fetch_fn=lambda c, d: download_candidate(c, d, timeout_s=30, max_bytes=20_000_000),
    )
    return Path(record["glb"]), {
        "candidate_id": candidate.candidate_id,
        "source_ref": ref,
        "source_url": candidate.download_url,
        "source_sha256": record["usd_sha256"],
        "license": license_info["spdx"],
        "license_evidence": license_info,
        "source_up_axis": record["up_axis"],
    }


def acquire(request: AssetRequest, staging_root: Path) -> AssetVersion:
    """Acquire actual bytes and register a content-addressed unqualified asset version."""

    root = Path(staging_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix="acquire-", dir=root))
    started = time.monotonic()
    try:
        _parameters(request.mass_kg, request.friction, request.scale)
        if request.selection != "exact":
            raise AssetAcquisitionError(
                "unsupported_selection", "only exact selection is implemented", attempt
            )
        provenance = {
            "source_kind": request.source,
            "selection_strategy": request.selection,
            "category": request.category,
            "color": request.color,
        }
        if request.source == "local":
            path, details = _local(request)
            provenance.update(details)
            mesh = _load_glb(path)
        elif request.source == "web":
            path, details = _web(request, attempt)
            provenance.update(details)
            mesh = _load_glb(path)
            if details["source_up_axis"] != "Y":
                raise ValueError("unsupported web source up axis")
            # glTF right-handed Y-up -> Harness right-handed Z-up (+90 degrees about X).
            transform = [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]
            mesh.apply_transform(transform)
            provenance.update(normalized_up_axis="Z", source_to_normalized_transform=transform)
        elif request.source == "generated":
            mesh, details = _generate(request, attempt)
            provenance.update(details)
        else:
            raise ValueError("unsupported asset source")
        mesh.apply_scale(request.scale)
        result = _publish(mesh, root, provenance, request.mass_kg, request.friction)
        (attempt / "result.json").write_bytes(
            _json(
                {
                    "version_id": result.version_id,
                    "wall_seconds": time.monotonic() - started,
                    "status": "succeeded",
                }
            )
        )
        return result
    except Exception as exc:
        code = exc.code if isinstance(exc, AssetAcquisitionError) else "asset_acquisition_failed"
        (attempt / "failure.json").write_bytes(
            _json(
                {
                    "code": code,
                    "detail": str(exc),
                    "wall_seconds": time.monotonic() - started,
                }
            )
        )
        raise AssetAcquisitionError(code, str(exc), attempt) from exc


def _generate(request, attempt):
    import trimesh

    from scene_gen.asset_generator import ensure_assets_for_scene
    from scene_gen.catalog import AssetCatalog
    from scene_gen.schema import RelationSpec, SceneObjectSpec, SceneSpec

    if request.category not in {"block", "cube", "cylinder", "hexagonal_prism", "octagonal_prism"}:
        raise ValueError("procedural generation supports declared primitive shapes only")
    spec = SceneSpec(
        scene_id="asset",
        request=f"a {request.category} on table",
        language="en",
        objects=(
            SceneObjectSpec(object_id="object", category=request.category, color=request.color),
        ),
        relations=(RelationSpec(relation="on_table", source="object", target="table"),),
    )
    catalog, _ = ensure_assets_for_scene(
        spec,
        AssetCatalog(robotwin_root=str(attempt), objects_root=str(attempt), entries=()),
        objects_root=attempt / "generated",
    )
    return trimesh.load(catalog.entries[0].models[0].visual_path, force="mesh"), {
        "generator": "scene_gen.procedural_proxy.v3",
        "license": "project_generated_academic_artifact",
    }


def revise(existing: AssetVersion, patch: dict, staging_root: Path) -> AssetVersion:
    """Create a new immutable version from a bounded physical parameter patch."""
    root = Path(staging_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix="revise-", dir=root))
    started = time.monotonic()
    try:
        result = _revise(existing, patch, root)
        (attempt / "result.json").write_bytes(
            _json(
                {
                    "version_id": result.version_id,
                    "wall_seconds": time.monotonic() - started,
                    "status": "succeeded",
                }
            )
        )
        return result
    except Exception as exc:
        (attempt / "failure.json").write_bytes(
            _json(
                {
                    "code": "asset_patch_failed",
                    "detail": str(exc),
                    "wall_seconds": time.monotonic() - started,
                }
            )
        )
        raise AssetAcquisitionError("asset_patch_failed", str(exc), attempt) from exc


def _revise(existing, patch, staging_root):

    if not patch or set(patch) - {"scale", "mass_kg", "friction"}:
        raise ValueError("unsupported asset patch fields")
    if existing.root.is_symlink() or (existing.root / "receipt.json").is_symlink():
        raise ValueError("asset version symlink")
    receipt = json.loads((existing.root / "receipt.json").read_bytes())
    if hashlib.sha256(_json(receipt)).hexdigest() != existing.version_id:
        raise ValueError("asset version receipt changed")
    for member in receipt["members"]:
        path = existing.root / member["path"]
        if path.is_symlink() or path.resolve().parent != existing.root.resolve():
            raise ValueError("asset member path escape")
        if hashlib.sha256(path.read_bytes()).hexdigest() != member["sha256"]:
            raise ValueError("asset member changed")
    mass, friction, scale = (
        patch.get("mass_kg", receipt["mass_kg"]),
        patch.get("friction", receipt["friction"]),
        patch.get("scale", 1),
    )
    _parameters(mass, friction, scale)
    mesh = _load_glb(existing.root / "visual.glb")
    mesh.apply_scale(scale)
    provenance = {
        key: value
        for key, value in receipt.items()
        if key
        not in {
            "schema_version",
            "parent_version",
            "entrypoint",
            "dimensions_m",
            "mass_kg",
            "friction",
            "physical_parameters_basis",
            "collision_method",
            "inertia_method",
            "checks",
            "members",
        }
    }
    provenance["patch"] = patch
    return _publish(
        mesh, Path(staging_root).resolve(), provenance, mass, friction, existing.version_id
    )
