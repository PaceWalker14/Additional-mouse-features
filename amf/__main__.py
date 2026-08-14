"""Entry point.  `python -m amf [--tray]`"""

from __future__ import annotations

import ctypes
import sys

from . import config as cfgmod
from . import winapi as w

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\AdditionalMouseFeatures.SingleInstance"


def _claim_single_instance():
    """Return the mutex handle, or None if another copy is already running."""
    handle = w.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if handle and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        # Ask the running copy to show itself, then bow out.
        hwnd = w.user32.FindWindowW("AMFTrayWindow", None)
        if hwnd:
            w.user32.PostMessageW(hwnd, w.WM_APP + 1, 0, w.WM_LBUTTONUP)
        return None
    return handle


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not sys.platform.startswith("win"):
        print("Additional Mouse Features only runs on Windows.", file=sys.stderr)
        return 1

    if _claim_single_instance() is None:
        return 0

    w.enable_dpi_awareness()

    from .ui import App  # imported late so the checks above run first

    cfg = cfgmod.load()
    hidden = "--tray" in argv and cfg.get("start_minimised", True)
    App(start_hidden=hidden).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
