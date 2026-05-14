# AlienHand Godot Surface Bridge Handoff

This is the integration handle for a Codex instance working inside Godot engine.

AlienHand exposes a localhost TCP JSONL stream for AlienHand-aware surfaces.
Godot should treat these messages as an independent virtual pointer stream, not as OS mouse events.

## Live Bridge Command

Run this from `AlienHand/AlienHand/python`:

```powershell
python -m alienhand_ai.cli godot-surface-bridge-live --host 127.0.0.1 --port 47991 --surface-id godot-main-viewport --duration 300 --max-events 10000
```

The bridge is non-invasive:

- It observes Windows Raw Input.
- It publishes AlienHand surface events.
- It does not block mouse input.
- It does not capture mouse input.
- It does not inject mouse input.

## Godot Connection

Connect a TCP client to:

```text
127.0.0.1:47991
```

Read newline-delimited UTF-8 JSON objects.

Each line is one event.

## Event Contract

```json
{
  "protocol": "AH_SURFACE/1",
  "type": "alienhand.pointer",
  "sequence": 0,
  "surface_id": "godot-main-viewport",
  "pointer_id": "alienhand:mouse-b",
  "device_id": "mouse-b",
  "action": "move",
  "x": 660,
  "y": 350,
  "dx": 20,
  "dy": -10,
  "buttons": [],
  "wheel_delta": 0,
  "timestamp_ms": 123456789,
  "source_channel": "alienhand_independent",
  "target_id": "godot-alienhand-surface",
  "metadata": {
    "target": "godot_surface"
  }
}
```

## Required Godot Behavior

- Accept only events with `protocol == "AH_SURFACE/1"`.
- Accept only events with `type == "alienhand.pointer"`.
- Treat `pointer_id` as the durable pointer identity.
- Treat `device_id` as the logical AlienHand device identity.
- Do not collapse AlienHand events into the OS mouse.
- Render or route `pointer_id == "alienhand:mouse-b"` as an independent pointer.
- Use `x` and `y` as absolute surface coordinates for the current prototype.
- Use `dx` and `dy` for motion deltas when needed.
- Use `buttons` as the current pressed-button state after the event.

## Actions

Known actions:

- `move`
- `left_down`
- `left_up`
- `right_down`
- `right_up`
- `middle_down`
- `middle_up`
- `wheel`
- `horizontal_wheel`

## Proof Command

Run this from `AlienHand/AlienHand/python`:

```powershell
python -m alienhand_ai.cli godot-surface-bridge-proof --output runs --name godot-surface-bridge-proof
```

Expected result:

- `bridge_ok: true`
- `protocol: AH_SURFACE/1`
- `transport: tcp-jsonl`
- four simulated `alienhand.pointer` events
- a JSONL proof journal at `runs/godot-surface-bridge-proof/godot-surface-bridge.jsonl`

## Current Boundary

This bridge is ready for Godot AlienHand-aware surface work.

It is not the final global dual-mouse capture system yet. Mouse B may still move the Windows cursor until the future capture/interception backend exists.
