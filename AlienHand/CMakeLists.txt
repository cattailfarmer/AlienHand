add_library(AlienHand STATIC
    src/ControlLayer.cpp
    src/DualMouse.cpp
)

target_include_directories(AlienHand
    PUBLIC
        include
)

target_compile_features(AlienHand PRIVATE cxx_std_17)

target_compile_definitions(AlienHand PRIVATE
    UNICODE
    _UNICODE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
)

target_link_libraries(AlienHand PRIVATE user32)
