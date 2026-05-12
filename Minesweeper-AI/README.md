# Minesweeper AI

An AlienHand experiment for learning and playing Minesweeper from the focused
window. Reusable agent primitives live in `../AlienHand/python/alienhand_ai`;
this project keeps the Minesweeper-specific board parser, solver, and CLI.

## Goals

- Capture the foreground window.
- Interpret a Minesweeper board from pixels.
- Represent board state as observations and actions.
- Use safe, opt-in input injection against a target window title.
- Log replay traces for later model training.
- Provide a simple transformer policy skeleton.

## Layout

- `../AlienHand/python/alienhand_ai/window_capture.py`: focused-window capture through Win32 GDI.
- `../AlienHand/python/alienhand_ai/input_control.py`: guarded desktop input actions.
- `../AlienHand/python/alienhand_ai/bitmap.py`: lightweight BMP loading for screen observations.
- `../AlienHand/python/alienhand_ai/replay.py`: reusable JSONL replay logging.
- `../AlienHand/python/alienhand_ai/transformer.py`: reusable grid transformer policy builder.
- `src/minesweeper_ai/board.py`: Minesweeper grid and pixel classifier.
- `src/minesweeper_ai/solver.py`: deterministic baseline move suggestions.
- `src/minesweeper_ai/model.py`: Minesweeper policy wrapper over the AlienHand transformer.

## Quick Start

```powershell
$env:PYTHONPATH="src;..\AlienHand\python"
python -m minesweeper_ai.cli observe --output observation.bmp
python -m minesweeper_ai.cli suggest --bmp observation.bmp --rows 9 --cols 9
python -m minesweeper_ai.cli profiles
python -m minesweeper_ai.cli record --output runs --name first-watch --frames 120 --interval 0.2 --rows 9 --cols 9 --report
python -m minesweeper_ai.cli record --output runs --name visual-watch --frames 120 --interval 0.1 --track-objects --report
python -m unittest discover tests
```

Input injection is intentionally guarded. Use `--allow-input` and
`--title-contains` only when you are ready to send clicks to the focused
Minesweeper window.

## Telemetry

The `record` command creates an AlienHand telemetry run with:

- `events.jsonl`: frame records, events, variables, decisions, and actions.
- `frames/`: captured screen images.
- `manifest.json`: run metadata and selected learning profile.
- `report.html`: optional visual report for reviewing what the agent saw and decided.
- Per-frame resource snapshots for process CPU/RAM/I/O, system memory/drive,
  optional network counters, and optional GPU/VRAM data when a backend is available.
- Optional object tracking with `--track-objects`, including non-text detections,
  persistent track IDs, velocities, reflex cues, and transformer-ready feature matrices.

Use `python -m minesweeper_ai.cli report --run runs\first-watch` to regenerate
the HTML report from an existing run.
