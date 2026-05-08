#pragma once

#include <array>
#include <cstddef>

namespace alienhand {

constexpr std::size_t kPlayerCount = 2;

struct PlayerCommand {
    float move = 0.0f;
    bool fire = false;
};

struct PlayerControlConfig {
    float sensitivity = 1.0f;
};

class ControlLayer {
public:
    void AddPlayerCommand(std::size_t index, const PlayerCommand& command);
    [[nodiscard]] PlayerCommand GetPlayerCommand(std::size_t index) const;
    [[nodiscard]] const std::array<PlayerCommand, kPlayerCount>& Snapshot() const;
    void ClearTransient();
    void SetPlayerConfig(std::size_t index, const PlayerControlConfig& config);
    [[nodiscard]] PlayerControlConfig GetPlayerConfig(std::size_t index) const;

private:
    std::array<PlayerCommand, kPlayerCount> commands_{};
    std::array<PlayerControlConfig, kPlayerCount> configs_{};
};

}  // namespace alienhand
