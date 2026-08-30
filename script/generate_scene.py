#!/usr/bin/env python3
"""Compile text into a deterministic RoboTwin generated-scene package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scene_gen.compiler import CompileFailure, CompileRequest, compile_scene


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--asset-catalog", required=True)
    parser.add_argument("--out-root", default="data/generated_scenes")
    parser.add_argument("--generate-missing-assets", action="store_true")
    parser.add_argument("--generated-objects-root")
    args = parser.parse_args()
    out_root = Path(args.out_root)
    try:
        outcome = compile_scene(
            CompileRequest(
                request=args.prompt,
                seed=args.seed,
                asset_catalog_path=Path(args.asset_catalog),
                out_root=out_root,
                generate_missing_assets=args.generate_missing_assets,
                generated_objects_root=(
                    Path(args.generated_objects_root)
                    if args.generated_objects_root
                    else None
                ),
            )
        )
    except CompileFailure as error:
        failure_id = hashlib.sha256(f"{args.seed}\0{args.prompt}".encode("utf-8")).hexdigest()[:16]
        scene_id = error.details.get("scene_id")
        report_stage = (
            "scene_spec_validation"
            if error.code == "T2E_REQUEST_REJECTED" and error.stage == "parse"
            else error.stage
        )
        report_blocker = (
            "request rejected before grounding"
            if error.code == "T2E_REQUEST_REJECTED" and error.stage == "parse"
            else str(error)
        )
        failure_path = (
            out_root / str(scene_id) / "failure_report.json"
            if scene_id
            else out_root / "_failures" / failure_id / "failure_report.json"
        )
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(
            json.dumps(
                {
                    "schema_version": "robotwin.scene_generation_failure.v1",
                    "status": "fail",
                    "stage": report_stage,
                    "code": error.code,
                    "blocker": report_blocker,
                    "error_type": type(error).__name__,
                    "request": args.prompt,
                    "seed": args.seed,
                    "details": error.details,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"FAIL {failure_path}")
        return 2
    print(
        f"PASS scene_id={outcome.scene_spec.scene_id} "
        f"resolved_sha256={outcome.manifest['resolved_scene_sha256']} "
        f"validation={outcome.static_validation['status']}"
    )
    return 0 if outcome.static_validation["status"] in {"pass", "incomplete"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
