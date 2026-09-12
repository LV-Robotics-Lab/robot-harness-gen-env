"""Generate or verify canonical x2env schema snapshots."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from self_improving.harness.x2env.schema_export import export  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "self_improving/harness/x2env/json_schemas"
    )
    args = parser.parse_args()
    drift = export(args.output, check=args.check)
    for name in drift:
        print(f"schema drift: {name}")
    return int(bool(drift))


if __name__ == "__main__":
    raise SystemExit(main())
