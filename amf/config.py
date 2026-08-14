"""Config model: the chord catalogue, defaults, and JSON load/save."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any

APP_NAME = "AdditionalMouseFeatures"
APP_TITLE = "Additional Mouse Features"

CONFIG_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

CONFIG_VERSION = 1

# Buttons, in the canonical order used to build chord keys.
BUTTON_ORDER = ["x1", "x2", "middle", "left", "right"]

# Only these can *start* a chord. A chord always needs one, because we have to
# swallow the anchor's click until we know whether a chord is forming — and
# silently swallowing plain left/right clicks would break normal mousing.
ANCHOR_BUTTONS = ("x1", "x2", "middle")

BUTTON_LABELS = {
    "x1": "Back side",
    "x2": "Front side",
    "middle": "Middle",
    "left": "Left click",
    "right": "Right click",
}

BUTTON_GLYPHS = {"x1": "◀", "x2": "▶", "middle": "◉", "left": "L", "right": "R"}

DIRECTIONS = ["swipe_left", "swipe_right", "swipe_up", "swipe_down", "tap"]

DIRECTION_LABELS = {
    "swipe_left": "Swipe left",
    "swipe_right": "Swipe right",
    "swipe_up": "Swipe up",
    "swipe_down": "Swipe down",
    "tap": "Tap (no movement)",
}

DIRECTION_GLYPHS = {
    "swipe_left": "←", "swipe_right": "→",
    "swipe_up": "↑", "swipe_down": "↓", "tap": "•",
}

# Every chord the UI offers. Keys must already be in BUTTON_ORDER order.
COMBOS: list[str] = [
    "x2+left",
    "x2+right",
    "x2+left+right",
    "x1+left",
    "x1+right",
    "x1+left+right",
    "x1+x2",
    "x1+middle",
    "x2+middle",
    "middle+left",
    "middle+right",
]


def chord_key(buttons) -> str:
    """Canonical chord id for a set of button names."""
    return "+".join(b for b in BUTTON_ORDER if b in buttons)


def chord_label(key: str) -> str:
    return " + ".join(BUTTON_LABELS.get(b, b) for b in key.split("+"))


def chord_glyphs(key: str) -> str:
    return " ".join(BUTTON_GLYPHS.get(b, "?") for b in key.split("+"))


def _binding(enabled: bool = False, **directions: Any) -> dict:
    b: dict[str, Any] = {"enabled": enabled}
    for d in DIRECTIONS:
        spec = directions.get(d, "none")
        if isinstance(spec, str):
            spec = {"action": spec, "param": ""}
        b[d] = spec
    return b


def default_config() -> dict:
    bindings = {key: _binding() for key in COMBOS}

    # Front side + left click: the trackpad-style app switcher.
    bindings["x2+left"] = _binding(
        True,
        swipe_left="app_switch_prev",
        swipe_right="app_switch_next",
        swipe_up="task_view",
        swipe_down="show_desktop",
        tap="none",
    )
    # Back side + left click: tabs.
    bindings["x1+left"] = _binding(
        True,
        swipe_left="prev_tab",
        swipe_right="next_tab",
        swipe_up="new_tab",
        swipe_down="close_tab",
        tap="reopen_tab",
    )
    # Both side buttons: virtual desktops.
    bindings["x1+x2"] = _binding(
        True,
        swipe_left="prev_desktop",
        swipe_right="next_desktop",
        swipe_up="task_view",
        swipe_down="show_desktop",
        tap="none",
    )

    return {
        "version": CONFIG_VERSION,
        "enabled": True,
        "run_on_startup": False,
        "start_minimised": True,
        "swipe_threshold": 45,      # px of travel before the first action fires
        "repeat_threshold": 65,     # px of further travel per repeat
        "tap_max_ms": 300,          # chord released faster than this = a tap
        "freeze_cursor": True,      # pin the pointer while a chord is held
        "ignore_injected": True,    # ignore software-generated mouse input
        "bindings": bindings,
    }


# ---------------------------------------------------------------------------


def _merge(defaults: dict, loaded: Any) -> dict:
    """Fill any missing keys from defaults; ignore junk in the saved file."""
    if not isinstance(loaded, dict):
        return copy.deepcopy(defaults)
    out = copy.deepcopy(defaults)
    for key, dval in defaults.items():
        if key not in loaded:
            continue
        lval = loaded[key]
        if isinstance(dval, dict) and isinstance(lval, dict):
            out[key] = _merge(dval, lval)
        elif isinstance(dval, bool):
            if isinstance(lval, bool):
                out[key] = lval
        elif isinstance(dval, int) and not isinstance(dval, bool):
            if isinstance(lval, (int, float)) and not isinstance(lval, bool):
                out[key] = int(lval)
        elif isinstance(dval, str):
            if isinstance(lval, str):
                out[key] = lval
        else:
            out[key] = lval
    return out


def load() -> dict:
    defaults = default_config()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return defaults
    return _merge(defaults, raw)


def save(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    # Write to a temp file in the same directory, then replace, so a crash
    # mid-write can't leave a half-written config behind.
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
