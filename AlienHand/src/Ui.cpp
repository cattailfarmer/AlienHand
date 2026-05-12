#include "AlienHand/Ui.h"

#include <iomanip>
#include <sstream>
#include <vector>

namespace alienhand {

namespace {

std::wstring FormatFloat(float value) {
    std::wostringstream stream;
    stream << std::fixed << std::setprecision(1) << value;
    return stream.str();
}

}  // namespace

void DrawTextLine(HDC dc, int x, int y, int size, const std::wstring& text, COLORREF color) {
    HFONT font = CreateFontW(
        size, 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, FF_DONTCARE, L"Consolas");
    HFONT old_font = static_cast<HFONT>(SelectObject(dc, font));
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, color);
    TextOutW(dc, x, y, text.c_str(), static_cast<int>(text.size()));
    SelectObject(dc, old_font);
    DeleteObject(font);
}

void DrawPanel(HDC dc, int left, int top, int right, int bottom, COLORREF color) {
    HBRUSH brush = CreateSolidBrush(color);
    RECT rect{left, top, right, bottom};
    FillRect(dc, &rect, brush);
    DeleteObject(brush);
}

void DrawSelectionMenu(VideoSurface& surface, const std::wstring& title, const std::wstring items[], int item_count, int selected) {
    HDC dc = surface.DeviceContext();
    DrawTextLine(dc, 24, 16, 20, title, RGB(235, 238, 243));
    for (int i = 0; i < item_count; ++i) {
        std::wstring line = (i == selected ? L"> " : L"  ");
        line += items[i];
        DrawTextLine(dc, 24, 70 + i * 24, 18, line, RGB(235, 238, 243));
    }
}

void DrawConfigMenu(VideoSurface& surface, const std::wstring& title, const std::wstring& label0, const std::wstring& label1, float value0, float value1, int selected) {
    HDC dc = surface.DeviceContext();
    DrawTextLine(dc, 24, 16, 20, title, RGB(235, 238, 243));
    std::wstring line0 = selected == 0 ? L"> " : L"  ";
    line0 += label0;
    line0 += L": ";
    line0 += FormatFloat(value0);
    std::wstring line1 = selected == 1 ? L"> " : L"  ";
    line1 += label1;
    line1 += L": ";
    line1 += FormatFloat(value1);
    DrawTextLine(dc, 24, 70, 18, line0, RGB(235, 238, 243));
    DrawTextLine(dc, 24, 94, 18, line1, RGB(235, 238, 243));
}

void DrawSprite(HDC dc, const BYTE* pixels, int width, int height, int x, int y, COLORREF transparent) {
    for (int yy = 0; yy < height; ++yy) {
        for (int xx = 0; xx < width; ++xx) {
            COLORREF color = *reinterpret_cast<const COLORREF*>(pixels + (yy * width + xx) * sizeof(COLORREF));
            if (color != transparent) {
                SetPixel(dc, x + xx, y + yy, color);
            }
        }
    }
}

}  // namespace alienhand
