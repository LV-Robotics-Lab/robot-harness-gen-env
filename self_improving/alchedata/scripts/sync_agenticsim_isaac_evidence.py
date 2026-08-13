#!/usr/bin/env python3
"""Snapshot AgenticSim Awesome Isaac evidence for the Open X Sim package."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGENTICSIM_ROOT = ROOT / "fixtures" / "retired_agenticsim"
ARCHIVED_SOURCE_COMMIT = "6d952560870b0a9b71f707f0476d28425bfab256"
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "openxsim" / "agenticsim_awesome_isaac_snapshot.json"
)
SOURCE_FILES = (
    "docs/awesome_isaac_environment_catalog.json",
    "docs/awesome_isaac_agenticsim_intake.json",
    "docs/awesome_isaac_runtime_evidence.json",
    "docs/awesome_isaac_environment_audit.md",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def build_snapshot(agenticsim_root: Path = DEFAULT_AGENTICSIM_ROOT) -> dict[str, Any]:
    source_files = []
    for relative in SOURCE_FILES:
        path = agenticsim_root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        source_files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    catalog = read_json(agenticsim_root / SOURCE_FILES[0])
    intake = read_json(agenticsim_root / SOURCE_FILES[1])
    runtime = read_json(agenticsim_root / SOURCE_FILES[2])
    runtime_by_slug = {row["slug"]: row for row in runtime["repositories"]}
    current_rows = [
        {
            "slug": slug,
            "canonical_slug": row.get("canonical_slug", slug),
            "head_oid": row["head_oid"],
            "task": row["task"],
            "runtime_status": row["runtime_status"],
            "runtime_passed": row["runtime_passed"],
            "open_source_runtime_verdict": row["open_source_runtime_verdict"],
            "open_source_closure_status": row["open_source_closure_status"],
            "open_source_closure_confirmed": row["open_source_closure_confirmed"],
            "academic_use_accepted": row["academic_use_accepted"],
            "academic_use_status": row["academic_use_status"],
            "academic_use_license_advisory": row["academic_use_license_advisory"],
            "required_asset_license_status": row["required_asset_license_status"],
            "asset_license_gap": row["asset_license_gap"],
            "asset_use_restriction": row["asset_use_restriction"],
            "source_tree_modified": row["source_tree_modified"],
            "steps_requested": row["steps_requested"],
            "steps_completed": row["steps_completed"],
            "render_passed": row["render_passed"],
            "conditions": row["conditions"],
            "remaining_blockers": row["remaining_blockers"],
            "error": row["error"],
            "license_findings": row["license_findings"],
            "artifacts": row["artifacts"],
        }
        for slug, row in sorted(runtime_by_slug.items())
    ]

    if (agenticsim_root / ".git").exists():
        head = git_output(agenticsim_root, "rev-parse", "HEAD")
        tracked_dirty = git_output(
            agenticsim_root, "status", "--short", "--untracked-files=no"
        )
        if tracked_dirty:
            raise RuntimeError(
                "AgenticSim tracked worktree must be clean before evidence sync"
            )
    else:
        head = ARCHIVED_SOURCE_COMMIT

    return {
        "schema_version": "alchedata.openxsim_agenticsim_awesome_isaac_snapshot.v3",
        "status": "pass_agenticsim_awesome_isaac_snapshot",
        "source_repository": "https://github.com/LV-Robotics-Lab/AgenticSim",
        "source_commit": head,
        "source_files": source_files,
        "usage_policy": runtime["usage_policy"],
        "catalog_summary": catalog["summary"],
        "source_list_audit": catalog["source_list_audit"],
        "intake_summary": intake["summary"],
        "runtime_summary": runtime["summary"],
        "runtime_baseline": runtime["baseline"],
        "current_runtime_rows": current_rows,
        "claim_boundary": (
            "The snapshot pins AgenticSim catalog and runtime evidence by commit and file hash. Under the local "
            "noncommercial academic-use policy, every technical runtime pass is admitted and license findings remain "
            "provenance advisories. Strict open-source closure stays separately reported. The snapshot proves the "
            "recorded RTX baseline and named bounded candidate smokes, not learned policy quality or a complete PEARL "
            "command loop over Isaac Sim."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agenticsim-root", type=Path, default=DEFAULT_AGENTICSIM_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_snapshot(args.agenticsim_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "source_commit": report["source_commit"],
                "repositories": report["catalog_summary"]["repository_count"],
                "runtime_pass": report["runtime_summary"]["runtime_pass_count"],
                "runtime_blocked": report["runtime_summary"]["runtime_blocked_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
