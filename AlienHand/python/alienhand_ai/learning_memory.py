from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from time import time
from typing import Any

from .sop import SOPProgram, compile_sop_text


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class LearnedNoveltyEdge:
    packet_id: str
    source_kind: str
    semantic_label: str
    confidence: float
    novelty_vector: JsonDict
    interface_route: str | None
    sop: JsonDict
    created_at: float
    notes: str = ""

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class LearningMemory:
    entries: tuple[LearnedNoveltyEdge, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "schema_version": 1,
            "metadata": self.metadata,
            "entries": [entry.to_dict() for entry in self.entries],
            "summary": summarize_learning_memory(self),
        }

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return output


def load_learning_packets(path: str | Path) -> list[JsonDict]:
    packet_path = Path(path)
    if not packet_path.exists():
        return []
    rows: list[JsonDict] = []
    with packet_path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_learning_memory(path: str | Path) -> LearningMemory:
    memory_path = Path(path)
    if not memory_path.exists():
        return LearningMemory(metadata={"created_at": time()})
    payload = json.loads(memory_path.read_text(encoding="utf-8-sig"))
    entries = tuple(
        LearnedNoveltyEdge(
            packet_id=str(entry.get("packet_id", "")),
            source_kind=str(entry.get("source_kind", "")),
            semantic_label=str(entry.get("semantic_label", "")),
            confidence=float(entry.get("confidence", 0.0)),
            novelty_vector=entry.get("novelty_vector", {}),
            interface_route=entry.get("interface_route"),
            sop=entry.get("sop", {}),
            created_at=float(entry.get("created_at", time())),
            notes=str(entry.get("notes", "")),
        )
        for entry in payload.get("entries", [])
    )
    return LearningMemory(entries=entries, metadata=payload.get("metadata", {}))


def ingest_learning_digest(
    packets_path: str | Path,
    digest_path: str | Path,
    output: str | Path,
    existing_memory: str | Path | None = None,
) -> LearningMemory:
    packets = {str(packet.get("id")): packet for packet in load_learning_packets(packets_path)}
    digest = json.loads(Path(digest_path).read_text(encoding="utf-8-sig"))
    packet_id = str(digest.get("packet_id", ""))
    if packet_id not in packets:
        raise KeyError(f"Digest refers to unknown learning packet {packet_id!r}")

    memory = load_learning_memory(existing_memory or output)
    edge = learned_edge_from_digest(packets[packet_id], digest)
    existing = [entry for entry in memory.entries if entry.packet_id != packet_id]
    updated = LearningMemory(entries=tuple(existing + [edge]), metadata={**memory.metadata, "updated_at": time()})
    updated.write_json(output)
    return updated


def learned_edge_from_digest(packet: JsonDict, digest: JsonDict) -> LearnedNoveltyEdge:
    sop_program = _sop_from_digest(packet, digest)
    payload = packet.get("payload", {})
    novelty_vector = _extract_novelty_vector(payload, packet.get("digest", {}))
    return LearnedNoveltyEdge(
        packet_id=str(packet.get("id", "")),
        source_kind=str(packet.get("kind", "")),
        semantic_label=str(digest.get("semantic_effect_label") or digest.get("label") or "unlabeled"),
        confidence=float(digest.get("confidence", 0.0)),
        novelty_vector=novelty_vector,
        interface_route=digest.get("mapped_interface_route") or digest.get("interface_route"),
        sop=sop_program.to_dict(),
        created_at=time(),
        notes=str(digest.get("notes", "")),
    )


def summarize_learning_memory(memory: LearningMemory) -> JsonDict:
    by_kind: dict[str, int] = {}
    by_label: dict[str, int] = {}
    for entry in memory.entries:
        by_kind[entry.source_kind] = by_kind.get(entry.source_kind, 0) + 1
        by_label[entry.semantic_label] = by_label.get(entry.semantic_label, 0) + 1
    return {"entries": len(memory.entries), "by_kind": by_kind, "by_label": by_label}


def _sop_from_digest(packet: JsonDict, digest: JsonDict) -> SOPProgram:
    if isinstance(digest.get("sop"), dict):
        sop_text = _sop_text_from_structured(digest["sop"])
    else:
        sop_text = str(digest.get("sop_text") or digest.get("sop") or _fallback_sop(packet, digest))
    name = str(digest.get("sop_name") or digest.get("semantic_effect_label") or "learned-sop")
    return compile_sop_text(sop_text, name=name, source=f"digest:{packet.get('id', '')}")


def _sop_text_from_structured(sop: JsonDict) -> str:
    steps = sop.get("steps", [])
    if isinstance(steps, list):
        lines = []
        for step in steps:
            if isinstance(step, str):
                lines.append(step)
            elif isinstance(step, dict):
                primitive = step.get("primitive") or step.get("action") or "observe"
                target = step.get("target") or step.get("condition") or step.get("route") or ""
                lines.append(f"{primitive} {target}".strip())
        if lines:
            return "\n".join(lines)
    return str(sop.get("text", "observe learned transition"))


def _fallback_sop(packet: JsonDict, digest: JsonDict) -> str:
    route = digest.get("mapped_interface_route") or digest.get("interface_route")
    if route:
        return f"call {route}"
    label = digest.get("semantic_effect_label") or digest.get("label") or "learned transition"
    return f"observe {label}"


def _extract_novelty_vector(payload: JsonDict, digest: JsonDict) -> JsonDict:
    if isinstance(payload, dict) and isinstance(payload.get("novelty_vector"), dict):
        return payload["novelty_vector"]
    if isinstance(digest, dict) and isinstance(digest.get("novelty_vector"), dict):
        return digest["novelty_vector"]
    return {}
