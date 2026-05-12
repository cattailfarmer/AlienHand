#include "AlienHand/VideoSurface.h"

namespace alienhand {

VideoSurface::~VideoSurface() {
    Reset();
}

void VideoSurface::Reset() {
    if (dc_ && previous_bitmap_) {
        SelectObject(dc_, previous_bitmap_);
        previous_bitmap_ = nullptr;
    }
    if (bitmap_) {
        DeleteObject(bitmap_);
        bitmap_ = nullptr;
    }
    if (dc_) {
        DeleteDC(dc_);
        dc_ = nullptr;
    }
    width_ = 0;
    height_ = 0;
}

void VideoSurface::Ensure(HDC target_dc, int width, int height) {
    if (dc_ && width_ == width && height_ == height) {
        return;
    }

    Reset();
    dc_ = CreateCompatibleDC(target_dc);
    bitmap_ = CreateCompatibleBitmap(target_dc, width, height);
    previous_bitmap_ = static_cast<HBITMAP>(SelectObject(dc_, bitmap_));
    width_ = width;
    height_ = height;
}

void VideoSurface::Fill(HBRUSH brush) {
    if (!dc_) {
        return;
    }
    RECT rect{0, 0, width_, height_};
    FillRect(dc_, &rect, brush);
}

HDC VideoSurface::DeviceContext() const {
    return dc_;
}

void VideoSurface::Present(HDC target_dc, int x, int y, int width, int height) const {
    if (!dc_) {
        return;
    }
    if (width_ == width && height_ == height) {
        BitBlt(target_dc, x, y, width, height, dc_, 0, 0, SRCCOPY);
    } else {
        StretchBlt(target_dc, x, y, width, height, dc_, 0, 0, width_, height_, SRCCOPY);
    }
}

int VideoSurface::Width() const {
    return width_;
}

int VideoSurface::Height() const {
    return height_;
}

}  // namespace alienhand
