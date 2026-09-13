#!/usr/bin/env python3
"""Compatibility entry point for generate_scene.run_placement_pipeline."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import follows the legacy source path bootstrap.
from generate_scene.run_placement_pipeline import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
