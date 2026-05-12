#pragma once

#include <windows.h>

namespace alienhand {

class AppShell {
public:
    AppShell();
    ~AppShell();

    AppShell(const AppShell&) = delete;
    AppShell& operator=(const AppShell&) = delete;

    void AttachWindow(HWND window);
    void SetClientSize(int width, int height);
    void SetPlaying(bool playing);
    void SetActive(bool active);
    void PauseToMenu();
    void SyncCursor();
    void ApplyWindowChrome(HWND window, bool maximize);

    void StartTimer(HWND window, UINT_PTR timer_id, UINT frame_ms);
    void StopTimer(HWND window, UINT_PTR timer_id);

    bool IsPlaying() const;
    bool IsActive() const;
    bool WasPlayingBeforePause() const;

private:
    HWND window_ = nullptr;
    int client_width_ = 0;
    int client_height_ = 0;
    bool playing_ = false;
    bool resume_from_pause_ = false;
    bool active_ = true;
    bool cursor_hidden_ = false;

    void SetCursorHidden(bool hidden);
};

}  // namespace alienhand
