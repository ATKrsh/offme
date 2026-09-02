# offme.py
# OffMe — Always-on-top quick-toggle control orb.
# Left-click left: disable keyboard | Left-click right: disable mouse
# Left-click center: monitor off   | Right-click left: toggle sound
# Right-click right: toggle internet | Master combo: re-enable all

import sys
import os
import json
import math
import time
import ctypes
import ctypes.wintypes
import subprocess
import threading
import logging
import traceback

# ─── File logging (survives no-console .exe) ──────────────────────
_LOG_PATH = os.path.join(os.environ.get("APPDATA", "."), "OffMe", "offme.log")
os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
logging.basicConfig(
    filename=_LOG_PATH,
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("OffMe")

# Also redirect print() / uncaught exceptions to the log
class _LogWriter:
    def __init__(self, lvl=logging.INFO):
        self._lvl = lvl
        self._buf = ""
    def write(self, msg):
        self._buf += msg
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                log.log(self._lvl, line)
    def flush(self): pass

sys.stdout = _LogWriter(logging.INFO)
sys.stderr = _LogWriter(logging.ERROR)

def _exc_hook(etype, val, tb):
    log.critical("Uncaught exception:\n" + "".join(traceback.format_exception(etype, val, tb)))
sys.excepthook = _exc_hook

log.info("OffMe starting up")

try:
    hwnd_con = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd_con:
        ctypes.windll.user32.ShowWindow(hwnd_con, 0)
except Exception:
    pass

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QSystemTrayIcon, QMenu,
    QAction, QDialog, QLabel, QLineEdit, QPushButton, QCheckBox, QRadioButton,
    QGroupBox, QScrollArea, QGridLayout, QButtonGroup
)
from PyQt5.QtCore import Qt, QPoint, QPointF, QTimer, QPropertyAnimation, QRectF
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QPen, QBrush, QColor, QFont

# ─────────────────────────────────────────────────────────────────
#  Win32 Setup
# ─────────────────────────────────────────────────────────────────
user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_SYSCOMMAND          = 0x0112
SC_MONITORPOWER        = 0xF170
HWND_BROADCAST         = 0xFFFF
WM_APPCOMMAND          = 0x0319
APPCOMMAND_VOLUME_MUTE = 8

WH_KEYBOARD_LL = 13
WH_MOUSE_LL    = 14

WM_MOUSEMOVE   = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP   = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP   = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP   = 0x0208
WM_MOUSEWHEEL  = 0x020A

WM_KEYDOWN    = 0x0100
WM_KEYUP      = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP   = 0x0105

VK_CONTROL = 0x11
VK_SHIFT   = 0x10
VK_MENU    = 0x12
VK_LWIN    = 0x5B
VK_RWIN    = 0x5C

HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      ctypes.c_uint),
        ("scanCode",    ctypes.c_uint),
        ("flags",       ctypes.c_uint),
        ("time",        ctypes.c_uint),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


def lerp_color(c1, c2, t):
    return QColor(
        int(c1.red()   + (c2.red()   - c1.red())   * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue()  + (c2.blue()  - c1.blue())  * t),
        int(c1.alpha() + (c2.alpha() - c1.alpha()) * t),
    )


# ─────────────────────────────────────────────────────────────────
#  Settings
# ─────────────────────────────────────────────────────────────────
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", "."), "OffMe", "settings.json")
DEFAULT_SETTINGS = {
    "middle_mouse_reenable_kb":    True,
    "middle_mouse_reenable_mouse": True,
    "monitor_on_key":              "O",
    "master_combo":                "Ctrl+Alt+M",
    "kb_toggle_combo":             "",
    "mouse_toggle_combo":          "",
    "kb_disable_all":              False,
    "kb_allowlist":                [],
    "mouse_mode":                  1,
    "mouse_motion_left_on":        True,
    "mouse_motion_middle_on":      True,
    "mouse_motion_right_on":       True,
    "mouse_clicks_block_left":     True,
    "mouse_clicks_block_middle":   True,
    "mouse_clicks_block_right":    True,
    "mouse_disable_wheel":         False,
    "pos_x": -1,
    "pos_y": -1,
}


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            d = json.load(f)
            r = dict(DEFAULT_SETTINGS)
            r.update(d)
            return r
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(s):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(s, f, indent=2)
    except Exception as e:
        print(f"[Settings] {e}")


# ─────────────────────────────────────────────────────────────────
#  Key Combo Parsing
# ─────────────────────────────────────────────────────────────────
MOD_VKS = {
    "ctrl": VK_CONTROL, "control": VK_CONTROL,
    "shift": VK_SHIFT,  "alt": VK_MENU, "win": VK_LWIN,
}


def parse_combo(combo_str):
    if not combo_str:
        return None
    parts = [p.strip().lower() for p in combo_str.split("+")]
    vks = set()
    for part in parts:
        if part in MOD_VKS:
            vks.add(MOD_VKS[part])
        elif len(part) == 1:
            v = user32.VkKeyScanW(ord(part.upper()))
            vks.add(v & 0xFF)
        elif part.startswith("f") and part[1:].isdigit():
            vks.add(0x6F + int(part[1:]))
        else:
            return None
    return frozenset(vks) if vks else None


