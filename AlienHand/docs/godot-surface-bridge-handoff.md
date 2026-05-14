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
    "target": "godot_surface",
    "cursor": {
      "cursor_presentation": "left_hand_reflected",
      "mirror_axis": "hotspot_vertical",
      "coordinate_policy": "x_y_are_hotspot_not_mirrored",
      "width": 32,
      "height": 32,
      "normal_hotspot": {"x": 0, "y": 0},
      "reflected_hotspot": {"x": 31, "y": 0},
      "normal_rect": {"left": 660, "top": 350, "right": 691, "bottom": 381},
      "reflected_rect": {"left": 629, "top": 350, "right": 660, "bottom": 381},
      "normal_clipped_rect": {"left": 660, "top": 350, "right": 691, "bottom": 381},
      "reflected_clipped_rect": {"left": 629, "top": 350, "right": 660, "bottom": 381}
    }
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

## Left-Hand Cursor Presentation

Godot should treat `x` and `y` as the pointer hotspot. Do not mirror the coordinates.

When `metadata.cursor.cursor_presentation == "left_hand_reflected"`:

- Render the AlienHand cursor texture as a horizontal reflection of the normal cursor.
- Mirror the cursor texture around the vertical line that passes through the hotspot.
- Keep selection, hit testing, drag origin, and drop coordinates at `x,y`.
- If the normal cursor hotspot is `(0,0)` with width `W`, the reflected cursor hotspot is `(W - 1,0)`.
- A normal cursor near the right edge clips against the right edge.
- The reflected left-hand cursor near the left edge clips against the left edge.
- Use `metadata.cursor.reflected_rect` for the intended reflected cursor rectangle before clipping.
- Use `metadata.cursor.reflected_clipped_rect` when the viewport needs a precomputed clipped draw rectangle.

This presentation rule lets the left-hand cursor and the normal cursor point at the same object while sharing the same vertical hotspot line.

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
