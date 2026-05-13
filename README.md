# AlienHand

AlienHand is a Windows-first game toolkit and experiment space for unusual input,
retro game ports, and screen-reading AI agents.

## Build Everything

```powershell
cmake -S . -B build
cmake --build build --config Release
```

## Run

```powershell
.\build\Space Invaders\Release\SpaceInvaders.exe
.\build\BurgerBlaster\Release\BurgerBlaster.exe
```

Each game can still be configured and built from its own folder. The top-level
build is preferred when working on shared AlienHand framework code.

## Layout

- `AlienHand/`: shared C++ toolkit code.
- `AlienHand/python/`: shared Python AI/screen-reading toolkit code.
- `GodotExtension/`: external C++ GDExtension project for loading AlienHand into a Godot project.
- `Space Invaders/`: two-player raw-mouse game prototype.
- `BurgerBlaster/`: Turbo Pascal game port using AlienHand.
- `Minesweeper-AI/`: first desktop game-agent consumer of `alienhand_ai`.

## IRC Chat Substrate

AlienHand now carries the chat-platform prototype under `AlienHand/`.

- `AlienHand/Ergo/`: app-owned IRC server dependency.
- `AlienHand/thelounge/`: AlienHand fork of the human IRC client surface.
- `AlienHand/BizHawk/`: emulator dependency kept nested under `AlienHand/BizHawk`.
- `AlienHand/python/alienhand_ai/chat_platform.py`: AH1 envelope, payload store, JSONL history, replay, and Ergo lifecycle proofs.
- `AlienHand/python/alienhand_ai/payload_http.py`: loopback payload resolver that serves render rows by message UUID.

Current proof command for live payload lookup:

```powershell
cd C:\Project\Codex_Projects\ReasoningFramework\AlienHand\AlienHand\python
$env:PYTHONPATH="."
python -m alienhand_ai.cli chat-payload-resolver-proof --output runs --name chat-payload-resolver-proof
```

## Godot Integration

The sibling repo `C:\Project\Codex_Projects\LLMOS-Compiler` contains a Godot
fork with an initial `modules/alienhand_agent` bridge. That module emits
engine-internal JSONL events so AlienHand can correlate what Godot says happened
with what the screen recorder, visual tracker, resource monitor, and decision
traces observed externally.

`GodotExtension/` is the complementary GDExtension route: it builds a DLL/shared
library loaded by Godot from a project via `addons/alienhand/alienhand.gdextension`.
Use the engine module when changing your fork is acceptable; use GDExtension when
you want a redistributable addon-style integration.

The generic Python harness can launch and monitor an LLMOS-Compiler build:

```powershell
cd C:\Project\Codex_Projects\ReasoningFramework\AlienHand\AlienHand\python
$env:PYTHONPATH="."
python -m alienhand_ai.cli record-process --output runs --observe-input --no-frame-images -- C:\Project\Codex_Projects\LLMOS-Compiler\bin\godot.windows.editor.x86_64.exe --editor
python -m alienhand_ai.cli interaction-trace --run runs\1
python -m alienhand_ai.cli learning-packets --run runs\1
```

When `--name` is omitted, AlienHand creates the next numeric run directory under
`--output`, such as `runs\1`, `runs\2`, and `runs\3`. Use `--name some-label`
only when you intentionally want a named run folder.

`--observe-input` is passive demonstration capture. AlienHand listens for
keyboard and mouse events only while the target process is foreground, then
`interaction-trace` pairs each event with the previous and current captured
frames. `learning-packets` preserves those unresolved observations as
`pending_llm_digest` packets so an LLM or neural model can label the semantic
effect later.

The default learning frame should be an observation report, not a screenshot:
structured process/window/input/resource state plus a delta from the previous
frame. Use screenshots only when visual learning is explicitly needed. True
internal stack/state snapshots require in-process instrumentation, such as the
Godot `AlienHandAgent`; otherwise AlienHand records external observation state.

Each learning packet includes a novelty vector. The current gates are
recognition, identity, delineation, and interface-route mapping; if any are
below threshold, AlienHand keeps recording and queues the transition for later
digestion instead of asserting a false match.

The round trip is now explicit: AlienHand writes learning opportunity packets,
an LLM/model digest translates those into semantic labels and SOP directives,
and `ingest-learning-digest` stores the result as a learned novelty edge so
future observations near that vector can follow the SOP instead of reopening the
same unknown.

AlienHand can also statically probe the Godot fork and produce an interface map
that links AI/scriptable APIs, editor UI shortcuts, project configuration, input
actions, and the `AlienHandAgent` bridge:

```powershell
cd C:\Project\Codex_Projects\ReasoningFramework\AlienHand\AlienHand\python
$env:PYTHONPATH="."
python -m alienhand_ai.cli probe-godot --root C:\Project\Codex_Projects\LLMOS-Compiler --output C:\Project\Codex_Projects\ReasoningFramework\AlienHand\build\godot-interface-map.json
```

The resulting JSON is intentionally advisory. `api_and_ui` routes identify where
a human-facing editor action and a scriptable API appear to coincide, `ai_bridge`
routes are direct AlienHand hooks inside the fork, and `ui_only` routes are good
targets for future bridge work.
