from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot
from time import time

from alienhand_ai.bitmap import Bitmap


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)

    def iou(self, other: "Rect") -> float:
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        overlap = Rect(left, top, right, bottom).area
        union = self.area + other.area - overlap
        return 0.0 if union <= 0 else overlap / union

    def distance_to(self, other: "Rect") -> float:
        ax, ay = self.center
        bx, by = other.center
        return hypot(ax - bx, ay - by)

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class Detection:
    bounds: Rect
    confidence: float
    source: str = "foreground"
    label: str = "object"

    def to_dict(self) -> dict[str, object]:
        return {
            "bounds": self.bounds.to_dict(),
            "confidence": self.confidence,
            "source": self.source,
            "label": self.label,
        }


@dataclass(frozen=True)
class Track:
    id: int
    bounds: Rect
    velocity: tuple[float, float]
    age: int
    missed: int
    confidence: float
    label: str = "object"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "bounds": self.bounds.to_dict(),
            "velocity": {"x": self.velocity[0], "y": self.velocity[1]},
            "age": self.age,
            "missed": self.missed,
            "confidence": self.confidence,
            "label": self.label,
        }


@dataclass(frozen=True)
class SceneFrame:
    timestamp: float
    detections: tuple[Detection, ...]
    tracks: tuple[Track, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "detections": [detection.to_dict() for detection in self.detections],
            "tracks": [track.to_dict() for track in self.tracks],
        }


class ObjectTracker:
    def __init__(self, max_distance: float = 32.0, max_missed: int = 5) -> None:
        self.max_distance = max_distance
        self.max_missed = max_missed
        self._next_id = 1
        self._tracks: dict[int, Track] = {}

    def update(self, detections: list[Detection] | tuple[Detection, ...]) -> SceneFrame:
        remaining = list(detections)
        updated: dict[int, Track] = {}

        for track_id, track in list(self._tracks.items()):
            match_index = self._best_match(track, remaining)
            if match_index is None:
                missed = Track(
                    id=track.id,
                    bounds=track.bounds,
                    velocity=track.velocity,
                    age=track.age + 1,
                    missed=track.missed + 1,
                    confidence=max(0.0, track.confidence * 0.8),
                    label=track.label,
                )
                if missed.missed <= self.max_missed:
                    updated[track_id] = missed
                continue

            detection = remaining.pop(match_index)
            previous_x, previous_y = track.bounds.center
            current_x, current_y = detection.bounds.center
            updated[track_id] = Track(
                id=track.id,
                bounds=detection.bounds,
                velocity=(current_x - previous_x, current_y - previous_y),
                age=track.age + 1,
                missed=0,
                confidence=detection.confidence,
                label=detection.label,
            )

        for detection in remaining:
            track_id = self._next_id
            self._next_id += 1
            updated[track_id] = Track(
                id=track_id,
                bounds=detection.bounds,
                velocity=(0.0, 0.0),
                age=1,
                missed=0,
                confidence=detection.confidence,
                label=detection.label,
            )

        self._tracks = updated
        return SceneFrame(timestamp=time(), detections=tuple(detections), tracks=tuple(updated.values()))

    def reset(self) -> None:
        self._next_id = 1
        self._tracks.clear()

    def _best_match(self, track: Track, detections: list[Detection]) -> int | None:
        best_index = None
        best_score = -1.0
        for index, detection in enumerate(detections):
            distance = track.bounds.distance_to(detection.bounds)
            if distance > self.max_distance and track.bounds.iou(detection.bounds) <= 0.0:
                continue
            score = track.bounds.iou(detection.bounds) + max(0.0, 1.0 - (distance / max(1.0, self.max_distance)))
            if score > best_score:
                best_score = score
                best_index = index
        return best_index


def detect_foreground(bitmap: Bitmap, threshold: int = 32, min_area: int = 4) -> list[Detection]:
    mask = _foreground_mask(bitmap, threshold)
    return _components_to_detections(mask, bitmap.width, bitmap.height, min_area, "foreground")


def detect_motion(previous: Bitmap, current: Bitmap, threshold: int = 40, min_area: int = 4) -> list[Detection]:
    if previous.width != current.width or previous.height != current.height:
        raise ValueError("Motion detection requires same-size bitmaps")

    mask = bytearray(previous.width * previous.height)
    for y in range(previous.height):
        for x in range(previous.width):
            pr, pg, pb = previous.rgb_at(x, y)
            cr, cg, cb = current.rgb_at(x, y)
            delta = abs(pr - cr) + abs(pg - cg) + abs(pb - cb)
            if delta >= threshold:
                mask[y * previous.width + x] = 1
    return _components_to_detections(mask, current.width, current.height, min_area, "motion")


