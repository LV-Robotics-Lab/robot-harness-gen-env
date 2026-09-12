#!/usr/bin/env python3
"""B line step 1 (isaac-smoke env): fetch YCB 025_mug from the NVIDIA assets
server (source USD + textures, provenance-hashed) and reverse-convert to GLB.

Outputs into data/asset_library/_source/ycb_025_mug/ (source mirror) and
--out (mug_visual.glb). Judged by content (PASS line + files), not exit code.
"""

import argparse
import asyncio
import hashlib
import json
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--source-dir", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
src_dir = Path(args.source_dir)
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)

BUCKET = "https://omniverse-content-production.s3-us-west-2.amazonaws.com"
PREFIX = "Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_keys(prefix):
    url = f"{BUCKET}/?list-type=2&prefix={prefix}"
    xml = urllib.request.urlopen(url, timeout=60).read().decode()
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    return [k.text for k in ET.fromstring(xml).iter(f"{{{ns['s3']}}}Key")]


def fetch(key, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(f"{BUCKET}/{key}", dst)
    return dst


manifest = {"source": "NVIDIA Isaac Assets 5.1 (YCB dataset)", "files": {}}
usd_local = fetch(f"{PREFIX}/025_mug.usd", src_dir / "025_mug.usd")
manifest["files"]["025_mug.usd"] = sha256(usd_local)
tex_keys = [
    k for k in list_keys(f"{PREFIX}/Materials/Textures/025") if ".thumbs" not in k
]
for key in tex_keys:
    rel = key.split(f"{PREFIX}/", 1)[1]
    local = fetch(key, src_dir / rel)
    manifest["files"][rel] = sha256(local)
print(f"fetched: 025_mug.usd + {len(tex_keys)} textures")
(src_dir / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2))

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})
try:
    from isaacsim.core.utils.extensions import enable_extension

    enable_extension("omni.kit.asset_converter")
    app.update()
    import omni.kit.asset_converter as ac

    async def convert(src, dst):
        ctx = ac.AssetConverterContext()
        task = ac.get_instance().create_converter_task(str(src), str(dst), None, ctx)
        ok = await task.wait_until_finished()
        if not ok:
            raise RuntimeError(f"{task.get_status()} {task.get_error_message()}")

    from pxr import Usd, UsdGeom

    src_stage = Usd.Stage.Open(str(usd_local))
    up_axis = str(UsdGeom.GetStageUpAxis(src_stage))
    manifest["up_axis"] = up_axis
    (src_dir / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    print(f"source upAxis: {up_axis}")

    dst = out / "mug_visual.glb"
    asyncio.get_event_loop().run_until_complete(convert(usd_local, dst))
    print(f"PASS s8a mug_visual.glb bytes={dst.stat().st_size} sha={sha256(dst)[:12]}")
except Exception as exc:  # noqa: BLE001
    import traceback

    traceback.print_exc()
    print(f"FAIL s8a: {type(exc).__name__}: {exc}")
app.close()
