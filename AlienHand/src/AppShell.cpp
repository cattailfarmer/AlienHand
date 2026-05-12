#include "AlienHand/AppShell.h"

namespace alienhand {

AppShell::AppShell() = default;

AppShell::~AppShell() {
    SetCursorHidden(false);
    ClipCursor(nullptr);
}

void AppShell::SetCursorHidden(bool hidden) {
    if (hidden) {
        while (ShowCursor(FALSE) >= 0) {
        }
    } else {
        while (ShowCursor(TRUE) < 0) {
        }
    }
    cursor_hidden_ = hidden;
}

void AppShell::AttachWindow(HWND window) {
    window_ = window;
}

void AppShell::SetClientSize(int width, int height) {
    client_width_ = width;
    client_height_ = height;
    SyncCursor();
}

void AppShell::SetPlaying(bool playing) {
    playing_ = playing;
    SyncCursor();
}

void AppShell::PauseToMenu() {
    resume_from_pause_ = playing_;
    playing_ = false;
    SyncCursor();
}

void AppShell::SetActive(bool active) {
    active_ = active;
    SyncCursor();
}

void AppShell::SyncCursor() {
    const bool want_capture = window_ && active_ && playing_;
    if (want_capture) {
        RECT rect{};
        if (GetClientRect(window_, &rect)) {
            POINT origin{rect.left, rect.top};
            ClientToScreen(window_, &origin);
            const LONG width = rect.right - rect.left;
            const LONG height = rect.bottom - rect.top;
            rect.left = origin.x;
            rect.top = origin.y;
            rect.right = origin.x + width;
            rect.bottom = origin.y + height;
            ClipCursor(&rect);
        }
        if (!cursor_hidden_) {
            SetCursorHidden(true);
        }
    } else {
        ClipCursor(nullptr);
        if (cursor_hidden_) {
            SetCursorHidden(false);
        }
    }
}

void AppShell::ApplyWindowChrome(HWND window, bool maximize) {
    window_ = window;
    if (maximize) {
        ShowWindow(window_, SW_SHOWMAXIMIZED);
    }
}

void AppShell::StartTimer(HWND window, UINT_PTR timer_id, UINT frame_ms) {
    SetTimer(window, timer_id, frame_ms, nullptr);
}

void AppShell::StopTimer(HWND window, UINT_PTR timer_id) {
    KillTimer(window, timer_id);
}

bool AppShell::IsPlaying() const {
    return playing_;
}

bool AppShell::IsActive() const {
    return active_;
}

bool AppShell::WasPlayingBeforePause() const {
    return resume_from_pause_;
}

}  // namespace alienhand
