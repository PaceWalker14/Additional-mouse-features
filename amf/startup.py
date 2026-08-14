"""Run-on-startup, via the per-user Run key (no admin rights needed)."""

from __future__ import annotations

import os
import sys
import winreg

from .config import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _quote(path: str) -> str:
    return f'"{path}"' if " " in path and not path.startswith('"') else path


def startup_command() -> str:
    """The command line Windows should run at logon."""
    if getattr(sys, "frozen", False):          # PyInstaller & friends
        return f"{_quote(sys.executable)} --tray"

    entry = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "run.pyw")
    # pythonw.exe keeps it windowless; fall back to whatever launched us.
    interp = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(interp):
        interp = sys.executable
    return f"{_quote(interp)} {_quote(entry)} --tray"


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Add or remove the Run entry. Returns True on success."""
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def refresh_if_enabled() -> None:
    """Keep the stored command in step if the app has been moved."""
    if is_enabled():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                current, _ = winreg.QueryValueEx(key, APP_NAME)
            if current != startup_command():
                set_enabled(True)
        except OSError:
            pass
