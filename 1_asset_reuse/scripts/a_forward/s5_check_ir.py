#!/usr/bin/env python3
"""IR integration check: bundles must validate as openxsim AssetBundle and
representation_for("isaacsim") must resolve to the converted USD — i.e. the
Transfer-side "no existing USD representation" blocker is gone.

Each CLI arg may be either a legacy flattened per-model bundle dict
(back-compat) or an authoritative per-asset ledger (has a top-level "models"
list) — the latter is unpacked via lib.ledger.to_ir_bundles into one IR
bundle per model, each checked independently.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import ledger  # noqa: E402

sys.path.insert(
    0, "/home/jingxiang/yuxin/env-gen-dev/shared/openxsim/source/agenticsim"
)
from agenticsim.openxsim.ir import AssetBundle  # noqa: E402


def to_bundles(data):
    """A per-asset ledger (top-level "models" list) unpacks into one IR
    bundle per model via ledger.to_ir_bundles; a legacy flattened bundle
    dict is used as-is (back-compat)."""
    if isinstance(data, dict) and "models" in data:
        return ledger.to_ir_bundles(data)
    return [data]


ok = True
for arg in sys.argv[1:]:
    data = json.loads(Path(arg).read_text())
    is_ledger = isinstance(data, dict) and "models" in data
    for bundle_data in to_bundles(data):
        label = f"{arg}::{bundle_data['asset_id']}" if is_ledger else arg
        pass_label = (
            f"{Path(arg).name}::{bundle_data['asset_id']}"
            if is_ledger
            else Path(arg).name
        )
        try:
            bundle = AssetBundle.from_dict(bundle_data)
            bundle.validate()
            rep = bundle.representation_for("isaacsim", ("usd", "usda", "usdc"))
            if rep is None:
                print(f"FAIL {label}: no isaacsim USD representation")
                ok = False
            elif not Path(rep.uri).is_file():
                print(f"FAIL {label}: representation uri missing on disk: {rep.uri}")
                ok = False
            else:
                print(
                    f"PASS {pass_label}: isaacsim rep -> {rep.uri} sha={rep.sha256[:12]}"
                )
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {label}: {type(exc).__name__}: {exc}")
            ok = False
print("PASS s5" if ok else "FAIL s5")
sys.exit(0 if ok else 1)
