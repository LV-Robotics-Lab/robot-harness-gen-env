#!/usr/bin/env python3
"""Write an adapter failure memory from an executed no-memory arm."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from pose_conditioned_trajectory_policy import read_json, sha256_file, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--conversion-report", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    controller_path = Path(args.controller).expanduser().resolve()
    conversion_path = Path(args.conversion_report).expanduser().resolve()
    controller = read_json(controller_path)
    conversion = read_json(conversion_path)
    if controller["mode"] != "no-memory" or controller["selected_adapter"] != "swap_red_blue":
        raise ValueError("Source arm is not the declared no-memory default")
    if controller["execution_count"] != 3 or controller["success_count"] != 0:
        raise ValueError("Source arm must be an executed 0/3 failure")
    repair = conversion.get("native_jpeg_color_repair", {})
    if "swap" not in str(repair).lower() or "rgb" not in str(repair).lower():
        raise ValueError("Conversion report does not establish the RGB repair contract")

    memory = {
        "schema_version": "alchedata.failure_memory.v0",
        "status": "active_failure_memory",
        "memory_id": "rgb_adapter_double_swap_failure_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_failure": {
            "controller": str(controller_path),
            "controller_sha256": sha256_file(controller_path),
            "evaluator_report": controller["evaluator_report"],
            "evaluator_report_sha256": controller["evaluator_report_sha256"],
            "success_count": 0,
            "episode_count": 3,
            "selected_adapter": controller["selected_adapter"],
        },
        "supporting_contract": {
            "conversion_report": str(conversion_path),
            "conversion_report_sha256": sha256_file(conversion_path),
            "native_jpeg_color_repair": repair,
        },
        "applicability": {
            "task_id": controller["fixed_protocol"]["task_id"],
            "checkpoint_sha256": controller["fixed_protocol"]["checkpoint_sha256"],
            "runtime_camera_source": controller["fixed_protocol"]["runtime_camera_source"],
        },
        "recommendation": {
            "runtime_color_adapter": "identity",
            "reason": (
                "The synchronized HDF5 conversion already repairs stored JPEG BGR ordering into runtime RGB. "
                "Applying swap_red_blue again at runtime is a double swap; preserve runtime RGB with identity."
            ),
        },
        "claim_boundary": (
            "This memory records one executed failure and a matching data-contract correction. It is not a "
            "general semantic memory, retrieval benchmark, or proof that memory improves unseen failure classes."
        ),
    }
    out_path = Path(args.out).expanduser().resolve()
    write_json(out_path, memory)
    print(json.dumps({"status": memory["status"], "memory_id": memory["memory_id"], "out": str(out_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
