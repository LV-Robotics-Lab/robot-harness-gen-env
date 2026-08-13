#!/usr/bin/env python3
"""Download one Articraft archive and validate its URDF/import boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tarfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "artifacts" / "adapter_catalog" / "articraft10k_manifest.json"
DEFAULT_OUT_DIR = ROOT / "runs" / "articraft_archive_probe_weight_bench"
DEFAULT_ASSET_ID = "rec_adjustable_weight_bench_with_hinged_backrest_008247976e6d499d8bcbd1304f26c972"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "alchedata-articraft-archive-probe/0"})
    with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def select_entry(manifest: dict[str, Any], asset_id: str, query: str | None) -> dict[str, Any]:
    entries = manifest.get("entries", [])
    if asset_id:
        for entry in entries:
            if entry.get("asset_id") == asset_id:
                return entry
        raise SystemExit(f"Articraft asset id not found in manifest: {asset_id}")
    if query:
        query_tokens = {token for token in query.lower().replace("_", " ").split() if token}
        for entry in entries:
            text = str(entry.get("semantic_text", "")).lower()
            tokens = set(entry.get("semantic_tokens", []))
            if query_tokens and query_tokens.issubset(tokens | set(text.split())):
                return entry
        raise SystemExit(f"Articraft query matched no manifest entry: {query}")
    raise SystemExit("Provide --asset-id or --query")


def safe_extract(archive: Path, extract_dir: Path) -> list[str]:
    extract_root = extract_dir.resolve()
    names: list[str] = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (extract_dir / member.name).resolve()
            if extract_root not in (target, *target.parents):
                raise RuntimeError(f"unsafe tar member path: {member.name}")
            names.append(member.name)
        tar.extractall(extract_dir)
    return names


def parse_urdf(path: Path, extract_dir: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    links = root.findall("link")
    joints = root.findall("joint")
    visual_geometries = root.findall(".//visual/geometry")
    collision_geometries = root.findall(".//collision/geometry")
    meshes = [mesh.attrib.get("filename", "") for mesh in root.findall(".//mesh") if mesh.attrib.get("filename")]
    mesh_paths = []
    missing_meshes = []
    for mesh in meshes:
        mesh_path = Path(mesh)
        if str(mesh).startswith("package://"):
            mesh_path = Path(str(mesh).removeprefix("package://"))
        resolved = (path.parent / mesh_path).resolve()
        fallback = (extract_dir / mesh_path).resolve()
        exists = resolved.exists() or fallback.exists()
        mesh_paths.append({"filename": mesh, "exists": exists})
        if not exists:
            missing_meshes.append(mesh)
    geometry_types = Counter()
    for geometry in root.findall(".//geometry"):
        for child in list(geometry):
            geometry_types[child.tag] += 1
    joint_types = Counter(joint.attrib.get("type", "unknown") for joint in joints)
    materials = root.findall(".//material")
    return {
        "robot_name": root.attrib.get("name"),
        "link_count": len(links),
        "joint_count": len(joints),
        "joint_types": dict(sorted(joint_types.items())),
        "visual_geometry_count": len(visual_geometries),
        "collision_geometry_count": len(collision_geometries),
        "geometry_types": dict(sorted(geometry_types.items())),
        "material_count": len(materials),
        "mesh_reference_count": len(meshes),
        "mesh_references": mesh_paths,
        "missing_mesh_references": missing_meshes,
        "first_links": [link.attrib.get("name") for link in links[:20]],
        "first_joints": [joint.attrib.get("name") for joint in joints[:20]],
    }


def run_sapien_smoke(urdf_path: Path, steps: int) -> dict[str, Any]:
    import sapien.core as sapien

    engine = sapien.Engine()
    renderer = sapien.SapienRenderer()
    engine.set_renderer(renderer)
    scene = engine.create_scene(sapien.SceneConfig())
    scene.set_timestep(1 / 250)
    scene.add_ground(0)
    loader = scene.create_urdf_loader()
    loader.fix_root_link = True
    articulation = loader.load(str(urdf_path))
    if articulation is None:
        raise RuntimeError("SAPIEN URDF loader returned None")
    link_count = len(articulation.get_links()) if hasattr(articulation, "get_links") else None
    active_joint_count = len(articulation.get_active_joints()) if hasattr(articulation, "get_active_joints") else None
    for _ in range(steps):
        scene.step()
    return {
        "status": "pass",
        "loader": "sapien.create_urdf_loader",
        "fix_root_link": True,
        "articulation_type": type(articulation).__name__,
        "link_count": link_count,
        "active_joint_count": active_joint_count,
        "physics_steps": steps,
        "sapien_module": getattr(sapien, "__file__", None),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe one Articraft-10K archive download and URDF/SAPIEN import.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--asset-id", default=DEFAULT_ASSET_ID)
    parser.add_argument("--query", default=None)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--archive-path", default=None)
    parser.add_argument("--sapien-smoke", action="store_true")
    parser.add_argument("--physics-steps", type=int, default=20)
    args = parser.parse_args()

    manifest_path = Path(args.manifest).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    archive_path = out_dir / "archive.tar.gz"
    extract_dir = out_dir / "extracted"
    report_path = out_dir / "probe_report.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)

    manifest = read_json(manifest_path)
    entry = select_entry(manifest, args.asset_id, args.query)
    started_at = datetime.now(timezone.utc).isoformat()
    if args.archive_path:
        shutil.copyfile(Path(args.archive_path).expanduser().resolve(), archive_path)
    else:
        download(str(entry["download_url"]), archive_path)

    members = safe_extract(archive_path, extract_dir)
    urdf_files = sorted(extract_dir.rglob("*.urdf"))
    json_files = sorted(extract_dir.rglob("*.json"))
    if not urdf_files:
        raise RuntimeError("archive contained no URDF files")
    urdf_path = urdf_files[0]
    compile_reports = [path for path in json_files if path.name == "compile_report.json"]
    compile_report = read_json(compile_reports[0]) if compile_reports else None
    urdf_summary = parse_urdf(urdf_path, extract_dir)

    checks = {
        "archive_downloaded": archive_path.exists() and archive_path.stat().st_size > 0,
        "archive_extracted": bool(members),
        "urdf_found": bool(urdf_files),
        "links_present": urdf_summary["link_count"] > 0,
        "joints_present": urdf_summary["joint_count"] > 0,
        "collision_geometry_present": urdf_summary["collision_geometry_count"] > 0,
        "mesh_references_resolved": not urdf_summary["missing_mesh_references"],
    }
    sapien_smoke: dict[str, Any] = {"status": "not_run"}
    if args.sapien_smoke:
        try:
            sapien_smoke = run_sapien_smoke(urdf_path, args.physics_steps)
        except Exception as exc:
            sapien_smoke = {"status": "fail", "error": repr(exc)}

    status = "pass_articraft_archive_urdf_metadata_probe"
    if not all(checks.values()):
        status = "fail_articraft_archive_urdf_metadata_probe"
    if args.sapien_smoke:
        status = "pass_articraft_archive_sapien_smoke" if status.startswith("pass_") and sapien_smoke.get("status") == "pass" else "fail_articraft_archive_sapien_smoke"

    report = {
        "schema_version": "alchedata.articraft_archive_probe.v0",
        "status": status,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "dataset": manifest.get("dataset"),
        "dataset_page": manifest.get("dataset_page"),
        "source_revision": manifest.get("source_revision"),
        "manifest": str(manifest_path),
        "selected_asset": {
            "asset_id": entry.get("asset_id"),
            "semantic_text": entry.get("semantic_text"),
            "source_path": entry.get("source_path"),
            "download_url": entry.get("download_url"),
        },
        "archive": {
            "path": str(archive_path),
            "size_bytes": archive_path.stat().st_size,
            "sha256": sha256_file(archive_path),
            "member_count": len(members),
            "first_members": members[:20],
        },
        "extraction": {
            "extract_dir": str(extract_dir),
            "urdf_files": [str(path) for path in urdf_files],
            "json_files": [str(path) for path in json_files],
        },
        "compile_report": compile_report,
        "urdf": {
            "path": str(urdf_path),
            "sha256": sha256_file(urdf_path),
            **urdf_summary,
        },
        "checks": checks,
        "sapien_smoke": sapien_smoke,
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cwd": os.getcwd(),
        },
        "claim_boundary": (
            "One selected Articraft-10K archive was downloaded, extracted, parsed as URDF, "
            "and optionally loaded/stepped in SAPIEN. This does not validate the full catalog, "
            "scale suitability, material fidelity, collision quality beyond URDF presence, "
            "or learned-policy task success."
        ),
    }
    write_json(report_path, report)
    print(json.dumps({"status": status, "report": str(report_path), "asset_id": entry.get("asset_id")}, ensure_ascii=False))
    time.sleep(0.1)
    return 0 if status.startswith("pass_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
