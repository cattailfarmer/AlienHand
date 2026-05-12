# AlienHand AI

Reusable Python primitives for desktop game agents.

This layer is intentionally game-agnostic. Individual projects such as
`Minesweeper-AI` should define their own state parsers, reward logic, and
domain-specific policies while importing shared capture, input, replay, and
model scaffolding from `alienhand_ai`.

## Modules

- `alienhand_ai.window_capture`: focused-window discovery and BMP capture.
- `alienhand_ai.input_control`: guarded mouse actions against the focused window.
- `alienhand_ai.input_observer`: observation-only Windows keyboard/mouse hooks filtered to the foreground target application.
- `alienhand_ai.motion`: semantic motion compression for steady cursor/object movement.
- `alienhand_ai.interaction_trace`: previous-frame + observed-input + current-frame transition builder for later semantic analysis.
- `alienhand_ai.bitmap`: tiny BMP loader for captured observations.
- `alienhand_ai.chat_platform`: first IRC chat substrate truth-test core for AHIRC/1 envelopes, durable payload files, JSONL channel history, payload resolution, and cold replay.
- `alienhand_ai.godot`: parser and normalizer for Godot-side AlienHandAgent JSONL streams.
- `alienhand_ai.interface_probe`: static interface mapper for Godot APIs, editor shortcuts, project inputs, and AlienHand bridge hooks.
- `alienhand_ai.learning`: named learning profiles with mechanics sensitivity, exploration, memory decay, and back-propagation rates.
- `alienhand_ai.learning_memory`: ingests resolved LLM/model digests into learned novelty edges bound to SOP directives.
- `alienhand_ai.learning_packets`: durable `pending_llm_digest` packets for offline observation now and LLM labeling later.
- `alienhand_ai.novelty`: recognition/identity/delineation/mapping confidence gate that routes unknown activity into learning packets.
- `alienhand_ai.replay`: JSONL observation/action/outcome traces.
- `alienhand_ai.resources`: frame-by-frame process CPU/RAM/I/O, system memory/drive, optional network, and optional GPU/VRAM probes.
- `alienhand_ai.runner`: launch/monitor arbitrary executables, optionally capturing windows, resources, object tracks, stdout, and stderr.
- `alienhand_ai.sop`: SOP distillation into simple decision-tree primitives plus `sop_distillation` packets for subjective language.
- `alienhand_ai.telemetry`: black-box recorder for events, screen frames, decision traces, variable snapshots, and HTML reports.
- `alienhand_ai.transformer`: simple grid transformer policy builder.
- `alienhand_ai.vision.font_profile`: font-profile data structures and signature glyph selection.
- `alienhand_ai.vision.lcd`: seven-segment/LCD display decoding for strict numeric readouts, text readouts, and geometry-aware limits for letters that need 14/16-segment or dot-matrix displays.
- `alienhand_ai.vision.dot_matrix`: classic dot-matrix bitmap font templates and nearest-template matching before falling back to trained OCR.
- `alienhand_ai.vision.tracking`: foreground/motion object detection, persistent tracks, group-motion summaries, velocity estimates, and object feature matrices.
- `alienhand_ai.vision.visual_reflex`: fast non-text attention cues for choosing which moving object matters now.
- `alienhand_ai.vision.visual_transformer`: object-sequence transformer scaffold for learned visual reflex and gameplay tracking.

## Telemetry Runs

`alienhand_ai.telemetry` records JSONL events, lightweight observation frames,
state deltas, variables, decision traces, selected actions, and debug payloads.
Frame images are optional artifacts, not the core frame format. Reports are
written as HTML so a human can review the run frame by frame.

Telemetry can also include non-text visual scene tracking. The tracker emits
object detections, persistent track IDs, positions, velocities, and normalized
feature matrices that can feed a transformer policy.

Learning profiles live in `alienhand_ai.learning` and are intentionally separate
from any one game. A project can choose profiles such as `observer`, `novice`,
`steady`, `expert`, or `experimental` to control update speed, exploration,
mechanics sensitivity, and memory decay.

## Generic Harness CLI

Run from `AlienHand/python` with `PYTHONPATH=.`:

