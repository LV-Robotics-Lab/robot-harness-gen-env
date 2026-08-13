#!/usr/bin/env python3
"""One-off (work/oneoff/): measure the current retrieval against CLIP-over-
thumbnails, on our own corpus, with the same queries.

Baseline = exactly what lib/a1_providers.NvidiaAssetServerProvider.search does
today: tokenise the query, count how many tokens appear as substrings of the
USD basename, rank by that count. Reimplemented here rather than imported so
the comparison stays honest if that file changes -- and kept character-for-
character equivalent (see _baseline_rank).

Candidate = CLIP text/image similarity over the server's own 256x256
thumbnails (see fetch_thumbs.py: 94% coverage, 22.6 MB, one 51 s mirror).

Three query families, chosen to separate what the baseline can and cannot do:
  exact      the query word IS in the filename  -> baseline should win/tie
  synonym    a different word for the same thing -> baseline structurally blind
  descriptive colour/shape/function phrasing     -> baseline hopeless

Reports top-5 per method per query plus the two failure counts that matter:
how often the baseline returns NOTHING, and how often its top score is a tie
(a tie means the pick is arbitrary, which is the retrieval-side twin of the
catalog-side tie the 2026-08-10 semantics audit found).
"""

import argparse
import json
import re
from pathlib import Path

QUERIES = {
    "exact": ["mug", "bowl", "banana", "hammer", "drill"],
    "synonym": [
        ("scissors", "shears"),
        ("soda can", "can / soup can"),
        ("coffee cup", "mug"),
        ("trash bin", "bin / basket"),
        ("screwdriver", "driver / tool"),
    ],
    "descriptive": [
        "a red mug with a handle",
        "a yellow curved fruit",
        "a cardboard box",
        "a metal wrench",
        "a wooden block",
    ],
}


def _tokens(query):
    """Byte-identical to a1_providers._tokens."""
    return {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1}


def _baseline_rank(query, usd_keys, limit=5):
    """Byte-identical scoring to NvidiaAssetServerProvider.search: substring of
    the basename, score = number of distinct query tokens hit, drop zero-hit."""
    toks = _tokens(query)
    scored = []
    for key in usd_keys:
        base = key.rsplit("/", 1)[-1].lower()
        hits = sum(1 for t in toks if t in base)
        if toks and not hits:
            continue
        scored.append((float(hits), key))
    scored.sort(key=lambda kv: (-kv[0], kv[1]))
    return scored[:limit], scored


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--thumbs", required=True, help="dir written by fetch_thumbs.py")
    ap.add_argument("--model", default="openai/clip-vit-large-patch14")
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()

    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    thumbs = Path(args.thumbs)
    manifest = json.loads((thumbs / "thumbs_manifest.json").read_text())
    pairs = manifest["pairs"]
    usd_keys = sorted(pairs)
    files = [thumbs / pairs[k]["file"] for k in usd_keys]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"loading {args.model} on {device} ...")
    model = CLIPModel.from_pretrained(args.model).to(device).eval()
    proc = CLIPProcessor.from_pretrained(args.model)

    print(f"embedding {len(files)} thumbnails ...")
    embs = []
    with torch.no_grad():
        for i in range(0, len(files), args.batch):
            imgs = [Image.open(f).convert("RGB") for f in files[i : i + args.batch]]
            inp = proc(images=imgs, return_tensors="pt").to(device)
            e = model.get_image_features(**inp)
            embs.append(torch.nn.functional.normalize(e, dim=-1))
    image_emb = torch.cat(embs)

    def clip_rank(text, limit=5):
        with torch.no_grad():
            inp = proc(
                text=[f"a photo of {text}"],
                return_tensors="pt",
                padding=True,
                truncation=True,
            ).to(device)
            t = torch.nn.functional.normalize(model.get_text_features(**inp), dim=-1)
            sims = (image_emb @ t.T).squeeze(-1)
        top = torch.topk(sims, k=min(limit, len(usd_keys)))
        return [(float(s), usd_keys[int(i)]) for s, i in zip(top.values, top.indices)]

    report = {"model": args.model, "corpus": len(usd_keys), "families": {}}
    empty = ties = 0
    for family, items in QUERIES.items():
        rows = []
        for item in items:
            query, note = (item, "") if isinstance(item, str) else item
            base_top, base_all = _baseline_rank(query, usd_keys)
            if not base_all:
                empty += 1
            elif len(base_all) > 1 and base_all[0][0] == base_all[1][0]:
                ties += 1
            rows.append(
                {
                    "query": query,
                    "expected_note": note,
                    "baseline_total_hits": len(base_all),
                    "baseline_top5": [
                        {"score": s, "name": k.rsplit("/", 1)[-1]} for s, k in base_top
                    ],
                    "clip_top5": [
                        {"score": round(s, 4), "name": k.rsplit("/", 1)[-1]}
                        for s, k in clip_rank(query)
                    ],
                }
            )
        report["families"][family] = rows

    report["baseline_zero_result_queries"] = empty
    report["baseline_top_score_ties"] = ties
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    for family, rows in report["families"].items():
        print(f"\n########## {family}")
        for r in rows:
            note = f"  (期望: {r['expected_note']})" if r["expected_note"] else ""
            print(f"\n  query: {r['query']!r}{note}")
            b = r["baseline_top5"]
            print(
                "    baseline :",
                ", ".join(f"{x['name']}({x['score']:.0f})" for x in b)
                if b
                else "—— 零结果",
            )
            print(
                "    clip     :",
                ", ".join(f"{x['name']}({x['score']:.3f})" for x in r["clip_top5"]),
            )
    print(
        f"\nbaseline 零结果查询 {empty}/{sum(len(v) for v in QUERIES.values())}"
        f" | 顶分并列 {ties}"
    )
    print(f"report -> {out}")


if __name__ == "__main__":
    main()
