#!/usr/bin/env python3
"""One-off: can we tell "the corpus doesn't contain this" automatically?

This is the safety question, not the ranking question. A retriever that always
returns its best guess will hand the import pipeline a confidently wrong asset,
and because the ledger stamps identity basis `requested_by_acquire`, that asset
then carries the name we ASKED for rather than the name of what it actually is.
Nothing downstream can detect that.

Tests three candidate signals on 30 present + 6 absent queries:
  clip_top      absolute top-1 similarity
  clip_margin   top-1 minus corpus median (scale-free within a query)
  lex_hits      how many corpus entries the lexical channel matched at all

and reports, for each, whether ANY threshold separates present from absent.
"""

import argparse
import json
from pathlib import Path

import retrieval_eval as R  # reuse GOLD / ABSENT / NON_OBJECT / tokenisation


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--thumbs", required=True)
    ap.add_argument("--model", default="openai/clip-vit-large-patch14")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    thumbs = Path(args.thumbs)
    manifest = json.loads((thumbs / "thumbs_manifest.json").read_text())
    pairs = manifest["pairs"]
    kept = [
        (k, k.rsplit("/", 1)[-1][:-4])
        for k in sorted(pairs)
        if not R.NON_OBJECT.search(k.rsplit("/", 1)[-1][:-4])
    ]
    stems = [s for _k, s in kept]
    files = [thumbs / pairs[k]["file"] for k, _s in kept]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(args.model).to(device).eval()
    proc = CLIPProcessor.from_pretrained(args.model)
    embs = []
    with torch.no_grad():
        for i in range(0, len(files), 64):
            imgs = [Image.open(f).convert("RGB") for f in files[i : i + 64]]
            inp = proc(images=imgs, return_tensors="pt").to(device)
            embs.append(
                torch.nn.functional.normalize(model.get_image_features(**inp), dim=-1)
            )
    image_emb = torch.cat(embs)

    def signals(query):
        with torch.no_grad():
            inp = proc(
                text=[f"a photo of {query}"],
                return_tensors="pt",
                padding=True,
                truncation=True,
            ).to(device)
            t = torch.nn.functional.normalize(model.get_text_features(**inp), dim=-1)
            sims = (image_emb @ t.T).squeeze(-1)
        s = sims.sort(descending=True).values
        toks = R._tokens(query)
        lex_hits = sum(
            1 for stem in stems if toks and any(t in stem.lower() for t in toks)
        )
        return {
            "clip_top": float(s[0]),
            "clip_margin": float(s[0] - s.median()),
            "lex_hits": lex_hits,
        }

    present = [{"query": q, **signals(q)} for q in R.GOLD]
    absent = [{"query": q, **signals(q)} for q in R.ABSENT]

    def separable(key, lower_is_absent=True):
        p = [r[key] for r in present]
        a = [r[key] for r in absent]
        if lower_is_absent:
            # a threshold exists iff max(absent) < min(present)
            return max(a) < min(p), min(p), max(a)
        return min(a) > max(p), max(p), min(a)

    lines = []
    for key in ("clip_top", "clip_margin", "lex_hits"):
        ok, p_lo, a_hi = separable(key)
        lines.append(
            f"{key:<13} present最低 {p_lo:>8.3f} | absent最高 {a_hi:>8.3f} | "
            f"{'可分' if ok else '★不可分（区间重叠）'}"
        )

    # combined rule: absent iff lexical found nothing AND clip margin is low
    def rule(r, m_thresh):
        return r["lex_hits"] == 0 and r["clip_margin"] < m_thresh

    best = None
    for m_thresh in [x / 1000 for x in range(40, 200, 2)]:
        tp = sum(1 for r in absent if rule(r, m_thresh))
        fp = sum(1 for r in present if rule(r, m_thresh))
        if fp == 0 and (best is None or tp > best[1]):
            best = (m_thresh, tp)

    report = {
        "present": present,
        "absent": absent,
        "separability": lines,
        "combined_rule": (
            {
                "margin_threshold": best[0],
                "absent_caught": best[1],
                "absent_total": len(absent),
                "present_false_alarms": 0,
            }
            if best
            else None
        ),
    }
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    print("单信号可分性：")
    for ln in lines:
        print("  " + ln)
    print("\n逐题信号：")
    print(f"{'query':<26}{'clip_top':>10}{'margin':>9}{'lex_hits':>10}  在库")
    for r in present:
        print(
            f"{r['query']:<26}{r['clip_top']:>10.3f}{r['clip_margin']:>9.3f}{r['lex_hits']:>10}  是"
        )
    for r in absent:
        print(
            f"{r['query']:<26}{r['clip_top']:>10.3f}{r['clip_margin']:>9.3f}{r['lex_hits']:>10}  否"
        )
    if best:
        print(
            f"\n组合规则「lex_hits==0 且 clip_margin<{best[0]:.3f}」→ "
            f"零误伤地判出 {best[1]}/{len(absent)} 个不在库的查询"
        )
    else:
        print("\n★ 找不到任何零误伤的组合阈值")
    print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
