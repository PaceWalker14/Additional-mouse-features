"""System tray icon, built on Shell_NotifyIcon.

Runs on its own thread with its own message pump, and calls back into the app
from that thread - callers are expected to marshal onto their own loop.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from . import winapi as w
from .config import APP_TITLE
from .icon import icon_path

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04

WM_TRAY = w.WM_APP + 1

MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0000, 0x0800, 0x0008, 0x0001
TPM_LEFTALIGN, TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0000, 0x0002, 0x0100

IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
IDI_APPLICATION = 32512

CMD_OPEN, CMD_TOGGLE, CMD_EXIT = 1, 2, 3


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", w.WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


w.shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
w.shell32.Shell_NotifyIconW.restype = wintypes.BOOL
w.user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
w.user32.DefWindowProcW.restype = w.LRESULT
w.user32.CreateWindowExW.restype = wintypes.HWND
w.user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                    ctypes.c_void_p]
w.user32.TrackPopupMenu.restype = wintypes.BOOL


class TrayIcon:
    def __init__(self, on_open=None, on_toggle=None, on_exit=None,
                 enabled: bool = True) -> None:
        self.on_open = on_open
        self.on_toggle = on_toggle
        self.on_exit = on_exit
        self._enabled = enabled

        self._hwnd = None
        self._hicon = None
        self._tid = 0
        self._added = False
        self._ready = threading.Event()
        self._wndproc = w.WNDPROC(self._proc)  # keep alive
        self._taskbar_created = w.user32.RegisterWindowMessageW("TaskbarCreated")
        self._thread = threading.Thread(target=self._loop, name="amf-tray", daemon=True)

    # -- public ---------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def stop(self) -> None:
        if self._tid:
            w.user32.PostThreadMessageW(self._tid, w.WM_QUIT, 0, 0)
            self._thread.join(timeout=2.0)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._update_tip()

    # -- internals ------------------------------------------------------------

    def _loop(self) -> None:
        hinst = w.kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW()
        cls.lpfnWndProc = self._wndproc
        cls.hInstance = hinst
        cls.lpszClassName = "AMFTrayWindow"
        if not w.user32.RegisterClassW(ctypes.byref(cls)):
            self._ready.set()
            return

        self._hwnd = w.user32.CreateWindowExW(0, "AMFTrayWindow", APP_TITLE, 0,
                                              0, 0, 0, 0, None, None, hinst, None)
        self._tid = w.kernel32.GetCurrentThreadId()
        self._load_icon()
        self._add()
        self._ready.set()

        msg = wintypes.MSG()
        while w.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            w.user32.TranslateMessage(ctypes.byref(msg))
            w.user32.DispatchMessageW(ctypes.byref(msg))

        self._remove()
        if self._hwnd:
            w.user32.DestroyWindow(self._hwnd)
        w.user32.UnregisterClassW("AMFTrayWindow", hinst)

    def _load_icon(self) -> None:
        path = icon_path()
        if path:
            self._hicon = w.user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                              LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not self._hicon:
            # MAKEINTRESOURCE: a stock icon id passed where a string is expected.
            self._hicon = w.user32.LoadIconW(
                None, ctypes.cast(ctypes.c_void_p(IDI_APPLICATION), wintypes.LPCWSTR))

    def _data(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hicon
        state = "on" if self._enabled else "paused"
        nid.szTip = f"{APP_TITLE} — gestures {state}"
        return nid

    def _add(self) -> None:
        self._added = bool(w.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._data())))

    def _update_tip(self) -> None:
        if self._added and self._hwnd:
            w.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._data()))

    def _remove(self) -> None:
        if self._added:
            w.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data()))
            self._added = False

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (w.WM_LBUTTONUP, 0x0203):      # click / double-click
                self._call(self.on_open)
            elif event == w.WM_RBUTTONUP:
                self._menu()
            return 0
        if msg == self._taskbar_created:               # explorer restarted
            self._added = False
            self._add()
            return 0
        if msg == w.WM_DESTROY:
            w.user32.PostQuitMessage(0)
            return 0
        return w.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self) -> None:
        menu = w.user32.CreatePopupMenu()
        if not menu:
            return
        try:
            w.user32.AppendMenuW(menu, MF_STRING, CMD_OPEN, "Open settings")
            w.user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self._enabled else 0),
                                 CMD_TOGGLE, "Gestures enabled")
            w.user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            w.user32.AppendMenuW(menu, MF_STRING, CMD_EXIT, "Exit")

            x, y = w.cursor_pos()
            w.user32.SetForegroundWindow(self._hwnd)
            choice = w.user32.TrackPopupMenu(
                menu, TPM_LEFTALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD,
                x, y, 0, self._hwnd, None)
            w.user32.PostMessageW(self._hwnd, w.WM_NULL, 0, 0)
        finally:
            w.user32.DestroyMenu(menu)

        if choice == CMD_OPEN:
            self._call(self.on_open)
        elif choice == CMD_TOGGLE:
            self._call(self.on_toggle)
        elif choice == CMD_EXIT:
            self._call(self.on_exit)

    @staticmethod
    def _call(fn) -> None:
        if fn is not None:
            try:
                fn()
            except Exception:
                pass
