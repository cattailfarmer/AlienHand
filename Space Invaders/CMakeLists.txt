cmake_minimum_required(VERSION 3.20)

project(SpaceInvaders LANGUAGES CXX)

add_subdirectory("../AlienHand" AlienHand-build)

add_executable(SpaceInvaders WIN32
    src/main.cpp
)

target_compile_features(SpaceInvaders PRIVATE cxx_std_17)

target_compile_definitions(SpaceInvaders PRIVATE
    UNICODE
    _UNICODE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
)

target_link_libraries(SpaceInvaders PRIVATE AlienHand user32 gdi32)
