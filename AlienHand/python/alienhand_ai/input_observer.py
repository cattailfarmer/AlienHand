from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
from ctypes import wintypes
from queue import Queue
from threading import Event, Lock, Thread
from time import time
from typing import Any

from .window_capture import WindowInfo, window_info_from_handle


JsonDict = dict[str, Any]

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_QUIT = 0x0012

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E

LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

LRESULT = ctypes.c_ssize_t
HHOOK = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = HHOOK
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = HINSTANCE


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


@dataclass(frozen=True)
class InputObserverConfig:
    process_id: int | None = None
    hwnd: int | None = None
    title_contains: str | None = None
    include_mouse_move: bool = True
    include_injected: bool = False


@dataclass(frozen=True)
class ObservedInputEvent:
    kind: str
    action: str
    timestamp: float
    hwnd: int
    title: str
    process_id: int
    payload: JsonDict

    def to_dict(self) -> JsonDict:
        return asdict(self)


class InputObserver:
    def __init__(self, config: InputObserverConfig) -> None:
        self.config = config
        self._events: Queue[ObservedInputEvent] = Queue()
        self._stop = Event()
        self._ready = Event()
        self._thread: Thread | None = None
        self._thread_id: int | None = None
        self._keyboard_hook: HHOOK | None = None
        self._mouse_hook: HHOOK | None = None
        self._keyboard_proc: HOOKPROC | None = None
        self._mouse_proc: HOOKPROC | None = None
        self._lock = Lock()
        self._error: BaseException | None = None

    @property
    def error(self) -> BaseException | None:
        return self._error

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="AlienHandInputObserver", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        if self._error is not None:
            raise RuntimeError("Input observer failed to start") from self._error

    def stop(self) -> None:
        self._stop.set()
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def drain(self) -> list[ObservedInputEvent]:
        events: list[ObservedInputEvent] = []
        while not self._events.empty():
            events.append(self._events.get_nowait())
        return events

    def _run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._keyboard_proc = HOOKPROC(self._handle_keyboard)
        self._mouse_proc = HOOKPROC(self._handle_mouse)
        module = kernel32.GetModuleHandleW(None)
        try:
            self._keyboard_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._keyboard_proc, module, 0)
            self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, module, 0)
            if not self._keyboard_hook or not self._mouse_hook:
                raise ctypes.WinError()
            self._ready.set()
            message = MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0 or result == -1:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as exc:
            self._error = exc
            self._ready.set()
        finally:
            with self._lock:
                if self._keyboard_hook:
                    user32.UnhookWindowsHookEx(self._keyboard_hook)
                    self._keyboard_hook = None
                if self._mouse_hook:
                    user32.UnhookWindowsHookEx(self._mouse_hook)
                    self._mouse_hook = None

    def _handle_keyboard(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == HC_ACTION:
            info = self._foreground_target()
            if info is not None:
                hook = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if self.config.include_injected or not (hook.flags & LLKHF_INJECTED):
                    event = _keyboard_event_from_hook(int(w_param), hook, info)
                    if event is not None:
                        self._events.put(event)
        return int(user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param))

    def _handle_mouse(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == HC_ACTION:
            info = self._foreground_target()
            if info is not None:
                hook = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if self.config.include_injected or not (hook.flags & LLMHF_INJECTED):
                    event = _mouse_event_from_hook(int(w_param), hook, info, include_move=self.config.include_mouse_move)
                    if event is not None:
                        self._events.put(event)
        return int(user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param))

    def _foreground_target(self) -> WindowInfo | None:
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return None
        try:
            info = window_info_from_handle(hwnd)
        except OSError:
            return None
        return info if matches_target_window(info, self.config) else None


def matches_target_window(info: WindowInfo, config: InputObserverConfig) -> bool:
    if config.hwnd is not None and info.hwnd != config.hwnd:
        return False
    if config.process_id is not None and info.process_id != config.process_id:
        return False
    if config.title_contains and config.title_contains.lower() not in info.title.lower():
        return False
    return True


def _keyboard_event_from_hook(message: int, hook: KBDLLHOOKSTRUCT, info: WindowInfo) -> ObservedInputEvent | None:
    action = {
        WM_KEYDOWN: "down",
        WM_SYSKEYDOWN: "sys_down",
        WM_KEYUP: "up",
        WM_SYSKEYUP: "sys_up",
    }.get(message)
    if action is None:
        return None
    return ObservedInputEvent(
        kind="keyboard",
        action=action,
        timestamp=time(),
        hwnd=info.hwnd,
        title=info.title,
        process_id=info.process_id,
        payload={
            "vk_code": int(hook.vkCode),
            "scan_code": int(hook.scanCode),
            "flags": int(hook.flags),
            "hook_time": int(hook.time),
            "injected": bool(hook.flags & LLKHF_INJECTED),
        },
    )


def _mouse_event_from_hook(message: int, hook: MSLLHOOKSTRUCT, info: WindowInfo, include_move: bool = True) -> ObservedInputEvent | None:
    action = {
        WM_MOUSEMOVE: "move",
        WM_LBUTTONDOWN: "left_down",
        WM_LBUTTONUP: "left_up",
        WM_RBUTTONDOWN: "right_down",
        WM_RBUTTONUP: "right_up",
        WM_MBUTTONDOWN: "middle_down",
        WM_MBUTTONUP: "middle_up",
        WM_MOUSEWHEEL: "wheel",
        WM_MOUSEHWHEEL: "horizontal_wheel",
        WM_XBUTTONDOWN: "x_down",
        WM_XBUTTONUP: "x_up",
    }.get(message)
    if action is None or (action == "move" and not include_move):
        return None

    screen_x = int(hook.pt.x)
    screen_y = int(hook.pt.y)
    payload: JsonDict = {
        "screen_x": screen_x,
        "screen_y": screen_y,
        "window_x": screen_x - info.left,
        "window_y": screen_y - info.top,
        "flags": int(hook.flags),
        "hook_time": int(hook.time),
        "injected": bool(hook.flags & LLMHF_INJECTED),
    }
    if action in {"wheel", "horizontal_wheel"}:
        payload["wheel_delta"] = _signed_high_word(int(hook.mouseData))
    if action in {"x_down", "x_up"}:
        high_word = _unsigned_high_word(int(hook.mouseData))
        payload["x_button"] = "x1" if high_word == XBUTTON1 else "x2" if high_word == XBUTTON2 else high_word

    return ObservedInputEvent(
        kind="mouse",
        action=action,
        timestamp=time(),
        hwnd=info.hwnd,
        title=info.title,
        process_id=info.process_id,
        payload=payload,
    )


def _signed_high_word(value: int) -> int:
    return ctypes.c_short((value >> 16) & 0xFFFF).value


def _unsigned_high_word(value: int) -> int:
    return (value >> 16) & 0xFFFF
