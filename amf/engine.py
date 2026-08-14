"""The gesture engine: a low-level mouse hook plus chord + swipe detection.

How the click-swallowing works
------------------------------
A chord always starts with an *anchor* button (a side button, or the middle
button). When an anchor goes down we suppress the event and hold onto it:

* if another button joins and the combination is a configured chord, we enter
  gesture mode and swallow everything until the chord is released;
* if the anchor is released on its own, we replay the click we swallowed, so a
  plain side-button press still means Back/Forward like it always did;
* if another button joins but the combination is *not* configured, we replay
  the anchor press immediately and get out of the way.

Left and right clicks are never suppressed unless a chord is already engaged,
so ordinary mousing is untouched.

The hook callback itself only does bookkeeping - every action is handed to a
worker thread, because Windows silently drops hooks whose callback is slow.
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import wintypes

from . import config as cfgmod
from . import winapi as w
from .actions import ActionExecutor, action_def

_DOWN_MESSAGES = {
    w.WM_LBUTTONDOWN: "left",
    w.WM_RBUTTONDOWN: "right",
    w.WM_MBUTTONDOWN: "middle",
}
_UP_MESSAGES = {
    w.WM_LBUTTONUP: "left",
    w.WM_RBUTTONUP: "right",
    w.WM_MBUTTONUP: "middle",
}


class GestureEngine:
    """Owns the hook thread, the chord state machine and the action worker."""

    def __init__(self, cfg: dict, on_state=None) -> None:
        self._cfg = cfg
        self._cfg_lock = threading.Lock()
        self._on_state = on_state          # called with a dict, from any thread

        self._exec = ActionExecutor()
        self._jobs: queue.Queue = queue.Queue()

        self._hook_thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._hook_tid = 0
        self._hook_handle = None
        self._proc = w.HOOKPROC(self._hook_proc)  # keep a reference alive
        self._running = False

        # Indirected so tests can drive the state machine without a real mouse.
        self._physically_held = w.physically_held
        self._cursor_pos = w.cursor_pos

        self._active_anchors: set[str] = set()
        self._refresh_anchors()

        # --- chord state (touched only by the hook thread) ---
        self._held: set[str] = set()
        self._deferred: list[str] = []     # anchors whose click we are holding
        self._flushed = False              # deferred anchors already replayed
        self._swallow_up: set[str] = set()
        self._engaged = False
        self._chord: str | None = None
        self._engaged_at = 0.0
        self._acc_x = 0.0
        self._acc_y = 0.0
        self._axis: str | None = None
        self._fires = 0
        self._dir_fires: dict[str, int] = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, name="amf-actions",
                                        daemon=True)
        self._worker.start()
        self._hook_thread = threading.Thread(target=self._hook_loop, name="amf-hook",
                                             daemon=True)
        self._hook_thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._hook_tid:
            w.user32.PostThreadMessageW(self._hook_tid, w.WM_QUIT, 0, 0)
        self._jobs.put(("quit", None, None))
        for t in (self._hook_thread, self._worker):
            if t is not None:
                t.join(timeout=2.0)
        self._exec.release_holds()
        self._hook_thread = self._worker = None
        self._hook_tid = 0

    # -- configuration -------------------------------------------------------

    def update_config(self, cfg: dict) -> None:
        with self._cfg_lock:
            self._cfg = cfg
        self._refresh_anchors()
        if not cfg.get("enabled", True):
            self._jobs.put(("release", None, None))

    def _refresh_anchors(self) -> None:
        with self._cfg_lock:
            bindings = self._cfg.get("bindings", {})
            enabled = self._cfg.get("enabled", True)
        anchors: set[str] = set()
        if enabled:
            for key, binding in bindings.items():
                if not binding.get("enabled"):
                    continue
                if all(_spec(binding, d)[0] == "none" for d in cfgmod.DIRECTIONS):
                    continue
                anchors.update(b for b in key.split("+") if b in cfgmod.ANCHOR_BUTTONS)
        self._active_anchors = anchors

    def _snapshot(self) -> dict:
        with self._cfg_lock:
            return self._cfg

    # -- hook thread ---------------------------------------------------------

    def _hook_loop(self) -> None:
        self._hook_tid = w.kernel32.GetCurrentThreadId()
        self._hook_handle = w.user32.SetWindowsHookExW(w.WH_MOUSE_LL, self._proc, None, 0)
        if not self._hook_handle:
            self._notify(error="Could not install the mouse hook "
                               f"(error {ctypes.get_last_error()}).")
            return
        self._notify()
        msg = wintypes.MSG()
        while self._running and w.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            w.user32.TranslateMessage(ctypes.byref(msg))
            w.user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook_handle:
            w.user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = None

    def _hook_proc(self, ncode, wparam, lparam):
        if ncode == w.HC_ACTION:
            try:
                if self._handle(wparam, lparam):
                    return 1
            except Exception:
                self._reset()  # never let a bug wedge the user's mouse
        return w.user32.CallNextHookEx(None, ncode, wparam, lparam)

    def _handle(self, msg: int, lparam: int) -> bool:
        info = ctypes.cast(lparam, w.LPMSLLHOOKSTRUCT).contents
        if info.dwExtraInfo == w.INJECT_TAG:
            return False

        cfg = self._snapshot()
        if not cfg.get("enabled", True):
            if self._engaged or self._deferred:
                self._abort()
            return False
        if (info.flags & w.LLMHF_INJECTED) and cfg.get("ignore_injected", True):
            return False

        if msg == w.WM_MOUSEMOVE:
            return self._on_move(info, cfg)

        button, down = _decode_button(msg, info.mouseData)
        if button is None:
            return False
        return self._on_down(button, cfg) if down else self._on_up(button, cfg)

    # -- button handling -----------------------------------------------------

    def _on_down(self, button: str, cfg: dict) -> bool:
        self._resync(button)
        self._held.add(button)

        if self._engaged:
            self._swallow_up.add(button)
            self._rematch(cfg)
            return True

        if button in self._active_anchors and not self._flushed:
            self._deferred.append(button)
            self._swallow_up.add(button)
            if self._match(cfg):
                self._engage(cfg)
            return True

        if self._deferred:
            if self._match(cfg):
                self._swallow_up.add(button)
                self._engage(cfg)
                return True
            self._flush_deferred()
            return False

        return False

    def _on_up(self, button: str, cfg: dict) -> bool:
        self._held.discard(button)
        was_deferred = button in self._deferred
        if was_deferred:
            self._deferred.remove(button)

        if self._engaged:
            self._swallow_up.discard(button)
            if not self._deferred:
                self._end_chord(cfg)
            else:
                self._rematch(cfg)
            return True

        if was_deferred and not self._flushed:
            self._swallow_up.discard(button)
            self._jobs.put(("replay", button, None))
            if not self._deferred:
                self._flushed = False
            return True

        if button in self._swallow_up:
            self._swallow_up.discard(button)
            return True

        if not self._deferred:
            self._flushed = False
        return False

    def _resync(self, ignore: str) -> None:
        """Drop buttons we think are held but that the OS says are not.

        Guards against a missed up event (focus loss, a suppressed event that
        another hook ate) leaving us permanently 'holding' a button.
        """
        if not self._held or self._engaged:
            return
        for button in list(self._held):
            if button != ignore and not self._physically_held(button):
                self._held.discard(button)
                self._swallow_up.discard(button)
                if button in self._deferred:
                    self._deferred.remove(button)

    # -- chord matching ------------------------------------------------------

    def _match(self, cfg: dict) -> str | None:
        key = cfgmod.chord_key(self._held)
        if "+" not in key:
            return None
        binding = cfg.get("bindings", {}).get(key)
        if binding and binding.get("enabled"):
            return key
        return None

    def _engage(self, cfg: dict) -> None:
        self._engaged = True
        self._chord = self._match(cfg)
        self._engaged_at = time.monotonic()
        self._acc_x = self._acc_y = 0.0
        self._axis = None
        self._fires = 0
        self._dir_fires = {}
        self._notify()

    def _rematch(self, cfg: dict) -> None:
        """A button joined or left mid-chord - switch bindings if one matches."""
        key = self._match(cfg)
        if key and key != self._chord:
            self._jobs.put(("release", None, None))
            self._chord = key
            self._acc_x = self._acc_y = 0.0
            self._axis = None
            self._fires = 0
            self._dir_fires = {}
            self._notify()

    def _end_chord(self, cfg: dict) -> None:
        chord, fires = self._chord, self._fires
        elapsed_ms = (time.monotonic() - self._engaged_at) * 1000.0
        self._engaged = False
        self._chord = None
        self._jobs.put(("release", None, None))

        if chord and fires == 0 and elapsed_ms <= cfg.get("tap_max_ms", 300):
            action, param = _spec(cfg["bindings"][chord], "tap")
            if action != "none":
                self._jobs.put(("action", (action, param), (chord, "tap")))
        self._notify()

    def _flush_deferred(self) -> None:
        """Let the swallowed anchor presses through - no chord is forming."""
        for button in self._deferred:
            self._swallow_up.discard(button)
            self._jobs.put(("press", button, None))
        self._deferred.clear()
        self._flushed = True

    def _abort(self) -> None:
        self._flush_deferred()
        self._reset()

    def _reset(self) -> None:
        self._held.clear()
        self._deferred.clear()
        self._swallow_up.clear()
        self._flushed = False
        self._engaged = False
        self._chord = None
        self._acc_x = self._acc_y = 0.0
        self._axis = None
        self._fires = 0
        self._dir_fires = {}
        self._jobs.put(("release", None, None))
        self._notify()

    # -- movement ------------------------------------------------------------

    def _on_move(self, info, cfg: dict) -> bool:
        if not self._engaged or not self._chord:
            return False

        # Measure against where the pointer actually is right now. While the
        # cursor is frozen that yields the raw physical delta for this event;
        # when it isn't frozen it yields the ordinary incremental delta. Either
        # way the sum is the real distance travelled.
        cx, cy = self._cursor_pos()
        self._acc_x += info.pt.x - cx
        self._acc_y += info.pt.y - cy

        first = self._fires == 0
        threshold = cfg.get("swipe_threshold", 45) if first else cfg.get("repeat_threshold", 65)
        threshold = max(8, threshold)

        if self._axis is None:
            if abs(self._acc_x) < threshold and abs(self._acc_y) < threshold:
                return cfg.get("freeze_cursor", True)
            self._axis = "x" if abs(self._acc_x) >= abs(self._acc_y) else "y"

        travel = self._acc_x if self._axis == "x" else self._acc_y
        if abs(travel) < threshold:
            return cfg.get("freeze_cursor", True)

        if self._axis == "x":
            direction = "swipe_right" if travel > 0 else "swipe_left"
        else:
            direction = "swipe_down" if travel > 0 else "swipe_up"

        self._acc_x = self._acc_y = 0.0
        self._fire(direction, cfg)
        return cfg.get("freeze_cursor", True)

    def _fire(self, direction: str, cfg: dict) -> None:
        binding = cfg.get("bindings", {}).get(self._chord or "")
        if not binding:
            return
        action, param = _spec(binding, direction)
        self._fires += 1
        if action == "none":
            return
        count = self._dir_fires.get(direction, 0)
        if count and not action_def(action).repeatable:
            return
        self._dir_fires[direction] = count + 1
        self._jobs.put(("action", (action, param), (self._chord, direction)))
        self._notify(fired=(self._chord, direction, action))

    # -- worker thread -------------------------------------------------------

    def _worker_loop(self) -> None:
        while True:
            kind, payload, meta = self._jobs.get()
            try:
                if kind == "quit":
                    return
                if kind == "action":
                    self._exec.run(payload[0], payload[1])
                elif kind == "release":
                    self._exec.release_holds()
                elif kind == "replay":
                    w.mouse_click(payload)
                elif kind == "press":
                    w.mouse_button(payload, True)
            except Exception:
                pass

    # -- reporting -----------------------------------------------------------

    def _notify(self, fired=None, error: str | None = None) -> None:
        if self._on_state is None:
            return
        try:
            self._on_state({
                "held": sorted(self._held, key=cfgmod.BUTTON_ORDER.index),
                "chord": self._chord,
                "engaged": self._engaged,
                "fired": fired,
                "error": error,
                "hooked": bool(self._hook_handle),
            })
        except Exception:
            pass


def _decode_button(msg: int, mouse_data: int):
    if msg in _DOWN_MESSAGES:
        return _DOWN_MESSAGES[msg], True
    if msg in _UP_MESSAGES:
        return _UP_MESSAGES[msg], False
    if msg in (w.WM_XBUTTONDOWN, w.WM_XBUTTONUP):
        which = (mouse_data >> 16) & 0xFFFF
        button = "x1" if which == w.XBUTTON1 else "x2" if which == w.XBUTTON2 else None
        return button, msg == w.WM_XBUTTONDOWN
    return None, False


def _spec(binding: dict, direction: str) -> tuple[str, str]:
    spec = binding.get(direction) or {}
    if isinstance(spec, str):
        return spec, ""
    return spec.get("action", "none") or "none", spec.get("param", "") or ""
