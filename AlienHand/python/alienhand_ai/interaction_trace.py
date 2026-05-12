from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from .telemetry import JsonDict, load_events


@dataclass(frozen=True)
class InteractionTransition:
    input_event: JsonDict
    previous_frame: JsonDict | None
    current_frame: JsonDict | None
    effect: JsonDict

    def to_dict(self) -> JsonDict:
        return asdict(self)


def build_interaction_transitions(run_dir: str | Path) -> list[InteractionTransition]:
    root = Path(run_dir)
    events = load_events(root / "events.jsonl")
    frames_by_index = {int(event["index"]): event for event in events if event.get("type") == "frame" and "index" in event}
    input_events = [event for event in events if event.get("type") == "event" and event.get("kind") == "observed_input"]

    transitions: list[InteractionTransition] = []
    for event in input_events:
        previous_index = int(event.get("frame_index") or 0)
        current_index = previous_index + 1
        previous_frame = frames_by_index.get(previous_index)
        current_frame = frames_by_index.get(current_index)
        transitions.append(
            InteractionTransition(
                input_event=event.get("payload", {}),
                previous_frame=_frame_summary(previous_frame, root) if previous_frame else None,
                current_frame=_frame_summary(current_frame, root) if current_frame else None,
                effect=_effect_summary(previous_frame, current_frame),
            )
        )
    return transitions


def write_interaction_trace(run_dir: str | Path, output: str | Path | None = None) -> Path:
    root = Path(run_dir)
    output_path = Path(output) if output else root / "interaction_trace.json"
    transitions = build_interaction_transitions(root)
    payload = {
        "schema_version": 1,
        "run_dir": str(root.resolve()),
        "transitions": [transition.to_dict() for transition in transitions],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output_path


def summarize_interaction_trace(run_dir: str | Path) -> JsonDict:
    transitions = build_interaction_transitions(run_dir)
    by_kind: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for transition in transitions:
        input_event = transition.input_event
        kind = str(input_event.get("kind", "unknown"))
        action = str(input_event.get("action", "unknown"))
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_action[action] = by_action.get(action, 0) + 1
    return {"transitions": len(transitions), "by_kind": by_kind, "by_action": by_action}


def _frame_summary(frame: JsonDict, run_dir: Path) -> JsonDict:
    observation = frame.get("observation", {})
    variables = frame.get("variables", {})
    window = observation.get("window", {}) if isinstance(observation, dict) else {}
    visual_scene = observation.get("visual_scene", {}) if isinstance(observation, dict) else {}
    tracks = visual_scene.get("tracks", []) if isinstance(visual_scene, dict) else []
    image = frame.get("image")
    image_path = str((run_dir / image).resolve()) if isinstance(image, str) else None
    return {
        "index": frame.get("index"),
        "timestamp": frame.get("timestamp"),
        "image": image_path,
        "window": window,
        "visual_track_count": len(tracks) if isinstance(tracks, list) else 0,
        "variables": {
            "frame": variables.get("frame") if isinstance(variables, dict) else None,
            "elapsed_seconds": variables.get("elapsed_seconds") if isinstance(variables, dict) else None,
            "track_count": variables.get("track_count") if isinstance(variables, dict) else None,
            "input_event_count": variables.get("input_event_count") if isinstance(variables, dict) else None,
        },
    }


def _effect_summary(previous_frame: JsonDict | None, current_frame: JsonDict | None) -> JsonDict:
    if previous_frame is None or current_frame is None:
        return {"paired": False}
    previous_observation = previous_frame.get("observation", {})
    current_observation = current_frame.get("observation", {})
    previous_window = previous_observation.get("window", {}) if isinstance(previous_observation, dict) else {}
    current_window = current_observation.get("window", {}) if isinstance(current_observation, dict) else {}
    previous_tracks = _track_count(previous_observation)
    current_tracks = _track_count(current_observation)
    return {
        "paired": True,
        "elapsed_seconds": float(current_frame.get("timestamp", 0.0)) - float(previous_frame.get("timestamp", 0.0)),
        "window_title_changed": previous_window.get("title") != current_window.get("title"),
        "window_size_changed": (
            previous_window.get("width") != current_window.get("width")
            or previous_window.get("height") != current_window.get("height")
        ),
        "visual_track_count_delta": current_tracks - previous_tracks,
        "image_changed": previous_frame.get("image") != current_frame.get("image"),
    }


def _track_count(observation: Any) -> int:
    if not isinstance(observation, dict):
        return 0
    visual_scene = observation.get("visual_scene", {})
    if not isinstance(visual_scene, dict):
        return 0
    tracks = visual_scene.get("tracks", [])
    return len(tracks) if isinstance(tracks, list) else 0
