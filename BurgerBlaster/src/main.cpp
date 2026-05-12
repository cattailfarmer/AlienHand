#include <windows.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "AlienHand/AppShell.h"
#include "AlienHand/ControlLayer.h"
#include "AlienHand/DualMouse.h"
#include "AlienHand/Ui.h"
#include "AlienHand/VideoSurface.h"

namespace {

constexpr int kWindowWidth = 1280;
constexpr int kWindowHeight = 720;
constexpr int kLogicalWidth = 320;
constexpr int kLogicalHeight = 200;
constexpr UINT_PTR kFrameTimerId = 1;
constexpr UINT kFrameMs = 16;
constexpr int kPlayLeft = 30;
constexpr int kPlayTop = 7;
constexpr int kPlayRight = 319;
constexpr int kPlayBottom = 182;

enum class UiMode {
    Playing,
    Menu,
    Config,
};

enum class MenuChoice {
    Resume,
    Configure,
    Quit,
};

struct Shape {
    int width = 0;
    int height = 0;
    std::vector<COLORREF> pixels;
};

struct FallingPiece {
    int x = 0;
    int y = 0;
    int speed = 0;
    int shape = 0;
    bool active = false;
};

struct GameState {
    alienhand::AppShell shell;
    alienhand::ControlLayer controls;
    alienhand::DualMouseInput input;
    alienhand::VideoSurface video;
    bool window_active = true;
    bool running = true;
    UiMode ui_mode = UiMode::Playing;
    MenuChoice menu_choice = MenuChoice::Resume;
    int config_selection = 0;

