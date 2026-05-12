cmake_minimum_required(VERSION 3.20)

project(BurgerBlaster LANGUAGES CXX)

if(NOT TARGET AlienHand)
    add_subdirectory("../AlienHand" AlienHand-build)
endif()

add_executable(BurgerBlaster WIN32
    src/main.cpp
)

target_compile_features(BurgerBlaster PRIVATE cxx_std_17)

target_compile_definitions(BurgerBlaster PRIVATE
    UNICODE
    _UNICODE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
)

target_link_libraries(BurgerBlaster PRIVATE AlienHand user32 gdi32)
