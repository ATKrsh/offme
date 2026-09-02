"""
Standalone test: verifies WH_KEYBOARD_LL can actually suppress keystrokes.
Runs for ~8 seconds:
  - First 3s: hook installed, NOT blocking (baseline - counts events arriving)
  - Next 5s: blocking=True (should count 0 events reaching other apps,
             but our callback still sees them)
Prints hook handle and event counts, exits 0 if hook handle is non-null.
"""
import ctypes, ctypes.wintypes, threading, time, sys

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_KEYDOWN    = 0x0100
WM_SYSKEYDOWN = 0x0104

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

_state = {"hook": None, "blocking": False, "seen": 0, "suppressed": 0}

def _cb(nCode, wParam, lParam):
    if nCode >= 0:
        _state["seen"] += 1
        if _state["blocking"]:
            _state["suppressed"] += 1
            return 1   # suppress
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                       ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
    user32.CallNextHookEx.restype  = ctypes.c_long
    return user32.CallNextHookEx(_state["hook"], nCode, wParam, lParam)

_proc = HOOKPROC(_cb)

_thread_id = None

def _run():
    global _thread_id
    _thread_id = kernel32.GetCurrentThreadId()
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                          ctypes.c_void_p, ctypes.c_ulong]
    user32.SetWindowsHookExW.restype  = ctypes.c_void_p
    _state["hook"] = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _proc, None, 0)
    err = kernel32.GetLastError()
    print(f"Hook handle : {_state['hook']}  LastError={err}", flush=True)

    msg = ctypes.wintypes.MSG()
    while True:
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r == 0 or r == -1:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    if _state["hook"]:
        user32.UnhookWindowsHookEx(_state["hook"])

t = threading.Thread(target=_run, daemon=True)
t.start()
time.sleep(0.5)  # let hook install

print("Hook installed. Please TYPE on your keyboard now for 3 seconds (baseline)...", flush=True)
time.sleep(3)
baseline = _state["seen"]
print(f"Baseline events seen: {baseline}", flush=True)

_state["blocking"] = True
print("BLOCKING ON — type keys for 5 seconds, they should be suppressed...", flush=True)
time.sleep(5)
_state["blocking"] = False

total_seen   = _state["seen"] - baseline
suppressed   = _state["suppressed"]

print(f"\n=== RESULTS ===", flush=True)
print(f"Events seen while blocking : {total_seen}", flush=True)
print(f"Events suppressed          : {suppressed}", flush=True)
print(f"Hook handle valid          : {bool(_state['hook'])}", flush=True)

if _thread_id:
    user32.PostThreadMessageW(_thread_id, 0x0012, 0, 0)

sys.exit(0 if _state["hook"] else 1)
