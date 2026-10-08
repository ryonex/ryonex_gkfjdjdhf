#!/usr/bin/env python3
"""Convenience shim so `python unprom.py ...` works from a clone without
installing.  The real implementation lives in `unprom/cli.py`."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unprom.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
