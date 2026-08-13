#!/usr/bin/env python3
"""One-off (work/oneoff/): build the 50k-scale NVIDIA index + thumbnail mirror
+ CLIP cache. Reconstructs (reproducibly) the 2026-08-12 expansion that first
ran inline:

  1. List 6 S3 prefixes (the production 5 + `NVIDIA/dsready_content`, NVIDIA's
     bulk SimReady drop: 175,822 listed keys) -> nvidia_keys_50k.json in the
     same {prefix: [[key, size], ...]} shape the providers already read.
  2. Pair USDs with the server's own 256x256 previews (a5 conventions) and
     mirror the pairs -- resumable, an existing file is never refetched.
  3. Rebuild thumbs_manifest.json from what actually paired, then embed the
     mirrored corpus once with CLIP ViT-L/14 into clip_l14_50k.npz.

Read-only w.r.t. the pipeline: writes only under data/asset_index/.
Measured 2026-08-12: 204,749 listed keys -> 31,444 prop USDs -> 10,696
thumbnailed (0 mirror failures) -> 10,696 x 768 embeddings in 70.5s.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "1_asset_reuse"))

from lib import a5_visual  # noqa: E402
from lib.a1_providers import list_bucket_keys  # noqa: E402

PREFIXES = [
    "Assets/Isaac/5.1/Isaac/Props",
    "Assets/Isaac/5.1/NVIDIA/Assets/ArchVis",
    "Assets/Isaac/5.1/NVIDIA/Assets/Vegetation",
    "Assets/Isaac/5.1/NVIDIA/Assets/DigitalTwin",
    "Assets/Isaac/5.1/Isaac/IsaacLab",
    "Assets/Isaac/5.1/NVIDIA/dsready_content",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset-index", default="data/asset_index")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    ai = Path(args.asset_index)

    idx_path = ai / "nvidia_keys_50k.json"
    if idx_path.exists():
        index = json.loads(idx_path.read_text())
        print(f"索引已存在，跳过列举: {sum(len(v) for v in index.values())} 条")
    else:
        t0 = time.time()
        index = {p: list_bucket_keys(p) for p in PREFIXES}
        idx_path.write_text(json.dumps(index))
        counts = ", ".join(f"{p.split('/')[-1]}={len(v)}" for p, v in index.items())
        print(f"[{time.time() - t0:.0f}s] 索引条目: {counts}")

    thumbs = ai / "thumbs_50k"
    t0 = time.time()
    res = a5_visual.mirror_thumbnails(index, thumbs, workers=args.workers)
    print(
        f"[{time.time() - t0:.0f}s] 镜像: 配对 {res['paired']} "
        f"就绪 {res['downloaded']} 失败 {len(res['failures'])}"
    )

    pairs = a5_visual.pair_usds_with_thumbs(index)
    manifest = {
        "schema": "thumbs_manifest.v1",
        "count": len(pairs),
        "pairs": {u: {"file": a5_visual._local_name(u)} for u in sorted(pairs)},
    }
    (thumbs / "thumbs_manifest.json").write_text(json.dumps(manifest))

    usd_keys = [
        e[0]
        for ks in index.values()
        for e in ks
        if str(e[0]).lower().endswith((".usd", ".usda", ".usdz"))
        and "/.thumbs/" not in e[0]
    ]
    t0 = time.time()
    keys, emb = a5_visual.build_or_load_embeddings(
        usd_keys, str(thumbs), str(ai / "clip_l14_50k.npz")
    )
    print(
        f"[{time.time() - t0:.0f}s] CLIP: {len(keys)} 向量 {emb.shape} "
        f"(USD 资产 {len(usd_keys)})"
    )


if __name__ == "__main__":
    main()
