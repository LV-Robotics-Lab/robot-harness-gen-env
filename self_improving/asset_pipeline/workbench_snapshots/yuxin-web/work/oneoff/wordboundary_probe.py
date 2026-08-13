#!/usr/bin/env python3
"""One-off: de-risk step 1 of the plan before anyone writes it into the
pipeline.

Step 1 proposes replacing the lexical channel's substring test with a word
boundary test. Substring is what produces `trash bin -> sektion_ca(bin)et` and
`teddy bear -> caster_(bear)ing`. But tightening a matcher can also DELETE
matches that currently work, and USD basenames are not English prose --
`035_power_drill`, `sm_whitecorrugatedbox_b04_brown_01`, `SM_Mug_A2`. So the
question is not "is word boundary better in principle" but "on THIS corpus,
what does it gain and what does it break".

Also compares CLIP ViT-L/14 against ViT-B/32: if the smaller model holds up,
the index is ~4x cheaper to build and ship.
"""

import argparse
import json
import re
from pathlib import Path

import retrieval_eval as R


def substring_hits(query, stem):
    toks = R._tokens(query)
    return sum(1 for t in toks if t in stem.lower())


def boundary_hits(query, stem):
    """Token must sit at a word boundary. USD names separate words with _ - .
    and camelCase, so the haystack is normalised to _-delimited lowercase
    first; digits are kept as their own words (`035_power_drill` -> 035 power
    drill) so `drill` still matches."""
    hay = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", stem)
    hay = re.sub(r"[^a-zA-Z0-9]+", "_", hay).lower()
    words = {w for w in hay.split("_") if w}
    # a query token matches if it IS a word, or a word starts with it and the
    # remainder is a digit run (b04 -> b + 04 style suffixes)
    hits = 0
    for t in R._tokens(query):
        if t in words or any(w.startswith(t) and w[len(t) :].isdigit() for w in words):
            hits += 1
    return hits


def rank_gold(query, stems, gold_idx, hit_fn):
    scored = []
    toks = R._tokens(query)
    for i, s in enumerate(stems):
        h = hit_fn(query, s)
        if toks and not h:
            continue
        scored.append((float(h), i))
    scored.sort(key=lambda kv: (-kv[0], stems[kv[1]]))
    order = [i for _s, i in scored]
    for r, i in enumerate(order, 1):
        if i in gold_idx:
            return r, len(order)
    return None, len(order)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--thumbs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--compare-models", action="store_true")
    args = ap.parse_args()

    manifest = json.loads((Path(args.thumbs) / "thumbs_manifest.json").read_text())
    stems = [
        k.rsplit("/", 1)[-1][:-4]
        for k in sorted(manifest["pairs"])
        if not R.NON_OBJECT.search(k.rsplit("/", 1)[-1][:-4])
    ]

    rows, gained, broke = [], [], []
    for query, gold_names in R.GOLD.items():
        gold_idx = {i for i, s in enumerate(stems) if s in set(gold_names)}
        if not gold_idx:
            continue
        sub_r, sub_n = rank_gold(query, stems, gold_idx, substring_hits)
        bnd_r, bnd_n = rank_gold(query, stems, gold_idx, boundary_hits)
        rows.append(
            {
                "query": query,
                "substring_rank": sub_r,
                "substring_candidates": sub_n,
                "boundary_rank": bnd_r,
                "boundary_candidates": bnd_n,
            }
        )
        if sub_r is None and bnd_r is not None:
            gained.append(query)
        if sub_r is not None and bnd_r is None:
            broke.append(query)

    absent_rows = []
    for query in R.ABSENT:
        sub_n = sum(1 for s in stems if substring_hits(query, s))
        bnd_n = sum(1 for s in stems if boundary_hits(query, s))
        absent_rows.append(
            {
                "query": query,
                "substring_false_hits": sub_n,
                "boundary_false_hits": bnd_n,
            }
        )

    sub_top1 = sum(1 for r in rows if r["substring_rank"] == 1)
    bnd_top1 = sum(1 for r in rows if r["boundary_rank"] == 1)
    report = {
        "queries": len(rows),
        "substring_top1": sub_top1,
        "boundary_top1": bnd_top1,
        "gained": gained,
        "broke": broke,
        "rows": rows,
        "absent": absent_rows,
    }

    if args.compare_models:
        import torch
        from PIL import Image
        from transformers import CLIPModel, CLIPProcessor

        pairs = manifest["pairs"]
        kept = [
            k
            for k in sorted(pairs)
            if not R.NON_OBJECT.search(k.rsplit("/", 1)[-1][:-4])
        ]
        files = [Path(args.thumbs) / pairs[k]["file"] for k in kept]
        model_stats = {}
        for name in ("openai/clip-vit-large-patch14", "openai/clip-vit-base-patch32"):
            device = "cuda" if torch.cuda.is_available() else "cpu"
            m = CLIPModel.from_pretrained(name).to(device).eval()
            p = CLIPProcessor.from_pretrained(name)
            embs = []
            with torch.no_grad():
                for i in range(0, len(files), 64):
                    imgs = [Image.open(f).convert("RGB") for f in files[i : i + 64]]
                    inp = p(images=imgs, return_tensors="pt").to(device)
                    embs.append(
                        torch.nn.functional.normalize(
                            m.get_image_features(**inp), dim=-1
                        )
                    )
            emb = torch.cat(embs)
            hit1 = hit5 = 0
            for query, gold_names in R.GOLD.items():
                gold_idx = {i for i, s in enumerate(stems) if s in set(gold_names)}
                if not gold_idx:
                    continue
                with torch.no_grad():
                    inp = p(
                        text=[f"a photo of {query}"],
                        return_tensors="pt",
                        padding=True,
                        truncation=True,
                    ).to(device)
                    t = torch.nn.functional.normalize(
                        m.get_text_features(**inp), dim=-1
                    )
                order = torch.argsort((emb @ t.T).squeeze(-1), descending=True).tolist()
                for r, i in enumerate(order, 1):
                    if i in gold_idx:
                        hit1 += r == 1
                        hit5 += r <= 5
                        break
            model_stats[name] = {
                "top1_pct": round(100 * hit1 / len(rows), 1),
                "top5_pct": round(100 * hit5 / len(rows), 1),
                "dim": int(emb.shape[1]),
            }
            del m, emb
            torch.cuda.empty_cache()
        report["models"] = model_stats

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    print(
        f"词法匹配：substring top-1 {sub_top1}/{len(rows)} → boundary {bnd_top1}/{len(rows)}"
    )
    print(f"  新命中（原本零结果）: {gained or '无'}")
    print(f"  ★ 打坏（原本能找到）: {broke or '无'}")
    print("\n候选池收缩（越小越精准，只要 gold 还在）:")
    for r in rows:
        if r["substring_candidates"] != r["boundary_candidates"]:
            print(
                f"  {r['query']:<24} {r['substring_candidates']:>4} → {r['boundary_candidates']:<4}"
                f" gold {r['substring_rank']} → {r['boundary_rank']}"
            )
    print("\n不在库查询的误命中:")
    for r in absent_rows:
        print(
            f"  {r['query']:<24} {r['substring_false_hits']:>3} → {r['boundary_false_hits']}"
        )
    if "models" in report:
        print("\n模型对比:")
        for k, v in report["models"].items():
            print(
                f"  {k:<36} top1 {v['top1_pct']}%  top5 {v['top5_pct']}%  dim {v['dim']}"
            )
    print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
