#!/usr/bin/env python3
"""One-off (work/oneoff/): labelled evaluation of four retrieval methods over
the NVIDIA prop corpus, using the server's own thumbnails.

Methods
  lexical    exactly what a1_providers does today: query tokens as substrings
             of the USD basename, score = hit count.
  clip       CLIP image/text similarity, single prompt "a photo of {q}".
  clip_ens   same, but the text embedding is the mean over a prompt ensemble
             (the technique from the original CLIP paper; single bare nouns are
             known to be its weakest input form, and this corpus is rendered
             props on plain backgrounds, not photos).
  rrf        Reciprocal Rank Fusion of lexical + clip_ens, k=60. The standard
             hybrid-IR combiner: it needs no score calibration between the two
             (their scales are not comparable), only their rankings.

Ground truth is hand-labelled against the actual corpus listing, and kept
deliberately generous (any of the listed files counts as correct) so the
comparison measures retrieval, not label pedantry.

`absent` queries name things this corpus does NOT contain. They exist to test
the property that matters more than ranking: does the method KNOW when the
answer isn't there? A retriever that always returns its best guess will hand
the import pipeline a confidently wrong asset -- and, since the ledger records
identity basis `requested_by_acquire`, that wrong asset then carries the name
we asked for, not the name of what it is.

Corpus hygiene: entries that are not candidate objects at all (material
definitions, physics proxies, scene scaffolding) are excluded and counted --
see NON_OBJECT. They are roughly half the raw listing and neither method
should be judged on its ability to avoid them.
"""

import argparse
import json
import re
from pathlib import Path

# Not graspable props: material libraries, physics-only proxies, scaffolding.
NON_OBJECT = re.compile(
    r"(^|_)(materials?|physics_material|plane|frame_prim|checkerboard|"
    r"instaceable_meshes|instanceable_meshes)(\.|$)"
    r"|^(Acrylic|Aluminium|Metal|MetalPainted|Plastic|Rubber|Steel)_"
    r"|_physics$"
    r"|^M_ConveyorBelt",
    re.IGNORECASE,
)

GOLD = {
    "mug": ["SM_Mug_A2", "SM_Mug_B1", "SM_Mug_C1", "SM_Mug_D1", "025_mug"],
    "coffee cup": ["SM_Mug_A2", "SM_Mug_B1", "SM_Mug_C1", "SM_Mug_D1", "025_mug"],
    "a red mug with a handle": [
        "025_mug",
        "SM_Mug_A2",
        "SM_Mug_B1",
        "SM_Mug_C1",
        "SM_Mug_D1",
    ],
    "bowl": ["024_bowl"],
    "banana": ["011_banana"],
    "a yellow curved fruit": ["011_banana"],
    "power drill": ["035_power_drill"],
    "drill": ["035_power_drill"],
    "scissors": ["037_scissors"],
    "shears": ["037_scissors"],
    "soda can": [
        "002_master_chef_can",
        "005_tomato_soup_can",
        "007_tuna_fish_can",
        "010_potted_meat_can",
    ],
    "tin can": [
        "002_master_chef_can",
        "005_tomato_soup_can",
        "007_tuna_fish_can",
        "010_potted_meat_can",
    ],
    "cardboard box": [
        "003_cracker_box",
        "004_sugar_box",
        "008_pudding_box",
        "009_gelatin_box",
    ],
    "clamp": ["051_large_clamp", "052_extra_large_clamp"],
    "marker pen": ["040_large_marker"],
    "cleaning bottle": ["021_bleach_cleanser"],
    "mustard bottle": ["006_mustard_bottle"],
    "pitcher": ["019_pitcher_base"],
    "a wooden block": ["036_wood_block"],
    "foam brick": ["061_foam_brick"],
    "rubiks cube": ["rubiks_cube"],
    "beaker": ["beaker_500ml"],
    "gear": ["gear_base", "gear_large", "gear_medium", "gear_small"],
    "pallet": ["pallet", "o3dyn_pallet", "pallet_holder", "pallet_holder_short"],
    "forklift": ["forklift", "S_ForkliftFork"],
    "cabinet": [
        "sektion_cabinet_instanceable",
        "sektion_cabinet_visuals",
        "sektion_cabinet_collisions",
    ],
    "conveyor belt": [f"ConveyorBelt_A{i:02d}" for i in range(1, 50)],
    "crate": ["SM_Crate_A07_01_1", "SM_Crate_A07_Yellow_01", "SM_Crate_A08_Blue_01"],
    "camera": ["camera"],
    "packing table": ["packing_table", "SM_HeavyDutyPackingTable_C02_01"],
}

ABSENT = ["hammer", "screwdriver", "trash bin", "umbrella", "teddy bear", "laptop"]

PROMPT_TEMPLATES = [
    "a photo of {}",
    "a 3d render of {}",
    "a {} on a white background",
    "a rendering of a {} object",
    "a product photo of {}",
]


def _tokens(q):
    return {t for t in re.findall(r"[a-z0-9]+", q.lower()) if len(t) > 1}


def lexical_rank(query, stems):
    toks = _tokens(query)
    scored = []
    for i, stem in enumerate(stems):
        base = stem.lower()
        hits = sum(1 for t in toks if t in base)
        if toks and not hits:
            continue
        scored.append((float(hits), i))
    scored.sort(key=lambda kv: (-kv[0], stems[kv[1]]))
    return [i for _s, i in scored]


def rank_of_gold(order, gold_idx):
    for r, i in enumerate(order, start=1):
        if i in gold_idx:
            return r
    return None


