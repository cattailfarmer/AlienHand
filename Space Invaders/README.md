# AlienHand Space Invaders

Windows prototype for two-mouse raw input.

## Standalone Build

```powershell
cmake -S . -B build
cmake --build build --config Release
```

## Run

```powershell
.\build\Release\SpaceInvaders.exe
```

## What it does

- Registers for raw mouse input through `WM_INPUT`
- Distinguishes mice by raw device handle
- Ignores standard pointer-driven gameplay input
- Shows a simple debug overlay with per-device deltas and button state
- Continues running if only one mouse is connected
- Uses the shared AlienHand app shell, control layer, and video surface
