"""CLI for auditing a consolidated checkout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .registry import audit_repository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    report = audit_repository(args.repo_root)
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for module in report["modules"]:
            print(f"{module['status']:>13}  {module['name']:<22} {module['path']}")
        print("ready" if report["ready"] else "incomplete")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