```powershell
python -m alienhand_ai.cli record-process --output runs --observe-input --no-frame-images -- --path\to\godot.exe --editor
python -m alienhand_ai.cli interaction-trace --run runs\1
python -m alienhand_ai.cli learning-packets --run runs\1
python -m alienhand_ai.cli sop-compile --file C:\path\procedure.md --output runs\procedure.sop.json --packets-output runs\procedure.sop_packets.jsonl
python -m alienhand_ai.cli ingest-learning-digest --packets runs\llmos\learning_packets.jsonl --digest runs\llmos\digest.json --output runs\llmos\learning_memory.json
python -m alienhand_ai.cli godot-summary --log user://alienhand_godot.jsonl
python -m alienhand_ai.cli godot-import --log C:\path\alienhand_godot.jsonl --output runs --name godot-import --report
python -m alienhand_ai.cli probe-godot --root C:\Project\Codex_Projects\LLMOS-Compiler --output runs\godot-interface-map.json
python -m alienhand_ai.cli chat-truth-test --output runs --name chat-truth-test
python -m alienhand_ai.cli chat-irc-publish --host 127.0.0.1 --port 6667 --nick alienhand-agent --text "hello AlienHand"
```

The generic CLI is the intended path for operating and debugging programs such
as `LLMOS-Compiler`; game-specific CLIs can stay focused on their own domain.

`chat-truth-test` exercises the first AlienHand chat substrate slice. It writes
payload JSON before channel JSONL history, publishes compact `AH1` envelope lines
to a local outbox, reloads the store from disk, and confirms cold replay can
recover payloads while surfacing missing payloads as `payload_error` records.
`chat-irc-publish` uses the same commit ordering, then registers with an IRC
server, joins the `#` plus 32-character UUID hex channel, and sends the compact
`AH1` envelope as a `PRIVMSG`.

If `--name` is omitted or set to `auto`, AlienHand stores the run in the next
numeric folder under `--output`: `runs\1`, `runs\2`, `runs\3`, and so on. It
scans existing numeric directories and increments from the maximum value.

`--observe-input` records keyboard and mouse input only while the launched
process owns the foreground window. It is intentionally passive: AlienHand
watches the user's demonstration instead of injecting actions. `--no-frame-images`
keeps the recorder in lightweight observation-report mode: each frame stores
structured process/window/input/resource state and a version-control-like delta
from the previous frame instead of saving a screenshot. `interaction-trace` then
pairs each observed input with the previous and current telemetry frames, and
`learning-packets` stores unresolved learning opportunities for a later LLM or
neural model pass.

Mouse movement is compressed by default when `--observe-input` is active. A
steady run of motion inside `--motion-window-ms` whose velocity stays within
`--motion-tolerance` of the local mean becomes one `move_segment` packet with
sample count, duration, start/end positions, bounds, mean velocity, and total
tracked distance. Clicks and key events remain discrete. Use
`--raw-mouse-motion` when diagnosing low-level hook behavior and every raw move
sample matters.

For true internal stack/state snapshots, the target needs an AlienHand bridge or
in-process instrumentation, such as Godot's `AlienHandAgent`. Without that,
AlienHand records external observation state rather than pretending to read
private process memory.

The learning packet uses a novelty vector with four gates: recognition,
identity, delineation, and interface-route mapping. If confidence drops below
threshold, the packet remains `pending_llm_digest` and asks a later model to
label the semantic effect, identify the UI element, delineate the screen region,
and match the action back to a known interface route if one exists.

`sop-compile` is the first SOP integration layer. It reduces clear instructions
such as `click Run Project button`, `press F5`, or
`call godot.api.EditorInterface.play_main_scene` into simple primitives. Fuzzy
instructions such as `wait until it looks stable` become `sop_distillation`
packets, asking a later LLM/model pass to resolve subjective terms into explicit
thresholds and decision-tree conditions.

The intended learning workflow is:

1. AlienHand observes a novelty edge and writes a learning opportunity packet.
2. A later LLM/model digest labels the effect and proposes SOP directives.
3. `ingest-learning-digest` stores that result as a learned novelty edge.
4. Future observations near that novelty vector can follow the learned SOP
   instead of asking for a new digest immediately.

`probe-godot` produces a JSON interface map with entries and guidance routes:

- `api_and_ui`: likely equivalence between a Godot editor UI action and a scriptable API.
- `ai_bridge`: direct `AlienHandAgent` methods compiled into the Godot fork.
- `ai_api`: scriptable Godot APIs that look useful for observation, control, input, lifecycle, debugging, or project inspection.
- `ui_only`: editor actions that AlienHand can see but cannot yet confidently drive through an API.
- `project_config` and `project_input`: project files, autoloads, and input actions that explain how a Godot project is wired.

For Store/UWP apps and tools that do not expose their main window as a direct
child of the launcher process, use `--watch-title-only` with
`--title-contains`. AlienHand will launch the helper command but bind capture
and passive input observation to the foreground window whose title matches the
provided text.
