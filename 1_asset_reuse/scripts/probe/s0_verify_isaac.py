#!/usr/bin/env python3
"""Checkpoint-1 verification: headless SimulationApp boots on RTX 5090 and the
asset_converter + URDF importer extensions load. Prints ISAAC_OK on success."""

import sys

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})
code = 1
try:
    import torch  # noqa: F401  (isaac ships its own torch; cuda check below)

    from isaacsim.core.utils.extensions import enable_extension

    ok_conv = enable_extension("omni.kit.asset_converter")
    urdf_ext = None
    for ext in ("isaacsim.asset.importer.urdf", "omni.importer.urdf"):
        try:
            if enable_extension(ext):
                urdf_ext = ext
                break
        except Exception:  # noqa: BLE001
            continue
    app.update()

    import importlib.metadata as im

    gpu_ok = True
    try:
        gpu_ok = torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        gpu_ok = "unknown"

    print(
        f"ISAAC_OK version={im.version('isaacsim')} asset_converter={ok_conv} "
        f"urdf_ext={urdf_ext} torch_cuda={gpu_ok}"
    )
    code = 0 if (ok_conv and urdf_ext) else 1
except Exception as exc:  # noqa: BLE001
    print(f"ISAAC_FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
    import traceback

    traceback.print_exc()
app.close()
sys.exit(code)
