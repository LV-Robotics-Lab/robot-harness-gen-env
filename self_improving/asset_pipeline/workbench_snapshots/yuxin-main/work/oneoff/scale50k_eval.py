#!/usr/bin/env python3
"""One-off (work/oneoff/): retrieval quality + perf + gate at the 50k-scale,
multi-source universe.

The universe this run sees:
  - NVIDIA S3 (6 prefixes, incl. dsready_content): 204,749 listed entries,
    ~x USD assets lexically searchable, 10,696 with thumbnails (visual+gate).
  - Objaverse LVIS: 46,207 community objects in 1,156 curated categories,
    per-object CC licenses, lazy Sketchfab thumbnails.

Three questions:
 1. PERF -- does query latency survive a 3.6x thumbnail corpus and a 7x raw
    listing? (postings are per-token, CLIP is one matmul; both should stay flat)
 2. QUALITY -- the 30-query labelled set, knowing its gold labels are now
    stale-in-one-direction (new same-category objects outrank old golds and
    COUNT AS MISSES here; the operative metric stays gate-verified acceptance).
 3. GATE at 50k -- present/absent separation, where "absent" is re-checked
    lexically against THIS corpus first: at 175k industrial props an
    yesterday's absent query may genuinely be present today, and an accept the
    7B sees as the right category is a find, not a false positive.
"""

import json
import sys
import time
from pathlib import Path

WT = "/home/jingxiang/yuxin/wt-main"
DEV = "/home/jingxiang/yuxin/env-gen-dev"
sys.path.insert(0, f"{WT}/1_asset_reuse")
sys.path.insert(0, f"{WT}/work/oneoff")
sys.path.insert(0, f"{DEV}/shared/openxsim/source/agenticsim")

from lib import a6_verify as a6  # noqa: E402
from lib.a1_providers import NvidiaAssetServerProvider  # noqa: E402
from lib.a5_visual import VisualProvider, rrf_merge  # noqa: E402
from lib.a7_objaverse import ObjaverseLvisProvider  # noqa: E402
import retrieval_eval as R  # noqa: E402

AI = f"{DEV}/data/asset_index"
IDX = f"{AI}/nvidia_keys_50k.json"
THUMBS = f"{AI}/thumbs_50k"
CACHE = f"{AI}/clip_l14_50k.npz"
PREF = [
    "Assets/Isaac/5.1/Isaac/Props",
    "Assets/Isaac/5.1/NVIDIA/Assets/ArchVis",
    "Assets/Isaac/5.1/NVIDIA/Assets/Vegetation",
    "Assets/Isaac/5.1/NVIDIA/Assets/DigitalTwin",
    "Assets/Isaac/5.1/Isaac/IsaacLab",
    "Assets/Isaac/5.1/NVIDIA/dsready_content",
]

PRESENT = [
    ("mug", ["mug", "cup"]),
    ("banana", ["banana"]),
    ("scissors", ["scissors", "shears"]),
    ("power drill", ["power drill", "drill"]),
    ("office chair", ["office chair", "chair"]),
    ("potted plant", ["potted plant", "plant"]),
    ("sofa", ["sofa", "couch"]),
    ("floor lamp", ["floor lamp", "lamp"]),
    ("trash bin", ["trash bin", "garbage can", "waste can", "trash can"]),
    ("forklift", ["forklift", "fork lift"]),
    ("fire extinguisher", ["fire extinguisher", "extinguisher"]),
]
# "absent" is a HYPOTHESIS at this scale -- re-checked lexically below.
ABSENT_MAYBE = [
    ("umbrella", ["umbrella"]),
    ("hammer", ["hammer"]),
    ("screwdriver", ["screwdriver"]),
    ("guitar", ["guitar"]),
    ("snowman", ["snowman"]),
]
OBJAVERSE_QUERIES = [
    "hammer",
    "umbrella",
    "screwdriver",
    "guitar",
    "teddy bear",
    "snowman",
]


