#!/usr/bin/env python3
"""Compatibility entry point; canonical implementation lives in scripts/."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    scripts = Path(__file__).resolve().parent / "scripts"
    sys.path.insert(0, str(scripts))
    runpy.run_path(
        str(scripts / "process_emoji_grid.py"),
        run_name="__main__",
    )