def parse_single_key(key_str):
    s = (key_str or "").strip().lower()
    if not s:
        return None
    if s in MOD_VKS:
        return MOD_VKS[s]
    if len(s) == 1:
        v = user32.VkKeyScanW(ord(s.upper()))
        return v & 0xFF
    if s.startswith("f") and s[1:].isdigit():
        return 0x6F + int(s[1:])
    return None


def get_current_mods():
    mods = set()
    for vk in (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            mods.add(vk)
    return mods


# ─────────────────────────────────────────────────────────────────
#  Low-Level Hook Engine  (separate Win32 thread)
# ─────────────────────────────────────────────────────────────────
class HookEngine:
    def __init__(self, app_ref):
        self.app = app_ref
        self._kb_hook    = None
        self._mouse_hook = None
        self._kb_proc    = None
        self._mouse_proc = None
        self._thread     = None
        self._thread_id  = None
        self._running    = False
        self._mid_n      = 0
        self._mid_t      = 0.0

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True, name="OffMeHook")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        self._install()
        msg = ctypes.wintypes.MSG()
        while self._running:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._uninstall()

    def _install(self):
        self._kb_proc    = HOOKPROC(self._kb_cb)
        self._mouse_proc = HOOKPROC(self._mouse_cb)
        # For WH_KEYBOARD_LL / WH_MOUSE_LL, hMod must be NULL and the
        # callback must be passed directly as a HOOKPROC, NOT cast to void*.
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                              ctypes.c_void_p, ctypes.c_ulong]
        user32.SetWindowsHookExW.restype  = ctypes.c_void_p
        self._kb_hook    = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kb_proc, None, 0)
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_proc, None, 0)
        err = kernel32.GetLastError()
        log.info(f"Hook install: kb_hook={self._kb_hook}  mouse_hook={self._mouse_hook}  LastError={err}")
        if not self._kb_hook:
            log.error(f"KB hook FAILED to install! LastError={err}")
        if not self._mouse_hook:
            log.error(f"Mouse hook FAILED to install! LastError={err}")

    def _uninstall(self):
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype  = ctypes.c_bool
        for h in (self._kb_hook, self._mouse_hook):
            if h:
                user32.UnhookWindowsHookEx(h)
        self._kb_hook = self._mouse_hook = None

    def _next_kb(self, nCode, wParam, lParam):
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                           ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        user32.CallNextHookEx.restype  = ctypes.c_long
        return user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)

    def _next_mouse(self, nCode, wParam, lParam):
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                           ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        user32.CallNextHookEx.restype  = ctypes.c_long
        return user32.CallNextHookEx(self._mouse_hook, nCode, wParam, lParam)

    def _suppress(self):
        return 1

    # ─── Keyboard callback ────────────────────────────────────────
    def _kb_cb(self, nCode, wParam, lParam):
        if nCode < 0:
            return self._next_kb(nCode, wParam, lParam)
        try:
            kb  = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk  = kb.vkCode
            app = self.app
            s   = app.settings
            is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)


            # Monitor re-enable key (always active)
            if app.monitor_off and is_down:
                mon_str = s.get("monitor_on_key", "O")
                combo   = parse_combo(mon_str)
                if combo:
                    mods = get_current_mods(); mods.add(vk)
                    if combo.issubset(mods):
                        QTimer.singleShot(0, app, app._monitor_on)
                        return self._suppress()
                else:
                    if parse_single_key(mon_str) == vk:
                        QTimer.singleShot(0, app, app._monitor_on)
                        return self._suppress()

            # Master combo — always wins, even when kb disabled
            master = parse_combo(s.get("master_combo", "Ctrl+Alt+M"))
            if master and is_down:
                mods = get_current_mods(); mods.add(vk)
                if master.issubset(mods):
                    QTimer.singleShot(0, app, app.master_toggle)
                    return self._suppress()

            # Keyboard disabled?
            if app.kb_disabled:
                if not s.get("kb_disable_all", False):
                    for cs in s.get("kb_allowlist", []):
                        cv = parse_combo(cs)
                        if cv and vk in cv:
                            mods = get_current_mods(); mods.add(vk)
                            if cv.issubset(mods):
                                return self._next_kb(nCode, wParam, lParam)
                return self._suppress()

            # KB toggle shortcut
            kbt = parse_combo(s.get("kb_toggle_combo", ""))
            if kbt and is_down:
                mods = get_current_mods(); mods.add(vk)
                if kbt.issubset(mods):
                    QTimer.singleShot(0, app, app.toggle_keyboard)
                    return self._suppress()

            # Mouse toggle shortcut
            mt = parse_combo(s.get("mouse_toggle_combo", ""))
            if mt and is_down:
                mods = get_current_mods(); mods.add(vk)
                if mt.issubset(mods):
                    QTimer.singleShot(0, app, app.toggle_mouse)
                    return self._suppress()

        except Exception as e:
            log.error(f"[Hook/KB] {e}\n{traceback.format_exc()}")
        return self._next_kb(nCode, wParam, lParam)

    # ─── Mouse callback ───────────────────────────────────────────
    def _mouse_cb(self, nCode, wParam, lParam):
        if nCode < 0:
            return self._next_mouse(nCode, wParam, lParam)
        try:
            app = self.app
            s   = app.settings

            # Monitor re-enable: double middle click
            if app.monitor_off:
                if wParam == WM_MBUTTONDOWN:
                    now = time.time()
                    if now - self._mid_t < 0.6:
                        self._mid_n += 1
                    else:
                        self._mid_n = 1
                    self._mid_t = now
                    if self._mid_n >= 2:
                        self._mid_n = 0
                        QTimer.singleShot(0, app, app._monitor_on)
                        return self._suppress()
                return self._next_mouse(nCode, wParam, lParam)

            # Middle click re-enables kb / mouse
            if wParam == WM_MBUTTONDOWN:
                consumed = False
                if app.kb_disabled and s.get("middle_mouse_reenable_kb", True):
                    QTimer.singleShot(0, app, lambda: app._set_keyboard(False))
                    consumed = True
                if app.mouse_disabled and s.get("middle_mouse_reenable_mouse", True):
                    QTimer.singleShot(0, app, lambda: app._set_mouse(False))
                    consumed = True
                if consumed:
                    return self._suppress()

            # Mouse disabled?
            if app.mouse_disabled:
                mode = s.get("mouse_mode", 1)
                if mode == 1:
                    if wParam == WM_MOUSEMOVE:
                        return self._suppress()
                    if wParam in (WM_LBUTTONDOWN, WM_LBUTTONUP) and not s.get("mouse_motion_left_on", True):
                        return self._suppress()
                    if wParam in (WM_MBUTTONDOWN, WM_MBUTTONUP) and not s.get("mouse_motion_middle_on", True):
                        return self._suppress()
                    if wParam in (WM_RBUTTONDOWN, WM_RBUTTONUP) and not s.get("mouse_motion_right_on", True):
                        return self._suppress()
                elif mode == 2:
                    if wParam in (WM_LBUTTONDOWN, WM_LBUTTONUP) and s.get("mouse_clicks_block_left", True):
                        return self._suppress()
                    if wParam in (WM_MBUTTONDOWN, WM_MBUTTONUP) and s.get("mouse_clicks_block_middle", True):
                        return self._suppress()
                    if wParam in (WM_RBUTTONDOWN, WM_RBUTTONUP) and s.get("mouse_clicks_block_right", True):
                        return self._suppress()
                else:
                    if wParam in (WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP,
                                  WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP):
                        return self._suppress()
                if wParam == WM_MOUSEWHEEL and s.get("mouse_disable_wheel", False):
                    return self._suppress()

        except Exception as e:
            log.error(f"[Hook/Mouse] {e}\n{traceback.format_exc()}")
        return self._next_mouse(nCode, wParam, lParam)


