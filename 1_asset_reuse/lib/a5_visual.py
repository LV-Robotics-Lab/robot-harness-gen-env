"""a5: visual retrieval channel over the asset server's own thumbnails.

The NVIDIA server publishes a 256x256 PNG preview next to each prop USD at a
deterministic path (`<dir>/.thumbs/256x256/<name>.usd.png`). Measured
2026-08-11 over the existing key index: 465 of 495 prop USDs (94%) have one,
median 55 KB, 22.6 MB and 51 s to mirror the whole corpus. a1's gate used to
discard these as `thumbs_artifact` noise -- they are in fact a free visual
index over the entire searchable universe, needing no USD download, no Kit
session and no conversion.

Why this channel exists ALONGSIDE the lexical one rather than replacing it
(30 labelled queries, same corpus):

    lexical      top-1 86.7%   top-5  86.7%
    clip         top-1 70.0%   top-5  80.0%
    RRF(both)    top-1 93.3%   top-5 100.0%

Replacing lexical with CLIP would have cost 16.7 points. The two fail in
disjoint ways: lexical returns nothing for synonyms and descriptions
("coffee cup", "shears"), CLIP demotes exact names (`bowl` -> rank 6). Fusing
their RANKINGS -- not their scores, which are not comparable -- recovers both.
Prompt ensembling was measured too and made it worse (66.7%), so it is not
used.

Weights are loaded offline-first, mirroring the upstream rendered_critic
convention: the pipeline's determinism guarantee (same prompt+seed -> same
scene) cannot survive a model that silently changes version underneath it.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agenticsim.openxsim.assets import AssetCandidate

BUCKET = "https://omniverse-content-production.s3-us-west-2.amazonaws.com"
DEFAULT_MODEL = "openai/clip-vit-large-patch14"

# ViT-L/14 vs ViT-B/32 measured on the same 30 queries: 70.0% vs 60.0% top-1.
# The index is a few hundred rows and rebuilds in seconds, so the smaller
# model buys nothing worth 10 points.


def thumb_key_for(usd_key: str) -> str:
    head, _, name = usd_key.rpartition("/")
    return f"{head}/.thumbs/256x256/{name}.png"


def _local_name(usd_key: str) -> str:
    return urllib.parse.quote(usd_key, safe="") + ".png"


def pair_usds_with_thumbs(index: dict) -> dict[str, str]:
    """usd_key -> thumb_key, only for thumbnails the listing actually contains.

    Never synthesises a URL that was not observed: a 404 on a guessed path
    would be indistinguishable from a missing asset."""
    listed, usds = set(), []
    for _prefix, entries in index.items():
        for key, _size in entries:
            if ".thumbs" in key:
                if key.endswith(".png"):
                    listed.add(key)
            elif key.endswith(".usd"):
                usds.append(key)
    return {u: thumb_key_for(u) for u in usds if thumb_key_for(u) in listed}


def mirror_thumbnails(index: dict, out_dir, *, workers: int = 8, bucket=BUCKET) -> dict:
    """Download every listed thumbnail that is not already on disk. Resumable:
    an existing file is never refetched, so a partial mirror costs only the
    remainder."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pairs = pair_usds_with_thumbs(index)
    todo = [(u, t) for u, t in pairs.items() if not (out / _local_name(u)).exists()]
    failures = []

    def one(item):
        usd_key, thumb_key = item
        try:
            with urllib.request.urlopen(
                f"{bucket}/{urllib.parse.quote(thumb_key)}", timeout=60
            ) as r:
                (out / _local_name(usd_key)).write_bytes(r.read())
        except Exception as exc:  # noqa: BLE001 -- collected, never swallowed
            failures.append({"usd": usd_key, "error": repr(exc)})

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, todo))
    present = [u for u in pairs if (out / _local_name(u)).exists()]
    return {"paired": len(pairs), "downloaded": len(present), "failures": failures}


def _corpus_fingerprint(usd_keys, model_name) -> str:
    h = hashlib.sha256(model_name.encode())
    for k in sorted(usd_keys):
        h.update(k.encode())
    return h.hexdigest()[:16]


def build_or_load_embeddings(
    usd_keys, thumbs_dir, cache_path, model_name=DEFAULT_MODEL
):
    """Return (keys, normalised embedding matrix). Cached on disk keyed by the
    corpus fingerprint + model, so adding one asset rebuilds, but a rerun over
    an unchanged corpus is a file read."""
    import numpy as np

    thumbs = Path(thumbs_dir)
    keys = [k for k in sorted(usd_keys) if (thumbs / _local_name(k)).exists()]
    fp = _corpus_fingerprint(keys, model_name)
    cache = Path(cache_path)
    if cache.exists():
        blob = np.load(cache, allow_pickle=False)
        if str(blob["fingerprint"]) == fp:
            return list(blob["keys"]), blob["emb"]

    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = (
        CLIPModel.from_pretrained(model_name, local_files_only=True).to(device).eval()
    )
    proc = CLIPProcessor.from_pretrained(model_name, local_files_only=True)
    chunks = []
    with torch.no_grad():
        for i in range(0, len(keys), 64):
            imgs = [
                Image.open(thumbs / _local_name(k)).convert("RGB")
                for k in keys[i : i + 64]
            ]
            inp = proc(images=imgs, return_tensors="pt").to(device)
            chunks.append(
                torch.nn.functional.normalize(model.get_image_features(**inp), dim=-1)
                .cpu()
                .numpy()
            )
    emb = np.concatenate(chunks) if chunks else np.zeros((0, 768), dtype="float32")
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, keys=np.array(keys), emb=emb, fingerprint=np.array(fp))
    return keys, emb


