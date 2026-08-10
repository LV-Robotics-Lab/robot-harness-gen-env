#!/usr/bin/env python3
"""Probe step for the B/C lines (checkpoint 1).

1. Reverse-conversion probe: can omni.kit.asset_converter export USD -> GLB/GLTF/OBJ?
   (tested on the already-assembled bottle.usd)
2. Asset-source probe: list the NVIDIA Isaac assets server for props matching the
   six parseable-but-unavailable env-gen categories
   (calculator/microwave/oven/remote_control/tray/vegetable); record fallbacks.

Writes probe_report.json into --out. Judged by content, not exit code.
"""

import argparse
import asyncio
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--out", required=True)
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})

report = {
    "reverse_export": {},
    "assets_root": None,
    "prop_candidates": [],
    "listing_errors": [],
}
try:
    from isaacsim.core.utils.extensions import enable_extension

    enable_extension("omni.kit.asset_converter")
    app.update()
    import omni.client
    import omni.kit.asset_converter as ac

    # ---- 1. reverse conversion probe ----
    src = Path(
        "/home/jingxiang/yuxin/env-gen-dev/results/_test/"
        "20260802_smoke_bottle_cabinet_glb2usd/bottle.usd"
    )

    async def try_export(dst):
        ctx = ac.AssetConverterContext()
        task = ac.get_instance().create_converter_task(str(src), str(dst), None, ctx)
        ok = await task.wait_until_finished()
        return bool(ok), str(task.get_status()), str(task.get_error_message())

    loop = asyncio.get_event_loop()
    for ext in ("glb", "gltf", "obj"):
        dst = out / f"probe_export.{ext}"
        try:
            ok, status, err = loop.run_until_complete(try_export(dst))
            size = dst.stat().st_size if dst.exists() else 0
            report["reverse_export"][ext] = {
                "ok": ok and size > 0,
                "status": status,
                "error": err,
                "bytes": size,
            }
        except Exception as exc:  # noqa: BLE001
            report["reverse_export"][ext] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        print(f"reverse {ext}: {report['reverse_export'][ext]}")

    # ---- 2. assets-server listing ----
    root = None
    try:
        from isaacsim.storage.native import get_assets_root_path

        root = get_assets_root_path()
    except Exception as exc:  # noqa: BLE001
        report["listing_errors"].append(f"get_assets_root_path: {exc}")
    report["assets_root"] = root
    print(f"assets_root: {root}")

    KEYWORDS = {
        "calculator": ["calculator"],
        "microwave": ["microwave"],
        "oven": ["oven"],
        "remote_control": ["remote"],
        "tray": ["tray"],
        "vegetable": ["vegetable", "carrot", "tomato", "corn", "banana"],
        "cup_fallback": ["mug", "cup"],
    }

    def ls(url):
        result, entries = omni.client.list(url)
        if result != omni.client.Result.OK:
            report["listing_errors"].append(f"list failed: {url} ({result})")
            return []
        return [e.relative_path for e in entries]

    if root:
        props = f"{root}/Isaac/Props"
        top = ls(props)
        print(f"Props dirs ({len(top)}): {top[:40]}")
        # walk two levels, match keywords
        for d in top:
            sub = ls(f"{props}/{d}")
            for name in [d] + sub:
                low = name.lower()
                for cat, kws in KEYWORDS.items():
                    if any(k in low for k in kws):
                        path = f"{props}/{d}" if name == d else f"{props}/{d}/{name}"
                        report["prop_candidates"].append(
                            {"category": cat, "match": name, "url": path}
                        )
        for c in report["prop_candidates"]:
            print(f"candidate: {c['category']} <- {c['url']}")

    (out / "probe_report.json").write_text(json.dumps(report, indent=2))
    print("PROBE_DONE")
except Exception as exc:  # noqa: BLE001
    import traceback

    traceback.print_exc()
    (out / "probe_report.json").write_text(json.dumps(report, indent=2))
    print(f"PROBE_PARTIAL {type(exc).__name__}: {exc}")
app.close()