    std::array<Shape, 12> shapes{};
    std::array<FallingPiece, 10> pieces{};
    std::array<std::array<int, 7>, 5> recipe{};
    int level = 1;
    int score = 0;
    int miss = 5;
    int tray_x = 150;
    int tray_dir = 0;
    int burger_on = 0;
    int burger_count = 0;
    int shot_active = 0;
    int shot_x = 0;
    int shot_y = 0;
    int current_piece = 0;
    int hotdog_active = 0;
};

GameState* GetState(HWND window) {
    return reinterpret_cast<GameState*>(GetWindowLongPtrW(window, GWLP_USERDATA));
}

bool IsPlaying(const GameState& game) {
    return game.running && game.ui_mode == UiMode::Playing;
}

void SyncAppMode(GameState& game) {
    game.shell.SetActive(game.window_active);
    game.shell.SetPlaying(game.window_active && IsPlaying(game));
}

void EnterMenu(GameState& game) {
    game.ui_mode = UiMode::Menu;
    game.menu_choice = MenuChoice::Resume;
    SyncAppMode(game);
}

void EnterConfig(GameState& game) {
    game.ui_mode = UiMode::Config;
    game.config_selection = 0;
    SyncAppMode(game);
}

void ResumeGame(GameState& game) {
    if (game.running) {
        game.ui_mode = UiMode::Playing;
    }
    SyncAppMode(game);
}

void FillRectPixel(Shape& shape, int x, int y, COLORREF color) {
    if (x < 0 || y < 0 || x >= shape.width || y >= shape.height) {
        return;
    }
    shape.pixels[static_cast<std::size_t>(y * shape.width + x)] = color;
}

void DrawShapeLine(Shape& shape, int x0, int y0, int x1, int y1, COLORREF color) {
    int dx = std::abs(x1 - x0);
    int sx = x0 < x1 ? 1 : -1;
    int dy = -std::abs(y1 - y0);
    int sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;

    while (true) {
        FillRectPixel(shape, x0, y0, color);
        if (x0 == x1 && y0 == y1) {
            break;
        }
        int e2 = 2 * err;
        if (e2 >= dy) {
            err += dy;
            x0 += sx;
        }
        if (e2 <= dx) {
            err += dx;
            y0 += sy;
        }
    }
}

Shape MakeShape(int width, int height, std::initializer_list<std::pair<int, int>> points, COLORREF color) {
    Shape shape;
    shape.width = width;
    shape.height = height;
    shape.pixels.assign(static_cast<std::size_t>(width * height), RGB(0, 0, 0));
    auto it = points.begin();
    while (it != points.end()) {
        auto [x0, y0] = *it++;
        if (it == points.end()) {
            break;
        }
        auto [x1, y1] = *it++;
        DrawShapeLine(shape, x0, y0, x1, y1, color);
    }
    return shape;
}

Shape MakeFilledRect(int width, int height, COLORREF fill) {
    Shape shape;
    shape.width = width;
    shape.height = height;
    shape.pixels.assign(static_cast<std::size_t>(width * height), fill);
    return shape;
}

void BuildShapes(GameState& game) {
    game.shapes[0] = MakeFilledRect(16, 8, RGB(0, 0, 0));
    game.shapes[1] = MakeShape(16, 8, {{1, 7}, {3, 1}, {12, 1}, {14, 7}, {1, 7}}, RGB(200, 200, 90));
    game.shapes[2] = MakeShape(16, 8, {{1, 7}, {2, 2}, {13, 2}, {14, 7}, {1, 7}}, RGB(190, 160, 80));
    game.shapes[3] = MakeShape(16, 8, {{1, 7}, {2, 3}, {13, 3}, {14, 7}, {1, 7}}, RGB(170, 110, 70));
    game.shapes[4] = MakeShape(16, 8, {{1, 7}, {3, 1}, {12, 1}, {14, 7}, {1, 7}}, RGB(120, 190, 60));
    game.shapes[5] = MakeShape(16, 8, {{1, 7}, {3, 2}, {12, 2}, {14, 7}, {1, 7}}, RGB(220, 70, 70));
    game.shapes[6] = MakeFilledRect(14, 6, RGB(210, 180, 70));
    game.shapes[7] = MakeFilledRect(14, 6, RGB(120, 70, 20));
    game.shapes[8] = MakeFilledRect(24, 10, RGB(90, 150, 240));
    game.shapes[9] = MakeFilledRect(14, 10, RGB(240, 120, 40));
    game.shapes[10] = MakeFilledRect(36, 6, RGB(60, 60, 60));
    game.shapes[11] = MakeFilledRect(18, 12, RGB(255, 220, 50));
}

void BuildRecipes(GameState& game) {
    game.recipe = {{
        {{3, 3, 1, 2, 0, 0, 0}},
        {{4, 3, 1, 4, 2, 0, 0}},
        {{4, 3, 1, 5, 2, 0, 0}},
        {{5, 3, 1, 3, 1, 2, 0}},
        {{5, 3, 1, 4, 5, 2, 0}},
    }};
}

std::wstring ToText(int value) {
    return std::to_wstring(value);
}

void ResetRound(GameState& game) {
    game.tray_x = 150;
    game.tray_dir = 0;
    game.burger_on = 0;
    game.burger_count = 0;
    game.shot_active = 0;
    game.current_piece = 0;
    game.hotdog_active = 0;
    for (auto& piece : game.pieces) {
        piece = {};
    }
}

void StartGame(GameState& game) {
    game.score = 0;
    game.miss = 5;
    game.level = 1;
    ResetRound(game);
    game.running = true;
    game.ui_mode = UiMode::Playing;
    SyncAppMode(game);
}

void SpawnPiece(GameState& game) {
    for (auto& piece : game.pieces) {
        if (piece.active) {
            continue;
        }

        const int slot = (game.current_piece % 6) + 1;
        game.current_piece = (game.current_piece + 1) % 1000;
        piece.active = true;
        piece.shape = (slot == 6) ? 11 : slot;
        if (slot == 6) {
            piece.shape = game.hotdog_active ? 1 : 9;
            if (piece.shape == 9) {
                game.hotdog_active = 1;
            }
        }
        piece.y = 33 + (rand() % 76);
        piece.speed = (piece.shape == 9) ? (8 + rand() % 8) : (1 + rand() % 15);
        if (rand() % 10 < 4) {
            piece.speed = -piece.speed;
            piece.x = 290;
        } else {
            piece.x = 35;
        }
        return;
    }
}

void DrawShape(HDC dc, const Shape& shape, int x, int y) {
    if (shape.width <= 0 || shape.height <= 0) {
        return;
    }

    for (int yy = 0; yy < shape.height; ++yy) {
        for (int xx = 0; xx < shape.width; ++xx) {
            const COLORREF color = shape.pixels[static_cast<std::size_t>(yy * shape.width + xx)];
            if (color == RGB(0, 0, 0)) {
                continue;
            }
            SetPixel(dc, x + xx, y + yy, color);
        }
    }
}

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

void DrawFrame(GameState& game, HDC dc, int width, int height) {
    game.video.Ensure(dc, kLogicalWidth, kLogicalHeight);
    HDC mem = game.video.DeviceContext();
    HBRUSH background = CreateSolidBrush(RGB(0, 0, 0));
    game.video.Fill(background);
    DeleteObject(background);

    Rectangle(mem, 30, 7, 319, 182);
    for (int i = 179; i <= 180; ++i) {
        MoveToEx(mem, 52, i, nullptr);
        LineTo(mem, game.tray_x - 1, i);
        MoveToEx(mem, game.tray_x + 24, i, nullptr);
        LineTo(mem, 297, i);
    }

    DrawTextLine(mem, 120, 0, 20, L"burger blaster", RGB(80, 120, 255));
    DrawTextLine(mem, 170, 185, 14, L"chances left " + ToText(game.miss), RGB(240, 240, 80));
    DrawTextLine(mem, 30, 185, 14, L"score " + ToText(game.score), RGB(240, 240, 80));

    DrawShape(mem, game.shapes[8], game.tray_x, 181);
    DrawShape(mem, game.shapes[6], 32, 181);
    DrawShape(mem, game.shapes[7], 298, 181);

    std::wstring controls = L"q quit  Space fire  arrows move  return stop";
    DrawTextLine(mem, 0, 195, 12, controls, RGB(180, 180, 220));

    std::wstring status = L"level " + ToText(game.level);
    DrawTextLine(mem, 240, 0, 12, status, RGB(180, 180, 220));

    for (int i = 1; i <= game.burger_on; ++i) {
        const int recipe_piece = game.recipe[game.level - 1][i];
        DrawShape(mem, game.shapes[recipe_piece], game.tray_x + 8, 179 - ((i - 1) * 5));
    }

    if (game.shot_active) {
        MoveToEx(mem, game.shot_x, game.shot_y, nullptr);
        LineTo(mem, game.shot_x, game.shot_y - 5);
        MoveToEx(mem, game.shot_x + 18, game.shot_y, nullptr);
        LineTo(mem, game.shot_x + 18, game.shot_y - 5);
    }

    for (const auto& piece : game.pieces) {
        if (!piece.active) {
            continue;
        }
        const Shape& shape = game.shapes[piece.shape];
        DrawShape(mem, shape, piece.x, piece.y);
        if (piece.shape == 9) {
            DrawShape(mem, game.shapes[11], piece.x, piece.y);
        }
    }

    if (!game.running) {
        alienhand::DrawPanel(mem, 56, 58, 264, 126, RGB(0, 0, 0));
        alienhand::DrawTextLine(mem, 74, 72, 18, L"Game Over", RGB(255, 255, 255));
        alienhand::DrawTextLine(mem, 74, 98, 12, L"Enter restarts", RGB(200, 200, 200));
    } else if (game.ui_mode == UiMode::Menu) {
        alienhand::DrawPanel(mem, 16, 12, 304, 158, RGB(0, 0, 0));
        const std::wstring items[] = {L"Resume", L"Configure", L"Quit"};
        alienhand::DrawSelectionMenu(game.video, L"Burger Blaster", items, 3, static_cast<int>(game.menu_choice));
    } else if (game.ui_mode == UiMode::Config) {
        alienhand::DrawPanel(mem, 16, 12, 304, 158, RGB(0, 0, 0));
        const auto p1 = game.controls.GetPlayerConfig(0);
        const auto p2 = game.controls.GetPlayerConfig(1);
        alienhand::DrawConfigMenu(game.video, L"Configuration", L"P1 sensitivity", L"P2 sensitivity", p1.sensitivity, p2.sensitivity, game.config_selection);
        alienhand::DrawTextLine(mem, 56, 124, 10, L"Esc returns  Left/Right adjust", RGB(190, 190, 190));
    }

    game.video.Present(dc, 0, 0, width, height);
}

void CheckCatch(GameState& game, int index) {
    FallingPiece& piece = game.pieces[static_cast<std::size_t>(index)];
    if (!piece.active) {
        return;
    }

    const int tray_center = game.tray_x + 15;
    if (std::abs((piece.x + 10) - tray_center) < 10 && game.recipe[game.level - 1][game.burger_on + 1] == std::abs(piece.shape)) {
        game.score += (std::abs(piece.speed) * 5);
        ++game.burger_on;
        piece.active = false;
        if (game.burger_on >= game.burger_count) {
            ++game.level;
            if (game.level > 5) {
                game.level = 1;
            }
            game.burger_on = 0;
            game.burger_count = game.recipe[game.level - 1][0];
        }
    } else {
        --game.miss;
        piece.active = false;
        if (game.miss <= 0) {
            game.running = false;
        }
    }
}

void UpdatePieces(GameState& game) {
    ++game.current_piece;
    if (game.current_piece > 10) {
        game.current_piece = 1;
    }

    if (rand() % 100 < 4) {
        SpawnPiece(game);
    }

    for (std::size_t idx = 0; idx < game.pieces.size(); ++idx) {
        FallingPiece& piece = game.pieces[idx];
        if (!piece.active) {
            continue;
        }

        if (piece.speed == 0) {
            piece.active = false;
            continue;
        }

        if (piece.speed > 0) {
            piece.x += piece.speed;
            if (piece.shape == 9) {
                piece.x += std::abs(game.tray_dir * 2);
            }
            if ((rand() % 100 < 4) && piece.shape == 9) {
                piece.speed = -piece.speed;
            }
            if (piece.x < 35 || piece.x > 290) {
                piece.active = false;
                if (piece.shape == 9) {
                    game.hotdog_active = 0;
                }
            }
        } else {
            piece.y += (rand() % 3) + 2;
            if (piece.y > 176 - (game.burger_on * 5)) {
                CheckCatch(game, static_cast<int>(idx));
            }
        }
    }
}

void UpdateTray(GameState& game) {
    if ((game.tray_dir == 1) && (game.tray_x > 55)) {
        --game.tray_x;
    }
    if ((game.tray_dir == -1) && (game.tray_x < 260)) {
        ++game.tray_x;
    }
}

void UpdateGame(GameState& game, float dt) {
    (void)dt;
    if (!IsPlaying(game)) {
        game.controls.ClearTransient();
        return;
    }
    UpdateTray(game);
    UpdatePieces(game);
}

void ApplyMouseControl(GameState& game) {
    if (!IsPlaying(game)) {
        game.controls.ClearTransient();
        return;
    }

    const auto& state = game.input.State();
    int active = 0;
    for (const auto& entry : state.mice) {
        if (!entry.second.connected) {
            continue;
        }
        ++active;
    }

    const auto command0 = game.controls.GetPlayerCommand(0);
    const auto command1 = game.controls.GetPlayerCommand(1);
    const float move0 = command0.move;
    const float move1 = command1.move;
    game.tray_x = std::clamp(game.tray_x + static_cast<int>(move0 + move1), 55, 260);
    if (command0.fire || command1.fire) {
        game.shot_active = 1;
        game.shot_x = game.tray_x + 7;
        game.shot_y = 176 - (game.burger_on * 5);
    }
    game.controls.ClearTransient();
    (void)active;
}

void SelectPrevious(GameState& game) {
    if (game.ui_mode == UiMode::Menu) {
        if (game.menu_choice == MenuChoice::Resume) {
            game.menu_choice = MenuChoice::Quit;
        } else if (game.menu_choice == MenuChoice::Configure) {
            game.menu_choice = MenuChoice::Resume;
        } else {
            game.menu_choice = MenuChoice::Configure;
        }
    } else if (game.ui_mode == UiMode::Config) {
        game.config_selection = (game.config_selection + 1) % 2;
    }
}

void SelectNext(GameState& game) {
    if (game.ui_mode == UiMode::Menu) {
        if (game.menu_choice == MenuChoice::Resume) {
            game.menu_choice = MenuChoice::Configure;
        } else if (game.menu_choice == MenuChoice::Configure) {
            game.menu_choice = MenuChoice::Quit;
        } else {
            game.menu_choice = MenuChoice::Resume;
        }
    } else if (game.ui_mode == UiMode::Config) {
        game.config_selection = (game.config_selection + 1) % 2;
    }
}

void AdjustSensitivity(GameState& game, float delta) {
    const auto player = static_cast<std::size_t>(game.config_selection);
    auto config = game.controls.GetPlayerConfig(player);
    config.sensitivity = std::clamp(config.sensitivity + delta, 0.1f, 6.0f);
    game.controls.SetPlayerConfig(player, config);
}

LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    auto* game = GetState(window);
    switch (message) {
    case WM_CREATE: {
        auto* create = reinterpret_cast<CREATESTRUCTW*>(lparam);
        SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(create->lpCreateParams));
        game = reinterpret_cast<GameState*>(create->lpCreateParams);
        game->shell.AttachWindow(window);
        RECT client{};
        GetClientRect(window, &client);
        game->shell.SetClientSize(client.right - client.left, client.bottom - client.top);
        game->input.AttachControlLayer(&game->controls);
        game->input.RegisterWindow(window);
        game->controls.SetPlayerConfig(0, alienhand::PlayerControlConfig{1.0f});
        game->controls.SetPlayerConfig(1, alienhand::PlayerControlConfig{1.0f});
        BuildShapes(*game);
        BuildRecipes(*game);
        StartGame(*game);
        game->shell.StartTimer(window, kFrameTimerId, kFrameMs);
        return 0;
    }
    case WM_INPUT:
        if (game) {
            game->input.HandleRawInput(reinterpret_cast<HRAWINPUT>(lparam));
            ApplyMouseControl(*game);
            InvalidateRect(window, nullptr, FALSE);
        }
        return 0;
    case WM_INPUT_DEVICE_CHANGE:
        if (game) {
            game->input.HandleDeviceChange(wparam, lparam);
        }
        return 0;
    case WM_ACTIVATEAPP:
        if (game) {
            game->window_active = (wparam != FALSE);
            if (!game->window_active && IsPlaying(*game)) {
                EnterMenu(*game);
            }
            SyncAppMode(*game);
        }
        return 0;
    case WM_ACTIVATE:
        if (game) {
            game->window_active = LOWORD(wparam) != WA_INACTIVE;
            if (!game->window_active && IsPlaying(*game)) {
                EnterMenu(*game);
            }
            SyncAppMode(*game);
        }
        return 0;
    case WM_TIMER:
        if (game && wparam == kFrameTimerId) {
            UpdateGame(*game, kFrameMs / 1000.0f);
            InvalidateRect(window, nullptr, FALSE);
        }
        return 0;
    case WM_KEYDOWN:
        if (!game) {
            return 0;
        }
        if (!game->running) {
            if (wparam == VK_RETURN || wparam == VK_SPACE) {
                StartGame(*game);
                InvalidateRect(window, nullptr, FALSE);
            } else if (wparam == VK_ESCAPE) {
                EnterMenu(*game);
                InvalidateRect(window, nullptr, FALSE);
            }
            return 0;
        }
        if (game->ui_mode == UiMode::Menu) {
            if (wparam == VK_ESCAPE) {
                ResumeGame(*game);
            } else if (wparam == VK_UP) {
                SelectPrevious(*game);
            } else if (wparam == VK_DOWN) {
                SelectNext(*game);
            } else if (wparam == VK_RETURN) {
                if (game->menu_choice == MenuChoice::Resume) {
                    ResumeGame(*game);
                } else if (game->menu_choice == MenuChoice::Configure) {
                    EnterConfig(*game);
                } else {
                    DestroyWindow(window);
                }
            }
            InvalidateRect(window, nullptr, FALSE);
            return 0;
        }
        if (game->ui_mode == UiMode::Config) {
            if (wparam == VK_ESCAPE) {
                EnterMenu(*game);
            } else if (wparam == VK_UP || wparam == VK_DOWN) {
                SelectNext(*game);
            } else if (wparam == VK_LEFT) {
                AdjustSensitivity(*game, -0.1f);
            } else if (wparam == VK_RIGHT) {
                AdjustSensitivity(*game, 0.1f);
            }
            InvalidateRect(window, nullptr, FALSE);
            return 0;
        }
        if (wparam == VK_ESCAPE) {
            EnterMenu(*game);
            InvalidateRect(window, nullptr, FALSE);
            return 0;
        }
        if (wparam == 'Q' && GetKeyState(VK_SHIFT) >= 0) {
            DestroyWindow(window);
            return 0;
        }
        if (wparam == VK_SPACE) {
            game->shot_active = 1;
            game->shot_x = game->tray_x + 7;
            game->shot_y = 176 - (game->burger_on * 5);
            InvalidateRect(window, nullptr, FALSE);
            return 0;
        }
        if (wparam == VK_RETURN) {
            game->tray_dir = 0;
            return 0;
        }
        if (wparam == VK_LEFT || wparam == 'K') {
            game->tray_dir = -1;
            return 0;
        }
        if (wparam == VK_RIGHT || wparam == 'M') {
            game->tray_dir = 1;
            return 0;
        }
        return 0;
    case WM_SIZE:
        if (game) {
            game->shell.SetClientSize(LOWORD(lparam), HIWORD(lparam));
        }
        return 0;
    case WM_PAINT:
        if (game) {
            PAINTSTRUCT ps{};
            HDC dc = BeginPaint(window, &ps);
            RECT client{};
            GetClientRect(window, &client);
            DrawFrame(*game, dc, client.right - client.left, client.bottom - client.top);
            EndPaint(window, &ps);
            return 0;
        }
        break;
    case WM_ERASEBKGND:
        return 1;
    case WM_DESTROY:
        if (game) {
            game->video.Reset();
            game->shell.StopTimer(window, kFrameTimerId);
        }
        PostQuitMessage(0);
        return 0;
    default:
        break;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

bool CenterWindow(HWND window, int width, int height) {
    RECT rect{0, 0, width, height};
    AdjustWindowRect(&rect, WS_OVERLAPPEDWINDOW, FALSE);
    const int window_width = rect.right - rect.left;
    const int window_height = rect.bottom - rect.top;
    const int x = (GetSystemMetrics(SM_CXSCREEN) - window_width) / 2;
    const int y = (GetSystemMetrics(SM_CYSCREEN) - window_height) / 2;
    return SetWindowPos(window, nullptr, x, y, window_width, window_height, SWP_NOZORDER | SWP_NOACTIVATE);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    GameState game{};

    WNDCLASSEXW wc{};
    wc.cbSize = sizeof(wc);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = WindowProc;
    wc.hInstance = instance;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = static_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));
    wc.lpszClassName = L"BurgerBlasterWindow";

    if (!RegisterClassExW(&wc)) {
        return 1;
    }

    HWND window = CreateWindowExW(
        0,
        wc.lpszClassName,
        L"Burger Blaster",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        kWindowWidth,
        kWindowHeight,
        nullptr,
        nullptr,
        instance,
        &game);
    if (!window) {
        return 1;
    }

    game.shell.ApplyWindowChrome(window, true);
    UpdateWindow(window);

    MSG msg{};
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return static_cast<int>(msg.wParam);
}