def rrf(rank_lists, k=60):
    scores = {}
    for order in rank_lists:
        for r, i in enumerate(order, start=1):
            scores[i] = scores.get(i, 0.0) + 1.0 / (k + r)
    return [i for i, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--thumbs", required=True)
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

    kept, dropped = [], []
    for usd_key in sorted(pairs):
        stem = usd_key.rsplit("/", 1)[-1][:-4]
        (dropped if NON_OBJECT.search(stem) else kept).append((usd_key, stem))
    stems = [s for _k, s in kept]
    files = [thumbs / pairs[k]["file"] for k, _s in kept]
    print(
        f"corpus {len(pairs)} -> objects {len(kept)}, dropped non-objects {len(dropped)}"
    )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(args.model).to(device).eval()
    proc = CLIPProcessor.from_pretrained(args.model)

    embs = []
    with torch.no_grad():
        for i in range(0, len(files), args.batch):
            imgs = [Image.open(f).convert("RGB") for f in files[i : i + args.batch]]
            inp = proc(images=imgs, return_tensors="pt").to(device)
            embs.append(
                torch.nn.functional.normalize(model.get_image_features(**inp), dim=-1)
            )
    image_emb = torch.cat(embs)

    def text_emb(prompts):
        with torch.no_grad():
            inp = proc(
                text=prompts, return_tensors="pt", padding=True, truncation=True
            ).to(device)
            t = torch.nn.functional.normalize(model.get_text_features(**inp), dim=-1)
        return torch.nn.functional.normalize(t.mean(0, keepdim=True), dim=-1)

    def clip_order(query, ensemble):
        prompts = (
            [tpl.format(query) for tpl in PROMPT_TEMPLATES]
            if ensemble
            else [f"a photo of {query}"]
        )
        sims = (image_emb @ text_emb(prompts).T).squeeze(-1)
        order = torch.argsort(sims, descending=True).tolist()
        return order, sims

    methods = ["lexical", "clip", "clip_ens", "rrf"]
    stats = {m: {"hit1": 0, "hit5": 0, "mrr": 0.0, "no_result": 0} for m in methods}
    rows = []
    for query, gold_names in GOLD.items():
        gold_idx = {i for i, s in enumerate(stems) if s in set(gold_names)}
        if not gold_idx:
            print(f"  !! skipping {query!r}: no gold survives corpus hygiene")
            continue
        lex = lexical_rank(query, stems)
        c_bare, _ = clip_order(query, ensemble=False)
        c_ens, _ = clip_order(query, ensemble=True)
        fused = rrf([lex, c_ens])
        orders = {"lexical": lex, "clip": c_bare, "clip_ens": c_ens, "rrf": fused}
        row = {"query": query, "gold": gold_names}
        for m, order in orders.items():
            r = rank_of_gold(order, gold_idx)
            row[m] = r
            if not order:
                stats[m]["no_result"] += 1
            if r == 1:
                stats[m]["hit1"] += 1
            if r is not None and r <= 5:
                stats[m]["hit5"] += 1
            if r is not None:
                stats[m]["mrr"] += 1.0 / r
        row["clip_ens_top1"] = stems[c_ens[0]]
        rows.append(row)

    n = len(rows)
    for m in methods:
        stats[m]["hit1_pct"] = round(100 * stats[m]["hit1"] / n, 1)
        stats[m]["hit5_pct"] = round(100 * stats[m]["hit5"] / n, 1)
        stats[m]["mrr"] = round(stats[m]["mrr"] / n, 3)

    # Abstention: is the top-1 similarity separable between present and absent?
    def margin(query):
        _order, sims = clip_order(query, ensemble=True)
        s = sims.sort(descending=True).values
        top = float(s[0])
        med = float(s.median())
        return top, top - med

    present_m = [margin(q) for q in list(GOLD)[:12]]
    absent_m = [margin(q) for q in ABSENT]

    report = {
        "model": args.model,
        "corpus_raw": len(pairs),
        "corpus_objects": len(kept),
        "dropped_non_objects": len(dropped),
        "queries": n,
        "stats": stats,
        "rows": rows,
        "abstention": {
            "present": [
                {"top": round(t, 4), "margin": round(mg, 4)} for t, mg in present_m
            ],
            "absent": [
                {"query": q, "top": round(t, 4), "margin": round(mg, 4)}
                for q, (t, mg) in zip(ABSENT, absent_m)
            ],
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    print(f"\n{'method':<10}{'top-1':>8}{'top-5':>8}{'MRR':>8}")
    for m in methods:
        s = stats[m]
        print(f"{m:<10}{s['hit1_pct']:>7}%{s['hit5_pct']:>7}%{s['mrr']:>8}")

    print("\n每题 gold 排名（None = 完全没找到）")
    print(
        f"{'query':<26}{'lexical':>9}{'clip':>7}{'clip_ens':>10}{'rrf':>6}   clip_ens top1"
    )
    for r in rows:
        print(
            f"{r['query']:<26}{str(r['lexical']):>9}{str(r['clip']):>7}"
            f"{str(r['clip_ens']):>10}{str(r['rrf']):>6}   {r['clip_ens_top1']}"
        )

    pt = [t for t, _ in present_m]
    at = [t for t, _ in absent_m]
    pm = [m for _, m in present_m]
    am = [m for _, m in absent_m]
    print(
        f"\n弃权判据：present top1 {min(pt):.3f}~{max(pt):.3f} margin {min(pm):.3f}~{max(pm):.3f}"
    )
    print(
        f"           absent  top1 {min(at):.3f}~{max(at):.3f} margin {min(am):.3f}~{max(am):.3f}"
    )
    print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
