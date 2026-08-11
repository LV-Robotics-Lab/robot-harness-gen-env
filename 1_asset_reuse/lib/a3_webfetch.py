"""a3: fetch web (GitHub) candidates and synthesize staging records for materialize."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_glb(src, dst):
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".glb":
        dst.write_bytes(src.read_bytes())
    else:
        import trimesh

        trimesh.load(str(src)).export(str(dst))
    return dst


def synth_staging_record(
    glb_path, source_path, source_sha, asset, model, entry, up_axis="Y"
):
    return {
        "group": f"web_{asset}",
        "usd": Path(source_path).name,
        "usd_local": str(source_path),
        "usd_sha256": source_sha,
        "asset": asset,
        "model": model,
        "category": entry["category"],
        "aliases": entry.get("aliases", [entry["category"]]),
        "glb": str(glb_path),
        "glb_sha256": _sha256(glb_path),
        "up_axis": up_axis,
        "status": "converted",
        # same per-item knobs a hand-written manifest item may carry
        **{k: entry[k] for k in ("size_policy", "collision", "reorient", "flat") if entry.get(k)},
    }


class ConvertError(RuntimeError):
    """Raised when a fetched web candidate fails GLB conversion / record synthesis."""


def stage_source(src_path, source_sha, entry, asset, model, staging_dir, up_axis="Y"):
    """Convert src_path to GLB, synthesize a staging record, and write
    staging_manifest.json under staging_dir. Conversion failures are wrapped
    in ConvertError; the manifest write itself is not."""
    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        glb = to_glb(src_path, staging / f"{asset}_m{model}.glb")
        record = synth_staging_record(
            glb, src_path, source_sha, asset, model, entry, up_axis=up_axis
        )
    except Exception as exc:  # noqa: BLE001
        raise ConvertError(f"{type(exc).__name__}: {exc}") from exc
    (staging / "staging_manifest.json").write_text(json.dumps([record], indent=1))
    return record


def stage_web_candidate(
    candidate, entry, asset, model, staging_dir, cache_dir, fetch_fn=None
):
    if fetch_fn is None:
        from agenticsim.openxsim.assets import download_candidate as fetch_fn
    downloaded = fetch_fn(candidate, cache_dir)
    record = stage_source(
        downloaded.path, downloaded.sha256, entry, asset, model, staging_dir
    )
    # Provenance the ledger's retrieved branch requires and only the candidate
    # knows. It must land in the ON-DISK staging manifest, not just the
    # returned dict: materialize reads the manifest, and a field that exists
    # only in the return value quietly never reaches the ledger (the a3 unit
    # test caught exactly that half-write).
    record["source_url"] = candidate.download_url
    record["source_page"] = candidate.source_page
    record["source_license"] = str(candidate.license)
    record["source_provider"] = candidate.provider
    manifest_file = Path(staging_dir) / "staging_manifest.json"
    manifest = json.loads(manifest_file.read_text())
    for i, m in enumerate(manifest):
        if m.get("asset") == asset and m.get("model") == model:
            manifest[i] = record
    manifest_file.write_text(json.dumps(manifest, indent=1) + "\n")
    return record
