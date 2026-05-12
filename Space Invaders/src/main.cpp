#include <windows.h>

#include <algorithm>
#include <array>
#include <string>
#include <vector>

#include "AlienHand/AppShell.h"
#include "AlienHand/ControlLayer.h"
#include "AlienHand/DualMouse.h"
#include "AlienHand/VideoSurface.h"

namespace {

constexpr int kClientWidth = 1280;
constexpr int kClientHeight = 720;
constexpr UINT_PTR kFrameTimerId = 1;
constexpr UINT kFrameMs = 16;

enum class GameMode {
    Title,
    Playing,
    Menu,
    Config,
    GameOver,
};

enum class MenuChoice {
    Resume,
    Configure,
    Quit,
};

struct Player {
    float x = 0.0f;
    float fire_cooldown = 0.0f;
    int lives = 3;
    int score = 0;
};

struct Bullet {
    float x = 0.0f;
    float y = 0.0f;
    float vy = 0.0f;
    int owner = 0;
    bool active = false;
};

struct Enemy {
    float x = 0.0f;
    float y = 0.0f;
    bool alive = true;
};

struct GameState {
    alienhand::AppShell shell;
    alienhand::ControlLayer controls;
    alienhand::DualMouseInput input;
    GameMode mode = GameMode::Title;
    MenuChoice menu_choice = MenuChoice::Resume;
    int config_selection = 0;
    std::array<Player, alienhand::kPlayerCount> players{};
    std::array<Bullet, 16> bullets{};
    std::vector<Enemy> enemies;
    float enemy_dir = 1.0f;
    float enemy_step_timer = 0.0f;
    bool fire_latched[alienhand::kPlayerCount] = {};
    int total_score = 0;
    alienhand::VideoSurface video;
    bool window_active = true;
};

GameState* GetState(HWND window) {
    return reinterpret_cast<GameState*>(GetWindowLongPtrW(window, GWLP_USERDATA));
}

void ResetWave(GameState& game) {
    game.enemies.clear();
    for (int row = 0; row < 5; ++row) {
        for (int col = 0; col < 10; ++col) {
            Enemy enemy{};
            enemy.x = 150.0f + col * 60.0f;
            enemy.y = 90.0f + row * 40.0f;
            game.enemies.push_back(enemy);
        }
    }
    game.enemy_dir = 1.0f;
    game.enemy_step_timer = 0.0f;
}

void ResetGame(GameState& game) {
    game.players[0] = Player{240.0f, 0.0f, 3, 0};
    game.players[1] = Player{720.0f, 0.0f, 3, 0};
    for (auto& bullet : game.bullets) {
        bullet = {};
    }
    game.total_score = 0;
    game.mode = GameMode::Playing;
    ResetWave(game);
}

void EnterMenu(GameState& game) {
    game.mode = GameMode::Menu;
    game.menu_choice = MenuChoice::Resume;
}

void EnterConfig(GameState& game) {
    game.mode = GameMode::Config;
    game.config_selection = 0;
}

void EnterTitle(GameState& game) {
    game.mode = GameMode::Title;
}

void EnterGameOver(GameState& game) {
    game.mode = GameMode::GameOver;
}

void SyncCursorMode(GameState& game) {
    const bool want_capture = game.window_active && game.mode == GameMode::Playing;
    game.shell.SetActive(game.window_active);
    game.shell.SetPlaying(want_capture);
}

float ClampPlayerX(float x) {
    return std::clamp(x, 40.0f, static_cast<float>(kClientWidth - 40));
}

bool AnyEnemiesAlive(const GameState& game) {
    for (const auto& enemy : game.enemies) {
        if (enemy.alive) {
            return true;
        }
    }
    return false;
}

void FireBullet(GameState& game, int owner, float x, float y) {
    for (auto& bullet : game.bullets) {
        if (!bullet.active) {
            bullet.active = true;
            bullet.owner = owner;
            bullet.x = x;
            bullet.y = y;
            bullet.vy = -420.0f;
            return;
        }
    }
}

void UpdatePlayer(GameState& game, std::size_t index, float dt) {
    Player& player = game.players[index];
    const alienhand::PlayerCommand command = game.controls.GetPlayerCommand(index);
    player.x = ClampPlayerX(player.x + command.move);
    player.fire_cooldown = std::max(0.0f, player.fire_cooldown - dt);

    const bool fire_pressed = command.fire;
    if (fire_pressed && !game.fire_latched[index] && player.fire_cooldown <= 0.0f) {
        FireBullet(game, static_cast<int>(index), player.x, 500.0f);
        player.fire_cooldown = 0.35f;
    }
    game.fire_latched[index] = fire_pressed;
}

void UpdateBullets(GameState& game, float dt) {
    for (auto& bullet : game.bullets) {
        if (!bullet.active) {
            continue;
        }
        bullet.y += bullet.vy * dt;
        if (bullet.y < 0.0f) {
            bullet.active = false;
            continue;
        }

        RECT bullet_rect{
            static_cast<LONG>(bullet.x - 3.0f),
            static_cast<LONG>(bullet.y - 10.0f),
            static_cast<LONG>(bullet.x + 3.0f),
            static_cast<LONG>(bullet.y + 10.0f),
        };

        for (auto& enemy : game.enemies) {
            if (!enemy.alive) {
                continue;
            }
            RECT enemy_rect{
                static_cast<LONG>(enemy.x - 20.0f),
                static_cast<LONG>(enemy.y - 15.0f),
                static_cast<LONG>(enemy.x + 20.0f),
                static_cast<LONG>(enemy.y + 15.0f),
            };
            RECT overlap{};
            if (IntersectRect(&overlap, &bullet_rect, &enemy_rect)) {
                enemy.alive = false;
                bullet.active = false;
                game.players[bullet.owner].score += 10;
                game.total_score += 10;
                break;
            }
        }
    }
}

void UpdateEnemies(GameState& game, float dt) {
    game.enemy_step_timer += dt;
    const float tick = std::max(0.18f, 0.8f - game.total_score * 0.002f);
    if (game.enemy_step_timer < tick) {
        return;
    }
    game.enemy_step_timer = 0.0f;

    float min_x = 99999.0f;
    float max_x = -99999.0f;
    for (const auto& enemy : game.enemies) {
        if (!enemy.alive) {
            continue;
        }
        min_x = std::min(min_x, enemy.x);
        max_x = std::max(max_x, enemy.x);
    }
    if (min_x > max_x) {
        ResetWave(game);
        return;
    }

    const float left_edge = 60.0f;
    const float right_edge = static_cast<float>(kClientWidth - 60);
    const float step = std::min(18.0f + game.total_score * 0.1f, 30.0f);
    const bool hit_edge = (game.enemy_dir < 0.0f && min_x - step <= left_edge) ||
                          (game.enemy_dir > 0.0f && max_x + step >= right_edge);

    if (hit_edge) {
        game.enemy_dir *= -1.0f;
        for (auto& enemy : game.enemies) {
            if (enemy.alive) {
                enemy.y += 18.0f;
            }
        }
    } else {
        for (auto& enemy : game.enemies) {
            if (enemy.alive) {
                enemy.x += step * game.enemy_dir;
            }
        }
    }

    for (const auto& enemy : game.enemies) {
        if (enemy.alive && enemy.y > 470.0f) {
            EnterGameOver(game);
            return;
        }
    }
}

void UpdateGame(GameState& game, float dt) {
    if (game.mode != GameMode::Playing) {
        game.controls.ClearTransient();
        return;
    }

    for (std::size_t i = 0; i < alienhand::kPlayerCount; ++i) {
        UpdatePlayer(game, i, dt);
    }
    UpdateBullets(game, dt);
    UpdateEnemies(game, dt);

    if (!AnyEnemiesAlive(game)) {
        ResetWave(game);
    }

    game.controls.ClearTransient();
}

void DrawRect(HDC dc, float left, float top, float right, float bottom, COLORREF color) {
    HBRUSH brush = CreateSolidBrush(color);
    RECT rect{
        static_cast<LONG>(left),
        static_cast<LONG>(top),
        static_cast<LONG>(right),
        static_cast<LONG>(bottom),
    };
    FillRect(dc, &rect, brush);
    DeleteObject(brush);
}

void DrawTextBlock(HDC dc, const std::wstring& text, RECT rect) {
    DrawTextW(dc, text.c_str(), -1, &rect, DT_LEFT | DT_TOP | DT_WORDBREAK);
}

void PaintGame(HWND window, GameState& game) {
    PAINTSTRUCT ps{};
    HDC dc = BeginPaint(window, &ps);

    RECT client{};
    GetClientRect(window, &client);

    const int width = client.right - client.left;
    const int height = client.bottom - client.top;
    alienhand::VideoSurface& buffer = game.video;
    buffer.Ensure(dc, width, height);

    HBRUSH background = CreateSolidBrush(RGB(9, 11, 18));
    buffer.Fill(background);
    DeleteObject(background);

    HDC buffer_dc = buffer.DeviceContext();
    SetBkMode(buffer_dc, TRANSPARENT);
    SetTextColor(buffer_dc, RGB(235, 238, 243));

    HFONT font = CreateFontW(
        20, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, FF_DONTCARE, L"Segoe UI");
    HFONT old_font = static_cast<HFONT>(SelectObject(buffer_dc, font));

    RECT title_rect{24, 16, 500, 60};
    DrawTextW(buffer_dc, L"AlienHand Space Invaders", -1, &title_rect, DT_LEFT | DT_TOP);

    if (game.mode == GameMode::Title) {
        DrawTextBlock(buffer_dc, L"Press any mouse button to start", RECT{24, 70, client.right - 24, 160});
    } else if (game.mode == GameMode::GameOver) {
        DrawTextBlock(buffer_dc, L"Game Over. Press any mouse button to restart.", RECT{24, 70, client.right - 24, 160});
    } else if (game.mode == GameMode::Menu) {
        std::wstring menu = L"Menu\n\n";
        menu += game.menu_choice == MenuChoice::Resume ? L"> Resume\n" : L"  Resume\n";
        menu += game.menu_choice == MenuChoice::Configure ? L"> Configure\n" : L"  Configure\n";
        menu += game.menu_choice == MenuChoice::Quit ? L"> Quit\n" : L"  Quit\n";
        menu += L"\nEnter selects, Escape returns to the game.";
        DrawTextBlock(buffer_dc, menu, RECT{24, 70, client.right - 24, 300});
    } else if (game.mode == GameMode::Config) {
        const alienhand::PlayerControlConfig p1 = game.controls.GetPlayerConfig(0);
        const alienhand::PlayerControlConfig p2 = game.controls.GetPlayerConfig(1);
        std::wstring config = L"Configuration\n\n";
        config += game.config_selection == 0 ? L"> P1 sensitivity: " : L"  P1 sensitivity: ";
        config += std::to_wstring(p1.sensitivity);
        config += L"\n";
        config += game.config_selection == 1 ? L"> P2 sensitivity: " : L"  P2 sensitivity: ";
        config += std::to_wstring(p2.sensitivity);
        config += L"\n\nUp/Down selects, Left/Right adjusts by 0.1, Escape returns.";
        DrawTextBlock(buffer_dc, config, RECT{24, 70, client.right - 24, 300});
    }

    if (game.mode == GameMode::Playing) {
        for (const auto& enemy : game.enemies) {
            if (enemy.alive) {
                DrawRect(buffer_dc, enemy.x - 18.0f, enemy.y - 12.0f, enemy.x + 18.0f, enemy.y + 12.0f, RGB(192, 82, 82));
            }
        }
        for (const auto& bullet : game.bullets) {
            if (bullet.active) {
                DrawRect(buffer_dc, bullet.x - 2.0f, bullet.y - 8.0f, bullet.x + 2.0f, bullet.y + 8.0f, RGB(252, 236, 126));
            }
        }
        for (std::size_t i = 0; i < game.players.size(); ++i) {
            const Player& player = game.players[i];
            const COLORREF color = i == 0 ? RGB(100, 186, 255) : RGB(123, 239, 123);
            DrawRect(buffer_dc, player.x - 24.0f, 515.0f, player.x + 24.0f, 540.0f, color);
        }
    }

    std::wstring hud;
    hud += L"P1 score: ";
    hud += std::to_wstring(game.players[0].score);
    hud += L"  lives: ";
    hud += std::to_wstring(game.players[0].lives);
    hud += L"\nP2 score: ";
    hud += std::to_wstring(game.players[1].score);
    hud += L"  lives: ";
    hud += std::to_wstring(game.players[1].lives);
    hud += L"\n\nP1 sensitivity: ";
    hud += std::to_wstring(game.controls.GetPlayerConfig(0).sensitivity);
    hud += L"\nP2 sensitivity: ";
    hud += std::to_wstring(game.controls.GetPlayerConfig(1).sensitivity);
    hud += L"\n\n";
    hud += game.input.BuildOverlay();

    RECT hud_rect{24, 320, client.right - 24, client.bottom - 24};
    DrawTextBlock(buffer_dc, hud, hud_rect);

    SelectObject(buffer_dc, old_font);
    DeleteObject(font);

    buffer.Present(dc, 0, 0, width, height);
    EndPaint(window, &ps);
}

void ApplyDefaultConfigs(GameState& game) {
    game.controls.SetPlayerConfig(0, alienhand::PlayerControlConfig{1.0f});
    game.controls.SetPlayerConfig(1, alienhand::PlayerControlConfig{1.0f});
}

void SelectPrevious(GameState& game) {
    if (game.mode == GameMode::Menu) {
        if (game.menu_choice == MenuChoice::Resume) {
            game.menu_choice = MenuChoice::Quit;
        } else if (game.menu_choice == MenuChoice::Configure) {
            game.menu_choice = MenuChoice::Resume;
        } else {
            game.menu_choice = MenuChoice::Configure;
        }
    } else if (game.mode == GameMode::Config) {
        game.config_selection = (game.config_selection + 1) % 2;
    }
}

void SelectNext(GameState& game) {
    if (game.mode == GameMode::Menu) {
        if (game.menu_choice == MenuChoice::Resume) {
            game.menu_choice = MenuChoice::Configure;
        } else if (game.menu_choice == MenuChoice::Configure) {
            game.menu_choice = MenuChoice::Quit;
        } else {
            game.menu_choice = MenuChoice::Resume;
        }
    } else if (game.mode == GameMode::Config) {
        game.config_selection = (game.config_selection + 1) % 2;
    }
}

void AdjustSensitivity(GameState& game, float delta) {
    const std::size_t index = static_cast<std::size_t>(game.config_selection);
    alienhand::PlayerControlConfig config = game.controls.GetPlayerConfig(index);
    config.sensitivity = std::clamp(config.sensitivity + delta, 0.1f, 6.0f);
    game.controls.SetPlayerConfig(index, config);
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
        ApplyDefaultConfigs(*game);
        ResetGame(*game);
        EnterTitle(*game);
        SyncCursorMode(*game);
        game->shell.StartTimer(window, kFrameTimerId, kFrameMs);
        return 0;
    }
    case WM_INPUT:
        if (game) {
            game->input.HandleRawInput(reinterpret_cast<HRAWINPUT>(lparam));
            if (game->mode == GameMode::Title) {
                ResetGame(*game);
            } else if (game->mode == GameMode::GameOver) {
                ResetGame(*game);
            }
            InvalidateRect(window, nullptr, FALSE);
        }
        return 0;
    case WM_INPUT_DEVICE_CHANGE:
        if (game) {
            game->input.HandleDeviceChange(wparam, lparam);
            InvalidateRect(window, nullptr, FALSE);
        }
        return 0;
    case WM_ACTIVATE:
        if (game) {
            game->window_active = LOWORD(wparam) != WA_INACTIVE;
            if (!game->window_active && game->mode == GameMode::Playing) {
                EnterMenu(*game);
            }
            SyncCursorMode(*game);
        }
        return 0;
    case WM_ACTIVATEAPP:
        if (game) {
            game->window_active = (wparam != FALSE);
            if (!game->window_active && game->mode == GameMode::Playing) {
                EnterMenu(*game);
            }
            SyncCursorMode(*game);
        }
        return 0;
    case WM_TIMER:
        if (game && wparam == kFrameTimerId) {
            UpdateGame(*game, kFrameMs / 1000.0f);
            InvalidateRect(window, nullptr, FALSE);
        }
        return 0;
    case WM_SIZE:
        if (game) {
            game->shell.SetClientSize(LOWORD(lparam), HIWORD(lparam));
        }
        return 0;
    case WM_KEYDOWN:
        if (!game) {
            return 0;
        }
        if (game->mode == GameMode::Menu) {
            if (wparam == VK_ESCAPE) {
                game->mode = GameMode::Playing;
                SyncCursorMode(*game);
            } else if (wparam == VK_UP) {
                SelectPrevious(*game);
            } else if (wparam == VK_DOWN) {
                SelectNext(*game);
            } else if (wparam == VK_RETURN) {
                if (game->menu_choice == MenuChoice::Resume) {
                    game->mode = GameMode::Playing;
                    SyncCursorMode(*game);
                } else if (game->menu_choice == MenuChoice::Configure) {
                    EnterConfig(*game);
                    SyncCursorMode(*game);
                } else {
                    DestroyWindow(window);
                }
            }
            InvalidateRect(window, nullptr, FALSE);
            return 0;
        }
        if (game->mode == GameMode::Config) {
            if (wparam == VK_ESCAPE) {
                EnterMenu(*game);
                SyncCursorMode(*game);
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
        if (game->mode == GameMode::Playing && wparam == VK_ESCAPE) {
            EnterMenu(*game);
            SyncCursorMode(*game);
            InvalidateRect(window, nullptr, FALSE);
            return 0;
        }
        if (game->mode == GameMode::Title && wparam == VK_ESCAPE) {
            DestroyWindow(window);
            return 0;
        }
        return 0;
    case WM_PAINT:
        if (game) {
            PaintGame(window, *game);
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

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show_command) {
    GameState game{};
    WNDCLASSEXW wc{};
    wc.cbSize = sizeof(wc);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = WindowProc;
    wc.hInstance = instance;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = static_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));
    wc.lpszClassName = L"AlienHandSpaceInvadersWindow";

    if (!RegisterClassExW(&wc)) {
        return 1;
    }

    HWND window = CreateWindowExW(
        0,
        wc.lpszClassName,
        L"AlienHand Space Invaders",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        kClientWidth,
        kClientHeight,
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
