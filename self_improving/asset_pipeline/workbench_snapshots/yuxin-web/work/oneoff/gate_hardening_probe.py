#!/usr/bin/env python3
"""One-off (work/oneoff/): measure hardening rules for the identity gate on
the EXPANDED corpus -- the one where the measured false positive happened
(a fluted planter accepted as "trash bin").

Rules compared, all on identical fused top-3 candidates per query:
  V0  current behaviour: accept the first match, any confidence
  V1  accept only confidence == "high"
  V2  second opinion: re-ask with an OPEN question ("what is this object?")
      and accept only if the open answer also matches the category
  V3  V1 + V2

Metrics: false accepts on absent queries (must hit 0), true accepts kept on
present queries (must not collapse). The winner is whatever reaches 0 false
accepts with the fewest lost true accepts.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "1_asset_reuse")
sys.path.insert(0, "1_asset_reuse/scripts/1_search")

from lib import a6_verify as a6  # noqa: E402
from lib.a1_providers import NvidiaAssetServerProvider  # noqa: E402
from lib.a5_visual import VisualProvider, rrf_merge  # noqa: E402

IDX = "data/asset_index/nvidia_keys_expanded.json"
PREF = [
    "Assets/Isaac/5.1/Isaac/Props",
    "Assets/Isaac/5.1/NVIDIA/Assets/ArchVis",
    "Assets/Isaac/5.1/NVIDIA/Assets/Vegetation",
]

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
]
ABSENT = [
    ("trash bin", ["trash bin", "garbage can"]),  # the measured false positive
    ("teddy bear", ["teddy bear"]),
    ("umbrella", ["umbrella"]),
    ("hammer", ["hammer"]),
    ("screwdriver", ["screwdriver"]),
]
PRESENT.append(("bicycle", ["bicycle", "bike", "cycle"]))  # OldBike.usd IS in ArchVis


OPEN_PROMPT = (
    "Look at this image and answer with one JSON object and nothing else:\n"
    '{"object": "<the single main object you see, 1-3 words>"}'
)


def open_identify(image_path):
    raw = a6._local_infer(Path(image_path), OPEN_PROMPT, a6.DEFAULT_MODEL)
    parsed = a6._parse('{"match": true, ' + raw.split("{", 1)[-1] if "{" in raw else "")
    # simpler: extract "object" field directly
    import re

    m = re.search(r'"object"\s*:\s*"([^"]+)"', raw)
    return m.group(1).lower() if m else None


def open_agrees(seen, category, aliases):
    if not seen:
        return False
    names = {category.lower(), *(a.lower() for a in aliases)}
    seen_words = set(seen.replace("-", " ").split())
    return any(
        n in seen or seen in n or (set(n.split()) & seen_words) for n in names
    )


def main():
    nv = NvidiaAssetServerProvider(PREF, IDX)
    vp = VisualProvider(
        IDX,
        "data/asset_index/thumbs_expanded",
        "data/asset_index/clip_l14_expanded.npz",
    )
    rows = []
    for truth, (q, aliases) in [("present", x) for x in PRESENT] + [
        ("absent", x) for x in ABSENT
    ]:
        fused = rrf_merge([nv.search(q, limit=10), vp.search(q, limit=10)], limit=3)
        plain = a6.verify_candidates(
            fused, q, aliases=aliases, max_check=3, second_opinion=False
        )
        hard = a6.verify_candidates(
            fused, q, aliases=aliases, max_check=3, second_opinion=True
        )
        row = {
            "query": q,
            "truth": truth,
            "plain_accept": plain["accepted"] is not None,
            "hard_accept": hard["accepted"] is not None,
            "name": plain["accepted"].name if plain["accepted"] else None,
            "open_answer": next(
                (r.get("open_answer") for r in hard["results"] if "open_answer" in r),
                None,
            ),
            "veto": any(r.get("second_opinion_veto") for r in hard["results"]),
        }
        rows.append(row)
        print(
            f'{q:<14} {truth:<8} 原规则={int(row["plain_accept"])} '
            f'加固后={int(row["hard_accept"])} '
            f'(top={row["name"]}, open={row["open_answer"]}, veto={row["veto"]})'
        )
    print()
    for v, label in (("plain", "原规则"), ("hard", "加固后(默认)")):
        fa = [r["query"] for r in rows if r["truth"] == "absent" and r[f"{v}_accept"]]
        tr = sum(1 for r in rows if r["truth"] == "present" and r[f"{v}_accept"])
        np_ = sum(1 for r in rows if r["truth"] == "present")
        print(f"{label}: 误放 {len(fa)}{fa or ''}  正放 {tr}/{np_}")
    Path("results/20260811_gate_hardening").mkdir(parents=True, exist_ok=True)
    Path("results/20260811_gate_hardening/final.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    main()
