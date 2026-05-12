# AlienHand Observation Launchers

These batch files start a target program and wrap it in the AlienHand telemetry
recorder.

## Launchers

- `observe-space-invaders.bat`
- `observe-burger-blaster.bat`
- `observe-minesweeper-10.bat`
- `observe-godot.bat`

## Usage

```bat
observe-space-invaders.bat [frames] [interval] [mode]
```

Defaults:

- `frames`: `2400`
- `interval`: `0.25`
- `mode`: `light`

Modes:

- `light`: structured observation reports with no frame images.
- `visual`: capture frame images and run object tracking.
- `raw`: light mode plus uncompressed raw mouse movement.
- `raw-visual`: visual mode plus uncompressed raw mouse movement.

Reports are written under:

```text
AlienHand\python\runs
```

Minesweeper 10 is launched through its Windows app identity:

```text
shell:AppsFolder\ReflectionIT.Minesweeper10_h3qw2m3pefnrp!App
```

The Minesweeper and Godot launchers use AlienHand's title-based watching mode
because those applications may not be direct child windows of the launcher
process.