def main():
    nv = NvidiaAssetServerProvider(PREF, IDX)
    vp = VisualProvider(IDX, THUMBS, CACHE)
    ob = ObjaverseLvisProvider(f"{AI}/objaverse")

    # ---- 0) perf ---------------------------------------------------------
    print("=== 性能（50k 宇宙）===")
    t0 = time.time()
    nv.search("warmup probe", limit=5)
    print(f"  词法冷启动（读索引+建倒排, 204,749 条）: {time.time() - t0:.2f}s")
    t0 = time.time()
    vp.search("warmup probe", limit=5)
    print(f"  视觉冷启动（载CLIP+{Path(CACHE).name}）: {time.time() - t0:.2f}s")
    lex_ms, vis_ms, rrf_ms = [], [], []
    for q in list(R.GOLD)[:15]:
        t0 = time.time()
        a_ = nv.search(q, limit=10)
        lex_ms.append(1e3 * (time.time() - t0))
        t0 = time.time()
        b_ = vp.search(q, limit=10)
        vis_ms.append(1e3 * (time.time() - t0))
        t0 = time.time()
        rrf_merge([a_, b_], limit=10)
        rrf_ms.append(1e3 * (time.time() - t0))
    med = lambda xs: sorted(xs)[len(xs) // 2]  # noqa: E731
    print(
        f"  暖查询中位数: 词法 {med(lex_ms):.2f}ms  视觉 {med(vis_ms):.1f}ms  RRF融合 {med(rrf_ms):.3f}ms"
    )
    t0 = time.time()
    ob.search("hammer", limit=6)
    print(
        f"  Objaverse 冷启动（46,207 物体/1,156 类目 + 首次水合）: {time.time() - t0:.2f}s"
    )
    t0 = time.time()
    ob.search("hammer", limit=6)
    print(f"  Objaverse 暖查询: {1e3 * (time.time() - t0):.1f}ms")

    # ---- 1) labelled 30-query set (stale-gold caveat applies) ------------
    manifest = json.loads((Path(THUMBS) / "thumbs_manifest.json").read_text())
    pairs = manifest["pairs"]
    kept = [
        (k, k.rsplit("/", 1)[-1][:-4])
        for k in sorted(pairs)
        if not R.NON_OBJECT.search(k.rsplit("/", 1)[-1][:-4])
    ]
    stems = [s for _k, s in kept]

    import os
    import numpy as np
    import torch
    from transformers import CLIPModel, CLIPProcessor

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = (
        CLIPModel.from_pretrained(
            "openai/clip-vit-large-patch14", local_files_only=True
        )
        .to(device)
        .eval()
    )
    proc = CLIPProcessor.from_pretrained(
        "openai/clip-vit-large-patch14", local_files_only=True
    )
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
    print(
        f"\n=== 检索质量（{n} 题旧标注，语料 {len(stems)} 物体 -- 陈旧金标只减不增）==="
    )
    for m in ("lexical", "clip", "rrf"):
        print(
            f"  {m:<8} top-1 {100 * stats[m]['hit1'] / n:.1f}%   top-5 {100 * stats[m]['hit5'] / n:.1f}%"
        )

    # ---- 2) absent re-check + gate at 50k --------------------------------
    def lexically_present(q):
        hits = nv.search(q, limit=3)
        return [h.name for h in hits[:2]]

    print(
        f"\n=== 闸门（7B+开放复核）@50k：{len(PRESENT)} 在库 + {len(ABSENT_MAYBE)} 疑似不在库 ==="
    )
    rows = []
    for truth, (q, aliases) in [("present", x) for x in PRESENT] + [
        ("absent?", x) for x in ABSENT_MAYBE
    ]:
        lex_seen = lexically_present(q) if truth == "absent?" else []
        fused = rrf_merge([nv.search(q, limit=10), vp.search(q, limit=10)], limit=3)
        t0 = time.time()
        v = a6.verify_candidates(fused, q, aliases=aliases, max_check=3)
        acc = v["accepted"].name if v["accepted"] else None
        first = v["results"][0] if v["results"] else {}
        rows.append(
            {
                "query": q,
                "truth": truth,
                "accept": acc,
                "seen_as": first.get("seen_as"),
                "lexical_names": lex_seen,
            }
        )
        note = f" 词法命中:{lex_seen}" if lex_seen else ""
        print(
            f"  {q:<18} {truth:<8} -> {str(acc):<40} "
            f"[{time.time() - t0:4.1f}s] 判定:{first.get('verdict')}({first.get('seen_as')}){note}"
        )
    tr = sum(1 for r in rows if r["truth"] == "present" and r["accept"])
    print(
        f"\n  在库正放 {tr}/{len(PRESENT)}；'absent?' 行的 accept 逐条看 seen_as 判真伪"
    )

    # ---- 3) objaverse coverage of the NVIDIA gaps ------------------------
    print("\n=== Objaverse 对 NVIDIA 缺口的覆盖 ===")
    for q in OBJAVERSE_QUERIES:
        cs = ob.search(q, limit=6)
        lic = {}
        for c in cs:
            spdx = c.metadata.get("license_spdx") or "unknown"
            lic[spdx] = lic.get(spdx, 0) + 1
        cats = sorted({c.metadata["lvis_category"] for c in cs})
        print(f"  {q:<12} -> {len(cs)} 候选  类目{cats}  许可{lic}")

    out = Path(f"{WT}/results/20260812_scale50k")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps(
            {
                "universe": {
                    "nvidia_listed": 204749,
                    "nvidia_usd_assets": 31444,
                    "nvidia_thumbnailed": len(pairs),
                    "objaverse_lvis": 46207,
                },
                "perf_ms": {
                    "lexical_median": med(lex_ms),
                    "visual_median": med(vis_ms),
                    "rrf_median": med(rrf_ms),
                },
                "retrieval": stats,
                "gate": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(f"\n报告 -> {out}/report.json")


if __name__ == "__main__":
    main()
