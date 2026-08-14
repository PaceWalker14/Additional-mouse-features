"""Drives the chord state machine with synthetic hook events.

Run with:  python -m unittest discover -s tests
"""

from __future__ import annotations

import ctypes
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amf import config as cfgmod          # noqa: E402
from amf import winapi as w               # noqa: E402
from amf.engine import GestureEngine      # noqa: E402

DOWN = {
    "left": (w.WM_LBUTTONDOWN, 0),
    "right": (w.WM_RBUTTONDOWN, 0),
    "middle": (w.WM_MBUTTONDOWN, 0),
    "x1": (w.WM_XBUTTONDOWN, w.XBUTTON1 << 16),
    "x2": (w.WM_XBUTTONDOWN, w.XBUTTON2 << 16),
}
UP = {
    "left": (w.WM_LBUTTONUP, 0),
    "right": (w.WM_RBUTTONUP, 0),
    "middle": (w.WM_MBUTTONUP, 0),
    "x1": (w.WM_XBUTTONUP, w.XBUTTON1 << 16),
    "x2": (w.WM_XBUTTONUP, w.XBUTTON2 << 16),
}


class Harness:
    """A GestureEngine with the hook, worker and OS state stubbed out."""

    def __init__(self, cfg: dict) -> None:
        self.engine = GestureEngine(cfg)
        self.engine._physically_held = lambda b: b in self.physical
        self.engine._cursor_pos = lambda: self.cursor
        self.physical: set[str] = set()
        self.cursor = (500, 500)

    # -- input ---------------------------------------------------------------

    def _post(self, msg: int, x: int = 500, y: int = 500, data: int = 0) -> bool:
        info = w.MSLLHOOKSTRUCT()
        info.pt = w.POINT(x, y)
        info.mouseData = data
        info.flags = 0
        info.time = 0
        info.dwExtraInfo = 0
        return self.engine._handle(msg, ctypes.addressof(info))

    def down(self, button: str) -> bool:
        self.physical.add(button)
        msg, data = DOWN[button]
        return self._post(msg, data=data)

    def up(self, button: str) -> bool:
        self.physical.discard(button)
        msg, data = UP[button]
        return self._post(msg, data=data)

    def move(self, dx: int, dy: int = 0, steps: int = 1) -> bool:
        """Move by (dx, dy) split over `steps` events, as a real mouse would."""
        cx, cy = self.cursor
        swallowed = False
        for _ in range(steps):
            swallowed = self._post(w.WM_MOUSEMOVE, cx + dx // steps, cy + dy // steps)
            if not swallowed:                 # cursor really moved
                self.cursor = (cx + dx // steps, cy + dy // steps)
                cx, cy = self.cursor
        return swallowed

    # -- output --------------------------------------------------------------

    def jobs(self) -> list[tuple]:
        out = []
        while not self.engine._jobs.empty():
            out.append(self.engine._jobs.get_nowait())
        return out

    def actions(self) -> list[str]:
        return [j[1][0] for j in self.jobs() if j[0] == "action"]

    def drain(self) -> None:
        self.jobs()


def make_config(**overrides) -> dict:
    cfg = cfgmod.default_config()
    cfg.update(overrides)
    return cfg


class ChordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(make_config())

    # -- ordinary mousing must be untouched ---------------------------------

    def test_plain_left_click_passes_through(self):
        self.assertFalse(self.h.down("left"))
        self.assertFalse(self.h.up("left"))

    def test_plain_right_click_passes_through(self):
        self.assertFalse(self.h.down("right"))
        self.assertFalse(self.h.up("right"))

    def test_movement_passes_through_when_idle(self):
        self.assertFalse(self.h.move(200))

    def test_lone_side_button_is_replayed_on_release(self):
        self.assertTrue(self.h.down("x1"), "the press is held back")
        self.assertTrue(self.h.up("x1"))
        self.assertIn(("replay", "x1", None), self.h.jobs())

    def test_unbound_combo_releases_the_side_button(self):
        cfg = make_config()
        cfg["bindings"]["x1+right"]["enabled"] = False
        h = Harness(cfg)
        self.assertTrue(h.down("x1"))
        self.assertFalse(h.down("right"), "the right click still gets through")
        self.assertIn(("press", "x1", None), h.jobs())
        self.assertFalse(h.up("x1"), "the real release matches the replayed press")

    def test_middle_button_untouched_when_no_combo_uses_it(self):
        self.assertFalse(self.h.down("middle"))
        self.assertFalse(self.h.up("middle"))

    def test_middle_button_becomes_an_anchor_once_bound(self):
        cfg = make_config()
        cfg["bindings"]["middle+left"]["enabled"] = True
        cfg["bindings"]["middle+left"]["swipe_right"] = {"action": "next_tab", "param": ""}
        h = Harness(cfg)
        self.assertTrue(h.down("middle"))

    # -- chords --------------------------------------------------------------

    def test_chord_engages_and_swallows_clicks(self):
        self.assertTrue(self.h.down("x2"))
        self.assertTrue(self.h.down("left"))
        self.assertTrue(self.h.engine._engaged)
        self.assertEqual(self.h.engine._chord, "x2+left")
        self.assertTrue(self.h.up("left"))
        self.assertTrue(self.h.up("x2"))
        self.assertNotIn("replay", [j[0] for j in self.h.jobs()])

    def test_swipe_right_fires_the_bound_action(self):
        self.h.down("x2")
        self.h.down("left")
        self.h.drain()
        self.h.move(80)
        self.assertEqual(self.h.actions(), ["app_switch_next"])

    def test_swipe_left_fires_the_other_way(self):
        self.h.down("x1")
        self.h.down("left")
        self.h.drain()
        self.h.move(-80)
        self.assertEqual(self.h.actions(), ["prev_tab"])

    def test_vertical_swipe_wins_when_it_dominates(self):
        self.h.down("x1")
        self.h.down("left")
        self.h.drain()
        self.h.move(10, -80)
        self.assertEqual(self.h.actions(), ["new_tab"])

    def test_small_movement_fires_nothing(self):
        self.h.down("x2")
        self.h.down("left")
        self.h.drain()
        self.h.move(20)
        self.assertEqual(self.h.actions(), [])

    def test_repeatable_action_repeats_while_held(self):
        self.h.down("x2")
        self.h.down("left")
        self.h.drain()
        self.h.move(80)
        self.h.move(80)
        self.h.move(80)
        self.assertEqual(self.h.actions(),
                         ["app_switch_next"] * 3)

    def test_non_repeatable_action_fires_once(self):
        self.h.down("x1")
        self.h.down("left")
        self.h.drain()
        self.h.move(0, -80)     # new_tab, not repeatable
        self.h.move(0, -80)
        self.assertEqual(self.h.actions(), ["new_tab"])

    def test_axis_locks_after_the_first_swipe(self):
        self.h.down("x2")
        self.h.down("left")
        self.h.drain()
        self.h.move(80)                     # locks to horizontal
        self.h.drain()
        self.h.move(0, 80)                  # vertical is now ignored
        self.assertEqual(self.h.actions(), [])

    def test_swipe_accumulates_over_several_small_moves(self):
        self.h.down("x2")
        self.h.down("left")
        self.h.drain()
        self.h.move(60, steps=6)            # 6 x 10px
        self.assertEqual(self.h.actions(), ["app_switch_next"])

    def test_alt_is_released_when_the_chord_ends(self):
        self.h.down("x2")
        self.h.down("left")
        self.h.move(80)
        self.h.drain()
        self.h.up("left")
        self.h.up("x2")
        self.assertIn(("release", None, None), self.h.jobs())

    def test_chord_survives_releasing_the_click_first(self):
        self.h.down("x2")
        self.h.down("left")
        self.h.up("left")                   # anchor still down
        self.assertTrue(self.h.engine._engaged)
        self.h.drain()
        self.h.move(80)
        self.assertEqual(self.h.actions(), ["app_switch_next"])

    def test_both_side_buttons_form_their_own_chord(self):
        self.h.down("x1")
        self.h.down("x2")
        self.assertEqual(self.h.engine._chord, "x1+x2")
        self.h.drain()
        self.h.move(80)
        self.assertEqual(self.h.actions(), ["next_desktop"])

    def test_adding_a_button_switches_binding(self):
        cfg = make_config()
        cfg["bindings"]["x1+left+right"]["enabled"] = True
        cfg["bindings"]["x1+left+right"]["swipe_right"] = {"action": "volume_up",
                                                           "param": ""}
        h = Harness(cfg)
        h.down("x1")
        h.down("left")
        self.assertEqual(h.engine._chord, "x1+left")
        h.down("right")
        self.assertEqual(h.engine._chord, "x1+left+right")
        h.drain()
        h.move(80)
        self.assertEqual(h.actions(), ["volume_up"])

    # -- taps ----------------------------------------------------------------

    def test_quick_release_fires_the_tap_action(self):
        self.h.down("x1")
        self.h.down("left")
        self.h.drain()
        self.h.up("left")
        self.h.up("x1")
        self.assertEqual(self.h.actions(), ["reopen_tab"])

    def test_no_tap_after_a_swipe(self):
        self.h.down("x1")
        self.h.down("left")
        self.h.move(80)
        self.h.drain()
        self.h.up("left")
        self.h.up("x1")
        self.assertEqual(self.h.actions(), [])

    def test_slow_release_is_not_a_tap(self):
        self.h.down("x1")
        self.h.down("left")
        self.h.engine._engaged_at -= 5.0     # pretend it was held for 5s
        self.h.drain()
        self.h.up("left")
        self.h.up("x1")
        self.assertEqual(self.h.actions(), [])

    # -- disabling -----------------------------------------------------------

    def test_master_switch_off_passes_everything_through(self):
        cfg = make_config(enabled=False)
        h = Harness(cfg)
        self.assertFalse(h.down("x1"))
        self.assertFalse(h.down("left"))
        self.assertFalse(h.move(200))

    def test_disabled_combo_is_not_an_anchor(self):
        cfg = make_config()
        for binding in cfg["bindings"].values():
            binding["enabled"] = False
        h = Harness(cfg)
        self.assertFalse(h.down("x1"))
        self.assertFalse(h.down("left"))

    def test_cursor_is_pinned_while_a_chord_is_held(self):
        self.h.down("x2")
        self.h.down("left")
        self.assertTrue(self.h.move(30), "movement is swallowed")

    def test_cursor_moves_when_pinning_is_off(self):
        h = Harness(make_config(freeze_cursor=False))
        h.down("x2")
        h.down("left")
        self.assertFalse(h.move(30))

    # -- recovery ------------------------------------------------------------

    def test_missed_release_is_resynced(self):
        self.h.down("x1")
        self.h.physical.discard("x1")        # the up event never arrived
        self.h.up("x1")
        self.h.drain()
        self.assertFalse(self.h.down("left"), "not stuck thinking x1 is held")


class HotkeyTests(unittest.TestCase):
    def test_parse(self):
        from amf.actions import parse_hotkey
        self.assertEqual(parse_hotkey("ctrl+shift+t"),
                         [w.VK_CONTROL, w.VK_SHIFT, ord("T")])
        self.assertEqual(parse_hotkey("win+d"), [w.VK_LWIN, ord("D")])
        self.assertEqual(parse_hotkey("f5"), [0x74])
        self.assertEqual(parse_hotkey(""), [])
        self.assertEqual(parse_hotkey("bogusname"), [])


class ConfigTests(unittest.TestCase):
    def test_every_combo_key_is_canonical(self):
        for key in cfgmod.COMBOS:
            self.assertEqual(key, cfgmod.chord_key(set(key.split("+"))))

    def test_every_combo_has_an_anchor(self):
        for key in cfgmod.COMBOS:
            self.assertTrue(any(b in cfgmod.ANCHOR_BUTTONS for b in key.split("+")), key)

    def test_defaults_survive_a_round_trip(self):
        from amf.config import _merge
        defaults = cfgmod.default_config()
        self.assertEqual(_merge(cfgmod.default_config(), defaults), defaults)

    def test_junk_in_the_file_is_ignored(self):
        from amf.config import _merge
        merged = _merge(cfgmod.default_config(),
                        {"enabled": "yes please", "swipe_threshold": 99, "nope": 1})
        self.assertIs(merged["enabled"], True)
        self.assertEqual(merged["swipe_threshold"], 99)
        self.assertNotIn("nope", merged)


if __name__ == "__main__":
    unittest.main(verbosity=2)
