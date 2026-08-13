#!/usr/bin/env python3
"""Live-search verification and import acceptance for ten public assets."""

from __future__ import annotations

import argparse
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap_repo_source

bootstrap_repo_source()

from agenticsim.openxsim.assets import (  # noqa: E402
    AssetCandidate,
    compile_downloaded_asset,
    download_candidate,
    render_obj_preview,
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def github_json(url: str, *, timeout_s: float = 60.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "AgenticSim-AssetScout-Acceptance/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub API request failed: {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"GitHub API returned a non-object: {url}")
    return payload


def verify_sources(catalog: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    blobs: dict[tuple[str, str], dict[str, Any]] = {}
    for source in catalog["sources"]:
        repository = str(source["repository"])
        commit = str(source["commit"])
        encoded_repository = "/".join(urllib.parse.quote(part, safe="") for part in repository.split("/"))
        metadata = github_json(f"https://api.github.com/repos/{encoded_repository}")
        tree = github_json(
            f"https://api.github.com/repos/{encoded_repository}/git/trees/{commit}?recursive=1"
        )
        if tree.get("truncated"):
            raise RuntimeError(f"GitHub tree is truncated for {repository}@{commit}")
        source_blobs = {
            str(item.get("path")): item
            for item in tree.get("tree") or []
            if item.get("type") == "blob" and item.get("path")
        }
        for path, item in source_blobs.items():
            blobs[(repository, path)] = dict(item)
        configured_assets = [
            item for item in catalog["assets"] if item.get("metadata", {}).get("repository") == repository
        ]
        missing = [
            str(item["metadata"]["path"])
            for item in configured_assets
            if str(item["metadata"]["path"]) not in source_blobs
        ]
        evidence.append(
            {
                "repository": repository,
                "repository_url": metadata.get("html_url"),
                "description": metadata.get("description"),
                "default_branch": metadata.get("default_branch"),
                "license_spdx": (metadata.get("license") or {}).get("spdx_id"),
                "configured_license": source.get("license"),
                "commit": commit,
                "tree_sha": tree.get("sha"),
                "tree_truncated": False,
                "discovery_query": source.get("discovery_query"),
                "selected_asset_count": len(configured_assets),
                "selected_paths_present": not missing,
                "missing_paths": missing,
                "status": "pass" if not missing else "block",
            }
        )
    return evidence, blobs


def mujoco_runtime_check(mjcf_path: Path, *, steps: int = 20) -> dict[str, Any]:
    try:
        import mujoco
    except ImportError as exc:
        return {"status": "conditional", "reason": f"mujoco_import_failed: {exc}"}
    try:
        model = mujoco.MjModel.from_xml_path(str(mjcf_path))
        data = mujoco.MjData(model)
        initial_time = float(data.time)
        for _ in range(steps):
            mujoco.mj_step(model, data)
        values_finite = all(math.isfinite(float(value)) for value in data.qpos)
        passed = model.ngeom >= 2 and float(data.time) > initial_time and values_finite
        return {
            "status": "pass" if passed else "block",
            "mujoco_version": mujoco.__version__,
            "model_file": str(mjcf_path),
            "body_count": int(model.nbody),
            "geom_count": int(model.ngeom),
            "joint_count": int(model.njnt),
            "steps": steps,
            "initial_time_s": initial_time,
            "final_time_s": float(data.time),
            "qpos_finite": values_finite,
        }
    except Exception as exc:
        return {"status": "block", "reason": f"mujoco_runtime_failed: {exc!r}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        default=str(Path(__file__).resolve().parents[1] / "configs/openxsim/asset_sources_v1.json"),
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    catalog_path = Path(args.catalog).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    source_evidence, blobs = verify_sources(catalog)
    write_json(output / "source_discovery.json", {"sources": source_evidence})

    assets: list[dict[str, Any]] = []
    for raw in catalog["assets"]:
        asset_id = str(raw["asset_id"])
        record: dict[str, Any] = {
            "asset_id": asset_id,
            "category": raw["category"],
            "status": "block",
        }
        try:
            candidate = AssetCandidate.from_dict(raw, provider=str(raw.get("provider") or "catalog"))
            metadata = raw["metadata"]
            blob = blobs.get((str(metadata["repository"]), str(metadata["path"])))
            if blob is None:
                raise RuntimeError("selected path was absent from the pinned GitHub tree")
            downloaded = download_candidate(candidate, output / "cache")
            bundle = compile_downloaded_asset(
                downloaded,
                output / "compiled",
                asset_id=asset_id,
                category=str(raw["category"]),
            )
            source_representation = next(
                item for item in bundle.representations if item.role == "source"
            )
            imported_representation = next(
                item for item in bundle.representations if item.format == "obj"
            )
            mjcf = next(item for item in bundle.representations if item.format == "mjcf")
            previews_dir = output / "previews" / asset_id
            source_preview = render_obj_preview(
                source_representation.uri, previews_dir / "source.png"
            )
            imported_preview = render_obj_preview(
                imported_representation.uri, previews_dir / "imported.png"
            )
            runtime = mujoco_runtime_check(Path(mjcf.uri))
            status = "pass" if runtime["status"] == "pass" else runtime["status"]
            record.update(
                {
                    "status": status,
                    "candidate": asdict(candidate),
                    "github_blob": {
                        "sha": blob.get("sha"),
                        "size_bytes": blob.get("size"),
                        "api_url": blob.get("url"),
                    },
                    "download": {
                        "path": downloaded.path,
                        "sha256": downloaded.sha256,
                        "size_bytes": downloaded.size_bytes,
                        "cache_hit": downloaded.cache_hit,
                        "provenance_path": downloaded.provenance_path,
                    },
                    "representations": [asdict(item) for item in bundle.representations],
                    "source_preview": source_preview,
                    "imported_preview": imported_preview,
                    "runtime": runtime,
                }
            )
            write_json(output / "bundles" / f"{asset_id}.json", asdict(bundle))
        except Exception as exc:
            record["failure"] = repr(exc)
        assets.append(record)
        write_json(output / "asset_acceptance.partial.json", {"assets": assets, "complete": False})
        print(f"{record['status'].upper()} {asset_id}", flush=True)

    pass_count = sum(item["status"] == "pass" for item in assets)
    conditional_count = sum(item["status"] == "conditional" for item in assets)
    block_count = sum(item["status"] == "block" for item in assets)
    categories = sorted({str(item["category"]) for item in assets})
    source_pass = all(item["status"] == "pass" for item in source_evidence)
    status = (
        "pass"
        if source_pass and len(source_evidence) >= 3 and len(assets) >= 10 and pass_count == len(assets)
        else "conditional"
        if source_pass and not block_count
        else "block"
    )
    report = {
        "schema": "agenticsim.asset_scout_acceptance.v1",
        "status": status,
        "catalog_path": str(catalog_path),
        "network_used": True,
        "discovery_method": catalog.get("selection_policy"),
        "source_count": len(source_evidence),
        "asset_count": len(assets),
        "category_count": len(categories),
        "categories": categories,
        "pass_count": pass_count,
        "conditional_count": conditional_count,
        "block_count": block_count,
        "sources": source_evidence,
        "assets": assets,
        "complete": True,
    }
    write_json(output / "asset_acceptance.json", report)
    print(
        f"{status.upper()} pass={pass_count}/{len(assets)} sources={len(source_evidence)} "
        f"report={output / 'asset_acceptance.json'}"
    )
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
