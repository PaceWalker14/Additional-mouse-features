"""Windowless launcher - double-click this, or run it with --tray at startup."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from amf.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
