from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .interaction_trace import InteractionTransition


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class NoveltyGateConfig:
    recognition_confidence_floor: float = 0.80
    identity_confidence_floor: float = 0.75
    delineation_confidence_floor: float = 0.75
    mapping_confidence_floor: float = 0.70
    novelty_threshold: float = 0.35


@dataclass(frozen=True)
class NoveltyVector:
    recognition_confidence: float
    identity_confidence: float
    delineation_confidence: float
    mapping_confidence: float
    change_intensity: float
    novelty_score: float
    triggers: tuple[str, ...]
    protocols: tuple[str, ...]
    should_packetize: bool
    recorder_action: str

    def to_dict(self) -> JsonDict:
        return asdict(self)


def compute_novelty_vector(
    transition: InteractionTransition,
    config: NoveltyGateConfig | None = None,
    recognition_confidence: float | None = None,
    identity_confidence: float | None = None,
    delineation_confidence: float | None = None,
    mapping_confidence: float | None = None,
) -> NoveltyVector:
    gate = config or NoveltyGateConfig()
    recognition = _clamp01(0.0 if recognition_confidence is None else recognition_confidence)
    identity = _clamp01(_estimate_identity_confidence(transition) if identity_confidence is None else identity_confidence)
    delineation = _clamp01(_estimate_delineation_confidence(transition) if delineation_confidence is None else delineation_confidence)
    mapping = _clamp01(0.0 if mapping_confidence is None else mapping_confidence)
    change = _estimate_change_intensity(transition)

    triggers: list[str] = []
    protocols: list[str] = []
    if recognition < gate.recognition_confidence_floor:
        triggers.append("recognition_below_threshold")
        protocols.append("semantic_labeling")
    if identity < gate.identity_confidence_floor:
        triggers.append("identity_below_threshold")
        protocols.append("identity_resolution")
    if delineation < gate.delineation_confidence_floor:
        triggers.append("delineation_below_threshold")
        protocols.append("boundary_delineation")
    if mapping < gate.mapping_confidence_floor:
        triggers.append("mapping_below_threshold")
        protocols.append("interface_route_matching")
    if change >= 0.5:
        triggers.append("strong_state_change")
        protocols.append("before_after_visual_comparison")

    uncertainty = (
        (gate.recognition_confidence_floor - min(recognition, gate.recognition_confidence_floor))
        + (gate.identity_confidence_floor - min(identity, gate.identity_confidence_floor))
        + (gate.delineation_confidence_floor - min(delineation, gate.delineation_confidence_floor))
        + (gate.mapping_confidence_floor - min(mapping, gate.mapping_confidence_floor))
    ) / (
        gate.recognition_confidence_floor
        + gate.identity_confidence_floor
        + gate.delineation_confidence_floor
        + gate.mapping_confidence_floor
    )
    novelty_score = _clamp01((uncertainty * 0.75) + (change * 0.25))
    should_packetize = novelty_score >= gate.novelty_threshold or bool(triggers)
    recorder_action = "continue_recording_and_queue_packet" if should_packetize else "record_summary_only"

    return NoveltyVector(
        recognition_confidence=recognition,
        identity_confidence=identity,
        delineation_confidence=delineation,
        mapping_confidence=mapping,
        change_intensity=change,
        novelty_score=round(novelty_score, 4),
        triggers=tuple(sorted(set(triggers))),
        protocols=tuple(sorted(set(protocols))),
        should_packetize=should_packetize,
        recorder_action=recorder_action,
    )


def _estimate_identity_confidence(transition: InteractionTransition) -> float:
    input_event = transition.input_event
    payload = input_event.get("payload", {}) if isinstance(input_event, dict) else {}
    has_input_identity = bool(input_event.get("kind")) and bool(input_event.get("action"))
    has_position = isinstance(payload, dict) and "window_x" in payload and "window_y" in payload
    has_window = bool((transition.current_frame or transition.previous_frame or {}).get("window"))
    score = 0.0
    if has_input_identity:
        score += 0.45
    if has_position:
        score += 0.25
    if has_window:
        score += 0.25
    if transition.effect.get("paired"):
        score += 0.05
    return _clamp01(score)


def _estimate_delineation_confidence(transition: InteractionTransition) -> float:
    previous_frame = transition.previous_frame or {}
    current_frame = transition.current_frame or {}
    previous_has_image = bool(previous_frame.get("image"))
    current_has_image = bool(current_frame.get("image"))
    previous_tracks = int(previous_frame.get("visual_track_count") or 0)
    current_tracks = int(current_frame.get("visual_track_count") or 0)
    score = 0.0
    if previous_has_image and current_has_image:
        score += 0.45
    if previous_tracks or current_tracks:
        score += 0.35
    if transition.effect.get("paired"):
        score += 0.20
    return _clamp01(score)


def _estimate_change_intensity(transition: InteractionTransition) -> float:
    effect = transition.effect
    if not effect.get("paired"):
        return 0.35
    score = 0.0
    if effect.get("window_title_changed"):
        score += 0.30
    if effect.get("window_size_changed"):
        score += 0.20
    if effect.get("image_changed"):
        score += 0.15
    track_delta = abs(int(effect.get("visual_track_count_delta") or 0))
    score += min(0.35, track_delta * 0.12)
    return _clamp01(score)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
