#!/usr/bin/env python3
"""Compile text into a deterministic RoboTwin generated-scene package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from scene_gen.asset_generator import ensure_assets_for_scene
from scene_gen.builder import build_scene_package
from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.schema import SceneSpecError
from scene_gen.solver import SceneSolveError, solve_scene
from scene_gen.validator import validate_resolved_scene


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
        spec = parse_rule_based(args.prompt, seed=args.seed)
    except (SceneSpecError, ValidationError) as error:
        failure_id = hashlib.sha256(f"{args.seed}\0{args.prompt}".encode("utf-8")).hexdigest()[:16]
        failure_path = out_root / "_failures" / failure_id / "failure_report.json"
        details = error.errors() if isinstance(error, ValidationError) else [{"message": str(error)}]
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(
            json.dumps(
                {
                    "schema_version": "robotwin.scene_generation_failure.v1",
                    "status": "fail",
                    "stage": "scene_spec_validation",
                    "blocker": "request rejected before grounding",
                    "error_type": type(error).__name__,
                    "request": args.prompt,
                    "seed": args.seed,
                    "details": details,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"FAIL {failure_path}")
        return 2
    catalog = load_catalog(Path(args.asset_catalog))
    out_dir = out_root / spec.scene_id
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.generate_missing_assets:
        catalog, generation_report = ensure_assets_for_scene(
            spec,
            catalog,
            objects_root=Path(args.generated_objects_root) if args.generated_objects_root else None,
        )
        (out_dir / "asset_generation_report.json").write_text(
            json.dumps(generation_report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (out_dir / "effective_asset_catalog.json").write_text(
            json.dumps(catalog.canonical_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    try:
        resolved = solve_scene(spec, catalog)
    except SceneSolveError as error:
        (out_dir / "failure_report.json").write_text(
            json.dumps(error.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"FAIL {out_dir / 'failure_report.json'}")
        return 2
    manifest = build_scene_package(spec, resolved, out_dir)
    report = validate_resolved_scene(resolved, package_root=out_dir, require_runtime=False)
    (out_dir / "validation_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"PASS scene_id={spec.scene_id} resolved_sha256={manifest['resolved_scene_sha256']} "
        f"validation={report['status']}"
    )
    return 0 if report["status"] in {"pass", "incomplete"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
