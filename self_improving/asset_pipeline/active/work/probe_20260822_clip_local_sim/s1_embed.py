#!/usr/bin/env python3
"""探针 s1：给 catalog 全部资产的缩略图算 CLIP 嵌入。

只读生产数据（catalog/web_thumbs/snapshots），产物落本探针目录 out/。
"""

import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

ACTIVE = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
MODEL = "openai/clip-vit-large-patch14"


def thumb_for(asset_id, lib):
    p = ACTIVE / "results" / "web_thumbs" / f"{asset_id}.png"
    if p.is_file():
        return p
    for c in [lib / asset_id, *lib.glob(f"*/{asset_id}")]:
        s = c / "snapshots" / "m0_default.png"
        if s.is_file():
            return s
    return None


def main():
    cat = json.loads((ACTIVE / "data/scene_gen_ext/asset_catalog.json").read_text())
    lib = ACTIVE / "data/asset_library"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(MODEL).to(device).eval()
    proc = CLIPProcessor.from_pretrained(MODEL)

    rows, vecs, missing = [], [], 0
    for e in cat.get("entries", []):
        aid = e.get("asset_id")
        t = thumb_for(aid, lib)
        if t is None:
            missing += 1
            continue
        img = Image.open(t).convert("RGB")
        with torch.no_grad():
            inp = proc(images=img, return_tensors="pt").to(device)
            v = model.get_image_features(**inp)[0]
            v = (v / v.norm()).cpu().numpy()
        vecs.append(v)
        rows.append(
            {
                "asset_id": aid,
                "category": e.get("category"),
                "colors": e.get("colors") or [],
                "thumb": str(t),
            }
        )
    np.savez(OUT / "embeds.npz", vecs=np.stack(vecs))
    (OUT / "assets.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    )
    print(f"embedded={len(rows)} missing_thumb={missing}")


if __name__ == "__main__":
    sys.exit(main())
