#pragma once

#include <windows.h>

namespace alienhand {

class VideoSurface {
public:
    VideoSurface() = default;
    ~VideoSurface();

    VideoSurface(const VideoSurface&) = delete;
    VideoSurface& operator=(const VideoSurface&) = delete;

    void Reset();
    void Ensure(HDC target_dc, int width, int height);
    void Fill(HBRUSH brush);
    HDC DeviceContext() const;
    void Present(HDC target_dc, int x, int y, int width, int height) const;
    int Width() const;
    int Height() const;

private:
    HDC dc_ = nullptr;
    HBITMAP bitmap_ = nullptr;
    HBITMAP previous_bitmap_ = nullptr;
    int width_ = 0;
    int height_ = 0;
};

}  // namespace alienhand
