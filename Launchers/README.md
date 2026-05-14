# AlienHand Observation Launchers

These batch files start a target program and wrap it in the AlienHand telemetry
recorder.

## Launchers

- `observe-space-invaders.bat`
- `observe-burger-blaster.bat`
- `observe-minesweeper-10.bat`
- `observe-godot.bat`
- `run-chat-workbench-live.bat`

## Observation Usage

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

## Chat Workbench Usage

```bat
run-chat-workbench-live.bat [run-name]
```

The default run name is `chat-workbench-live`.

This launcher starts the app-owned Ergo server, payload resolver, The Lounge,
and seeded UUID channel used for reviewing the AlienHand chat/refinement
workbench. It waits for the live runtime to write a ready file, then opens The
Lounge automatically.

Ready files are written under:

```text
AlienHand\python\runs\<run-name>\workbench-live-ready.json
```

Press any key in the launcher window to request shutdown. The launcher writes a
`stop` file into the run directory, and the live runtime exits after it observes
that file.

If the launcher reports that The Lounge build output is missing, build The
Lounge first:

```bat
cd /d AlienHand\thelounge
yarn build
```
