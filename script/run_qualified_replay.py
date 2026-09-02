#!/usr/bin/env python3
"""Run the checked-in replay qualification's fixed case on its bound deployment."""

# ruff: noqa: E402, I001 -- checkout bootstrap must precede the project import.

from __future__ import annotations

import sys
from pathlib import Path

_CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CHECKOUT_ROOT))

from self_improving.qualified_replay_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
