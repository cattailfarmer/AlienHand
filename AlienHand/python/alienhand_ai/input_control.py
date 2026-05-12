from __future__ import annotations

import ctypes
from dataclasses import dataclass

from .window_capture import focused_window


user32 = ctypes.windll.user32

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010


@dataclass(frozen=True)
class ClickAction:
    x: int
    y: int
    button: str = "left"


def guarded_click(action: ClickAction, title_contains: str, allow_input: bool) -> None:
    info = focused_window()
    if title_contains.lower() not in info.title.lower():
        raise RuntimeError(f"Focused window {info.title!r} does not match {title_contains!r}")
    if not allow_input:
        raise RuntimeError("Input injection requires --allow-input")

    user32.SetCursorPos(info.left + action.x, info.top + action.y)
    down, up = _button_events(action.button)
    user32.mouse_event(down, 0, 0, 0, 0)
    user32.mouse_event(up, 0, 0, 0, 0)


def _button_events(button: str) -> tuple[int, int]:
    if button == "right":
        return MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    if button == "left":
        return MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
    raise ValueError(f"Unsupported mouse button: {button}")
