#!/usr/bin/env python3
"""One-off (work/oneoff/): quality + gate verification at the 3,571-image
corpus (Props + ArchVis + Vegetation + DigitalTwin + IsaacLab).

Two questions only a scale test answers:
 1. Does rank fusion HOLD when the distractor pool triples? (The 30-query
    labelled set's gold objects are all still in the corpus; every new asset
    is a potential distractor.)
 2. Does the hardened gate still separate present/absent when the corpus is
    industrial-messy -- AND when yesterday's absent queries become present
    (the corpus now genuinely contains trash cans and a teddy bear)?
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "1_asset_reuse")
sys.path.insert(0, "1_asset_reuse/scripts/1_search")
sys.path.insert(0, "work/oneoff")

from lib import a6_verify as a6  # noqa: E402
from lib.a1_providers import NvidiaAssetServerProvider  # noqa: E402
from lib.a5_visual import VisualProvider, rrf_merge  # noqa: E402
import retrieval_eval as R  # noqa: E402  (GOLD / rank helpers)

IDX = "data/asset_index/nvidia_keys_2k.json"
THUMBS = "data/asset_index/thumbs_2k"
CACHE = "data/asset_index/clip_l14_2k.npz"
PREF = [
    "Assets/Isaac/5.1/Isaac/Props",
    "Assets/Isaac/5.1/NVIDIA/Assets/ArchVis",
    "Assets/Isaac/5.1/NVIDIA/Assets/Vegetation",
    "Assets/Isaac/5.1/NVIDIA/Assets/DigitalTwin",
    "Assets/Isaac/5.1/Isaac/IsaacLab",
]

# Gate truth sets, re-verified against THIS corpus (2026-08-12 lexical sweep):
# trash bin and teddy bear moved to PRESENT -- the corpus now really has them.
PRESENT = [
    ("mug", ["mug", "cup"]),
    ("banana", ["banana"]),
    ("scissors", ["scissors", "shears"]),
    ("power drill", ["power drill", "drill"]),
    ("beaker", ["beaker", "lab glass", "glass beaker", "cup"]),
    ("office chair", ["office chair", "chair"]),
    ("potted plant", ["potted plant", "plant"]),
    ("sofa", ["sofa", "couch"]),
    ("floor lamp", ["floor lamp", "lamp"]),
    ("wooden block", ["wooden block", "block"]),
    ("bicycle", ["bicycle", "bike", "cycle"]),
    ("trash bin", ["trash bin", "garbage can", "waste can", "trash can"]),
    ("teddy bear", ["teddy bear", "stuffed bear", "plush bear"]),
    ("forklift", ["forklift", "fork lift"]),
    ("fire extinguisher", ["fire extinguisher", "extinguisher"]),
]
ABSENT = [
    ("umbrella", ["umbrella"]),
    ("hammer", ["hammer"]),
    ("screwdriver", ["screwdriver"]),
    ("guitar", ["guitar"]),
    ("snowman", ["snowman"]),
]


def main():
    nv = NvidiaAssetServerProvider(PREF, IDX)
    vp = VisualProvider(IDX, THUMBS, CACHE)

    # ---- 1) retrieval quality: the 30-query labelled set at 3,571 ----
    manifest = json.loads((Path(THUMBS) / "thumbs_manifest.json").read_text())
    pairs = manifest["pairs"]
    kept = [
        (k, k.rsplit("/", 1)[-1][:-4])
        for k in sorted(pairs)
        if not R.NON_OBJECT.search(k.rsplit("/", 1)[-1][:-4])
    ]
    stems = [s for _k, s in kept]
    files = [Path(THUMBS) / pairs[k]["file"] for k, _s in kept]

    import numpy as np
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor
    import os

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = (
        CLIPModel.from_pretrained("openai/clip-vit-large-patch14", local_files_only=True)
        .to(device)
        .eval()
    )
    proc = CLIPProcessor.from_pretrained(
        "openai/clip-vit-large-patch14", local_files_only=True
    )
    # reuse the a5 cache's embeddings via VisualProvider internals
    vp._ensure()
    key_to_i = {k: i for i, k in enumerate(vp._keys)}
    emb = vp._emb
    stem_rows = [(s, key_to_i.get(k)) for k, s in kept]

    def clip_order(query):
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
        sims = emb @ t.T
        order_keys = np.argsort(-sims.squeeze(-1))
        rank_of = {int(i): r for r, i in enumerate(order_keys, 1)}
        return sorted(
            range(len(stem_rows)),
            key=lambda j: rank_of.get(stem_rows[j][1], 10**9),
        )

    stats = {m: dict(hit1=0, hit5=0) for m in ("lexical", "clip", "rrf")}
    n = 0
    for query, gold_names in R.GOLD.items():
        gold_idx = {j for j, (s, _e) in enumerate(stem_rows) if s in set(gold_names)}
        if not gold_idx:
            continue
        n += 1
        lex = R.lexical_rank(query, stems)
        cl = clip_order(query)
        fused = R.rrf([lex, cl])
        for m, order in (("lexical", lex), ("clip", cl), ("rrf", fused)):
            r = R.rank_of_gold(order, gold_idx)
            stats[m]["hit1"] += r == 1
            stats[m]["hit5"] += r is not None and r <= 5
    print(f"=== 检索质量（{n} 题带标注，语料 {len(stems)} 物体）===")
    for m in ("lexical", "clip", "rrf"):
        print(
            f"  {m:<8} top-1 {100*stats[m]['hit1']/n:.1f}%   top-5 {100*stats[m]['hit5']/n:.1f}%"
        )

    # ---- 2) hardened gate at scale (7B + second opinion) ----
    print(f"\n=== 闸门（7B+开放复核）：{len(PRESENT)} 在库 + {len(ABSENT)} 不在库 ===")
    rows = []
    for truth, (q, aliases) in [("present", x) for x in PRESENT] + [
        ("absent", x) for x in ABSENT
    ]:
        fused = rrf_merge([nv.search(q, limit=10), vp.search(q, limit=10)], limit=3)
        t0 = time.time()
        v = a6.verify_candidates(fused, q, aliases=aliases, max_check=3)
        acc = v["accepted"].name if v["accepted"] else None
        rows.append({"query": q, "truth": truth, "accept": acc})
        first = v["results"][0] if v["results"] else {}
        print(
            f"  {q:<18} {truth:<8} -> {str(acc):<34} "
            f"[{time.time()-t0:4.1f}s] top判定:{first.get('verdict')}({first.get('seen_as')})"
        )
    fa = [r["query"] for r in rows if r["truth"] == "absent" and r["accept"]]
    tr = sum(1 for r in rows if r["truth"] == "present" and r["accept"])
    print(f"\n  误放 {len(fa)}{fa or ''}   正放 {tr}/{len(PRESENT)}")
    Path("results/20260812_scale2k").mkdir(parents=True, exist_ok=True)
    Path("results/20260812_scale2k/report.json").write_text(
        json.dumps({"retrieval": stats, "gate": rows}, indent=2, ensure_ascii=False)
        + "\n"
    )


if __name__ == "__main__":
    main()
