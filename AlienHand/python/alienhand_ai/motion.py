from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any, Sequence


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class MotionCompressionConfig:
    window_ms: float = 100.0
    tolerance_ratio: float = 0.2
    min_samples: int = 3


@dataclass(frozen=True)
class MotionPoint:
    timestamp: float
    x: float
    y: float


def compress_input_events(events: Sequence[JsonDict], config: MotionCompressionConfig | None = None) -> list[JsonDict]:
    """Collapse steady mouse movement into semantic motion packets.

    Button/key events stay discrete. Mouse move events are grouped inside a time
    window when their velocity vectors stay within the configured tolerance.
    """

    compression = config or MotionCompressionConfig()
    output: list[JsonDict] = []
    move_buffer: list[JsonDict] = []

    def flush_moves() -> None:
        if not move_buffer:
            return
        output.extend(_compress_mouse_moves(move_buffer, compression))
        move_buffer.clear()

    for event in events:
        if _is_mouse_move(event):
            move_buffer.append(event)
        else:
            flush_moves()
            output.append(event)
    flush_moves()
    return output


def _compress_mouse_moves(events: Sequence[JsonDict], config: MotionCompressionConfig) -> list[JsonDict]:
    if config.window_ms <= 0 or len(events) < config.min_samples:
        return list(events)

    compressed: list[JsonDict] = []
    index = 0
    while index < len(events):
        start_timestamp = float(events[index].get("timestamp", 0.0))
        end = index + 1
        while end < len(events):
            timestamp = float(events[end].get("timestamp", start_timestamp))
            if (timestamp - start_timestamp) * 1000.0 > config.window_ms:
                break
            end += 1

        chunk = list(events[index:end])
        if len(chunk) >= config.min_samples:
            packet = _continuous_motion_packet(chunk, config)
            compressed.extend([packet] if packet is not None else chunk)
        else:
            compressed.extend(chunk)
        index = end
    return compressed


def _continuous_motion_packet(events: Sequence[JsonDict], config: MotionCompressionConfig) -> JsonDict | None:
    points = [_point_from_input_event(event) for event in events]
    if any(point is None for point in points):
        return None

    motion_points = [point for point in points if point is not None]
    stats = _motion_stats(motion_points, config.tolerance_ratio)
    if not stats["steady"]:
        return None

    first = events[0]
    last = events[-1]
    payload = dict(first.get("payload", {}))
    payload.update(
        {
            "compressed": True,
            "compression": "continuous_even_motion",
            "continuous": True,
            "even_motion": True,
            "sample_count": len(events),
            "duration_ms": stats["duration_ms"],
            "start_timestamp": motion_points[0].timestamp,
            "end_timestamp": motion_points[-1].timestamp,
            "total_distance_px": stats["total_distance_px"],
            "variation_ratio": stats["variation_ratio"],
            "tolerance_ratio": config.tolerance_ratio,
            "start": dict(first.get("payload", {})),
            "end": dict(last.get("payload", {})),
            "bounds": stats["bounds"],
            "mean_velocity_px_per_s": stats["mean_velocity_px_per_s"],
        }
    )

    packet = dict(first)
    packet["action"] = "move_segment"
    packet["timestamp"] = motion_points[0].timestamp
    packet["payload"] = payload
    return packet


def _motion_stats(points: Sequence[MotionPoint], tolerance_ratio: float) -> JsonDict:
    if len(points) < 2:
        return {
            "steady": False,
            "duration_ms": 0.0,
            "total_distance_px": 0.0,
            "variation_ratio": 1.0,
            "bounds": {},
            "mean_velocity_px_per_s": {"x": 0.0, "y": 0.0},
        }

    velocities: list[tuple[float, float]] = []
    total_distance = 0.0
    for previous, current in zip(points, points[1:]):
        dx = current.x - previous.x
        dy = current.y - previous.y
        dt = max(0.001, current.timestamp - previous.timestamp)
        velocities.append((dx / dt, dy / dt))
        total_distance += hypot(dx, dy)

    mean_x = sum(vector[0] for vector in velocities) / len(velocities)
    mean_y = sum(vector[1] for vector in velocities) / len(velocities)
    mean_speed = hypot(mean_x, mean_y)
    max_deviation = max(hypot(vector[0] - mean_x, vector[1] - mean_y) for vector in velocities)
    if mean_speed <= 0.001 and total_distance <= 0.001:
        variation_ratio = 0.0
    else:
        variation_ratio = max_deviation / max(1.0, mean_speed)

    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return {
        "steady": variation_ratio <= tolerance_ratio,
        "duration_ms": max(0.0, (points[-1].timestamp - points[0].timestamp) * 1000.0),
        "total_distance_px": total_distance,
        "variation_ratio": variation_ratio,
        "bounds": {
            "min_x": min(xs),
            "min_y": min(ys),
            "max_x": max(xs),
            "max_y": max(ys),
        },
        "mean_velocity_px_per_s": {"x": mean_x, "y": mean_y},
    }


def _point_from_input_event(event: JsonDict) -> MotionPoint | None:
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        return None
    x = payload.get("window_x", payload.get("screen_x"))
    y = payload.get("window_y", payload.get("screen_y"))
    if x is None or y is None:
        return None
    return MotionPoint(timestamp=float(event.get("timestamp", 0.0)), x=float(x), y=float(y))


def _is_mouse_move(event: JsonDict) -> bool:
    return event.get("kind") == "mouse" and event.get("action") == "move"
