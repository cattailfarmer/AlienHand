#pragma once

#include <windows.h>

#include <string>

#include "AlienHand/VideoSurface.h"

namespace alienhand {

struct MenuModel {
    int selection = 0;
    int count = 0;
};

struct ConfigModel {
    int selection = 0;
    float values[2] = {1.0f, 1.0f};
};

void DrawTextLine(HDC dc, int x, int y, int size, const std::wstring& text, COLORREF color);
void DrawPanel(HDC dc, int left, int top, int right, int bottom, COLORREF color);
void DrawSelectionMenu(VideoSurface& surface, const std::wstring& title, const std::wstring items[], int item_count, int selected);
void DrawConfigMenu(VideoSurface& surface, const std::wstring& title, const std::wstring& label0, const std::wstring& label1, float value0, float value1, int selected);
void DrawSprite(HDC dc, const BYTE* pixels, int width, int height, int x, int y, COLORREF transparent = RGB(0, 0, 0));

}  // namespace alienhand

