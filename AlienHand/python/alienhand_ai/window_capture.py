from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ctypes
from ctypes import wintypes
import struct
from time import monotonic, sleep


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process_id: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


def focused_window() -> WindowInfo:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("No foreground window")

    return window_info_from_handle(hwnd)


def window_info_from_handle(hwnd: int) -> WindowInfo:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))

    return WindowInfo(
        hwnd=hwnd,
        title=buffer.value,
        process_id=int(process_id.value),
        left=rect.left,
        top=rect.top,
        right=rect.right,
        bottom=rect.bottom,
    )


def capture_focused_window(output: str | Path) -> WindowInfo:
    return capture_window(focused_window().hwnd, output)


def capture_window(hwnd: int, output: str | Path) -> WindowInfo:
    info = window_info_from_handle(hwnd)
    if info.width <= 0 or info.height <= 0:
        raise RuntimeError("Window has no drawable area")

    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        raise ctypes.WinError()
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, info.width, info.height)
    old = gdi32.SelectObject(mem_dc, bitmap)

    try:
        if not gdi32.BitBlt(mem_dc, 0, 0, info.width, info.height, hwnd_dc, 0, 0, 0x00CC0020):
            raise ctypes.WinError()
        _write_bitmap(output, mem_dc, bitmap, info.width, info.height)
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)

    return info


def list_windows(process_id: int | None = None, title_contains: str | None = None) -> list[WindowInfo]:
    windows: list[WindowInfo] = []
    title_filter = title_contains.lower() if title_contains else None

    @EnumWindowsProc
    def enum_proc(hwnd: int, lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        try:
            info = window_info_from_handle(hwnd)
        except OSError:
            return True
        if info.width <= 0 or info.height <= 0:
            return True
        if process_id is not None and info.process_id != process_id:
            return True
        if title_filter and title_filter not in info.title.lower():
            return True
        windows.append(info)
        return True

    user32.EnumWindows(enum_proc, 0)
    return windows


def find_window_for_process(process_id: int, timeout: float = 0.0, title_contains: str | None = None) -> WindowInfo | None:
    return find_window(timeout=timeout, process_id=process_id, title_contains=title_contains)


def find_window(timeout: float = 0.0, process_id: int | None = None, title_contains: str | None = None) -> WindowInfo | None:
    deadline = monotonic() + timeout
    while True:
        candidates = list_windows(process_id=process_id, title_contains=title_contains)
        best = _select_best_window(candidates)
        if best is not None:
            return best
        if monotonic() >= deadline:
            return None
        sleep(0.05)


def _select_best_window(windows: list[WindowInfo]) -> WindowInfo | None:
    if not windows:
        return None
    return max(windows, key=lambda window: window.width * window.height)


def _write_bitmap(path: str | Path, dc: int, bitmap: int, width: int, height: int) -> None:
    header_size = 40
    bits = 24
    stride = ((width * bits + 31) // 32) * 4
    image_size = stride * height

    bmi = bytearray(header_size)
    struct.pack_into("<IiiHHIIiiII", bmi, 0, header_size, width, height, 1, bits, 0, image_size, 0, 0, 0, 0)
    pixels = bytearray(image_size)
    pixels_buffer = (ctypes.c_ubyte * image_size).from_buffer(pixels)
    bmi_buffer = (ctypes.c_ubyte * header_size).from_buffer(bmi)
    if not gdi32.GetDIBits(dc, bitmap, 0, height, pixels_buffer, bmi_buffer, 0):
        raise ctypes.WinError()

    file_header = bytearray(14)
    pixel_offset = 14 + header_size
    struct.pack_into("<2sIHHI", file_header, 0, b"BM", pixel_offset + image_size, 0, 0, pixel_offset)
    Path(path).write_bytes(bytes(file_header + bmi + pixels))
