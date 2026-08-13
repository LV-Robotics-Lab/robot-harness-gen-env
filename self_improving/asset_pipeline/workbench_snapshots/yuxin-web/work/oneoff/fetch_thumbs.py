#!/usr/bin/env python3
"""One-off (work/oneoff/): mirror the NVIDIA asset server's own thumbnails.

Why this exists: a1_providers currently DISCARDS every `.thumbs` key as noise
(`if ".thumbs" in key: continue`, elimination code `thumbs_artifact`). But the
server publishes, next to each prop USD, a pre-rendered 256x256 PNG preview at
a deterministic path:

    Assets/.../Props/Beaker/beaker_500ml.usd
    Assets/.../Props/Beaker/.thumbs/256x256/beaker_500ml.usd.png

Measured over the existing index: 465 of 495 prop USDs (94%) have one, median
55 KB, 22.6 MB for the entire corpus. That is a free visual signal for the
whole searchable universe -- no USD download, no Kit session, no conversion.

Read-only with respect to the pipeline: writes only into --out.
"""

import argparse
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BUCKET = "https://omniverse-content-production.s3-us-west-2.amazonaws.com"


def thumb_key_for(usd_key):
    """<dir>/<name>.usd -> <dir>/.thumbs/256x256/<name>.usd.png"""
    head, _, name = usd_key.rpartition("/")
    return f"{head}/.thumbs/256x256/{name}.png"


def build_map(index_path):
    """usd_key -> thumb_key, for every prop USD whose thumbnail the index
    actually lists. Never guesses a URL that was not observed in the listing:
    a 404 would otherwise look like a missing asset rather than a missing
    thumbnail."""
    data = json.loads(Path(index_path).read_text())
    listed = set()
    usds = []
    for _prefix, entries in data.items():
        for key, _size in entries:
            if ".thumbs" in key:
                if key.endswith(".png"):
                    listed.add(key)
            elif key.endswith(".usd"):
                usds.append(key)
    pairs = {}
    for usd in usds:
        t = thumb_key_for(usd)
        if t in listed:
            pairs[usd] = t
    return pairs, len(usds)


def fetch(args):
    pairs, total_usd = build_map(args.index)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "thumbs_manifest.json"

    def local_name(usd_key):
        return urllib.parse.quote(usd_key, safe="") + ".png"

    todo = [(u, t) for u, t in pairs.items() if not (out / local_name(u)).exists()]
    print(
        f"prop USDs {total_usd} | with thumbnail {len(pairs)} "
        f"({len(pairs) / total_usd:.0%}) | to download {len(todo)}"
    )

    failures = []

    def one(item):
        usd_key, thumb_key = item
        url = f"{BUCKET}/{urllib.parse.quote(thumb_key)}"
        dest = out / local_name(usd_key)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                dest.write_bytes(r.read())
        except Exception as exc:  # noqa: BLE001 -- collected, never swallowed
            failures.append({"usd": usd_key, "thumb": thumb_key, "error": repr(exc)})

    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(one, todo))

    manifest = {
        "bucket": BUCKET,
        "index": str(args.index),
        "prop_usd_total": total_usd,
        "with_thumbnail": len(pairs),
        "downloaded": sum(1 for u in pairs if (out / local_name(u)).exists()),
        "failures": failures,
        "pairs": {u: {"thumb_key": t, "file": local_name(u)} for u, t in pairs.items()},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"downloaded {manifest['downloaded']}/{len(pairs)}  failures {len(failures)}")
    print(f"manifest -> {manifest_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", default="data/asset_index/nvidia_keys.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    fetch(ap.parse_args())


if __name__ == "__main__":
    main()
