from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from .tracking import Track


@dataclass(frozen=True)
class VisualReflexCue:
    focus_track_id: int | None
    urgency: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "focus_track_id": self.focus_track_id,
            "urgency": self.urgency,
            "reason": self.reason,
        }


def choose_visual_reflex(tracks: tuple[Track, ...]) -> VisualReflexCue:
    if not tracks:
        return VisualReflexCue(focus_track_id=None, urgency=0.0, reason="no tracked objects")

    best = max(tracks, key=_track_urgency)
    urgency = _track_urgency(best)
    return VisualReflexCue(
        focus_track_id=best.id,
        urgency=urgency,
        reason="largest fast-moving object" if urgency > 0.0 else "largest stable object",
    )


def _track_urgency(track: Track) -> float:
    speed = hypot(track.velocity[0], track.velocity[1])
    size = min(1.0, track.bounds.area / 4096.0)
    return min(1.0, (speed / 64.0) + size + (track.confidence * 0.1))
