#!/usr/bin/env python3
"""IR integration check: bundles must validate as openxsim AssetBundle and
representation_for("isaacsim") must resolve to the converted USD — i.e. the
Transfer-side "no existing USD representation" blocker is gone.
"""

import json
import sys
from pathlib import Path

sys.path.insert(
    0, "/home/jingxiang/yuxin/env-gen-dev/2_sim_migration/openxsim/source/agenticsim"
)
from agenticsim.openxsim.ir import AssetBundle  # noqa: E402

ok = True
for arg in sys.argv[1:]:
    data = json.loads(Path(arg).read_text())
    try:
        bundle = AssetBundle.from_dict(data)
        bundle.validate()
        rep = bundle.representation_for("isaacsim", ("usd", "usda", "usdc"))
        if rep is None:
            print(f"FAIL {arg}: no isaacsim USD representation")
            ok = False
        elif not Path(rep.uri).is_file():
            print(f"FAIL {arg}: representation uri missing on disk: {rep.uri}")
            ok = False
        else:
            print(
                f"PASS {Path(arg).name}: isaacsim rep -> {rep.uri} sha={rep.sha256[:12]}"
            )
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {arg}: {type(exc).__name__}: {exc}")
        ok = False
print("PASS s5" if ok else "FAIL s5")
sys.exit(0 if ok else 1)
