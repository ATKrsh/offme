"""
Minimal WH_KEYBOARD_LL test - hook on main thread, keys sent from background thread.
"""
import ctypes, ctypes.wintypes, threading, time, sys

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
WH_KEYBOARD_LL = 13

HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
)

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                 ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                 ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p)]
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                 ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                 ("dwExtraInfo", ctypes.c_void_p)]
class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short),
                 ("wParamH", ctypes.c_short)]
class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]
class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("_u", _U)]

print(f"sizeof INPUT={ctypes.sizeof(INPUT)}", flush=True)
KEYEVENTF_KEYUP = 0x0002

user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype  = ctypes.c_uint

def send_key(vk, up=False):
    inp = INPUT(); inp.type = 1
    inp._u.ki.wVk = vk
    inp._u.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    r = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if r == 0:
        print(f"  SendInput FAILED err={kernel32.GetLastError()}", flush=True)

state = {"hook": None, "blocking": False, "seen": 0, "sup": 0, "quit_tid": None}

def cb(nCode, wParam, lParam):
    if nCode >= 0:
        state["seen"] += 1
        print(f"  CB fired nCode={nCode} wParam={wParam:#06x} seen={state['seen']}", flush=True)
        if state["blocking"]:
            state["sup"] += 1
            return 1
    user32.CallNextHookEx.restype = ctypes.c_long
    return user32.CallNextHookEx(state["hook"], nCode, wParam, lParam)

_proc = HOOKPROC(cb)

# Install hook on MAIN thread
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_ulong]
user32.SetWindowsHookExW.restype  = ctypes.c_void_p
state["hook"] = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _proc, None, 0)
print(f"Hook={state['hook']} err={kernel32.GetLastError()}", flush=True)

state["quit_tid"] = kernel32.GetCurrentThreadId()

# Send keys from background thread
def sender():
    time.sleep(0.5)
    print("Sending pass-through keys...", flush=True)
    for vk in (0x41, 0x42):
        send_key(vk); time.sleep(0.05); send_key(vk, True); time.sleep(0.05)
    time.sleep(0.3)
    
    state["blocking"] = True
    print("Blocking ON - sending keys...", flush=True)
    for vk in (0x43, 0x44, 0x45):
        send_key(vk); time.sleep(0.05); send_key(vk, True); time.sleep(0.05)
    time.sleep(0.3)
    state["blocking"] = False
    
    print(f"\nResult: seen={state['seen']} suppressed={state['sup']}", flush=True)
    if state["hook"]:
        user32.UnhookWindowsHookEx(state["hook"])
    # Quit main loop
    user32.PostThreadMessageW(state["quit_tid"], 0x0012, 0, 0)

threading.Thread(target=sender, daemon=True).start()

# Main thread message pump (hook installed here)
msg = ctypes.wintypes.MSG()
while True:
    r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
    if r <= 0: break
    user32.TranslateMessage(ctypes.byref(msg))
    user32.DispatchMessageW(ctypes.byref(msg))

print("PASS" if state["sup"] >= 4 else f"FAIL (sup={state['sup']})")
sys.exit(0 if state["sup"] >= 4 else 1)