def rrf_merge(rank_lists, *, k: int = 60, limit: int = 20):
    """Reciprocal Rank Fusion over candidate lists. Operates on RANKINGS, not
    scores: a lexical hit-count and a cosine similarity share no scale, and
    calibrating them would be one more thing to drift. Identity is the
    candidate_id, so the same asset found by both channels accumulates both
    contributions instead of appearing twice."""
    scores, seen = {}, {}
    for lst in rank_lists:
        for rank, cand in enumerate(lst, start=1):
            scores[cand.candidate_id] = scores.get(cand.candidate_id, 0.0) + 1.0 / (
                k + rank
            )
            kept = seen.setdefault(cand.candidate_id, cand)
            # The two channels describe the same asset with different metadata:
            # only the visual one knows where the thumbnail is, and the identity
            # gate downstream needs exactly that. Keeping whichever arrived
            # first silently stripped it, so every fused winner reported
            #  and nothing could ever be verified.
            if kept is not cand and isinstance(cand.metadata, dict):
                for key, value in cand.metadata.items():
                    if kept.metadata.get(key) in (None, ""):
                        kept.metadata[key] = value
    order = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [seen[cid] for cid, _s in order[:limit]]


class VisualProvider:
    """Same interface as the other providers: .search(query, limit) ->
    [AssetCandidate]. Emits candidates the lexical channel never sees (the
    corpus has no file named "cup", but five mugs)."""

    name = "nvidia_visual"

    def __init__(
        self,
        index_path,
        thumbs_dir,
        cache_path,
        model_name=DEFAULT_MODEL,
        bucket=BUCKET,
    ):
        self.index_path = Path(index_path)
        self.thumbs_dir = Path(thumbs_dir)
        self.cache_path = Path(cache_path)
        self.model_name = model_name
        self.bucket = bucket
        self._keys = None
        self._emb = None
        self._sizes = {}
        self._text_model = None  # loaded once per process, not per query
        self.last_stats = {}

    def _text_encoder(self):
        if self._text_model is None:
            import torch
            from transformers import CLIPModel, CLIPProcessor

            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = (
                CLIPModel.from_pretrained(self.model_name, local_files_only=True)
                .to(device)
                .eval()
            )
            proc = CLIPProcessor.from_pretrained(self.model_name, local_files_only=True)
            self._text_model = (model, proc, device, torch)
        return self._text_model

    def _ensure(self):
        if self._keys is not None:
            return
        index = json.loads(self.index_path.read_text())
        for _p, entries in index.items():
            for key, size in entries:
                self._sizes[key] = size
        pairs = pair_usds_with_thumbs(index)
        self._keys, self._emb = build_or_load_embeddings(
            list(pairs), self.thumbs_dir, self.cache_path, self.model_name
        )

    def search(self, query, limit=20):
        from lib.a1_providers import NON_OBJECT

        self._ensure()
        if not self._keys:
            self.last_stats = {"indexed": 0, "returned": 0}
            return []
        import numpy as np

        model, proc, device, torch = self._text_encoder()
        with torch.no_grad():
            inp = proc(
                text=[f"a photo of {query}"],
                return_tensors="pt",
                padding=True,
                truncation=True,
            ).to(device)
            t = (
                torch.nn.functional.normalize(model.get_text_features(**inp), dim=-1)
                .cpu()
                .numpy()
            )
        sims = (self._emb @ t.T).squeeze(-1)
        order = np.argsort(-sims)
        out, dropped = [], 0
        for i in order:
            key = self._keys[int(i)]
            stem = key.rsplit("/", 1)[-1][:-4]
            if NON_OBJECT.search(stem):
                dropped += 1
                continue
            out.append(
                AssetCandidate(
                    candidate_id=f"nvidia:{key}",
                    name=key.rsplit("/", 1)[-1],
                    category=key.rsplit("/", 2)[-2].lower(),
                    download_url=f"{self.bucket}/{urllib.parse.quote(key)}",
                    source_page=f"{self.bucket}/{urllib.parse.quote(key)}",
                    format="usd",
                    provider=self.name,
                    license="unknown (NVIDIA Omniverse asset server)",
                    score=float(sims[int(i)]),
                    metadata={
                        "key": key,
                        "size_bytes": self._sizes.get(key),
                        "thumbnail": str(self.thumbs_dir / _local_name(key)),
                        "visual_score": float(sims[int(i)]),
                    },
                )
            )
            if len(out) >= limit:
                break
        self.last_stats = {
            "indexed": len(self._keys),
            "returned": len(out),
            "non_object": dropped,
        }
        return out
