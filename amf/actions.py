"""The catalogue of assignable actions, and the thing that performs them."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass

from . import winapi as w


@dataclass(frozen=True)
class ActionDef:
    id: str
    label: str
    group: str
    param: str | None = None      # None | "keys" | "path"
    repeatable: bool = False      # can fire again while the same chord is held
    note: str = ""

    @property
    def menu_label(self) -> str:
        if self.group == "":
            return self.label
        return f"{self.group}  •  {self.label}"


ACTIONS: list[ActionDef] = [
    ActionDef("none", "Do nothing", ""),

    ActionDef("app_switch_next", "App switcher → next", "Windows",
              repeatable=True,
              note="Holds Alt down like the trackpad switcher; keep swiping to "
                   "move through the list, release the chord to commit."),
    ActionDef("app_switch_prev", "App switcher ← previous", "Windows",
              repeatable=True),
    ActionDef("task_view", "Task view (Win+Tab)", "Windows"),
    ActionDef("show_desktop", "Show desktop", "Windows"),
    ActionDef("next_desktop", "Next virtual desktop", "Windows", repeatable=True),
    ActionDef("prev_desktop", "Previous virtual desktop", "Windows", repeatable=True),

    ActionDef("next_tab", "Next tab", "Tabs", repeatable=True),
    ActionDef("prev_tab", "Previous tab", "Tabs", repeatable=True),
    ActionDef("new_tab", "New tab", "Tabs"),
    ActionDef("close_tab", "Close tab", "Tabs"),
    ActionDef("reopen_tab", "Reopen closed tab", "Tabs"),

    ActionDef("nav_back", "Back", "Navigation"),
    ActionDef("nav_forward", "Forward", "Navigation"),
    ActionDef("refresh", "Refresh", "Navigation"),

    ActionDef("minimize_window", "Minimise window", "Window"),
    ActionDef("maximize_window", "Maximise window", "Window"),
    ActionDef("snap_left", "Snap window left", "Window"),
    ActionDef("snap_right", "Snap window right", "Window"),
    ActionDef("close_window", "Close window (Alt+F4)", "Window"),

    ActionDef("volume_up", "Volume up", "Media", repeatable=True),
    ActionDef("volume_down", "Volume down", "Media", repeatable=True),
    ActionDef("mute", "Mute", "Media"),
    ActionDef("media_play", "Play / pause", "Media"),
    ActionDef("media_next", "Next track", "Media", repeatable=True),
    ActionDef("media_prev", "Previous track", "Media", repeatable=True),

    ActionDef("copy", "Copy", "Editing"),
    ActionDef("paste", "Paste", "Editing"),
    ActionDef("cut", "Cut", "Editing"),
    ActionDef("undo", "Undo", "Editing", repeatable=True),
    ActionDef("redo", "Redo", "Editing", repeatable=True),

    ActionDef("middle_click", "Middle click", "Mouse"),
    ActionDef("scroll_up", "Scroll up", "Mouse", repeatable=True),
    ActionDef("scroll_down", "Scroll down", "Mouse", repeatable=True),

    ActionDef("screenshot", "Screen snip (Win+Shift+S)", "Other"),
    ActionDef("hotkey", "Custom hotkey…", "Other", param="keys", repeatable=True),
    ActionDef("launch", "Launch program / file…", "Other", param="path"),
]

ACTIONS_BY_ID: dict[str, ActionDef] = {a.id: a for a in ACTIONS}


def action_def(action_id: str) -> ActionDef:
    return ACTIONS_BY_ID.get(action_id, ACTIONS_BY_ID["none"])


# ---------------------------------------------------------------------------
# Hotkey text -> virtual key codes
# ---------------------------------------------------------------------------

_NAMED_KEYS = {
    "ctrl": w.VK_CONTROL, "control": w.VK_CONTROL,
    "shift": w.VK_SHIFT,
    "alt": w.VK_MENU, "menu": w.VK_MENU,
    "win": w.VK_LWIN, "super": w.VK_LWIN, "cmd": w.VK_LWIN,
    "tab": w.VK_TAB, "enter": w.VK_RETURN, "return": w.VK_RETURN,
    "esc": w.VK_ESCAPE, "escape": w.VK_ESCAPE,
    "space": w.VK_SPACE, "backspace": w.VK_BACK,
    "del": w.VK_DELETE, "delete": w.VK_DELETE, "insert": w.VK_INSERT,
    "home": w.VK_HOME, "end": w.VK_END,
    "pgup": w.VK_PRIOR, "pageup": w.VK_PRIOR,
    "pgdn": w.VK_NEXT, "pagedown": w.VK_NEXT,
    "left": w.VK_LEFT, "right": w.VK_RIGHT, "up": w.VK_UP, "down": w.VK_DOWN,
}
for _i in range(1, 25):
    _NAMED_KEYS[f"f{_i}"] = 0x6F + _i


def parse_hotkey(text: str) -> list[int]:
    """'ctrl+shift+t' -> [VK_CONTROL, VK_SHIFT, ord('T')]. Unknown parts dropped."""
    vks: list[int] = []
    for raw in (text or "").replace(" ", "").split("+"):
        if not raw:
            continue
        part = raw.lower()
        if part in _NAMED_KEYS:
            vks.append(_NAMED_KEYS[part])
        elif len(part) == 1:
            vks.append(ord(part.upper()))
    return vks


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


@dataclass
class _Holds:
    """Modifier keys we are holding down on behalf of a chord."""
    alt: bool = False


class ActionExecutor:
    """Runs actions. Called from a single worker thread, so ordering is stable."""

    def __init__(self) -> None:
        self._holds = _Holds()
        self._lock = threading.RLock()

    # -- held-modifier actions ------------------------------------------------

    def _hold_alt(self) -> None:
        if not self._holds.alt:
            w.key_down(w.VK_MENU)
            self._holds.alt = True
            time.sleep(0.03)  # let the shell open the switcher

    def release_holds(self) -> None:
        """Let go of anything we are holding on behalf of a finished chord."""
        with self._lock:
            if self._holds.alt:
                w.key_up(w.VK_MENU)
                self._holds.alt = False

    @property
    def holding(self) -> bool:
        return self._holds.alt

    # -- dispatch -------------------------------------------------------------

    def run(self, action_id: str, param: str = "") -> None:
        with self._lock:
            try:
                self._run(action_id, param)
            except Exception:
                pass  # a bad binding must never take the hook down

    def _run(self, action_id: str, param: str) -> None:
        K = w.key_combo
        if action_id in ("none", ""):
            return

        if action_id == "app_switch_next":
            self._hold_alt()
            w.key_tap(w.VK_TAB)
        elif action_id == "app_switch_prev":
            self._hold_alt()
            w.key_down(w.VK_SHIFT)
            w.key_tap(w.VK_TAB)
            w.key_up(w.VK_SHIFT)
        elif action_id == "task_view":
            K([w.VK_LWIN, w.VK_TAB])
        elif action_id == "show_desktop":
            K([w.VK_LWIN, ord("D")])
        elif action_id == "next_desktop":
            K([w.VK_LWIN, w.VK_CONTROL, w.VK_RIGHT])
        elif action_id == "prev_desktop":
            K([w.VK_LWIN, w.VK_CONTROL, w.VK_LEFT])

        elif action_id == "next_tab":
            K([w.VK_CONTROL, w.VK_TAB])
        elif action_id == "prev_tab":
            K([w.VK_CONTROL, w.VK_SHIFT, w.VK_TAB])
        elif action_id == "new_tab":
            K([w.VK_CONTROL, ord("T")])
        elif action_id == "close_tab":
            K([w.VK_CONTROL, ord("W")])
        elif action_id == "reopen_tab":
            K([w.VK_CONTROL, w.VK_SHIFT, ord("T")])

        elif action_id == "nav_back":
            K([w.VK_MENU, w.VK_LEFT])
        elif action_id == "nav_forward":
            K([w.VK_MENU, w.VK_RIGHT])
        elif action_id == "refresh":
            K([w.VK_CONTROL, ord("R")])

        elif action_id == "minimize_window":
            K([w.VK_LWIN, w.VK_DOWN])
        elif action_id == "maximize_window":
            K([w.VK_LWIN, w.VK_UP])
        elif action_id == "snap_left":
            K([w.VK_LWIN, w.VK_LEFT])
        elif action_id == "snap_right":
            K([w.VK_LWIN, w.VK_RIGHT])
        elif action_id == "close_window":
            K([w.VK_MENU, w.VK_F4])

        elif action_id == "volume_up":
            w.key_tap(w.VK_VOLUME_UP)
        elif action_id == "volume_down":
            w.key_tap(w.VK_VOLUME_DOWN)
        elif action_id == "mute":
            w.key_tap(w.VK_VOLUME_MUTE)
        elif action_id == "media_play":
            w.key_tap(w.VK_MEDIA_PLAY_PAUSE)
        elif action_id == "media_next":
            w.key_tap(w.VK_MEDIA_NEXT_TRACK)
        elif action_id == "media_prev":
            w.key_tap(w.VK_MEDIA_PREV_TRACK)

        elif action_id == "copy":
            K([w.VK_CONTROL, ord("C")])
        elif action_id == "paste":
            K([w.VK_CONTROL, ord("V")])
        elif action_id == "cut":
            K([w.VK_CONTROL, ord("X")])
        elif action_id == "undo":
            K([w.VK_CONTROL, ord("Z")])
        elif action_id == "redo":
            K([w.VK_CONTROL, ord("Y")])

        elif action_id == "middle_click":
            w.mouse_click("middle")
        elif action_id == "scroll_up":
            w.mouse_wheel(120)
        elif action_id == "scroll_down":
            w.mouse_wheel(-120)

        elif action_id == "screenshot":
            K([w.VK_LWIN, w.VK_SHIFT, ord("S")])

        elif action_id == "hotkey":
            vks = parse_hotkey(param)
            if vks:
                K(vks)

        elif action_id == "launch":
            target = (param or "").strip().strip('"')
            if target:
                self._launch(target)

    @staticmethod
    def _launch(target: str) -> None:
        try:
            os.startfile(target)  # noqa: S606 - user-configured target, by design
        except Exception:
            subprocess.Popen(target, shell=True)
