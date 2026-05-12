#pragma once

#include <windows.h>

#include <string>
#include <unordered_map>
#include <vector>

#include "AlienHand/ControlLayer.h"

namespace alienhand {

struct MouseState {
    bool connected = false;
    bool seen_since_start = false;
    LONG x = 0;
    LONG y = 0;
    USHORT buttons = 0;
    LONG last_dx = 0;
    LONG last_dy = 0;
    std::wstring name;
};

struct DualMouseState {
    std::unordered_map<HANDLE, MouseState> mice;
    std::vector<HANDLE> device_order;
    HANDLE primary_mouse = nullptr;
    HANDLE secondary_mouse = nullptr;
};

class DualMouseInput {
public:
    void RegisterWindow(HWND window);
    void HandleRawInput(HRAWINPUT raw_input);
    void HandleDeviceChange(WPARAM wparam, LPARAM lparam);
    void HandleWindowActivation(bool active);
    void ResetDeviceAssignments();
    void AttachControlLayer(ControlLayer* controls);

    [[nodiscard]] const DualMouseState& State() const;
    [[nodiscard]] std::wstring BuildOverlay() const;

private:
    DualMouseState state_;
    HWND window_ = nullptr;
    ControlLayer* controls_ = nullptr;

    MouseState& EnsureMouse(HANDLE device);
    void RefreshAssignments();
    static std::wstring GetDeviceName(HANDLE device);
    static std::wstring FormatHandle(HANDLE handle);
    void ApplyCommandForMouse(HANDLE device, const MouseState& mouse);
};

}  // namespace alienhand
