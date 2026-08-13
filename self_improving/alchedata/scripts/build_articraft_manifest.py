#!/usr/bin/env python3
"""Build a searchable Articraft-10K catalog manifest from Hugging Face metadata."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = "camvsl/Articraft-10K"
DEFAULT_OUT_DIR = ROOT / "artifacts" / "adapter_catalog"
HF_API = "https://huggingface.co/api/datasets"
DATASETS_SERVER = "https://datasets-server.huggingface.co"
STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "alchedata-articraft-manifest/0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def stable_url(base: str, dataset: str, revision: str, path: str) -> str:
    quoted_path = urllib.parse.quote(path, safe="/")
    return f"{base}/{dataset}/{revision}/{quoted_path}"


def clean_prompt(stem: str) -> str:
    text = stem
    if text.startswith("rec_"):
        text = text[4:]
    parts = text.split("_")
    while parts:
        tail = parts[-1]
        if re.fullmatch(r"[0-9a-f]{8,32}", tail) or re.fullmatch(r"\d{6,}", tail):
            parts.pop()
            continue
        break
    text = "_".join(parts)
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def semantic_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return sorted({token for token in tokens if len(token) > 1 and token not in STOPWORDS})


def build_entry(dataset: str, revision: str, sibling: dict[str, Any]) -> dict[str, Any]:
    path = str(sibling["rfilename"])
    stem = path.removesuffix(".tar.gz")
    prompt = clean_prompt(stem)
    tokens = semantic_tokens(prompt)
    return {
        "asset_id": stem,
        "source_path": path,
        "semantic_text": prompt,
        "semantic_tokens": tokens,
        "format": "tar.gz",
        "asset_format_in_archive": "URDF",
        "download_url": stable_url("https://huggingface.co/datasets", dataset, f"resolve/{revision}", path),
        "browse_url": stable_url("https://huggingface.co/datasets", dataset, f"blob/{revision}", path),
        "metadata_source": "huggingface_dataset_api_siblings",
        "import_status": "catalog_only_not_imported",
        "selection2env_use": "searchable_candidate_metadata_only",
    }


def query_entries(entries: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    query_tokens = semantic_tokens(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        tokens = set(entry["semantic_tokens"])
        text = entry["semantic_text"].lower()
        score = sum(2 for token in query_tokens if token in tokens)
        score += sum(1 for token in query_tokens if token in text and token not in tokens)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], item[1]["asset_id"]))
    return [
        {
            "asset_id": entry["asset_id"],
            "semantic_text": entry["semantic_text"],
            "source_path": entry["source_path"],
            "score": score,
        }
        for score, entry in scored[:limit]
    ]


def get_viewer_status(dataset: str) -> dict[str, Any]:
    url = f"{DATASETS_SERVER}/is-valid?dataset={urllib.parse.quote(dataset, safe='')}"
    try:
        return fetch_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"error": repr(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Articraft-10K metadata/search manifest from Hugging Face.")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--queries", nargs="*", default=["microscope", "monitor", "bench", "lighter", "crane", "washing machine"])
    parser.add_argument("--query-limit", type=int, default=5)
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    dataset_url = f"{HF_API}/{urllib.parse.quote(args.dataset, safe='/')}"
    repo = fetch_json(dataset_url)
    revision = repo.get("sha") or "main"
    siblings = repo.get("siblings") or []
    tar_siblings = [item for item in siblings if str(item.get("rfilename", "")).endswith(".tar.gz")]
    if not tar_siblings:
        raise SystemExit("No .tar.gz Articraft assets found in Hugging Face siblings metadata")

    entries = [build_entry(args.dataset, revision, sibling) for sibling in tar_siblings]
    token_counts = Counter(token for entry in entries for token in entry["semantic_tokens"])
    generated_at = datetime.now(timezone.utc).isoformat()
    viewer_status = get_viewer_status(args.dataset)
    search_examples = [
        {
            "query": query,
            "matches": query_entries(entries, query, args.query_limit),
        }
        for query in args.queries
    ]

    manifest = {
        "schema_version": "alchedata.articraft10k_manifest.v0",
        "status": "pass_searchable_manifest_from_hf_metadata",
        "generated_at": generated_at,
        "dataset": args.dataset,
        "dataset_api_url": dataset_url,
        "dataset_page": f"https://huggingface.co/datasets/{args.dataset}",
        "source_revision": revision,
        "last_modified": repo.get("lastModified"),
        "license": (repo.get("cardData") or {}).get("license"),
        "tags": repo.get("tags", []),
        "description": repo.get("description", ""),
        "private": repo.get("private"),
        "gated": repo.get("gated"),
        "disabled": repo.get("disabled"),
        "viewer_status": viewer_status,
        "sibling_count": len(siblings),
        "asset_count": len(entries),
        "archive_format": "tar.gz",
        "asset_format_in_archive": "URDF",
        "claim_boundary": "Searchable Hugging Face metadata manifest only; archives are not downloaded and RoboTwin/SAPIEN import is not claimed.",
        "top_tokens": [{"token": token, "count": count} for token, count in token_counts.most_common(50)],
        "entries": entries,
    }
    search_report = {
        "schema_version": "alchedata.articraft10k_search_examples.v0",
        "generated_at": generated_at,
        "dataset": args.dataset,
        "manifest": "artifacts/adapter_catalog/articraft10k_manifest.json",
        "query_limit": args.query_limit,
        "queries": search_examples,
        "claim_boundary": "String/token search over manifest metadata only; not a physics import or task success claim.",
    }
    probe = {
        "schema_version": "alchedata.adapter_catalog_probe.v0",
        "catalog": "Articraft-10K",
        "checked_at": generated_at,
        "host": "huggingface_hub",
        "status": "pass_hf_metadata_manifest",
        "dataset": args.dataset,
        "dataset_page": f"https://huggingface.co/datasets/{args.dataset}",
        "source_revision": revision,
        "last_modified": repo.get("lastModified"),
        "license": (repo.get("cardData") or {}).get("license"),
        "viewer_status": viewer_status,
        "asset_count": len(entries),
        "sibling_count": len(siblings),
        "manifest": "artifacts/adapter_catalog/articraft10k_manifest.json",
        "search_examples": "artifacts/adapter_catalog/articraft10k_search_examples.json",
        "previous_local_mount_status": "metadata_not_found_on_host",
        "claim_boundary": "Metadata is parsed from the public Hugging Face dataset API; full archive mount/import remains pending.",
    }

    write_json(out_dir / "articraft10k_manifest.json", manifest)
    write_json(out_dir / "articraft10k_search_examples.json", search_report)
    write_json(out_dir / "articraft10k_probe.json", probe)
    print(
        json.dumps(
            {
                "status": probe["status"],
                "asset_count": len(entries),
                "manifest": str(out_dir / "articraft10k_manifest.json"),
                "search_examples": str(out_dir / "articraft10k_search_examples.json"),
            },
            ensure_ascii=False,
        )
    )
    # Small delay avoids hammering HF when called repeatedly in scripts.
    time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
