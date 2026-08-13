"""Compatibility wrapper for generate_scene.schemas."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generate_scene.schemas import *  # noqa: F401,F403

if __name__ == "__main__":
    from generate_scene.schemas import main

    raise SystemExit(main())
