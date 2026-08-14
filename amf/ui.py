"""The settings window."""

from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import config as cfgmod
from . import startup
from . import winapi as w
from .actions import ACTIONS, action_def
from .engine import GestureEngine
from .icon import icon_path
from .tray import TrayIcon

# --- palette ---------------------------------------------------------------
BG = "#14161c"
PANEL = "#1c1f27"
PANEL_HI = "#242833"
LINE = "#2e3340"
TEXT = "#e7e9f0"
MUTED = "#8b91a4"
ACCENT = "#7c8aff"
GOOD = "#57d38c"
WARN = "#e8b04b"

class Toggle(ttk.Frame):
    """A pill switch - ttk's own check indicator looks crude on a dark theme."""

    W, H = 40, 22

    def __init__(self, master, text: str, variable: tk.BooleanVar, command=None,
                 bg: str = BG, style: str = "TFrame", label_style: str = "TLabel"):
        super().__init__(master, style=style)
        self.var = variable
        self.command = command
        self._bg = bg

        self.canvas = tk.Canvas(self, width=self.W, height=self.H, bg=bg,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(side="left")
        self.label = ttk.Label(self, text=text, style=label_style, cursor="hand2")
        self.label.pack(side="left", padx=(9, 0))

        for widget in (self.canvas, self.label):
            widget.bind("<Button-1>", self._clicked)
        self.var.trace_add("write", lambda *_: self.redraw())
        self.redraw()

    def _clicked(self, _event=None) -> None:
        if str(self.label.cget("state")) == "disabled":
            return
        self.var.set(not self.var.get())
        if self.command is not None:
            self.command()

    def redraw(self) -> None:
        on = bool(self.var.get())
        c = self.canvas
        c.delete("all")
        r = self.H / 2
        track = ACCENT if on else LINE
        c.create_oval(0, 0, self.H, self.H, fill=track, outline=track)
        c.create_oval(self.W - self.H, 0, self.W, self.H, fill=track, outline=track)
        c.create_rectangle(r, 0, self.W - r, self.H, fill=track, outline=track)
        knob = "#12131a" if on else MUTED
        kx = self.W - r if on else r
        c.create_oval(kx - r + 4, 4, kx + r - 4, self.H - 4, fill=knob, outline=knob)


ACTION_LABELS = {a.id: a.menu_label for a in ACTIONS}
LABEL_TO_ACTION = {a.menu_label: a.id for a in ACTIONS}
ACTION_CHOICES = [a.menu_label for a in ACTIONS]


class App:
    def __init__(self, start_hidden: bool = False) -> None:
        self.cfg = cfgmod.load()
        self.events: queue.Queue = queue.Queue()
        self._suspend_writes = False
        self._save_job = None
        self.sliders: dict[str, tuple] = {}

        self.root = tk.Tk()
        self.root.title(cfgmod.APP_TITLE)
        self.root.configure(bg=BG)
        self.root.geometry("1060x700")
        self.root.minsize(940, 620)
        try:
            self.root.tk.call("tk", "scaling", w.scaling_factor() * 1.0)
        except tk.TclError:
            pass
        ico = icon_path()
        if ico:
            try:
                self.root.iconbitmap(ico)
            except tk.TclError:
                pass

        self._build_styles()
        self._build_ui()

        self.engine = GestureEngine(self.cfg, on_state=self._engine_state)
        self.engine.start()

        self.tray = TrayIcon(on_open=lambda: self.events.put(("show", None)),
                             on_toggle=lambda: self.events.put(("toggle", None)),
                             on_exit=lambda: self.events.put(("exit", None)),
                             enabled=self.cfg["enabled"])
        self.tray.start()

        startup.refresh_if_enabled()
        self._select_combo(self._first_enabled_combo())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(60, self._pump)

        if start_hidden:
            self.root.withdraw()

    # -- theming -------------------------------------------------------------

    def _build_styles(self) -> None:
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL,
                     bordercolor=LINE, lightcolor=PANEL, darkcolor=PANEL,
                     focuscolor=ACCENT)
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("Card.TFrame", background=PANEL_HI)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        st.configure("Muted.TLabel", background=BG, foreground=MUTED)
        st.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        st.configure("Title.TLabel", background=BG, foreground=TEXT,
                     font=("Segoe UI Semibold", 15))
        st.configure("Sub.TLabel", background=BG, foreground=MUTED,
                     font=("Segoe UI", 9))
        st.configure("Chord.TLabel", background=BG, foreground=TEXT,
                     font=("Segoe UI Semibold", 13))
        st.configure("Glyph.TLabel", background=BG, foreground=ACCENT,
                     font=("Segoe UI", 16))

        st.configure("TCheckbutton", background=BG, foreground=TEXT,
                     indicatorcolor=PANEL_HI, focuscolor=BG)
        st.map("TCheckbutton",
               background=[("active", BG)],
               indicatorcolor=[("selected", ACCENT), ("!selected", PANEL_HI)],
               foreground=[("disabled", MUTED)])

        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 6, 0, 0))
        st.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                     padding=(16, 8), borderwidth=0, font=("Segoe UI", 10))
        st.map("TNotebook.Tab",
               background=[("selected", BG)],
               foreground=[("selected", TEXT), ("active", TEXT)])

        st.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                     foreground=TEXT, borderwidth=0, rowheight=30)
        st.map("Treeview", background=[("selected", ACCENT)],
               foreground=[("selected", "#12131a")])
        st.configure("Treeview.Heading", background=BG, foreground=MUTED,
                     borderwidth=0, font=("Segoe UI", 9))
        st.map("Treeview.Heading", background=[("active", BG)])

        st.configure("TCombobox", arrowcolor=TEXT, selectbackground=PANEL_HI,
                     selectforeground=TEXT, padding=4)
        st.map("TCombobox",
               fieldbackground=[("readonly", PANEL_HI), ("!readonly", PANEL_HI)],
               background=[("readonly", PANEL_HI)],
               foreground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL_HI)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#12131a")

        st.configure("TEntry", fieldbackground=PANEL_HI, foreground=TEXT,
                     insertcolor=TEXT, padding=4)
        st.configure("TButton", background=PANEL_HI, foreground=TEXT,
                     borderwidth=0, padding=(12, 6))
        st.map("TButton", background=[("active", LINE)])
        st.configure("Accent.TButton", background=ACCENT, foreground="#12131a")
        st.map("Accent.TButton", background=[("active", "#94a0ff")])
        st.configure("Horizontal.TScale", background=BG, troughcolor=PANEL_HI)

    # -- layout --------------------------------------------------------------

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(18, 14, 18, 6))
        header.pack(fill="x")
        ttk.Label(header, text="🖱", style="Glyph.TLabel").pack(side="left", padx=(0, 10))
        titles = ttk.Frame(header)
        titles.pack(side="left")
        ttk.Label(titles, text=cfgmod.APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Trackpad-style gestures from your mouse's side buttons",
                  style="Sub.TLabel").pack(anchor="w")

        self.var_enabled = tk.BooleanVar(value=self.cfg["enabled"])
        Toggle(header, "Gestures enabled", self.var_enabled,
               self._on_master_toggle).pack(side="right")

        self._build_statusbar()   # packed first so it keeps its strip at the bottom

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        self.tab_gestures = ttk.Frame(nb, padding=10)
        self.tab_settings = ttk.Frame(nb, padding=(24, 18))
        self.tab_help = ttk.Frame(nb, padding=(24, 18))
        nb.add(self.tab_gestures, text="Gestures")
        nb.add(self.tab_settings, text="Settings")
        nb.add(self.tab_help, text="How it works")
        nb.enable_traversal()

        self._build_gestures(self.tab_gestures)
        self._build_settings(self.tab_settings)
        self._build_help(self.tab_help)
        self._dark_titlebar()

    def _build_gestures(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        left = ttk.Frame(parent, style="Panel.TFrame", padding=(0, 6))
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        left.rowconfigure(1, weight=1)
        ttk.Label(left, text="  BUTTON COMBOS", style="PanelMuted.TLabel",
                  font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w",
                                                     padx=10, pady=(4, 6))

        self.tree = ttk.Treeview(left, columns=("state",), show="tree",
                                 selectmode="browse", height=14)
        self.tree.column("#0", width=250, stretch=False, anchor="w")
        self.tree.column("state", width=52, stretch=False, anchor="e")
        self.tree.grid(row=1, column=0, sticky="nsew", padx=6)
        self.tree.tag_configure("off", foreground=MUTED)
        self.tree.tag_configure("on", foreground=TEXT)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_tree_select())

        for key in cfgmod.COMBOS:
            self.tree.insert("", "end", iid=key, text="", values=("",))
        self._refresh_tree()

        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        head = ttk.Frame(right)
        head.grid(row=0, column=0, sticky="ew", pady=(2, 2))
        head.columnconfigure(1, weight=1)
        self.lbl_glyph = ttk.Label(head, text="", style="Glyph.TLabel")
        self.lbl_glyph.grid(row=0, column=0, rowspan=2, sticky="w", padx=(2, 12))
        self.lbl_chord = ttk.Label(head, text="", style="Chord.TLabel")
        self.lbl_chord.grid(row=0, column=1, sticky="w")
        ttk.Label(head, text="Hold the combo, then flick the mouse in a direction.",
                  style="Sub.TLabel").grid(row=1, column=1, sticky="w")

        self.var_combo_enabled = tk.BooleanVar(value=False)
        Toggle(head, "Enable this combo", self.var_combo_enabled,
               self._on_combo_toggle).grid(row=0, column=2, rowspan=2, sticky="e")

        rows = ttk.Frame(right, style="Panel.TFrame", padding=(16, 14))
        rows.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        rows.columnconfigure(2, weight=1)
        right.rowconfigure(1, weight=1)

        self.dir_widgets: dict[str, dict] = {}
        for i, direction in enumerate(cfgmod.DIRECTIONS):
            ttk.Label(rows, text=cfgmod.DIRECTION_GLYPHS[direction], style="Panel.TLabel",
                      font=("Segoe UI", 14), foreground=ACCENT
                      ).grid(row=i * 2, column=0, sticky="w", pady=(0, 2))
            ttk.Label(rows, text=cfgmod.DIRECTION_LABELS[direction], style="Panel.TLabel",
                      width=17).grid(row=i * 2, column=1, sticky="w", padx=(8, 12))

            box = ttk.Combobox(rows, values=ACTION_CHOICES, state="readonly", width=38)
            box.grid(row=i * 2, column=2, sticky="ew")
            box.bind("<<ComboboxSelected>>",
                     lambda _e, d=direction: self._on_action_change(d))

            param_row = ttk.Frame(rows, style="Panel.TFrame")
            param_row.grid(row=i * 2 + 1, column=2, sticky="ew", pady=(4, 12))
            param_row.columnconfigure(0, weight=1)
            entry = ttk.Entry(param_row)
            entry.grid(row=0, column=0, sticky="ew")
            entry.bind("<KeyRelease>", lambda _e, d=direction: self._on_param_change(d))
            browse = ttk.Button(param_row, text="Browse…", width=10,
                                command=lambda d=direction: self._browse(d))
            browse.grid(row=0, column=1, padx=(8, 0))
            param_row.grid_remove()

            hint = ttk.Label(rows, text="", style="PanelMuted.TLabel",
                             font=("Segoe UI", 8), wraplength=430, justify="left")
            hint.grid(row=i * 2 + 1, column=2, sticky="w", pady=(2, 10))

            self.dir_widgets[direction] = {"box": box, "param_row": param_row,
                                           "entry": entry, "browse": browse,
                                           "hint": hint}

    def _build_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        row = 0

        def section(title: str) -> None:
            nonlocal row
            ttk.Label(parent, text=title.upper(), style="Muted.TLabel",
                      font=("Segoe UI", 8, "bold")).grid(row=row, column=0, sticky="w",
                                                         pady=(16 if row else 0, 8))
            row += 1

        def check(text: str, var: tk.BooleanVar, cmd, note: str = "") -> None:
            nonlocal row
            Toggle(parent, text, var, cmd).grid(row=row, column=0, sticky="w",
                                                pady=(0, 2))
            row += 1
            if note:
                ttk.Label(parent, text=note, style="Muted.TLabel", font=("Segoe UI", 8)
                          ).grid(row=row, column=0, sticky="w", padx=(49, 0), pady=(0, 6))
                row += 1

        def slider(text: str, key: str, lo: int, hi: int, suffix: str, note: str) -> None:
            nonlocal row
            holder = ttk.Frame(parent)
            holder.grid(row=row, column=0, sticky="ew", pady=(2, 0))
            holder.columnconfigure(1, weight=1)
            ttk.Label(holder, text=text, width=22).grid(row=0, column=0, sticky="w")
            value = tk.IntVar(value=self.cfg[key])
            readout = ttk.Label(holder, text=f"{value.get()}{suffix}", width=8,
                                style="Muted.TLabel")

            def changed(raw):
                value.set(int(float(raw)))
                readout.configure(text=f"{value.get()}{suffix}")
                self.cfg[key] = value.get()
                self._apply()

            scale = ttk.Scale(holder, from_=lo, to=hi, orient="horizontal")
            scale.set(self.cfg[key])
            scale.configure(command=changed)   # only after the initial set
            scale.grid(row=0, column=1, sticky="ew", padx=12)
            self.sliders[key] = (scale, readout, suffix)
            readout.grid(row=0, column=2, sticky="e")
            row += 1
            ttk.Label(parent, text=note, style="Muted.TLabel", font=("Segoe UI", 8)
                      ).grid(row=row, column=0, sticky="w", pady=(0, 6))
            row += 1

        section("Startup")
        self.var_startup = tk.BooleanVar(value=startup.is_enabled())
        check("Run when I sign in to Windows", self.var_startup, self._on_startup_toggle,
              "Adds a per-user entry to the Windows Run key. No admin rights needed.")
        self.var_minimised = tk.BooleanVar(value=self.cfg["start_minimised"])
        check("Start minimised to the tray", self.var_minimised, self._on_flag_toggle_factory("start_minimised"))

        section("Feel")
        slider("Swipe distance", "swipe_threshold", 15, 150, " px",
               "How far to move before the first action fires. Lower = twitchier.")
        slider("Repeat distance", "repeat_threshold", 15, 200, " px",
               "Extra distance for each repeat while you keep holding the combo.")
        slider("Tap window", "tap_max_ms", 100, 800, " ms",
               "Release the combo faster than this without moving to fire the tap action.")

        section("Behaviour")
        self.var_freeze = tk.BooleanVar(value=self.cfg["freeze_cursor"])
        check("Pin the pointer while a combo is held", self.var_freeze,
              self._on_flag_toggle_factory("freeze_cursor"),
              "Keeps the cursor still so a swipe doesn't drag it across the screen.")
        self.var_injected = tk.BooleanVar(value=self.cfg["ignore_injected"])
        check("Ignore software-generated mouse input", self.var_injected,
              self._on_flag_toggle_factory("ignore_injected"),
              "Turn this off only if your mouse's own driver software injects its clicks.")

        section("Config")
        buttons = ttk.Frame(parent)
        buttons.grid(row=row, column=0, sticky="w", pady=(2, 0))
        ttk.Button(buttons, text="Open config folder",
                   command=self._open_config_dir).pack(side="left")
        ttk.Button(buttons, text="Reset everything to defaults",
                   command=self._reset_defaults).pack(side="left", padx=8)
        row += 1
        ttk.Label(parent, text=cfgmod.CONFIG_PATH, style="Muted.TLabel",
                  font=("Consolas", 8)).grid(row=row, column=0, sticky="w", pady=(8, 0))

    def _build_help(self, parent: ttk.Frame) -> None:
        text = tk.Text(parent, wrap="word", bg=BG, fg=TEXT, bd=0,
                       highlightthickness=0, font=("Segoe UI", 10), padx=4, spacing1=2,
                       spacing3=6, cursor="arrow")
        text.pack(fill="both", expand=True)
        text.tag_configure("h", font=("Segoe UI Semibold", 11), foreground=ACCENT,
                           spacing1=12, spacing3=4)
        text.tag_configure("dim", foreground=MUTED)

        def para(body: str, tag: str = "") -> None:
            text.insert("end", body + "\n", tag)

        para("The idea", "h")
        para("A laptop trackpad switches apps because three fingers move together. "
             "Here the side button is the one that starts the gesture and your normal "
             "clicking fingers join in — three fingers, same shape, on a mouse.")

        para("Using it", "h")
        para("1.  Press and hold a side button.\n"
             "2.  While holding it, also hold left click (or right, or both).\n"
             "3.  Flick the mouse left, right, up or down. Keep flicking the same way "
             "to keep going — the app switcher stays open while you hold, exactly like "
             "the trackpad.\n"
             "4.  Let go of the side button to commit.")

        para("Your normal clicks still work", "h")
        para("A side button pressed on its own still sends Back/Forward — the press is "
             "held back for a moment and replayed when you release it without forming a "
             "combo. Left and right clicks are never swallowed unless a combo is already "
             "running, so ordinary mousing is untouched.")

        para("If a gesture doesn't reach an app", "h")
        para("Windows blocks input sent from a normal program to an elevated one. If "
             "gestures do nothing in an app running as administrator, run this program "
             "as administrator too.", "dim")

        para("Where things live", "h")
        para(f"Settings: {cfgmod.CONFIG_PATH}", "dim")

        text.configure(state="disabled")

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(20, 10))
        bar.pack(fill="x", side="bottom")
        self.lbl_status = ttk.Label(bar, text="Starting…", style="Muted.TLabel")
        self.lbl_status.pack(side="left")
        self.lbl_last = ttk.Label(bar, text="", style="Muted.TLabel")
        self.lbl_last.pack(side="right")

    def _dark_titlebar(self) -> None:
        """Ask DWM for a dark title bar so the frame matches the window."""
        try:
            self.root.update_idletasks()
            hwnd = w.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            dwm = ctypes.WinDLL("dwmapi")
            on = ctypes.c_int(1)
            # 20 on current Windows 10/11; 19 on older builds.
            for attribute in (20, 19):
                dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attribute,
                                          ctypes.byref(on), ctypes.sizeof(on))
        except Exception:
            pass

    # -- combo list ----------------------------------------------------------

    def _refresh_tree(self) -> None:
        for key in cfgmod.COMBOS:
            on = bool(self.cfg["bindings"][key]["enabled"])
            self.tree.item(key,
                           text=f" {cfgmod.chord_glyphs(key)}   {cfgmod.chord_label(key)}",
                           values=("on" if on else "off",),
                           tags=("on" if on else "off",))

    def _first_enabled_combo(self) -> str:
        for key in cfgmod.COMBOS:
            if self.cfg["bindings"][key]["enabled"]:
                return key
        return cfgmod.COMBOS[0]

    def _select_combo(self, key: str) -> None:
        self.tree.selection_set(key)
        self.tree.focus(key)
        self._load_combo(key)

    @property
    def _current(self) -> str:
        sel = self.tree.selection()
        return sel[0] if sel else cfgmod.COMBOS[0]

    def _on_tree_select(self) -> None:
        self._load_combo(self._current)

    def _load_combo(self, key: str) -> None:
        binding = self.cfg["bindings"][key]
        self._suspend_writes = True
        try:
            self.lbl_glyph.configure(text=cfgmod.chord_glyphs(key))
            self.lbl_chord.configure(text=cfgmod.chord_label(key))
            self.var_combo_enabled.set(bool(binding["enabled"]))
            for direction in cfgmod.DIRECTIONS:
                spec = binding[direction]
                widgets = self.dir_widgets[direction]
                widgets["box"].set(ACTION_LABELS.get(spec.get("action", "none"),
                                                     ACTION_LABELS["none"]))
                # A disabled Entry ignores insert/delete, so re-enable first;
                # _sync_enabled_state below puts it back as it should be.
                widgets["entry"].configure(state="normal")
                widgets["entry"].delete(0, "end")
                widgets["entry"].insert(0, spec.get("param", ""))
                self._sync_param_row(direction)
        finally:
            self._suspend_writes = False
        self._sync_enabled_state()

    def _sync_enabled_state(self) -> None:
        on = self.var_combo_enabled.get()
        for direction in cfgmod.DIRECTIONS:
            widgets = self.dir_widgets[direction]
            widgets["box"].configure(state="readonly" if on else "disabled")
            widgets["entry"].configure(state="normal" if on else "disabled")
            widgets["browse"].configure(state="normal" if on else "disabled")

    def _sync_param_row(self, direction: str) -> None:
        widgets = self.dir_widgets[direction]
        action = self._selected_action(direction)
        spec = action_def(action)
        if spec.param:
            widgets["param_row"].grid()
            if spec.param == "path":
                widgets["browse"].grid()
                placeholder = r"e.g. C:\Windows\System32\notepad.exe"
            else:
                widgets["browse"].grid_remove()
                placeholder = "e.g. ctrl+shift+n  —  ctrl, shift, alt, win, f1-f24, or any letter"
            widgets["hint"].configure(text="" if widgets["entry"].get() else placeholder)
        else:
            widgets["param_row"].grid_remove()
            widgets["hint"].configure(text=spec.note)

    def _selected_action(self, direction: str) -> str:
        return LABEL_TO_ACTION.get(self.dir_widgets[direction]["box"].get(), "none")

    # -- edits ---------------------------------------------------------------

    def _on_action_change(self, direction: str) -> None:
        if self._suspend_writes:
            return
        self.cfg["bindings"][self._current][direction]["action"] = \
            self._selected_action(direction)
        self._sync_param_row(direction)
        self._apply()

    def _on_param_change(self, direction: str) -> None:
        if self._suspend_writes:
            return
        self.cfg["bindings"][self._current][direction]["param"] = \
            self.dir_widgets[direction]["entry"].get()
        self._apply()

    def _browse(self, direction: str) -> None:
        path = filedialog.askopenfilename(
            title="Choose a program or file",
            filetypes=[("Programs", "*.exe;*.bat;*.cmd;*.lnk"), ("All files", "*.*")])
        if not path:
            return
        entry = self.dir_widgets[direction]["entry"]
        entry.delete(0, "end")
        entry.insert(0, os.path.normpath(path))
        self._on_param_change(direction)

    def _on_combo_toggle(self) -> None:
        self.cfg["bindings"][self._current]["enabled"] = self.var_combo_enabled.get()
        self._sync_enabled_state()
        self._refresh_tree()
        self._apply()

    def _on_master_toggle(self) -> None:
        self.cfg["enabled"] = self.var_enabled.get()
        self.tray.set_enabled(self.cfg["enabled"])
        self._apply()

    def _on_flag_toggle_factory(self, key: str):
        def toggle() -> None:
            var = {"start_minimised": self.var_minimised,
                   "freeze_cursor": self.var_freeze,
                   "ignore_injected": self.var_injected}[key]
            self.cfg[key] = var.get()
            self._apply()
        return toggle

    def _on_startup_toggle(self) -> None:
        wanted = self.var_startup.get()
        if not startup.set_enabled(wanted):
            self.var_startup.set(not wanted)
            messagebox.showerror(cfgmod.APP_TITLE,
                                 "Couldn't update the Windows startup entry.",
                                 parent=self.root)
            return
        self.cfg["run_on_startup"] = wanted
        self._apply()

    def _reset_defaults(self) -> None:
        if not messagebox.askyesno(cfgmod.APP_TITLE,
                                   "Reset every combo and setting back to the defaults?",
                                   parent=self.root):
            return
        keep_startup = self.var_startup.get()
        self.cfg = cfgmod.default_config()
        self.cfg["run_on_startup"] = keep_startup
        self.var_enabled.set(self.cfg["enabled"])
        self.tray.set_enabled(self.cfg["enabled"])
        self.var_minimised.set(self.cfg["start_minimised"])
        self.var_freeze.set(self.cfg["freeze_cursor"])
        self.var_injected.set(self.cfg["ignore_injected"])
        for key, (scale, readout, suffix) in self.sliders.items():
            scale.set(self.cfg[key])
            readout.configure(text=f"{self.cfg[key]}{suffix}")
        self._refresh_tree()
        self._select_combo(self._first_enabled_combo())
        self._apply()

    def _open_config_dir(self) -> None:
        os.makedirs(cfgmod.CONFIG_DIR, exist_ok=True)
        subprocess.Popen(["explorer", cfgmod.CONFIG_DIR])

    # -- apply / save --------------------------------------------------------

    def _apply(self) -> None:
        engine = getattr(self, "engine", None)
        if engine is None:
            return          # still building the window
        engine.update_config(self.cfg)
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(400, self._save)

    def _save(self) -> None:
        self._save_job = None
        try:
            cfgmod.save(self.cfg)
        except OSError as exc:
            self.lbl_last.configure(text=f"Could not save settings: {exc}",
                                    foreground=WARN)

    # -- engine / tray events ------------------------------------------------

    def _engine_state(self, state: dict) -> None:
        self.events.put(("state", state))

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "state":
                    self._render_state(payload)
                elif kind == "show":
                    self._show_window()
                elif kind == "toggle":
                    self.var_enabled.set(not self.var_enabled.get())
                    self._on_master_toggle()
                elif kind == "exit":
                    self._quit()
                    return
        except queue.Empty:
            pass
        self.root.after(60, self._pump)

    def _render_state(self, state: dict) -> None:
        if state.get("error"):
            self.lbl_status.configure(text=state["error"], foreground=WARN)
            return
        if not self.cfg["enabled"]:
            self.lbl_status.configure(text="Gestures paused", foreground=MUTED)
        elif state.get("engaged") and state.get("chord"):
            self.lbl_status.configure(
                text=f"Combo active — {cfgmod.chord_label(state['chord'])}",
                foreground=ACCENT)
        elif state.get("held"):
            held = " + ".join(cfgmod.BUTTON_LABELS[b] for b in state["held"])
            self.lbl_status.configure(text=f"Holding {held}", foreground=MUTED)
        else:
            self.lbl_status.configure(text="Listening", foreground=GOOD)

        fired = state.get("fired")
        if fired:
            chord, direction, action = fired
            self.lbl_last.configure(
                text=f"{cfgmod.DIRECTION_GLYPHS[direction]}  {action_def(action).label}",
                foreground=TEXT)

    # -- window lifecycle ----------------------------------------------------

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_close(self) -> None:
        self.root.withdraw()

    def _quit(self) -> None:
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save()
        self.engine.stop()
        self.tray.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