def track_features(track: Track, frame_width: int, frame_height: int) -> tuple[float, ...]:
    cx, cy = track.bounds.center
    return (
        cx / max(1, frame_width),
        cy / max(1, frame_height),
        track.bounds.width / max(1, frame_width),
        track.bounds.height / max(1, frame_height),
        track.velocity[0] / max(1, frame_width),
        track.velocity[1] / max(1, frame_height),
        min(1.0, track.age / 60.0),
        track.confidence,
    )


def scene_feature_matrix(tracks: tuple[Track, ...], frame_width: int, frame_height: int, max_tracks: int = 32) -> list[list[float]]:
    features = [list(track_features(track, frame_width, frame_height)) for track in tracks[:max_tracks]]
    while len(features) < max_tracks:
        features.append([0.0] * 8)
    return features


def summarize_track_group_motion(tracks: tuple[Track, ...]) -> dict[str, object]:
    if not tracks:
        return {"count": 0, "track_ids": [], "members": []}

    left = min(track.bounds.left for track in tracks)
    top = min(track.bounds.top for track in tracks)
    right = max(track.bounds.right for track in tracks)
    bottom = max(track.bounds.bottom for track in tracks)
    centers = [track.bounds.center for track in tracks]
    mean_x = sum(center[0] for center in centers) / len(centers)
    mean_y = sum(center[1] for center in centers) / len(centers)
    velocity_x = sum(track.velocity[0] for track in tracks) / len(tracks)
    velocity_y = sum(track.velocity[1] for track in tracks) / len(tracks)
    return {
        "count": len(tracks),
        "track_ids": [track.id for track in tracks],
        "bounds": Rect(left=left, top=top, right=right, bottom=bottom).to_dict(),
        "centroid": {"x": mean_x, "y": mean_y},
        "mean_velocity": {"x": velocity_x, "y": velocity_y},
        "members": [
            {
                "id": track.id,
                "center": {"x": center[0], "y": center[1]},
                "velocity": {"x": track.velocity[0], "y": track.velocity[1]},
                "bounds": track.bounds.to_dict(),
            }
            for track, center in zip(tracks, centers)
        ],
    }


def _foreground_mask(bitmap: Bitmap, threshold: int) -> bytearray:
    corners = [
        bitmap.rgb_at(0, 0),
        bitmap.rgb_at(bitmap.width - 1, 0),
        bitmap.rgb_at(0, bitmap.height - 1),
        bitmap.rgb_at(bitmap.width - 1, bitmap.height - 1),
    ]
    background = tuple(sum(pixel[channel] for pixel in corners) // len(corners) for channel in range(3))
    mask = bytearray(bitmap.width * bitmap.height)
    for y in range(bitmap.height):
        for x in range(bitmap.width):
            r, g, b = bitmap.rgb_at(x, y)
            delta = abs(r - background[0]) + abs(g - background[1]) + abs(b - background[2])
            if delta >= threshold:
                mask[y * bitmap.width + x] = 1
    return mask


def _components_to_detections(mask: bytearray, width: int, height: int, min_area: int, source: str) -> list[Detection]:
    detections: list[Detection] = []
    seen = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            offset = y * width + x
            if not mask[offset] or seen[offset]:
                continue
            bounds, area = _flood_component(mask, seen, width, height, x, y)
            if area >= min_area:
                detections.append(Detection(bounds=bounds, confidence=min(1.0, area / max(1, bounds.area)), source=source))
    return detections


def _flood_component(mask: bytearray, seen: bytearray, width: int, height: int, x: int, y: int) -> tuple[Rect, int]:
    stack = [(x, y)]
    seen[y * width + x] = 1
    left = right = x
    top = bottom = y
    area = 0
    while stack:
        cx, cy = stack.pop()
        area += 1
        left = min(left, cx)
        right = max(right, cx)
        top = min(top, cy)
        bottom = max(bottom, cy)
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            offset = ny * width + nx
            if mask[offset] and not seen[offset]:
                seen[offset] = 1
                stack.append((nx, ny))
    return Rect(left=left, top=top, right=right + 1, bottom=bottom + 1), area