# ─────────────────────────────────────────────────────────────────
#  Network / Sound helpers
# ─────────────────────────────────────────────────────────────────
def toggle_mute():
    hwnd = user32.GetForegroundWindow()
    user32.SendMessageW(hwnd, WM_APPCOMMAND, 0, APPCOMMAND_VOLUME_MUTE << 16)


def get_active_adapters():
    adapters = []
    try:
        r = subprocess.run(["netsh", "interface", "show", "interface"],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            p = line.split()
            if len(p) >= 4 and p[1] == "Connected":
                adapters.append(" ".join(p[3:]))
    except Exception as e:
        print(f"[Net] {e}")
    return adapters


def set_adapter(name, enable):
    action = "enable" if enable else "disable"
    try:
        subprocess.Popen(["netsh", "interface", "set", "interface", name, action],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        print(f"[Net] {e}")


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
#  Settings Dialog
# ─────────────────────────────────────────────────────────────────
DLG_CSS = """
QDialog { background:#1a1d26; color:#e0e0e0; }
QGroupBox { border:1px solid #3a3f52; border-radius:6px; margin-top:10px;
            color:#8ab4f8; font-weight:bold; padding-top:6px; }
QGroupBox::title { subcontrol-origin:margin; left:10px; }
QLabel { color:#c8ccd6; }
QLineEdit { background:#22263a; border:1px solid #3a3f52; border-radius:4px;
            color:#e0e0e0; padding:3px 6px; }
QLineEdit:focus { border:1px solid #5a8af8; }
QCheckBox,QRadioButton { color:#c8ccd6; spacing:6px; }
QCheckBox::indicator,QRadioButton::indicator { width:14px; height:14px;
  border:1px solid #3a3f52; border-radius:3px; background:#22263a; }
QCheckBox::indicator:checked,QRadioButton::indicator:checked {
  background:#5a8af8; border-color:#5a8af8; }
QRadioButton::indicator { border-radius:7px; }
QPushButton { background:#2a3f6f; color:#e0e0e0; border:none;
              border-radius:4px; padding:5px 12px; }
QPushButton:hover { background:#3a5a9f; }
QPushButton#save { background:#1a5a30; }
QPushButton#save:hover { background:#2a7a40; }
QPushButton#del { background:#5a1a1a; padding:2px 6px; min-width:22px; }
QPushButton#del:hover { background:#8a2a2a; }
QScrollArea { border:none; background:transparent; }
"""


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = dict(settings)
        self.setWindowTitle("OffMe — Settings")
        self.setMinimumWidth(500)
        self.setStyleSheet(DLG_CSS)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        outer.setContentsMargins(12, 12, 12, 12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cw = QWidget()
        cw.setStyleSheet("background:transparent;")
        vb = QVBoxLayout(cw)
        vb.setSpacing(10)
        vb.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(cw)
        outer.addWidget(scroll)

        # Re-enable
        g = QGroupBox("Re-enable Options")
        gv = QVBoxLayout(g)
        self.chk_mid_kb    = QCheckBox("Middle mouse re-enables keyboard")
        self.chk_mid_kb.setChecked(self.settings.get("middle_mouse_reenable_kb", True))
        self.chk_mid_mouse = QCheckBox("Middle mouse re-enables mouse")
        self.chk_mid_mouse.setChecked(self.settings.get("middle_mouse_reenable_mouse", True))
        gv.addWidget(self.chk_mid_kb)
        gv.addWidget(self.chk_mid_mouse)
        h = QHBoxLayout()
        h.addWidget(QLabel("Monitor ON key/combo:"))
        self.e_mon = QLineEdit(self.settings.get("monitor_on_key", "O"))
        self.e_mon.setMaximumWidth(130)
        self.e_mon.setPlaceholderText("e.g. O  or  Ctrl+O")
        h.addWidget(self.e_mon)
        h.addStretch()
        gv.addLayout(h)
        vb.addWidget(g)

        # Shortcuts
        g2 = QGroupBox("Shortcuts")
        gg = QGridLayout(g2)
        gg.setColumnMinimumWidth(0, 195)
        shortcuts = [
            ("Master toggle combo:",   "master_combo",       "Ctrl+Alt+M"),
            ("Keyboard toggle combo:", "kb_toggle_combo",    ""),
            ("Mouse toggle combo:",    "mouse_toggle_combo", ""),
        ]
        self._sc = {}
        for row, (lbl, key, dflt) in enumerate(shortcuts):
            gg.addWidget(QLabel(lbl), row, 0)
            e = QLineEdit(self.settings.get(key, dflt))
            e.setPlaceholderText("e.g. Ctrl+Alt+K")
            self._sc[key] = e
            gg.addWidget(e, row, 1)
        vb.addWidget(g2)

        # Keyboard disable options
        g3 = QGroupBox("Keyboard Disable Options")
        gv3 = QVBoxLayout(g3)
        self.chk_kb_all = QCheckBox(
            "Disable All  (no allow-list; mouse motion & clicks strictly ON)")
        self.chk_kb_all.setChecked(self.settings.get("kb_disable_all", False))
        gv3.addWidget(self.chk_kb_all)
        gv3.addWidget(QLabel("Key / combo allow-list  (pass-through even when keyboard disabled):"))
        self._allow_rows = []
        self._allow_w    = QWidget()
        self._allow_w.setStyleSheet("background:transparent;")
        self._allow_vb   = QVBoxLayout(self._allow_w)
        self._allow_vb.setContentsMargins(0, 0, 0, 0)
        self._allow_vb.setSpacing(3)
        for cs in self.settings.get("kb_allowlist", []):
            self._add_row(cs)
        gv3.addWidget(self._allow_w)
        btn_add = QPushButton("+ Add entry")
        btn_add.clicked.connect(lambda: self._add_row(""))
        gv3.addWidget(btn_add)
        vb.addWidget(g3)

        # Mouse disable mode
        g4 = QGroupBox("Mouse Disable Mode")
        gv4 = QVBoxLayout(g4)
        self._mg = QButtonGroup(self)
        self.r1 = QRadioButton("1. Disable motion only  (clicks remain ON — select which below)")
        self.r2 = QRadioButton("2. Disable clicks only  (motion remains ON — select which below)")
        self.r3 = QRadioButton("3. Disable both motion and clicks")
        for i, r in enumerate([self.r1, self.r2, self.r3], 1):
            self._mg.addButton(r, i)
            gv4.addWidget(r)

        mode = self.settings.get("mouse_mode", 1)
        [self.r1, self.r2, self.r3][mode - 1].setChecked(True)

        self.g_m1 = QGroupBox("  Clicks remaining ON (mode 1):")
        h1 = QHBoxLayout(self.g_m1)
        self.c1l = QCheckBox("Left");   self.c1l.setChecked(self.settings.get("mouse_motion_left_on", True))
        self.c1m = QCheckBox("Middle"); self.c1m.setChecked(self.settings.get("mouse_motion_middle_on", True))
        self.c1r = QCheckBox("Right");  self.c1r.setChecked(self.settings.get("mouse_motion_right_on", True))
        for w in [self.c1l, self.c1m, self.c1r]:
            h1.addWidget(w)
        h1.addStretch()
        gv4.addWidget(self.g_m1)

        self.g_m2 = QGroupBox("  Clicks to BLOCK (mode 2):")
        h2 = QHBoxLayout(self.g_m2)
        self.c2l = QCheckBox("Left");   self.c2l.setChecked(self.settings.get("mouse_clicks_block_left", True))
        self.c2m = QCheckBox("Middle"); self.c2m.setChecked(self.settings.get("mouse_clicks_block_middle", True))
        self.c2r = QCheckBox("Right");  self.c2r.setChecked(self.settings.get("mouse_clicks_block_right", True))
        for w in [self.c2l, self.c2m, self.c2r]:
            h2.addWidget(w)
        h2.addStretch()
        gv4.addWidget(self.g_m2)

        self.chk_wheel = QCheckBox("Disable scroll wheel")
        self.chk_wheel.setChecked(self.settings.get("mouse_disable_wheel", False))
        gv4.addWidget(self.chk_wheel)

        self._update_subs(mode)
        self._mg.idClicked.connect(self._update_subs)
        vb.addWidget(g4)

        # Buttons
        br = QHBoxLayout()
        b_cancel = QPushButton("Cancel")
        b_save   = QPushButton("Save")
        b_save.setObjectName("save")
        b_cancel.clicked.connect(self.reject)
        b_save.clicked.connect(self._save)
        br.addStretch()
        br.addWidget(b_cancel)
        br.addWidget(b_save)
        outer.addLayout(br)

    def _add_row(self, val=""):
        rw = QWidget()
        rw.setStyleSheet("background:transparent;")
        h = QHBoxLayout(rw)
        h.setContentsMargins(0, 0, 0, 0)
        e = QLineEdit(val)
        e.setPlaceholderText("e.g.  Ctrl+C  or  F5")
        b = QPushButton("✕")
        b.setObjectName("del")
        b.setFixedWidth(26)
        b.clicked.connect(lambda: self._del_row(rw))
        h.addWidget(e)
        h.addWidget(b)
        self._allow_vb.addWidget(rw)
        self._allow_rows.append((rw, e))

    def _del_row(self, rw):
        self._allow_rows = [(w, e) for w, e in self._allow_rows if w is not rw]
        rw.setParent(None)

    def _update_subs(self, mode):
        self.g_m1.setVisible(mode == 1)
        self.g_m2.setVisible(mode == 2)

    def _save(self):
        s = self.settings
        s["middle_mouse_reenable_kb"]    = self.chk_mid_kb.isChecked()
        s["middle_mouse_reenable_mouse"] = self.chk_mid_mouse.isChecked()
        s["monitor_on_key"]              = self.e_mon.text().strip()
        for key, e in self._sc.items():
            s[key] = e.text().strip()
        s["kb_disable_all"] = self.chk_kb_all.isChecked()
        s["kb_allowlist"]   = [e.text().strip() for _, e in self._allow_rows if e.text().strip()]
        s["mouse_mode"]                = self._mg.checkedId()
        s["mouse_motion_left_on"]      = self.c1l.isChecked()
        s["mouse_motion_middle_on"]    = self.c1m.isChecked()
        s["mouse_motion_right_on"]     = self.c1r.isChecked()
        s["mouse_clicks_block_left"]   = self.c2l.isChecked()
        s["mouse_clicks_block_middle"] = self.c2m.isChecked()
        s["mouse_clicks_block_right"]  = self.c2r.isChecked()
        s["mouse_disable_wheel"]       = self.chk_wheel.isChecked()
        save_settings(s)
        self.accept()


# ─────────────────────────────────────────────────────────────────
#  Orb Drawing Widget
# ─────────────────────────────────────────────────────────────────
class OffMeButton(QWidget):
    OUTER_R = 26
    INNER_R = 11
    SIZE    = 56

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pw = parent
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.SizeAllCursor)
        self.setMouseTracking(True)

        self.is_dragging  = False
        self.drag_pos     = QPoint()
        self._hover_zone  = None
        self._press_zone  = None
        self._rpress_zone = None

        self._hv = dict(inner=0.0, left=0.0, right=0.0)
        self._pv = dict(inner=0.0, left=0.0, right=0.0)
        self._pt = 0.0

        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(28)

    def _zone(self, pos):
        cx = cy = self.SIZE / 2
        dx = pos.x() - cx
        dy = pos.y() - cy
        r  = math.hypot(dx, dy)
        if r <= self.INNER_R:  return "inner"
        if r <= self.OUTER_R:  return "left" if dx < 0 else "right"
        return None

    def _tick(self):
        self._pt = (self._pt + 0.062) % (2 * math.pi)
        tgt = dict(inner=0.0, left=0.0, right=0.0)
        if self._hover_zone in tgt:
            tgt[self._hover_zone] = 1.0
        for z in ("inner", "left", "right"):
            self._hv[z] += (tgt[z] - self._hv[z]) * 0.15
            self._pv[z]  = 1.0 if self._press_zone == z else max(0.0, self._pv[z] - 0.07)
        self.update()

    def paintEvent(self, _):
        try:
            self._paint()
        except Exception as e:
            print(f"[Paint] {e}")

    def _paint(self):
        p  = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        S  = self.SIZE
        cx = cy = S / 2.0
        pls = 0.5 + 0.5 * math.sin(self._pt)
        pw  = self.pw

        CB  = QColor(30, 34, 45, 178)
        CH  = QColor(45, 52, 68, 217)
        CP  = QColor(0, 120, 215, 204)
        CKB = QColor(255, 140, 0,  190)
        CMO = QColor(220, 50,  50, 190)
        CMN = QColor(50,  50,  65, 210)
        CSN = QColor(80,  200, 120,180)
        CNT = QColor(200, 80,  80, 180)

        def bg(z, ac=None):
            c = lerp_color(CB, CH, self._hv[z])
            if ac: c = lerp_color(c, ac, 0.55)
            return lerp_color(c, CP, self._pv[z])

        p.setPen(Qt.NoPen)
        OR = self.OUTER_R
        rect = QRectF(cx-OR, cy-OR, OR*2, OR*2)

        # Left half
        p.setBrush(QBrush(bg("left", CKB if pw.kb_disabled else (CSN if pw.sound_muted else None))))
        p.drawPie(rect, 90*16, 180*16)

        # Right half
        p.setBrush(QBrush(bg("right", CMO if pw.mouse_disabled else (CNT if pw.net_disabled else None))))
        p.drawPie(rect, 270*16, 180*16)

        # Divider
        dpen = QPen(QColor(255, 255, 255, 12))
        dpen.setWidthF(1.0)
        dpen.setStyle(Qt.DashLine)
        p.setPen(dpen)
        ie = self.INNER_R + 2.5
        oe = self.OUTER_R - 1.0
        p.drawLine(QPointF(cx, cy-oe), QPointF(cx, cy-ie))
        p.drawLine(QPointF(cx, cy+ie), QPointF(cx, cy+oe))

        # Sep ring
        sp = QPen(QColor(255, 255, 255, 15))
        sp.setWidthF(1.0)
        p.setPen(sp)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), self.INNER_R+2, self.INNER_R+2)

        # Inner
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(bg("inner", CMN if pw.monitor_off else None)))
        p.drawEllipse(QPointF(cx, cy), self.INNER_R, self.INNER_R)

        # Inner symbol
        if pw.monitor_off:
            p.setPen(QPen(QColor(180, 180, 200, 180)))
            p.setFont(QFont("Segoe UI", 5, QFont.Bold))
            p.drawText(QRectF(cx-10, cy-6, 20, 12), Qt.AlignCenter, "OFF")
        else:
            pen = QPen(QColor(160, 180, 255, 140), 1.5)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(cx-4.5, cy-4.5, 9, 9), 60*16, 240*16)
            p.drawLine(QPointF(cx, cy-7.5), QPointF(cx, cy-3))

        # Outer border ring
        kbo = pw.kb_disabled
        moo = pw.mouse_disabled
        ovh = max(self._hv.values())
        ovp = max(self._pv.values())

        if kbo and moo:
            bc = lerp_color(QColor(180, 80, 220, 153), QColor(210, 110, 255, 220), ovh)
            bw = 2.5
        elif kbo:
            bc = lerp_color(QColor(255, 140, 0, 153), QColor(255, 180, 50, 220), ovh)
            bw = 2.5
        elif moo:
            bc = lerp_color(QColor(220, 50, 50, 153), QColor(255, 80, 80, 220), ovh)
            bw = 2.5
        elif pw.monitor_off:
            bc = lerp_color(QColor(80, 80, 100, 120), QColor(100, 100, 130, 180), ovh)
            bw = 2.0
        else:
            bc = lerp_color(QColor(255, 255, 255, 38), QColor(77, 150, 255, 128), ovh)
            bc = lerp_color(bc, QColor(0, 120, 215, 255), ovp)
            bw = 2.0

        if kbo or moo or pw.monitor_off:
            alpha = min(1.0, bc.alphaF() + (0.12 + 0.18 * pls))
            bc.setAlphaF(alpha)

        pen = QPen(bc)
        pen.setWidthF(bw)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), OR, OR)
        p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            if not self.pw.is_locked:
                self.drag_pos    = ev.globalPos() - self.pw.frameGeometry().topLeft()
                self.is_dragging = False
            self._press_zone = self._zone(ev.pos())
            self.update()
            ev.accept()
        elif ev.button() == Qt.RightButton:
            self._rpress_zone = self._zone(ev.pos())
            ev.accept()
        else:
            super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        z = self._zone(ev.pos())
        if z != self._hover_zone:
            self._hover_zone = z
            self.update()
        if ev.buttons() == Qt.LeftButton and not self.pw.is_locked:
            diff = ev.globalPos() - (self.pw.frameGeometry().topLeft() + self.drag_pos)
            if diff.manhattanLength() > 5:
                self.is_dragging = True
            self.pw.move(ev.globalPos() - self.drag_pos)
            ev.accept()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            pz = self._press_zone
            self._press_zone = None
            self.update()
            if self.is_dragging:
                self.is_dragging = False
                self.pw._save_position()
            else:
                z = self._zone(ev.pos())
                if z == pz:
                    if   z == "left":  self.pw.toggle_keyboard()
                    elif z == "right": self.pw.toggle_mouse()
                    elif z == "inner": self.pw.monitor_off_action()
            ev.accept()
        elif ev.button() == Qt.RightButton:
            rpz = self._rpress_zone
            self._rpress_zone = None
            z = self._zone(ev.pos())
            if z == rpz:
                if   z == "left":  self.pw.toggle_sound()
                elif z == "right": self.pw.toggle_internet()
            ev.accept()
        else:
            super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev):
        self._hover_zone = None
        self.update()
        super().leaveEvent(ev)


