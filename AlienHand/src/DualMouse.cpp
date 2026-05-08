#include "AlienHand/DualMouse.h"

#include <hidusage.h>

#include <cstdint>
#include <string>
#include <vector>

namespace alienhand {

std::wstring DualMouseInput::FormatHandle(HANDLE handle) {
    wchar_t buffer[32];
    swprintf_s(buffer, L"0x%p", handle);
    return buffer;
}

std::wstring DualMouseInput::GetDeviceName(HANDLE device) {
    UINT size = 0;
    if (GetRawInputDeviceInfoW(device, RIDI_DEVICENAME, nullptr, &size) != 0 || size == 0) {
        return L"unknown";
    }

    std::wstring name(size, L'\0');
    if (GetRawInputDeviceInfoW(device, RIDI_DEVICENAME, name.data(), &size) == static_cast<UINT>(-1)) {
        return L"unknown";
    }

    if (!name.empty() && name.back() == L'\0') {
        name.pop_back();
    }
    return name;
}

void DualMouseInput::SetCursorHidden(bool hidden) {
    if (hidden) {
        while (ShowCursor(FALSE) >= 0) {
        }
    } else {
        while (ShowCursor(TRUE) < 0) {
        }
    }
}

MouseState& DualMouseInput::EnsureMouse(HANDLE device) {
    auto [it, inserted] = state_.mice.try_emplace(device);
    MouseState& mouse = it->second;
    if (inserted) {
        mouse.connected = true;
        mouse.name = GetDeviceName(device);
        state_.device_order.push_back(device);
        RefreshAssignments();
    }
    return mouse;
}

void DualMouseInput::RefreshAssignments() {
    state_.primary_mouse = nullptr;
    state_.secondary_mouse = nullptr;
    for (HANDLE device : state_.device_order) {
        auto it = state_.mice.find(device);
        if (it == state_.mice.end() || !it->second.connected) {
            continue;
        }
        if (!state_.primary_mouse) {
            state_.primary_mouse = device;
        } else if (!state_.secondary_mouse && device != state_.primary_mouse) {
            state_.secondary_mouse = device;
        }
    }
}

void DualMouseInput::UpdateCursorCapture() {
    if (!window_) {
        return;
    }

    RECT rect{};
    if (GetClientRect(window_, &rect)) {
        const LONG width = rect.right - rect.left;
        const LONG height = rect.bottom - rect.top;
        POINT origin{rect.left, rect.top};
        ClientToScreen(window_, &origin);
        rect.left = origin.x;
        rect.top = origin.y;
        rect.right = origin.x + width;
        rect.bottom = origin.y + height;
        ClipCursor(&rect);
    }

    if (!cursor_hidden_) {
        SetCursorHidden(true);
        cursor_hidden_ = true;
    }
}

void DualMouseInput::ReleaseCursorCapture() {
    ClipCursor(nullptr);
    if (cursor_hidden_) {
        SetCursorHidden(false);
        cursor_hidden_ = false;
    }
}

void DualMouseInput::RegisterWindow(HWND window) {
    window_ = window;

    RAWINPUTDEVICE rid{};
    rid.usUsagePage = HID_USAGE_PAGE_GENERIC;
    rid.usUsage = HID_USAGE_GENERIC_MOUSE;
    rid.dwFlags = RIDEV_INPUTSINK | RIDEV_DEVNOTIFY;
    rid.hwndTarget = window;
    RegisterRawInputDevices(&rid, 1, sizeof(rid));

    UpdateCursorCapture();
}

void DualMouseInput::HandleRawInput(HRAWINPUT raw_input) {
    UINT size = 0;
    GetRawInputData(raw_input, RID_INPUT, nullptr, &size, sizeof(RAWINPUTHEADER));
    if (size == 0) {
        return;
    }

    std::vector<std::uint8_t> buffer(size);
    if (GetRawInputData(raw_input, RID_INPUT, buffer.data(), &size, sizeof(RAWINPUTHEADER)) != size) {
        return;
    }

    const RAWINPUT* input = reinterpret_cast<const RAWINPUT*>(buffer.data());
    if (input->header.dwType != RIM_TYPEMOUSE) {
        return;
    }

    HANDLE device = input->header.hDevice;
    MouseState& mouse = EnsureMouse(device);
    mouse.seen_since_start = true;
    mouse.last_dx = input->data.mouse.lLastX;
    mouse.last_dy = input->data.mouse.lLastY;
    mouse.x += mouse.last_dx;
    mouse.y += mouse.last_dy;
    mouse.buttons = input->data.mouse.usButtonFlags;
    ApplyCommandForMouse(device, mouse);
}

void DualMouseInput::HandleDeviceChange(WPARAM wparam, LPARAM lparam) {
    HANDLE device = reinterpret_cast<HANDLE>(lparam);
    if (wparam == GIDC_ARRIVAL) {
        MouseState& mouse = EnsureMouse(device);
        mouse.connected = true;
        mouse.name = GetDeviceName(device);
    } else if (wparam == GIDC_REMOVAL) {
        auto it = state_.mice.find(device);
        if (it != state_.mice.end()) {
            it->second.connected = false;
        }
    }

    RefreshAssignments();
}

void DualMouseInput::HandleWindowActivation(bool active) {
    if (active) {
        UpdateCursorCapture();
    } else {
        ReleaseCursorCapture();
    }
}

void DualMouseInput::AttachControlLayer(ControlLayer* controls) {
    controls_ = controls;
}

void DualMouseInput::ResetDeviceAssignments() {
    state_.primary_mouse = nullptr;
    state_.secondary_mouse = nullptr;
    RefreshAssignments();
}

const DualMouseState& DualMouseInput::State() const {
    return state_;
}

void DualMouseInput::ApplyCommandForMouse(HANDLE device, const MouseState& mouse) {
    if (!controls_) {
        return;
    }

    std::size_t player_index = 0;
    if (device == state_.secondary_mouse) {
        player_index = 1;
    }

    PlayerCommand command{};
    command.move = static_cast<float>(mouse.last_dx);
    command.fire = (mouse.buttons & RI_MOUSE_LEFT_BUTTON_DOWN) != 0;
    controls_->AddPlayerCommand(player_index, command);
}

std::wstring DualMouseInput::BuildOverlay() const {
    std::wstring text;
    text += L"AlienHand - raw two-mouse prototype\n";
    text += L"OS cursor is ignored for gameplay\n\n";

    if (state_.mice.empty()) {
        text += L"Waiting for raw mouse input...\n";
        text += L"Plug in two mice to see separate tracks.\n";
        return text;
    }

    int index = 1;
    for (HANDLE device : state_.device_order) {
        auto it = state_.mice.find(device);
        if (it == state_.mice.end()) {
            continue;
        }

        const MouseState& mouse = it->second;
        text += L"Mouse ";
        text += std::to_wstring(index++);
        text += L": ";
        text += FormatHandle(device);
        text += L"\n  name: ";
        text += mouse.name;
        text += L"\n  connected: ";
        text += mouse.connected ? L"yes" : L"no";
        text += L"\n  dx/dy: ";
        text += std::to_wstring(mouse.last_dx);
        text += L", ";
        text += std::to_wstring(mouse.last_dy);
        text += L"\n  pos: ";
        text += std::to_wstring(mouse.x);
        text += L", ";
        text += std::to_wstring(mouse.y);
        text += L"\n  buttons: 0x";
        wchar_t button_hex[16];
        swprintf_s(button_hex, L"%04x", mouse.buttons);
        text += button_hex;
        text += L"\n\n";
    }

    if (!state_.primary_mouse || !state_.secondary_mouse) {
        text += L"Fallback: only one mouse is active right now.\n";
        text += L"The game will keep running and wait for a second device.\n";
    }

    return text;
}

}  // namespace alienhand
