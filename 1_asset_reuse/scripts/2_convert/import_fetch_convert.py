#!/usr/bin/env python3
"""Batch external import, phase 1 (isaac-smoke env).

Reads configs/external_manifest.json, mirrors each group's server directory
(all keys under the prefix except .thumbs; provenance-hashed), reads each
source USD's upAxis, converts every item to GLB in one SimulationApp session.
Writes staging_manifest.json for phase 2. Judged by content: PASS/FAIL lines
per item plus a final summary line.
"""

import argparse
import asyncio
import hashlib
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--manifest", required=True)
parser.add_argument("--source-root", required=True, help="data/asset_library/_source")
parser.add_argument("--staging", required=True, help="dir for converted GLBs")
args = parser.parse_args()

BUCKET = "https://omniverse-content-production.s3-us-west-2.amazonaws.com"
manifest = json.loads(Path(args.manifest).read_text())
source_root = Path(args.source_root)
staging = Path(args.staging)
staging.mkdir(parents=True, exist_ok=True)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_all_keys(prefix):
    keys, token = [], None
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    while True:
        url = f"{BUCKET}/?list-type=2&prefix={urllib.parse.quote(prefix)}"
        if token:
            url += f"&continuation-token={urllib.parse.quote(token)}"
        root = ET.fromstring(urllib.request.urlopen(url, timeout=120).read())
        keys += [k.text for k in root.iter(f"{ns}Key")]
        token_el = root.find(f"{ns}NextContinuationToken")
        if token_el is None:
            return keys
        token = token_el.text


def mirror_group(group):
    gdir = source_root / group["name"]
    files = {}
    keys = [
        k
        for k in list_all_keys(group["prefix"] + "/")
        if "/.thumbs/" not in k and not k.endswith("/")
    ]
    for key in keys:
        rel = key[len(group["prefix"]) + 1 :]
        dst = gdir / rel
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(f"{BUCKET}/{key}", dst)
        files[rel] = sha256(dst)
    (gdir / "SOURCE_MANIFEST.json").write_text(
        json.dumps({"prefix": group["prefix"], "files": files}, indent=2)
    )
    print(f"mirrored {group['name']}: {len(keys)} files")
    return gdir


mirrors = {g["name"]: mirror_group(g) for g in manifest["groups"]}

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})
results = []
try:
    from isaacsim.core.utils.extensions import enable_extension

    enable_extension("omni.kit.asset_converter")
    app.update()
    import omni.kit.asset_converter as ac
    from pxr import Usd, UsdGeom

    async def convert(src, dst):
        ctx = ac.AssetConverterContext()
        task = ac.get_instance().create_converter_task(str(src), str(dst), None, ctx)
        ok = await task.wait_until_finished()
        if not ok:
            raise RuntimeError(f"{task.get_status()} {task.get_error_message()}")

    loop = asyncio.get_event_loop()
    for group in manifest["groups"]:
        gdir = mirrors[group["name"]]
        for item in group["items"]:
            usd_local = gdir / item["usd"]
            glb = staging / f"{item['asset']}_m{item['model']}.glb"
            rec = {
                "group": group["name"],
                **item,
                "usd_local": str(usd_local),
                "usd_sha256": sha256(usd_local),
                "glb": str(glb),
            }
            try:
                stage = Usd.Stage.Open(str(usd_local))
                rec["up_axis"] = str(UsdGeom.GetStageUpAxis(stage))
                loop.run_until_complete(convert(usd_local, glb))
                rec["glb_sha256"] = sha256(glb)
                rec["status"] = "converted"
                print(f"PASS convert {item['usd']} -> {glb.name} (up={rec['up_axis']})")
            except Exception as exc:  # noqa: BLE001
                rec["status"] = "convert_failed"
                rec["error"] = f"{type(exc).__name__}: {exc}"
                print(f"FAIL convert {item['usd']}: {rec['error']}")
            results.append(rec)
finally:
    (staging / "staging_manifest.json").write_text(json.dumps(results, indent=2))
    ok = sum(1 for r in results if r["status"] == "converted")
    print(f"PHASE1 {ok}/{len(results)} converted")
    app.close()
