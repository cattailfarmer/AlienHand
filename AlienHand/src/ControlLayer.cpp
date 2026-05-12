#include "AlienHand/ControlLayer.h"

namespace alienhand {

void ControlLayer::AddPlayerCommand(std::size_t index, const PlayerCommand& command) {
    if (index >= kPlayerCount) {
        return;
    }
    commands_[index].move += command.move * configs_[index].sensitivity;
    commands_[index].fire = commands_[index].fire || command.fire;
}

PlayerCommand ControlLayer::GetPlayerCommand(std::size_t index) const {
    if (index >= kPlayerCount) {
        return {};
    }
    return commands_[index];
}

const std::array<PlayerCommand, kPlayerCount>& ControlLayer::Snapshot() const {
    return commands_;
}

void ControlLayer::ClearTransient() {
    for (auto& command : commands_) {
        command.move = 0.0f;
        command.fire = false;
    }
}

void ControlLayer::SetPlayerConfig(std::size_t index, const PlayerControlConfig& config) {
    if (index >= kPlayerCount) {
        return;
    }
    configs_[index] = config;
}

PlayerControlConfig ControlLayer::GetPlayerConfig(std::size_t index) const {
    if (index >= kPlayerCount) {
        return {};
    }
    return configs_[index];
}

}  // namespace alienhand
