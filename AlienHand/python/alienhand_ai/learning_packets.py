from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from time import time
from typing import Any

from .interaction_trace import InteractionTransition, build_interaction_transitions
from .novelty import compute_novelty_vector


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class LearningPacket:
    id: str
    kind: str
    status: str
    created_at: float
    source_run: str
    tags: tuple[str, ...]
    payload: JsonDict
    digest: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return asdict(self)


def build_learning_packets(run_dir: str | Path) -> list[LearningPacket]:
    root = Path(run_dir)
    transitions = build_interaction_transitions(root)
    return [_packet_from_transition(root, index, transition) for index, transition in enumerate(transitions, start=1)]


def write_learning_packets(run_dir: str | Path, output: str | Path | None = None) -> Path:
    root = Path(run_dir)
    output_path = Path(output) if output else root / "learning_packets.jsonl"
    packets = build_learning_packets(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        for packet in packets:
            file.write(json.dumps(packet.to_dict(), sort_keys=True))
            file.write("\n")
    return output_path


def summarize_learning_packets(run_dir: str | Path) -> JsonDict:
    packets = build_learning_packets(run_dir)
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    for packet in packets:
        by_status[packet.status] = by_status.get(packet.status, 0) + 1
        by_kind[packet.kind] = by_kind.get(packet.kind, 0) + 1
        for tag in packet.tags:
            by_tag[tag] = by_tag.get(tag, 0) + 1
    return {"packets": len(packets), "by_status": by_status, "by_kind": by_kind, "by_tag": by_tag}


def _packet_from_transition(root: Path, index: int, transition: InteractionTransition) -> LearningPacket:
    payload = transition.to_dict()
    novelty = compute_novelty_vector(transition)
    payload["novelty_vector"] = novelty.to_dict()
    packet_id = _stable_packet_id(root, index, payload)
    return LearningPacket(
        id=packet_id,
        kind="ui_interaction_transition",
        status="pending_llm_digest",
        created_at=time(),
        source_run=str(root.resolve()),
        tags=tuple(sorted(_packet_tags(transition))),
        payload=payload,
        digest={
            "schema_version": 1,
            "novelty_vector": novelty.to_dict(),
            "needs": [
                "semantic_effect_label",
                "mapped_interface_route",
                "confidence",
                "delineated_screen_region",
                "identified_ui_element",
                "follow_up_observation_plan",
            ],
        },
    )


def _packet_tags(transition: InteractionTransition) -> set[str]:
    tags = {"observation", "human_demonstration"}
    input_event = transition.input_event
    if isinstance(input_event, dict):
        kind = input_event.get("kind")
        action = input_event.get("action")
        if isinstance(kind, str):
            tags.add(kind)
        if isinstance(action, str):
            tags.add(action)
    effect = transition.effect
    if effect.get("paired"):
        tags.add("paired_state_transition")
    if effect.get("window_title_changed"):
        tags.add("window_change")
    if effect.get("visual_track_count_delta"):
        tags.add("visual_change")
    novelty = compute_novelty_vector(transition)
    if novelty.should_packetize:
        tags.add("novelty")
    for protocol in novelty.protocols:
        tags.add(protocol)
    return tags


def _stable_packet_id(root: Path, index: int, payload: JsonDict) -> str:
    encoded = json.dumps({"root": str(root.resolve()), "index": index, "payload": payload}, sort_keys=True).encode("utf-8")
    return "learn_" + hashlib.sha1(encoded).hexdigest()[:16]