# ─────────────────────────────────────────────────────────────────
#  Icon
# ─────────────────────────────────────────────────────────────────
def make_icon(size=32):
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    cx = cy = size / 2.0
    r  = size / 2.0 - 1
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(255, 140, 0, 210)))
    p.drawPie(QRectF(cx-r, cy-r, r*2, r*2), 90*16, 180*16)
    p.setBrush(QBrush(QColor(60, 140, 220, 210)))
    p.drawPie(QRectF(cx-r, cy-r, r*2, r*2), 270*16, 180*16)
    p.setBrush(QBrush(QColor(30, 34, 45, 230)))
    p.drawEllipse(QPointF(cx, cy), r*0.32, r*0.32)
    p.setPen(QPen(QColor(255, 255, 255, 60), 1.5))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.end()
    return pix


# ─────────────────────────────────────────────────────────────────
#  Main Widget
# ─────────────────────────────────────────────────────────────────
TRAY_CSS = """
QMenu { background:#1a1d26; color:#e0e0e0; border:1px solid #3a3f52;
        border-radius:6px; padding:4px; }
QMenu::item { padding:6px 20px; border-radius:4px; }
QMenu::item:selected { background:#2a3f6f; }
QMenu::item:checked { color:#8af888; }
QMenu::item:disabled { color:#8a8e9e; }
QMenu::separator { height:1px; background:#3a3f52; margin:4px 8px; }
"""


class OffMeWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()

        self.kb_disabled    = False
        self.mouse_disabled = False
        self.monitor_off    = False
        self.sound_muted    = False
        self.net_disabled   = False
        self.is_locked      = False
        self._dis_adapters  = []

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
            Qt.Tool | Qt.SubWindow | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(56, 56)

        self._pix  = make_icon(32)
        self._icon = QIcon(self._pix)
        self.setWindowIcon(self._icon)
        self.setWindowTitle("OffMe")
        self.setWindowOpacity(0.5)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.button = OffMeButton(self)
        layout.addWidget(self.button)

        self._setup_tray()

        self.fade = QPropertyAnimation(self, b"windowOpacity")
        self.fade.setDuration(120)

        self.hooks = HookEngine(self)
        self.hooks.start()

        px = self.settings.get("pos_x", -1)
        py = self.settings.get("pos_y", -1)
        if px >= 0 and py >= 0:
            self.move(px, py)

    # ── Fade ──────────────────────────────────────────────────────
    def _fade_to(self, v):
        self.fade.stop()
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(v)
        self.fade.start()

    def enterEvent(self, e):
        self._fade_to(0.95)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._fade_to(0.50)
        super().leaveEvent(e)

    def _save_position(self):
        self.settings["pos_x"] = self.x()
        self.settings["pos_y"] = self.y()
        save_settings(self.settings)

    # ── Actions ───────────────────────────────────────────────────
    def toggle_keyboard(self):
        self._set_keyboard(not self.kb_disabled)

    def _set_keyboard(self, v):
        self.kb_disabled = v
        lbl = "DISABLED" if v else "ENABLED"
        log.info(f"Keyboard {lbl} — kb_hook={self.hooks._kb_hook}")
        self.button.update()
        self._refresh_tray()
        self.tray.showMessage("OffMe", f"Keyboard {lbl.lower()}", QSystemTrayIcon.Information, 1500)

    def toggle_mouse(self):
        self._set_mouse(not self.mouse_disabled)

    def _set_mouse(self, v):
        self.mouse_disabled = v
        lbl = "DISABLED" if v else "ENABLED"
        print(f"[OffMe] Mouse {lbl}")
        self.button.update()
        self._refresh_tray()
        self.tray.showMessage("OffMe", f"Mouse {lbl.lower()}", QSystemTrayIcon.Information, 1500)

    def monitor_off_action(self):
        self.monitor_off = True
        self.button.update()
        QTimer.singleShot(150, self._do_mon_off)

    def _do_mon_off(self):
        user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)

    def _monitor_on(self):
        self.monitor_off = False
        self.button.update()
        print("[OffMe] Monitor ON")
        user32.mouse_event(0x0001, 1, 0, 0, 0)
        user32.mouse_event(0x0001, -1, 0, 0, 0)

    def toggle_sound(self):
        toggle_mute()
        self.sound_muted = not self.sound_muted
        lbl = "MUTED" if self.sound_muted else "UNMUTED"
        print(f"[OffMe] Sound {lbl}")
        self.button.update()
        self._refresh_tray()
        self.tray.showMessage("OffMe", f"Sound {lbl.lower()}", QSystemTrayIcon.Information, 1500)

    def toggle_internet(self):
        if not is_admin():
            self.tray.showMessage(
                "OffMe",
                "Internet toggle needs Admin rights.\nRelaunch OffMe as Administrator.",
                QSystemTrayIcon.Warning, 3000
            )
            return
        if self.net_disabled:
            for a in self._dis_adapters:
                set_adapter(a, True)
            self._dis_adapters = []
            self.net_disabled  = False
            self.tray.showMessage("OffMe", "Internet enabled", QSystemTrayIcon.Information, 1500)
        else:
            adapters = get_active_adapters()
            if adapters:
                for a in adapters:
                    set_adapter(a, False)
                self._dis_adapters = adapters
                self.net_disabled  = True
                self.tray.showMessage("OffMe", "Internet disabled", QSystemTrayIcon.Information, 1500)
            else:
                self.tray.showMessage("OffMe", "No active adapters found",
                                      QSystemTrayIcon.Warning, 2000)
        self.button.update()
        self._refresh_tray()

    def master_toggle(self):
        changed = []
        if self.kb_disabled:
            self._set_keyboard(False); changed.append("keyboard")
        if self.mouse_disabled:
            self._set_mouse(False);    changed.append("mouse")
        if self.monitor_off:
            self._monitor_on();        changed.append("monitor")
        if self.net_disabled:
            for a in self._dis_adapters:
                set_adapter(a, True)
            self._dis_adapters = []
            self.net_disabled  = False
            self.button.update()
            changed.append("internet")
        self._refresh_tray()
        if changed:
            self.tray.showMessage("OffMe", "Re-enabled: " + ", ".join(changed),
                                   QSystemTrayIcon.Information, 2000)

    # ── Tray ──────────────────────────────────────────────────────
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self._icon, self)
        self.tray.setToolTip("OffMe — Input & Display Control")

        m = QMenu()
        m.setStyleSheet(TRAY_CSS)

        hdr = QAction("OffMe", self)
        hdr.setEnabled(False)
        m.addAction(hdr)
        m.addSeparator()

        self.act_kb    = QAction("⌨  Keyboard [ENABLED]",  self)
        self.act_mouse = QAction("🖱  Mouse [ENABLED]",     self)
        self.act_sound = QAction("🔊  Sound [ON]",          self)
        self.act_net   = QAction("🌐  Internet [ON]",       self)
        for a in (self.act_kb, self.act_mouse, self.act_sound, self.act_net):
            a.setCheckable(True)

        self.act_kb.triggered.connect(self.toggle_keyboard)
        self.act_mouse.triggered.connect(self.toggle_mouse)
        self.act_sound.triggered.connect(self.toggle_sound)
        self.act_net.triggered.connect(self.toggle_internet)

        for a in (self.act_kb, self.act_mouse, self.act_sound, self.act_net):
            m.addAction(a)
        m.addSeparator()

        # Detailed Legend Submenu
        legend_menu = m.addMenu("💡 Orb Controls Legend")
        legend_menu.setStyleSheet(TRAY_CSS)
        
        legend_items = [
            ("── Left-Click Orb ──", True),
            ("Left Half:  Disable / Enable Keyboard", False),
            ("Right Half: Disable / Enable Mouse", False),
            ("Center Eye: Turn Monitor Off", False),
            ("", True),
            ("── Right-Click Orb ──", True),
            ("Left Half:  Toggle Sound Mute", False),
            ("Right Half: Toggle Internet Connection", False),
            ("", True),
            ("── Emergency & Reset ──", True),
            ("Middle Mouse Click: Re-enable Inputs", False),
            ("Master Combo: Ctrl+Alt+M (Re-enable All)", False),
        ]
        for text, is_header in legend_items:
            if not text:
                legend_menu.addSeparator()
            else:
                act = QAction(text if is_header else "   " + text, self)
                act.setEnabled(False)
                legend_menu.addAction(act)

        m.addSeparator()

        act_s = QAction("⚙  Settings…", self)
        act_s.triggered.connect(self.open_settings)
        m.addAction(act_s)
        m.addSeparator()

        act_q = QAction("✕  Quit OffMe", self)
        act_q.triggered.connect(QApplication.quit)
        m.addAction(act_q)

        self.tray.setContextMenu(m)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

    def _refresh_tray(self):
        self.act_kb.setChecked(self.kb_disabled)
        self.act_kb.setText("⌨  Keyboard [DISABLED]" if self.kb_disabled else "⌨  Keyboard [ENABLED]")
        self.act_mouse.setChecked(self.mouse_disabled)
        self.act_mouse.setText("🖱  Mouse [DISABLED]" if self.mouse_disabled else "🖱  Mouse [ENABLED]")
        self.act_sound.setChecked(self.sound_muted)
        self.act_sound.setText("🔇  Sound [MUTED]" if self.sound_muted else "🔊  Sound [ON]")
        self.act_net.setChecked(self.net_disabled)
        self.act_net.setText("🌐  Internet [OFF]" if self.net_disabled else "🌐  Internet [ON]")

    def _on_tray(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible(): self.hide()
            else:                self.show(); self.raise_()

    def open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec_() == QDialog.Accepted:
            self.settings = dlg.settings

    def closeEvent(self, ev):
        self._save_position()
        self.hooks.stop()
        super().closeEvent(ev)


# ─────────────────────────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    widget = OffMeWidget()

    if widget.settings.get("pos_x", -1) < 0:
        screen = QApplication.primaryScreen().geometry()
        widget.move(screen.width() - 80, screen.height() - 180)

    widget.show()
    sys.exit(app.exec_())
