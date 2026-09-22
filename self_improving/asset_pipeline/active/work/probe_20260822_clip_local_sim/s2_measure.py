#!/usr/bin/env python3
"""探针 s2：同类 vs 异类的 CLIP 文本-图像相似度分布是否分得开。

ground truth = catalog 的 category 标签（免费金标）。
双向指标：候选阈值下同时报 同类召回 与 异类误入。
附带颜色子探针：\"a photo of a {color} ball\" 能否把颜色排对。
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from transformers import CLIPModel, CLIPProcessor

OUT = Path(__file__).resolve().parent / "out"
MODEL = "openai/clip-vit-large-patch14"


def main():
    rows = [json.loads(x) for x in (OUT / "assets.jsonl").read_text().splitlines()]
    vecs = np.load(OUT / "embeds.npz")["vecs"]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(MODEL).to(device).eval()
    proc = CLIPProcessor.from_pretrained(MODEL)

    def embed_text(texts):
        with torch.no_grad():
            inp = proc(text=texts, return_tensors="pt", padding=True).to(device)
            v = model.get_text_features(**inp)
            return (v / v.norm(dim=-1, keepdim=True)).cpu().numpy()

    cats = sorted({r["category"] for r in rows if r["category"]})
    tvecs = embed_text([f"a photo of a {c.replace('_', ' ')}" for c in cats])

    pos, neg = [], []
    detail = open(OUT / "sims.jsonl", "w")
    for ci, c in enumerate(cats):
        sims = vecs @ tvecs[ci]
        for r, s in zip(rows, sims):
            is_pos = r["category"] == c
            (pos if is_pos else neg).append(float(s))
            detail.write(
                json.dumps(
                    {
                        "query": c,
                        "asset": r["asset_id"],
                        "cat": r["category"],
                        "sim": round(float(s), 4),
                        "pos": is_pos,
                    }
                )
                + "\n"
            )
    detail.close()
    pos, neg = np.array(pos), np.array(neg)

    def pct(a, q):
        return round(float(np.percentile(a, q)), 4)

    print(f"pairs: pos={len(pos)} neg={len(neg)}")
    print(f"pos: p5={pct(pos, 5)} p50={pct(pos, 50)} p95={pct(pos, 95)}")
    print(f"neg: p5={pct(neg, 5)} p50={pct(neg, 50)} p95={pct(neg, 95)}")
    # AUC（无 sklearn：秩和法）
    all_s = np.concatenate([pos, neg])
    ranks = all_s.argsort().argsort() + 1
    auc = (ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (
        len(pos) * len(neg)
    )
    print(f"AUC={auc:.4f}")
    print("threshold  同类召回  异类误入")
    for t in np.arange(0.14, 0.32, 0.02):
        rec = float((pos >= t).mean())
        fp = float((neg >= t).mean())
        print(f"  {t:.2f}      {rec:.3f}     {fp:.3f}")

    # 颜色子探针：多色同类资产上，带色 query 是否把同色排前
    by_cat = defaultdict(list)
    for i, r in enumerate(rows):
        if r["colors"]:
            by_cat[r["category"]].append(i)
    checked = ok = 0
    for c, idxs in by_cat.items():
        colors = {col for i in idxs for col in rows[i]["colors"]}
        if len(idxs) < 2 or len(colors) < 2:
            continue
        for col in sorted(colors):
            tv = embed_text([f"a photo of a {col} {c.replace('_', ' ')}"])[0]
            sims = [(float(vecs[i] @ tv), i) for i in idxs]
            sims.sort(reverse=True)
            top = rows[sims[0][1]]
            checked += 1
            hit = col in top["colors"]
            ok += hit
            print(
                f"color-probe: {col} {c} -> top={top['asset_id']}{top['colors']} {'✓' if hit else '✗'}"
            )
    if checked:
        print(f"color-probe: {ok}/{checked} 排序正确")


if __name__ == "__main__":
    sys.exit(main())
