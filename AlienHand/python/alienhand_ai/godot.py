from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class GodotEvent:
    type: str
    session_id: str
    frame_index: int
    ticks_msec: int
    unix_time: float
    payload: JsonDict

    def to_dict(self) -> JsonDict:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "frame_index": self.frame_index,
            "ticks_msec": self.ticks_msec,
            "unix_time": self.unix_time,
            "payload": self.payload,
        }


def load_godot_events(path: str | Path) -> list[GodotEvent]:
    events: list[GodotEvent] = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            events.append(normalize_godot_event(json.loads(line)))
    return events


def normalize_godot_event(raw: JsonDict) -> GodotEvent:
    event_type = str(raw.get("type", "godot_event"))
    payload_key = "state" if event_type == "godot_frame" else "payload"
    return GodotEvent(
        type=event_type,
        session_id=str(raw.get("session_id", "")),
        frame_index=int(raw.get("frame_index", 0)),
        ticks_msec=int(raw.get("ticks_msec", 0)),
        unix_time=float(raw.get("unix_time", 0.0)),
        payload=dict(raw.get(payload_key, {})),
    )


def summarize_godot_events(events: list[GodotEvent]) -> JsonDict:
    frames = [event for event in events if event.type == "godot_frame"]
    event_counts: dict[str, int] = {}
    for event in events:
        event_counts[event.type] = event_counts.get(event.type, 0) + 1
    return {
        "sessions": sorted({event.session_id for event in events if event.session_id}),
        "frames": len(frames),
        "events": len(events),
        "event_counts": event_counts,
        "first_tick": min((event.ticks_msec for event in events), default=None),
        "last_tick": max((event.ticks_msec for event in events), default=None),
    }
