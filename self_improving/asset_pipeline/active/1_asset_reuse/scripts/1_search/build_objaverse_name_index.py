#!/usr/bin/env python3
"""Build the full-Objaverse NAME index: 798k community objects searchable by
what their authors called them, not only by the 46k-object LVIS category
subset.

Why: the LVIS subset is category-curated (hammer, umbrella, ...) but a
request like "tissue box" falls outside its 1,156 categories even though
Sketchfab hosts plenty of tissue-box models. The per-object names live in the
~160 metadata shards (downloaded once, cached under metadata/); this tool
flattens them into two small artifacts the retrieval provider can load lazily:

  name_index.json.gz   row-aligned lists: uids, names, license slugs
  token_index.json.gz  lowercased name word -> [row ...] postings

Rebuild whenever the shard mirror changes. Reads shards, writes only the two
index files."""

import argparse
import gzip
import json
import re
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from runtime_config import OBJAVERSE_DATA_ROOT  # noqa: E402

WORD = re.compile(r"[a-z0-9]+")


def one_shard(path):
    out = []
    data = json.load(gzip.open(path))
    for uid, ann in data.items():
        name = str(ann.get("name") or "").strip()
        if not name:
            continue
        out.append((uid, name[:120], str(ann.get("license") or "").lower()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data-dir",
        default=str(OBJAVERSE_DATA_ROOT),
    )
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    d = Path(a.data_dir)
    shards = sorted((d / "metadata").glob("*.json.gz"))
    print(f"分片: {len(shards)}")
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor(a.workers) as ex:
        for part in ex.map(one_shard, shards):
            rows.extend(part)
    print(f"[{time.time() - t0:.0f}s] 物体: {len(rows)}")

    uids = [r[0] for r in rows]
    names = [r[1] for r in rows]
    slugs = [r[2] for r in rows]
    tokens: dict[str, list[int]] = {}
    for i, name in enumerate(names):
        for w in set(WORD.findall(name.lower())):
            tokens.setdefault(w, []).append(i)

    with gzip.open(d / "name_index.json.gz", "wt") as f:
        json.dump({"uids": uids, "names": names, "licenses": slugs}, f)
    with gzip.open(d / "token_index.json.gz", "wt") as f:
        json.dump(tokens, f)
    print(
        f"[{time.time() - t0:.0f}s] name_index {len(uids)} 行, "
        f"token_index {len(tokens)} 词 -> {d}"
    )


if __name__ == "__main__":
    main()
