#!/usr/bin/env python3
"""Compile prepared EnvironmentPackages to SAPIEN and execute runtime checks."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap_repo_source

bootstrap_repo_source()

from agenticsim.openxsim.backends import compile_package  # noqa: E402
from agenticsim.openxsim.ir import EnvironmentPackage  # noqa: E402


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--steps", type=int, default=120)
    args = parser.parse_args()
    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    records: list[dict[str, Any]] = []
    for package_path in sorted(input_dir.glob("*.json")):
        package = EnvironmentPackage.read_json(package_path)
        case_id = package_path.stem
        scene_dir = output_dir / case_id
        if scene_dir.exists():
            shutil.rmtree(scene_dir)
        record: dict[str, Any] = {
            "case_id": case_id,
            "package_id": package.package_id,
            "package_digest": package.digest(),
            "status": "fail",
        }
        try:
            result = compile_package(package, scene_dir / "compiled", ("sapien",), strict=True)["sapien"]
            command = [sys.executable, *result.runtime_command[1:], "--steps", str(args.steps)]
            environment = os.environ.copy()
            with (scene_dir / "runtime.log").open("w", encoding="utf-8") as log:
                completed = subprocess.run(
                    command,
                    cwd=Path(__file__).resolve().parents[1],
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            runtime_path = Path(result.manifest_path).parent / "runtime_evidence.json"
            runtime = json.loads(runtime_path.read_text(encoding="utf-8")) if runtime_path.is_file() else None
            runtime_checks = {
                "reset_ok": bool(runtime and runtime.get("reset_ok") is True),
                "step_ok": bool(runtime and runtime.get("step_ok") is True),
                "trajectory_nonempty": bool(runtime and runtime.get("trajectory")),
            }
            runtime_pass = completed.returncode == 0 and all(runtime_checks.values())
            record.update(
                {
                    "status": "pass" if runtime_pass else "fail",
                    "compile_result": result.to_dict(),
                    "runtime_exit_code": completed.returncode,
                    "runtime_evidence_path": str(runtime_path),
                    "runtime_status": "pass" if runtime_pass else "fail",
                    "runtime_checks": runtime_checks,
                    "action_interface_bound": runtime.get("action_interface_bound") if runtime else None,
                }
            )
        except Exception as exc:
            record["failure"] = repr(exc)
        records.append(record)
        write_json(output_dir / "remote_summary.partial.json", {"records": records, "complete": False})
        print(f"{record['status'].upper()} {package.package_id}", flush=True)
    report = {
        "schema": "agenticsim.crosssim_sapien_remote.v1",
        "status": "pass" if records and all(item["status"] == "pass" for item in records) else "fail",
        "record_count": len(records),
        "records": records,
        "complete": True,
    }
    write_json(output_dir / "remote_summary.json", report)
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
