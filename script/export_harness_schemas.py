#!/usr/bin/env python3
"""Export or verify the committed Harness MVP JSON Schema snapshots."""

from __future__ import annotations

import argparse
from pathlib import Path

from self_improving.harness.schema_catalog import (
    DEFAULT_SCHEMA_ROOT,
    SchemaSnapshotMismatch,
    export_schema_snapshots,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_SCHEMA_ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        paths = export_schema_snapshots(args.out, check=args.check)
    except SchemaSnapshotMismatch as error:
        print(f"FAIL {error}")
        return 1
    action = "verified" if args.check else "wrote"
    print(f"{action} {len(paths)} Harness schema snapshots in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
